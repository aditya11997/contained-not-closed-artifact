"""H5 (crash safety) from a crash-matrix run directory (experiments/crash_matrix.py).

    python -m analysis.crash_matrix results/holdout/crash_matrix [--out DIR]

--out writes crash_matrix.csv, crash_matrix.tex (the per-policy table, `table_tex`) and
crash_matrix_h5.tex (the \\Hfive… macros, also in numbers.tex).

Per (cell, policy): runs, crashed runs, false closures, false exposures, duplicate effects and
failed invariants, summed over every run of the cell, crashed or not (all seeds x faults x k).
H5 holds for a (cell, policy) of P4-P6 iff it has 0 false closures, 0 false exposures, 0
duplicate effects, 0 failed invariants and at least MIN_CRASHED crashed runs (so a zero is not
for lack of crashes). H5 is supported
iff it holds for every (cell, policy) of P4-P6. P3 rows are reported, not part of H5.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

from analysis.tables import _tabular, fmt_int, macro, numbers_tex, short_policy

H5_POLICIES = ("lm_verify", "lm_reconcile", "adaptive")
MIN_CRASHED = 100
INVARIANTS = ("integrity_problems", "duplicate_effects", "report_malformed", "unsupported_evidence",
              "report_errors")


def load_runs(run_dir: Path) -> pd.DataFrame:
    return pd.read_csv(Path(run_dir) / "crash_runs.csv", dtype={"cell": str, "policy": str})


def cell_table(runs: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (cell, policy), g in runs.groupby(["cell", "policy"], sort=False):
        failed = int((g[list(INVARIANTS)].sum(axis=1) > 0).sum())
        row = {"cell": cell, "policy": policy, "runs": len(g), "crashed": int(g["crashed"].sum()),
               "false_closures": int(g["false_closure"].sum()),
               "false_closures_crashed": int(g.loc[g["crashed"] == 1, "false_closure"].sum()),
               "false_exposures": int(g["false_exposure"].sum()),
               "duplicate_effects": int(g["duplicate_effects"].sum()), "failed_invariants": failed,
               "checker_not_ok": int((g["checker_ok"] == 0).sum())}
        row["h5_holds"] = (row["false_closures"] == 0 and row["false_exposures"] == 0
                           and row["duplicate_effects"] == 0 and failed == 0
                           and row["crashed"] >= MIN_CRASHED) if policy in H5_POLICIES else None
        rows.append(row)
    return pd.DataFrame(rows)


def h5_counts(table: pd.DataFrame) -> dict[str, int]:
    h5 = table[table["policy"].isin(H5_POLICIES)]
    return {"CellsTotal": len(h5), "CellsHold": int(h5["h5_holds"].sum()),
            "Supported": int(len(h5) > 0 and bool(h5["h5_holds"].all())),
            "FalseClosures": int(h5["false_closures"].sum()),
            "FalseExposures": int(h5["false_exposures"].sum()),
            "DuplicateEffects": int(table["duplicate_effects"].sum()),
            "CrashedRuns": int(h5["crashed"].sum()), "MinCrashed": int(h5["crashed"].min()) if len(h5) else 0,
            "Runs": int(table["runs"].sum())}


def table_tex(table: pd.DataFrame) -> str:
    """Table T3: one row per policy, summed over its cells (all seeds x faults x k, crashed or not)."""
    rows = []
    for policy, g in table.groupby("policy", sort=False):
        held = f"{int(g['h5_holds'].sum())}/{len(g)}" if policy in H5_POLICIES else "--"
        rows.append([short_policy(policy), held,
                     f"{fmt_int(g['crashed'].sum())} ({fmt_int(g['crashed'].min())})",
                     fmt_int(g["false_closures"].sum()),
                     "/".join(fmt_int(g[c].sum()) for c in ("false_exposures", "duplicate_effects",
                                                             "failed_invariants"))])
    header = ["Policy", "Cells held", "Crashed (min)", "FC", "FE/Dup/Inv"]
    notes = ("Rows sum each policy's crash-matrix cells over all their runs, crashed or not.",
             "Crashed (min): crashed runs, and in parentheses the fewest crashed runs in one cell.",
             "FC/FE/Dup: false closures, false exposures, duplicate effects; Inv: runs with a failed invariant.",
             f"A cell holds with FC = FE = Dup = Inv = 0 and at least {MIN_CRASHED} crashed runs (H5).")
    return _tabular("lrrrr", header, rows, "Crash matrix (H5), summed over each policy's cells. P3's false "
                    "closures are measurements; P3 is not part of H5.", "tab:crash-matrix", notes=notes)


def h5_tex(run_dir: Path, table: pd.DataFrame) -> str:
    macros = dict(macro("Hfive" + k, str(v)) for k, v in h5_counts(table).items())
    return numbers_tex(macros, f"H5 crash matrix from {Path(run_dir).name}", source="analysis/crash_matrix.py")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m analysis.crash_matrix", description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--out", type=Path, help="write crash_matrix.csv, crash_matrix.tex and crash_matrix_h5.tex here")
    args = ap.parse_args(argv)
    table = cell_table(load_runs(args.run_dir))
    print(table.to_string(index=False))
    c = h5_counts(table)
    print(f"\nH5 {'SUPPORTED' if c['Supported'] else 'NOT supported'}: {c['CellsHold']} of {c['CellsTotal']} "
          f"(cell, policy) pairs of P4-P6 hold; min crashed runs per pair {c['MinCrashed']} "
          f"(need >= {MIN_CRASHED}).")
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        table.to_csv(args.out / "crash_matrix.csv", index=False)
        (args.out / "crash_matrix.tex").write_text(table_tex(table), encoding="utf-8")
        (args.out / "crash_matrix_h5.tex").write_text(h5_tex(args.run_dir, table), encoding="utf-8")
        print(f"wrote {args.out}/crash_matrix.csv, crash_matrix.tex and crash_matrix_h5.tex", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
