"""Three competent runbooks, and an exact modeled-value scheduling candidate.
None sees the oracle labels. All receive identical observations and permissions.
"""
from functools import lru_cache
from typing import Tuple
from .model import Observation


def estimated_gain(p: Observation, collateral_factor: float) -> float:
    # Expected avoided suspect capture, less expected cost of cancelling legitimate payment.
    return p.amount * (p.risk_estimate - collateral_factor * (1-p.risk_estimate))


def plan(observations: Tuple[Observation, ...], method: str,
         collateral_factor: float = 0.5) -> Tuple[str, ...]:
    valid = tuple(p for p in observations if p.permitted and
                  estimated_gain(p, collateral_factor) > 0)
    if method == "none":
        return ()
    if method == "edf":
        ordered = sorted(valid, key=lambda p: (p.capture_at, -estimated_gain(p, collateral_factor), p.uid))
    elif method == "value_greedy":
        ordered = sorted(valid, key=lambda p: (-estimated_gain(p, collateral_factor)/p.remedy_minutes,
                                               p.capture_at, p.uid))
    elif method == "dp":
        # Subset dynamic program: in this model, completion time = sum of chosen durations.
        # Maximize projected net prevented exposure across all feasible cancel sequences.
        # This is a conventional small-instance scheduling optimizer, NOT claimed novel.
        candidates = tuple(sorted(valid, key=lambda p: p.uid))
        n = len(candidates)
        @lru_cache(None)
        def rec(mask: int, elapsed: int):
            best_score, best_ids = 0.0, ()
            for i, p in enumerate(candidates):
                finish = elapsed + p.remedy_minutes
                if mask & (1 << i) or finish >= p.capture_at:
                    continue
                future_score, future_ids = rec(mask | (1 << i), finish)
                score = estimated_gain(p, collateral_factor) + future_score
                ids = (p.uid,) + future_ids
                if score > best_score + 1e-8 or (abs(score-best_score) < 1e-8 and ids < best_ids):
                    best_score, best_ids = score, ids
            return best_score, best_ids
        return rec(0, 0)[1]
    else:
        raise ValueError("Unknown method: " + method)
    chosen = []
    elapsed = 0
    for p in ordered:
        if elapsed + p.remedy_minutes < p.capture_at:
            chosen.append(p.uid)
            elapsed += p.remedy_minutes
    return tuple(chosen)
