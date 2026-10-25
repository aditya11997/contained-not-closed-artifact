"""Moore-Hodgson weighted heuristic: sanity checks (not a claim)."""
import numpy as np

from p4.sched import Job, edf, jobs_from, mh, plan_edf, plan_mh, schedule_feasible
from p4.sim.scenario import ScenarioConfig, make_scenario


def value(jobs, uids):
    w = {j.uid: j.w for j in jobs}
    return sum(w[u] for u in uids)


def test_mh_at_least_edf_on_95_percent_of_dev_instances():
    total = ok = 0
    for seed in range(100000, 101000):
        for slack in ("tight", "normal"):
            obs = make_scenario(seed, ScenarioConfig(n=8, slack=slack)).observations()
            jobs = jobs_from(obs, 0.5)
            total += 1
            ok += value(jobs, plan_mh(obs, 0.5)) >= value(jobs, plan_edf(obs, 0.5)) - 1e-9
    assert ok / total >= 0.95, f"MH >= EDF on {ok}/{total}"


def test_mh_plans_are_feasible_in_edd_order():
    rng = np.random.default_rng([100010, 2])
    for _ in range(300):
        jobs = [Job(f"J{i:02d}", float(rng.uniform(1, 100)), int(rng.integers(1, 6)),
                    int(rng.integers(1, 25))) for i in range(int(rng.integers(1, 14)))]
        by = {j.uid: j for j in jobs}
        for k in (1, 3):
            chosen = [by[u] for u in mh(jobs, k=k)]
            assert chosen == sorted(chosen, key=lambda j: (j.d, j.uid))
            assert schedule_feasible(chosen, [0] * k)


def test_mh_drops_low_density_job_that_edf_keeps():
    # EDF keeps the early cheap job and then cannot fit the valuable one; MH drops the cheap one.
    jobs = [Job("cheap", 1.0, 3, 4), Job("rich", 100.0, 3, 5)]
    assert edf(jobs) == ("cheap",)
    assert mh(jobs) == ("rich",)
