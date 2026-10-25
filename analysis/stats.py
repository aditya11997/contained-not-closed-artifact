"""Statistics over trials.csv (WP4; CODE_AUDIT C3, C4).

- Paired per-seed differences a - b of a metric within a cell (same seeds, same scenario).
- Percentile bootstrap of the mean difference (N_BOOT resamples). Each contrast has its own
  generator, seeded from BOOT_SEED and a stable hash of the contrast label, so results do not
  depend on the order contrasts are computed in.
- p-value by inverting the percentile bootstrap: p = min(1, 2 * min(#{b* <= 0} + 1,
  #{b* >= 0} + 1) / (B + 1)). A percentile CI at level 1 - alpha excludes 0 iff p <= alpha
  (up to the +1 smoothing, which keeps p > 0). All-zero differences give p = 1.
- Holm-Bonferroni step-down over a contrast family (adjusted p-values), and, for each
  contrast, the percentile CI at its Holm level 1 - alpha / (m - rank + 1).
- Post hoc, reporting only (docs/POSTHOC.md PH6): the percentile CI at the Bonferroni level
  1 - alpha / m for every contrast of the family (`bonf_ci_lo`, `bonf_ci_hi`). It is a
  simultaneous interval and does not depend on how tied p-values are ranked. No verdict uses it.
- Win/tie/loss counts; Wilson score intervals for rates; exact false-closure counts.
"""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

N_BOOT = 10_000
BOOT_SEED = 20261027
ALPHA = 0.05
Z95 = 1.959963984540054

# Pre-registered primary contrasts (a - b; negative = a has lower loss), the Holm family.
# PREREG_DRAFT §3; {P5-P3, P4-P3} decided on 2026-10-22. P6-P5 (H3) is pre-registered but
# secondary (dev showed P6 ~= P5): reported over the primary grid without Holm adjustment.
PRIMARY_CONTRASTS = (("lm_reconcile", "lm_ack"), ("lm_verify", "lm_ack"))
SECONDARY_PRIMARY_GRID_CONTRASTS = (("adaptive", "lm_reconcile"),)
EVIDENCE_BOUNDED = ("lm_verify", "lm_reconcile", "adaptive")
NEGATIVE_CONTROL_POLICIES = ("lm_ack", "lm_verify", "lm_reconcile", "adaptive")
FACTOR_COLUMNS = ("fault", "slack", "k", "n", "score", "jitter", "c", "late_cancel")


def load_trials(path) -> pd.DataFrame:
    path = Path(path)
    if path.is_dir():
        path = path / "trials.csv"
    return pd.read_csv(path, dtype={"k": str, "cell_id": str}, keep_default_na=False,
                       na_values={"time_to_full_disposition": [""]})


def stable_hash(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big") >> 1


def contrast_rng(label: str) -> np.random.Generator:
    return np.random.default_rng([BOOT_SEED, stable_hash(label)])


# -- paired differences -----------------------------------------------------------------------
def paired_differences(trials: pd.DataFrame, cell_id: str, a: str, b: str,
                       metric: str = "loss") -> pd.Series:
    """a - b per seed in one cell. Both policies must cover the same seeds exactly once."""
    cell = trials[trials["cell_id"] == cell_id]
    sa = cell[cell["policy"] == a].set_index("seed")[metric]
    sb = cell[cell["policy"] == b].set_index("seed")[metric]
    if sa.empty or sb.empty:
        raise ValueError(f"cell {cell_id}: no rows for {a!r} or {b!r}")
    if not sa.index.is_unique or not sb.index.is_unique or set(sa.index) != set(sb.index):
        raise ValueError(f"cell {cell_id}: {a} and {b} do not cover the same seeds once each")
    return (sa - sb.loc[sa.index]).sort_index()


def paired_across(left: pd.DataFrame, right: pd.DataFrame, policy: str, metric: str = "loss") -> pd.Series:
    """left - right per seed for one policy, rows already restricted to one cell each."""
    sa = left[left["policy"] == policy].set_index("seed")[metric]
    sb = right[right["policy"] == policy].set_index("seed")[metric]
    if sa.empty or set(sa.index) != set(sb.index) or not sa.index.is_unique or not sb.index.is_unique:
        raise ValueError(f"{policy}: the two cells do not cover the same seeds once each")
    return (sa - sb.loc[sa.index]).sort_index()


# -- bootstrap ----------------------------------------------------------------------------------
def bootstrap_means(x, n_boot: int, rng: np.random.Generator, chunk: int = 1000) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n == 0:
        raise ValueError("bootstrap of an empty sample")
    out = np.empty(n_boot)
    for start in range(0, n_boot, chunk):
        m = min(chunk, n_boot - start)
        out[start:start + m] = x[rng.integers(0, n, size=(m, n))].mean(axis=1)
    return out


def percentile_ci(boot: np.ndarray, alpha: float = ALPHA) -> tuple[float, float]:
    lo, hi = np.quantile(boot, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def bootstrap_p(boot: np.ndarray) -> float:
    b = len(boot)
    le, ge = int(np.sum(boot <= 0)), int(np.sum(boot >= 0))
    return min(1.0, 2 * min(le + 1, ge + 1) / (b + 1))


def bootstrap_ci(x, n_boot: int = N_BOOT, alpha: float = ALPHA, label: str = "") -> dict:
    boot = bootstrap_means(x, n_boot, contrast_rng(label))
    lo, hi = percentile_ci(boot, alpha)
    return {"mean": float(np.mean(x)), "lo": lo, "hi": hi, "p": bootstrap_p(boot), "boot": boot}


# -- multiplicity, counts, rates --------------------------------------------------------------
def holm(pvals) -> np.ndarray:
    """Holm-Bonferroni adjusted p-values (step-down, monotone, capped at 1)."""
    p = np.asarray(pvals, dtype=float)
    m = len(p)
    order = np.argsort(p, kind="stable")
    adj = np.empty(m)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p[i]))
        adj[i] = running
    return adj


def holm_levels(pvals, alpha: float = ALPHA) -> np.ndarray:
    """Per-contrast Holm significance level alpha / (m - rank + 1), rank by ascending p."""
    p = np.asarray(pvals, dtype=float)
    m = len(p)
    levels = np.empty(m)
    for rank, i in enumerate(np.argsort(p, kind="stable")):
        levels[i] = alpha / (m - rank)
    return levels


def win_tie_loss(d) -> tuple[int, int, int]:
    """(a better, tie, a worse) for differences a - b of a cost (lower is better)."""
    d = np.asarray(d)
    return int(np.sum(d < 0)), int(np.sum(d == 0)), int(np.sum(d > 0))


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n <= 0:
        return (float("nan"), float("nan"))
    phat = k / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * np.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, float(centre - half)), min(1.0, float(centre + half)))


# -- tables ---------------------------------------------------------------------------------------
def cells(trials: pd.DataFrame) -> pd.DataFrame:
    """One row per cell, in first-appearance (config) order."""
    return trials.drop_duplicates("cell_id")[["cell_id", *FACTOR_COLUMNS]].reset_index(drop=True)


def contrast_table(trials: pd.DataFrame, contrasts=PRIMARY_CONTRASTS, metric: str = "loss",
                   alpha: float = ALPHA, n_boot: int = N_BOOT, holm_family: bool = True) -> pd.DataFrame:
    """One row per (cell, contrast): mean a - b, percentile CI, p, W/T/L, and (if `holm_family`)
    Holm-adjusted p, rejection, the CI at the contrast's Holm level and (post hoc, reporting
    only) the CI at the Bonferroni level alpha / m."""
    rows, boots = [], []
    for cell in cells(trials).itertuples(index=False):
        for a, b in contrasts:
            d = paired_differences(trials, cell.cell_id, a, b, metric)
            r = bootstrap_ci(d.to_numpy(), n_boot, alpha, label=f"{cell.cell_id}|{a}-{b}|{metric}")
            w, t, lo_ = win_tie_loss(d)
            rows.append({**cell._asdict(), "contrast": f"{a}-{b}", "a": a, "b": b, "metric": metric,
                         "n_seeds": len(d), "mean_diff": r["mean"], "ci_lo": r["lo"], "ci_hi": r["hi"],
                         "p": r["p"], "a_better": w, "ties": t, "a_worse": lo_})
            boots.append(r["boot"])
    table = pd.DataFrame(rows)
    if holm_family and len(table):
        table["p_holm"] = holm(table["p"])
        table["holm_reject"] = table["p_holm"] <= alpha
        levels = holm_levels(table["p"], alpha)
        cis = [percentile_ci(bt, lv) for bt, lv in zip(boots, levels)]
        table["holm_level"] = levels
        table["holm_ci_lo"] = [c[0] for c in cis]
        table["holm_ci_hi"] = [c[1] for c in cis]
        bonf = [percentile_ci(bt, alpha / len(table)) for bt in boots]
        table["bonf_ci_lo"] = [c[0] for c in bonf]
        table["bonf_ci_hi"] = [c[1] for c in bonf]
    return table


def false_closure_table(trials: pd.DataFrame) -> pd.DataFrame:
    """Exact false-closure counts per (cell, policy), with Wilson intervals per run (any false
    closure) and per payment."""
    rows = []
    for (cell_id, policy), g in trials.groupby(["cell_id", "policy"], sort=False):
        runs, fc = len(g), int(g["false_closure"].sum())
        runs_fc = int((g["false_closure"] > 0).sum())
        payments = int(g["n"].astype(int).sum())
        rlo, rhi = wilson(runs_fc, runs)
        plo, phi = wilson(fc, payments)
        rows.append({"cell_id": cell_id, **{f: g[f].iloc[0] for f in FACTOR_COLUMNS}, "policy": policy,
                     "runs": runs, "false_closures": fc, "runs_with_fc": runs_fc,
                     "run_rate": runs_fc / runs, "run_rate_lo": rlo, "run_rate_hi": rhi,
                     "payments": payments, "payment_rate": fc / payments,
                     "payment_rate_lo": plo, "payment_rate_hi": phi,
                     "mean_loss": float(g["loss"].mean())})
    return pd.DataFrame(rows)


def negative_control_table(trials: pd.DataFrame, policies=NEGATIVE_CONTROL_POLICIES) -> pd.DataFrame:
    """Per cell: seeds on which every policy in `policies` has the same loss, and the largest
    |difference| from the first policy."""
    rows = []
    for cell in cells(trials).itertuples(index=False):
        g = trials[(trials["cell_id"] == cell.cell_id) & trials["policy"].isin(policies)]
        wide = g.pivot(index="seed", columns="policy", values="loss")[list(policies)]
        diffs = wide.sub(wide[policies[0]], axis=0)
        rows.append({**cell._asdict(), "seeds": len(wide),
                     "seeds_all_equal": int((diffs.abs().max(axis=1) == 0).sum()),
                     "max_abs_diff": float(diffs.abs().to_numpy().max())})
    return pd.DataFrame(rows)
