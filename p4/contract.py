"""Disposition contract: per-payment states, the report a runbook hands back, and a schema
check. Whether a report is *true* is the independent checker's job (WP3), not this module's.

Evidence-bounded runbooks (P4-P6) may report CLOSED_CANCELLED only with a sink reply or read
that says CANCELLED, and CLOSED_EXPOSURE only with one that says CAPTURED or TOO_LATE.
`evidence_seq` is the sink log line of that reply/read; `effect_seq` the CANCELLED/CAPTURED
line it rests on. Ack-trust runbooks (P0-P3) may close without evidence; `basis` says why.
"""
from dataclasses import dataclass, field
from enum import Enum

from .sim.protocol import ALREADY_CANCELLED, CANCELLED, CAPTURED, TOO_LATE_CAPTURED


class Disposition(str, Enum):
    CLOSED_CANCELLED = "CLOSED_CANCELLED"
    CLOSED_EXPOSURE = "CLOSED_EXPOSURE"
    UNRESOLVED_PENDING = "UNRESOLVED_PENDING"
    UNRESOLVED_UNKNOWN = "UNRESOLVED_UNKNOWN"


# Why a runbook chose a disposition.
BASIS_REPLY = "cancel_reply"           # a cancel reply delivered by the sink
BASIS_REPLAY = "replay_reply"          # a same-key replay reply (a cancel and a probe in one)
BASIS_STATUS = "status_read"           # a status read
BASIS_TIMEOUT_TRUSTED = "timeout_trusted"    # ack-trust: a timed-out cancel counted as success
BASIS_NOMINAL_DEADLINE = "nominal_deadline"  # ack-trust: unplanned, assumed captured at d
BASIS_NO_EVIDENCE = "no_evidence"      # unresolved: nothing definitive was observed

BASES = (BASIS_REPLY, BASIS_REPLAY, BASIS_STATUS, BASIS_TIMEOUT_TRUSTED,
         BASIS_NOMINAL_DEADLINE, BASIS_NO_EVIDENCE)
EVIDENCE_RESULTS = {
    Disposition.CLOSED_CANCELLED: {CANCELLED, ALREADY_CANCELLED},
    Disposition.CLOSED_EXPOSURE: {CAPTURED, TOO_LATE_CAPTURED},
}


@dataclass(frozen=True)
class DispositionEntry:
    uid: str
    state: Disposition
    basis: str
    reported_at: int
    evidence_seq: int | None = None
    effect_seq: int | None = None
    evidence_result: str | None = None

    def to_dict(self) -> dict:
        return {"uid": self.uid, "state": self.state.value, "basis": self.basis,
                "reported_at": self.reported_at, "evidence_seq": self.evidence_seq,
                "effect_seq": self.effect_seq, "evidence_result": self.evidence_result}


@dataclass(frozen=True)
class DispositionReport:
    policy: str
    incident_id: str
    evidence_bounded: bool
    entries: tuple[DispositionEntry, ...] = field(default_factory=tuple)

    def states(self) -> dict[str, Disposition]:
        return {e.uid: e.state for e in self.entries}

    def entry(self, uid: str) -> DispositionEntry:
        for e in self.entries:
            if e.uid == uid:
                return e
        raise KeyError(uid)

    def to_dict(self) -> dict:
        return {"policy": self.policy, "incident_id": self.incident_id,
                "evidence_bounded": self.evidence_bounded,
                "entries": [e.to_dict() for e in self.entries]}


def _is_seq(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


def validate(report: DispositionReport, uids=None) -> list[str]:
    """Schema problems in `report` (empty list = valid). Checks shape, not truth.

    If `uids` is given, the report must cover exactly those payments.
    """
    errs = []
    if not isinstance(report.policy, str) or not isinstance(report.incident_id, str):
        errs.append("policy and incident_id must be strings")
    if not isinstance(report.evidence_bounded, bool):
        errs.append("evidence_bounded must be a bool")
    seen = set()
    for e in report.entries:
        where = f"entry {e.uid!r}"
        if e.uid in seen:
            errs.append(f"{where}: duplicate uid")
        seen.add(e.uid)
        if not isinstance(e.state, Disposition):
            errs.append(f"{where}: state {e.state!r} is not a Disposition")
            continue
        if e.basis not in BASES:
            errs.append(f"{where}: unknown basis {e.basis!r}")
        if not _is_seq(e.reported_at):
            errs.append(f"{where}: reported_at must be an int >= 0")
        for name, v in (("evidence_seq", e.evidence_seq), ("effect_seq", e.effect_seq)):
            if v is not None and not _is_seq(v):
                errs.append(f"{where}: {name} must be None or an int >= 0")
        if (e.evidence_seq is None) != (e.evidence_result is None):
            errs.append(f"{where}: evidence_seq and evidence_result go together")
        if e.evidence_result is not None and e.state in EVIDENCE_RESULTS \
                and e.evidence_result not in EVIDENCE_RESULTS[e.state]:
            errs.append(f"{where}: evidence {e.evidence_result!r} does not support {e.state.value}")
        if report.evidence_bounded and e.state in EVIDENCE_RESULTS and (
                e.evidence_seq is None or e.basis not in (BASIS_REPLY, BASIS_REPLAY, BASIS_STATUS)):
            errs.append(f"{where}: evidence-bounded {e.state.value} without sink evidence")
    if uids is not None and seen != set(uids):
        errs.append(f"report covers {sorted(seen)}, expected {sorted(set(uids))}")
    return errs
