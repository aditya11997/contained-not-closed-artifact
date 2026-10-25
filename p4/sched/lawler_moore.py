"""Lawler & Moore (1969) exact algorithm for 1 || sum w_j U_j, used as the scheduling baseline
(CODE_AUDIT B1; not a contribution).

In an optimal schedule the on-time jobs can run in EDD order, so a DP over jobs in EDD order
and total busy time t keeps the best weight per t: O(n * sum s).

Exact only for one worker. For k > 1 this module uses a heuristic: take the k = 1 LM
selection on the earliest-free worker, then add each remaining job (EDD order) whose
addition keeps the EDD list schedule on all k workers feasible. The paper must call k > 1
LM a heuristic.
"""
from collections.abc import Sequence

import numpy as np

from .common import Job, edd_key, fill_edd, jobs_from, workers

EPS = 1e-9


def _lm_single(jobs: list[Job], start: int) -> list[Job]:
    """Exact max-weight on-time subset on one machine free at `start`; returned in EDD order."""
    jobs = [j for j in jobs if start + j.s < j.d]
    if not jobs:
        return []
    horizon = min(sum(j.s for j in jobs), max(j.d for j in jobs) - start - 1)
    best = np.full(horizon + 1, -np.inf)
    best[0] = 0.0
    keep = np.zeros((len(jobs), horizon + 1), dtype=bool)
    for i, j in enumerate(jobs):
        last = min(horizon, j.d - start - 1)  # busy time t is on time iff start + t < d
        if last < j.s:
            continue
        cand = best[: last - j.s + 1] + j.w
        take = cand > best[j.s: last + 1] + EPS
        keep[i, j.s: last + 1] = take
        best[j.s: last + 1] = np.where(take, cand, best[j.s: last + 1])
    top = best.max()
    t = int(np.flatnonzero(best >= top - EPS)[0])  # least busy time among optimal ends
    chosen = []
    for i in range(len(jobs) - 1, -1, -1):
        if keep[i, t]:
            chosen.append(jobs[i])
            t -= jobs[i].s
    if t != 0:
        raise AssertionError("Lawler-Moore backtrack did not reach t = 0")
    return chosen[::-1]


def lawler_moore(jobs: Sequence[Job], k: int = 1, start: int = 0,
                 worker_free_at: Sequence[int] | None = None) -> tuple[str, ...]:
    free = workers(k, start, worker_free_at)
    ordered = sorted(jobs, key=edd_key)
    chosen = _lm_single(ordered, min(free))
    if len(free) > 1:
        chosen = fill_edd(chosen, ordered, free)
    return tuple(j.uid for j in chosen)


def plan_lm(observations, c: float, k: int = 1) -> tuple[str, ...]:
    return lawler_moore(jobs_from(observations, c), k)
