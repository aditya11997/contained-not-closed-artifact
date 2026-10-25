"""HISTORICAL — applies to the pre-G1 P6 only (run at commit 3229674; results in
results/dev/g1-rho-prior-200). P6 no longer has rho_hat or a prior (commit de3e958), so this
script fails at later commits; check out tag dev-g1-old-p6 (3229674) to reproduce it.

G1 (fix-before-freeze item): sensitivity of P6 to its rho_hat prior Beta(a, b). Exploratory,
dev seeds only. P6's code is unchanged: each variant is `Adaptive(prior=(a, b))` passed to
`run_episode` through `runbook_factory`, on the tight primary cells with faults.

Loss comes from p4.sim.oracle.score; false closures are counted from the report against the
sink log (CLOSED_CANCELLED without a final CANCELLED). The checker is not run here (these runs
are not archived). Variants are paired per seed against results/dev/primary-500 (P6 with the
default Beta(1, 1), and P5); the Beta(1, 1) variant must reproduce primary-500's P6 exactly.

    python -m analysis.g1.rho_prior --primary results/dev/primary-500 \
        --out results/dev/g1-rho-prior-200 --section-out results/dev/primary-500/analysis
(add --reuse to rebuild only the section from an existing run)
"""
import argparse
import csv
import json
import multiprocessing as mp
import sys
from pathlib import Path

import pandas as pd

from analysis.stats import bootstrap_ci
from experiments.config import CONFIGS, REPO, load_config
from experiments.manifest import base_manifest, now_iso, write_manifest
from p4.contract import Disposition
from p4.harness import run_episode
from p4.runbooks.adaptive import Adaptive
from p4.sim.oracle import score
from p4.sim.scenario import make_scenario

from .common import ci_str, load_trials, md_table, write_section

PRIORS = ((1.0, 1.0), (1.0, 3.0), (3.0, 1.0), (2.0, 2.0), (1.0, 9.0), (9.0, 1.0))
SEEDS = range(100000, 100200)
CONFIG = CONFIGS / "primary.toml"


class _Factory:
    def __init__(self, c, prior, late_cancel):
        self.c, self.prior, self.late_cancel = c, prior, late_cancel

    def __call__(self):
        return Adaptive(c=self.c, prior=self.prior, late_cancel=self.late_cancel)


def task(args):
    cell, prior = args
    c, lc = cell.factors["c"], cell.factors["late_cancel"]
    rows = []
    for seed in SEEDS:
        scenario = make_scenario(seed, cell.scenario_cfg)
        ep = run_episode(scenario, "adaptive", cell.fault_cfg, seed, cell.k, c=c, late_cancel=lc,
                         runbook_factory=_Factory(c, prior, lc))
        final = {}
        for e in ep.log:
            if e["type"] in ("CANCELLED", "CAPTURED"):
                final.setdefault(e["uid"], e["type"])
        fc = sum(1 for en in ep.report.entries
                 if en.state == Disposition.CLOSED_CANCELLED and final.get(en.uid) != "CANCELLED")
        rows.append({"cell_id": cell.cell_id, "seed": seed, "prior_a": prior[0], "prior_b": prior[1],
                     "loss": score(ep.log, scenario, c)["loss"], "false_closure": fc})
    return rows


def section(df: pd.DataFrame, primary: pd.DataFrame) -> tuple[str, int]:
    ref = primary[primary["seed"].isin(SEEDS)].set_index(["cell_id", "seed", "policy"])["loss"]
    cellf = primary.drop_duplicates("cell_id").set_index("cell_id")[["fault", "slack", "k", "n"]]
    mismatch = 0
    rows = []
    for (cid, a, b), g in df.groupby(["cell_id", "prior_a", "prior_b"], sort=False):
        g = g.set_index("seed").sort_index()
        p6 = ref.loc[[(cid, s, "adaptive") for s in g.index]].to_numpy()
        p5 = ref.loc[[(cid, s, "lm_reconcile") for s in g.index]].to_numpy()
        if (a, b) == (1.0, 1.0):
            mismatch += int((g["loss"].to_numpy() != p6).sum())
        d6 = bootstrap_ci(g["loss"].to_numpy() - p6, label=f"{cid}|rho{a},{b}-p6")
        d5 = bootstrap_ci(g["loss"].to_numpy() - p5, label=f"{cid}|rho{a},{b}-p5")
        f = cellf.loc[cid]
        rows.append({"cell": f"{f['fault']}/{f['slack']}/k{f['k']}/n{f['n']}", "prior": f"Beta({a:g},{b:g})",
                     "prior mean": f"{a / (a + b):.2f}", "− P6 Beta(1,1)": ci_str(d6["mean"], d6["lo"], d6["hi"]),
                     "− P5": ci_str(d5["mean"], d5["lo"], d5["hi"]), "FC": int(g["false_closure"].sum())})
    text = "\n".join([
        "## 5. P6 ρ̂ prior probe (no P6 code change)", "",
        (f"P6 run with `Adaptive(prior=(a, b))` via `runbook_factory` on the tight, faulted primary cells, "
        f"{len(SEEDS)} dev seeds (100000–100199). Paired loss differences vs primary-500's P6 (Beta(1, 1)) and "
        "P5 on the same seeds; unadjusted 95% bootstrap CIs; `*` excludes 0. FC = false closures (must be 0). "
        "The prior mean is P6's assumed P(a timed-out cancel was applied); realized shares are in §3/§4."), "",
        md_table(pd.DataFrame(rows)), "",
        f"Sanity: Beta(1, 1) rows differing from primary-500's P6: {mismatch} (must be 0).", ""])
    return text, mismatch


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(prog="python -m analysis.g1.rho_prior")
    ap.add_argument("--primary", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--section-out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--reuse", action="store_true",
                    help="only rebuild the section from an existing --out/trials.csv (no runs)")
    args = ap.parse_args(argv)
    out = Path(args.out)
    if args.reuse:
        text, mismatch = section(pd.read_csv(out / "trials.csv"), load_trials(args.primary))
        print(write_section(Path(args.section_out), "5_rho_prior", text))
        return 0 if mismatch == 0 else 1
    if out.exists() and any(out.iterdir()):
        print(f"{out} exists and is not empty", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    cfg = load_config(CONFIG)
    cells = [c for c in cfg.cells if c.factors["slack"] == "tight" and c.factors["fault"] != "none"]
    manifest = base_manifest(REPO, CONFIG, ["-m", "analysis.g1.rho_prior", *argv])
    manifest.update({"experiment": "g1_rho_prior", "partition": "dev",
                     "seeds": {"first": SEEDS[0], "last": SEEDS[-1], "count": len(SEEDS)},
                     "priors": [list(p) for p in PRIORS], "cells": [c.cell_id for c in cells],
                     "end": None})
    write_manifest(out / "manifest.json", manifest)
    with mp.get_context("spawn").Pool(args.workers) as pool:
        rows = [r for rs in pool.map(task, [(c, p) for c in cells for p in PRIORS]) for r in rs]
    df = pd.DataFrame(rows)
    df.to_csv(out / "trials.csv", index=False, quoting=csv.QUOTE_MINIMAL)
    text, mismatch = section(df, load_trials(args.primary))
    manifest["end"] = now_iso()
    write_manifest(out / "manifest.json", manifest)
    print(write_section(Path(args.section_out), "5_rho_prior", text))
    print(json.dumps({"runs": len(df), "false_closures": int(df["false_closure"].sum()),
                      "beta11_mismatch": mismatch}))
    return 0 if mismatch == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
