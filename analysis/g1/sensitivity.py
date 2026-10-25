"""G1 item 4: sensitivity of the H1/H2 pattern to the placeholder outage presets (lambda, L, r)
and the capture jitter j (configs/dev/g1_*.toml, 200 dev seeds).

Per grid point (outage preset tag x jitter):
- H1: P3 false closures at none / low / high (sum over the 8 slack x k x n cells), whether
  they increase, in how many of the 8 cells high > low, and P4-P6 false closures (must be 0);
- H2: P4 - P3 paired loss (mean, unadjusted 95% bootstrap CI) at tight, k = 1, n in {8, 32};
- for information only (NOT a selection criterion): cells with CI < 0 / CI > 0 for P5 - P3
  and P6 - P5;
- outage exposure lambda * L and the realized share of timed-out cancels the sink applied.
Also checks that the centre point reproduces results/dev/primary-500 on the same seeds.

    python -m analysis.g1.sensitivity --results results/dev/g1-sensitivity-200 \
        --primary results/dev/primary-500 --out results/dev/primary-500/analysis
"""
import argparse
import multiprocessing as mp
import re
from pathlib import Path

import pandas as pd

from analysis.stats import bootstrap_ci, paired_differences

from .common import (
    cell_params,
    ci_str,
    fmt,
    iter_runs,
    load_trials,
    md_table,
    timeout_applied,
    write_section,
)

METRICS = ("loss", "suspect_captured", "legit_cancelled", "false_closure", "closed_cancelled",
           "closed_exposure", "unresolved_pending", "unresolved_unknown", "n_cancel_calls",
           "n_status_calls", "n_replays", "end_of_run")
POINT_ORDER = ("c", "lam005", "lam05", "L2", "L8", "r0", "r1", "hiLhiL", "loLloL")


def point_of(fault: str, jitter: str) -> tuple[str, str]:
    m = re.fullmatch(r"g1_(.+)_(low|high)", fault)
    return (m.group(1) if m else None), jitter


def applied_task(task):
    results, cell_id = task
    n = a = 0
    for _, _, files in iter_runs(Path(results), cell_id, ("lm_reconcile",)):
        if files:
            flags = timeout_applied(files["journal.jsonl"], files["events.jsonl"])
            n, a = n + len(flags), a + sum(flags)
    return cell_id, n, a


def ci_cells(trials, cell_ids, a, b):
    neg = pos = 0
    for cid in cell_ids:
        d = paired_differences(trials, cid, a, b)
        r = bootstrap_ci(d.to_numpy(), label=f"{cid}|{a}-{b}|loss")
        neg += r["hi"] < 0
        pos += r["lo"] > 0
    return f"{neg}/{pos}"


def analyse(trials: pd.DataFrame, params: dict, applied: dict) -> pd.DataFrame:
    cellf = trials.drop_duplicates("cell_id").set_index("cell_id")[["fault", "slack", "k", "n", "jitter"]]
    rows = []
    jitters = list(dict.fromkeys(cellf["jitter"]))
    points = [(t, "uniform3") for t in POINT_ORDER] + [("c", j) for j in jitters if j != "uniform3"]
    for tag, jitter in points:
        sel = cellf[(cellf["jitter"] == jitter) &
                    ((cellf["fault"] == "none") | cellf["fault"].isin([f"g1_{tag}_low", f"g1_{tag}_high"]))]
        if sel.empty:
            continue
        level = sel["fault"].map(lambda f: "none" if f == "none" else f.rsplit("_", 1)[1])
        t = trials[trials["cell_id"].isin(sel.index)].assign(level=trials["cell_id"].map(level))
        p3 = t[t["policy"] == "lm_ack"]
        fc = p3.groupby("level")["false_closure"].sum()
        by_cell = p3.groupby(["slack", "k", "n", "level"])["false_closure"].sum().unstack("level")
        hi = params[sel.index[level == "high"][0]]["fault"]
        row = {"point": tag, "jitter": jitter, "λ_high": hi["outage_rate"], "L_high": hi["outage_length"],
               "r": hi["outage_commit_prob"], "λL_high": hi["outage_rate"] * hi["outage_length"],
               "P3 FC none/low/high": f"{fc.get('none', 0)}/{fc.get('low', 0)}/{fc.get('high', 0)}",
               "H1 increasing": bool(fc.get("none", 0) <= fc.get("low", 0) < fc.get("high", 0)),
               "cells high>low": f"{int((by_cell['high'] > by_cell['low']).sum())}/{len(by_cell)}",
               "P4–P6 FC": int(t[t["policy"].isin(["lm_verify", "lm_reconcile", "adaptive"])]["false_closure"].sum())}
        for lvl in ("low", "high"):
            for n in (8, 32):
                cid = sel.index[(level == lvl) & (sel["slack"] == "tight") & (sel["k"] == "1") &
                                (sel["n"] == n)][0]
                r = bootstrap_ci(paired_differences(trials, cid, "lm_verify", "lm_ack").to_numpy(),
                                 label=f"{cid}|lm_verify-lm_ack|loss")
                row[f"H2 P4−P3 {lvl} n{n}"] = ci_str(r["mean"], r["lo"], r["hi"])
        faulted = list(sel.index[level != "none"])
        row["P5−P3 cells CI<0/>0"] = ci_cells(trials, faulted, "lm_reconcile", "lm_ack")
        row["P6−P5 cells CI<0/>0 (info)"] = ci_cells(trials, faulted, "adaptive", "lm_reconcile")
        for lvl in ("low", "high"):
            ids = sel.index[level == lvl]
            n_to, n_ap = sum(applied[c][0] for c in ids), sum(applied[c][1] for c in ids)
            row[f"applied share of timeouts {lvl}"] = n_ap / n_to if n_to else float("nan")
        rows.append(row)
    return pd.DataFrame(rows)


def centre_check(trials: pd.DataFrame, primary: pd.DataFrame) -> str:
    rename = {"g1_c_low": "low", "g1_c_high": "high", "none": "none"}
    g = trials[trials["fault"].isin(rename) & (trials["jitter"] == "uniform3")].copy()
    g["fault"] = g["fault"].map(rename)
    keys = ["fault", "slack", "k", "n", "seed", "policy"]
    p = primary[primary["seed"].isin(g["seed"].unique())]
    m = g.merge(p, on=keys, suffixes=("", "_p"), how="inner")
    diff = sum(int((m[c] != m[f"{c}_p"]).sum()) for c in METRICS)
    return (f"Centre-point check: {len(m)} of {len(g)} centre rows matched to primary-500 rows "
            f"(same factors, seed, policy); metric values differing: {diff} (must be 0).")


def section(table: pd.DataFrame, check: str, n_seeds: int) -> str:
    show = table.copy()
    for col in ("applied share of timeouts low", "applied share of timeouts high"):
        show[col] = show[col].map(lambda x: fmt(x, 2))
    show["λL_high"] = show["λL_high"].map(lambda x: fmt(x, 3))
    a = show[["point", "jitter", "λ_high", "L_high", "r", "λL_high", "applied share of timeouts low",
              "applied share of timeouts high", "P3 FC none/low/high", "H1 increasing", "cells high>low",
              "P4–P6 FC"]]
    b = show[["point", "jitter"] + [c for c in show.columns if c.startswith(("H2", "P5−", "P6−"))]]
    return "\n".join([
        "## 4. Sensitivity of the H1/H2 pattern to outage presets and jitter", "",
        (f"Grid `configs/dev/g1_sensitivity.toml` on {n_seeds} dev seeds (100000–100199): one factor at a time "
        "around the centre `c` (high: λ = 0.02, L = 4, r = 0.5; low: λ/4, L/2; f_req/f_resp as now; j = 3) "
        "plus corners `hiLhiL` (λ = 0.05, L = 8) and `loLloL` (λ = 0.005, L = 2); jitter none / uniform6 at "
        "the centre. Sums over the 8 slack × k × n cells per fault level. CIs: unadjusted 95% bootstrap; "
        "`*` excludes 0. The P6 − P5 column is information only and is not used to choose presets."), "",
        md_table(a), "", md_table(b), "", check, ""])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m analysis.g1.sensitivity")
    ap.add_argument("--results", required=True)
    ap.add_argument("--primary", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)
    results, out = Path(args.results), Path(args.out)
    trials = load_trials(results)
    params = cell_params(results)
    with mp.get_context("spawn").Pool(args.workers) as pool:
        applied = {cid: (n, a) for cid, n, a in pool.map(applied_task, [(str(results), c) for c in params])}
    table = analyse(trials, params, applied)
    table.to_csv(out / "g1_sensitivity.csv", index=False)
    check = centre_check(trials, load_trials(args.primary))
    print(check)
    print(write_section(out, "4_sensitivity", section(table, check, trials["seed"].nunique())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
