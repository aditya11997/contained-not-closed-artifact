"""analysis.stats: bootstrap CI on a known distribution, Holm on the textbook example, Wilson
intervals, paired differences."""
import numpy as np
import pandas as pd
import pytest

from analysis import stats


def test_holm_textbook_example():
    # Holm (1979)-style example: sorted p 0.005, 0.01, 0.03, 0.04 with m = 4.
    p = [0.01, 0.04, 0.03, 0.005]
    assert stats.holm(p) == pytest.approx([0.03, 0.06, 0.06, 0.02])
    assert list(stats.holm(p) <= 0.05) == [True, False, False, True]
    assert stats.holm_levels(p) == pytest.approx([0.05 / 3, 0.05 / 1, 0.05 / 2, 0.05 / 4])


def test_holm_caps_at_one_and_is_monotone():
    adj = stats.holm([0.5, 0.6, 0.001])
    assert adj == pytest.approx([1.0, 1.0, 0.003])


def test_bootstrap_ci_matches_normal_theory():
    rng = np.random.default_rng(100001)
    x = rng.normal(1.0, 2.0, size=4000)
    r = stats.bootstrap_ci(x, n_boot=10_000, label="normal")
    half = stats.Z95 * x.std(ddof=1) / np.sqrt(len(x))
    assert r["mean"] == pytest.approx(x.mean())
    assert r["lo"] == pytest.approx(x.mean() - half, abs=0.1 * half)
    assert r["hi"] == pytest.approx(x.mean() + half, abs=0.1 * half)
    assert r["p"] < 1e-3


def test_bootstrap_is_seeded_by_label():
    x = np.arange(50.0)
    a = stats.bootstrap_ci(x, n_boot=500, label="a")
    assert stats.bootstrap_ci(x, n_boot=500, label="a")["lo"] == a["lo"]
    assert not np.array_equal(stats.bootstrap_ci(x, n_boot=500, label="b")["boot"], a["boot"])


def test_bootstrap_p_all_zero_and_one_sided():
    assert stats.bootstrap_ci(np.zeros(100), n_boot=1000)["p"] == 1.0
    p = stats.bootstrap_ci(np.full(100, -3.0), n_boot=999)["p"]
    assert p == pytest.approx(2 / 1000)


@pytest.mark.slow
def test_bootstrap_coverage():
    rng = np.random.default_rng(100002)
    hits = 0
    reps = 400
    for i in range(reps):
        x = rng.exponential(1.0, size=200)   # skewed, true mean 1
        r = stats.bootstrap_ci(x, n_boot=2000, label=f"cov{i}")
        hits += r["lo"] <= 1.0 <= r["hi"]
    assert 0.91 <= hits / reps <= 0.98


def test_wilson_known_values():
    assert stats.wilson(5, 10) == pytest.approx((0.2366, 0.7634), abs=1e-4)
    assert stats.wilson(0, 10) == pytest.approx((0.0, 0.2775), abs=1e-4)
    assert stats.wilson(10, 10)[1] == pytest.approx(1.0)
    assert all(np.isnan(stats.wilson(0, 0)))


def test_win_tie_loss():
    assert stats.win_tie_loss([-1, 0, 0, 2, -3]) == (2, 2, 1)


def _trials():
    rows = []
    for seed in (1, 2, 3):
        for pol, loss in (("lm_ack", 10.0 * seed), ("lm_reconcile", 10.0 * seed + 1)):
            rows.append({"cell_id": "c", "seed": seed, "policy": pol, "loss": loss})
    return pd.DataFrame(rows)


def test_paired_differences():
    d = stats.paired_differences(_trials(), "c", "lm_ack", "lm_reconcile")
    assert list(d) == [-1.0, -1.0, -1.0]


def test_paired_differences_requires_matching_seeds():
    t = _trials()
    t = t[~((t["policy"] == "lm_ack") & (t["seed"] == 3))]
    with pytest.raises(ValueError, match="same seeds"):
        stats.paired_differences(t, "c", "lm_ack", "lm_reconcile")


def _grid_trials(n_cells=3, n_seeds=60):
    rng = np.random.default_rng(100002)
    rows = []
    for c in range(n_cells):
        factors = {"fault": "low", "slack": "tight", "k": "1", "n": 8, "score": "calibrated",
                   "jitter": "uniform3", "c": 0.5, "late_cancel": True}
        for seed in range(n_seeds):
            base = rng.normal(100, 20)
            for pol, shift in (("lm_ack", 0.0), ("lm_verify", 5.0 * c), ("lm_reconcile", -3.0)):
                rows.append({"cell_id": f"c{c}", **factors, "seed": seed, "policy": pol,
                             "loss": base + shift + rng.normal(0, 4)})
    return pd.DataFrame(rows)


def test_bonferroni_ci_columns_post_hoc():
    """docs/POSTHOC.md PH6: the CI at alpha / m is reported beside the Holm-level CI. It is never
    narrower than the Holm-level CI and is absent outside a Holm family."""
    t = stats.contrast_table(_grid_trials(), n_boot=2000)
    m = len(t)
    assert m == 6
    assert (t["bonf_ci_lo"] <= t["holm_ci_lo"] + 1e-12).all()
    assert (t["bonf_ci_hi"] >= t["holm_ci_hi"] - 1e-12).all()
    assert (t["bonf_ci_lo"] <= t["ci_lo"]).all() and (t["bonf_ci_hi"] >= t["ci_hi"]).all()
    r = t.iloc[0]
    d = stats.paired_differences(_grid_trials(), r.cell_id, r.a, r.b).to_numpy()
    boot = stats.bootstrap_ci(d, 2000, label=f"{r.cell_id}|{r.a}-{r.b}|loss")["boot"]
    assert (r.bonf_ci_lo, r.bonf_ci_hi) == stats.percentile_ci(boot, stats.ALPHA / m)
    plain = stats.contrast_table(_grid_trials(), n_boot=200, holm_family=False)
    assert "bonf_ci_lo" not in plain and "holm_ci_lo" not in plain
