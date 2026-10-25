"""Evaluator-only scoring from an exported sink log plus the hidden scenario truth.

Never imported by p4/sched or p4/runbooks (CLAUDE.md rule 1). It reads log data, not the
live SimProcessor. Chain verification is the independent checker's job (WP3).

`denied_calls` counts rejected requests (CODE_AUDIT A5): a denial is not a violation.
`revoked_actor_effects` counts state changes made by anyone other than the remediation
operator after t0. It is 0 by construction in SimProcessor and asserted by the checker.
"""
import json
from collections.abc import Iterable
from pathlib import Path

from .protocol import DENIED, REMEDIATION_OPERATOR
from .scenario import Scenario

CANCEL_REQUEST_TYPES = ("CANCEL_REQ", "IDEMPOTENT_REPLAY", "MISMATCH")


def read_log(path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def score(log: str | Path | Iterable[dict], scenario: Scenario, c: float) -> dict:
    entries = read_log(log) if isinstance(log, (str, Path)) else [dict(e) for e in log]
    final: dict[str, str] = {}
    revoked_effects = 0
    for e in entries:
        if e["type"] in ("CANCELLED", "CAPTURED"):
            final.setdefault(e["uid"], e["type"])
            if e["type"] == "CANCELLED" and e["actor"] != REMEDIATION_OPERATOR:
                revoked_effects += 1
    missing = [p.uid for p in scenario.payments if p.uid not in final]
    if missing:
        raise ValueError(f"log ends before the horizon: no terminal state for {missing}")

    suspect_captured = sum(p.amount for p in scenario.payments
                           if p.y_suspect and final[p.uid] == "CAPTURED")
    legit_cancelled = sum(p.amount for p in scenario.payments
                          if not p.y_suspect and final[p.uid] == "CANCELLED")
    denied = [e for e in entries if e["type"] == DENIED]
    return {
        "suspect_captured_value": suspect_captured,
        "legitimate_cancelled_value": legit_cancelled,
        "loss": suspect_captured + c * legit_cancelled,
        "cancelled_count": sum(v == "CANCELLED" for v in final.values()),
        "captured_count": sum(v == "CAPTURED" for v in final.values()),
        "cancel_calls": sum(e["type"] in CANCEL_REQUEST_TYPES for e in entries)
                        + sum(e["key"] is not None for e in denied),
        "status_calls": sum(e["type"] == "STATUS_READ" for e in entries)
                        + sum(e["key"] is None for e in denied),
        "denied_calls": len(denied),
        "revoked_actor_effects": revoked_effects,
    }
