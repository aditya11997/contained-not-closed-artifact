"""Experiment runner (WP4).

    python -m experiments.run --config configs/primary.toml --partition dev --n-seeds 500 \
        --out results/dev/primary

For each cell of the config and each seed, the scenario is built once; each policy then runs
on a fresh sink, transport, clock and journal (`p4.harness.run_episode`), so every policy
gets the identical scenario, observations, k and fault realization. Each run is exported and
graded by the independent checker in a separate process (`check_runs`, batched per chunk of
seeds), always with the policy that produced it.

Outputs in --out (which must not exist or be empty):
- trials.csv: one row per (cell, seed, policy); outcomes come from the checker's verdict
  (loss is cross-checked against p4.sim.oracle). Byte-identical for the same config, seeds
  and code, whatever --workers is.
- timing.csv: wall_ms per (cell, seed, policy) for the episode alone (not deterministic, so
  kept out of trials.csv).
- checker_verdicts.jsonl, checker_summary.json, manifest.json.
- runs/<cell_id>.tar.gz: every exported run (events, report, truth, journal) unless
  --no-keep-runs.

Holdout seeds are refused unless experiments.guard allows them (prereg-v1 tag, frozen
PREREG.md, clean tree). Exit status: 0 if every checker verdict is ok, 1 if any is not (all
rows are still written; none is dropped), 2 on bad arguments or a refused holdout run.
"""
import argparse
import csv
import dataclasses
import gzip
import io
import json
import multiprocessing as mp
import os
import shutil
import sys
import tarfile
import time
from pathlib import Path

from p4 import schema
from p4.contract import Disposition
from p4.harness import check_runs, export_run, run_episode
from p4.sim.oracle import score
from p4.sim.scenario import PARTITIONS, make_scenario

from .config import (
    FACTORS,
    FAULTS_FILE,
    PRESETS_FILE,
    REPO,
    load_config,
    presets_for_manifest,
)
from .guard import HoldoutRefused, check_holdout_allowed
from .manifest import base_manifest, now_iso, write_manifest

CHUNK_SEEDS = 100   # seeds per checker batch
METRICS = ("loss", "suspect_captured", "legit_cancelled", "false_closure", "false_exposure",
           "closed_cancelled", "closed_exposure", "unresolved_pending", "unresolved_unknown",
           "duplicate_effects", "n_cancel_calls", "n_status_calls", "n_replays", "end_of_run",
           "time_to_full_disposition", "checker_ok")
TRIAL_COLUMNS = ("cell_id",) + FACTORS + ("seed", "policy") + METRICS
TIMING_COLUMNS = ("cell_id", "seed", "policy", "wall_ms")
SUMMARY_COUNTS = schema.INTEGRITY_COUNTS + schema.REPORT_COUNTS
CLOSED = (Disposition.CLOSED_CANCELLED, Disposition.CLOSED_EXPOSURE)


def episode_metrics(ep) -> dict:
    """Call counts and timing from the run itself (the checker supplies the outcomes)."""
    closed = [e.reported_at for e in ep.report.entries if e.state in CLOSED]
    full = len(closed) == len(ep.report.entries)
    return {
        "n_cancel_calls": sum(r.op == "cancel" for r in ep.trace),   # includes replays
        "n_status_calls": sum(r.op == "status" for r in ep.trace),
        "n_replays": sum(j["kind"] == "intent" and j["op"] == "replay" for j in ep.journal),
        "end_of_run": ep.end_of_run,
        "time_to_full_disposition": max(closed, default=0) if full else "",
    }


def trial_row(cell, seed, policy, metrics, verdict) -> dict:
    counts, loss = verdict["counts"], verdict["loss"]
    return {"cell_id": cell.cell_id, **cell.factors, "seed": seed, "policy": policy,
            "loss": loss["loss"], "suspect_captured": loss["suspect_captured_value"],
            "legit_cancelled": loss["legitimate_cancelled_value"],
            **{k: counts[k] for k in ("false_closure", "false_exposure", "closed_cancelled",
                                      "closed_exposure", "unresolved_pending", "unresolved_unknown",
                                      "duplicate_effects")},
            **metrics, "checker_ok": verdict["ok"]}


class _DeterministicTar:
    """gzip'd tar with fixed mtimes and owners, so archives do not depend on when they ran."""

    def __init__(self, path: Path):
        self._raw = path.open("wb")
        self._gz = gzip.GzipFile(fileobj=self._raw, mode="wb", mtime=0)
        self._tar = tarfile.open(fileobj=self._gz, mode="w")  # noqa: SIM115

    def add(self, path: Path, arcname: str) -> None:
        data = path.read_bytes()
        info = tarfile.TarInfo(arcname)
        info.size, info.mtime, info.mode = len(data), 0, 0o644
        self._tar.addfile(info, io.BytesIO(data))

    def close(self) -> None:
        self._tar.close()
        self._gz.close()
        self._raw.close()


def run_cell(task) -> dict:
    """Run every (seed, policy) of one cell; returns its rows, verdicts and timings."""
    cell, policies, seeds, out_dir, keep_runs, timeout_factor = task
    work = Path(out_dir) / "_work" / cell.cell_id
    tar = _DeterministicTar(Path(out_dir) / "runs" / f"{cell.cell_id}.tar.gz") if keep_runs else None
    rows, verdict_rows, timings = [], [], []
    c, late_cancel = cell.factors["c"], cell.factors["late_cancel"]
    try:
        for start in range(0, len(seeds), CHUNK_SEEDS):
            chunk = seeds[start:start + CHUNK_SEEDS]
            exports, meta = [], []
            for seed in chunk:
                scenario = make_scenario(seed, cell.scenario_cfg)
                for policy in policies:
                    t0 = time.perf_counter()
                    ep = run_episode(scenario, policy, cell.fault_cfg, seed, cell.k, c=c,
                                     late_cancel=late_cancel, timeout_factor=timeout_factor)
                    wall_ms = (time.perf_counter() - t0) * 1000
                    exports.append(export_run(ep, scenario, c, work / str(seed) / policy))
                    meta.append((seed, policy, episode_metrics(ep), score(ep.log, scenario, c)["loss"],
                                 wall_ms))
            verdicts = check_runs(exports, [m[1] for m in meta], work / "verdicts.jsonl")
            for (seed, policy, metrics, oracle_loss, wall_ms), v in zip(meta, verdicts):
                if v["loss"]["loss"] != oracle_loss:
                    raise RuntimeError(f"cell {cell.cell_id} seed {seed} {policy}: checker loss "
                                       f"{v['loss']['loss']} != oracle loss {oracle_loss}")
                rows.append(trial_row(cell, seed, policy, metrics, v))
                verdict = {k: x for k, x in v.items() if k != "id"}
                verdict_rows.append({"cell_id": cell.cell_id, "seed": seed, "policy": policy,
                                     "verdict": verdict})
                timings.append({"cell_id": cell.cell_id, "seed": seed, "policy": policy,
                                "wall_ms": round(wall_ms, 3)})
            if tar is not None:
                for seed in chunk:
                    for policy in policies:
                        for f in (schema.EVENTS_FILE, schema.REPORT_FILE, schema.TRUTH_FILE,
                                  schema.JOURNAL_FILE):
                            tar.add(work / str(seed) / policy / f, f"{cell.cell_id}/{seed}/{policy}/{f}")
            shutil.rmtree(work)
    finally:
        if tar is not None:
            tar.close()
    return {"cell_id": cell.cell_id, "rows": rows, "verdicts": verdict_rows, "timings": timings}


def _fmt(x) -> str:
    return repr(x) if isinstance(x, float) else str(x)


def _csv_writer(path: Path, columns):
    fh = path.open("w", encoding="utf-8", newline="")
    writer = csv.writer(fh, lineterminator="\n")
    writer.writerow(columns)
    return fh, writer


def select_seeds(partition: str, n_seeds: int, seed_start: int | None) -> list[int]:
    part = PARTITIONS[partition]
    start = part.start if seed_start is None else seed_start
    seeds = list(range(start, start + n_seeds))
    if n_seeds < 1 or seeds[0] not in part or seeds[-1] not in part:
        raise ValueError(f"seeds {start}..{start + n_seeds - 1} are not all in the {partition} "
                         f"partition {part.start}..{part.stop - 1}")
    return seeds


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="python -m experiments.run", description=__doc__.splitlines()[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--partition", required=True, choices=tuple(PARTITIONS))
    ap.add_argument("--n-seeds", type=int, help="default: the config's n_seeds")
    ap.add_argument("--seed-start", type=int, help="first seed (default: start of the partition)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--faults", help="fault presets file (dev only; default: configs/faults.toml)")
    ap.add_argument("--presets", help="jitter/score presets file (dev only; default: configs/presets.toml)")
    ap.add_argument("--timeout-factor", type=int, default=1,
                    help="dev only, post hoc (docs/POSTHOC.md PH3): timeouts reach the caller after "
                         "this many times the call latency (default 1, as in every frozen result)")
    ap.add_argument("--no-keep-runs", dest="keep_runs", action="store_false",
                    help="do not archive the exported runs (smoke tests)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = parse_args(argv)
    try:
        prereg_commit = None
        if args.partition == "holdout":
            if args.faults or args.presets or args.timeout_factor != 1:
                raise HoldoutRefused("--faults/--presets/--timeout-factor are dev-only; holdout uses "
                                     "the frozen settings")
            prereg_commit = check_holdout_allowed(REPO)   # before anything is built or written
        faults_path = Path(args.faults) if args.faults else FAULTS_FILE
        presets_path = Path(args.presets) if args.presets else PRESETS_FILE
        cfg = load_config(args.config, faults_path=faults_path, presets_path=presets_path)
        seeds = select_seeds(args.partition, args.n_seeds or cfg.n_seeds, args.seed_start)
        if args.workers < 1:
            raise ValueError("--workers must be >= 1")
        if args.timeout_factor < 1:
            raise ValueError("--timeout-factor must be >= 1")
    except HoldoutRefused as exc:
        print(f"run: holdout refused: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError) as exc:
        print(f"run: {exc}", file=sys.stderr)
        return 2

    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        print(f"run: {out} exists and is not empty; results are never overwritten", file=sys.stderr)
        return 2
    (out / "runs").mkdir(parents=True, exist_ok=True)

    manifest = base_manifest(REPO, cfg.path, ["-m", "experiments.run", *argv])
    manifest.update({
        "experiment": cfg.name,
        "partition": args.partition,
        "prereg_tag_commit": prereg_commit,
        "seeds": {"first": seeds[0], "last": seeds[-1], "count": len(seeds)},
        "policies": list(cfg.policies),
        "workers": args.workers,
        "keep_runs": args.keep_runs,
        "presets_sha256": presets_for_manifest(faults_path, presets_path),
        "timeout_factor": args.timeout_factor,
        "cells": [{"cell_id": c.cell_id, "factors": c.factors,
                   "fault": dataclasses.asdict(c.fault_cfg),
                   "scenario": dataclasses.asdict(c.scenario_cfg)} for c in cfg.cells],
        "end": None,
    })
    write_manifest(out / "manifest.json", manifest)

    tasks = [(cell, cfg.policies, seeds, str(out), args.keep_runs, args.timeout_factor)
             for cell in cfg.cells]
    totals = dict.fromkeys(SUMMARY_COUNTS, 0)
    by_cell, n_runs, n_not_ok = {}, 0, 0
    trials_fh, trials = _csv_writer(out / "trials.csv", TRIAL_COLUMNS)
    timing_fh, timing = _csv_writer(out / "timing.csv", TIMING_COLUMNS)
    with trials_fh, timing_fh, (out / "checker_verdicts.jsonl").open("w", encoding="utf-8") as vfh:
        if args.workers == 1:
            results = map(run_cell, tasks)
            pool = None
        else:
            pool = mp.get_context("spawn").Pool(min(args.workers, len(tasks)))
            results = pool.imap(run_cell, tasks)   # ordered: output is independent of workers
        try:
            for res in results:
                for row in res["rows"]:
                    trials.writerow([_fmt(row[c]) for c in TRIAL_COLUMNS])
                for row in res["timings"]:
                    timing.writerow([_fmt(row[c]) for c in TIMING_COLUMNS])
                not_ok = 0
                for v in res["verdicts"]:
                    vfh.write(json.dumps(v, sort_keys=True, separators=(",", ":")) + "\n")
                    for k in SUMMARY_COUNTS:
                        totals[k] += v["verdict"]["counts"][k]
                    not_ok += not v["verdict"]["ok"]
                n_runs += len(res["verdicts"])
                n_not_ok += not_ok
                by_cell[res["cell_id"]] = {"runs": len(res["verdicts"]), "not_ok": not_ok}
                print(f"cell {res['cell_id']}: {len(res['verdicts'])} runs, {not_ok} not ok", flush=True)
        finally:
            if pool is not None:
                pool.close()
                pool.join()
    shutil.rmtree(out / "_work", ignore_errors=True)
    if not args.keep_runs:
        shutil.rmtree(out / "runs", ignore_errors=True)

    summary = {"runs": n_runs, "not_ok": n_not_ok, "counts": totals, "by_cell": by_cell}
    (out / "checker_summary.json").write_text(json.dumps(summary, sort_keys=True, indent=1) + "\n",
                                              encoding="utf-8")
    manifest.update({"end": now_iso(), "checker": {"runs": n_runs, "not_ok": n_not_ok}})
    write_manifest(out / "manifest.json", manifest)
    print(f"{cfg.name}: {len(cfg.cells)} cells x {len(seeds)} seeds x {len(cfg.policies)} policies = "
          f"{n_runs} runs; checker not ok: {n_not_ok}; "
          + ", ".join(f"{k}={totals[k]}" for k in SUMMARY_COUNTS))
    return 0 if n_not_ok == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
