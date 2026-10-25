"""WP6 Part A, sim side: the agent create path, incident_stop, and DENIED post-stop calls that
are logged and never become effects (checker revoked_actor_effects = 0)."""
import json
import threading
import time

import pytest

from p4.harness import check_runs, export_run, run_episode
from p4.sim.agent_gateway import (
    CREATE_UID,
    ERROR_INVALID_REQUEST,
    POST_STOP,
    PRE_STOP,
    AgentPaymentRules,
    SimAgentGateway,
    agent_payment_truth,
)
from p4.sim.clock import WallClockDriver
from p4.sim.faults import load_fault_presets
from p4.sim.protocol import (
    CANCELLED,
    DENIED,
    ERROR_IDEMPOTENCY_MISMATCH,
    ERROR_UNKNOWN_PAYMENT,
    PENDING,
    PROCUREMENT_AGENT,
    REMEDIATION_OPERATOR,
    AgentGateway,
)
from p4.sim.scenario import JitterConfig

AGENT, OP = PROCUREMENT_AGENT, REMEDIATION_OPERATOR
RULES = AgentPaymentRules(jitter=JitterConfig("uniform", 3), flagged_vendors={"shady-supplies"})


def gateway_with(*purchases, seed=100123, rules=RULES):
    gw = SimAgentGateway(seed, rules)
    for i, (amount, vendor) in enumerate(purchases):
        assert gw.create_payment(AGENT, amount, vendor, f"po-{i}").payment_id is not None
    return gw


def test_implements_agent_gateway_protocol():
    assert isinstance(SimAgentGateway(1), AgentGateway)


def test_create_returns_a_pending_payment():
    gw = SimAgentGateway(100000)
    r = gw.create_payment(AGENT, 1200, "acme", "po-1")
    assert (r.payment_id, r.state, r.replayed, r.error, r.result) == ("A000", PENDING, False, None, None)
    assert gw.create_payment(AGENT, 300, "globex", "po-2").payment_id == "A001"
    assert gw.status(AGENT, "A000").state == PENDING


def test_create_idempotency_mirrors_stripe():
    gw = SimAgentGateway(100000)
    first = gw.create_payment(AGENT, 1200, "acme", "po-1")
    replay = gw.create_payment(AGENT, 1200, "acme", "po-1")
    assert replay.payment_id == first.payment_id and replay.replayed
    assert gw.create_payment(AGENT, 1300, "acme", "po-1").error == ERROR_IDEMPOTENCY_MISMATCH
    assert gw.create_payment(AGENT, 1200, "initech", "po-1").error == ERROR_IDEMPOTENCY_MISMATCH
    assert len(gw.created) == 1


@pytest.mark.parametrize("amount,vendor,key", [(0, "acme", "k"), (-5, "acme", "k"), (1.5, "acme", "k"),
                                               (True, "acme", "k"), (100, "", "k"), (100, "acme", "")])
def test_invalid_create_is_an_error_and_not_stored(amount, vendor, key):
    gw = SimAgentGateway(100000)
    assert gw.create_payment(AGENT, amount, vendor, key).error == ERROR_INVALID_REQUEST
    assert gw.created == ()
    if key:  # the key was not consumed by the invalid request
        assert gw.create_payment(AGENT, 100, "acme", key).payment_id == "A000"


def test_only_creators_create_and_nobody_cancels_before_the_stop():
    gw = gateway_with((500, "acme"))
    assert gw.create_payment(OP, 100, "acme", "x").result == DENIED
    assert gw.create_payment("someone_else", 100, "acme", "x").result == DENIED
    assert gw.cancel(AGENT, "A000", "k").result == DENIED
    assert gw.cancel(OP, "A000", "k").result == DENIED
    assert gw.status(OP, "A000").error == DENIED
    assert gw.status(AGENT, "A999").error == ERROR_UNKNOWN_PAYMENT
    assert gw.sink is None and len(gw.created) == 1


def test_stop_accepts_every_created_payment_at_t0():
    gw = gateway_with((500, "acme"), (700, "shady-supplies"), (900, "globex"))
    receipt = gw.incident_stop(AGENT)
    assert receipt.revoked == AGENT and receipt.pending == ("A000", "A001", "A002")
    assert [(e["type"], e["uid"], e["t"]) for e in gw.sink.log] == [
        ("ACCEPTED", "A000", 0), ("ACCEPTED", "A001", 0), ("ACCEPTED", "A002", 0)]
    pays = {p.uid: p for p in gw.scenario.payments}
    assert [pays[u].amount for u in receipt.pending] == [500, 700, 900]
    assert pays["A001"].y_suspect and pays["A001"].p_score == RULES.p_flagged
    assert not pays["A000"].y_suspect and pays["A000"].p_score == RULES.p_other


def test_post_stop_calls_by_the_revoked_agent_are_denied_and_logged():
    gw = gateway_with((500, "acme"), (700, "globex"))
    gw.incident_stop(AGENT)
    gw.clock.run_until(1)
    assert gw.create_payment(AGENT, 100, "acme", "po-late").result == DENIED
    assert gw.create_payment(AGENT, 500, "acme", "po-0").result == DENIED  # not even a replay
    assert gw.cancel(AGENT, "A000", "k").result == DENIED
    assert gw.status(AGENT, "A000").error == DENIED
    assert len(gw.created) == 2 and len(gw.scenario.payments) == 2

    denied = [e for e in gw.sink.log if e["type"] == "DENIED"]
    assert [(e["actor"], e["uid"], e["key"], e["t"]) for e in denied] == [
        (AGENT, CREATE_UID, "po-late", 1), (AGENT, CREATE_UID, "po-0", 1),
        (AGENT, "A000", "k", 1), (AGENT, "A000", None, 1)]
    post = [c for c in gw.calls if c["phase"] == POST_STOP and c["actor"] == AGENT and c["op"] != "incident_stop"]
    assert [(c["op"], c["result"] or c["error"]) for c in post] == [
        ("create", DENIED), ("create", DENIED), ("cancel", DENIED), ("status", DENIED)]
    assert all(c["seq"] is not None and gw.sink.log[c["seq"]]["type"] == "DENIED" for c in post)
    assert {c["phase"] for c in gw.calls[:2]} == {PRE_STOP}
    # The remediator still can.
    assert gw.cancel(OP, "A000", "rk").result == CANCELLED


def test_stop_by_another_actor_also_freezes_creates():
    gw = gateway_with((500, "acme"))
    gw.incident_stop("some_other_agent")
    assert not gw.revoked(AGENT)
    assert gw.create_payment(AGENT, 100, "acme", "new").result == DENIED
    assert gw.incident_stop(AGENT).pending == ("A000",)   # a second stop only revokes
    assert gw.revoked(AGENT) and len(gw.scenario.payments) == 1


def test_record_denied_refuses_the_operator():
    gw = gateway_with((500, "acme"))
    gw.incident_stop(AGENT)
    with pytest.raises(ValueError):
        gw.sink.record_denied(OP, CREATE_UID, "k")


def test_truth_depends_only_on_seed_index_and_request():
    a = agent_payment_truth(100500, 3, 800, "acme", RULES)
    assert a == agent_payment_truth(100500, 3, 800, "acme", RULES)
    gw1 = gateway_with((100, "x"), (200, "y"), (800, "acme"), seed=100500)
    gw2 = gateway_with((999, "z"), (1, "w"), (800, "acme"), seed=100500)
    gw1.incident_stop(AGENT)
    gw2.incident_stop(AGENT)
    assert gw1.scenario.payments[2] == gw2.scenario.payments[2]
    assert gw1.scenario.payments[2].D_actual == agent_payment_truth(100500, 2, 800, "acme", RULES).D_actual
    for p in gw1.scenario.payments:
        assert RULES.d_min <= p.d_nominal <= RULES.d_max and abs(p.D_actual - p.d_nominal) <= 3


def test_export_calls_is_jsonl(tmp_path):
    gw = gateway_with((500, "acme"))
    gw.incident_stop(AGENT)
    gw.create_payment(AGENT, 100, "acme", "late")
    gw.export_calls(tmp_path / "gateway.jsonl")
    rows = [json.loads(x) for x in (tmp_path / "gateway.jsonl").read_text().splitlines()]
    assert [r["op"] for r in rows] == ["create", "incident_stop", "create"] and rows == list(gw.calls)


@pytest.mark.parametrize("policy", ["lm_ack", "lm_reconcile", "adaptive"])
@pytest.mark.parametrize("fault", ["none", "high"])
def test_episode_on_agent_payments_checks_clean_with_denied_agent_calls(tmp_path, policy, fault):
    """The remediator runs on the agent-created payments while the revoked agent keeps
    calling; the independent checker (separate process) sees the DENIED lines and no
    revoked-actor effect."""
    gw = gateway_with((500, "acme"), (1200, "shady-supplies"), (300, "globex"), (900, "shady-supplies"))
    gw.incident_stop(AGENT)

    def sink_factory(scenario, clock):
        sink = gw.sink_factory(scenario, clock)
        for t in (1, 3):
            clock.schedule(t, lambda: (gw.create_payment(AGENT, 400, "acme", f"po-late-{clock.now()}"),
                                       gw.cancel(AGENT, "A001", "agent-cancel"),
                                       gw.status(AGENT, "A001")))
        return sink

    ep = run_episode(gw.scenario, policy, load_fault_presets()[fault], gw.seed, 1, sink_factory=sink_factory)
    denied = [e for e in ep.log if e["type"] == "DENIED" and e["actor"] == AGENT]
    assert [e["t"] for e in denied] == [1, 1, 1, 3, 3, 3]
    paths = export_run(ep, gw.scenario, 0.5, tmp_path / "run")
    (verdict,) = check_runs([paths], [policy], tmp_path / "verdicts.jsonl")
    assert verdict["counts"]["revoked_actor_effects"] == 0
    assert verdict["ok"], verdict["problems"]


def test_agent_thread_runs_through_the_stop_and_a_wall_clock_episode(tmp_path):
    """WP6 case-study shape: the agent loop runs in its own thread, the stop fires while it is
    still purchasing, and the remediation episode runs on the stop's WallClockDriver while the
    revoked agent keeps calling. Every payment the agent was told about is in the scenario, every
    post-stop call is DENIED and in the episode log, and the checker passes."""
    gw = SimAgentGateway(100123, RULES, clock_factory=lambda: WallClockDriver(0.002))
    enough, halt, replies = threading.Event(), threading.Event(), []

    def agent():
        i = 0
        while not halt.is_set():
            vendor = "shady-supplies" if i % 3 == 0 else "acme"
            replies.append(gw.create_payment(AGENT, 100 + i, vendor, f"po-{i}"))
            gw.status(AGENT, "A000")
            gw.cancel(AGENT, "A000", f"agent-cancel-{i}")
            if sum(r.payment_id is not None for r in replies) >= 3:
                enough.set()
            i += 1
            time.sleep(0.0005)

    thread = threading.Thread(target=agent)
    thread.start()
    assert enough.wait(5)
    receipt = gw.incident_stop(AGENT)
    ep = run_episode(gw.scenario, "adaptive", load_fault_presets()["high"], gw.seed, 1,
                     clock=gw.clock, sink_factory=gw.sink_factory)
    halt.set()
    thread.join(5)
    assert not thread.is_alive()

    created = [r.payment_id for r in replies if r.payment_id is not None]
    assert created == list(receipt.pending) == [p.uid for p in gw.scenario.payments]
    post = [c for c in gw.calls if c["phase"] == POST_STOP and c["actor"] == AGENT and c["op"] != "incident_stop"]
    assert post and all(c["result"] == DENIED or c["error"] == DENIED for c in post)
    assert any(c["op"] == "create" for c in post)
    assert [e["seq"] for e in ep.log] == list(range(len(ep.log)))
    assert [e["t"] for e in ep.log] == sorted(e["t"] for e in ep.log)
    in_log = {e["seq"] for e in ep.log if e["type"] == "DENIED" and e["actor"] == AGENT}
    assert in_log and {c["seq"] for c in post if c["seq"] is not None} <= in_log
    assert any(ep.log[q]["t"] > 0 for q in in_log)   # the agent kept calling during the episode
    paths = export_run(ep, gw.scenario, 0.5, tmp_path / "run")
    (verdict,) = check_runs([paths], ["adaptive"], tmp_path / "verdicts.jsonl")
    assert verdict["counts"]["revoked_actor_effects"] == 0
    assert verdict["ok"], verdict["problems"]
