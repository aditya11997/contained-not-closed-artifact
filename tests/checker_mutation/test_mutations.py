"""WP3 mutation tests: the independent checker flags every injected tampering of a valid run
(100%) and nothing on the 20 unmutated runs.

Valid runs: P4-P6 (the policies that claim the evidence contract) on dev seeds under high
faults with capture jitter. Log tamperings are tried raw (hashes left stale), re-chained by
the forger with the anchor in truth.json, and re-chained without the anchor (only the
checker's semantic replay can catch those).
"""
import json

import mutations
import numpy as np
import pytest

from checker.check import check_run
from p4 import schema
from p4.harness import run_episode, truth_dict
from p4.sim.faults import load_fault_presets
from p4.sim.scenario import JitterConfig, ScenarioConfig, make_scenario, partition_seeds

HIGH = load_fault_presets()["high"]
CFG = ScenarioConfig(n=8, slack="tight", jitter=JitterConfig("uniform", 6))
POLICIES = ("lm_verify", "lm_reconcile", "adaptive")
N_RUNS = 20
# The counter each tampering must raise when only semantics can catch it.
PRIMARY = {
    "forged_closed_cancelled": ("false_closure",),
    "evidence_wrong_uid": ("unsupported_evidence",),
    "deleted_captured": ("truth_mismatch",),
    "edited_timestamp": ("non_monotone", "inconsistent_replies", "truth_mismatch"),
    "duplicate_key_second_effect": ("duplicate_effects",),
    "revoked_actor_effect": ("revoked_actor_effects",),
    "broken_hash": ("chain_broken",),
}
VARIANTS = ([(m, "raw") for m in mutations.ALL]
            + [(m, v) for m in mutations.LOG_MUTATIONS for v in ("rechained", "rechained_unanchored")])


def _runs():
    out = []
    for i, seed in enumerate(partition_seeds("dev")):
        scenario = make_scenario(seed, CFG)
        policy, k = POLICIES[i % 3], 1 + (i % 2) * 2
        ep = run_episode(scenario, policy, HIGH, seed, k)
        lines = [json.loads(json.dumps(dict(x))) for x in ep.log]
        report = {"schema": schema.REPORT_SCHEMA, **ep.report.to_dict()}
        out.append((seed, policy, lines, report, truth_dict(scenario, 0.5, ep.log)))
        if len(out) == N_RUNS:
            return out


RUNS = _runs()


def test_unmutated_runs_are_clean():
    assert len(RUNS) == N_RUNS
    for seed, policy, lines, report, truth in RUNS:
        for t in (truth, {k: v for k, v in truth.items() if k != "log_anchor"}):
            v = check_run(lines, report, t, policy)
            assert v["ok"] and v["problems"] == [], (seed, policy, v["problems"])
            assert all(v["counts"][c] == 0 for c in schema.INTEGRITY_COUNTS + schema.REPORT_COUNTS)


@pytest.mark.parametrize("name,variant", VARIANTS, ids=[f"{m}-{v}" for m, v in VARIANTS])
def test_every_mutation_is_flagged(name, variant):
    applied = flagged = 0
    for seed, policy, lines, report, truth in RUNS:
        rng = np.random.default_rng([seed, len(name)])
        out = mutations.apply(name, lines, report, rng, rechain_log=variant != "raw")
        if out is None:
            continue
        applied += 1
        t = truth if variant != "rechained_unanchored" else {k: v for k, v in truth.items() if k != "log_anchor"}
        v = check_run(*out, t, policy)
        assert not v["ok"], (name, variant, seed, policy)
        if variant == "rechained_unanchored" or name in mutations.REPORT_MUTATIONS or name in mutations.RAW_ONLY:
            assert any(v["counts"][c] > 0 for c in PRIMARY[name]), (name, seed, v["problems"])
        flagged += 1
    assert applied >= 10, (name, variant, applied)
    assert flagged == applied


def test_rechained_tampering_is_caught_by_the_anchor_too():
    _, policy, lines, report, truth = RUNS[0]
    out = mutations.apply("deleted_captured", lines, report, np.random.default_rng(0), rechain_log=True)
    v = check_run(*out, truth, policy)
    assert v["counts"]["anchor_mismatch"] == 1 and v["counts"]["chain_broken"] == 0


def test_forged_closure_on_ack_trust_is_counted():
    """For P3 a false closure is a measurement, not an integrity failure: the checker counts
    a forged CLOSED_CANCELLED instead of failing the run."""
    seed = 100000
    scenario = make_scenario(seed, CFG)
    ep = run_episode(scenario, "lm_ack", HIGH, seed, 1)
    lines = [json.loads(json.dumps(dict(x))) for x in ep.log]
    report = {"schema": schema.REPORT_SCHEMA, **ep.report.to_dict()}
    truth = truth_dict(scenario, 0.5, ep.log)
    before = check_run(lines, report, truth, "lm_ack")
    victim = next(e for e in report["entries"] if e["state"] == "CLOSED_EXPOSURE")
    forged = json.loads(json.dumps(report))
    for e in forged["entries"]:
        if e["uid"] == victim["uid"]:
            e.update(state="CLOSED_CANCELLED", basis="timeout_trusted", evidence_seq=None,
                     effect_seq=None, evidence_result=None)
    after = check_run(lines, forged, truth, "lm_ack")
    assert after["counts"]["false_closure"] == before["counts"]["false_closure"] + 1
    assert before["ok"] and after["ok"]
    # ...but the same report claimed as an evidence-bounded policy fails.
    forged["policy"], forged["evidence_bounded"] = "lm_verify", True
    assert not check_run(lines, forged, truth, "lm_verify")["ok"]


def test_relabelling_the_policy_does_not_escape_the_contract():
    _, policy, lines, report, truth = RUNS[0]
    relabelled = {**report, "policy": "lm_ack", "evidence_bounded": False}
    assert not check_run(lines, relabelled, truth, policy)["ok"]
