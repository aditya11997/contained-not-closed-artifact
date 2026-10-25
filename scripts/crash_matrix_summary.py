"""Print the WP3 crash-matrix summary (dev seeds only): per cell x policy, how many runs
crashed, how many failed the independent checker, and false closures with and without the
crash. Rows for `lm_ack_trust_orphans` are the superseded P3 restart rule, kept as a test
fixture for comparison. Usage: python scripts/crash_matrix_summary.py [n_seeds] [restart_delay]
(defaults 25 and 0)."""
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests" / "helpers")]

from crash_matrix import (
    CELLS,
    FAULTS,
    FIXTURES,
    KS,
    LATE_SCENARIO,
    POLICIES,
    SCENARIO,
    cell_label,
    run_matrix,
)


def main() -> None:
    n_seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    restart_delay = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    policies = POLICIES + tuple(FIXTURES)
    rows = defaultdict(lambda: defaultdict(int))
    with tempfile.TemporaryDirectory() as tmp:
        for r in run_matrix(Path(tmp), n_seeds, policies=policies, baseline=True,
                            restart_delay=restart_delay):
            row = rows[(r.label, r.policy)]
            row["runs"] += 1
            if r.fired is None:
                continue
            row["crashed"] += 1
            row["checker_not_ok"] += not r.verdict["ok"]
            row["dup_effects"] += r.verdict["counts"]["duplicate_effects"]
            row["fc_crash"] += r.verdict["counts"]["false_closure"]
            row["fc_no_crash"] += r.baseline_false_closure
            row["runs_fc_up"] += r.verdict["counts"]["false_closure"] > r.baseline_false_closure
    print(f"Dev seeds {n_seeds} x faults {list(FAULTS)} x k {list(KS)}; scenario n={SCENARIO.n}, "
          f"slack={SCENARIO.slack}, jitter={SCENARIO.jitter.kind}({SCENARIO.jitter.j}); "
          f"late_cancel cells jitter={LATE_SCENARIO.jitter.kind}({LATE_SCENARIO.jitter.j}); "
          f"restart_delay={restart_delay}.")
    print("fc_crash / fc_no_crash: false closures summed over crashed runs, with the crash and "
          "for the same runs without it. runs_fc_up: crashed runs with more false closures than without.\n")
    cols = ("runs", "crashed", "checker_not_ok", "dup_effects", "fc_crash", "fc_no_crash", "runs_fc_up")
    print("| cell | policy | " + " | ".join(cols) + " |")
    print("|---|---|" + "---|" * len(cols))
    for point, target in CELLS:
        for policy in policies:
            row = rows.get((cell_label(point, target), policy))
            if row:
                print(f"| {cell_label(point, target)} | {policy} | " + " | ".join(str(row[c]) for c in cols) + " |")


if __name__ == "__main__":
    main()
