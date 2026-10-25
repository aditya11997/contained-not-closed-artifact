"""C1 conformance: the same scripted sequences against the simulator and Stripe test mode,
asserting matching abstract outcomes (WP5; REVAMP_PLAN §4).

Both backends get identical payments (same seed, rules and purchases) and a logical
EventClock, so the expected result is stronger than "same verdicts": the two event logs must
be line-for-line identical (same seq, t, type, uid, actor, key, result, hence the same hash
chain), and every reply must agree. Stripe-side facts the sim does not model (the
Idempotent-Replayed header, error type/code, the refused capture) are asserted from the
sanitized observations. Every outcome is recorded for docs/STRIPE_MAPPING.md (conftest.py).

Payments: D_actual = 5 for every payment in the scripted transitions (d_min = d_max = 5,
no jitter). The whole-episode checks use d in 4..9 and jitter 2.
"""
import json

import pytest

from p4.adapters.stripe_test import StripeTestProcessor, default_client
from p4.harness import check_runs, export_run, run_episode
from p4.sim.agent_gateway import AgentPaymentRules, SimAgentGateway
from p4.sim.faults import FaultConfig, FaultyTransport, fault_draw, load_fault_presets
from p4.sim.protocol import (
    ALREADY_CANCELLED,
    CANCELLED,
    CAPTURED,
    DENIED,
    ERROR_IDEMPOTENCY_MISMATCH,
    PENDING,
    PROCUREMENT_AGENT,
    REMEDIATION_OPERATOR,
    TOO_LATE_CAPTURED,
)
from p4.sim.scenario import JitterConfig

pytestmark = pytest.mark.stripe

AGENT, OP = PROCUREMENT_AGENT, REMEDIATION_OPERATOR
SEED = 100901
FIXED_D = AgentPaymentRules(d_min=5, d_max=5)


class Backends:
    """One sim gateway and one Stripe adapter driven by the same script."""

    def __init__(self, record, name, rules=FIXED_D, faults=None, purchases=((1200, "acme"),), seed=SEED):
        self.record, self.name = record, name
        self.sim = SimAgentGateway(seed, rules)
        self.stripe = StripeTestProcessor(default_client(), run_id=f"{record.session}-{name}", seed=seed,
                                          rules=rules, faults=faults)
        self.steps = []
        self.targets = None   # (sim, stripe) Processor pair for cancel/status; default: the sinks
        for i, (amount, vendor) in enumerate(purchases):
            self.both("create", AGENT, amount, vendor, f"po-{i}")

    def both(self, op, *args):
        """Run one call on both backends; return (sim reply, stripe reply)."""
        if op == "advance":
            self.sim.clock.run_until(args[0])
            self.stripe.clock.run_until(args[0])
            return None, None
        if op == "stop":
            return self.sim.incident_stop(*args), self.stripe.incident_stop(*args)
        method = {"create": "create_payment", "cancel": "cancel", "status": "status"}[op]
        sim, stripe = self.targets if self.targets and op != "create" else (self.sim, self.stripe)
        a, b = getattr(sim, method)(*args), getattr(stripe, method)(*args)
        self.steps.append((op, args, a, b))
        return a, b

    def finish(self, transition, **extra):
        sim_log = [dict(e) for e in self.sim.sink.log]
        stripe_log = [dict(e) for e in self.stripe.log]
        replies_match = all(a == b for _, _, a, b in self.steps)
        match = sim_log == stripe_log and replies_match and not self.stripe.anomalies
        self.record.outcome(transition, _project(sim_log), _project(stripe_log), match,
                            replies=[[op, list(args), _reply(a), _reply(b)] for op, args, a, b in self.steps],
                            anomalies=list(self.stripe.anomalies), **extra)
        self.record.observations(transition, self.stripe)
        assert self.stripe.anomalies == []
        assert stripe_log == sim_log
        for op, args, a, b in self.steps:
            assert a == b, (op, args, a, b)
        return self.stripe

    def through_transports(self, cfg):
        """Send later cancels/status reads through FaultyTransport (same latencies on both
        sides): the sim's faults from the transport, Stripe's from the adapter's proxy."""
        lat = {p.uid: p.s_cancel for p in self.sim.scenario.payments}
        self.targets = (FaultyTransport(self.sim.sink, self.sim.clock, cfg, self.sim.seed,
                                        cancel_latency=lat, status_latency=1),
                        FaultyTransport(self.stripe, self.stripe.clock, FaultConfig("none"), self.sim.seed,
                                        cancel_latency=lat, status_latency=1))

    def obs(self, op, uid=None, who=None):
        return [o for o in self.stripe.observations
                if o.op == op and (uid is None or o.uid == uid) and (who is None or o.who == who)]


def _project(log):
    return [[e["t"], e["type"], e["uid"], e["actor"], e["result"]] for e in log]


def _reply(r):
    return None if r is None else {k: v for k, v in r.__dict__.items() if v not in (None, False)}


# -- scripted transitions -----------------------------------------------------------------------------
def test_create_reaches_requires_capture_and_replays(record):
    b = Backends(record, "create", purchases=())
    first, _ = b.both("create", AGENT, 1200, "acme", "po-1")
    again, _ = b.both("create", AGENT, 1200, "acme", "po-1")
    other, _ = b.both("create", AGENT, 1300, "acme", "po-1")
    status, _ = b.both("status", AGENT, "A000")
    b.both("stop", AGENT)
    assert first.state == PENDING and again.replayed and other.error == ERROR_IDEMPOTENCY_MISMATCH
    assert status.state == PENDING
    stripe = b.finish("create_manual_capture")
    creates = b.obs("create")
    assert [(o.http, o.pi_status, o.replayed, o.error_type) for o in creates] == [
        (200, "requires_capture", False, None), (200, "requires_capture", True, None),
        (400, None, False, "idempotency_error")]
    assert len({o.pi for o in creates[:2]}) == 1 and stripe.pi_of("A000") == creates[0].pi


def test_cancel_in_requires_capture_then_capture_is_refused(record):
    b = Backends(record, "cancel-pending")
    b.both("stop", AGENT)
    b.both("advance", 2)
    r, _ = b.both("cancel", OP, "A000", "c0")
    s1, _ = b.both("status", OP, "A000")
    b.both("advance", 6)       # merchant capture at D = 5 meets a cancelled PaymentIntent
    s2, _ = b.both("status", OP, "A000")
    assert r.result == CANCELLED and s1.state == s2.state == CANCELLED
    b.finish("cancel_in_requires_capture")
    (cancel,) = b.obs("cancel")
    assert (cancel.http, cancel.pi_status) == (200, "canceled")
    (capture,) = b.obs("capture")
    assert (capture.http, capture.error_code, capture.pi_status) == (400, "payment_intent_unexpected_state", "canceled")


def test_capture_after_cancel_is_a_noop(record):
    b = Backends(record, "capture-after-cancel")
    b.both("stop", AGENT)
    b.both("advance", 4)
    b.both("cancel", OP, "A000", "c0")
    b.both("advance", 5)
    s, _ = b.both("status", OP, "A000")
    assert s.state == CANCELLED
    b.finish("capture_after_cancel")
    (capture,) = b.obs("capture")
    assert (capture.http, capture.error_code) == (400, "payment_intent_unexpected_state")


def test_cancel_after_capture_is_too_late(record):
    b = Backends(record, "cancel-captured")
    b.both("stop", AGENT)
    b.both("advance", 5)        # the capture fires first at t = 5 (ties go to the capture)
    r, _ = b.both("cancel", OP, "A000", "c0")
    s, _ = b.both("status", OP, "A000")
    replay, _ = b.both("cancel", OP, "A000", "c0")
    assert r.result == replay.result == TOO_LATE_CAPTURED and s.state == CAPTURED
    b.finish("cancel_after_capture")
    cancels = b.obs("cancel")
    assert [(o.http, o.error_code, o.pi_status, o.replayed) for o in cancels] == [
        (400, "payment_intent_unexpected_state", "succeeded", False),
        (400, "payment_intent_unexpected_state", "succeeded", True)]
    (capture,) = b.obs("capture")
    assert (capture.http, capture.pi_status) == (200, "succeeded")


def test_replay_same_key_same_params_returns_the_stored_result(record):
    b = Backends(record, "replay")
    b.both("stop", AGENT)
    first, _ = b.both("cancel", OP, "A000", "c0")
    replay, _ = b.both("cancel", OP, "A000", "c0")
    s, _ = b.both("status", OP, "A000")
    assert first.result == replay.result == CANCELLED and replay.effect_seq == first.effect_seq
    assert s.state == CANCELLED
    stripe = b.finish("replay_same_key_same_params")
    assert stripe.log[replay.seq]["type"] == "IDEMPOTENT_REPLAY"
    assert [o.replayed for o in b.obs("cancel")] == [False, True]


def test_same_key_different_params_is_a_mismatch_with_no_effect(record):
    b = Backends(record, "mismatch", purchases=((1200, "acme"), (800, "globex")))
    b.both("stop", AGENT)
    b.both("cancel", OP, "A000", "c0")
    m, _ = b.both("cancel", OP, "A001", "c0")     # same key, other PaymentIntent
    s1, _ = b.both("status", OP, "A001")
    b.both("advance", 5)
    s2, _ = b.both("status", OP, "A001")
    assert m.error == ERROR_IDEMPOTENCY_MISMATCH and s1.state == PENDING and s2.state == CAPTURED
    b.finish("same_key_different_params")
    mismatch = b.obs("cancel", uid="A001")
    assert [(o.http, o.error_type) for o in mismatch] == [(400, "idempotency_error")]


def test_new_key_on_a_cancelled_payment_is_already_cancelled(record):
    b = Backends(record, "already")
    b.both("stop", AGENT)
    b.both("cancel", OP, "A000", "c0")
    r, _ = b.both("cancel", OP, "A000", "c1")
    s, _ = b.both("status", OP, "A000")
    assert r.result == ALREADY_CANCELLED and s.state == CANCELLED
    b.finish("cancel_already_cancelled")
    second = b.obs("cancel")[1]
    assert (second.http, second.error_code, second.pi_status, second.replayed) == (
        400, "payment_intent_unexpected_state", "canceled", False)


LOST_ACK = FaultConfig("lost_ack", f_resp=0.5)


def test_lost_ack_commits_and_a_replay_probes_it(record):
    """drop_resp at a real processor: Stripe cancels, the caller sees a timeout; the replay
    with the same key returns the stored CANCELLED. At SEED the realization is: cancel
    attempt 0 drop_resp, attempt 1 delivered, status attempt 0 delivered (asserted below)."""
    draws = [fault_draw(SEED, LOST_ACK, op, "A000", a) for op, a in (("cancel", 0), ("cancel", 1), ("status", 0))]
    assert [d.drop_resp for d in draws] == [True, False, False]
    b = Backends(record, "lost-ack", faults=LOST_ACK)
    b.both("stop", AGENT)
    b.through_transports(LOST_ACK)
    first, _ = b.both("cancel", OP, "A000", "c0")
    probe, _ = b.both("cancel", OP, "A000", "c0")
    s, _ = b.both("status", OP, "A000")
    assert first.timeout and probe.result == CANCELLED and s.state == CANCELLED
    stripe = b.finish("drop_resp_lost_ack_then_replay")
    assert [p.fault for p in stripe.proxy.trace] == ["drop_resp", "ok", "ok"]
    assert [(o.http, o.pi_status, o.replayed) for o in b.obs("cancel")] == [(200, "canceled", False),
                                                                             (200, "canceled", True)]
    assert stripe.log[probe.seq]["type"] == "IDEMPOTENT_REPLAY"


def test_revoked_agent_is_denied_after_the_stop(record):
    b = Backends(record, "revoked")
    b.both("stop", AGENT)
    b.both("advance", 1)
    n_http = len(b.stripe.observations)
    c, _ = b.both("create", AGENT, 500, "acme", "po-late")
    x, _ = b.both("cancel", AGENT, "A000", "agent-cancel")
    s, _ = b.both("status", AGENT, "A000")
    assert c.result == x.result == DENIED and s.error == DENIED
    assert len(b.stripe.observations) == n_http   # nothing was sent to Stripe
    b.finish("revoked_agent_post_stop")


# -- whole episodes ----------------------------------------------------------------------------------
EPISODE_RULES = AgentPaymentRules(d_min=4, d_max=9, jitter=JitterConfig("uniform", 2),
                                  flagged_vendors={"shady-supplies"})
PURCHASES = ((1200, "acme"), (900, "shady-supplies"), (400, "globex"), (1500, "shady-supplies"))
# First dev seed >= 100901 at which every policy below meets >= 1 injected fault at "high",
# including a drop_resp (deterministic offline search over the simulator; the run at SEED
# injected none, so the "high" episodes were vacuous as fault tests). Asserted in the test.
EPISODE_SEED = 100906


@pytest.mark.parametrize("fault", ["none", "high"])
@pytest.mark.parametrize("policy", ["lm_ack", "lm_reconcile", "adaptive"])
def test_episode_logs_match_and_check_clean(record, tmp_path, policy, fault):
    """P3/P5/P6 on agent-created payments. The sim's faults come from FaultyTransport, Stripe's
    from StripeFaultProxy with the same realization; the transport in front of the adapter is
    fault-free. Logs must be identical and the independent checker must pass both."""
    cfg = load_fault_presets()[fault]
    b = Backends(record, f"ep-{policy}-{fault}", rules=EPISODE_RULES, faults=cfg, purchases=PURCHASES,
                 seed=EPISODE_SEED)
    b.both("stop", AGENT)
    sim_ep = run_episode(b.sim.scenario, policy, cfg, EPISODE_SEED, 1, sink_factory=b.sim.sink_factory)
    stripe_ep = run_episode(b.stripe.scenario, policy, FaultConfig("none"), EPISODE_SEED, 1,
                            sink_factory=b.stripe.sink_factory)
    paths = [export_run(sim_ep, b.sim.scenario, 0.5, tmp_path / "sim"),
             export_run(stripe_ep, b.stripe.scenario, 0.5, tmp_path / "stripe")]
    verdicts = check_runs(paths, [policy, policy], tmp_path / "verdicts.jsonl")
    sim_log, stripe_log = [dict(e) for e in sim_ep.log], [dict(e) for e in stripe_ep.log]
    match = sim_log == stripe_log and sim_ep.report == stripe_ep.report and not b.stripe.anomalies
    record.outcome(f"episode/{policy}/{fault}", _project(sim_log), _project(stripe_log), match,
                   verdicts=[{"ok": v["ok"], "counts": v["counts"]} for v in verdicts],
                   faults=[p.fault for p in b.stripe.proxy.trace], anomalies=list(b.stripe.anomalies))
    record.observations(f"episode/{policy}/{fault}", b.stripe)
    assert b.stripe.anomalies == []
    assert stripe_log == sim_log
    assert [r.fault for r in sim_ep.trace] == [p.fault for p in b.stripe.proxy.trace]
    if fault == "high":
        assert any(p.fault != "ok" for p in b.stripe.proxy.trace), "vacuous: no fault injected"
    assert json.dumps(sim_ep.report.to_dict(), sort_keys=True) == json.dumps(stripe_ep.report.to_dict(), sort_keys=True)
    assert all(v["ok"] for v in verdicts), [v["problems"] for v in verdicts]
    assert all(v["counts"]["revoked_actor_effects"] == 0 for v in verdicts)
