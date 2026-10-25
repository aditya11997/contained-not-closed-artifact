"""Synthetic payment ledger and immutable policy observations.
All times are *simulated minutes*, not actual card-network settlement windows.
"""
from dataclasses import dataclass
from typing import Tuple
import random

@dataclass(frozen=True)
class Payment:
    uid: str
    amount: int
    capture_at: int
    remedy_minutes: int
    risk_estimate: float
    suspected_problem: bool  # Oracle truth, deliberately hidden from policies.
    permitted: bool = True

@dataclass(frozen=True)
class Observation:
    uid: str
    amount: int
    capture_at: int
    remedy_minutes: int
    risk_estimate: float
    permitted: bool


def policy_view(payments: Tuple[Payment, ...]) -> Tuple[Observation, ...]:
    return tuple(Observation(p.uid, p.amount, p.capture_at,
                             p.remedy_minutes, p.risk_estimate, p.permitted)
                 for p in payments)


def make_scenario(seed: int, count: int = 8, urgency: str = "normal",
                  calibration_shift: float = 0.0) -> Tuple[Payment, ...]:
    rng = random.Random(seed)
    factor = {"tight": 0.7, "normal": 1.0, "loose": 1.5}[urgency]
    result = []
    for i in range(count):
        amount = rng.randrange(100, 2100, 100)
        risk = round(rng.uniform(0.18, 0.92), 3)
        actual_risk = max(0.0, min(1.0, risk + calibration_shift))
        capture_at = max(2, round(rng.randrange(5, 19) * factor))
        duration = rng.randint(1, 5)
        permitted = (rng.random() > 0.06)
        result.append(Payment(f"P{i:02d}", amount, capture_at, duration,
                              risk, rng.random() < actual_risk, permitted))
    return tuple(result)


class SyntheticProcessor:
    """Independent sink with write-side truth, access-controlled cancel and idempotency."""
    def __init__(self, payments: Tuple[Payment, ...]):
        self._payments = {p.uid: p for p in payments}
        self._cancelled = set()
        self._requests = {}  # idempotency key -> result
        self.events = []

    def cancel(self, uid: str, finish_at: int, actor: str, key: str) -> str:
        if key in self._requests:
            return self._requests[key]
        p = self._payments[uid]
        if actor != "remediation_operator" or not p.permitted:
            outcome = "DENIED"
        elif uid in self._cancelled:
            outcome = "ALREADY_CANCELLED"
        elif finish_at >= p.capture_at:
            outcome = "TOO_LATE_CAPTURED"
        else:
            self._cancelled.add(uid)
            outcome = "CANCELLED"
        self._requests[key] = outcome
        self.events.append((uid, finish_at, outcome, actor, key))
        return outcome

    def independent_state(self, uid: str, at_time: int) -> str:
        if uid in self._cancelled:
            return "CANCELLED"
        if at_time >= self._payments[uid].capture_at:
            return "CAPTURED"
        return "PENDING"

    def score_truth(self, horizon: int) -> dict:
        """Only evaluator may call this; policies never see suspected_problem."""
        suspect_captured = 0
        legitimate_cancelled = 0
        cancelled_count = 0
        for uid, p in self._payments.items():
            cancelled = uid in self._cancelled
            if cancelled:
                cancelled_count += 1
            if p.suspected_problem and not cancelled and horizon >= p.capture_at:
                suspect_captured += p.amount
            if not p.suspected_problem and cancelled:
                legitimate_cancelled += p.amount
        return {"suspect_captured_value": suspect_captured,
                "legitimate_cancelled_value": legitimate_cancelled,
                "cancelled_count": cancelled_count,
                "cancel_calls": len(self.events),
                "authority_violations": sum(e[2] == "DENIED" for e in self.events)}
