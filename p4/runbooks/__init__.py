"""Closed-loop runbooks P0-P6 (REVAMP_PLAN §3). They differ only in selection and in how
they handle evidence; all get the same observations, k, authority and fault realization."""
from functools import partial

from ..sched import plan_edf, plan_lm, plan_mh
from .ack_retry import AckRetry
from .ack_trust import AckTrust, no_plan
from .adaptive import Adaptive
from .base import CRASH_POINTS, Action, Runbook, SimulatedCrash, cancel_key, effective_k
from .reconcile import Reconcile
from .verify_each import VerifyEach

POLICIES = {
    "none": partial(AckTrust, no_plan, "none"),          # P0
    "edf": partial(AckTrust, plan_edf, "edf"),           # P1
    "mh": partial(AckTrust, plan_mh, "mh"),              # P2
    "lm_ack": partial(AckTrust, plan_lm, "lm_ack"),      # P3
    "lm_verify": VerifyEach,                             # P4
    "lm_reconcile": Reconcile,                           # P5
    "adaptive": Adaptive,                                # P6
    "lm_ack_retry": AckRetry,                            # P3b, post hoc (docs/POSTHOC.md PH2)
}


# Evidence-bounded runbooks take the `late_cancel` flag (applied identically to all three).
LATE_CANCEL_POLICIES = ("lm_verify", "lm_reconcile", "adaptive")


def make_runbook(name: str, c: float = 0.5, incident_id: str = "INC-0",
                 late_cancel: bool = True) -> Runbook:
    if name not in POLICIES:
        raise ValueError(f"unknown policy {name!r}; expected one of {tuple(POLICIES)}")
    if name in LATE_CANCEL_POLICIES:
        return POLICIES[name](c=c, incident_id=incident_id, late_cancel=late_cancel)
    return POLICIES[name](c=c, incident_id=incident_id)


__all__ = [
    "CRASH_POINTS",
    "LATE_CANCEL_POLICIES",
    "POLICIES",
    "AckRetry",
    "AckTrust",
    "Action",
    "Adaptive",
    "Reconcile",
    "Runbook",
    "SimulatedCrash",
    "VerifyEach",
    "cancel_key",
    "effective_k",
    "make_runbook",
]
