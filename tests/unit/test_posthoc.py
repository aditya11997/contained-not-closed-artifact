"""Post hoc, exploratory additions (docs/POSTHOC.md): P3b `lm_ack_retry` (PH2) and the
transport's `timeout_factor` (PH3). Both must leave every frozen policy and the m = 1 path
unchanged."""
import json

import pytest
from sim_helpers import payment

from experiments import run as runner
from experiments.config import CONFIGS
from p4.contract import BASIS_REPLAY, BASIS_TIMEOUT_TRUSTED, Disposition, validate
from p4.harness import run_episode
from p4.runbooks import POLICIES, SimulatedCrash
from p4.runbooks.ack_retry import MAX_RETRIES
from p4.sim import faults
from p4.sim.clock import EventClock
from p4.sim.faults import FaultConfig, FaultDraw, FaultyTransport, load_fault_presets
from p4.sim.processor import SimProcessor
from p4.sim.scenario import Scenario, ScenarioConfig, make_scenario, partition_seeds

NONE = FaultConfig()
HIGH = load_fault_presets()["high"]
DEV = list(partition_seeds("dev"))
FROZEN = ("none", "edf", "mh", "lm_ack", "lm_verify", "lm_reconcile", "adaptive")


def drop_cancels(monkeypatch, attempts):
    """drop_req on the cancel attempts in `attempts` (all payments); every other call is ok."""
    def draw(seed, cfg, op, uid, attempt):
        return FaultDraw(op == "cancel" and attempt in attempts, False, False)
    monkeypatch.setattr(faults, "fault_draw", draw)


def one(d=20, s=2):
    return Scenario(payments=(payment("A", D=d, s=s, p=0.9),))


def cancel_results(ep):
    return [(j["t"], j["op"]) for j in ep.journal if j["kind"] == "result"]


def test_p3b_registered_alongside_the_frozen_policies():
    assert tuple(POLICIES)[:7] == FROZEN and "lm_ack_retry" in POLICIES


# -- P3b ---------------------------------------------------------------------------------------

def test_p3b_retries_a_dropped_cancel_and_trusts_the_definitive_reply(monkeypatch):
    drop_cancels(monkeypatch, {0})
    p3 = run_episode(one(), "lm_ack", NONE, DEV[0], 1)
    assert p3.report.entry("A").basis == BASIS_TIMEOUT_TRUSTED          # P3: false closure
    ep = run_episode(one(), "lm_ack_retry", NONE, DEV[0], 1)
    e = ep.report.entry("A")
    assert e.state == Disposition.CLOSED_CANCELLED and e.basis == BASIS_REPLAY
    assert ep.log[e.evidence_seq]["result"] == "CANCELLED"
    assert cancel_results(ep) == [(2, "cancel"), (4, "replay")]
    assert not ep.report.evidence_bounded


def test_p3b_retry_bound_then_trusts_the_final_timeout(monkeypatch):
    drop_cancels(monkeypatch, set(range(100)))
    ep = run_episode(one(), "lm_ack_retry", NONE, DEV[0], 1)
    assert len(ep.trace) == 1 + MAX_RETRIES
    e = ep.report.entry("A")
    assert e.state == Disposition.CLOSED_CANCELLED and e.basis == BASIS_TIMEOUT_TRUSTED
    assert [x["type"] for x in ep.log if x["uid"] == "A"][-1] == "CAPTURED"   # a false closure


def test_p3b_retries_only_while_the_cancel_can_finish(monkeypatch):
    drop_cancels(monkeypatch, set(range(100)))
    ep = run_episode(one(d=5, s=2), "lm_ack_retry", NONE, DEV[0], 1)
    assert cancel_results(ep) == [(2, "cancel"), (4, "replay")]   # 4 + 2 >= 5: no third send
    assert ep.report.entry("A").basis == BASIS_TIMEOUT_TRUSTED


def test_p3b_equals_p3_without_faults():
    for seed in DEV[:30]:
        scenario = make_scenario(seed, ScenarioConfig(n=8, slack="tight"))
        for k in (1, 4):
            a = run_episode(scenario, "lm_ack", NONE, seed, k)
            b = run_episode(scenario, "lm_ack_retry", NONE, seed, k)
            assert a.log == b.log and a.report.states() == b.report.states(), (seed, k)


def test_p3b_restart_from_the_journal_keeps_one_effect_per_payment(tmp_path):
    fired = []

    def hook(point, action, reply, rb):
        if point == "after_send" and len(fired) < 2:
            fired.append(point)
            raise SimulatedCrash()

    for seed in DEV[:20]:
        fired.clear()
        scenario = make_scenario(seed, ScenarioConfig(n=8, slack="tight"))
        ep = run_episode(scenario, "lm_ack_retry", HIGH, seed, 2, crash_hook=hook,
                         journal_path=tmp_path / f"{seed}.jsonl", journal_sync=False)
        uids = [o.uid for o in scenario.observations()]
        assert ep.restarts >= 1 and validate(ep.report, uids) == []
        effects = [x["uid"] for x in ep.log if x["type"] in ("CANCELLED", "CAPTURED")]
        assert sorted(effects) == sorted(uids), seed


# -- timeout_factor ----------------------------------------------------------------------------

def test_timeout_factor_delays_only_timeouts(monkeypatch):
    drop_cancels(monkeypatch, set(range(100)))
    ep = run_episode(one(), "lm_ack_retry", NONE, DEV[0], 1, timeout_factor=2)
    # each send reaches the sink at t + 2 and its timeout returns at t + 4
    assert cancel_results(ep) == [(4, "cancel"), (8, "replay"), (12, "replay"), (16, "replay")]
    assert [(r.t_sent, r.t_arrive) for r in ep.trace] == [(0, 2), (4, 6), (8, 10), (12, 14)]


def test_timeout_factor_leaves_successful_replies_alone():
    for seed in DEV[:20]:
        scenario = make_scenario(seed, ScenarioConfig(n=8, slack="tight"))
        for policy in POLICIES:
            a = run_episode(scenario, policy, NONE, seed, 2)
            b = run_episode(scenario, policy, NONE, seed, 2, timeout_factor=4)
            assert a.log == b.log and a.report == b.report and a.journal == b.journal, (seed, policy)
            assert a.inputs_sha256 != b.inputs_sha256


def test_timeout_factor_one_is_the_frozen_path():
    seed = DEV[7]
    scenario = make_scenario(seed, ScenarioConfig(n=16, slack="tight"))
    for policy in POLICIES:
        a = run_episode(scenario, policy, HIGH, seed, 4)
        b = run_episode(scenario, policy, HIGH, seed, 4, timeout_factor=1)
        assert a == b, policy


@pytest.mark.parametrize("m", [2, 4])
def test_timeout_factor_is_fair_and_keeps_the_contract(m):
    for seed in DEV[:20]:
        scenario = make_scenario(seed, ScenarioConfig(n=8, slack="tight"))
        uids = [o.uid for o in scenario.observations()]
        hashes = set()
        for policy in POLICIES:
            ep = run_episode(scenario, policy, HIGH, seed, 2, timeout_factor=m)
            hashes.add(ep.inputs_sha256)
            assert validate(ep.report, uids) == [], (seed, policy)
            if ep.report.evidence_bounded:
                final = {x["uid"]: x["type"] for x in ep.log if x["type"] in ("CANCELLED", "CAPTURED")}
                assert all(final[e.uid] == "CANCELLED" for e in ep.report.entries
                           if e.state == Disposition.CLOSED_CANCELLED), (seed, policy)
        assert len(hashes) == 1, seed


@pytest.mark.parametrize("bad", [0, -1, 1.5, True])
def test_transport_rejects_bad_timeout_factor(bad):
    clock = EventClock()
    with pytest.raises(ValueError):
        FaultyTransport(SimProcessor(one(), clock), clock, NONE, 0, cancel_latency={"A": 2},
                        status_latency=1, timeout_factor=bad)


def test_runner_refuses_timeout_factor_on_holdout(tmp_path):
    out = tmp_path / "h"
    assert runner.main(["--config", str(CONFIGS / "smoke.toml"), "--partition", "holdout",
                        "--out", str(out), "--timeout-factor", "2"]) == 2
    assert not out.exists()


def test_runner_records_timeout_factor(tmp_path):
    out = tmp_path / "d"
    assert runner.main(["--config", str(CONFIGS / "posthoc" / "primary_timeout.toml"), "--partition", "dev",
                        "--n-seeds", "1", "--seed-start", "110000", "--out", str(out), "--workers", "1",
                        "--no-keep-runs", "--timeout-factor", "2"]) == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["timeout_factor"] == 2 and manifest["checker"] == {"runs": 72, "not_ok": 0}
