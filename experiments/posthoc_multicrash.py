"""PH7 (post hoc, exploratory; docs/POSTHOC.md): multi-crash resumption. Dev seeds only.

    python -m experiments.posthoc_multicrash --seed-start 112000 --n-seeds 500 \
        --out results/dev/posthoc-multicrash

The crash matrix (experiments/crash_matrix.py, frozen, imported unchanged) crashes once per
episode. Here every run's first crash is the crash-matrix hook of its cell, and the restarted
instance may crash once more:
  same       at the same crash point, on its first call for the payment of the first crash
  different  at the next crash point (cyclic), on its first call for any payment
Policies P4-P6 and the post-hoc P3b; P3 is not run. A run whose crash points are never reached
finishes and is graded; an exception escaping the episode is a failed run (recorded, not
dropped). Outputs in --out: crash_runs.csv and manifest.json.
"""
import argparse
import csv
import multiprocessing as mp
import os
import shutil
import sys
import traceback
from pathlib import Path

from p4 import schema
from p4.contract import validate
from p4.harness import run_episode
from p4.runbooks import CRASH_POINTS, SimulatedCrash
from p4.sim.scenario import make_scenario

from . import crash_matrix as cm
from .config import REPO
from .manifest import base_manifest, now_iso, write_manifest
from .run import select_seeds

POLICIES = ("lm_verify", "lm_reconcile", "adaptive", "lm_ack_retry")
VARIANTS = ("same", "different")
COLUMNS = ("variant", "policy", "cell", "seed", "fault", "k", "crashes", "first_crash", "second_crash",
           "restarts", "error", "checker_ok", "false_closure", "false_exposure", "duplicate_effects",
           "unsupported_evidence", "report_malformed", "integrity_problems", "report_errors")


def next_point(point: str) -> str:
    return CRASH_POINTS[(CRASH_POINTS.index(point) + 1) % len(CRASH_POINTS)]


class TwoCrashes:
    """The crash-matrix hook of a cell, then at most one more crash in the restarted instance."""

    def __init__(self, point: str, target: str, variant: str):
        self.first = cm.CrashAt(point, target)
        self.variant = variant
        self.first_rb = None
        self.second = None   # (point, op, uid, t) once fired

    def __call__(self, point, action, reply, rb):
        if self.first.fired is None:
            try:
                self.first(point, action, reply, rb)
            except SimulatedCrash:
                self.first_rb = rb   # kept alive, compared by identity
                raise
            return
        if self.second is not None or rb is self.first_rb:
            return
        first_point, _, first_uid, _ = self.first.fired
        if self.variant == "same":
            if action.uid != first_uid or point != first_point:
                return
        elif point != next_point(first_point):
            return
        self.second = (point, action.op, action.uid, rb.clock.now())
        raise SimulatedCrash(self.second)


def run_one(variant, policy, point, target, seed, fault, k, journal_path) -> dict:
    scenario = make_scenario(seed, cm.scenario_for(target))
    hook = TwoCrashes(point, target, variant)
    row = {"variant": variant, "policy": policy, "cell": cm.cell_label(point, target), "seed": seed,
           "fault": fault, "k": k}
    try:
        ep = run_episode(scenario, policy, cm.FAULTS[fault], seed, k, crash_hook=hook,
                         journal_path=journal_path, journal_sync=False)
    except Exception as exc:  # noqa: BLE001  (an escaped exception is a failed run, kept)
        row.update(crashes=(hook.first.fired is not None) + (hook.second is not None),
                   first_crash=repr(hook.first.fired), second_crash=repr(hook.second), restarts="",
                   error=f"{type(exc).__name__}: {exc}".replace("\n", " ")[:300], checker_ok=0,
                   **dict.fromkeys(COLUMNS[12:], ""))
        traceback.clear_frames(exc.__traceback__)
        return row
    v = cm.grade(ep, scenario, policy)
    c = v["counts"]
    row.update(crashes=(hook.first.fired is not None) + (hook.second is not None),
               first_crash=repr(hook.first.fired) if hook.first.fired else "",
               second_crash=repr(hook.second) if hook.second else "", restarts=ep.restarts, error="",
               checker_ok=int(v["ok"]), false_closure=c["false_closure"], false_exposure=c["false_exposure"],
               duplicate_effects=c["duplicate_effects"], unsupported_evidence=c["unsupported_evidence"],
               report_malformed=c["report_malformed"],
               integrity_problems=sum(c[name] for name in schema.INTEGRITY_COUNTS),
               report_errors=len(validate(ep.report, [p.uid for p in scenario.payments])))
    return row


def failed(row: dict) -> bool:
    """An invariant every policy must keep, or an escaped exception (false closures measured)."""
    return bool(row["error"] or row["integrity_problems"] or row["duplicate_effects"]
                or row["report_malformed"] or row["unsupported_evidence"] or row["report_errors"])


def _task(task) -> list[dict]:
    variant, policy, point, target, seeds, work = task
    tmp = Path(work) / f"{variant}-{policy}-{cm.cell_label(point, target)}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        return [run_one(variant, policy, point, target, seed, fault, k, tmp / f"{seed}-{fault}-{k}.jsonl")
                for seed in seeds for fault in cm.FAULTS for k in cm.KS]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def tasks(seeds, work) -> list[tuple]:
    return [(variant, policy, point, target, seeds, str(work)) for variant in VARIANTS
            for policy in POLICIES for point, target in cm.CELLS if cm.applies(policy, target)]


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(prog="python -m experiments.posthoc_multicrash",
                                 description=__doc__.splitlines()[0])
    ap.add_argument("--n-seeds", type=int, required=True)
    ap.add_argument("--seed-start", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    args = ap.parse_args(argv)
    try:
        seeds = select_seeds("dev", args.n_seeds, args.seed_start)   # dev only, never holdout
    except ValueError as exc:
        print(f"posthoc_multicrash: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        print(f"posthoc_multicrash: {out} exists and is not empty", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    manifest = base_manifest(REPO, Path(__file__), ["-m", "experiments.posthoc_multicrash", *argv])
    manifest.update({"experiment": "posthoc_multicrash", "partition": "dev",
                     "seeds": {"first": seeds[0], "last": seeds[-1], "count": len(seeds)},
                     "policies": list(POLICIES), "variants": list(VARIANTS),
                     "cells": [cm.cell_label(p, t) for p, t in cm.CELLS], "faults": list(cm.FAULTS),
                     "k": list(cm.KS), "workers": args.workers, "end": None})
    write_manifest(out / "manifest.json", manifest)
    jobs = tasks(seeds, out / "_work")
    n_runs = n_failed = 0
    with (out / "crash_runs.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, COLUMNS, lineterminator="\n")
        writer.writeheader()
        pool = None if args.workers == 1 else mp.get_context("spawn").Pool(min(args.workers, len(jobs)))
        try:
            results = map(_task, jobs) if pool is None else pool.imap(_task, jobs)   # ordered
            for (variant, policy, point, target, _, _), rows in zip(jobs, results):
                writer.writerows(rows)
                bad = sum(failed(r) for r in rows)
                n_runs, n_failed = n_runs + len(rows), n_failed + bad
                twice = sum(r["crashes"] == 2 for r in rows)
                print(f"{variant}/{cm.cell_label(point, target)}/{policy}: {len(rows)} runs, "
                      f"{twice} crashed twice, {bad} failed", flush=True)
        finally:
            if pool is not None:
                pool.close()
                pool.join()
    shutil.rmtree(out / "_work", ignore_errors=True)
    manifest.update({"end": now_iso(), "checker": {"runs": n_runs, "failed": n_failed}})
    write_manifest(out / "manifest.json", manifest)
    print(f"posthoc_multicrash: {len(jobs)} (variant, policy, cell) x {len(seeds)} seeds = {n_runs} runs; "
          f"failed: {n_failed}")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
