"""PH2 (post hoc, exploratory; docs/POSTHOC.md): P3 vs P3b (at-least-once ack-trust) vs P5 on
the primary grid, dev seeds.

    python -m analysis.posthoc.p3b --run results/dev/posthoc-p3b --out paper/generated/posthoc

Writes p3b_contrasts.csv (P3b - P3 and P5 - P3b as planned, P5 - P3 for reference; unadjusted
95% percentile bootstrap CIs, W/T/L) and p3b_false_closures.csv (per fault level and policy).
"""
import argparse
from pathlib import Path

import pandas as pd

from analysis.stats import contrast_table

from .common import false_closures, load_dev_run

P3, P3B, P5 = "lm_ack", "lm_ack_retry", "lm_reconcile"
CONTRASTS = ((P3B, P3), (P5, P3B), (P5, P3))
COLUMNS = ["cell_id", "fault", "slack", "k", "n", "contrast", "n_seeds", "mean_diff", "ci_lo", "ci_hi",
           "a_better", "ties", "a_worse"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m analysis.posthoc.p3b", description=__doc__.splitlines()[0])
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    trials, manifest = load_dev_run(args.run)
    args.out.mkdir(parents=True, exist_ok=True)
    table = contrast_table(trials, CONTRASTS, holm_family=False)[COLUMNS]
    table.insert(0, "seeds", f"{manifest['seeds']['first']}-{manifest['seeds']['last']}")
    table.to_csv(args.out / "p3b_contrasts.csv", index=False)
    fc = false_closures(trials, (P3, P3B, P5))
    fc.to_csv(args.out / "p3b_false_closures.csv", index=False)
    pd.set_option("display.width", 250)
    wide = table.pivot_table(index=["fault", "slack", "k", "n"], columns="contrast",
                             values=["mean_diff", "ci_lo", "ci_hi"], sort=False)
    print(f"EXPLORATORY (dev seeds {table['seeds'].iloc[0]}): mean loss difference [95% CI]")
    for idx, r in wide.iterrows():
        cells = [f"{c}: {r[('mean_diff', c)]:8.1f} [{r[('ci_lo', c)]:7.1f}, {r[('ci_hi', c)]:7.1f}]"
                 for c in (f"{a}-{b}" for a, b in CONTRASTS)]
        print("/".join(map(str, idx)).ljust(18), " | ".join(cells))
    print()
    print(fc.round(4).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
