"""EDF accept-if-feasible (legacy `plan(..., "edf")`): earliest deadline first, ties by
larger gain then uid; keep a job iff it still finishes before its deadline."""
from collections.abc import Sequence

from .common import Job, accept_if_feasible, jobs_from, workers


def edf(jobs: Sequence[Job], k: int = 1, start: int = 0,
        worker_free_at: Sequence[int] | None = None) -> tuple[str, ...]:
    ordered = sorted(jobs, key=lambda j: (j.d, -j.w, j.uid))
    return tuple(j.uid for j in accept_if_feasible(ordered, workers(k, start, worker_free_at)))


def plan_edf(observations, c: float, k: int = 1) -> tuple[str, ...]:
    return edf(jobs_from(observations, c), k)
