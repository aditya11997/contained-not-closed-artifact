"""G1 item 2: late-capture opportunity (decides the sweep-timing item).

For each run: payments with D > d_nominal ("late") that were not cancelled by d_nominal
(still PENDING at d), their suspect value, and how many a cancel could still have stopped:
- feasible_blind: d + s < D (a cancel sent at d lands before capture; captures win ties);
- feasible_read:  d + q + s < D (a status read at d, then the cancel);
- beyond_sweep:   not (end_of_watch + q + s < D), i.e. out of reach of the current single
  end-of-run sweep (end_of_watch = last d_nominal);
- rescued:        cancelled after d (rescued_sweep: by a cancel the end-of-run sweep sent, i.e. the
  current late_cancel; the rest are pre-deadline calls, e.g. P5 replays, that land after d).
"missed" = actionable (permitted, gain > 0) ∧ feasible_read ∧ beyond_sweep: what a
per-deadline read could add. Net value = suspect value − c · legit value (loss reduction).

    python -m analysis.g1.late_capture --results results/dev/primary-500 \
        --out results/dev/primary-500/analysis
"""
import argparse
import multiprocessing as mp
from pathlib import Path

import pandas as pd

from p4.sched.common import gain

from .common import (
    P_NAMES,
    cell_params,
    fmt,
    iter_runs,
    load_trials,
    md_table,
    write_section,
)

POLICIES = ("none", "lm_ack", "lm_verify", "lm_reconcile", "adaptive")
SETS = ("late", "open", "actionable", "feas_blind", "feas_read", "missed", "rescued", "rescued_sweep")


class _O:  # gain() takes an object with amount and p_score
    def __init__(self, p):
        self.amount, self.p_score = p["amount"], p["p_score"]


def run_counts(files, q: int, c: float) -> dict:
    truth, events = files["truth.json"], files["events.jsonl"]
    pays = truth["payments"]
    eow = max(p["d_nominal"] for p in pays)
    cancelled_at = {e["uid"]: e["t"] for e in events if e["type"] == "CANCELLED"}
    # Cancels the sweep sent (intent at or after the last d_nominal) vs earlier calls that land late.
    sweep_cancel = {r["uid"] for r in files["journal.jsonl"]
                    if r["kind"] == "intent" and r["op"] != "status" and r["t"] >= eow}
    out = {f"{s}_{m}": 0 for s in SETS for m in ("n", "suspect", "legit")}
    for p in pays:
        d, D, s, uid = p["d_nominal"], p["D_actual"], p["s_cancel"], p["uid"]
        if D <= d:
            continue
        tags = ["late"]
        if uid in cancelled_at and cancelled_at[uid] <= d:
            pass
        else:
            tags.append("open")
            if p["permitted"] and gain(_O(p), c) > 0:
                tags.append("actionable")
                if d + s < D:
                    tags.append("feas_blind")
                if d + q + s < D:
                    tags.append("feas_read")
                    if not (eow + q + s < D):
                        tags.append("missed")
            if uid in cancelled_at:
                tags.append("rescued")
                if uid in sweep_cancel:
                    tags.append("rescued_sweep")
        for t in tags:
            out[f"{t}_n"] += 1
            out[f"{t}_suspect" if p["y_suspect"] else f"{t}_legit"] += p["amount"]
    return out


def cell_task(task):
    results, cell_id, q, c = task
    rows = []
    for seed, policy, files in iter_runs(Path(results), cell_id, POLICIES):
        if files:
            rows.append({"cell_id": cell_id, "seed": seed, "policy": policy, **run_counts(files, q, c)})
    return rows


def compute(results: Path, workers: int) -> pd.DataFrame:
    params = cell_params(results)
    tasks = [(str(results), cid, p["scenario"]["q"], p["factors"]["c"]) for cid, p in params.items()]
    with mp.get_context("spawn").Pool(workers) as pool:
        per_run = [r for rows in pool.map(cell_task, tasks) for r in rows]
    return pd.DataFrame(per_run)


def summarize(per_run: pd.DataFrame, trials: pd.DataFrame) -> pd.DataFrame:
    c = trials.groupby("cell_id")["c"].first().astype(float)
    agg = per_run.groupby(["cell_id", "policy"], sort=False).mean(numeric_only=True).drop(columns="seed")
    loss = trials.groupby(["cell_id", "policy"])["loss"].mean().rename("mean_loss")
    agg = agg.join(loss).reset_index()
    agg["c"] = agg["cell_id"].map(c)
    for s in SETS:
        agg[f"{s}_net"] = agg[f"{s}_suspect"] - agg["c"] * agg[f"{s}_legit"]
    agg["missed_net_pct_loss"] = 100 * agg["missed_net"] / agg["mean_loss"]
    agg["feas_read_net_pct_loss"] = 100 * agg["feas_read_net"] / agg["mean_loss"]
    factors = trials.drop_duplicates("cell_id").set_index("cell_id")[["fault", "slack", "k", "n", "jitter"]]
    return agg.join(factors, on="cell_id")


def jitter_section(agg: pd.DataFrame, n_seeds: int, source: str) -> str:
    """Compact view for a multi-jitter grid: P5's late-capture opportunity by jitter preset."""
    p5 = agg[agg["policy"] == "lm_reconcile"]
    cols = {"late_n": "late n", "actionable_n": "act n", "feas_read_n": "feas n", "missed_n": "missed n",
            "missed_net_pct_loss": "missed % loss", "feas_read_net_pct_loss": "feas % loss",
            "rescued_sweep_n": "rescued by sweep n"}
    rows = []
    for jitter, g in p5.groupby("jitter", sort=False):
        row = {"jitter": jitter, "cells": len(g)}
        for c, name in cols.items():
            row[name] = f"{fmt(g[c].mean(), 3)} (max {fmt(g[c].max(), 3)})"
        rows.append(row)
    return "\n".join([
        "### Late-capture opportunity by jitter (sensitivity grid)", "",
        (f"P5 (lm_reconcile) on `{source}` ({n_seeds} dev seeds). Per-seed means averaged over the grid "
        "cells of each jitter preset (max over cells in brackets); definitions as above. Per-cell data: "
        "`g1_late_capture.csv` in that run's analysis directory."), "", md_table(pd.DataFrame(rows)), ""])


def section(agg: pd.DataFrame, n_seeds: int, threshold: float = 1.0) -> str:
    out = ["## 2. Late-capture opportunity (sweep timing)", "",
           (f"Per-seed means over {n_seeds} seeds. *late*: D > d_nominal. *open*: late and not cancelled "
           "by d_nominal. *act*: open, permitted, gain > 0. *feas*: act and d + q + s < D (read at d, then "
           "cancel, lands before capture). *missed*: feas but out of reach of the current end-of-run "
           "sweep (not last-d + q + s < D) — the most a per-deadline read could add. *rescued*: cancelled "
           "after d; *by sweep*: of those, by a cancel the end-of-run sweep sent (the current late_cancel); "
           "the rest are pre-deadline calls (P5 replays are not deadline-checked) that land after d. Values in amount units; net = suspect − c · legit. "
           "Full per-cell data incl. blind-cancel feasibility: `g1_late_capture.csv`."), ""]
    rows = []
    for r in agg[agg["policy"].isin(["lm_ack", "lm_reconcile", "adaptive"])].itertuples():
        rows.append({"cell": f"{r.fault}/{r.slack}/k{r.k}/n{r.n}", "policy": P_NAMES[r.policy],
                     "late n": fmt(r.late_n, 2), "open n": fmt(r.open_n, 2),
                     "open suspect": fmt(r.open_suspect, 0), "act n": fmt(r.actionable_n, 2),
                     "feas n": fmt(r.feas_read_n, 3), "feas net": fmt(r.feas_read_net, 1),
                     "missed n": fmt(r.missed_n, 3), "missed net": fmt(r.missed_net, 1),
                     "rescued n": fmt(r.rescued_n, 3), "by sweep": fmt(r.rescued_sweep_n, 3),
                     "mean loss": fmt(r.mean_loss, 0),
                     "missed % loss": fmt(r.missed_net_pct_loss, 2)})
    out += [md_table(pd.DataFrame(rows)), ""]
    p5 = agg[agg["policy"] == "lm_reconcile"]
    worst = p5.loc[p5["missed_net_pct_loss"].idxmax()]
    worst_feas = p5.loc[p5["feas_read_net_pct_loss"].idxmax()]
    verdict = "meets" if worst["missed_net_pct_loss"] >= threshold else "does not meet"
    out += [(f"**Decision rule (proposed): change sweep timing only if P5's missed net value ≥ {threshold}% "
            f"of P5 mean loss in some cell.** Largest P5 missed net share: "
            f"{fmt(worst['missed_net_pct_loss'], 3)}% ({worst['fault']}/{worst['slack']}/k{worst['k']}/"
            f"n{worst['n']}; {fmt(worst['missed_n'], 3)} payments/seed) → {verdict} the threshold. "
            f"Upper bound if every feasible late payment were cancelled (ignoring the sweep): "
            f"{fmt(worst_feas['feas_read_net_pct_loss'], 3)}% ({worst_feas['fault']}/{worst_feas['slack']}/"
            f"k{worst_feas['k']}/n{worst_feas['n']})."), ""]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m analysis.g1.late_capture")
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--section-out", help="directory for the Markdown section (default: --out)")
    ap.add_argument("--section-name", default="2_late_capture")
    args = ap.parse_args(argv)
    out = Path(args.out)
    trials = load_trials(args.results)
    per_run = compute(Path(args.results), args.workers)
    # Sanity: P0 never cancels, so its open set is exactly its late set; rescued <= open.
    p0 = per_run[per_run["policy"] == "none"]
    assert (p0["open_n"] == p0["late_n"]).all() and (per_run["rescued_n"] <= per_run["open_n"]).all()
    agg = summarize(per_run, trials)
    agg.to_csv(out / "g1_late_capture.csv", index=False)
    n_seeds = trials["seed"].nunique()
    text = (jitter_section(agg, n_seeds, args.results) if agg["jitter"].nunique() > 1
            else section(agg, n_seeds))
    print(write_section(Path(args.section_out or out), args.section_name, text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
