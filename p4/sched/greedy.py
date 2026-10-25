"""Value-per-time greedy (legacy `plan(..., "value_greedy")`): order by gain / s, ties by
deadline then uid; keep a job iff it still finishes before its deadline. Jobs run in
greedy order, as in the legacy pilot."""
from collections.abc import Sequence

from .common import Job, accept_if_feasible, jobs_from, workers


def greedy(jobs: Sequence[Job], k: int = 1, start: int = 0,
           worker_free_at: Sequence[int] | None = None) -> tuple[str, ...]:
    ordered = sorted(jobs, key=lambda j: (-j.w / j.s, j.d, j.uid))
    return tuple(j.uid for j in accept_if_feasible(ordered, workers(k, start, worker_free_at)))


def plan_greedy(observations, c: float, k: int = 1) -> tuple[str, ...]:
    return greedy(jobs_from(observations, c), k)
