"""Event-driven runbook engine shared by P0-P6.

`Runbook.run(observations, transport, clock, journal, k)` drives k workers on the shared
clock. Whenever a worker is free the policy's `next_action(now)` returns a cancel, a replay
(the same cancel key sent again: a cancel and a probe in one), a status read, or None
(idle). Replies come back through the transport's callbacks. The run ends when no call is in
flight, no wake-up is pending and the policy has nothing left to do. The harness, not the
runbook, then advances the clock to the true horizon.

A runbook sees only Observations, the AsyncProcessor protocol, the clock and the journal
(CLAUDE.md rule 1; tests/leakage). It never has two calls in flight for one payment.

After the last nominal deadline (`end_of_watch`), evidence-bounded runbooks (P4-P6) run an
accounting sweep: a status read for every payment without a closed disposition, retried up
to SWEEP_RETRIES times on timeout. With `late_cancel` (default on, identical for P4-P6), a
sweep read that still says PENDING for a permitted payment with gain > 0 is followed by a
cancel with the same key: a late capture may still be preventable after d_nominal.

Crash and restart (WP3). A test may set `crash_hook(point, action, reply, runbook)`; it is
called at each CRASH_POINTS position of every call and may raise SimulatedCrash. The crashed
instance is then dead: replies to its in-flight calls still reach the sink but are dropped,
and its wake-ups do nothing. A fresh instance given the same journal rebuilds its state from
the journal (`_recover`) and carries on from the current clock time: it re-plans exactly as
at t = 0, skips payments it already attempted, and replays every journaled result through
the same evidence rules and policy hook as the live run. An intent with no journaled result
is an outcome the remediator cannot know; `recover_orphans` decides what to do with it. The
default (P4-P6) journals it as a timeout and handles it like one; ack-trust runbooks re-send
it instead (outbox recovery, ack_trust.py).
"""
import math
from dataclasses import dataclass

from ..contract import (
    BASIS_NO_EVIDENCE,
    BASIS_REPLAY,
    BASIS_REPLY,
    BASIS_STATUS,
    Disposition,
    DispositionEntry,
    DispositionReport,
)
from ..journal import Journal
from ..sched import gain
from ..sim.protocol import (
    ALREADY_CANCELLED,
    CANCELLED,
    CAPTURED,
    PENDING,
    REMEDIATION_OPERATOR,
    TOO_LATE_CAPTURED,
    AsyncProcessor,
    Reply,
    StatusReply,
)

CANCEL, REPLAY, STATUS = "cancel", "replay", "status"
SWEEP_RETRIES = 3
# (a) intent journaled, call not sent; (b) sent, no reply yet; (c) reply received, result not
# journaled; (d) result journaled and applied, before the worker's next call.
BEFORE_SEND, AFTER_SEND, AFTER_REPLY, AFTER_JOURNAL = "before_send", "after_send", "after_reply", "after_journal"
CRASH_POINTS = (BEFORE_SEND, AFTER_SEND, AFTER_REPLY, AFTER_JOURNAL)


class SimulatedCrash(Exception):
    """Raised by a crash hook to kill the remediator process at a crash point."""


@dataclass(frozen=True)
class Action:
    op: str   # cancel | replay | status
    uid: str


def cancel_key(incident_id: str, uid: str) -> str:
    """The one idempotency key for cancelling `uid` in this incident; reused on every retry."""
    return f"{incident_id}/{uid}/cancel/v1"


def effective_k(k, n: int) -> int:
    """k = inf (or None) means one worker per payment, the most that can ever be busy."""
    if k is None or k == math.inf:
        return max(n, 1)
    if int(k) != k or k < 1:
        raise ValueError(f"k must be a positive integer or inf, got {k!r}")
    return int(k)


def outcome(reply, recovered: bool = False) -> dict:
    if isinstance(reply, StatusReply):
        return {"state": reply.state, "error": reply.error, "timeout": reply.timeout,
                "seq": reply.seq, "effect_seq": reply.effect_seq, "recovered": recovered}
    return {"result": reply.result, "error": reply.error, "timeout": reply.timeout,
            "seq": reply.seq, "effect_seq": reply.effect_seq, "recovered": recovered}


def reply_from(record) -> Reply | StatusReply:
    """The reply a journaled result record describes."""
    common = {"error": record["error"], "timeout": record["timeout"], "seq": record["seq"],
              "effect_seq": record["effect_seq"]}
    if record["op"] == STATUS:
        return StatusReply(state=record["state"], **common)
    return Reply(result=record["result"], **common)


class Runbook:
    name = "base"
    evidence_bounded = True
    crash_hook = None

    def __init__(self, c: float = 0.5, incident_id: str = "INC-0", late_cancel: bool = True):
        self.c = c
        self.incident_id = incident_id
        self.late_cancel = late_cancel

    # -- policy hooks ------------------------------------------------------------------
    def start(self) -> None:
        """Called once at t = 0, before the first dispatch."""

    def next_action(self, now: int) -> Action | None:
        raise NotImplementedError

    def on_reply(self, action: Action, reply) -> None:
        """Called after the engine has recorded any definitive evidence in `reply`."""

    def finalize_open(self, uid: str, now: int) -> DispositionEntry:
        """Disposition of a payment still open when the run ends."""
        state = (Disposition.UNRESOLVED_PENDING if self.last_read.get(uid) == PENDING
                 else Disposition.UNRESOLVED_UNKNOWN)
        return DispositionEntry(uid, state, BASIS_NO_EVIDENCE, now)

    # -- engine --------------------------------------------------------------------------
    def run(self, observations, transport: AsyncProcessor, clock, journal: Journal, k) -> DispositionReport:
        self.obs = {o.uid: o for o in observations}
        self.order = [o.uid for o in observations]
        self.transport, self.clock, self.journal = transport, clock, journal
        self.k = effective_k(k, len(self.order))
        self.closed: dict[str, DispositionEntry] = {}
        self.attempted: set[str] = set()
        self.unknown: set[str] = set()      # a cancel was sent, no definitive answer yet
        self.last_read: dict[str, str] = {}
        self.inflight: dict[str, int] = {}  # uid -> time its call completes (cancels exact)
        self._free = self.k
        self._wakeups = 0
        self._sweep_tries: dict[str, int] = {}
        self._swept_pending: set[str] = set()
        self.end_of_watch = max((o.d_nominal for o in observations), default=0)
        self.post_deadline = False
        self._replay_t: int | None = None
        self._alive = True

        try:
            self.start()
            self._recover()
            self.wake_at(self.end_of_watch, self._on_end_of_watch)
            self._dispatch()
            while self.inflight or self._wakeups:
                self.clock.run_until(self.clock.next_time())
        except SimulatedCrash:
            self._alive = False
            raise
        now = self.clock.now()
        entries = tuple(self.closed[u] if u in self.closed else self.finalize_open(u, now)
                        for u in self.order)
        return DispositionReport(self.name, self.incident_id, self.evidence_bounded, entries)

    def wake_at(self, t: int, hook=None) -> None:
        self._wakeups += 1

        def fire():
            if not self._alive:
                return
            self._wakeups -= 1
            if hook is not None:
                hook()
            self._dispatch()

        self.clock.schedule(max(t, self.clock.now()), fire)

    def _on_end_of_watch(self) -> None:
        self.post_deadline = True

    def _dispatch(self) -> None:
        while self._free > 0:
            action = self.next_action(self.clock.now())
            if action is None:
                return
            self._send(action)

    def _send(self, action: Action) -> None:
        uid = action.uid
        if uid in self.inflight:
            raise AssertionError(f"{self.name}: second call in flight for {uid}")
        if uid in self.closed:
            raise AssertionError(f"{self.name}: call for already-closed {uid}")
        now = self.clock.now()
        self._free -= 1
        key = None if action.op == STATUS else cancel_key(self.incident_id, uid)
        self.journal.append("intent", t=now, op=action.op, uid=uid, key=key)
        self._crash(BEFORE_SEND, action)

        def done(reply):
            if not self._alive:
                return  # the process that sent this call is gone; its reply is lost
            self._crash(AFTER_REPLY, action, reply)
            del self.inflight[uid]
            self._free += 1
            self.journal.append("result", t=self.clock.now(), op=action.op, uid=uid, key=key,
                                **outcome(reply))
            self._record(action, reply)
            self.on_reply(action, reply)
            self._crash(AFTER_JOURNAL, action, reply)
            self._dispatch()

        if action.op == STATUS:
            self.inflight[uid] = now  # status latency is not needed by any planner
            self.transport.submit_status(REMEDIATION_OPERATOR, uid, done)
        else:
            self.attempted.add(uid)
            self.inflight[uid] = now + self.obs[uid].s_cancel
            self.transport.submit_cancel(REMEDIATION_OPERATOR, uid, key, done)
        self._crash(AFTER_SEND, action)

    def _crash(self, point: str, action: Action, reply=None) -> None:
        if self.crash_hook is not None:
            self.crash_hook(point, action, reply, self)

    # -- restart from the journal ----------------------------------------------------------
    def _recover(self) -> None:
        """Rebuild engine and policy state from journaled records (no-op on a new journal)."""
        records = self.journal.entries
        if not records:
            return
        open_intents: dict[int, dict] = {}   # journal index -> intent with no result yet
        for r in records:
            self._replay_t = r["t"]
            self.post_deadline = r["t"] >= self.end_of_watch
            action = Action(r["op"], r["uid"])
            if r["kind"] == "intent":
                open_intents[r["i"]] = r
                if r["op"] == STATUS:
                    if self.post_deadline:
                        self._sweep_tries[r["uid"]] = self._sweep_tries.get(r["uid"], 0) + 1
                else:
                    self.attempted.add(r["uid"])
                    if r["op"] == CANCEL:
                        self._swept_pending.discard(r["uid"])
                continue
            match = [i for i, x in open_intents.items() if x["op"] == r["op"] and x["uid"] == r["uid"]]
            if not match:
                raise ValueError(f"journal record {r['i']}: result without an intent")
            del open_intents[match[0]]
            reply = reply_from(r)
            self._record(action, reply)
            self.on_reply(action, reply)
        self._replay_t = None
        self.post_deadline = self.clock.now() >= self.end_of_watch
        self.recover_orphans(tuple(open_intents.values()))

    def recover_orphans(self, intents) -> None:
        """Journaled intents with no result after a restart. Default: the outcome is unknown,
        so journal each as a timeout and handle it like one."""
        for r in intents:
            action = Action(r["op"], r["uid"])
            reply = StatusReply(timeout=True) if r["op"] == STATUS else Reply(timeout=True)
            self.journal.append("result", t=self.clock.now(), op=r["op"], uid=r["uid"], key=r["key"],
                                **outcome(reply, recovered=True))
            self._record(action, reply)
            self.on_reply(action, reply)

    # -- evidence ------------------------------------------------------------------------
    def close(self, uid: str, state: Disposition, basis: str, reply=None, result=None) -> None:
        self.closed[uid] = DispositionEntry(
            uid, state, basis, self.clock.now() if self._replay_t is None else self._replay_t,
            evidence_seq=None if reply is None else reply.seq,
            effect_seq=None if reply is None else reply.effect_seq,
            evidence_result=result)
        self.unknown.discard(uid)

    def _record(self, action: Action, reply) -> None:
        """Close `uid` on definitive sink evidence; otherwise track what is still unknown."""
        uid = action.uid
        if isinstance(reply, StatusReply):
            if reply.state in (CANCELLED, CAPTURED):
                state = (Disposition.CLOSED_CANCELLED if reply.state == CANCELLED
                         else Disposition.CLOSED_EXPOSURE)
                self.close(uid, state, BASIS_STATUS, reply, reply.state)
            elif reply.state == PENDING:
                self.last_read[uid] = PENDING
                if self.post_deadline:
                    self._swept_pending.add(uid)
                self.unknown.discard(uid)  # no call in flight, so no earlier cancel applied
            return
        assert isinstance(reply, Reply)
        self.last_read.pop(uid, None)  # a newer cancel supersedes any earlier read
        basis = BASIS_REPLAY if action.op == REPLAY else BASIS_REPLY
        if reply.result in (CANCELLED, ALREADY_CANCELLED):
            self.close(uid, Disposition.CLOSED_CANCELLED, basis, reply, reply.result)
        elif reply.result == TOO_LATE_CAPTURED:
            self.close(uid, Disposition.CLOSED_EXPOSURE, basis, reply, reply.result)
        elif reply.timeout:
            self.unknown.add(uid)

    # -- helpers for policies ------------------------------------------------------------
    def can_finish(self, uid: str, now: int) -> bool:
        """Policy-side feasibility: a cancel sent now completes before d_nominal."""
        o = self.obs[uid]
        return now + o.s_cancel < o.d_nominal

    def is_open(self, uid: str) -> bool:
        return uid not in self.closed and uid not in self.inflight

    def fresh(self, uid: str) -> bool:
        """Never attempted and not closed. Always true for a planned payment in an
        uninterrupted run; after a restart it skips what the journal shows was done."""
        return uid not in self.attempted and uid not in self.closed

    def sweep_action(self) -> Action | None:
        """Accounting sweep after end_of_watch (evidence-bounded runbooks)."""
        if not self.post_deadline:
            return None
        if self.late_cancel:
            for uid in self.order:
                o = self.obs[uid]
                if uid in self._swept_pending and self.is_open(uid) and o.permitted \
                        and gain(o, self.c) > 0:
                    self._swept_pending.discard(uid)  # re-read after this cancel if unresolved
                    return Action(CANCEL, uid)
        for uid in self.order:
            if not self.is_open(uid) or uid in self._swept_pending:
                continue
            tries = self._sweep_tries.get(uid, 0)
            if tries <= SWEEP_RETRIES:
                self._sweep_tries[uid] = tries + 1
                return Action(STATUS, uid)
        return None
