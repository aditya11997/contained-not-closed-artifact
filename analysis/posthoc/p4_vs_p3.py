"""PH5 (post hoc, descriptive; docs/POSTHOC.md): why P4 - P3 has the sign it has, per faulty
primary cell. Reads a primary run directory read-only (trials.csv and runs/*.tar.gz).

    python -m analysis.posthoc.p4_vs_p3 --results results/holdout/primary --out paper/generated/posthoc

Writes two tables:
- p4_vs_p3_split.csv: mean P4 - P3 loss in seeds where P3 had no false closure and in seeds
  where it had at least one (count, mean, unadjusted 95% percentile bootstrap CI).
- p4_vs_p3_attribution.csv: each payment P3 reported CLOSED_CANCELLED whose log final is not a
  CANCELLED before capture (the checker's false closure) is classified by what P4 did with it:
  recovered_resend (P4 cancelled it before d_nominal after >= 2 sends), recovered_first_send
  (one send; different timing), recovered_late (cancelled at or after d_nominal) or
  lost_both (captured under P4 as well). Per class: payments, and the mean per-seed sum of the
  per-payment loss difference P4 - P3. `other` is the rest of the mean P4 - P3: every other
  payment (capacity cost, late cancels on other payments). The classes plus `other` add up to
  `mean_diff`, which is checked against trials.csv.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from analysis.g1.common import iter_seed_groups
from analysis.stats import bootstrap_ci, load_trials, paired_differences

from .common import CELL_COLUMNS

P3, P4 = "lm_ack", "lm_verify"
FAULTY = ("low", "high")
CLASSES = ("recovered_resend", "recovered_first_send", "recovered_late", "lost_both")


def first_effects(events) -> dict[str, dict]:
    out = {}
    for e in events:
        if e["type"] in ("CANCELLED", "CAPTURED"):
            out.setdefault(e["uid"], e)
    return out


def payment_loss(p: dict, effect: dict, c: float) -> float:
    if effect is not None and effect["type"] == "CANCELLED" and effect["t"] < p["D_actual"]:
        return c * p["amount"] if not p["y_suspect"] else 0.0
    return float(p["amount"]) if p["y_suspect"] else 0.0


def false_closed(report, truth, effects) -> list[str]:
    pay = {p["uid"]: p for p in truth["payments"]}
    out = []
    for e in report["entries"]:
        f = effects.get(e["uid"])
        if e["state"] == "CLOSED_CANCELLED" and (
                f is None or f["type"] != "CANCELLED" or f["t"] >= pay[e["uid"]]["D_actual"]):
            out.append(e["uid"])
    return out


def classify(uid, p, effect4, journal4) -> str:
    if effect4 is None or effect4["type"] != "CANCELLED" or effect4["t"] >= p["D_actual"]:
        return "lost_both"
    if effect4["t"] >= p["d_nominal"]:
        return "recovered_late"
    sends = sum(1 for r in journal4 if r["kind"] == "intent" and r["uid"] == uid
                and r["op"] in ("cancel", "replay"))
    return "recovered_resend" if sends >= 2 else "recovered_first_send"


def attribute_cell(args) -> dict:
    """Per-seed sums for one cell, read from its run archive."""
    results, cell_id = args
    n_pay = dict.fromkeys(CLASSES, 0)
    per_seed = {}
    for seed, runs in iter_seed_groups(Path(results), cell_id, (P3, P4)):
        truth = runs[P3]["truth.json"]
        c, pay = truth["c"], {p["uid"]: p for p in truth["payments"]}
        e3, e4 = first_effects(runs[P3]["events.jsonl"]), first_effects(runs[P4]["events.jsonl"])
        sums = dict.fromkeys(CLASSES, 0.0)
        for uid in false_closed(runs[P3]["report.json"], truth, e3):
            k = classify(uid, pay[uid], e4.get(uid), runs[P4]["journal.jsonl"])
            n_pay[k] += 1
            sums[k] += payment_loss(pay[uid], e4.get(uid), c) - payment_loss(pay[uid], e3.get(uid), c)
        total = sum(payment_loss(p, e4.get(u), c) - payment_loss(p, e3.get(u), c) for u, p in pay.items())
        per_seed[seed] = (total, sums)
    return {"cell_id": cell_id, "n_pay": n_pay, "per_seed": per_seed}


def faulty_cells(trials: pd.DataFrame) -> pd.DataFrame:
    cells = trials.drop_duplicates("cell_id")[list(CELL_COLUMNS)]
    return cells[cells["fault"].isin(FAULTY)].reset_index(drop=True)


def split_table(trials: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for cell in faulty_cells(trials).itertuples(index=False):
        d = paired_differences(trials, cell.cell_id, P4, P3)
        t = trials[(trials["cell_id"] == cell.cell_id) & (trials["policy"] == P3)].set_index("seed")
        fc = t["false_closure"].loc[d.index]
        row = cell._asdict()
        for name, mask in (("p3_fc0", fc == 0), ("p3_fc1", fc > 0)):
            x = d[mask].to_numpy()
            row[f"{name}_seeds"] = len(x)
            if len(x):
                r = bootstrap_ci(x, label=f"posthoc|{cell.cell_id}|{P4}-{P3}|{name}")
                row.update({f"{name}_mean": r["mean"], f"{name}_ci_lo": r["lo"], f"{name}_ci_hi": r["hi"]})
            else:
                row.update(dict.fromkeys((f"{name}_mean", f"{name}_ci_lo", f"{name}_ci_hi"), np.nan))
        row["mean_diff"] = float(d.mean())
        rows.append(row)
    return pd.DataFrame(rows)


def attribution_table(trials: pd.DataFrame, results: Path, workers: int) -> pd.DataFrame:
    cells = faulty_cells(trials)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        parts = list(pool.map(attribute_cell, [(str(results), c) for c in cells["cell_id"]]))
    rows = []
    for cell, part in zip(cells.itertuples(index=False), parts):
        d = paired_differences(trials, cell.cell_id, P4, P3)
        seeds = sorted(part["per_seed"])
        if seeds != sorted(d.index):
            raise RuntimeError(f"{cell.cell_id}: archive seeds differ from trials.csv")
        totals = np.array([part["per_seed"][s][0] for s in seeds])
        if not np.allclose(totals, d.loc[seeds].to_numpy()):
            raise RuntimeError(f"{cell.cell_id}: per-payment losses do not add up to trials.csv loss")
        row = {**cell._asdict(), "n_seeds": len(seeds), "mean_diff": float(totals.mean())}
        for k in CLASSES:
            row[f"{k}_payments"] = part["n_pay"][k]
            row[f"{k}_per_seed"] = float(np.mean([part["per_seed"][s][1][k] for s in seeds]))
        row["other_per_seed"] = row["mean_diff"] - sum(row[f"{k}_per_seed"] for k in CLASSES)
        rows.append(row)
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m analysis.posthoc.p4_vs_p3", description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=Path, required=True, help="a primary run directory (read only)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    trials = load_trials(args.results)
    args.out.mkdir(parents=True, exist_ok=True)
    split = split_table(trials)
    split.to_csv(args.out / "p4_vs_p3_split.csv", index=False)
    attr = attribution_table(trials, args.results, args.workers)
    attr.to_csv(args.out / "p4_vs_p3_attribution.csv", index=False)
    pd.set_option("display.width", 250)
    print(split.drop(columns="cell_id").round(1).to_string(index=False))
    print()
    print(attr.drop(columns="cell_id").round(1).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
