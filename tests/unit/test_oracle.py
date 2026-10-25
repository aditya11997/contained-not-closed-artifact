import pytest
from sim_helpers import payment, sink

from p4.sim.oracle import read_log, score
from p4.sim.protocol import ORIGINAL_AGENT, REMEDIATION_OPERATOR

OP = REMEDIATION_OPERATOR


def _run(tmp_path):
    s, clock = sink(
        payment("S1", D=5, amount=1000, y=True),   # suspect, cancelled
        payment("S2", D=5, amount=300, y=True),    # suspect, captured
        payment("L1", D=5, amount=200, y=False),   # legit, cancelled
        payment("L2", D=5, amount=700, y=False),   # legit, captured
    )
    s.cancel(OP, "S1", "k1")
    s.cancel(OP, "L1", "k2")
    s.cancel(ORIGINAL_AGENT, "S2", "k3")
    s.status(OP, "S2")
    clock.run_until(s.scenario.horizon)
    path = tmp_path / "log.jsonl"
    s.export_log(path)
    return s, path


def test_scores_from_exported_log(tmp_path):
    s, path = _run(tmp_path)
    r = score(path, s.scenario, c=0.5)
    assert r["suspect_captured_value"] == 300
    assert r["legitimate_cancelled_value"] == 200
    assert r["loss"] == 300 + 0.5 * 200
    assert r["cancelled_count"] == 2 and r["captured_count"] == 2
    assert r["cancel_calls"] == 3 and r["status_calls"] == 1
    assert r["denied_calls"] == 1 and r["revoked_actor_effects"] == 0
    assert score(read_log(path), s.scenario, c=0.5) == r


def test_revoked_actor_effect_is_counted(tmp_path):
    s, path = _run(tmp_path)
    entries = read_log(path)
    forged = dict(entries[-1], seq=len(entries), type="CANCELLED", uid="S2", actor=ORIGINAL_AGENT)
    # The oracle scores outcomes; chain verification is the checker's job (WP3).
    r = score(entries + [forged], s.scenario, c=0.5)
    assert r["revoked_actor_effects"] == 1


def test_refuses_log_that_has_not_reached_horizon():
    s, clock = sink(payment("A", D=5))
    clock.run_until(4)
    with pytest.raises(ValueError):
        score(s.log, s.scenario, c=0.5)
