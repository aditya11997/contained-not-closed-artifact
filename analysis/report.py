"""Regenerate every table, macro and figure from experiment run directories.

    python -m analysis.report --results results/holdout --out paper/generated --figs paper/figs \
        [--conformance results/conformance/stripe-<UTC time> ...]
    python -m analysis.report --results results/dev/primary --out results/dev/primary/analysis

--results is one run directory (it has manifest.json) or a directory of them. Runs are
recognised by the experiment name in their manifest: primary, secondary, negative_control,
scaling, crash_matrix (H5, analysis/crash_matrix.py); others get the generic contrast and
false-closure tables. Output under paper/ is
refused unless every run's manifest says partition = holdout, so dev numbers never reach the
paper.

It also computes the verdict of every pre-registered hypothesis (H1-H5, C1) from its rule in
docs/PREREG.md §4 and the §9 negligible-false-closure condition (analysis/verdicts.py), applies
the §8 gate (a P4-P6 contract violation labels the verdicts pre-fix and makes H1 or H5 "not
supported (pre-fix)"; any other checker not-ok run blocks the rest), prints them, and writes
verdicts.json and verdicts.md. C1 needs --conformance with every C1 attempt (run directories,
any order); without it C1 is "not evaluated". After a deviation, --pre-fix-verdicts adds the
superseded report's verdicts alongside the post-fix ones (and `\\VerdictPreFix…` macros).
--narrow also writes each contrast table as a 5-column `contrasts_<name>_narrow.tex` for a
one-column float (same rows and numbers). The crash matrix (H5) is also written as a per-policy
table, crash_matrix.tex, and the faulty primary cells as Table T4, contrasts_faulty_tex.tex.
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from p4 import schema

from . import crash_matrix, figures, stats, stripe_mapping, tables, verdicts

REPO = Path(__file__).resolve().parents[1]
PAPER = REPO / "paper"
MATCH = ("fault", "slack", "k", "n", "score", "jitter", "c")


def discover(results: Path) -> dict[str, Path]:
    dirs = [results] if (results / "manifest.json").is_file() else \
        sorted(p.parent for p in results.glob("*/manifest.json"))
    if not dirs:
        raise ValueError(f"no run directories (with manifest.json) under {results}")
    runs = {}
    for d in dirs:
        name = json.loads((d / "manifest.json").read_text(encoding="utf-8"))["experiment"]
        if name in runs:
            raise ValueError(f"two runs of {name!r}: {runs[name]} and {d}")
        runs[name] = d
    return runs


def _manifest(run: Path) -> dict:
    return json.loads((run / "manifest.json").read_text(encoding="utf-8"))


def _inside(path: Path, parent: Path) -> bool:
    return path.resolve() == parent.resolve() or parent.resolve() in path.resolve().parents


def check_paper_output(runs: dict[str, Path], *outs: Path) -> None:
    if any(_inside(o, PAPER) for o in outs):
        bad = {n: _manifest(d)["partition"] for n, d in runs.items() if _manifest(d)["partition"] != "holdout"}
        if bad:
            raise ValueError(f"refusing to write paper outputs from non-holdout runs: {bad}")


def run_macros(name: str, run: Path) -> dict[str, str]:
    summary = json.loads((run / "checker_summary.json").read_text(encoding="utf-8"))
    m = _manifest(run)
    t = tables.token(name)
    return {f"NSeeds{t}": f"{m['seeds']['count']:,}".replace(",", "{,}"),
            f"NRuns{t}": f"{summary['runs']:,}".replace(",", "{,}"),
            f"NCheckerNotOk{t}": str(summary["not_ok"]),
            f"NChainBroken{t}": str(summary["counts"]["chain_broken"])}


def contrast_macros(table: pd.DataFrame) -> dict[str, str]:
    out = {}
    for _, r in table.iterrows():
        name = f"{tables.token(r['a'])}{tables.token(r['b'])}{tables.cell_token(r)}"
        out[f"Diff{name}"] = tables.fmt_num(r["mean_diff"])
        out[f"DiffLo{name}"] = tables.fmt_num(r["ci_lo"])
        out[f"DiffHi{name}"] = tables.fmt_num(r["ci_hi"])
        if "p_holm" in table:
            out[f"PHolm{name}"] = tables.fmt_p(r["p_holm"])
        if "holm_ci_lo" in table:
            out[f"HolmLo{name}"] = tables.fmt_num(r["holm_ci_lo"])
            out[f"HolmHi{name}"] = tables.fmt_num(r["holm_ci_hi"])
    if "p_holm" in table:
        for (a, b), g in table.groupby(["a", "b"], sort=False):
            pre = f"{tables.token(a)}{tables.token(b)}"
            out[f"NHolmLower{pre}"] = str(int((g["holm_reject"] & (g["mean_diff"] < 0)).sum()))
            out[f"NHolmHigher{pre}"] = str(int((g["holm_reject"] & (g["mean_diff"] > 0)).sum()))
            out[f"NCells{pre}"] = str(len(g))
    return out


def fc_macros(fc: pd.DataFrame, prefix: str = "") -> dict[str, str]:
    out = {}
    agg = fc.groupby(["policy", "fault"], sort=False)[["false_closures", "runs_with_fc", "payments"]].sum()
    for (policy, fault), r in agg.iterrows():
        name = f"{prefix}{tables.token(policy)}{tables.token(fault)}"
        out[f"FC{name}"] = f"{int(r['false_closures']):,}".replace(",", "{,}")
        out[f"FCRuns{name}"] = f"{int(r['runs_with_fc']):,}".replace(",", "{,}")
        out[f"FCRate{name}"] = tables.fmt_rate(r["false_closures"] / r["payments"])
    return out


def fc_share_macros(trials: pd.DataFrame, policy: str = "lm_ack") -> dict[str, str]:
    """H1 and PREREG §9: the policy's false closures as a share of its CLOSED_CANCELLED reports per
    faulty level, summed over the grid's cells (as verdicts.negligible_fc), with Wilson 95% bounds;
    and the faulty cells in which it false-closes at all (as verdicts.h1)."""
    out = {}
    t = tables.token(policy)
    p = trials[(trials["policy"] == policy) & trials["fault"].isin(verdicts.FAULTY)]
    for fault in verdicts.FAULTY:
        g = p[p["fault"] == fault]
        fc, closed = int(g["false_closure"].sum()), int(g["closed_cancelled"].sum())
        lo, hi = stats.wilson(fc, closed)
        f = tables.token(fault)
        out[f"FCShare{t}{f}"] = tables.fmt_rate(fc / closed if closed else float("nan"))
        out[f"FCShareLo{t}{f}"] = tables.fmt_rate(lo)
        out[f"FCShareHi{t}{f}"] = tables.fmt_rate(hi)
        out[f"FCClosedCancelled{t}{f}"] = tables.fmt_int(closed)
    per_cell = p.groupby("cell_id")["false_closure"].sum()
    out[f"NFaultyCells{t}FC"] = str(int((per_cell > 0).sum()))
    out["NFaultyCells"] = str(len(per_cell))
    return out


def h2_summary_macros(table: pd.DataFrame, macros: dict[str, str]) -> dict[str, str]:
    """H2: the smallest Holm-level CI lower bound of P4 - P3 over the H2 cells (verdicts.H2_CELL)
    per faulty level. It must equal the `HolmLo…` macro of the cell it comes from."""
    out = {}
    rows = table[table["contrast"] == verdicts.H2_CONTRAST]
    for fault in verdicts.FAULTY:
        cells = verdicts.cell_rows(rows, fault=fault, **verdicts.H2_CELL)
        if cells.empty:
            continue
        r = cells.loc[cells["holm_ci_lo"].idxmin()]
        name = f"HTwoMinHolmLo{tables.token(fault)}"
        out[name] = tables.fmt_num(r["holm_ci_lo"])
        cell = f"HolmLo{tables.token(r['a'])}{tables.token(r['b'])}{tables.cell_token(r)}"
        if macros.get(cell) != out[name]:
            raise ValueError(f"\\{name} = {out[name]} but \\{cell} = {macros.get(cell)}")
    return out


def h3_summary_macros(table: pd.DataFrame) -> dict[str, str]:
    """H3 (P6 - P5) per k: seeds whose paired difference is not 0, and cells whose unadjusted 95%
    CI excludes 0."""
    out = {}
    rows = table[table["contrast"] == verdicts.H3_CONTRAST]
    for k, g in rows.groupby("k", sort=False):
        t = f"K{tables.token(k)}"
        out[f"HThreeNonzeroSeeds{t}"] = tables.fmt_int((g["n_seeds"] - g["ties"]).sum())
        out[f"HThreeSeeds{t}"] = tables.fmt_int(g["n_seeds"].sum())
        out[f"HThreeCellsExcludeZero{t}"] = str(int(((g["ci_lo"] > 0) | (g["ci_hi"] < 0)).sum()))
        out[f"HThreeCells{t}"] = str(len(g))
    return out


def late_cancel_ablation(secondary: pd.DataFrame, primary: pd.DataFrame) -> pd.DataFrame:
    """Paired loss difference (late_cancel = false) − (true) per seed for P4-P6, matching each
    ablation cell to the late_cancel = true cell with the same other factors (in the secondary
    run itself, e.g. the j = 6 block, or else in the primary run)."""
    rows = []
    abl = secondary[secondary["late_cancel"] == False]
    for cell in stats.cells(abl).itertuples(index=False):
        key = pd.Series({f: getattr(cell, f) for f in MATCH})
        ref = pd.DataFrame()
        for pool in (secondary, primary):
            ref = pool[(pool["late_cancel"] == True) & (pool[list(MATCH)] == key).all(axis=1)]
            if not ref.empty:
                break
        if ref.empty:
            continue
        left = abl[abl["cell_id"] == cell.cell_id]
        for pol in stats.EVIDENCE_BOUNDED:
            d = stats.paired_across(left, ref, pol)
            r = stats.bootstrap_ci(d.to_numpy(), label=f"{cell.cell_id}|late_cancel|{pol}")
            w, t, lo = stats.win_tie_loss(d)
            rows.append({**{f: getattr(cell, f) for f in MATCH}, "policy": pol, "n_seeds": len(d),
                         "mean_diff": r["mean"], "ci_lo": r["lo"], "ci_hi": r["hi"], "p": r["p"],
                         "a_better": w, "ties": t, "a_worse": lo})
    return pd.DataFrame(rows)


def _check_rows(run: Path) -> pd.DataFrame:
    """One row per checked run of an experiments.run directory, for verdicts.classify."""
    other = [k for k in schema.INTEGRITY_COUNTS if k != "duplicate_effects"] + ["report_malformed",
                                                                                "unsupported_evidence"]
    rows = []
    with (run / "checker_verdicts.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            c = r["verdict"]["counts"]
            rows.append({"policy": r["policy"], "checker_ok": int(r["verdict"]["ok"]),
                         **{k: c[k] for k in verdicts.CONTRACT_COUNTS},
                         "other_problems": sum(c[k] for k in other)})
    return pd.DataFrame(rows, columns=["policy", "checker_ok", *verdicts.CONTRACT_COUNTS, "other_problems"])


def _crash_rows(run: Path) -> pd.DataFrame:
    r = crash_matrix.load_runs(run)
    other = (r["integrity_problems"] - r["duplicate_effects"] + r["report_malformed"] + r["unsupported_evidence"]
             + r["report_errors"])
    return r.assign(other_problems=other)[["policy", "checker_ok", *verdicts.CONTRACT_COUNTS, "other_problems"]]


def build(results: Path, out: Path, figs: Path, conformance=None, pre_fix_verdicts: Path | None = None,
          narrow: bool = False) -> list[Path]:
    runs = discover(results)
    check_paper_output(runs, out, figs)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    macros: dict[str, str] = {}
    trials = {name: stats.load_trials(d) for name, d in runs.items() if name not in ("scaling", "crash_matrix")}

    found: dict[str, dict] = {}

    def write(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        written.append(path)

    def write_contrasts(stem: str, table: pd.DataFrame, caption: str, label: str) -> None:
        write(out / f"{stem}.tex", tables.contrast_tex(table, caption, label))
        if narrow:
            write(out / f"{stem}_narrow.tex", tables.contrast_tex_narrow(table, caption, f"{label}-narrow"))

    for name, run in runs.items():
        macros.update(run_macros(name, run))
    for name, tr in trials.items():
        if name == "negative_control":
            neg = stats.negative_control_table(tr)
            neg.to_csv(out / "negative_control.csv", index=False)
            macros["NegCtrlSeedsEqual"] = f"{int(neg['seeds_all_equal'].sum()):,}".replace(",", "{,}")
            macros["NegCtrlSeeds"] = f"{int(neg['seeds'].sum()):,}".replace(",", "{,}")
            found["H4"] = verdicts.h4(neg)
            continue
        primary = name == "primary"
        table = stats.contrast_table(tr, holm_family=primary)
        table.to_csv(out / f"contrasts_{name}.csv", index=False)
        write_contrasts(f"contrasts_{name}", table,
                        f"Paired loss differences, {name} grid" + (" (Holm over this table)." if primary else
                                                                   " (not in the Holm family)."),
                        f"tab:contrasts-{name.replace('_', '-')}")
        fc = stats.false_closure_table(tr)
        fc.to_csv(out / f"false_closure_{name}.csv", index=False)
        write(out / f"false_closure_{name}.tex", tables.false_closure_tex(
            fc, f"False closures by policy and fault level, {name} grid.", f"tab:fc-{name.replace('_', '-')}"))
        if primary:
            found["H2"] = verdicts.h2(table)
            macros.update(contrast_macros(table))
            macros.update(h2_summary_macros(table, macros))
            macros.update(fc_macros(fc))
            macros.update(fc_share_macros(tr))
            # Table T4: the faulty cells only, with the Holm-level CIs that decide H2
            write(out / "contrasts_faulty_tex.tex", tables.contrast_faulty_tex(
                table, (verdicts.H2_CONTRAST, *(f"{a}-{b}" for a, b in stats.PRIMARY_CONTRASTS
                                        if f"{a}-{b}" != verdicts.H2_CONTRAST)),
                verdicts.FAULTY, stats.ALPHA, "tab:contrasts-faulty"))
            # H3 (P6 - P5): pre-registered, secondary, not in the Holm family.
            h3 = stats.contrast_table(tr, contrasts=stats.SECONDARY_PRIMARY_GRID_CONTRASTS, holm_family=False)
            h3.to_csv(out / "contrasts_primary_secondary.csv", index=False)
            write_contrasts("contrasts_primary_secondary", h3,
                            "Paired loss differences P6$-$P5, primary grid (secondary; not in the Holm family).",
                            "tab:contrasts-primary-secondary")
            macros.update(contrast_macros(h3))
            macros.update(h3_summary_macros(h3))
            found["H3"] = verdicts.h3(h3)
            vs_p5 = stats.contrast_table(tr, contrasts=(("lm_ack", "lm_reconcile"), ("lm_verify", "lm_reconcile"),
                                                        ("adaptive", "lm_reconcile")), holm_family=False)
            written.append(figures.f3_paired_vs_p5(vs_p5, figs / "f3_paired_vs_p5.pdf"))
            written.append(figures.f4_false_closure(fc, figs / "f4_false_closure.pdf"))
            written.append(figures.f5_frontier(fc, figs / "f5_frontier.pdf"))
        else:  # non-primary contrasts are in the tables only (cell tokens omit score/jitter/c)
            macros.update(fc_macros(fc, prefix=tables.token(name)))
    if "secondary" in trials and "primary" in trials:
        abl = late_cancel_ablation(trials["secondary"], trials["primary"])
        abl.to_csv(out / "ablation_late_cancel.csv", index=False)
        for _, r in abl.iterrows():
            name = (f"AblLateCancel{tables.token(r['policy'])}{tables.token(r['fault'])}"
                    f"{tables.token(r['slack'])}J{tables.token(r['jitter'])}")
            macros[name] = tables.fmt_num(r["mean_diff"])

    if "crash_matrix" in runs:
        crash = crash_matrix.cell_table(crash_matrix.load_runs(runs["crash_matrix"]))
        crash.to_csv(out / "crash_matrix.csv", index=False)
        write(out / "crash_matrix.tex", crash_matrix.table_tex(crash))
        h5_counts = crash_matrix.h5_counts(crash)
        macros.update({f"Hfive{k}": str(v) for k, v in h5_counts.items()})
        found["H5"] = verdicts.h5(h5_counts)

    timing = None
    if "scaling" in runs:
        timing = pd.read_csv(runs["scaling"] / "timing.csv", dtype={"cell_id": str}).merge(
            stats.load_trials(runs["scaling"])[["cell_id", "seed", "policy", "n"]], on=["cell_id", "seed", "policy"])
        med = timing.groupby(["policy", "n"])["wall_ms"].median().reset_index()
        med.to_csv(out / "scaling.csv", index=False)
        for r in med.itertuples(index=False):
            macros[f"WallMs{tables.token(r.policy)}N{tables.token(int(r.n))}"] = tables.fmt_num(r.wall_ms, 2)
    neg = pd.read_csv(out / "negative_control.csv") if "negative_control" in trials else None
    if neg is not None or timing is not None:
        written.append(figures.f6_control_scaling(neg, timing, figs / "f6_control_scaling.pdf"))

    h1_trials = dict(trials)
    if "scaling" in runs:
        h1_trials["scaling"] = stats.load_trials(runs["scaling"])
    found["H1"] = verdicts.h1(h1_trials)
    if conformance:
        attempts = [stripe_mapping.attempt(Path(d)) for d in sorted(conformance, key=lambda d: Path(d).name)]
        found["C1"] = verdicts.c1_attempts(attempts)
        deciding = next((a for a in attempts if a["name"] == found["C1"].get("deciding")), None)
        if deciding is not None:
            macros.update({f"Stripe{k}": str(v) for k, v in deciding["counts"].items()})
    missing = {"H2": "no primary run", "H3": "no primary run", "H4": "no negative_control run",
               "H5": "no crash_matrix run", "C1": "no conformance run given (--conformance)"}
    final = [found.get(h) or verdicts.not_evaluated(h, missing[h]) for h in verdicts.ORDER]
    final.append(verdicts.negligible_fc(trials.get("primary")))
    checks = {name: verdicts.classify(_crash_rows(d) if name == "crash_matrix" else _check_rows(d))
              for name, d in runs.items()}
    final = verdicts.gate(final, checks)
    if pre_fix_verdicts is not None:
        old = json.loads(Path(pre_fix_verdicts).read_text(encoding="utf-8"))
        final = verdicts.with_pre_fix(final, old)
        for v in old:
            macros["VerdictPreFix" + verdicts.MACRO[v["hypothesis"]].removeprefix("Verdict")] = v["verdict"]
    for v in final:
        macros[verdicts.MACRO[v["hypothesis"]]] = v["verdict"]
    write(out / "verdicts.json", json.dumps(final, indent=1) + "\n")
    write(out / "verdicts.md", verdicts.render(final))

    header = "Runs: " + ", ".join(f"{n} (commit {(_manifest(d)['git']['commit'] or '?')[:12]}, "
                                  f"{_manifest(d)['partition']})" for n, d in runs.items())
    write(out / "numbers.tex", tables.numbers_tex(macros, header))
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m analysis.report", description=__doc__.splitlines()[0])
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True, help="tables, CSVs and numbers.tex")
    ap.add_argument("--figs", help="figure directory (default: <out>/figs)")
    ap.add_argument("--conformance", type=Path, nargs="+",
                    help="every C1 Stripe conformance attempt (run directories)")
    ap.add_argument("--pre-fix-verdicts", type=Path,
                    help="verdicts.json of the superseded pre-fix report, reported alongside (PREREG §8)")
    ap.add_argument("--narrow", action="store_true",
                    help="also write 5-column contrasts_<name>_narrow.tex tables for one-column floats")
    args = ap.parse_args(argv)
    out = Path(args.out)
    try:
        written = build(Path(args.results), out, Path(args.figs) if args.figs else out / "figs",
                        args.conformance, args.pre_fix_verdicts, args.narrow)
    except (OSError, ValueError, KeyError) as exc:
        print(f"report: {exc}", file=sys.stderr)
        return 2
    for p in written:
        print(p)
    print("\nPre-registered verdicts (docs/PREREG.md §4):")
    print((out / "verdicts.md").read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
