"""PH3 (post hoc, exploratory; docs/POSTHOC.md): timeout-multiplier sweep, P3/P4/P5 on the
primary grid, dev seeds, one run per timeout factor m.

    python -m analysis.posthoc.timeout_sweep --runs results/dev/posthoc-timeout-m1 \
        results/dev/posthoc-timeout-m2 results/dev/posthoc-timeout-m4 --out paper/generated/posthoc

Writes:
- timeout_contrasts.csv: P4 - P3 and P5 - P3 per cell at each m (unadjusted 95% CIs).
- timeout_interaction.csv: per cell, the per-seed (a - b)@m - (a - b)@1 for m > 1, with its CI.
- timeout_false_closures.csv: per m, fault level and policy.
- timeout_policy_means.csv: per m, cell and policy, mean loss, suspect captured, legit
  cancelled and calls, to see which policy's loss moves with m.
It also checks that the faultless cells are identical across m (no timeout can happen there)
and fails if they are not.
"""
import argparse
from pathlib import Path

import pandas as pd

from analysis.stats import (
    bootstrap_ci,
    contrast_table,
    paired_differences,
    win_tie_loss,
)

from .common import CELL_COLUMNS, false_closures, load_dev_run

P3, P4, P5 = "lm_ack", "lm_verify", "lm_reconcile"
CONTRASTS = ((P4, P3), (P5, P3))
OUTCOMES = ["loss", "suspect_captured", "legit_cancelled", "false_closure", "closed_cancelled",
            "n_cancel_calls", "n_status_calls", "n_replays"]


def load(runs) -> dict[int, pd.DataFrame]:
    out = {}
    for run in runs:
        trials, manifest = load_dev_run(run)
        m = int(manifest.get("timeout_factor", 1))
        if m in out:
            raise ValueError(f"two runs with timeout_factor {m}")
        out[m] = trials
    if 1 not in out:
        raise ValueError("the sweep needs the m = 1 run as its reference")
    seeds = {m: tuple(sorted(t["seed"].unique())) for m, t in out.items()}
    if len(set(seeds.values())) != 1:
        raise ValueError("the runs do not cover the same seeds")
    return dict(sorted(out.items()))


def faultless_identical(by_m) -> int:
    """Number of faultless (cell, seed, policy) rows that differ from m = 1 on any outcome."""
    key = ["cell_id", "seed", "policy"]
    ref = by_m[1][by_m[1]["fault"] == "none"].set_index(key)[OUTCOMES].sort_index()
    bad = 0
    for t in by_m.values():
        other = t[t["fault"] == "none"].set_index(key)[OUTCOMES].sort_index()
        bad += int((other != ref).any(axis=1).sum())
    return bad


def interaction(by_m) -> pd.DataFrame:
    rows = []
    ref = by_m[1]
    cells = ref.drop_duplicates("cell_id")[list(CELL_COLUMNS)]
    for m, t in by_m.items():
        if m == 1:
            continue
        for cell in cells.itertuples(index=False):
            for a, b in CONTRASTS:
                d = (paired_differences(t, cell.cell_id, a, b)
                     - paired_differences(ref, cell.cell_id, a, b))
                r = bootstrap_ci(d.to_numpy(), label=f"posthoc-timeout|{cell.cell_id}|{a}-{b}|m{m}-m1")
                w, ties, lo = win_tie_loss(d)
                rows.append({"m": m, **cell._asdict(), "contrast": f"{a}-{b}", "n_seeds": len(d),
                             "mean_change": r["mean"], "ci_lo": r["lo"], "ci_hi": r["hi"],
                             "lower_at_m": w, "ties": ties, "higher_at_m": lo})
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m analysis.posthoc.timeout_sweep",
                                 description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=Path, nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    by_m = load(args.runs)
    bad = faultless_identical(by_m)
    if bad:
        raise SystemExit(f"{bad} faultless rows differ across m: an implementation bug (POSTHOC.md PH3)")
    args.out.mkdir(parents=True, exist_ok=True)
    cols = ["cell_id", "fault", "slack", "k", "n", "contrast", "n_seeds", "mean_diff", "ci_lo", "ci_hi",
            "a_better", "ties", "a_worse"]
    contrasts = pd.concat([contrast_table(t, CONTRASTS, holm_family=False)[cols].assign(m=m)
                           for m, t in by_m.items()], ignore_index=True)
    contrasts = contrasts[["m", *cols]]
    contrasts.to_csv(args.out / "timeout_contrasts.csv", index=False)
    inter = interaction(by_m)
    inter.to_csv(args.out / "timeout_interaction.csv", index=False)
    fc = pd.concat([false_closures(t, (P3, P4, P5)).assign(m=m) for m, t in by_m.items()], ignore_index=True)
    fc.to_csv(args.out / "timeout_false_closures.csv", index=False)
    means = pd.concat([t.groupby(list(CELL_COLUMNS) + ["policy"], sort=False)[
        ["loss", "suspect_captured", "legit_cancelled", "n_cancel_calls", "n_status_calls"]].mean()
        .reset_index().assign(m=m) for m, t in by_m.items()], ignore_index=True)
    means.to_csv(args.out / "timeout_policy_means.csv", index=False)

    seeds = by_m[1]["seed"]
    print(f"EXPLORATORY (dev seeds {seeds.min()}-{seeds.max()}); faultless rows identical across m: yes")
    faulty = contrasts[contrasts["fault"] != "none"]
    for contrast in (f"{a}-{b}" for a, b in CONTRASTS):
        print(f"\n{contrast}: mean loss difference [95% CI] by m")
        sub = faulty[faulty["contrast"] == contrast]
        for (fault, slack, k, n), g in sub.groupby(["fault", "slack", "k", "n"], sort=False):
            cells = [f"m={r.m}: {r.mean_diff:7.1f} [{r.ci_lo:7.1f}, {r.ci_hi:7.1f}]" for r in g.itertuples()]
            print(f"{fault}/{slack}/k{k}/n{n}".ljust(18), " | ".join(cells))
    print()
    print(fc.round(4).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
