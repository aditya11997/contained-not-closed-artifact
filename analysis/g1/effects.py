"""G1 item 1: effect sizes (mean paired difference, unadjusted 95% percentile bootstrap CI,
W/T/L) for P5-P3, P6-P5, P6-P3, P4-P3 in every cell, and false closures per policy by fault.

    python -m analysis.g1.effects --results results/dev/primary-500 \
        --out results/dev/primary-500/analysis
"""
import argparse
from pathlib import Path

import pandas as pd

from analysis.stats import contrast_table, wilson

from .common import P_NAMES, ci_str, fmt, load_trials, md_table, write_section

CONTRASTS = (("lm_reconcile", "lm_ack"), ("adaptive", "lm_reconcile"), ("adaptive", "lm_ack"),
             ("lm_verify", "lm_ack"))


def label(a, b) -> str:
    return f"{P_NAMES[a]}−{P_NAMES[b]}"


def effects(trials: pd.DataFrame) -> pd.DataFrame:
    table = contrast_table(trials, CONTRASTS, holm_family=False)
    p3 = trials[trials["policy"] == "lm_ack"].groupby("cell_id")["loss"].mean().rename("p3_mean_loss")
    table = table.join(p3, on="cell_id")
    table["pct_of_p3_loss"] = 100 * table["mean_diff"] / table["p3_mean_loss"]
    table["label"] = [label(a, b) for a, b in zip(table["a"], table["b"])]
    return table


def fc_by_fault(trials: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (fault, policy), g in trials.groupby(["fault", "policy"], sort=False):
        runs, fc = len(g), int(g["false_closure"].sum())
        runs_fc = int((g["false_closure"] > 0).sum())
        payments = int(g["n"].astype(int).sum())
        lo, hi = wilson(fc, payments)
        rlo, rhi = wilson(runs_fc, runs)
        rows.append({"fault": fault, "policy": policy, "runs": runs, "false_closures": fc,
                     "runs_with_fc": runs_fc, "run_rate": runs_fc / runs, "run_rate_lo": rlo,
                     "run_rate_hi": rhi, "payments": payments, "payment_rate": fc / payments,
                     "payment_rate_lo": lo, "payment_rate_hi": hi})
    return pd.DataFrame(rows)


def section(table: pd.DataFrame, fc: pd.DataFrame, trials: pd.DataFrame) -> str:
    n_seeds = trials["seed"].nunique()
    out = ["## 1. Effect sizes (paired loss differences, per cell)", "",
           (f"Mean of per-seed paired differences a − b of loss ({n_seeds} seeds per cell); negative = "
           "a has lower loss. Brackets: **unadjusted** 95% percentile bootstrap CI (B = 10,000; not "
           "Holm-adjusted, exploratory). `*` = CI excludes 0. Full table with W/T/L and % of P3 loss: "
           "`g1_effects.csv`."), ""]
    wide = []
    for cell_id, g in table.groupby("cell_id", sort=False):
        r0 = g.iloc[0]
        row = {"fault": r0["fault"], "slack": r0["slack"], "k": r0["k"], "n": r0["n"],
               "P3 loss": fmt(r0["p3_mean_loss"], 0)}
        for r in g.itertuples():
            row[r.label] = ci_str(r.mean_diff, r.ci_lo, r.ci_hi)
        wide.append(row)
    out += [md_table(pd.DataFrame(wide)), ""]

    wtl = []
    for cell_id, g in table.groupby("cell_id", sort=False):
        r0 = g.iloc[0]
        row = {"cell": f"{r0['fault']}/{r0['slack']}/k{r0['k']}/n{r0['n']}"}
        for r in g.itertuples():
            row[f"{r.label} W/T/L"] = f"{r.a_better}/{r.ties}/{r.a_worse}"
            row[f"{r.label} %P3"] = fmt(r.pct_of_p3_loss, 2)
        wtl.append(row)
    out += ["Win/tie/loss (a better / tie / a worse, seeds) and mean difference as % of P3 mean loss:", "",
            md_table(pd.DataFrame(wtl)), ""]

    out += ["### False closures per policy by fault level", "",
            ("Summed over the 8 cells of each fault level. Cell entries: false closures (payments) · "
            "runs with ≥ 1 false closure / runs · payment-level rate with Wilson 95% CI."), ""]
    faults = list(dict.fromkeys(fc["fault"]))
    rows = []
    for policy in P_NAMES:
        row = {"policy": f"{P_NAMES[policy]} {policy}"}
        for fault in faults:
            r = fc[(fc["fault"] == fault) & (fc["policy"] == policy)].iloc[0]
            row[fault] = (f"{r['false_closures']} · {r['runs_with_fc']}/{r['runs']} · "
                          f"{100 * r['payment_rate']:.2f}% [{100 * r['payment_rate_lo']:.2f}, "
                          f"{100 * r['payment_rate_hi']:.2f}]")
        rows.append(row)
    out += [md_table(pd.DataFrame(rows)), ""]

    per_cell = (trials[trials["policy"].isin(["edf", "mh", "lm_ack"]) & (trials["fault"] != "none")]
                .groupby(["fault", "slack", "k", "n", "policy"], sort=False)["false_closure"].sum()
                .unstack("policy").reset_index())
    per_cell = per_cell.rename(columns={p: f"{P_NAMES[p]} {p}" for p in ("edf", "mh", "lm_ack")})
    out += [("False closures per cell for the ack-trust runbooks (fault ≠ none; P0 and P4–P6 have 0 "
            "everywhere, see above):"), "", md_table(per_cell), ""]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m analysis.g1.effects")
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    trials = load_trials(args.results)
    table = effects(trials)
    table.to_csv(out / "g1_effects.csv", index=False)
    fc = fc_by_fault(trials)
    fc.to_csv(out / "g1_false_closure_by_fault.csv", index=False)
    print(write_section(out, "1_effects", section(table, fc, trials)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
