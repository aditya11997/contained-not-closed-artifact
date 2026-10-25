"""G1 item 3: where and why P6 (adaptive) and P5 (lm_reconcile) act differently.

Both runbooks see identical inputs and fault draws (keyed by (seed, call index per payment)),
so their journals agree until the first point they choose differently. Intents are compared
as per-time sets (simultaneous dispatches at k > 1 may be ordered differently without being a
real difference). At the first time t whose sets differ, the common prefix gives the state:
outstanding unknowns (cancels that timed out with no definitive reply yet), TOO_LATE replies
seen, and P6's rho_hat. Mechanisms:
- replan_no_unknown: both send cancels, no unknown outstanding (P6 re-solves LM at every
  event with the true worker-free times; P5 follows its t = 0 plan);
- replan_with_unknown: both send cancels with an unknown outstanding (P6 ranks the unknown,
  weighted by 1 - rho_hat, against fresh cancels; P5 only replays after its plan is sent);
- p6_replays_first: P6 replays an unknown while P5 sends its next planned cancel;
- p5_replays_p6_not: P5 replays an unknown that P6 does not replay at t;
- sweep_or_other: anything else (status reads, idle vs act).

    python -m analysis.g1.p6_vs_p5 --results results/dev/primary-500 \
        --out results/dev/primary-500/analysis
"""
import argparse
import multiprocessing as mp
from collections import defaultdict
from pathlib import Path

import pandas as pd

from .common import (
    cell_params,
    fmt,
    iter_seed_groups,
    load_trials,
    md_table,
    timeout_applied,
    write_section,
)

PAIR = ("lm_reconcile", "adaptive")
MECHANISMS = ("replan_no_unknown", "replan_with_unknown", "p6_replays_first", "p5_replays_p6_not",
              "sweep_or_other")


def by_time(journal):
    out = defaultdict(set)
    for r in journal:
        if r["kind"] == "intent":
            out[r["t"]].add((r["op"], r["uid"]))
    return out


def first_divergence(j5, j6):
    a, b = by_time(j5), by_time(j6)
    for t in sorted(set(a) | set(b)):
        if a.get(t, set()) != b.get(t, set()):
            return t, a.get(t, set()), b.get(t, set())
    return None


def prefix_state(journal, t):
    """Outstanding unknowns, TOO_LATE replies and P6's rho_hat from results before time t
    (plus results at t, which are processed before dispatches at t)."""
    unknown, too_late, resolved, applied = set(), 0, 0, 0
    for r in journal:
        if r["kind"] != "result" or r["t"] > t:
            continue
        if r["op"] == "status":
            if r["state"] in ("CANCELLED", "CAPTURED", "PENDING"):
                unknown.discard(r["uid"])
            continue
        if r["timeout"]:
            unknown.add(r["uid"])
            continue
        if (r["op"] == "replay" and r["uid"] in unknown
                and r["result"] in ("CANCELLED", "ALREADY_CANCELLED", "TOO_LATE_CAPTURED")):
            resolved += 1
            applied += r["result"] in ("CANCELLED", "ALREADY_CANCELLED") and r["effect_seq"] < r["seq"]
        if r["result"] == "TOO_LATE_CAPTURED":
            too_late += 1
        unknown.discard(r["uid"])
    return unknown, too_late, (applied + 1) / (resolved + 2)


def classify(s5, s6, unknown) -> str:
    ops5, ops6 = {op for op, _ in s5 - s6}, {op for op, _ in s6 - s5}
    rep5 = {u for op, u in s5 if op == "replay"}
    rep6 = {u for op, u in s6 if op == "replay"}
    if rep6 - rep5 and "cancel" in ops5:
        return "p6_replays_first"
    if rep5 - rep6:
        return "p5_replays_p6_not"
    if ops5 <= {"cancel"} and ops6 <= {"cancel"} and (ops5 or ops6):
        return "replan_with_unknown" if unknown else "replan_no_unknown"
    return "sweep_or_other"


def replay_stats(journal) -> tuple[int, int, list[int]]:
    """(replays, replays that timed out, gaps from the payment's latest timeout to the replay)."""
    n = to = 0
    gaps, last_timeout = [], {}
    for r in journal:
        if r["kind"] != "result" or r["op"] not in ("cancel", "replay"):
            continue
        if r["op"] == "replay":
            n += 1
            to += r["timeout"]
            if r["uid"] in last_timeout:
                gaps.append(r["t"] - last_timeout[r["uid"]])
        if r["timeout"]:
            last_timeout[r["uid"]] = r["t"]
    return n, to, gaps


def cell_task(task):
    results, cell_id, losses = task
    rows = []
    for seed, runs in iter_seed_groups(Path(results), cell_id, PAIR):
        j5, j6 = runs[PAIR[0]]["journal.jsonl"], runs[PAIR[1]]["journal.jsonl"]
        same = [r for r in j5] == [r for r in j6]
        l5, l6 = losses[(seed, PAIR[0])], losses[(seed, PAIR[1])]
        applied = timeout_applied(j5, runs[PAIR[0]]["events.jsonl"])
        row = {"cell_id": cell_id, "seed": seed, "journals_identical": same, "loss_p5": l5,
               "loss_p6": l6, "diff": l6 - l5, "timeouts_p5": len(applied),
               "timeouts_applied_p5": sum(applied), "t_div": None, "mechanism": None,
               "unknowns_at_div": None, "too_late_at_div": None, "rho_hat_at_div": None,
               "p5_actions": "", "p6_actions": ""}
        for tag, j in (("p5", j5), ("p6", j6)):
            n, to, gaps = replay_stats(j)
            row.update({f"replays_{tag}": n, f"replay_timeouts_{tag}": to,
                        f"replay_gap_sum_{tag}": sum(gaps), f"replay_gap_n_{tag}": len(gaps)})
        div = None if same else first_divergence(j5, j6)
        if div is not None:
            t, s5, s6 = div
            unknown, too_late, rho = prefix_state(j5, t)
            row.update(t_div=t, mechanism=classify(s5, s6, unknown), unknowns_at_div=len(unknown),
                       too_late_at_div=too_late, rho_hat_at_div=rho,
                       p5_actions=" ".join(sorted(f"{o}:{u}" for o, u in s5 - s6)),
                       p6_actions=" ".join(sorted(f"{o}:{u}" for o, u in s6 - s5)))
        elif not same:
            row["mechanism"] = "sweep_or_other"   # same intents, different results/order only
        rows.append(row)
    return rows


def compute(results: Path, trials: pd.DataFrame, workers: int) -> pd.DataFrame:
    tasks = []
    for cell_id in cell_params(results):
        g = trials[(trials["cell_id"] == cell_id) & trials["policy"].isin(PAIR)]
        tasks.append((str(results), cell_id,
                      {(s, p): l for s, p, l in zip(g["seed"], g["policy"], g["loss"])}))
    with mp.get_context("spawn").Pool(workers) as pool:
        return pd.DataFrame([r for rows in pool.map(cell_task, tasks) for r in rows])


# -- traces ----------------------------------------------------------------------------------
def timeline(journal, events):
    """t -> list of strings: intents, results and sink effects at t."""
    out = defaultdict(list)
    for r in journal:
        if r["kind"] == "intent":
            out[r["t"]].append(f"→{r['op']} {r['uid']}")
        else:
            res = "TIMEOUT" if r["timeout"] else (r.get("result") or r.get("state"))
            out[r["t"]].append(f"←{r['op']} {r['uid']} {res}")
    for e in events:
        if e["type"] in ("CANCELLED", "CAPTURED"):
            out[e["t"]].append(f"[sink {e['uid']} {e['type']}]")
    return out


def trace_md(row, runs, factors) -> str:
    truth = runs[PAIR[0]]["truth.json"]
    head = (f"### {factors['fault']}/{factors['slack']}/k{factors['k']}/n{factors['n']}, seed {row.seed} "
            f"— mechanism `{row.mechanism}`, loss P5 {fmt(row.loss_p5, 0)} vs P6 {fmt(row.loss_p6, 0)} "
            f"(P6 − P5 = {fmt(row.diff, 0)})")
    pays = pd.DataFrame([{"uid": p["uid"], "amount": p["amount"], "p": p["p_score"], "d": p["d_nominal"],
                          "D": p["D_actual"], "s": p["s_cancel"], "permitted": p["permitted"],
                          "suspect": p["y_suspect"]} for p in truth["payments"]])
    finals = {}
    for pol in PAIR:
        finals[pol] = {e["uid"]: e["type"] for e in runs[pol]["events.jsonl"]
                       if e["type"] in ("CANCELLED", "CAPTURED")}
    pays["final P5"] = pays["uid"].map(finals[PAIR[0]])
    pays["final P6"] = pays["uid"].map(finals[PAIR[1]])
    involved = set()
    for pol in PAIR:
        involved |= {r["uid"] for r in runs[pol]["journal.jsonl"]}
    differ = pays["final P5"] != pays["final P6"]
    pays = pays[pays["uid"].isin(involved) | differ]
    tl5 = timeline(runs[PAIR[0]]["journal.jsonl"], [e for e in runs[PAIR[0]]["events.jsonl"]
                                                    if e["uid"] in involved or True])
    tl6 = timeline(runs[PAIR[1]]["journal.jsonl"], runs[PAIR[1]]["events.jsonl"])
    rows = []
    for t in sorted(set(tl5) | set(tl6)):
        a, b = sorted(tl5.get(t, [])), sorted(tl6.get(t, []))
        if not any("→" in x or "←" in x for x in a + b):
            continue  # only sink captures at t: skip to keep the trace short
        mark = "▶ " if row.t_div == t else ""
        rows.append({"t": f"{mark}{t}", "P5 lm_reconcile": "<br>".join(a), "P6 adaptive": "<br>".join(b)})
    return "\n".join([head, "",
                      (f"First divergence at t = {row.t_div}: P5-only `{row.p5_actions or '–'}`, "
                      f"P6-only `{row.p6_actions or '–'}`; unknowns outstanding {row.unknowns_at_div}, "
                      f"TOO_LATE replies so far {row.too_late_at_div}, P6 ρ̂ {fmt(row.rho_hat_at_div, 2)}. "
                      f"Timeouts in P5's run: {row.timeouts_p5} ({row.timeouts_applied_p5} applied)."), "",
                      "Payments called or ending differently (finals from the sink log):", "",
                      md_table(pays), "",
                      ("Timeline (→ call sent, ← reply, [sink] state change; times with only captures omitted; "
                      "▶ first divergence):"), "", md_table(pd.DataFrame(rows)), ""])


def pick_examples(df: pd.DataFrame, n: int = 3) -> pd.DataFrame:
    differ = df[df["diff"] != 0].assign(absdiff=lambda x: x["diff"].abs())
    differ = differ.sort_values(["absdiff", "cell_id", "seed"], ascending=[False, True, True])
    picks, seen = [], set()
    for row in differ.itertuples():
        if row.mechanism not in seen:
            picks.append(row.Index)
            seen.add(row.mechanism)
        if len(picks) == n:
            break
    for row in differ.itertuples():   # fewer distinct mechanisms than n: fill by |diff|
        if len(picks) == n:
            break
        if row.Index not in picks:
            picks.append(row.Index)
    return df.loc[picks]


def section(df: pd.DataFrame, trials: pd.DataFrame) -> str:
    factors = trials.drop_duplicates("cell_id").set_index("cell_id")[["fault", "slack", "k", "n"]]
    df = df.join(factors, on="cell_id")
    rows = []
    for cell_id, g in df.groupby("cell_id", sort=False):
        f = factors.loc[cell_id]
        d = g[~g["journals_identical"]]
        mech = d["mechanism"].value_counts()
        rho = g["timeouts_applied_p5"].sum() / g["timeouts_p5"].sum() if g["timeouts_p5"].sum() else float("nan")
        rows.append({"cell": f"{f['fault']}/{f['slack']}/k{f['k']}/n{f['n']}",
                     "actions differ": len(d), "loss differs": int((g["diff"] != 0).sum()),
                     "P6 better/worse": f"{int((g['diff'] < 0).sum())}/{int((g['diff'] > 0).sum())}",
                     **{m: int(mech.get(m, 0)) for m in MECHANISMS},
                     "realized ρ (P5 timeouts)": fmt(rho, 2),
                     "mean ρ̂ at div": fmt(d["rho_hat_at_div"].mean(), 2)})
        for tag in ("p5", "p6"):
            n = g[f"replays_{tag}"].sum()
            rows[-1][f"{tag.upper()} replays/seed · timeout share · gap"] = (
                f"{fmt(n / len(g), 2)} · {fmt(g[f'replay_timeouts_{tag}'].sum() / n if n else float('nan'), 2)}"
                f" · {fmt(g[f'replay_gap_sum_{tag}'].sum() / max(g[f'replay_gap_n_{tag}'].sum(), 1), 1)}")
    table = pd.DataFrame(rows)
    by_mech = (df[df["mechanism"].notna()].groupby("mechanism")["diff"]
               .agg(seeds="size", mean_diff="mean", p6_better=lambda x: int((x < 0).sum()),
                    p6_worse=lambda x: int((x > 0).sum()), total_diff="sum").reset_index())
    by_mech["mean_diff"] = by_mech["mean_diff"].map(lambda x: fmt(x, 1))
    by_mech["total_diff"] = by_mech["total_diff"].map(lambda x: fmt(x, 0))
    bad = df[df["journals_identical"] & (df["diff"] != 0)]
    return "\n".join([
        "## 3. P6 vs P5: where their actions differ, and why", "",
        ("Seeds per cell whose journals differ, the mechanism at the first divergence (see "
        "`analysis/g1/p6_vs_p5.py` for definitions), and P6 − P5 loss. *Realized ρ*: share of P5's "
        "timed-out cancels that the sink had in fact applied (drop_resp or outage commit). *ρ̂ at div*: "
        "P6's Beta(1, 1) estimate at the divergence. Per-seed rows: `g1_p6_vs_p5.csv`; 3 example "
        "traces: `g1_p6_traces.md`. Replay columns: replays per seed · share of replays that timed out · "
        "mean time from the payment's latest timeout to the replay."), "", md_table(table), "",
        "Loss effect by mechanism (all cells; P6 − P5 per seed):", "", md_table(by_mech), "",
        f"Sanity: seeds with identical journals but different loss: {len(bad)} (must be 0).", ""])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m analysis.g1.p6_vs_p5")
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)
    results, out = Path(args.results), Path(args.out)
    trials = load_trials(results)
    df = compute(results, trials, args.workers)
    df.to_csv(out / "g1_p6_vs_p5.csv", index=False)
    params = cell_params(results)
    traces = ["# G1: P6 vs P5 example traces (dev, exploratory)", "",
              ("The three seeds with the largest |P6 − P5| loss difference, preferring distinct "
              "mechanisms. Generated by `analysis/g1/p6_vs_p5.py` from the archived runs."), ""]
    for row in pick_examples(df).itertuples():
        runs = next(r for s, r in iter_seed_groups(results, row.cell_id, PAIR) if s == row.seed)
        traces.append(trace_md(row, runs, params[row.cell_id]["factors"]))
    (out / "g1_p6_traces.md").write_text("\n".join(traces), encoding="utf-8")
    print(write_section(out, "3_p6_vs_p5", section(df, trials)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
