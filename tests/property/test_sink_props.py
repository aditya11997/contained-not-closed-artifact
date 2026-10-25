"""Sink invariants under random call sequences (CODE_AUDIT E6), direct and through faults."""
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sim_helpers import chain_ok, effects, payment, sink

from p4.sim.faults import FaultConfig, FaultyTransport
from p4.sim.protocol import ORIGINAL_AGENT, REMEDIATION_OPERATOR

UIDS = ("A", "B", "C")
KEYS = ("k1", "k2", "k3")
ACTORS = (REMEDIATION_OPERATOR, REMEDIATION_OPERATOR, ORIGINAL_AGENT)

payments_st = st.tuples(*[
    st.builds(payment, st.just(u), D=st.integers(1, 12), s=st.integers(1, 3),
              permitted=st.booleans()) for u in UIDS])
op_st = st.one_of(
    st.tuples(st.just("wait"), st.integers(0, 4)),
    st.tuples(st.just("cancel"), st.sampled_from(ACTORS), st.sampled_from(UIDS), st.sampled_from(KEYS)),
    st.tuples(st.just("status"), st.sampled_from(ACTORS), st.sampled_from(UIDS)),
)
fault_st = st.builds(FaultConfig, name=st.just("prop"), f_req=st.sampled_from([0, .3, 1]),
                     f_resp=st.sampled_from([0, .3, 1]), outage_rate=st.sampled_from([0, .2]),
                     outage_length=st.integers(1, 4), outage_commit_prob=st.sampled_from([0, .5, 1]))
SETTINGS = settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])


EXPECTED_EFFECT = {"CANCELLED": "CANCELLED", "ALREADY_CANCELLED": "CANCELLED",
                   "TOO_LATE_CAPTURED": "CAPTURED"}


def _run(api, log, clock, ops):
    replies = {}  # (actor, key) -> first definitive cancel reply
    for op in ops:
        if op[0] == "wait":
            clock.run_until(clock.now() + op[1])
        elif op[0] == "cancel":
            _, actor, uid, key = op
            r = api.cancel(actor, uid, key)
            if r.result in EXPECTED_EFFECT:
                # A definitive reply is true of *this* payment: its evidence is this uid's effect.
                ev = log()[r.effect_seq]
                assert (ev["uid"], ev["type"]) == (uid, EXPECTED_EFFECT[r.result])
            if r.result is not None and r.result != "DENIED":
                first = replies.setdefault((actor, key), (uid, r))
                if first[0] == uid:
                    # (d) a replay returns the stored answer and evidence, never a new effect
                    assert (r.result, r.effect_seq) == (first[1].result, first[1].effect_seq)
        else:
            api.status(op[1], op[2])
    clock.run_until(clock.now() + 20)


def _check(log):
    for uid in UIDS:
        eff = effects(log, uid)
        assert len(eff) == 1                              # (a) exactly one effect by horizon
        assert {e["type"] for e in eff} != {"CANCELLED", "CAPTURED"}  # (b)
    assert all(e["actor"] == REMEDIATION_OPERATOR for e in log if e["type"] == "CANCELLED")
    keyed = [e["key"] for e in log if e["type"] == "CANCELLED"]
    assert len(keyed) == len(set(keyed))                  # (d) one effect per key at most
    assert chain_ok(log)                                  # (c)


@SETTINGS
@given(payments_st, st.lists(op_st, max_size=25))
def test_sink_invariants_direct(pays, ops):
    s, clock = sink(*pays)
    _run(s, lambda: s.log, clock, ops)
    _check(s.log)


@SETTINGS
@given(payments_st, st.lists(op_st, max_size=25), fault_st, st.integers(0, 2**31))
def test_sink_invariants_through_faulty_transport(pays, ops, cfg, seed):
    s, clock = sink(*pays)
    tr = FaultyTransport(s, clock, cfg, seed, cancel_latency={p.uid: p.s_cancel for p in pays},
                         status_latency=1)
    _run(tr, lambda: s.log, clock, ops)
    _check(s.log)
