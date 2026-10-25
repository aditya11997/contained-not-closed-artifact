"""Schedulers: pure functions of policy Observations (CLAUDE.md rule 1).

Nothing here imports `p4.sim`. A planner accepts any objects with the Observation fields
(uid, amount, d_nominal, s_cancel, p_score, permitted) and returns the uids to cancel in
execution order. For k > 1 workers the order is executed by list scheduling
(`common.list_schedule`).
"""
from .common import Job, candidates, gain, jobs_from, list_schedule, schedule_feasible
from .edf import edf, plan_edf
from .greedy import greedy, plan_greedy
from .lawler_moore import lawler_moore, plan_lm
from .mh import mh, plan_mh

PLANNERS = {
    "edf": plan_edf,
    "greedy": plan_greedy,
    "mh": plan_mh,
    "lm": plan_lm,
}

__all__ = [
    "PLANNERS",
    "Job",
    "candidates",
    "edf",
    "gain",
    "greedy",
    "jobs_from",
    "lawler_moore",
    "list_schedule",
    "mh",
    "plan_edf",
    "plan_greedy",
    "plan_lm",
    "plan_mh",
    "schedule_feasible",
]
