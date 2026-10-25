"""Post-hoc macros (docs/POSTHOC.md; exploratory, not pre-registered) from the committed CSVs.

    python -m analysis.posthoc.macros --posthoc paper/generated/posthoc \
        --contrasts paper/generated/contrasts_primary.csv --out paper/generated/posthoc.tex

Reads only the tables the post-hoc scripts already wrote (no second analysis) and writes
`\\PH<ID>…` macros, every one prefixed by its analysis ID:
- PH2 (p3b_*.csv): P3/P3b/P5 false closures and shares of CLOSED_CANCELLED per faulty level;
  over the faulty cells, how many have a 95% CI below / above 0 for P3b - P3 and P5 - P3b, and
  the min and max mean.
- PH3 (timeout_contrasts.csv): P4 - P3 at H2's cells (verdicts.H2_CELL) per fault and m (mean,
  CI); per m and (k, n), the faulty cells where P5 - P3 has its CI below 0.
- PH5 (p4_vs_p3_*.csv): for each faulty cell where P4 beats P3 (mean P4 - P3 < 0): the mean, the
  "recovered by re-send" part and its share of the mean, and the mean over the seeds where P3
  had no false closure, with its CI.
- PH6 (contrasts_primary.csv): the Bonferroni-level CI of P4 - P3 at H2's cells, and the family size.
- PH7 (multicrash.csv): P4-P6 pairs that hold, runs, min double crashes and violation totals;
  P3b's false closures and failed invariants.
- PH8 (scheduler_k.csv): for k > 1, per (k, n), contrast and fault level (and over all levels):
  the min and max mean of mh - lm_ack and mh - lm_reconcile, with the CI and slack of that cell.
"""
import argparse
from pathlib import Path

import pandas as pd

from analysis.posthoc.multicrash import ASSURED
from analysis.tables import fmt_int, fmt_num, fmt_rate, numbers_tex, token
from analysis.verdicts import FAULTY, H2_CELL, H2_CONTRAST, cell_rows

P3, P3B, P4, P5, P2 = "lm_ack", "lm_ack_retry", "lm_verify", "lm_reconcile", "mh"


def _pair(contrast: str) -> str:
    a, b = contrast.split("-")
    return token(a) + token(b)


def _excludes(g: pd.DataFrame) -> tuple[int, int]:
    return int((g["ci_hi"] < 0).sum()), int((g["ci_lo"] > 0).sum())


def ph2(fc: pd.DataFrame, contrasts: pd.DataFrame) -> dict[str, str]:
    out = {}
    for r in fc[fc["fault"].isin(FAULTY)].itertuples(index=False):
        name = f"{token(r.policy)}{token(r.fault)}"
        out[f"PHTwoFC{name}"] = fmt_int(r.false_closures)
        out[f"PHTwoFCShare{name}"] = fmt_rate(r.fc_share_of_closed_cancelled)
    faulty = contrasts[contrasts["fault"].isin(FAULTY)]
    for contrast in (f"{P3B}-{P3}", f"{P5}-{P3B}"):
        g = faulty[faulty["contrast"] == contrast]
        below, above = _excludes(g)
        pre = f"PHTwo{_pair(contrast)}"
        out[f"{pre}CellsBelow"], out[f"{pre}CellsAbove"] = str(below), str(above)
        out[f"{pre}Cells"] = str(len(g))
        out[f"{pre}Min"], out[f"{pre}Max"] = fmt_num(g["mean_diff"].min()), fmt_num(g["mean_diff"].max())
    return out


def ph3(contrasts: pd.DataFrame) -> dict[str, str]:
    out = {}
    h2 = f"{P4}-{P3}"
    for (m, fault), g in contrasts[contrasts["contrast"] == h2].groupby(["m", "fault"], sort=False):
        if fault not in FAULTY:
            continue
        for r in cell_rows(g, **H2_CELL).itertuples(index=False):
            name = f"PHThree{_pair(h2)}{token(fault)}M{token(m)}"
            out[name], out[f"{name}Lo"], out[f"{name}Hi"] = fmt_num(r.mean_diff), fmt_num(r.ci_lo), fmt_num(r.ci_hi)
    p5 = contrasts[(contrasts["contrast"] == f"{P5}-{P3}") & contrasts["fault"].isin(FAULTY)]
    for (m, k, n), g in p5.groupby(["m", "k", "n"], sort=False):
        name = f"PHThree{_pair(f'{P5}-{P3}')}K{token(k)}N{token(n)}M{token(m)}"
        out[f"{name}CellsBelow"] = str(_excludes(g)[0])
        out[f"{name}Cells"] = str(len(g))
    return out


def ph5(attribution: pd.DataFrame, split: pd.DataFrame) -> dict[str, str]:
    out = {}
    key = ["cell_id"]
    t = attribution.merge(split[key + ["p3_fc0_mean", "p3_fc0_ci_lo", "p3_fc0_ci_hi"]], on=key, validate="1:1")
    better = t[t["mean_diff"] < 0]
    out["PHFiveCellsVerifyBetter"] = str(len(better))
    out["PHFiveCells"] = str(len(t))
    for r in better.itertuples(index=False):
        cell = f"{token(r.fault)}{token(r.slack)}K{token(r.k)}N{token(r.n)}"
        out[f"PHFiveDiff{cell}"] = fmt_num(r.mean_diff)
        out[f"PHFiveResend{cell}"] = fmt_num(r.recovered_resend_per_seed)
        out[f"PHFiveResendShare{cell}"] = fmt_rate(r.recovered_resend_per_seed / r.mean_diff)
        out[f"PHFiveOther{cell}"] = fmt_num(r.other_per_seed)
        out[f"PHFiveNoFCDiff{cell}"] = fmt_num(r.p3_fc0_mean)
        out[f"PHFiveNoFCLo{cell}"], out[f"PHFiveNoFCHi{cell}"] = fmt_num(r.p3_fc0_ci_lo), fmt_num(r.p3_fc0_ci_hi)
    return out


def ph6(primary: pd.DataFrame) -> dict[str, str]:
    out = {"PHSixFamilySize": str(len(primary))}
    rows = primary[primary["contrast"] == H2_CONTRAST]
    for fault in FAULTY:
        for r in cell_rows(rows, fault=fault, **H2_CELL).itertuples(index=False):
            name = f"{_pair(H2_CONTRAST)}{token(fault)}"
            out[f"PHSixBonfLo{name}"], out[f"PHSixBonfHi{name}"] = fmt_num(r.bonf_ci_lo), fmt_num(r.bonf_ci_hi)
    return out


def ph7(multicrash: pd.DataFrame) -> dict[str, str]:
    a = multicrash[multicrash["policy"].isin(ASSURED)]
    out = {"PHSevenPairs": str(len(a)), "PHSevenPairsHold": str(int((a["holds"] == "holds").sum())),
           "PHSevenRuns": fmt_int(a["runs"].sum()), "PHSevenCrashedTwice": fmt_int(a["crashed_twice"].sum()),
           "PHSevenMinCrashedTwice": fmt_int(a["crashed_twice"].min())}
    for col, name in (("false_closures", "FalseClosures"), ("false_exposures", "FalseExposures"),
                      ("duplicate_effects", "DuplicateEffects"), ("failed_invariants", "FailedInvariants")):
        out[f"PHSeven{name}"] = fmt_int(a[col].sum())
    b = multicrash[multicrash["policy"] == P3B]
    out[f"PHSeven{token(P3B)}Runs"] = fmt_int(b["runs"].sum())
    out[f"PHSeven{token(P3B)}FalseClosures"] = fmt_int(b["false_closures"].sum())
    out[f"PHSeven{token(P3B)}FailedInvariants"] = fmt_int(b["failed_invariants"].sum())
    return out


def ph8(table: pd.DataFrame) -> dict[str, str]:
    out = {}
    t = table[table["k"].astype(str) != "inf"]
    t = t[t["k"].astype(int) > 1]          # PH8's question is about the k > 1 scheduler
    for (k, n, contrast), g in t.groupby(["k", "n", "contrast"], sort=False):
        for level, sub in [("All", g)] + [(token(f), g[g["fault"] == f]) for f in g["fault"].unique()]:
            for which, idx in (("Min", sub["mean_diff"].idxmin()), ("Max", sub["mean_diff"].idxmax())):
                r = sub.loc[idx]
                name = f"PHEight{_pair(contrast)}{which}{{}}{level}K{token(k)}N{token(n)}"
                out[name.format("")] = fmt_num(r["mean_diff"])
                out[name.format("Lo")], out[name.format("Hi")] = fmt_num(r["ci_lo"]), fmt_num(r["ci_hi"])
                out[name.format("Slack")] = str(r["slack"])
    return out


def build(posthoc: Path, contrasts: Path) -> dict[str, str]:
    read = lambda name: pd.read_csv(posthoc / name, dtype={"cell_id": str})
    macros = {}
    macros.update(ph2(read("p3b_false_closures.csv"), read("p3b_contrasts.csv")))
    macros.update(ph3(read("timeout_contrasts.csv")))
    macros.update(ph5(read("p4_vs_p3_attribution.csv"), read("p4_vs_p3_split.csv")))
    macros.update(ph6(pd.read_csv(contrasts, dtype={"cell_id": str})))
    macros.update(ph7(read("multicrash.csv")))
    macros.update(ph8(read("scheduler_k.csv")))
    return macros


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m analysis.posthoc.macros", description=__doc__.splitlines()[0])
    ap.add_argument("--posthoc", type=Path, required=True, help="directory of the post-hoc CSVs")
    ap.add_argument("--contrasts", type=Path, required=True, help="contrasts_primary.csv (PH6 columns)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    macros = build(args.posthoc, args.contrasts)
    args.out.write_text(numbers_tex(macros, "Post hoc, exploratory (docs/POSTHOC.md); not pre-registered",
                                    source="analysis/posthoc/macros.py"), encoding="utf-8")
    print(f"wrote {args.out}: {len(macros)} macros")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
