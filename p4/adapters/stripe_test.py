"""Stripe TEST-mode adapter (WP5) with the WP6 agent create path.

Same interfaces as the simulator: `Processor` (cancel, status) for the remediator and
`AgentGateway` (create_payment, incident_stop) for the agent, with the same hidden-truth
rules as p4/sim/agent_gateway.py. Every Stripe behaviour relied on here is cited in
docs/STRIPE_MAPPING.md.

Safety (CLAUDE.md): the key comes only from the environment variable STRIPE_TEST_KEY, any key
not starting with `sk_test_` is refused, requests go only to https://api.stripe.com, and the
key never appears in a repr, an exception, a log line or an exported file. Response bodies are
never stored; only status, error type/code, PaymentIntent id and status are kept.

Layers on the remediator's path, top to bottom:
  StripeTestProcessor -> [StripeFaultProxy] -> _Recorder -> RateLimitedClient -> UrllibHttpClient
The recorder sits below the fault proxy, so the event log records what Stripe actually did:
with `drop_resp` the request reaches Stripe and the log has its line, but the caller gets a
timeout (a real lost ACK). The agent's creates and the merchant-capture emulator use the
client below the proxy (faults are a remediator <-> processor model, REVAMP_PLAN §2).

Time is the same EventClock as the simulator; nothing before incident_stop has a time. At
the stop each created PaymentIntent gets an ACCEPTED line at t = 0 and a merchant capture
scheduled at its D_actual (captures are scheduled first, so they win ties, as in
SimProcessor). With an EventClock driven by the harness the timing is logical; with
`clock_factory=WallClockDriver` (p4/sim/clock.py) 1 sim unit = `unit_seconds` of wall time,
starting at the stop.

Threads (WP6): the agent loop may run in its own thread, concurrently with the stop and the
remediation episode. A create and the stop are serialized (a create already in flight when the
stop is called finishes first and is in the stop's scenario; any later create is DENIED), and
log and call-log appends are locked, so the hash chain stays intact. Pass the stop's clock to
`run_episode(..., clock=adapter.clock, sink_factory=adapter.sink_factory)` so the agent's
calls between the stop and the episode stay in the episode's log.

Authorization is the adapter's job, not Stripe's (one account key): a call by anyone but the
remediation operator, or by a revoked actor, is DENIED locally, logged, and never sent.
Idempotency is Stripe's: keys are sent as `Idempotency-Key: p4/<run_id>/<actor>/<key>`, so
they are scoped per actor and per run (keys persist on Stripe for at least 24 hours), and a
replay or mismatch is what Stripe says it is (`Idempotent-Replayed` header, `idempotency_error`).
"""
import base64
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from ..sim.agent_gateway import (
    CREATE_UID,
    DEFAULT_RULES,
    ERROR_INVALID_REQUEST,
    AgentPaymentRules,
    CallLog,
    agent_payment_truth,
    agent_uid,
    valid_create,
)
from ..sim.clock import EventClock, WallClockDriver  # noqa: F401  (re-exported)
from ..sim.faults import FaultConfig, fault_draw, outage_windows
from ..sim.processor import GENESIS_HASH, SINK_ACTOR, entry_hash
from ..sim.protocol import (
    ALREADY_CANCELLED,
    CANCELLED,
    CAPTURED,
    DENIED,
    ERROR_IDEMPOTENCY_MISMATCH,
    ERROR_UNKNOWN_PAYMENT,
    PENDING,
    PROCUREMENT_AGENT,
    REMEDIATION_OPERATOR,
    TOO_LATE_CAPTURED,
    CreateReply,
    Reply,
    StatusReply,
    StopReceipt,
)
from ..sim.scenario import Scenario

API_BASE = "https://api.stripe.com"
STRIPE_VERSION = "2026-09-30.endive"   # pinned; https://docs.stripe.com/api/versioning
KEY_ENV = "STRIPE_TEST_KEY"
TEST_KEY_PREFIX = "sk_test_"
TEST_PAYMENT_METHOD = "pm_card_visa"   # https://docs.stripe.com/testing
MAX_KEY_LENGTH = 255                   # https://docs.stripe.com/api/idempotent_requests
# Sandbox global limit is 25 requests/s (https://docs.stripe.com/rate-limits); stay well below.
DEFAULT_RATE, DEFAULT_BURST = 8.0, 4

# PaymentIntent status -> sim payment state (https://docs.stripe.com/payments/paymentintents/lifecycle)
STATE_BY_PI_STATUS = {"requires_capture": PENDING, "canceled": CANCELLED, "succeeded": CAPTURED}
ERROR_STRIPE_PREFIX = "STRIPE_"   # any other Stripe error, e.g. STRIPE_payment_intent_unexpected_state


class StripeKeyError(RuntimeError):
    """Missing or non-test key. The message never contains the key."""


class StripeUnavailable(RuntimeError):
    """The adapter could not learn what Stripe did, so the run's log cannot be trusted."""


class TransportError(Exception):
    """No response reached the caller (network error, timeout, or discarded by the proxy)."""


def check_test_key(key) -> str:
    if not isinstance(key, str) or not key.startswith(TEST_KEY_PREFIX) or len(key) <= len(TEST_KEY_PREFIX):
        raise StripeKeyError(f"refusing Stripe key: only a test-mode secret key ({TEST_KEY_PREFIX}...) is allowed")
    return key


def load_test_key(environ: Mapping[str, str] | None = None) -> str:
    """The key from STRIPE_TEST_KEY, and from nowhere else."""
    env = os.environ if environ is None else environ
    if not env.get(KEY_ENV):
        raise StripeKeyError(f"{KEY_ENV} is not set")
    return check_test_key(env[KEY_ENV])


# -- HTTP ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]   # lower-case names
    body: dict

    @property
    def replayed(self) -> bool:
        return self.headers.get("idempotent-replayed", "").lower() == "true"

    @property
    def error(self) -> dict:
        err = self.body.get("error") if isinstance(self.body, dict) else None
        return err if isinstance(err, dict) else {}


@runtime_checkable
class HttpClient(Protocol):
    def request(self, method: str, path: str, params: dict | None = None,
                headers: dict | None = None) -> HttpResponse: ...


def form_encode(params: dict | None) -> str:
    """Stripe's form encoding: nested dicts as a[b]=v, lists as a[]=v, booleans as true/false."""
    pairs: list[tuple[str, str]] = []

    def walk(prefix, value):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(f"{prefix}[{k}]" if prefix else str(k), v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(f"{prefix}[]", v)
        elif isinstance(value, bool):
            pairs.append((prefix, "true" if value else "false"))
        elif value is not None:
            pairs.append((prefix, str(value)))

    walk("", params or {})
    return urllib.parse.urlencode(pairs)


class UrllibHttpClient:
    """The real HTTP layer (stdlib only). Talks only to API_BASE."""

    def __init__(self, key: str, *, timeout: float = 20.0, stripe_version: str = STRIPE_VERSION,
                 urlopen: Callable = urllib.request.urlopen):
        auth = base64.b64encode(f"{check_test_key(key)}:".encode()).decode()
        self.__auth = f"Basic {auth}"     # name-mangled; never in repr or errors
        self._timeout = timeout
        self._version = stripe_version
        self._urlopen = urlopen

    def __repr__(self) -> str:
        return f"UrllibHttpClient(base={API_BASE!r}, key=<redacted>, version={self._version!r})"

    def request(self, method, path, params=None, headers=None) -> HttpResponse:
        if method not in ("GET", "POST") or not path.startswith("/v1/") or "//" in path or ".." in path:
            raise ValueError(f"refusing request {method} {path}")
        data = form_encode(params)
        url = API_BASE + path + (f"?{data}" if method == "GET" and data else "")
        req = urllib.request.Request(url, method=method,
                                     data=data.encode() if method == "POST" else None)
        req.add_header("Authorization", self.__auth)
        req.add_header("Stripe-Version", self._version)
        if method == "POST":
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with self._urlopen(req, timeout=self._timeout) as resp:
                return HttpResponse(resp.status, _lower(resp.headers), _json(resp.read()))
        except urllib.error.HTTPError as exc:
            with exc:
                return HttpResponse(exc.code, _lower(exc.headers), _json(exc.read()))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransportError(type(exc).__name__) from None


def _lower(headers) -> dict[str, str]:
    return {k.lower(): v for k, v in (headers.items() if headers is not None else ())}


def _json(raw: bytes) -> dict:
    try:
        body = json.loads(raw or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


class TokenBucket:
    """Client-side token bucket (https://docs.stripe.com/rate-limits, "Handle limiting")."""

    def __init__(self, rate: float = DEFAULT_RATE, burst: int = DEFAULT_BURST, *,
                 monotonic: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        if rate <= 0 or burst < 1:
            raise ValueError("rate must be > 0 and burst >= 1")
        self.rate, self.burst = float(rate), float(burst)
        self._monotonic, self._sleep = monotonic, sleep
        self._tokens, self._last = self.burst, monotonic()

    def _refill(self) -> None:
        now = self._monotonic()
        self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
        self._last = now

    def acquire(self) -> None:
        self._refill()
        if self._tokens < 1:
            self._sleep((1 - self._tokens) / self.rate)
            self._refill()
            self._tokens = max(self._tokens, 1.0)  # a coarse sleep may return a hair early
        self._tokens -= 1


class RateLimitedClient:
    """Token bucket in front of every request; a 429 (not cached under the idempotency key) or
    a 409 `idempotency_key_in_use` is retried with the same key and parameters, with
    exponential backoff (https://docs.stripe.com/error-low-level)."""

    def __init__(self, inner: HttpClient, bucket: TokenBucket | None = None, *, max_retries: int = 4,
                 backoff: float = 0.5, sleep: Callable[[float], None] = time.sleep):
        self._inner, self._bucket = inner, bucket or TokenBucket()
        self._max_retries, self._backoff, self._sleep = max_retries, backoff, sleep
        self.retries = 0

    def request(self, method, path, params=None, headers=None) -> HttpResponse:
        attempt = 0
        while True:
            self._bucket.acquire()
            resp = self._inner.request(method, path, params, headers)
            busy = resp.status == 429 or (resp.status == 409 and resp.error.get("code") == "idempotency_key_in_use")
            if not busy or attempt >= self._max_retries:
                return resp
            self.retries += 1
            self._sleep(self._backoff * 2 ** attempt)
            attempt += 1


def default_client(environ: Mapping[str, str] | None = None) -> RateLimitedClient:
    return RateLimitedClient(UrllibHttpClient(load_test_key(environ)))


# -- faults ---------------------------------------------------------------------------------------
def classify(method: str, path: str) -> tuple[str | None, str | None]:
    """(op, PaymentIntent id) for a remediator call: cancel or status; (None, None) otherwise."""
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[:2] == ["v1", "payment_intents"]:
        if method == "POST" and len(parts) == 4 and parts[3] == "cancel":
            return "cancel", parts[2]
        if method == "GET" and len(parts) == 3:
            return "status", parts[2]
    return None, None


@dataclass(frozen=True)
class ProxyRecord:
    op: str
    uid: str
    attempt: int
    fault: str   # ok | drop_req | drop_resp | outage_commit | outage_lost


class StripeFaultProxy:
    """Transport faults between the remediator and Stripe, at the HTTP layer.

    Same realization as the simulator's FaultyTransport: a call's fault is
    fault_draw(seed, cfg, op, uid, attempt), `attempt` counting that op on that payment, and
    outage windows depend only on the seed (needs `now`). drop_req never sends; drop_resp sends
    and discards the response; in an outage a cancel is sent (and its response discarded) with
    prob r, else nothing is sent. Every fault reaches the caller as TransportError. Requests
    that are not a remediator cancel or status of a known payment pass through untouched.
    """

    def __init__(self, inner: HttpClient, cfg: FaultConfig, seed: int, *,
                 uid_of: Callable[[str], str | None], now: Callable[[], int] | None = None):
        self._inner, self.cfg, self.seed = inner, cfg, seed
        self._uid_of, self._now = uid_of, now
        self._attempts: dict[tuple[str, str], int] = {}
        self._windows: list[tuple[int, int]] = []
        self._windows_until = 0
        self.trace: list[ProxyRecord] = []

    def in_outage(self, t: int) -> bool:
        if t >= self._windows_until:
            self._windows_until = max(2 * self._windows_until, t + 1, 64)
            self._windows = outage_windows(self.seed, self.cfg, self._windows_until)
        return any(a <= t < b for a, b in self._windows)

    def request(self, method, path, params=None, headers=None) -> HttpResponse:
        op, pi = classify(method, path)
        uid = self._uid_of(pi) if op else None
        if uid is None:
            return self._inner.request(method, path, params, headers)
        attempt = self._attempts.get((op, uid), 0)
        self._attempts[(op, uid)] = attempt + 1
        draw = fault_draw(self.seed, self.cfg, op, uid, attempt)
        if self._now is not None and self.in_outage(self._now()):
            if op == "cancel" and draw.outage_commit:
                self._inner.request(method, path, params, headers)
                return self._fail(op, uid, attempt, "outage_commit")
            return self._fail(op, uid, attempt, "outage_lost")
        if draw.drop_req:
            return self._fail(op, uid, attempt, "drop_req")
        if draw.drop_resp:
            self._inner.request(method, path, params, headers)
            return self._fail(op, uid, attempt, "drop_resp")
        resp = self._inner.request(method, path, params, headers)
        self.trace.append(ProxyRecord(op, uid, attempt, "ok"))
        return resp

    def _fail(self, op, uid, attempt, fault):
        self.trace.append(ProxyRecord(op, uid, attempt, fault))
        raise TransportError(fault)


class _Recorder:
    """Below the fault proxy: hands every response the remediator's requests really got to
    `on_response`. A real network failure is reconciled by resending the same request (same
    idempotency key and parameters, so Stripe returns the stored result)."""

    def __init__(self, inner: HttpClient, on_response: Callable[[HttpResponse], None], *,
                 retries: int = 3, sleep: Callable[[float], None] = time.sleep):
        self._inner, self._on_response = inner, on_response
        self._retries, self._sleep = retries, sleep
        self.reconciled = 0

    def request(self, method, path, params=None, headers=None) -> HttpResponse:
        for attempt in range(self._retries + 1):
            try:
                resp = self._inner.request(method, path, params, headers)
            except TransportError:
                if attempt == self._retries:
                    raise StripeUnavailable(f"no response from Stripe for {method} {path}") from None
                self.reconciled += 1
                self._sleep(0.5 * 2 ** attempt)
                continue
            self._on_response(resp)
            return resp
        raise AssertionError("unreachable")


# -- the processor ----------------------------------------------------------------------------------
@dataclass(frozen=True)
class Observation:
    """Sanitized record of one Stripe response (no body, never a secret)."""
    t: int | None
    who: str          # agent | remediator | merchant | adapter
    op: str           # create | cancel | status | capture
    uid: str | None
    pi: str | None
    http: int
    replayed: bool
    error_type: str | None
    error_code: str | None
    pi_status: str | None
    request_id: str | None


@dataclass
class _Call:
    op: str
    actor: str
    uid: str
    key: str | None
    reply: object = None


class StripeTestProcessor:
    def __init__(self, http: HttpClient, *, run_id: str, seed: int,
                 rules: AgentPaymentRules = DEFAULT_RULES, faults: FaultConfig | None = None,
                 creators=(PROCUREMENT_AGENT,), currency: str = "usd",
                 payment_method: str = TEST_PAYMENT_METHOD, sleep: Callable[[float], None] = time.sleep,
                 clock_factory: Callable[[], EventClock] = EventClock):
        if not run_id or "/" in run_id:
            raise ValueError("run_id must be a non-empty string without '/'")
        self.run_id, self.seed, self.rules = run_id, seed, rules
        self._http = http
        self._creators = frozenset(creators)
        self._currency, self._payment_method = currency, payment_method
        self._sleep = sleep
        self._clock_factory = clock_factory
        self._stop_lock = threading.Lock()   # create vs incident_stop
        self._log_lock = threading.Lock()
        self._recorder = _Recorder(http, self._on_remediator_response, sleep=sleep)
        self.proxy = (StripeFaultProxy(self._recorder, faults, seed, uid_of=self._uid_of_pi, now=self._now)
                      if faults is not None else None)
        self._wire: HttpClient = self.proxy or self._recorder
        self._revoked: set[str] = set()
        self._created: list[tuple[str, str, int, str, str]] = []   # (uid, pi, amount, vendor, actor)
        self._uid_by_pi: dict[str, str] = {}
        self._pi_by_uid: dict[str, str] = {}
        self.observations: list[Observation] = []
        self.anomalies: list[str] = []
        self.scenario: Scenario | None = None
        self.clock: EventClock | None = None
        self._calls = CallLog(lambda: self.stopped, lambda: self.clock.now() if self.clock else None)
        self._reset_log()
        self._pending: _Call | None = None

    # -- state -----------------------------------------------------------------------------
    @property
    def stopped(self) -> bool:
        return self.scenario is not None

    @property
    def log(self) -> tuple:
        return tuple(self._log)

    @property
    def calls(self) -> tuple:
        return self._calls.entries

    def pi_of(self, uid: str) -> str | None:
        return self._pi_by_uid.get(uid)

    def _uid_of_pi(self, pi: str | None) -> str | None:
        return self._uid_by_pi.get(pi) if pi else None

    def _now(self) -> int:
        return self.clock.now() if self.clock is not None else 0

    def _stripe_key(self, actor: str, key: str) -> str:
        full = f"p4/{self.run_id}/{actor}/{key}"
        if len(full) > MAX_KEY_LENGTH:
            raise ValueError("idempotency key longer than Stripe's 255 characters")
        return full

    # -- event log (same schema and hash chain as SimProcessor) ------------------------------
    def _reset_log(self) -> None:
        self._log: list[MappingProxyType] = []
        self._state: dict[str, str] = {}
        self._effect_seq: dict[str, int] = {}
        self._keys: dict[tuple[str, str], tuple[str, str]] = {}   # (actor, key) -> (uid, result)

    def _append(self, type_: str, uid: str, actor: str, key: str | None, result: str) -> int:
        with self._log_lock:
            seq = len(self._log)
            prev = self._log[-1]["hash"] if self._log else GENESIS_HASH
            body = {"seq": seq, "t": self._now(), "type": type_, "uid": uid, "actor": actor,
                    "key": key, "result": result, "prev_hash": prev}
            body["hash"] = entry_hash(prev, body)
            self._log.append(MappingProxyType(body))
            return seq

    def export_log(self, path) -> None:
        with Path(path).open("w", encoding="utf-8", newline="\n") as fh:
            fh.writelines(json.dumps(dict(e), sort_keys=True, separators=(",", ":")) + "\n" for e in self._log)

    def export_calls(self, path) -> None:
        self._calls.export(path)

    def export_observations(self, path) -> None:
        with Path(path).open("w", encoding="utf-8", newline="\n") as fh:
            fh.writelines(json.dumps(o.__dict__, sort_keys=True, separators=(",", ":")) + "\n"
                          for o in self.observations)

    def _observe(self, who, op, uid, resp: HttpResponse) -> Observation:
        err = resp.error
        pi_obj = err.get("payment_intent") if isinstance(err.get("payment_intent"), dict) else None
        body_pi = resp.body if resp.body.get("object") == "payment_intent" else None
        pi_src = body_pi or pi_obj or {}
        obs = Observation(t=self.clock.now() if self.clock else None, who=who, op=op, uid=uid,
                          pi=pi_src.get("id") or (self._pi_by_uid.get(uid) if uid else None),
                          http=resp.status, replayed=resp.replayed, error_type=err.get("type"),
                          error_code=err.get("code"), pi_status=pi_src.get("status"),
                          request_id=resp.headers.get("request-id"))
        self.observations.append(obs)
        return obs

    # -- agent-facing ------------------------------------------------------------------------
    def create_payment(self, actor: str, amount: int, vendor: str, idem_key: str) -> CreateReply:
        with self._stop_lock:
            return self._create_payment(actor, amount, vendor, idem_key)

    def _create_payment(self, actor, amount, vendor, idem_key) -> CreateReply:
        if self.stopped or actor in self._revoked or actor not in self._creators:
            seq = self._append("DENIED", CREATE_UID, actor, idem_key, DENIED) if self.stopped else None
            return self._calls.add("create", actor, CreateReply(result=DENIED), key=idem_key,
                                   amount=amount, vendor=vendor, seq=seq)
        if not valid_create(amount, vendor, idem_key):
            return self._calls.add("create", actor, CreateReply(error=ERROR_INVALID_REQUEST), key=idem_key,
                                   amount=amount, vendor=vendor)
        params = {"amount": amount, "currency": self._currency, "capture_method": "manual",
                  "confirm": True, "payment_method": self._payment_method,
                  "automatic_payment_methods": {"enabled": True, "allow_redirects": "never"},
                  "description": f"p4 test purchase from {vendor}",
                  "metadata": {"p4_run": self.run_id, "p4_actor": actor, "p4_vendor": vendor,
                               "p4_key": idem_key}}
        try:
            resp = self._http.request("POST", "/v1/payment_intents", params,
                                      {"Idempotency-Key": self._stripe_key(actor, idem_key)})
        except TransportError:
            return self._calls.add("create", actor, CreateReply(timeout=True), key=idem_key,
                                   amount=amount, vendor=vendor)
        obs = self._observe("agent", "create", None, resp)
        if resp.status == 200 and obs.pi_status == "requires_capture" and obs.pi:
            uid = self._uid_by_pi.get(obs.pi)
            if uid is None:
                uid = agent_uid(len(self._created))
                self._created.append((uid, obs.pi, amount, vendor, actor))
                self._uid_by_pi[obs.pi], self._pi_by_uid[uid] = uid, obs.pi
                if resp.replayed:
                    self.anomalies.append(f"create {idem_key}: first response for this key was a replay")
            reply = CreateReply(payment_id=uid, state=PENDING, replayed=resp.replayed)
        elif obs.error_type == "idempotency_error":
            reply = CreateReply(error=ERROR_IDEMPOTENCY_MISMATCH)
        else:
            reply = CreateReply(error=ERROR_STRIPE_PREFIX + (obs.error_code or obs.pi_status or str(resp.status)))
        return self._calls.add("create", actor, reply, uid=reply.payment_id, key=idem_key,
                               amount=amount, vendor=vendor)

    def incident_stop(self, actor: str) -> StopReceipt:
        with self._stop_lock:
            if not self.stopped:
                payments = tuple(agent_payment_truth(self.seed, i, amount, vendor, self.rules)
                                 for i, (_, _, amount, vendor, _) in enumerate(self._created))
                self.scenario = Scenario(payments=payments, seed=self.seed)
                self.attach(self._clock_factory())
            self._revoked.add(actor)
            self._calls.add_entry("incident_stop", actor, result="REVOKED")
            return StopReceipt(revoked=actor, pending=tuple(c[0] for c in self._created))

    def attach(self, clock: EventClock) -> "StripeTestProcessor":
        """Bind the log and the capture emulator to `clock` (fresh, t = 0). Allowed until the
        first call after the stop; the harness uses it via `sink_factory`. The stop's own clock
        (`self.clock`) keeps the current log, including calls made since the stop."""
        if not self.stopped:
            raise RuntimeError("attach() needs incident_stop() first")
        if clock is self.clock:
            return self
        if clock.now() != 0:
            raise ValueError("attach() needs a fresh clock at t=0 (revocation time)")
        if any(e["type"] != "ACCEPTED" for e in self._log):
            raise RuntimeError("attach() after calls were made; the PaymentIntents are already used")
        self.clock = clock
        self._reset_log()
        for p in self.scenario.payments:
            self._state[p.uid] = PENDING
            self._append("ACCEPTED", p.uid, SINK_ACTOR, None, PENDING)
            clock.schedule(p.D_actual, lambda uid=p.uid: self._merchant_capture(uid))
        return self

    def sink_factory(self, scenario: Scenario, clock: EventClock) -> "StripeTestProcessor":
        if scenario is not self.scenario:
            raise ValueError("sink_factory serves only this adapter's scenario")
        return self.attach(clock)

    # -- merchant-capture emulator ---------------------------------------------------------------
    def _merchant_capture(self, uid: str) -> None:
        """Runs at D_actual. Always sends the capture, so a cancelled payment shows Stripe's
        refusal; only a successful capture of a PENDING payment writes CAPTURED."""
        key = self._stripe_key("merchant", f"{uid}/capture")
        path = f"/v1/payment_intents/{self._pi_by_uid[uid]}/capture"
        for attempt in range(4):
            try:
                resp = self._http.request("POST", path, None, {"Idempotency-Key": key})
                break
            except TransportError:
                self._sleep(0.5 * 2 ** attempt)
        else:
            raise StripeUnavailable(f"merchant capture of {uid}: no response")
        obs = self._observe("merchant", "capture", uid, resp)
        captured = resp.status == 200 and obs.pi_status == "succeeded"
        if captured and self._state[uid] == PENDING:
            self._state[uid] = CAPTURED
            self._effect_seq[uid] = self._append(CAPTURED, uid, SINK_ACTOR, None, CAPTURED)
        elif captured or (self._state[uid] == PENDING):
            self.anomalies.append(f"t={self._now()} capture {uid}: http {resp.status} "
                                  f"{obs.error_code} pi_status={obs.pi_status}, log state {self._state[uid]}")

    # -- Processor protocol -------------------------------------------------------------------------
    def cancel(self, actor: str, uid: str, idem_key: str) -> Reply:
        reply = self._cancel(actor, uid, idem_key)
        return self._calls.add("cancel", actor, reply, uid=uid, key=idem_key, seq=reply.seq)

    def _cancel(self, actor, uid, idem_key) -> Reply:
        if not self.stopped:
            return Reply(result=DENIED)
        if actor != REMEDIATION_OPERATOR or actor in self._revoked or (
                uid in self._pi_by_uid and not self._permitted(uid)):
            return Reply(result=DENIED, seq=self._append("DENIED", uid, actor, idem_key, DENIED))
        if uid not in self._pi_by_uid:
            return Reply(error=ERROR_UNKNOWN_PAYMENT,
                         seq=self._append("CANCEL_REQ", uid, actor, idem_key, ERROR_UNKNOWN_PAYMENT))
        return self._send(_Call("cancel", actor, uid, idem_key), "POST",
                          f"/v1/payment_intents/{self._pi_by_uid[uid]}/cancel",
                          {"Idempotency-Key": self._stripe_key(actor, idem_key)}, Reply(timeout=True))

    def status(self, actor: str, uid: str) -> StatusReply:
        reply = self._status(actor, uid)
        return self._calls.add("status", actor, reply, uid=uid, seq=reply.seq)

    def _status(self, actor, uid) -> StatusReply:
        if not self.stopped:
            if actor in self._creators and uid in self._pi_by_uid:
                try:
                    resp = self._http.request("GET", f"/v1/payment_intents/{self._pi_by_uid[uid]}")
                except TransportError:
                    return StatusReply(timeout=True)
                obs = self._observe("agent", "status", uid, resp)
                state = STATE_BY_PI_STATUS.get(obs.pi_status)
                return StatusReply(state=state) if state else StatusReply(
                    error=ERROR_STRIPE_PREFIX + str(obs.error_code or obs.pi_status))
            return StatusReply(error=ERROR_UNKNOWN_PAYMENT if actor in self._creators else DENIED)
        if actor != REMEDIATION_OPERATOR or actor in self._revoked:
            return StatusReply(error=DENIED, seq=self._append("DENIED", uid, actor, None, DENIED))
        if uid not in self._pi_by_uid:
            return StatusReply(error=ERROR_UNKNOWN_PAYMENT,
                               seq=self._append("STATUS_READ", uid, actor, None, ERROR_UNKNOWN_PAYMENT))
        return self._send(_Call("status", actor, uid, None), "GET",
                          f"/v1/payment_intents/{self._pi_by_uid[uid]}", None, StatusReply(timeout=True))

    def _permitted(self, uid: str) -> bool:
        return next(p.permitted for p in self.scenario.payments if p.uid == uid)

    def _send(self, call: _Call, method, path, headers, timeout_reply):
        self._pending = call
        try:
            self._wire.request(method, path, None, headers)
        except TransportError:
            return timeout_reply   # the log has a line iff Stripe got the request (drop_resp)
        finally:
            self._pending = None
        return call.reply

    def _on_remediator_response(self, resp: HttpResponse) -> None:
        call = self._pending
        obs = self._observe("remediator", call.op, call.uid, resp)
        if call.op == "status":
            state = STATE_BY_PI_STATUS.get(obs.pi_status) if resp.status == 200 else None
            if state is None:
                err = ERROR_STRIPE_PREFIX + str(obs.error_code or obs.pi_status or resp.status)
                call.reply = StatusReply(error=err, seq=self._append("STATUS_READ", call.uid, call.actor, None, err))
                return
            if state != self._state[call.uid]:
                self.anomalies.append(f"t={self._now()} status {call.uid}: Stripe {obs.pi_status}, log {self._state[call.uid]}")
            seq = self._append("STATUS_READ", call.uid, call.actor, None, state)
            call.reply = StatusReply(state=state, seq=seq, effect_seq=self._effect_seq.get(call.uid))
            return
        self._record_cancel(call, resp, obs)

    def _record_cancel(self, call: _Call, resp: HttpResponse, obs: Observation) -> None:
        uid, actor, key = call.uid, call.actor, call.key
        if obs.error_type == "idempotency_error":
            seq = self._append("MISMATCH", uid, actor, key, ERROR_IDEMPOTENCY_MISMATCH)
            call.reply = Reply(error=ERROR_IDEMPOTENCY_MISMATCH, seq=seq)
            return
        if resp.status == 200 and obs.pi_status == "canceled":
            result = CANCELLED
        elif obs.error_code == "payment_intent_unexpected_state":
            pi_status = obs.pi_status or self._silent_status(uid)
            result = {"canceled": ALREADY_CANCELLED, "succeeded": TOO_LATE_CAPTURED}.get(pi_status)
        else:
            result = None
        if result is None:   # not a state answer: an error with no effect, not stored
            err = ERROR_STRIPE_PREFIX + str(obs.error_code or obs.pi_status or resp.status)
            call.reply = Reply(error=err, seq=self._append("CANCEL_REQ", uid, actor, key, err))
            self.anomalies.append(f"t={self._now()} cancel {uid}: unmapped response {err}")
            return

        stored = self._keys.get((actor, key))
        if stored is not None and resp.replayed:
            if stored != (uid, result):
                self.anomalies.append(f"t={self._now()} replay {key}: Stripe {result}, stored {stored}")
            seq = self._append("IDEMPOTENT_REPLAY", uid, actor, key, result)
            call.reply = Reply(result=result, seq=seq, effect_seq=self._effect_seq.get(uid))
            return
        if stored is not None:
            self.anomalies.append(f"t={self._now()} cancel {uid}: Stripe re-executed key {key}")
        expected = {PENDING: CANCELLED, CANCELLED: ALREADY_CANCELLED, CAPTURED: TOO_LATE_CAPTURED}[self._state[uid]]
        if result != expected:
            self.anomalies.append(f"t={self._now()} cancel {uid}: Stripe says {result}, log state {self._state[uid]}")
        seq = self._append("CANCEL_REQ", uid, actor, key, result)
        if result == CANCELLED and self._state[uid] == PENDING:
            self._state[uid] = CANCELLED
            self._effect_seq[uid] = self._append(CANCELLED, uid, actor, key, CANCELLED)
        self._keys[(actor, key)] = (uid, result)
        call.reply = Reply(result=result, seq=seq, effect_seq=self._effect_seq.get(uid))

    def _silent_status(self, uid: str) -> str | None:
        """Evaluator-side read to classify an error without a PaymentIntent; not logged as a
        remediator status read."""
        try:
            resp = self._http.request("GET", f"/v1/payment_intents/{self._pi_by_uid[uid]}")
        except TransportError:
            return None
        return self._observe("adapter", "status", uid, resp).pi_status

