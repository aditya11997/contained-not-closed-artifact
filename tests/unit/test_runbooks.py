"""Runbooks P0-P6: H4 sanity, forced lost ACKs, the evidence contract, determinism."""
import math

import pytest
from sim_helpers import payment

from p4.contract import BASIS_TIMEOUT_TRUSTED, Disposition, validate
from p4.harness import run_episode
from p4.runbooks import POLICIES, cancel_key
from p4.sched import plan_lm
from p4.sim import faults
from p4.sim.faults import FaultConfig, FaultDraw, load_fault_presets
from p4.sim.scenario import (
    JitterConfig,
    Scenario,
    ScenarioConfig,
    make_scenario,
    partition_seeds,
)

NONE = FaultConfig()
PRESETS = load_fault_presets()
DEV = list(partition_seeds("dev"))
ASSURED = ("lm_verify", "lm_reconcile", "adaptive")


def final_states(log):
    out = {}
    for e in log:
        if e["type"] in ("CANCELLED", "CAPTURED"):
            out.setdefault(e["uid"], e["type"])
    return out


def false_closures(ep):
    final = final_states(ep.log)
    return [e.uid for e in ep.report.entries
            if e.state == Disposition.CLOSED_CANCELLED and final.get(e.uid) != "CANCELLED"]


def force(monkeypatch, uid, **kind):
    """Force a fault on the first cancel of `uid`; every other call is fault-free."""
    def draw(seed, cfg, op, u, attempt):
        if op == "cancel" and u == uid and attempt == 0:
            return FaultDraw(kind.get("drop_req", False), kind.get("drop_resp", False), False)
        return FaultDraw(False, False, False)
    monkeypatch.setattr(faults, "fault_draw", draw)


@pytest.mark.parametrize("n,slack", [(8, "tight"), (8, "normal"), (32, "tight")])
def test_h4_no_faults_k_inf_assured_runbooks_match_lm_ack(n, slack):
    for seed in DEV[:50]:
        scenario = make_scenario(seed, ScenarioConfig(n=n, slack=slack))
        states = {p: run_episode(scenario, p, NONE, seed, math.inf).report.states()
                  for p in ("lm_ack",) + ASSURED}
        assert all(s == states["lm_ack"] for s in states.values()), (seed, states)


def _first_planned(scenario):
    return plan_lm(scenario.observations(), 0.5, 1)[0]


def test_forced_drop_resp_p3_trusts_timeout_assured_runbooks_get_evidence(monkeypatch):
    seed = DEV[0]
    scenario = make_scenario(seed, ScenarioConfig(n=8, slack="normal"))
    uid = _first_planned(scenario)
    force(monkeypatch, uid, drop_resp=True)

    p3 = run_episode(scenario, "lm_ack", NONE, seed, 1)
    e = p3.report.entry(uid)
    assert e.state == Disposition.CLOSED_CANCELLED and e.basis == BASIS_TIMEOUT_TRUSTED
    assert e.evidence_seq is None

    for policy in ASSURED:
        ep = run_episode(scenario, policy, NONE, seed, 1)
        e = ep.report.entry(uid)
        assert e.state == Disposition.CLOSED_CANCELLED, policy
        line = ep.log[e.evidence_seq]
        assert line["uid"] == uid and line["result"] in ("CANCELLED", "ALREADY_CANCELLED"), policy
        assert ep.log[e.effect_seq]["type"] == "CANCELLED", policy
        assert validate(ep.report, [o.uid for o in scenario.observations()]) == []


def test_forced_drop_req_is_a_p3_false_closure_and_not_for_assured(monkeypatch):
    seed = DEV[0]
    scenario = make_scenario(seed, ScenarioConfig(n=8, slack="tight"))
    uid = _first_planned(scenario)
    force(monkeypatch, uid, drop_req=True)
    assert uid in false_closures(run_episode(scenario, "lm_ack", NONE, seed, 1))
    for policy in ASSURED:
        assert false_closures(run_episode(scenario, policy, NONE, seed, 1)) == [], policy


@pytest.mark.parametrize("fault", ["low", "high"])
@pytest.mark.parametrize("k", [1, 4])
def test_assured_runbooks_honour_the_contract_under_faults(fault, k):
    cfg = PRESETS[fault]
    for seed in DEV[:40]:
        scenario = make_scenario(seed, ScenarioConfig(n=8, slack="tight"))
        uids = [o.uid for o in scenario.observations()]
        for policy in POLICIES:
            ep = run_episode(scenario, policy, cfg, seed, k)
            assert validate(ep.report, uids) == [], (seed, policy)
            effects = [x["uid"] for x in ep.log if x["type"] in ("CANCELLED", "CAPTURED")]
            assert sorted(effects) == sorted(uids), (seed, policy)  # one effect per payment
            if policy not in ASSURED:
                continue
            assert false_closures(ep) == [], (seed, policy)
            for e in ep.report.entries:
                if e.evidence_seq is not None:
                    line = ep.log[e.evidence_seq]
                    assert line["uid"] == e.uid and line["result"] == e.evidence_result


def test_every_call_uses_the_one_key_and_is_journaled():
    seed = DEV[3]
    scenario = make_scenario(seed, ScenarioConfig(n=8, slack="tight"))
    for policy in POLICIES:
        ep = run_episode(scenario, policy, PRESETS["high"], seed, 2, incident_id="INC-7")
        for line in ep.log:
            if line["type"] in ("CANCEL_REQ", "IDEMPOTENT_REPLAY"):
                assert line["key"] == cancel_key("INC-7", line["uid"]) == f"INC-7/{line['uid']}/cancel/v1"
        intents = [j for j in ep.journal if j["kind"] == "intent"]
        results = [j for j in ep.journal if j["kind"] == "result"]
        assert len(intents) == len(results) == len(ep.trace), policy


def test_runs_are_deterministic():
    seed = DEV[5]
    scenario = make_scenario(seed, ScenarioConfig(n=16, slack="tight"))
    for policy in POLICIES:
        a = run_episode(scenario, policy, PRESETS["high"], seed, 4)
        b = run_episode(scenario, policy, PRESETS["high"], seed, 4)
        assert a.report == b.report and a.log == b.log, policy


def test_p0_reports_everything_as_exposure_without_calls():
    seed = DEV[0]
    scenario = make_scenario(seed, ScenarioConfig(n=8))
    ep = run_episode(scenario, "none", PRESETS["high"], seed, 1)
    assert ep.trace == ()
    assert set(ep.report.states().values()) == {Disposition.CLOSED_EXPOSURE}


def test_rejects_bad_k():
    scenario = make_scenario(DEV[0], ScenarioConfig(n=4))
    for k in (0, -1, 1.5):
        with pytest.raises(ValueError):
            run_episode(scenario, "lm_ack", NONE, DEV[0], k)


# -- late_cancel: P4-P6 may cancel after d_nominal while a sweep read still says PENDING ----

def _late_scenario(p_score=0.9, permitted=True):
    # Unplannable before d (s == d), captured late at D = 20; the sweep reads it at t = 3.
    return Scenario(payments=(payment("A", D=20, d=3, s=3, p=p_score, permitted=permitted),))


@pytest.mark.parametrize("policy", ASSURED)
def test_late_cancel_prevents_a_late_capture_with_evidence(policy):
    ep = run_episode(_late_scenario(), policy, NONE, DEV[0], 1)
    e = ep.report.entry("A")
    assert e.state == Disposition.CLOSED_CANCELLED
    assert ep.log[e.evidence_seq]["result"] == "CANCELLED" and ep.log[e.evidence_seq]["t"] > 3
    assert final_states(ep.log) == {"A": "CANCELLED"}


@pytest.mark.parametrize("policy", ASSURED)
def test_late_cancel_off_reports_unresolved_pending(policy):
    ep = run_episode(_late_scenario(), policy, NONE, DEV[0], 1, late_cancel=False)
    assert ep.report.entry("A").state == Disposition.UNRESOLVED_PENDING
    assert final_states(ep.log) == {"A": "CAPTURED"}
    assert all(r.op == "status" for r in ep.trace)


@pytest.mark.parametrize("policy", ASSURED)
@pytest.mark.parametrize("p_score,permitted", [(0.1, True), (0.9, False)])
def test_late_cancel_needs_positive_gain_and_permission(policy, p_score, permitted):
    ep = run_episode(_late_scenario(p_score, permitted), policy, NONE, DEV[0], 1)
    assert ep.report.entry("A").state == Disposition.UNRESOLVED_PENDING
    assert all(r.op == "status" for r in ep.trace)


def test_late_cancel_does_not_change_ack_trust():
    for policy in ("none", "edf", "mh", "lm_ack", "lm_ack_retry"):
        on = run_episode(_late_scenario(), policy, NONE, DEV[0], 1)
        off = run_episode(_late_scenario(), policy, NONE, DEV[0], 1, late_cancel=False)
        assert on.report == off.report and on.log == off.log


@pytest.mark.parametrize("late_cancel", [True, False])
def test_contract_holds_with_late_jitter_and_faults(late_cancel):
    cfg = ScenarioConfig(n=8, slack="tight", jitter=JitterConfig("uniform", 6))
    late = 0
    for seed in DEV[:40]:
        scenario = make_scenario(seed, cfg)
        uids = [o.uid for o in scenario.observations()]
        end_of_watch = max(o.d_nominal for o in scenario.observations())
        for policy in ASSURED:
            ep = run_episode(scenario, policy, PRESETS["high"], seed, 2, late_cancel=late_cancel)
            assert validate(ep.report, uids) == [], (seed, policy)
            assert false_closures(ep) == [], (seed, policy)
            effects = [x["uid"] for x in ep.log if x["type"] in ("CANCELLED", "CAPTURED")]
            assert sorted(effects) == sorted(uids), (seed, policy)
            late += sum(j["kind"] == "intent" and j["op"] == "cancel" and j["t"] >= end_of_watch
                        for j in ep.journal)
    assert (late > 0) == late_cancel  # the late path is exercised, and only when enabled
