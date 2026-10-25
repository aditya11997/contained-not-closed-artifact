"""Paired-seed synthetic evaluations. Standard library only."""
import argparse
import csv
import json
import math
import random
import statistics
from pathlib import Path
from .model import make_scenario, policy_view, SyntheticProcessor
from .policies import plan

METHODS = ("none", "edf", "value_greedy", "dp")


def evaluate(payments, method: str, collateral_factor: float = 0.5):
    processor = SyntheticProcessor(payments)
    observed = policy_view(payments)
    path = plan(observed, method, collateral_factor)
    # Governance is not supplied by the original agent. An independent cancel-only
    # remediation operator performs every action; authorization checked at sink.
    t = 0
    for uid in path:
        p = next(p for p in observed if p.uid == uid)
        t += p.remedy_minutes
        processor.cancel(uid, t, "remediation_operator", f"{uid}/incident-1/cancel")
    max_capture = max(p.capture_at for p in payments)
    truth = processor.score_truth(max_capture + 1)
    # A canceled valid payment is a cost, not silently counted as a safety win.
    truth["loss"] = truth["suspect_captured_value"] + collateral_factor * truth["legitimate_cancelled_value"]
    truth["elapsed_remediation_minutes"] = t
    truth["planned_count"] = len(path)
    return truth


def run_trials(n: int, count: int, output: Path, base_seed: int = 71231):
    output.parent.mkdir(parents=True, exist_ok=True)
    configs = [
       ("main", "normal", 0.0, 0.5),
       ("tight", "tight", 0.0, 0.5),
       ("loose", "loose", 0.0, 0.5),
       ("shift_negative", "normal", -0.2, 0.5),
       ("shift_positive", "normal", 0.2, 0.5),
       ("high_collateral", "normal", 0.0, 1.0),
    ]
    metrics = ("loss", "suspect_captured_value", "legitimate_cancelled_value",
               "cancelled_count", "elapsed_remediation_minutes", "authority_violations")
    rows = []
    for cfg, urgency, shift, cost in configs:
        for i in range(n):
            seed = base_seed + i
            payments = make_scenario(seed, count, urgency, shift)
            for method in METHODS:
                results = evaluate(payments, method, cost)
                rows.append({"config": cfg, "seed": seed, "method": method,
                             "cost_factor": cost, **{k: results[k] for k in metrics}})
    fields = ["config", "seed", "method", "cost_factor", *metrics]
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def paired_bootstrap(values, replicates: int = 2000, seed: int = 4001):
    rng = random.Random(seed)
    n = len(values)
    if not n:
        return [None, None]
    estimates = []
    for _ in range(replicates):
        estimates.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    estimates.sort()
    return [estimates[int(.025*replicates)], estimates[int(.975*replicates)-1]]


def summarize(rows, target: Path):
    from collections import defaultdict
    grp = defaultdict(list)
    index = defaultdict(dict)
    for r in rows:
        grp[(r["config"],r["method"])].append(r)
        index[(r["config"],r["seed"])][r["method"]] = r
    summary = []
    configs = sorted({r["config"] for r in rows})
    for cfg in configs:
        paired_edf = []
        paired_greedy = []
        for key, policies in index.items():
            if key[0] != cfg:
                continue
            paired_edf.append(float(policies["dp"]["loss"]) - float(policies["edf"]["loss"]))
            paired_greedy.append(float(policies["dp"]["loss"]) - float(policies["value_greedy"]["loss"]))
        for method in METHODS:
            items = grp[(cfg,method)]
            summary.append({"config":cfg,"method":method,"n":len(items),
                "mean_loss":round(statistics.mean(float(q["loss"]) for q in items),2),
                "mean_suspect_captured":round(statistics.mean(float(q["suspect_captured_value"]) for q in items),2),
                "mean_legitimate_cancelled":round(statistics.mean(float(q["legitimate_cancelled_value"]) for q in items),2),
                "mean_cancelled_count":round(statistics.mean(float(q["cancelled_count"]) for q in items),2),
                "dp_minus_edf_mean":round(statistics.mean(paired_edf),2) if method=="dp" else "",
                "dp_minus_edf_ci95":json.dumps(paired_bootstrap(paired_edf)) if method=="dp" else "",
                "dp_minus_greedy_mean":round(statistics.mean(paired_greedy),2) if method=="dp" else "",
                "dp_minus_greedy_ci95":json.dumps(paired_bootstrap(paired_greedy)) if method=="dp" else "",
            })
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=list(summary[0]))
        writer.writeheader();writer.writerows(summary)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=1000)
    parser.add_argument("--payments",type=int,default=8)
    parser.add_argument("--seed",type=int,default=71231)
    parser.add_argument("--out",default="results")
    args=parser.parse_args()
    if args.n<1 or not 1<=args.payments<=12:
        parser.error("n >= 1 and 1 <= payments <= 12 required")
    out=Path(args.out)
    rows=run_trials(args.n,args.payments,out/"trials.csv",args.seed)
    summary=summarize(rows,out/"summary.csv")
    for r in summary:
        if r["config"]=="main":
            print(f"{r['method']:15} mean_loss={r['mean_loss']:8.2f} mean_suspect_captured={r['mean_suspect_captured']:8.2f}")
    print("Saved", (out/"trials.csv").as_posix(),"and",(out/"summary.csv").as_posix())

if __name__ == "__main__":
    main()
