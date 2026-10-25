"""G1 follow-up: P6 (P5 + per-event re-planning) vs P5, per primary cell, on the design range
(dev 100500-100999, where P6 may be tuned) and the sanity range (dev 101000-101499, run once,
no tuning).

Criterion, fixed before the sanity range was run (docs/CHANGELOG_RESEARCH.md, G1 entry):
P6 is "at least as good as P5" on a range iff in no primary cell the unadjusted 95% percentile
bootstrap CI of the mean paired difference P6 - P5 lies entirely above 0. If the sanity range
fails it, P6 is dropped from the holdout (PI decision).

    python -m analysis.g1.p6_check --design results/dev/primary-p6b-design-100500 \
        --sanity results/dev/primary-p6b-sanity-101000 --out results/dev/primary-p6b-sanity-101000/analysis
"""
import argparse
from pathlib import Path

import pandas as pd

from analysis.stats import contrast_table

from .common import ci_str, fmt, load_trials, manifest, md_table

CONTRAST = (("adaptive", "lm_reconcile"),)


def table_for(results: Path) -> pd.DataFrame:
    t = contrast_table(load_trials(results), CONTRAST, holm_family=False)
    t["worse"] = t["ci_lo"] > 0
    t["better"] = t["ci_hi"] < 0
    return t


def passes(t: pd.DataFrame) -> bool:
    return not bool(t["worse"].any())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m analysis.g1.p6_check")
    ap.add_argument("--design", required=True)
    ap.add_argument("--sanity")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ranges = {"design": Path(args.design)}
    if args.sanity:
        ranges["sanity"] = Path(args.sanity)
    tables, lines = {}, []
    for name, path in ranges.items():
        t = table_for(path)
        t.to_csv(out / f"p6_vs_p5_{name}.csv", index=False)
        tables[name] = t
        m = manifest(path)
        lines.append(f"- {name}: `{path}` seeds {m['seeds']['first']}–{m['seeds']['last']}, commit "
                     f"{m['git']['commit'][:12]} (dirty {m['git']['dirty']}): P6 better in "
                     f"{int(t['better'].sum())} cells, worse in {int(t['worse'].sum())} of {len(t)}; "
                     f"mean over cells of the per-cell mean P6 − P5 = {fmt(t['mean_diff'].mean(), 1)}; "
                     f"criterion {'PASSED' if passes(t) else 'FAILED'}.")
    base = tables["design"][["fault", "slack", "k", "n"]].copy()
    for name, t in tables.items():
        base[f"P6−P5 {name}"] = [ci_str(r.mean_diff, r.ci_lo, r.ci_hi) for r in t.itertuples()]
        base[f"W/T/L {name}"] = [f"{r.a_better}/{r.ties}/{r.a_worse}" for r in t.itertuples()]
    text = "\n".join([
        "# P6 (P5 + per-event re-planning) vs P5 — dev design and sanity ranges", "",
        ("Per-seed paired loss differences P6 − P5 per primary cell, 500 seeds each; unadjusted 95% "
        "percentile bootstrap CI (B = 10,000); `*` = CI excludes 0. Criterion (fixed before the sanity "
        "run): no cell with the CI entirely above 0."), "", *lines, "", md_table(base), ""])
    (out / "p6_check.md").write_text(text, encoding="utf-8")
    print(text)
    return 0 if all(passes(t) for t in tables.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
