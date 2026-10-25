"""Shared scheduling primitives.

A `Job` is a cancel the remediator may send: weight `w` (expected gain), duration `s`
(worker time), absolute nominal deadline `d`. A job finishing at time f is feasible iff
f < d (strict, as in the legacy pilot).
"""
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Job:
    uid: str
    w: float
    s: int
    d: int


def gain(o, c: float) -> float:
    """Expected avoided suspect capture minus expected collateral: a * (p - c * (1 - p))."""
    return o.amount * (o.p_score - c * (1.0 - o.p_score))


def candidates(observations: Iterable, c: float) -> list:
    """Permitted observations with positive expected gain."""
    return [o for o in observations if o.permitted and gain(o, c) > 0]


def jobs_from(observations: Iterable, c: float) -> list[Job]:
    return [Job(o.uid, gain(o, c), o.s_cancel, o.d_nominal) for o in candidates(observations, c)]


def edd_key(j: Job):
    return (j.d, j.uid)


def workers(k: int, start: int = 0, worker_free_at: Sequence[int] | None = None) -> list[int]:
    if worker_free_at is not None:
        return list(worker_free_at)
    if k < 1:
        raise ValueError("k must be >= 1")
    return [start] * k


def list_schedule(jobs: Sequence[Job], worker_free_at: Sequence[int]) -> list[tuple[Job, int, int, int]]:
    """Assign jobs, in the given order, each to the earliest-free worker (lowest index on ties).

    Returns (job, worker, start, finish) per job.
    """
    free = list(worker_free_at)
    out = []
    for j in jobs:
        w = min(range(len(free)), key=lambda i: (free[i], i))
        start = free[w]
        free[w] = start + j.s
        out.append((j, w, start, free[w]))
    return out


def schedule_feasible(jobs: Sequence[Job], worker_free_at: Sequence[int]) -> bool:
    return all(f < j.d for j, _, _, f in list_schedule(jobs, worker_free_at))


def accept_if_feasible(ordered: Sequence[Job], worker_free_at: Sequence[int]) -> list[Job]:
    """Walk `ordered`; tentatively give each job the earliest-free worker and keep it iff it
    finishes before its deadline (legacy EDF / greedy rule, extended to k workers)."""
    free = list(worker_free_at)
    chosen = []
    for j in ordered:
        w = min(range(len(free)), key=lambda i: (free[i], i))
        if free[w] + j.s < j.d:
            free[w] += j.s
            chosen.append(j)
    return chosen


def fill_edd(chosen: Sequence[Job], jobs: Sequence[Job], worker_free_at: Sequence[int]) -> list[Job]:
    """Add each job not yet chosen, in EDD order, if the EDD list schedule stays feasible.
    Returns the chosen jobs in EDD order."""
    chosen = sorted(chosen, key=edd_key)
    taken = {j.uid for j in chosen}
    for j in sorted(jobs, key=edd_key):
        if j.uid not in taken and schedule_feasible(sorted(chosen + [j], key=edd_key), worker_free_at):
            chosen = sorted(chosen + [j], key=edd_key)
            taken.add(j.uid)
    return chosen
