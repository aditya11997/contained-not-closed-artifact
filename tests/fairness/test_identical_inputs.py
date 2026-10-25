"""CLAUDE.md rule 2: for a seed, every policy gets the identical observations, k, status-read
cost, fault windows and per-(op, uid, attempt) fault draws, and the faults it actually met
are exactly those draws."""
import pytest

from p4.harness import run_episode
from p4.runbooks import POLICIES
from p4.sim.faults import fault_draw, load_fault_presets, outage_windows
from p4.sim.scenario import ScenarioConfig, make_scenario, partition_seeds

SEEDS = list(partition_seeds("dev"))[:50]
HIGH = load_fault_presets()["high"]


def expected_fault(rec, seed, cfg, in_outage):
    draw = fault_draw(seed, cfg, rec.op, rec.uid, rec.attempt)
    if in_outage:
        return "outage_commit" if rec.op == "cancel" and draw.outage_commit else "outage_lost"
    if draw.drop_req:
        return "drop_req"
    return "drop_resp" if draw.drop_resp else "ok"


def outage_at(seed, cfg, t, horizon):
    return any(a <= t < b for a, b in outage_windows(seed, cfg, max(horizon, t + 1)))


@pytest.mark.parametrize("k", [1, 4])
def test_identical_inputs_across_all_policies(k):
    assert len(POLICIES) == 8  # P0-P6 and the post-hoc P3b (docs/POSTHOC.md PH2): all must match
    for seed in SEEDS:
        scenario = make_scenario(seed, ScenarioConfig(n=8, slack="tight"))
        hashes = {}
        for policy in POLICIES:
            ep = run_episode(scenario, policy, HIGH, seed, k)
            hashes[policy] = ep.inputs_sha256
            horizon = max(ep.end_of_run, scenario.horizon) + 1
            for rec in ep.trace:
                assert rec.fault == expected_fault(rec, seed, HIGH, outage_at(seed, HIGH, rec.t_arrive, horizon)), \
                    (seed, policy, rec)
        assert len(set(hashes.values())) == 1, (seed, hashes)


def test_fingerprint_changes_when_inputs_change():
    scenario = make_scenario(SEEDS[0], ScenarioConfig(n=8))
    base = run_episode(scenario, "lm_ack", HIGH, SEEDS[0], 1).inputs_sha256
    assert run_episode(scenario, "lm_ack", HIGH, SEEDS[0], 2).inputs_sha256 != base
    assert run_episode(scenario, "lm_ack", HIGH, SEEDS[1], 1).inputs_sha256 != base
    other = make_scenario(SEEDS[1], ScenarioConfig(n=8))
    assert run_episode(other, "lm_ack", HIGH, SEEDS[0], 1).inputs_sha256 != base
