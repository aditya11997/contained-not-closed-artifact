"""Regression tests for CODE_AUDIT A1, A2, A3 and A5, re-targeted from legacy p4lab (WP0,
strict xfail) at p4.sim (WP1, passing). Each asserts the same correct behaviour as in WP0.
"""
import pytest
from sim_helpers import effects, payment, sink

from p4.sim.oracle import score
from p4.sim.protocol import (
    CANCELLED,
    CAPTURED,
    DENIED,
    ORIGINAL_AGENT,
    REMEDIATION_OPERATOR,
)

OPERATOR = REMEDIATION_OPERATOR


def test_a1_idempotency_key_is_bound_to_its_request():
    s, clock = sink(payment("A"), payment("B"))
    assert s.cancel(OPERATOR, "A", "K").result == CANCELLED

    reply_b = s.cancel(OPERATOR, "B", "K")
    clock.run_until(11)
    state_b = s.status(OPERATOR, "B").state
    # Reusing K for a different payment must not report a cancellation that never happened.
    assert not (reply_b.result == CANCELLED and state_b != CANCELLED), (reply_b, state_b)


def test_a2_unauthorized_call_does_not_poison_key_for_operator():
    s, clock = sink(payment("A"))
    assert s.cancel(ORIGINAL_AGENT, "A", "K").result == DENIED

    # The legitimate operator's call with the same key must be evaluated on its own merits.
    assert s.cancel(OPERATOR, "A", "K").result == CANCELLED
    clock.run_until(11)
    assert s.status(OPERATOR, "A").state == CANCELLED


def test_a3_caller_cannot_backdate_cancel_after_capture():
    s, clock = sink(payment("A", D=3))
    clock.run_until(5)
    assert s.status(OPERATOR, "A").state == CAPTURED

    # No caller-supplied time exists, and the clock cannot be moved backwards.
    with pytest.raises(ValueError):
        clock.run_until(0)
    assert s.cancel(OPERATOR, "A", "K2").result != CANCELLED
    assert s.status(OPERATOR, "A").state == CAPTURED
    assert [e["type"] for e in effects(s.log, "A")] == ["CAPTURED"]


def test_a5_denied_call_is_a_rejection_not_an_authority_violation():
    s, clock = sink(payment("A"))
    assert s.cancel(ORIGINAL_AGENT, "A", "K").result == DENIED
    clock.run_until(11)

    truth = score(s.log, s.scenario, c=0.5)
    # A denial is reported as such; it is not an effect by an unauthorized identity.
    assert truth.get("denied_calls") == 1, truth
    assert truth.get("revoked_actor_effects", 0) == 0, truth
    assert "authority_violations" not in truth
