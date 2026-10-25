"""H5 sanity (not a paper result): Lawler-Moore reaches the subset-DP objective; it scales."""
import time

import numpy as np

from p4.sched import Job, jobs_from, lawler_moore, plan_lm, schedule_feasible
from p4.sched.subset_dp import subset_dp
from p4.sim.scenario import ScenarioConfig, make_scenario


def value(jobs, uids):
    w = {j.uid: j.w for j in jobs}
    return sum(w[u] for u in uids)


def random_jobs(rng, n):
    return [Job(f"J{i:02d}", float(rng.uniform(0.01, 1000.0)), int(rng.integers(1, 6)),
                int(rng.integers(1, 31))) for i in range(n)]


def test_lm_matches_subset_dp_on_1000_random_instances():
    rng = np.random.default_rng([100000, 2])
    for trial in range(1000):
        jobs = random_jobs(rng, int(rng.integers(0, 11)))
        start = int(rng.integers(0, 4)) if trial % 4 == 0 else 0
        lm, dp = lawler_moore(jobs, start=start), subset_dp(jobs, start=start)
        assert schedule_feasible([j for j in sorted(jobs, key=lambda j: (j.d, j.uid)) if j.uid in lm],
                                 [start]), (trial, lm)
        assert abs(value(jobs, lm) - value(jobs, dp)) < 1e-6, (trial, lm, dp)


def test_lm_matches_subset_dp_on_dev_scenarios():
    for seed in range(100000, 100200):
        for slack in ("tight", "normal"):
            obs = make_scenario(seed, ScenarioConfig(n=10, slack=slack)).observations()
            jobs = jobs_from(obs, 0.5)
            assert abs(value(jobs, plan_lm(obs, 0.5)) - value(jobs, subset_dp(jobs))) < 1e-6, seed


def test_lm_plan_is_in_edd_order_and_feasible_for_k_workers():
    rng = np.random.default_rng([100001, 2])
    for _ in range(300):
        jobs = random_jobs(rng, int(rng.integers(1, 16)))
        for k in (1, 2, 4):
            uids = lawler_moore(jobs, k=k)
            by = {j.uid: j for j in jobs}
            chosen = [by[u] for u in uids]
            assert chosen == sorted(chosen, key=lambda j: (j.d, j.uid))
            assert schedule_feasible(chosen, [0] * k)


def test_lm_k_workers_never_selects_less_value_than_k1():
    rng = np.random.default_rng([100002, 2])
    for _ in range(300):
        jobs = random_jobs(rng, int(rng.integers(1, 16)))
        v1 = value(jobs, lawler_moore(jobs, k=1))
        for k in (2, 4):
            assert value(jobs, lawler_moore(jobs, k=k)) >= v1 - 1e-9


def test_lm_n128_under_50ms():
    rng = np.random.default_rng([100003, 2])
    obs = make_scenario(100003, ScenarioConfig(n=128)).observations()
    hard = [Job(f"J{i:03d}", float(rng.uniform(1, 1000)), int(rng.integers(1, 6)),
                int(rng.integers(1, 384))) for i in range(128)]  # deadlines up to ~sum s
    for jobs in (jobs_from(obs, 0.5), hard):
        best = min(_timed(lawler_moore, jobs) for _ in range(3))
        assert best < 0.050, f"Lawler-Moore took {best * 1000:.1f} ms for n=128"


def _timed(fn, *args):
    t = time.perf_counter()
    fn(*args)
    return time.perf_counter() - t


def test_lm_empty_and_infeasible():
    assert lawler_moore([]) == ()
    assert lawler_moore([Job("A", 5.0, 3, 3)]) == ()  # finish == deadline is too late
    assert lawler_moore([Job("A", 5.0, 2, 3)]) == ("A",)
