"""Event-sourced synthetic processor sink (fixes CODE_AUDIT A1-A4).

- Time comes only from the shared EventClock; no method takes a time argument (A3).
- CAPTURE(uid) is scheduled at D_actual; it is a no-op if the payment was cancelled.
- Authorization is checked before the idempotency store; denials are logged, never
  cached (A2). Keys are scoped per actor and bound to the request fingerprint (uid,
  "cancel"); a mismatched reuse is an error with no effect (A1).
- Every call and every state change is an append-only, SHA-256-chained log line (A4).

Each call writes exactly one request line (CANCEL_REQ, IDEMPOTENT_REPLAY, MISMATCH,
DENIED or STATUS_READ, whose `result` is the answer); each state change writes one effect
line (CANCELLED or CAPTURED). Replies cite both by `seq` / `effect_seq`.
"""
import hashlib
import json
import threading
from pathlib import Path
from types import MappingProxyType

from .clock import EventClock
from .protocol import (  # noqa: F401  (re-exported for callers of this module)
    ALREADY_CANCELLED,
    CANCELLED,
    CAPTURED,
    DENIED,
    ERROR_IDEMPOTENCY_MISMATCH,
    ERROR_UNKNOWN_PAYMENT,
    ORIGINAL_AGENT,
    PENDING,
    REMEDIATION_OPERATOR,
    TOO_LATE_CAPTURED,
    Processor,
    Reply,
    StatusReply,
)
from .scenario import Scenario

GENESIS_HASH = "0" * 64
SINK_ACTOR = "processor"


def entry_hash(prev_hash: str, body: dict) -> str:
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev_hash + canon).encode()).hexdigest()


class SimProcessor:
    def __init__(self, scenario: Scenario, clock: EventClock):
        if clock.now() != 0:
            raise ValueError("SimProcessor needs a fresh clock at t=0 (revocation time)")
        self.scenario = scenario
        self._clock = clock
        self._payments = {p.uid: p for p in scenario.payments}
        self._state = {uid: PENDING for uid in self._payments}
        self._effect_seq: dict[str, int] = {}
        self._idem: dict[tuple[str, str], tuple[tuple[str, str], Reply]] = {}
        self._log: list[MappingProxyType] = []
        self._log_lock = threading.Lock()
        for p in scenario.payments:
            self._append("ACCEPTED", p.uid, SINK_ACTOR, None, PENDING)
            clock.schedule(p.D_actual, lambda uid=p.uid: self._capture(uid))

    # -- log ---------------------------------------------------------------------------
    @property
    def log(self) -> tuple:
        """Read-only view of the log lines."""
        return tuple(self._log)

    def _append(self, type_: str, uid: str, actor: str, key: str | None, result: str) -> int:
        # Locked: in the WP6 case study a revoked agent's thread logs DENIED lines while the
        # remediator runs.
        with self._log_lock:
            seq = len(self._log)
            prev = self._log[-1]["hash"] if self._log else GENESIS_HASH
            body = {"seq": seq, "t": self._clock.now(), "type": type_, "uid": uid, "actor": actor,
                    "key": key, "result": result, "prev_hash": prev}
            body["hash"] = entry_hash(prev, body)
            self._log.append(MappingProxyType(body))
            return seq

    def export_log(self, path) -> None:
        with Path(path).open("w", encoding="utf-8", newline="\n") as fh:
            fh.writelines(json.dumps(dict(e), sort_keys=True, separators=(",", ":")) + "\n" for e in self._log)

    # -- effects -----------------------------------------------------------------------
    def _capture(self, uid: str) -> None:
        if self._state[uid] == PENDING:
            self._state[uid] = CAPTURED
            self._effect_seq[uid] = self._append(CAPTURED, uid, SINK_ACTOR, None, CAPTURED)

    # -- Processor protocol ------------------------------------------------------------
    def cancel(self, actor: str, uid: str, idem_key: str) -> Reply:
        # 1. Authorization first; denials are not cached under the key.
        if actor != REMEDIATION_OPERATOR or (uid in self._payments and not self._payments[uid].permitted):
            seq = self._append("DENIED", uid, actor, idem_key, DENIED)
            return Reply(result=DENIED, seq=seq)
        if uid not in self._payments:
            seq = self._append("CANCEL_REQ", uid, actor, idem_key, ERROR_UNKNOWN_PAYMENT)
            return Reply(error=ERROR_UNKNOWN_PAYMENT, seq=seq)

        # 2. Idempotency, scoped to (actor, key) and bound to the request fingerprint.
        fingerprint = (uid, "cancel")
        stored = self._idem.get((actor, idem_key))
        if stored is not None:
            if stored[0] != fingerprint:
                seq = self._append("MISMATCH", uid, actor, idem_key, ERROR_IDEMPOTENCY_MISMATCH)
                return Reply(error=ERROR_IDEMPOTENCY_MISMATCH, seq=seq)
            first = stored[1]
            seq = self._append("IDEMPOTENT_REPLAY", uid, actor, idem_key, first.result)
            return Reply(result=first.result, seq=seq, effect_seq=first.effect_seq)

        # 3. Fresh request against current sink state.
        state = self._state[uid]
        if state == PENDING:
            result = CANCELLED
        elif state == CANCELLED:
            result = ALREADY_CANCELLED
        else:
            result = TOO_LATE_CAPTURED
        seq = self._append("CANCEL_REQ", uid, actor, idem_key, result)
        if result == CANCELLED:
            self._state[uid] = CANCELLED
            self._effect_seq[uid] = self._append(CANCELLED, uid, actor, idem_key, CANCELLED)
        reply = Reply(result=result, seq=seq, effect_seq=self._effect_seq[uid])
        self._idem[(actor, idem_key)] = (fingerprint, reply)
        return reply

    def record_denied(self, actor: str, uid: str, key: str | None) -> Reply:
        """Log a request refused in front of the processor API (a create by a revoked agent,
        WP6). Writes one DENIED line and has no effect; `uid` is a placeholder for a create."""
        if actor == REMEDIATION_OPERATOR:
            raise ValueError("record_denied is for revoked or unknown actors only")
        seq = self._append("DENIED", uid, actor, key, DENIED)
        return Reply(result=DENIED, seq=seq)

    def status(self, actor: str, uid: str) -> StatusReply:
        if actor != REMEDIATION_OPERATOR:
            seq = self._append("DENIED", uid, actor, None, DENIED)
            return StatusReply(error=DENIED, seq=seq)
        if uid not in self._payments:
            seq = self._append("STATUS_READ", uid, actor, None, ERROR_UNKNOWN_PAYMENT)
            return StatusReply(error=ERROR_UNKNOWN_PAYMENT, seq=seq)
        state = self._state[uid]
        seq = self._append("STATUS_READ", uid, actor, None, state)
        return StatusReply(state=state, seq=seq, effect_seq=self._effect_seq.get(uid))
