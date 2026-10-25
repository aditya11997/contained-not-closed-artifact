"""PH7 (post hoc, exploratory; docs/POSTHOC.md): multi-crash resumption table.

    python -m analysis.posthoc.multicrash --run results/dev/posthoc-multicrash --out paper/generated/posthoc

Per (variant, cell, policy): runs, runs crashed once/twice, false closures, false exposures,
duplicate effects, failed invariants (incl. escaped exceptions). A P4-P6 pair holds iff all four
are 0 and >= MIN_TWICE runs crashed twice. P3b's false closures are measurements.
"""
import argparse
import json
from pathlib import Path

import pandas as pd

ASSURED = ("lm_verify", "lm_reconcile", "adaptive")
MIN_TWICE = 100
INVARIANTS = ("integrity_problems", "duplicate_effects", "report_malformed", "unsupported_evidence",
              "report_errors")


def table(runs: pd.DataFrame) -> pd.DataFrame:
    runs = runs.copy()
    runs["error"] = runs["error"].fillna("")
    num = ["false_closure", "false_exposure", *INVARIANTS]
    runs[num] = runs[num].apply(pd.to_numeric, errors="coerce").fillna(0)
    runs["failed"] = (runs["error"] != "") | (runs[list(INVARIANTS)].sum(axis=1) > 0)
    rows = []
    for (variant, cell, policy), g in runs.groupby(["variant", "cell", "policy"], sort=False):
        row = {"variant": variant, "cell": cell, "policy": policy, "runs": len(g),
               "crashed_once": int((g["crashes"] == 1).sum()), "crashed_twice": int((g["crashes"] == 2).sum()),
               "false_closures": int(g["false_closure"].sum()), "false_exposures": int(g["false_exposure"].sum()),
               "duplicate_effects": int(g["duplicate_effects"].sum()), "failed_invariants": int(g["failed"].sum()),
               "escaped_exceptions": int((g["error"] != "").sum())}
        clean = row["false_closures"] == row["false_exposures"] == row["duplicate_effects"] \
            == row["failed_invariants"] == 0
        row["holds"] = ("holds" if clean and row["crashed_twice"] >= MIN_TWICE else
                        "FAILS" if not clean else "too few double crashes") if policy in ASSURED else ""
        rows.append(row)
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m analysis.posthoc.multicrash", description=__doc__.splitlines()[0])
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    manifest = json.loads((args.run / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("partition") != "dev":
        raise SystemExit("PH7 runs are dev only")
    t = table(pd.read_csv(args.run / "crash_runs.csv", dtype={"cell": str, "error": str}))
    args.out.mkdir(parents=True, exist_ok=True)
    t.to_csv(args.out / "multicrash.csv", index=False)
    pd.set_option("display.width", 250)
    s = manifest["seeds"]
    print(f"EXPLORATORY (dev seeds {s['first']}-{s['last']})")
    print(t.to_string(index=False))
    a = t[t["policy"].isin(ASSURED)]
    print(f"\nP4-P6 pairs: {len(a)}; holds {int((a['holds'] == 'holds').sum())}; "
          f"FAILS {int((a['holds'] == 'FAILS').sum())}; too few double crashes "
          f"{int((a['holds'] == 'too few double crashes').sum())}; min crashed twice {int(a['crashed_twice'].min())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
