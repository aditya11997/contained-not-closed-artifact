"""PH8 (post hoc, descriptive; docs/POSTHOC.md): the shared k > 1 scheduler of P3-P6 against P2's
weighted Moore-Hodgson, per primary cell. Reads a primary run directory read-only (trials.csv).

    python -m analysis.posthoc.scheduler_k --results results/holdout/primary --out paper/generated/posthoc

Writes scheduler_k.csv: per cell and contrast (mh - lm_ack, mh - lm_reconcile), the mean paired
per-seed loss difference, its unadjusted 95% percentile bootstrap CI and the win/tie/loss counts.
With fault = none, mh - lm_ack is the scheduler alone (both trust ACKs). No decision rule.
"""
import argparse
from pathlib import Path

import pandas as pd

from analysis.stats import (
    bootstrap_ci,
    cells,
    load_trials,
    paired_differences,
    win_tie_loss,
)

CONTRASTS = (("mh", "lm_ack"), ("mh", "lm_reconcile"))


def scheduler_table(trials: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for cell in cells(trials).itertuples(index=False):
        for a, b in CONTRASTS:
            d = paired_differences(trials, cell.cell_id, a, b)
            r = bootstrap_ci(d.to_numpy(), label=f"posthoc|{cell.cell_id}|{a}-{b}")
            better, ties, worse = win_tie_loss(d)
            rows.append({**cell._asdict(), "contrast": f"{a}-{b}", "n_seeds": len(d),
                         "mean_diff": r["mean"], "ci_lo": r["lo"], "ci_hi": r["hi"],
                         "a_better": better, "ties": ties, "a_worse": worse})
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m analysis.posthoc.scheduler_k", description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=Path, required=True, help="a primary run directory (read only)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    table = scheduler_table(load_trials(args.results))
    args.out.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out / "scheduler_k.csv", index=False)
    pd.set_option("display.width", 250)
    print(table.drop(columns="cell_id").round(1).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
