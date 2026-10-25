"""Brute-force subset DP, ported from the legacy `plan(..., "dp")`. Test oracle only (never a
policy, never in tables). Exponential in n; use n <= 12."""
from collections.abc import Sequence
from functools import lru_cache

from .common import Job


def subset_dp(jobs: Sequence[Job], start: int = 0) -> tuple[str, ...]:
    """Best-weight feasible cancel sequence on one machine free at `start`."""
    cands = tuple(sorted(jobs, key=lambda j: j.uid))

    @lru_cache(None)
    def rec(mask: int, elapsed: int):
        best_score, best_ids = 0.0, ()
        for i, j in enumerate(cands):
            finish = elapsed + j.s
            if mask & (1 << i) or finish >= j.d:
                continue
            future_score, future_ids = rec(mask | (1 << i), finish)
            score = j.w + future_score
            ids = (j.uid,) + future_ids
            if score > best_score + 1e-8 or (abs(score - best_score) < 1e-8 and ids < best_ids):
                best_score, best_ids = score, ids
        return best_score, best_ids

    return rec(0, start)[1]
