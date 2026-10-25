"""experiments/posthoc_multicrash.py (docs/POSTHOC.md PH7): the two-crash hook fires twice in
the restarted instance, and it reaches the frozen P3's known double-crash failure, so a clean
P4-P6 result there is not for lack of opportunity."""
from experiments import posthoc_multicrash as mc


def test_next_point_is_cyclic():
    assert [mc.next_point(p) for p in mc.CRASH_POINTS] == list(mc.CRASH_POINTS[1:] + mc.CRASH_POINTS[:1])


def _rows(tmp_path, variant, policy, point="after_send", target="first_cancel", seeds=range(100000, 100006)):
    return [mc.run_one(variant, policy, point, target, s, f, k, tmp_path / f"{variant}{policy}{s}{f}{k}.jsonl")
            for s in seeds for f in mc.cm.FAULTS for k in mc.cm.KS]


def test_same_variant_crashes_twice_on_one_payment(tmp_path):
    rows = _rows(tmp_path, "same", "lm_verify")
    twice = [r for r in rows if r["crashes"] == 2]
    assert twice
    for r in twice:
        first, second = eval(r["first_crash"]), eval(r["second_crash"])
        assert first[2] == second[2] and first[0] == second[0] == "after_send"
        assert r["restarts"] == 2 and not mc.failed(r)


def test_different_variant_uses_the_next_point(tmp_path):
    rows = _rows(tmp_path, "different", "lm_reconcile")
    twice = [r for r in rows if r["crashes"] == 2]
    assert twice
    assert all(eval(r["second_crash"])[0] == "after_reply" for r in twice)


def test_hook_reaches_the_frozen_p3_double_crash_failure(tmp_path):
    rows = _rows(tmp_path, "same", "lm_ack")
    assert any(mc.failed(r) and "second call in flight" in r["error"] for r in rows)
