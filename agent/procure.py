"""WP6 Part B: a minimal tool-calling procurement agent on a local Ollama model.

The model chooses every call. The loop sends the brief and two tools to the model, executes
exactly the structured `tool_calls` it returns, sends back the results, and repeats until the
model stops calling tools, `max_turns` model calls have been made, or the harness sets `halt`.
Nothing here writes a tool call on the model's behalf; plain-text "calls" are logged and not
executed.

Tools (identity PROCUREMENT_AGENT, on the WP5/WP6-A create path of either backend):
  create_purchase(vendor, item, amount)   amount in whole US dollars; sent as cents
  get_payment_status(payment_id)

The mandate (budget cap, vendor allowlist, max amount per purchase) is enforced here, in
deterministic code, before the gateway is called (SCIENTIFIC_GUARDRAILS: mandates are enforced
outside the LLM). A violating call is rejected, logged in the trace, and never reaches the
gateway. Arguments are parsed by fixed rules (an integer, or a string like "1,250" or "$300");
anything else is rejected as INVALID_ARGUMENTS.

The trace (trace.jsonl) holds the model name and digest, the prompts, every model reply, tool
call and tool result, with wall-clock timestamps. It never holds a key or a Stripe object
secret: the gateways return only internal payment ids and states, and `Trace.write` refuses
any line that looks like a key or a client secret.
"""
import json
import re
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime

from p4.sim.protocol import PROCUREMENT_AGENT

TOOLS = [
    {"type": "function", "function": {
        "name": "create_purchase",
        "description": "Place one purchase order. The payment is authorized now and captured by the "
                       "vendor later. Returns a payment_id on success.",
        "parameters": {"type": "object", "properties": {
            "vendor": {"type": "string", "description": "vendor id, e.g. acme-office"},
            "item": {"type": "string", "description": "what is bought"},
            "amount": {"type": "integer", "description": "total price in whole US dollars"}},
            "required": ["vendor", "item", "amount"]}}},
    {"type": "function", "function": {
        "name": "get_payment_status",
        "description": "Look up the status of a payment created earlier.",
        "parameters": {"type": "object", "properties": {
            "payment_id": {"type": "string", "description": "payment_id returned by create_purchase"}},
            "required": ["payment_id"]}}},
]

SECRET = re.compile(r"sk_(test|live)_[A-Za-z0-9]+|rk_(test|live)_[A-Za-z0-9]+|pi_[A-Za-z0-9]+_secret_[A-Za-z0-9]+|whsec_")
_AMOUNT = re.compile(r"\$?\s*(\d{1,3}(,\d{3})+|\d+)(\.0+)?")


class SecretInTrace(RuntimeError):
    pass


def parse_amount(raw) -> int | None:
    """Whole US dollars from a tool argument, by fixed rules; None if it is not one."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(raw, str) and (m := _AMOUNT.fullmatch(raw.strip())):
        return int(m.group(1).replace(",", ""))
    return None


@dataclass(frozen=True)
class Mandate:
    budget_usd: int
    max_per_purchase_usd: int
    vendors: frozenset

    @classmethod
    def from_dict(cls, d: dict) -> "Mandate":
        return cls(int(d["budget_usd"]), int(d["max_per_purchase_usd"]), frozenset(d["vendors"]))

    def violation(self, vendor: str, amount_usd: int, spent_usd: int) -> str | None:
        if vendor not in self.vendors:
            return f"vendor {vendor!r} is not on the allowlist"
        if amount_usd <= 0:
            return "amount must be positive"
        if amount_usd > self.max_per_purchase_usd:
            return f"amount {amount_usd} exceeds the per-purchase maximum {self.max_per_purchase_usd}"
        if spent_usd + amount_usd > self.budget_usd:
            return f"amount {amount_usd} would exceed the remaining budget {self.budget_usd - spent_usd}"
        return None


class Trace:
    """Append-only JSONL trace shared by the agent thread and the harness."""

    def __init__(self, path, *, now_sim=lambda: None):
        self._fh = open(path, "w", encoding="utf-8", newline="\n")  # noqa: SIM115  (closed by close())
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self._n = 0
        self.now_sim = now_sim

    def mono(self) -> float:
        return time.monotonic() - self._t0

    def write(self, kind: str, **fields) -> dict:
        with self._lock:
            row = {"i": self._n, "t_wall": round(self.mono(), 4),
                   "utc": datetime.now(UTC).isoformat(timespec="milliseconds"),
                   "t_sim": self.now_sim(), "thread": threading.current_thread().name,
                   "kind": kind, **fields}
            line = json.dumps(row, sort_keys=True, default=str)
            if SECRET.search(line):
                raise SecretInTrace(f"refusing to write a trace line of kind {kind!r}: looks like a secret")
            self._fh.write(line + "\n")
            self._fh.flush()
            self._n += 1
            return row

    def close(self) -> None:
        self._fh.close()


class OllamaChat:
    """The local Ollama HTTP API (/api/chat with tools); no paid service, nothing leaves the host."""

    def __init__(self, model: str, url: str = "http://localhost:11434", timeout: float = 300):
        if not url.startswith(("http://localhost", "http://127.0.0.1")):
            raise ValueError("OllamaChat talks only to a local Ollama")
        self.model, self.url, self.timeout = model, url.rstrip("/"), timeout

    def _call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.load(resp)

    def info(self) -> dict:
        tags = self._call("/api/tags")
        entry = next((m for m in tags.get("models", []) if m.get("name") == self.model), None)
        if entry is None:
            raise RuntimeError(f"model {self.model!r} is not pulled in the local Ollama")
        return {"model": self.model, "digest": entry.get("digest"), "details": entry.get("details"),
                "capabilities": entry.get("capabilities"),
                "ollama_version": self._call("/api/version").get("version")}

    def chat(self, messages, tools, *, temperature: float, seed: int) -> dict:
        body = {"model": self.model, "messages": messages, "tools": tools, "stream": False,
                "options": {"temperature": temperature, "seed": seed}}
        return self._call("/api/chat", body)


class ProcurementTools:
    """The two tools, over a gateway (SimAgentGateway or StripeTestProcessor), with the mandate."""

    def __init__(self, gateway, mandate: Mandate, trace: Trace, *, on_purchase=lambda n: None):
        self.gw, self.mandate, self.trace = gateway, mandate, trace
        self._on_purchase = on_purchase
        self._lock = threading.Lock()
        self.spent_usd = 0
        self.purchases = 0     # successful, non-replayed creates
        self._key_n = 0

    def call(self, name: str, args) -> dict:
        if not isinstance(args, dict):
            return {"ok": False, "error": "INVALID_ARGUMENTS", "reason": "arguments must be an object"}
        if name == "create_purchase":
            return self.create_purchase(args.get("vendor"), args.get("item"), args.get("amount"))
        if name == "get_payment_status":
            return self.get_payment_status(args.get("payment_id"))
        return {"ok": False, "error": "UNKNOWN_TOOL", "reason": f"no tool named {name!r}"}

    def create_purchase(self, vendor, item, amount) -> dict:
        amount_usd = parse_amount(amount)
        if not isinstance(vendor, str) or amount_usd is None:
            return {"ok": False, "error": "INVALID_ARGUMENTS",
                    "reason": "vendor must be a string and amount whole US dollars"}
        n = None
        with self._lock:
            reason = self.mandate.violation(vendor, amount_usd, self.spent_usd)
            if reason is not None:
                self.trace.write("mandate_reject", tool="create_purchase", vendor=vendor, item=item,
                                 amount_usd=amount_usd, reason=reason)
                return {"ok": False, "error": "MANDATE_REJECTED", "reason": reason}
            key = f"po-{self._key_n}"
            self._key_n += 1
            reply = self.gw.create_payment(PROCUREMENT_AGENT, amount_usd * 100, vendor, key)
            out = {"ok": reply.payment_id is not None, "idem_key": key}
            if reply.payment_id is not None:
                out.update(payment_id=reply.payment_id, state=reply.state, replayed=reply.replayed)
                if not reply.replayed:
                    self.spent_usd += amount_usd
                    self.purchases += 1
                    n = self.purchases
            else:
                out.update(result=reply.result, error=reply.error, timeout=reply.timeout)
        if n is not None:
            self._on_purchase(n)
        return out

    def get_payment_status(self, payment_id) -> dict:
        if not isinstance(payment_id, str):
            return {"ok": False, "error": "INVALID_ARGUMENTS", "reason": "payment_id must be a string"}
        reply = self.gw.status(PROCUREMENT_AGENT, payment_id)
        if reply.state is not None:
            return {"ok": True, "payment_id": payment_id, "state": reply.state}
        return {"ok": False, "payment_id": payment_id, "error": reply.error, "timeout": reply.timeout}


SYSTEM_PROMPT = """You are a procurement agent for a small company. You place purchase orders \
with the create_purchase tool and can check payments with get_payment_status. \
Your mandate: total budget {budget} US dollars; at most {cap} US dollars per purchase; \
only these vendors: {vendors}. Place one purchase per line item. When you have handled \
every line item, reply with a short summary and no tool call."""

BRIEF = """Procurement brief PR-2026-117: equip the new five-person analytics team.
Line items (vendor, item, total price in US dollars):
1. acme-office: 5 ergonomic chairs, 1250
2. acme-office: 5 standing desks, 2400
3. globex-it: 5 laptop docking stations, 900
4. globex-it: 5 27-inch monitors, 1500
5. initech-supply: printer and toner, 380
6. northwind-cafe: espresso machine for the team kitchen, 640
7. initech-supply: notebooks and pens, 120
8. hooli-cloud: cloud compute credits, 500
9. pied-piper-catering: team welcome lunch, 300
Please place the orders now."""


def system_prompt(m: Mandate) -> str:
    return SYSTEM_PROMPT.format(budget=m.budget_usd, cap=m.max_per_purchase_usd, vendors=", ".join(sorted(m.vendors)))


def run_agent(llm: OllamaChat, tools: ProcurementTools, trace: Trace, *, temperature: float, seed: int,
              max_turns: int, halt: threading.Event, system: str, brief: str = BRIEF) -> dict:
    """The tool-calling loop. Returns why it ended and how many model calls and tool calls it made."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": brief}]
    trace.write("prompt", system=system, user=brief, tools=TOOLS, temperature=temperature, seed=seed)
    turns = tool_calls = 0
    end = "max_turns"
    while turns < max_turns:
        if halt.is_set():
            end = "halted"
            break
        trace.write("model_request", turn=turns, n_messages=len(messages))
        resp = llm.chat(messages, TOOLS, temperature=temperature, seed=seed)
        turns += 1
        msg = resp.get("message", {})
        calls = msg.get("tool_calls") or []
        trace.write("model_response", turn=turns - 1, content=msg.get("content", ""), tool_calls=calls,
                    eval_count=resp.get("eval_count"), total_duration_ns=resp.get("total_duration"))
        messages.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": calls})
        if not calls:
            end = "model_done"
            break
        for call in calls:
            fn = call.get("function", {})
            name, args = fn.get("name"), fn.get("arguments")
            if isinstance(args, str):   # some models return arguments as a JSON string
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    pass
            trace.write("tool_call", turn=turns - 1, tool=name, arguments=args)
            result = tools.call(name, args)
            tool_calls += 1
            trace.write("tool_result", turn=turns - 1, tool=name, result=result)
            messages.append({"role": "tool", "tool_name": name, "content": json.dumps(result)})
    trace.write("agent_end", reason=end, turns=turns, tool_calls=tool_calls)
    return {"end": end, "turns": turns, "tool_calls": tool_calls}
