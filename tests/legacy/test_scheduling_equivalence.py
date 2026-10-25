"""Records CODE_AUDIT B1: the legacy "candidate" subset DP is 1||sum w_j U_j.

Ported from scripts/audit_scheduling_equivalence.py. On dev seeds, the classical
Lawler & Moore (1969) pseudo-polynomial DP (EDD order + knapsack over completion time)
reaches the same objective as the legacy subset DP in every config.
"""
import pytest
from p4lab.model import make_scenario, policy_view
from p4lab.policies import estimated_gain, plan

DEV_SEEDS = range(100000, 100300)
# (name, urgency, calibration_shift, collateral_factor), as in the audit script.
CONFIGS = [
    ("main", "normal", 0.0, 0.5),
    ("tight", "tight", 0.0, 0.5),
    ("loose", "loose", 0.0, 0.5),
    ("high_collateral", "normal", 0.0, 1.0),
]


def lawler_moore(obs, c=0.5):
    jobs = sorted([p for p in obs if p.permitted and estimated_gain(p, c) > 0],
                  key=lambda p: (p.capture_at, p.uid))
    best = {0: (0.0, ())}
    for p in jobs:
        new = dict(best)
        for t, (v, ids) in best.items():
            f = t + p.remedy_minutes
            if f < p.capture_at:
                cand = (v + estimated_gain(p, c), ids + (p.uid,))
                if f not in new or cand[0] > new[f][0] + 1e-9:
                    new[f] = cand
        best = new
    return max(best.values(), key=lambda x: x[0])[1]


def exp_value(obs, ids, c):
    lookup = {o.uid: o for o in obs}
    return sum(estimated_gain(lookup[i], c) for i in ids)


def is_feasible_in_order(obs, ids):
    lookup = {o.uid: o for o in obs}
    t = 0
    for uid in ids:
        t += lookup[uid].remedy_minutes
        if t >= lookup[uid].capture_at:
            return False
    return True


@pytest.mark.parametrize("name,urgency,shift,c", CONFIGS, ids=[cfg[0] for cfg in CONFIGS])
def test_lawler_moore_matches_subset_dp_objective(name, urgency, shift, c):
    mismatches = []
    for seed in DEV_SEEDS:
        obs = policy_view(make_scenario(seed, 8, urgency, shift))
        dp_ids = plan(obs, "dp", c)
        lm_ids = lawler_moore(obs, c)
        assert is_feasible_in_order(obs, lm_ids), (seed, lm_ids)
        if abs(exp_value(obs, dp_ids, c) - exp_value(obs, lm_ids, c)) > 1e-6:
            mismatches.append(seed)
    assert mismatches == []
