"""Weighted Moore–Hodgson heuristic (Moore 1968, Hodgson's algorithm; weighted drop rule).

Add jobs in EDD order; whenever the scheduled set is infeasible, drop the scheduled job with
the smallest gain / s (ties: later deadline, then earlier in EDD order) until it is feasible
again. Then a fill pass re-adds, in EDD order, each dropped job that still fits: a drop that
did not by itself restore feasibility can leave room (WP2 decision, CHANGELOG_RESEARCH).
For unit weights the drop rule is Moore-Hodgson (drop the longest job); with weights it is a
heuristic. For k > 1 feasibility is checked by EDD list scheduling.
"""
from collections.abc import Sequence

from .common import Job, edd_key, fill_edd, jobs_from, schedule_feasible, workers


def mh(jobs: Sequence[Job], k: int = 1, start: int = 0,
       worker_free_at: Sequence[int] | None = None, fill: bool = True) -> tuple[str, ...]:
    """`fill=False` is the bare drop rule, kept only to document why the fill pass exists."""
    free = workers(k, start, worker_free_at)
    scheduled: list[Job] = []
    for j in sorted(jobs, key=edd_key):
        scheduled.append(j)
        while scheduled and not schedule_feasible(scheduled, free):
            worst = min(scheduled, key=lambda x: (x.w / x.s, -x.d))
            scheduled.remove(worst)
    if fill:
        scheduled = fill_edd(scheduled, jobs, free)
    return tuple(j.uid for j in scheduled)


def plan_mh(observations, c: float, k: int = 1) -> tuple[str, ...]:
    return mh(jobs_from(observations, c), k)
