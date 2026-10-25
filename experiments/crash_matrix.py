"""Crash matrix (WP3; hypothesis H5): kill the remediator at a crash point, restart a fresh
runbook from the durable journal on the same sink, finish, and grade the run with the
independent checker.

    python -m experiments.crash_matrix --partition dev --n-seeds 25 --out results/dev/crash
    python -m experiments.crash_matrix --partition holdout --n-seeds 2000 --out results/holdout/crash_matrix

Cells (point x target):
  a-d  first_cancel  the first cancel before the last nominal deadline, at
                     (a) before_send (b) after_send (c) after_reply (d) after_journal
  e    outage        the first crash point of any call while a transport outage is active
  sweep_read a-d     the first status read of the post-deadline accounting sweep
  late_cancel a-d    the first post-deadline cancel (late_cancel after a PENDING sweep read)
P3 has no sweep, so the sweep cells apply to P4-P6 only. Every (policy, cell) runs every seed
under faults {none, high, crash_outage} and k in {1, 3}; a run whose crash point is never
reached finishes uncrashed and is still graded (`crashed` = 0).

The scenario, fault and k settings below are fixed parts of H5 (docs/PREREG.md), not presets
of the main grid. Holdout seeds need experiments.guard, as for experiments.run.

Outputs in --out (must not exist or be empty): crash_runs.csv (one row per run), checker_summary.json,
manifest.json. Exit status 0 if no run has an integrity problem, a duplicate effect, a malformed
report or unsupported evidence; 1 otherwise (every row is still written); 2 on bad arguments or
a refused holdout run. False closures are measurements, not failures, here: H5 is decided by
analysis/crash_matrix.py.
"""
import argparse
import csv
import dataclasses
import json
import multiprocessing as mp
import os
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from checker.check import check_run
from p4 import schema
from p4.contract import validate
from p4.harness import run_episode, truth_dict
from p4.runbooks import CRASH_POINTS, SimulatedCrash
from p4.sim.faults import FaultConfig, load_fault_presets
from p4.sim.scenario import PARTITIONS, JitterConfig, ScenarioConfig, make_scenario

from .config import REPO
from .guard import HoldoutRefused, check_holdout_allowed
from .manifest import base_manifest, now_iso, write_manifest
from .run import select_seeds

POLICIES = ("lm_ack", "lm_verify", "lm_reconcile", "adaptive")
ASSURED = ("lm_verify", "lm_reconcile", "adaptive")
LETTERS = dict(zip(CRASH_POINTS, "abcd"))
CELLS = ([(p, "first_cancel") for p in CRASH_POINTS] + [("any", "outage")]
         + [(p, "sweep_read") for p in CRASH_POINTS] + [(p, "late_cancel") for p in CRASH_POINTS])
SWEEP_TARGETS = ("sweep_read", "late_cancel")

SCENARIO = ScenarioConfig(n=8, slack="tight", jitter=JitterConfig("uniform", 6))
# Late cancels need captures well after the last nominal deadline; with j = 6 only ~6% of
# runs reach one, so the late_cancel cells use wider jitter.
LATE_SCENARIO = ScenarioConfig(n=8, slack="tight", jitter=JitterConfig("uniform", 15))
# Outages frequent enough that most runs meet one (not a main-grid preset).
OUTAGE = FaultConfig("crash_outage", outage_rate=0.1, outage_length=3, outage_commit_prob=0.5)
FAULTS = {"none": FaultConfig(), "high": load_fault_presets()["high"], "crash_outage": OUTAGE}
KS = (1, 3)
C = 0.5

RUN_COLUMNS = ("policy", "cell", "seed", "fault", "k", "crashed", "crash_op", "crash_t", "restarts",
               "checker_ok", "false_closure", "false_exposure", "duplicate_effects",
               "unsupported_evidence", "report_malformed", "chain_broken", "integrity_problems", "report_errors",
               "recovered_results", "max_cancel_intents")


def cell_label(point: str, target: str) -> str:
    if target == "first_cancel":
        return LETTERS[point]
    if target == "outage":
        return "e"
    return f"{target}_{LETTERS[point]}"


def applies(policy: str, target: str) -> bool:
    return policy in ASSURED or target not in SWEEP_TARGETS


class CrashAt:
    """Crash hook: raise SimulatedCrash once, at the first call matching (point, target)."""

    def __init__(self, point: str, target: str):
        self.point, self.target = point, target
        self.fired = None  # (point, op, uid, t) once fired

    def __call__(self, point, action, reply, rb):
        if self.fired or (self.point != "any" and point != self.point):
            return
        if self.target == "first_cancel":
            hit = action.op == "cancel" and not rb.post_deadline
        elif self.target == "sweep_read":
            hit = action.op == "status" and rb.post_deadline
        elif self.target == "late_cancel":
            hit = action.op == "cancel" and rb.post_deadline
        else:
            hit = rb.transport.in_outage(rb.clock.now())
        if hit:
            self.fired = (point, action.op, action.uid, rb.clock.now())
            raise SimulatedCrash(self.fired)


@dataclass(frozen=True)
class CrashRun:
    policy: str
    label: str
    seed: int
    fault: str
    k: int
    fired: tuple | None
    restarts: int
    verdict: dict
    baseline_false_closure: int | None   # the same run without a crash (if asked for)
    report_errors: tuple
    recovered_results: int        # journal results written on restart for unknown outcomes
    max_cancel_intents: int       # most cancel intents journaled for one payment


def grade(ep, scenario, policy: str, c: float = C) -> dict:
    lines = [json.loads(json.dumps(dict(e))) for e in ep.log]
    report = {"schema": "p4.report/v1", **ep.report.to_dict()}
    return check_run(lines, report, truth_dict(scenario, c, ep.log), policy)


def scenario_for(target: str):
    return LATE_SCENARIO if target == "late_cancel" else SCENARIO


def run_cell(policy, point, target, seed, fault, k, journal_path, sync=False,
             baseline=False, restart_delay=0, *, graded_as=None, runbook_factory=None) -> CrashRun:
    """One crash run. `graded_as`/`runbook_factory` let tests run a fixture runbook that
    reports as `graded_as`."""
    scenario = make_scenario(seed, scenario_for(target))
    graded_as = graded_as or policy
    hook = CrashAt(point, target)
    ep = run_episode(scenario, graded_as, FAULTS[fault], seed, k, crash_hook=hook,
                     journal_path=journal_path, journal_sync=sync, restart_delay=restart_delay,
                     runbook_factory=runbook_factory)
    base_fc = None
    if baseline:
        base = run_episode(scenario, graded_as, FAULTS[fault], seed, k)
        base_fc = grade(base, scenario, graded_as)["counts"]["false_closure"]
    cancels = Counter(j["uid"] for j in ep.journal if j["kind"] == "intent" and j["op"] == "cancel")
    return CrashRun(
        policy=policy, label=cell_label(point, target), seed=seed, fault=fault, k=k,
        fired=hook.fired, restarts=ep.restarts, verdict=grade(ep, scenario, graded_as),
        baseline_false_closure=base_fc,
        report_errors=tuple(validate(ep.report, [p.uid for p in scenario.payments])),
        recovered_results=sum(j["kind"] == "result" and j["recovered"] for j in ep.journal),
        max_cancel_intents=max(cancels.values(), default=0))


def run_matrix(tmp_dir, seeds, policies=POLICIES, cells=CELLS, sync=False, baseline=False,
               restart_delay=0, factories=None):
    """Yield a CrashRun for every applicable (policy, cell, seed, fault, k). `factories` maps a
    fixture name to (policy it reports as, runbook factory)."""
    factories = factories or {}
    for policy in policies:
        graded_as, factory = factories.get(policy, (policy, None))
        for point, target in cells:
            if not applies(graded_as, target):
                continue
            for seed in seeds:
                for fault in FAULTS:
                    for k in KS:
                        path = Path(tmp_dir) / f"{policy}-{cell_label(point, target)}-{seed}-{fault}-{k}.jsonl"
                        yield run_cell(policy, point, target, seed, fault, k, path, sync, baseline,
                                       restart_delay, graded_as=graded_as, runbook_factory=factory)


def run_row(r: CrashRun) -> dict:
    c = r.verdict["counts"]
    return {"policy": r.policy, "cell": r.label, "seed": r.seed, "fault": r.fault, "k": r.k,
            "crashed": int(r.fired is not None), "crash_op": r.fired[1] if r.fired else "",
            "crash_t": r.fired[3] if r.fired else "", "restarts": r.restarts,
            "checker_ok": int(r.verdict["ok"]), "false_closure": c["false_closure"],
            "false_exposure": c["false_exposure"], "duplicate_effects": c["duplicate_effects"],
            "unsupported_evidence": c["unsupported_evidence"], "report_malformed": c["report_malformed"],
            "chain_broken": c["chain_broken"], "integrity_problems": sum(c[name] for name in schema.INTEGRITY_COUNTS),
            "report_errors": len(r.report_errors), "recovered_results": r.recovered_results,
            "max_cancel_intents": r.max_cancel_intents}


def failed(row: dict) -> bool:
    """A run that breaks an invariant every policy must keep (false closures are measured)."""
    return bool(row["integrity_problems"] or row["duplicate_effects"] or row["report_malformed"]
                or row["unsupported_evidence"] or row["report_errors"])


def _task(task) -> list[dict]:
    policy, point, target, seeds, work = task
    tmp = Path(work) / f"{policy}-{cell_label(point, target)}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        return [run_row(r) for r in run_matrix(tmp, seeds, policies=(policy,), cells=[(point, target)])]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def tasks(seeds, work) -> list[tuple]:
    return [(policy, point, target, seeds, str(work))
            for policy in POLICIES for point, target in CELLS if applies(policy, target)]


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="python -m experiments.crash_matrix", description=__doc__.splitlines()[0])
    ap.add_argument("--partition", required=True, choices=tuple(PARTITIONS))
    ap.add_argument("--n-seeds", type=int, required=True)
    ap.add_argument("--seed-start", type=int, help="first seed (default: start of the partition)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = parse_args(argv)
    try:
        prereg_commit = check_holdout_allowed(REPO) if args.partition == "holdout" else None
        seeds = select_seeds(args.partition, args.n_seeds, args.seed_start)
        if args.workers < 1:
            raise ValueError("--workers must be >= 1")
    except HoldoutRefused as exc:
        print(f"crash_matrix: holdout refused: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"crash_matrix: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        print(f"crash_matrix: {out} exists and is not empty; results are never overwritten", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)

    manifest = base_manifest(REPO, Path(__file__), ["-m", "experiments.crash_matrix", *argv])
    manifest.update({
        "experiment": "crash_matrix", "partition": args.partition, "prereg_tag_commit": prereg_commit,
        "seeds": {"first": seeds[0], "last": seeds[-1], "count": len(seeds)},
        "policies": list(POLICIES), "cells": [cell_label(p, t) for p, t in CELLS],
        "faults": {name: dataclasses.asdict(cfg) for name, cfg in FAULTS.items()}, "k": list(KS), "c": C,
        "scenario": repr(SCENARIO), "late_scenario": repr(LATE_SCENARIO),
        "workers": args.workers, "end": None,
    })
    write_manifest(out / "manifest.json", manifest)

    jobs = tasks(seeds, out / "_work")
    n_runs = n_failed = n_crashed = 0
    summed = ("chain_broken", "integrity_problems", "duplicate_effects", "false_closure")
    counts = dict.fromkeys(summed, 0)
    by_cell = {}
    with (out / "crash_runs.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, RUN_COLUMNS, lineterminator="\n")
        writer.writeheader()
        pool = None if args.workers == 1 else mp.get_context("spawn").Pool(min(args.workers, len(jobs)))
        try:
            results = map(_task, jobs) if pool is None else pool.imap(_task, jobs)   # ordered
            for (policy, point, target, _, _), rows in zip(jobs, results):
                writer.writerows(rows)
                bad = sum(failed(r) for r in rows)
                n_runs, n_failed = n_runs + len(rows), n_failed + bad
                n_crashed += sum(r["crashed"] for r in rows)
                for r in rows:
                    for name in summed:
                        counts[name] += r[name]
                key = f"{cell_label(point, target)}/{policy}"
                by_cell[key] = {"runs": len(rows), "crashed": sum(r["crashed"] for r in rows), "not_ok": bad}
                print(f"{key}: {len(rows)} runs, {by_cell[key]['crashed']} crashed, {bad} failed", flush=True)
        finally:
            if pool is not None:
                pool.close()
                pool.join()
    shutil.rmtree(out / "_work", ignore_errors=True)
    summary = {"runs": n_runs, "not_ok": n_failed, "crashed": n_crashed,
               "counts": counts,
               "by_cell": by_cell}
    (out / "checker_summary.json").write_text(json.dumps(summary, sort_keys=True, indent=1) + "\n",
                                              encoding="utf-8")
    manifest.update({"end": now_iso(), "checker": {"runs": n_runs, "not_ok": n_failed}})
    write_manifest(out / "manifest.json", manifest)
    print(f"crash_matrix: {len(jobs)} (policy, cell) x {len(seeds)} seeds x {len(FAULTS)} faults x "
          f"{len(KS)} k = {n_runs} runs; {n_crashed} crashed; failed invariants: {n_failed}")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
