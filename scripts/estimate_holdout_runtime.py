"""Estimate holdout wall time per config by timing the real runners on dev seeds (never holdout).

    python scripts/estimate_holdout_runtime.py [--workers N] [--small 10] [--large 40]

Each config runs twice on dev seeds (--small and --large seeds, --no-keep-runs, into a temp
dir); the time per seed is (t_large - t_small) / (large - small), so the fixed start-up cost is
not scaled up. Estimate = t_small + (n_holdout - small) * per_seed, with n_holdout the config's
n_seeds (the crash matrix: 2,000). Prints a markdown table. It is an estimate on this machine,
not a result.
"""
import argparse
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from experiments.config import load_config

CONFIGS = ("primary", "secondary", "negative_control", "scaling")
CRASH_SEEDS = 2000


def timed(cmd) -> float:
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stdout[-2000:]}{proc.stderr[-2000:]}")
    return time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--small", type=int, default=10)
    ap.add_argument("--large", type=int, default=40)
    args = ap.parse_args()
    py = sys.executable
    jobs = []
    for name in CONFIGS:
        n = load_config(REPO / "configs" / f"{name}.toml").n_seeds
        jobs.append((name, n, lambda k, out, name=name: [
            py, "-m", "experiments.run", "--config", f"configs/{name}.toml", "--partition", "dev",
            "--n-seeds", str(k), "--out", out, "--workers", str(args.workers), "--no-keep-runs"]))
    jobs.append(("crash_matrix", CRASH_SEEDS, lambda k, out: [
        py, "-m", "experiments.crash_matrix", "--partition", "dev", "--n-seeds", str(k), "--out", out,
        "--workers", str(args.workers)]))
    print(f"Machine: {platform.platform()}, {os.cpu_count()} CPUs, --workers {args.workers}; "
          f"dev seeds {args.small} and {args.large}.\n")
    print("| config | holdout seeds | t(small) s | t(large) s | s per seed | estimate |")
    print("|---|---|---|---|---|---|")
    total = 0.0
    with tempfile.TemporaryDirectory() as tmp:
        for name, n, cmd in jobs:
            small = min(args.small, n)
            large = min(args.large, n)
            ts = timed(cmd(small, f"{tmp}/{name}-s"))
            tl = timed(cmd(large, f"{tmp}/{name}-l"))
            per = max((tl - ts) / (large - small), 0.0) if large > small else ts / small
            est = ts + (n - small) * per
            total += est
            print(f"| {name} | {n:,} | {ts:.1f} | {tl:.1f} | {per:.2f} | {est / 60:.0f} min |", flush=True)
    print(f"| **total** | | | | | **{total / 3600:.1f} h** |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
