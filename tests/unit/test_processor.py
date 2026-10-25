import json

import pytest
from sim_helpers import chain_ok, effects, payment, sink

from p4.sim.processor import SimProcessor
from p4.sim.protocol import (
    ALREADY_CANCELLED,
    CANCELLED,
    CAPTURED,
    DENIED,
    ERROR_IDEMPOTENCY_MISMATCH,
    ERROR_UNKNOWN_PAYMENT,
    ORIGINAL_AGENT,
    PENDING,
    REMEDIATION_OPERATOR,
    TOO_LATE_CAPTURED,
    Processor,
)

OP = REMEDIATION_OPERATOR


def test_implements_processor_protocol():
    s, _ = sink(payment("A"))
    assert isinstance(s, Processor)


def test_accepted_lines_then_capture_at_actual_time():
    s, clock = sink(payment("A", D=4), payment("B", D=7))
    assert [e["type"] for e in s.log] == ["ACCEPTED", "ACCEPTED"]
    clock.run_until(4)
    assert s.status(OP, "A").state == CAPTURED
    assert s.status(OP, "B").state == PENDING
    cap = effects(s.log, "A")
    assert len(cap) == 1 and cap[0]["t"] == 4


def test_cancel_before_capture_wins_and_capture_becomes_noop():
    s, clock = sink(payment("A", D=4))
    clock.run_until(3)
    r = s.cancel(OP, "A", "k")
    assert r.result == CANCELLED and r.error is None and not r.timeout
    assert s.log[r.effect_seq]["type"] == "CANCELLED" and s.log[r.seq]["type"] == "CANCEL_REQ"
    clock.run_until(20)
    assert s.status(OP, "A").state == CANCELLED
    assert [e["type"] for e in effects(s.log, "A")] == ["CANCELLED"]


def test_cancel_at_capture_time_is_too_late():
    s, clock = sink(payment("A", D=4))
    clock.run_until(4)
    r = s.cancel(OP, "A", "k")
    assert r.result == TOO_LATE_CAPTURED
    assert s.log[r.effect_seq]["type"] == "CAPTURED"


def test_terminal_states_are_absorbing():
    s, clock = sink(payment("A", D=4), payment("B", D=4))
    s.cancel(OP, "A", "ka")
    clock.run_until(4)
    assert s.cancel(OP, "A", "ka2").result == ALREADY_CANCELLED
    assert s.cancel(OP, "B", "kb").result == TOO_LATE_CAPTURED
    clock.run_until(50)
    assert s.status(OP, "A").state == CANCELLED and s.status(OP, "B").state == CAPTURED
    for uid in "AB":
        assert len(effects(s.log, uid)) == 1


def test_replay_returns_stored_reply_without_second_effect():
    s, clock = sink(payment("A", D=10))
    first = s.cancel(OP, "A", "k")
    clock.run_until(12)
    again = s.cancel(OP, "A", "k")
    assert again.result == first.result == CANCELLED
    assert again.effect_seq == first.effect_seq
    assert s.log[again.seq]["type"] == "IDEMPOTENT_REPLAY"
    assert len(effects(s.log, "A")) == 1


def test_denial_is_logged_but_not_cached():
    s, _ = sink(payment("A"))
    r = s.cancel(ORIGINAL_AGENT, "A", "k")
    assert r.result == DENIED and r.effect_seq is None
    assert s.log[r.seq]["type"] == "DENIED"
    assert s.cancel(OP, "A", "k").result == CANCELLED


def test_unpermitted_payment_is_denied_for_operator():
    s, _ = sink(payment("A", permitted=False))
    assert s.cancel(OP, "A", "k").result == DENIED
    assert effects(s.log, "A") == []


def test_unknown_payment_is_an_error_without_effect():
    s, _ = sink(payment("A"))
    r = s.cancel(OP, "Z", "k")
    assert r.error == ERROR_UNKNOWN_PAYMENT and r.result is None
    assert s.cancel(OP, "A", "k").result == CANCELLED  # error not cached under k


def test_key_mismatch_is_an_error_without_effect():
    s, _ = sink(payment("A"), payment("B"))
    s.cancel(OP, "A", "k")
    r = s.cancel(OP, "B", "k")
    assert r.error == ERROR_IDEMPOTENCY_MISMATCH and r.result is None
    assert s.log[r.seq]["type"] == "MISMATCH"
    assert effects(s.log, "B") == [] and s.status(OP, "B").state == PENDING


def test_status_read_is_logged_and_operator_only():
    s, _ = sink(payment("A"))
    r = s.status(OP, "A")
    assert r.state == PENDING and s.log[r.seq]["type"] == "STATUS_READ"
    assert s.log[r.seq]["result"] == PENDING
    assert s.status(ORIGINAL_AGENT, "A").error == DENIED
    assert s.status(OP, "Z").error == ERROR_UNKNOWN_PAYMENT


def test_log_entries_have_schema_and_chain_verifies():
    s, clock = sink(payment("A", D=3), payment("B", D=8))
    s.cancel(ORIGINAL_AGENT, "B", "x")
    s.cancel(OP, "B", "k")
    s.status(OP, "A")
    clock.run_until(10)
    keys = {"seq", "t", "type", "uid", "actor", "key", "result", "prev_hash", "hash"}
    assert all(set(e) == keys for e in s.log)
    assert chain_ok(s.log)
    ts = [e["t"] for e in s.log]
    assert ts == sorted(ts)


def test_log_is_read_only():
    s, _ = sink(payment("A"))
    with pytest.raises((TypeError, AttributeError)):
        s.log.append({})
    with pytest.raises(TypeError):
        s.log[0]["type"] = "X"


def test_export_log_roundtrips_and_is_deterministic(tmp_path):
    def run(path):
        s, clock = sink(payment("A", D=3), payment("B", D=8))
        s.cancel(OP, "B", "k")
        clock.run_until(10)
        s.export_log(path)
        return path.read_bytes()

    a, b = run(tmp_path / "a.jsonl"), run(tmp_path / "b.jsonl")
    assert a == b
    lines = [json.loads(x) for x in a.decode().splitlines()]
    assert chain_ok(lines) and len(lines) == 5


def test_construction_requires_fresh_clock():
    from p4.sim.clock import EventClock
    from p4.sim.scenario import Scenario
    clock = EventClock()
    clock.run_until(1)
    with pytest.raises(ValueError):
        SimProcessor(Scenario(payments=(payment("A"),)), clock)
