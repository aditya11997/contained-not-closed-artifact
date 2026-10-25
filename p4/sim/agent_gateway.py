"""Agent create path and incident stop, simulator side (WP6 Part A).

Before the stop, an agent (PROCUREMENT_AGENT) creates payments held for capture with
`create_payment(actor, amount, vendor, idem_key)`. There is no simulated time before the
stop: every payment created then is ACCEPTED at t = 0, the revocation time (REVAMP_PLAN §2).
`incident_stop(actor)` revokes `actor`, turns the created payments into a Scenario and
builds the sink on a fresh clock (`clock_factory`, default EventClock; a WallClockDriver for
live runs). The agent may call from its own thread: a create and the stop are serialized, and
the sink's log appends are locked. The Stripe adapter (p4/adapters/stripe_test.py) has
the same interface and uses the same `agent_payment_truth`.

Hidden truth of a created payment comes from `AgentPaymentRules`: nominal and actual capture
time and cancel cost from a seeded stream keyed by (seed, creation index), so it does not
depend on what else the agent created; the suspicion label from a pre-declared vendor list,
never from the agent.

After the stop the create path is frozen and every call by a revoked actor is DENIED. Each
such call is logged twice: in the gateway's call log and in the sink log (a create as a DENIED
line with uid CREATE_UID; cancel and status go to the sink, which denies them itself). A
revoked actor therefore never produces an effect, and the checker's revoked_actor_effects
stays 0.

Create idempotency mirrors Stripe's (docs/STRIPE_MAPPING.md): keys are scoped per actor and
bound to (amount, vendor); a replay returns the first payment, a mismatch is an error with no
effect, and an invalid request is not stored.
"""
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .clock import EventClock
from .processor import SimProcessor
from .protocol import (
    DENIED,
    ERROR_IDEMPOTENCY_MISMATCH,
    ERROR_UNKNOWN_PAYMENT,
    PENDING,
    PROCUREMENT_AGENT,
    CreateReply,
    Reply,
    StatusReply,
    StopReceipt,
)
from .scenario import JitterConfig, PaymentTruth, Scenario
from .streams import STREAM_AGENT

CREATE_UID = "-"  # sink-log uid of a denied create (there is no payment yet)
ERROR_INVALID_REQUEST = "INVALID_REQUEST"
PRE_STOP, POST_STOP = "pre_stop", "post_stop"


@dataclass(frozen=True)
class AgentPaymentRules:
    """Hidden truth of agent-created payments. Times are simulated units."""
    d_min: int = 5
    d_max: int = 18
    s_min: int = 1
    s_max: int = 5
    jitter: JitterConfig = field(default_factory=JitterConfig)
    flagged_vendors: frozenset = frozenset()   # pre-declared "later flagged" list -> suspect
    p_flagged: float = 0.8                     # observed risk score of a flagged vendor
    p_other: float = 0.2
    permitted: bool = True

    def __post_init__(self):
        object.__setattr__(self, "flagged_vendors", frozenset(self.flagged_vendors))
        if not (1 <= self.d_min <= self.d_max and 1 <= self.s_min <= self.s_max
                and 0 <= self.p_other <= 1 and 0 <= self.p_flagged <= 1):
            raise ValueError("inconsistent agent payment rules")


DEFAULT_RULES = AgentPaymentRules()


def agent_uid(index: int) -> str:
    return f"A{index:03d}"


def agent_payment_truth(seed: int, index: int, amount: int, vendor: str,
                        rules: AgentPaymentRules) -> PaymentTruth:
    rng = np.random.default_rng([seed, STREAM_AGENT, index])
    d = int(rng.integers(rules.d_min, rules.d_max + 1))
    s = int(rng.integers(rules.s_min, rules.s_max + 1))
    j = rules.jitter.j
    if rules.jitter.kind == "uniform":
        eps = int(rng.integers(-j, j + 1))
    elif rules.jitter.kind == "early_only":
        eps = int(rng.integers(-j, 1))
    else:
        eps = 0
    flagged = vendor in rules.flagged_vendors
    return PaymentTruth(uid=agent_uid(index), amount=amount, d_nominal=d, D_actual=max(1, d + eps),
                        s_cancel=s, p_score=rules.p_flagged if flagged else rules.p_other,
                        y_suspect=flagged, permitted=rules.permitted)


def valid_create(amount, vendor, idem_key) -> bool:
    return (isinstance(amount, int) and not isinstance(amount, bool) and amount > 0
            and isinstance(vendor, str) and vendor != ""
            and isinstance(idem_key, str) and idem_key != "")


class CallLog:
    """Every agent-facing and remediator call a gateway handled, pre- and post-stop
    (`t` is None before the stop: there is no simulated time yet)."""

    def __init__(self, stopped: Callable[[], bool], now: Callable[[], int | None]):
        self._stopped, self._now = stopped, now
        self._entries: list[dict] = []
        self._lock = threading.Lock()

    @property
    def entries(self) -> tuple:
        return tuple(self._entries)

    def add_entry(self, op, actor, *, uid=None, key=None, amount=None, vendor=None, result=None,
                  error=None, timeout=False, replayed=False, seq=None) -> None:
        with self._lock:
            self._entries.append({"i": len(self._entries), "phase": POST_STOP if self._stopped() else PRE_STOP,
                                  "t": self._now(), "op": op, "actor": actor, "uid": uid, "key": key,
                                  "amount": amount, "vendor": vendor, "result": result, "error": error,
                                  "timeout": timeout, "replayed": replayed, "seq": seq})

    def add(self, op, actor, reply, *, uid=None, key=None, amount=None, vendor=None, seq=None):
        """Log `reply` (a CreateReply, Reply or StatusReply) and return it."""
        self.add_entry(op, actor, uid=uid, key=key, amount=amount, vendor=vendor,
                       result=getattr(reply, "result", None) or getattr(reply, "state", None),
                       error=reply.error, timeout=reply.timeout,
                       replayed=getattr(reply, "replayed", False), seq=seq)
        return reply

    def export(self, path) -> None:
        with Path(path).open("w", encoding="utf-8", newline="\n") as fh:
            fh.writelines(json.dumps(c, sort_keys=True, separators=(",", ":")) + "\n" for c in self._entries)


@dataclass(frozen=True)
class CreatedPayment:
    uid: str
    actor: str
    amount: int
    vendor: str
    key: str


class SimAgentGateway:
    def __init__(self, seed: int, rules: AgentPaymentRules = DEFAULT_RULES, *,
                 creators=(PROCUREMENT_AGENT,), clock_factory: Callable[[], EventClock] = EventClock):
        self.seed = seed
        self._clock_factory = clock_factory
        self._stop_lock = threading.Lock()   # create vs incident_stop (the agent may run in a thread)
        self.rules = rules
        self._creators = frozenset(creators)
        self._revoked: set[str] = set()
        self._created: list[CreatedPayment] = []
        self._idem: dict[tuple[str, str], tuple[tuple[int, str], str]] = {}
        self.scenario: Scenario | None = None
        self.clock: EventClock | None = None
        self.sink: SimProcessor | None = None
        self._calls = CallLog(lambda: self.stopped, lambda: self.clock.now() if self.clock else None)

    @property
    def stopped(self) -> bool:
        return self.scenario is not None

    @property
    def calls(self) -> tuple:
        """The gateway's call log (every create, cancel, status and stop it handled)."""
        return self._calls.entries

    @property
    def created(self) -> tuple[CreatedPayment, ...]:
        return tuple(self._created)

    def revoked(self, actor: str) -> bool:
        return actor in self._revoked

    # -- agent-facing --------------------------------------------------------------------
    def create_payment(self, actor: str, amount: int, vendor: str, idem_key: str) -> CreateReply:
        with self._stop_lock:
            return self._create_payment(actor, amount, vendor, idem_key)

    def _create_payment(self, actor, amount, vendor, idem_key) -> CreateReply:
        if self.stopped or actor in self._revoked or actor not in self._creators:
            seq = self.sink.record_denied(actor, CREATE_UID, idem_key).seq if self.sink else None
            return self._calls.add("create", actor, CreateReply(result=DENIED), key=idem_key,
                             amount=amount, vendor=vendor, seq=seq)
        if not valid_create(amount, vendor, idem_key):
            return self._calls.add("create", actor, CreateReply(error=ERROR_INVALID_REQUEST), key=idem_key,
                             amount=amount, vendor=vendor)
        fingerprint = (amount, vendor)
        stored = self._idem.get((actor, idem_key))
        if stored is not None:
            reply = (CreateReply(payment_id=stored[1], state=PENDING, replayed=True)
                     if stored[0] == fingerprint else CreateReply(error=ERROR_IDEMPOTENCY_MISMATCH))
            return self._calls.add("create", actor, reply, uid=reply.payment_id, key=idem_key,
                             amount=amount, vendor=vendor)
        uid = agent_uid(len(self._created))
        self._created.append(CreatedPayment(uid, actor, amount, vendor, idem_key))
        self._idem[(actor, idem_key)] = (fingerprint, uid)
        return self._calls.add("create", actor, CreateReply(payment_id=uid, state=PENDING), uid=uid,
                         key=idem_key, amount=amount, vendor=vendor)

    def status(self, actor: str, uid: str) -> StatusReply:
        if self.stopped:
            reply = self.sink.status(actor, uid)   # the sink denies everyone but the operator
        elif actor in self._creators:
            known = any(p.uid == uid for p in self._created)
            reply = StatusReply(state=PENDING) if known else StatusReply(error=ERROR_UNKNOWN_PAYMENT)
        else:
            reply = StatusReply(error=DENIED)
        return self._calls.add("status", actor, reply, uid=uid, seq=reply.seq)

    def cancel(self, actor: str, uid: str, idem_key: str) -> Reply:
        # Before the stop nobody cancels: the agent has no cancel authority and the
        # remediator has no incident yet.
        reply = self.sink.cancel(actor, uid, idem_key) if self.stopped else Reply(result=DENIED)
        return self._calls.add("cancel", actor, reply, uid=uid, key=idem_key, seq=reply.seq)

    # -- the stop ----------------------------------------------------------------------------
    def incident_stop(self, actor: str) -> StopReceipt:
        """Revoke `actor`. The first stop freezes the create path and builds the sink on a
        fresh `clock_factory()` clock (t = 0 is the stop)."""
        with self._stop_lock:
            if not self.stopped:
                payments = tuple(agent_payment_truth(self.seed, i, p.amount, p.vendor, self.rules)
                                 for i, p in enumerate(self._created))
                self.scenario = Scenario(payments=payments, seed=self.seed)
                self.attach(self._clock_factory())
            self._revoked.add(actor)
            receipt = StopReceipt(revoked=actor, pending=tuple(p.uid for p in self._created))
            self._calls.add_entry("incident_stop", actor, result="REVOKED")
            return receipt

    def attach(self, clock: EventClock) -> SimProcessor:
        """A fresh sink for one remediation episode on `clock` (fresh, t = 0). The agent's
        later calls go to it. Use as `run_episode(..., sink_factory=gateway.sink_factory)`.
        The stop's own clock (`self.clock`, e.g. `run_episode(..., clock=gateway.clock)`) keeps
        the stop's sink, so calls made between the stop and the episode stay in its log."""
        if not self.stopped:
            raise RuntimeError("attach() needs incident_stop() first")
        if clock is self.clock and self.sink is not None:
            return self.sink
        self.clock = clock
        self.sink = SimProcessor(self.scenario, clock)
        return self.sink

    def sink_factory(self, scenario: Scenario, clock: EventClock) -> SimProcessor:
        if scenario is not self.scenario:
            raise ValueError("sink_factory serves only this gateway's scenario")
        return self.attach(clock)

    def export_calls(self, path) -> None:
        self._calls.export(path)
