"""WP3 crash matrix: kill the remediator at each crash point (a-e, plus the accounting sweep
and its late cancels), restart from the durable journal on the same sink, finish, and grade
with the independent checker. Every run: no duplicate effect and a clean log; P4-P6: zero
false closures. P3's false closures are recorded by scripts/crash_matrix_summary.py."""
import pytest
from crash_matrix import (
    ASSURED,
    CELLS,
    POLICIES,
    applies,
    cell_label,
    run_cell,
    run_matrix,
)

from p4 import schema

N_SEEDS = 12
# Minimum crashed runs per (cell, policy) out of N_SEEDS x 3 faults x 2 k = 72, so no cell
# passes vacuously. Outages and late cancels are reached by fewer runs.
MIN_CRASHED = {"e": 10, "late_cancel": 8}
MATRIX = [pytest.param(policy, point, target, id=f"{cell_label(point, target)}-{policy}")
          for point, target in CELLS for policy in POLICIES if applies(policy, target)]


def _min_crashed(label: str) -> int:
    return next((v for k, v in MIN_CRASHED.items() if label.startswith(k)), 60)


@pytest.mark.parametrize("policy,point,target", MATRIX)
def test_crash_matrix_cell(tmp_path, policy, point, target):
    runs = list(run_matrix(tmp_path, N_SEEDS, policies=(policy,), cells=[(point, target)]))
    crashed = [r for r in runs if r.fired is not None]
    assert len(crashed) >= _min_crashed(cell_label(point, target)), len(crashed)
    for r in runs:
        where = (r.seed, r.fault, r.k, r.fired)
        assert r.restarts == (r.fired is not None), where
        assert r.report_errors == (), where
        c = r.verdict["counts"]
        assert all(c[name] == 0 for name in schema.INTEGRITY_COUNTS), (where, r.verdict["problems"])
        assert c["duplicate_effects"] == 0 and c["report_malformed"] == 0, where
        assert c["unsupported_evidence"] == 0, (where, r.verdict["problems"])
        if policy in ASSURED:
            assert c["false_closure"] == 0 and r.verdict["ok"], (where, r.verdict["problems"])


UNKNOWN_CELLS = [(p, "first_cancel") for p in ("before_send", "after_send", "after_reply")]


def test_crashes_leave_unknown_outcomes(tmp_path):
    """Non-vacuity: crashing before the reply is journaled leaves outcomes the restarted
    remediator cannot know. P4-P6 journal them as recovered timeouts and resolve them with
    evidence; the superseded P3 rule (trust them, kept as a fixture) closes some falsely. So
    the assured runbooks' zero above is not for lack of opportunity."""
    assured = list(run_matrix(tmp_path, N_SEEDS, policies=ASSURED, cells=UNKNOWN_CELLS))
    assert sum(r.recovered_results for r in assured) > 0
    old_p3 = list(run_matrix(tmp_path, N_SEEDS, policies=("lm_ack_trust_orphans",),
                             cells=UNKNOWN_CELLS, baseline=True))
    assert sum(r.recovered_results for r in old_p3) > 0
    assert sum(r.verdict["counts"]["false_closure"] > r.baseline_false_closure for r in old_p3) > 0
    assert all(r.verdict["ok"] for r in old_p3)  # false closures are measurements for ack-trust


def test_p3_outbox_recovery_resends_each_unresolved_cancel_once(tmp_path):
    """P3 re-sends an unresolved journaled cancel (same key) instead of trusting it: it never
    journals a recovered timeout, sends at most one extra cancel per payment, and with no
    transport faults (so no timeouts) a crash adds no false closure."""
    runs = list(run_matrix(tmp_path, N_SEEDS, policies=("lm_ack",), cells=UNKNOWN_CELLS, baseline=True))
    crashed = [r for r in runs if r.fired is not None]
    assert crashed and all(r.recovered_results == 0 for r in runs)
    assert all(r.max_cancel_intents <= 2 for r in runs)
    assert any(r.max_cancel_intents == 2 for r in crashed)  # the outbox re-send happened
    for r in crashed:
        if r.fault == "none":
            assert r.verdict["counts"]["false_closure"] == r.baseline_false_closure == 0, r


@pytest.mark.parametrize("policy", POLICIES)
def test_crash_with_restart_delay(tmp_path, policy):
    """Restart is not assumed instant: the remediator is down for 3 time units (calls already
    sent still land, captures still fire) and the invariants still hold."""
    cells = [c for c in CELLS if applies(policy, c[1])]
    runs = list(run_matrix(tmp_path, 6, policies=(policy,), cells=cells, restart_delay=3))
    assert sum(r.fired is not None for r in runs) >= 100
    for r in runs:
        c = r.verdict["counts"]
        assert all(c[name] == 0 for name in schema.INTEGRITY_COUNTS), (r.label, r.seed, r.verdict["problems"])
        assert c["unsupported_evidence"] == 0 and r.report_errors == ()
        if policy in ASSURED:
            assert c["false_closure"] == 0 and r.verdict["ok"], (r.label, r.seed, r.verdict["problems"])


@pytest.mark.parametrize("policy", POLICIES)
def test_crash_with_fsynced_journal(tmp_path, policy):
    """The same restart path with fsync on (the matrix runs it off for speed)."""
    for seed in (100000, 100001, 100002):
        r = run_cell(policy, "after_reply", "first_cancel", seed, "high", 2,
                     tmp_path / f"{policy}-{seed}.jsonl", sync=True)
        assert r.fired is not None and r.restarts == 1
        assert r.verdict["counts"]["duplicate_effects"] == 0
        assert (tmp_path / f"{policy}-{seed}.jsonl.idx").exists()
        if policy in ASSURED:
            assert r.verdict["ok"] and r.verdict["counts"]["false_closure"] == 0
