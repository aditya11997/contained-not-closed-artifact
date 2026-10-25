from pathlib import Path

import pytest
from sim_helpers import effects, payment, sink

from p4.sim.faults import (
    FaultConfig,
    FaultyTransport,
    fault_draw,
    load_fault_presets,
    outage_windows,
)
from p4.sim.protocol import (
    CANCELLED,
    PENDING,
    REMEDIATION_OPERATOR,
    Processor,
)
from p4.sim.scenario import make_scenario

OP = REMEDIATION_OPERATOR
REPO = Path(__file__).resolve().parents[2]
NONE = FaultConfig()


def transport(s, clock, cfg=NONE, seed=100000, q=1):
    lat = {p.uid: p.s_cancel for p in s.scenario.payments}
    return FaultyTransport(s, clock, cfg, seed, cancel_latency=lat, status_latency=q)


def test_implements_processor_protocol():
    s, clock = sink(payment("A"))
    assert isinstance(transport(s, clock), Processor)


def test_no_faults_cancel_consumes_latency_then_applies():
    s, clock = sink(payment("A", D=10, s=3))
    tr = transport(s, clock)
    r = tr.cancel(OP, "A", "k")
    assert r.result == CANCELLED and clock.now() == 3
    assert s.log[r.effect_seq]["t"] == 3
    st = tr.status(OP, "A")
    assert st.state == CANCELLED and clock.now() == 4


def test_completion_at_capture_time_is_too_late():
    # Legacy feasibility: completion must be strictly before capture.
    s, clock = sink(payment("A", D=3, s=3))
    r = transport(s, clock).cancel(OP, "A", "k")
    assert r.result == "TOO_LATE_CAPTURED"


def test_capture_due_mid_latency_fires_before_the_cancel_applies():
    s, clock = sink(payment("A", D=3, s=5))
    r = transport(s, clock).cancel(OP, "A", "k")
    assert r.result == "TOO_LATE_CAPTURED" and clock.now() == 5
    cap, req = s.log[r.effect_seq], s.log[r.seq]
    assert (cap["type"], cap["t"]) == ("CAPTURED", 3)
    assert (req["type"], req["t"]) == ("CANCEL_REQ", 5) and cap["seq"] < req["seq"]


def test_capture_mid_latency_with_parallel_workers():
    # A's capture falls inside A's latency; B's cancel lands before B's capture.
    s, clock = sink(payment("A", D=3, s=5), payment("B", D=4, s=2))
    tr = transport(s, clock)
    got = {}
    tr.submit_cancel(OP, "A", "ka", lambda r: got.__setitem__("A", r.result))
    tr.submit_cancel(OP, "B", "kb", lambda r: got.__setitem__("B", r.result))
    clock.run_until(10)
    assert got == {"A": "TOO_LATE_CAPTURED", "B": CANCELLED}
    assert [(e["type"], e["uid"], e["t"]) for e in effects(s.log)] == \
        [("CANCELLED", "B", 2), ("CAPTURED", "A", 3)]


def test_drop_resp_times_out_but_sink_cancelled():
    s, clock = sink(payment("A", D=10, s=2))
    tr = transport(s, clock, FaultConfig(name="resp", f_resp=1.0))
    r = tr.cancel(OP, "A", "k")
    assert r.timeout and r.result is None and r.seq is None
    assert [e["type"] for e in effects(s.log, "A")] == ["CANCELLED"]
    clock.run_until(20)
    assert transport(s, clock).status(OP, "A").state == CANCELLED


def test_drop_req_times_out_and_sink_untouched():
    s, clock = sink(payment("A", D=10, s=2))
    n_before = len(s.log)
    tr = transport(s, clock, FaultConfig(name="req", f_req=1.0))
    assert tr.cancel(OP, "A", "k").timeout
    assert len(s.log) == n_before and clock.now() == 2
    assert tr.status(OP, "A").timeout
    assert transport(s, clock).status(OP, "A").state == PENDING


def test_replay_after_lost_ack_is_a_status_probe():
    s, clock = sink(payment("A", D=10, s=1))
    lossy = transport(s, clock, FaultConfig(name="resp", f_resp=1.0))
    assert lossy.cancel(OP, "A", "k").timeout
    again = transport(s, clock).cancel(OP, "A", "k")
    assert again.result == CANCELLED and s.log[again.seq]["type"] == "IDEMPOTENT_REPLAY"
    assert len(effects(s.log, "A")) == 1


def test_same_seed_uid_attempt_gives_same_fault_across_transports():
    cfg = FaultConfig(name="mix", f_req=0.3, f_resp=0.3)
    sc = make_scenario(100010)
    uids = [p.uid for p in sc.payments]

    def realized(order):
        s, clock = sink(*sc.payments)
        tr = transport(s, clock, cfg, seed=100010)
        for uid in order:
            tr.cancel(OP, uid, f"inc/{uid}/cancel/v1")
            tr.status(OP, uid)
        return {(c.op, c.uid, c.attempt): c.fault for c in tr.trace}

    a = realized(uids + uids)               # plan order, two attempts each
    b = realized(list(reversed(uids)) * 2)  # different order and timing
    assert a == b
    assert len(set(a.values())) > 1  # the config actually produces faults


def test_fault_draw_is_pure_and_keyed_by_op_uid_attempt():
    cfg = FaultConfig(name="mix", f_req=0.5, f_resp=0.5, outage_commit_prob=0.5)
    assert fault_draw(7, cfg, "cancel", "P00", 0) == fault_draw(7, cfg, "cancel", "P00", 0)
    draws = {fault_draw(7, cfg, op, uid, a) for op in ("cancel", "status")
             for uid in ("P00", "P01") for a in range(4)}
    assert len(draws) > 1
    assert {fault_draw(s, cfg, "cancel", "P00", 0) for s in range(20)} != {fault_draw(7, cfg, "cancel", "P00", 0)}


def test_outage_windows_identical_across_policies_and_seed_dependent():
    cfg = FaultConfig(name="out", outage_rate=0.2, outage_length=3, outage_commit_prob=0.5)
    w1 = outage_windows(100011, cfg, until=200)
    assert w1 == outage_windows(100011, cfg, until=200)
    assert w1 and all(e - s == 3 for s, e in w1)
    assert outage_windows(100012, cfg, until=200) != w1
    # Prefix-stable: a longer horizon does not change earlier windows.
    assert outage_windows(100011, cfg, until=400)[:len(w1)] == w1
    s, clock = sink(payment("A"))
    t1, t2 = transport(s, clock, cfg, seed=100011), transport(s, clock, cfg, seed=100011)
    for t in range(200):
        assert t1.in_outage(t) == t2.in_outage(t) == any(a <= t < b for a, b in w1)
    assert outage_windows(100011, NONE, until=1000) == []


def test_outage_cancel_times_out_and_commits_with_prob_r():
    always = FaultConfig(name="o1", outage_rate=1.0, outage_length=10**6, outage_commit_prob=1.0)
    never = FaultConfig(name="o0", outage_rate=1.0, outage_length=10**6, outage_commit_prob=0.0)
    for cfg, expect in ((always, ["CANCELLED"]), (never, [])):
        s, clock = sink(payment("A", D=10**6, s=1))
        tr = transport(s, clock, cfg, seed=3)
        clock.run_until(outage_windows(3, cfg, until=10)[0][0])
        assert tr.cancel(OP, "A", "k").timeout
        assert tr.status(OP, "A").timeout
        assert [e["type"] for e in effects(s.log, "A")] == expect
        assert not any(e["type"] == "STATUS_READ" for e in s.log)


def test_submit_api_supports_parallel_workers():
    s, clock = sink(payment("A", D=10, s=4), payment("B", D=10, s=2))
    tr = transport(s, clock)
    got = []
    tr.submit_cancel(OP, "A", "ka", lambda r: got.append(("A", clock.now(), r.result)))
    tr.submit_cancel(OP, "B", "kb", lambda r: got.append(("B", clock.now(), r.result)))
    clock.run_until(10)
    assert got == [("B", 2, CANCELLED), ("A", 4, CANCELLED)]


def test_presets_load_from_toml():
    presets = load_fault_presets(REPO / "configs" / "faults.toml")
    assert set(presets) >= {"none", "low", "high"}
    assert presets["none"] == FaultConfig(name="none")
    assert (presets["low"].f_req, presets["low"].f_resp) == (0.02, 0.05)
    assert (presets["high"].f_req, presets["high"].f_resp) == (0.05, 0.15)
    assert presets["high"].outage_rate > presets["low"].outage_rate >= 0
    with pytest.raises(ValueError):
        FaultConfig(name="bad", f_req=1.5)


def test_same_seed_and_config_give_byte_identical_logs(tmp_path):
    from p4.sim.clock import EventClock
    from p4.sim.processor import SimProcessor
    from p4.sim.scenario import JitterConfig, ScenarioConfig

    cfg = ScenarioConfig(jitter=JitterConfig("uniform", 2))
    high = load_fault_presets(REPO / "configs" / "faults.toml")["high"]

    def run(path):
        sc = make_scenario(100020, cfg)
        clock = EventClock()
        s = SimProcessor(sc, clock)
        tr = transport(s, clock, high, seed=100020)
        for o in sorted(sc.observations(), key=lambda o: o.d_nominal):
            if tr.cancel(OP, o.uid, f"inc/{o.uid}/cancel/v1").timeout:
                tr.cancel(OP, o.uid, f"inc/{o.uid}/cancel/v1")
        clock.run_until(max(clock.now(), sc.horizon))
        s.export_log(path)
        return path.read_bytes()

    assert run(tmp_path / "a.jsonl") == run(tmp_path / "b.jsonl")
