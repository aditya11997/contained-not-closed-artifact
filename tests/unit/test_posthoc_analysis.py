"""analysis/posthoc/p4_vs_p3.py (docs/POSTHOC.md PH5): the per-payment pieces of the attribution."""
from analysis.posthoc.p4_vs_p3 import (
    classify,
    false_closed,
    first_effects,
    payment_loss,
)

PAY = {"uid": "A", "amount": 1000, "D_actual": 10, "d_nominal": 9, "y_suspect": True}
LEGIT = dict(PAY, uid="B", y_suspect=False)


def ev(uid, typ, t):
    return {"uid": uid, "type": typ, "t": t}


def test_payment_loss_matches_the_loss_definition():
    assert payment_loss(PAY, ev("A", "CAPTURED", 10), 0.5) == 1000
    assert payment_loss(PAY, ev("A", "CANCELLED", 4), 0.5) == 0
    assert payment_loss(LEGIT, ev("B", "CANCELLED", 4), 0.5) == 500
    assert payment_loss(LEGIT, ev("B", "CAPTURED", 10), 0.5) == 0


def test_false_closed_uses_the_log_final_state():
    truth = {"payments": [PAY, LEGIT]}
    report = {"entries": [{"uid": "A", "state": "CLOSED_CANCELLED"}, {"uid": "B", "state": "CLOSED_CANCELLED"}]}
    effects = first_effects([ev("A", "CAPTURED", 10), ev("B", "CANCELLED", 3), ev("A", "CANCELLED", 11)])
    assert false_closed(report, truth, effects) == ["A"]


def test_classify():
    resend = [{"kind": "intent", "uid": "A", "op": "cancel"}] * 2
    once = resend[:1]
    assert classify("A", PAY, ev("A", "CANCELLED", 5), resend) == "recovered_resend"
    assert classify("A", PAY, ev("A", "CANCELLED", 5), once) == "recovered_first_send"
    assert classify("A", PAY, ev("A", "CANCELLED", 9), resend) == "recovered_late"
    assert classify("A", PAY, ev("A", "CAPTURED", 10), resend) == "lost_both"
    assert classify("A", PAY, None, resend) == "lost_both"


def _run_dir(tmp_path, **manifest):
    import json
    d = tmp_path / "run"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"partition": "dev", "checker": {"not_ok": 0}, **manifest}))
    (d / "trials.csv").write_text("cell_id,k,seed,policy,loss\nc,1,1,lm_ack,0\n")
    return d


def test_posthoc_loader_is_dev_only(tmp_path):
    import pytest

    from analysis.posthoc.common import load_dev_run
    assert len(load_dev_run(_run_dir(tmp_path))[0]) == 1
    with pytest.raises(ValueError, match="dev only"):
        load_dev_run(_run_dir(tmp_path / "h", partition="holdout"))
    with pytest.raises(ValueError, match="not-ok"):
        load_dev_run(_run_dir(tmp_path / "n", checker={"not_ok": 2}))


def test_scheduler_k_pairs_seeds_within_a_cell():
    """analysis/posthoc/scheduler_k.py (docs/POSTHOC.md PH8): paired mh - b per seed, per cell."""
    import pandas as pd

    from analysis.posthoc.scheduler_k import scheduler_table

    factors = {"fault": "none", "slack": "tight", "k": "4", "n": 32, "score": "calibrated",
               "jitter": "uniform3", "c": 0.5, "late_cancel": True}
    loss = {"mh": [10, 20, 30], "lm_ack": [15, 20, 25], "lm_reconcile": [15, 25, 30]}
    trials = pd.DataFrame([{"cell_id": "c1", **factors, "seed": s, "policy": p, "loss": x}
                           for p, xs in loss.items() for s, x in enumerate(xs)])
    t = scheduler_table(trials).set_index("contrast")
    assert t.loc["mh-lm_ack", ["mean_diff", "a_better", "ties", "a_worse"]].tolist() == [0, 1, 1, 1]
    assert t.loc["mh-lm_reconcile", ["a_better", "ties", "a_worse"]].tolist() == [2, 1, 0]
    assert (t["n_seeds"] == 3).all()
