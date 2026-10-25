"""The verdict of every pre-registered hypothesis, computed from its decision rule in
docs/PREREG.md §4. Nothing is judged by hand.

Each function takes the table its rule is stated over and returns
{"hypothesis", "verdict", "detail"}, with verdict SUPPORTED, NOT_SUPPORTED or NOT_EVALUATED (an
input is missing, which is never read as support). `gate` applies PREREG §8. A P4-P6 false
closure, false exposure or duplicate effect (a contract violation) does not block: every
verdict is still computed and labelled "(pre-fix)", and the hypothesis whose runs hold it (H1
for the experiments.run configs, H5 for the crash matrix) is "not supported (pre-fix)". Any
other checker not-ok run blocks the remaining verdicts (BLOCKED). The checker's `ok` already
treats P0-P3 false closures as measurements. `negligible_fc` is the PREREG §9 condition, reported beside the
verdicts. `analysis/report.py` calls them from the tables it builds, writes verdicts.json and
verdicts.md, adds `\\Verdict…` macros, and prints them.
"""
import pandas as pd

SUPPORTED, NOT_SUPPORTED, NOT_EVALUATED = "supported", "not supported", "not evaluated"
BLOCKED = "blocked"
MET, NOT_MET = "met", "not met"
C1_MAX_ATTEMPTS = 3              # the first run plus up to 2 retries after transient failures
NEGLIGIBLE_FC_SHARE = 0.01       # §9: P3 false closures < 1% of P3's CLOSED_CANCELLED
FAULTY = ("low", "high")
FAULT_LEVELS = ("none", "low", "high")
H1_ZERO_POLICIES = ("none", "lm_verify", "lm_reconcile", "adaptive")   # P0, P4, P5, P6
H1_RUNS = ("primary", "secondary", "negative_control", "scaling")
H2_CONTRAST = "lm_verify-lm_ack"                                     # P4 - P3
H2_CELL = {"slack": "tight", "k": 1, "n": 32}                         # at each faulty level
H3_CONTRAST = "adaptive-lm_reconcile"                                # P6 - P5
H3_CELLS_NEEDED, H3_LEVELS_NEEDED = 3, 2
ORDER = ("H1", "H2", "H3", "H4", "H5", "C1")
MACRO = {"H1": "VerdictHOne", "H2": "VerdictHTwo", "H3": "VerdictHThree", "H4": "VerdictHFour",
         "H5": "VerdictHFive", "C1": "VerdictCOne", "S9": "VerdictNegligibleFalseClosure"}


def _v(h: str, ok: bool | None, detail: str) -> dict:
    verdict = NOT_EVALUATED if ok is None else (SUPPORTED if ok else NOT_SUPPORTED)
    return {"hypothesis": h, "verdict": verdict, "detail": detail}


def not_evaluated(h: str, why: str) -> dict:
    return _v(h, None, why)


def cell_rows(df: pd.DataFrame, **factors) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    for k, v in factors.items():
        mask &= df[k].astype(str) == str(v)
    return df[mask]


def h1(trials: dict[str, pd.DataFrame]) -> dict:
    """False closures summed over all seeds per cell: P3 > 0 in every faulty primary cell, and
    the P0, P4, P5, P6 total is 0 across primary, secondary, negative_control and scaling.
    Crash-matrix runs are governed by H5."""
    missing = [n for n in H1_RUNS if n not in trials]
    if missing:
        return not_evaluated("H1", f"missing runs: {', '.join(missing)}")
    p3 = trials["primary"][(trials["primary"]["policy"] == "lm_ack") & trials["primary"]["fault"].isin(FAULTY)]
    per_cell = p3.groupby("cell_id")["false_closure"].sum()
    p3_zero = int((per_cell == 0).sum())
    totals = {n: int(t[t["policy"].isin(H1_ZERO_POLICIES)]["false_closure"].sum()) for n, t in trials.items()
              if n in H1_RUNS}
    total = sum(totals.values())
    ok = len(per_cell) > 0 and p3_zero == 0 and total == 0
    return _v("H1", ok, f"P3 false closures > 0 in {len(per_cell) - p3_zero} of {len(per_cell)} faulty primary "
                        f"cells; P0/P4/P5/P6 false closures in total: {total} ({totals})")


def h2(holm_table: pd.DataFrame) -> dict:
    """P4 - P3 Holm-level CI lower bound > 0 in both cells tight/k1/n32 x {low, high}. The
    p-values are shown for information only; they do not decide."""
    parts, ok = [], True
    for fault in FAULTY:
        row = cell_rows(holm_table[holm_table["contrast"] == H2_CONTRAST], fault=fault, **H2_CELL)
        if len(row) != 1 or "holm_ci_lo" not in row:
            return not_evaluated("H2", f"no Holm row for P4-P3 at {fault}/tight/k1/n32")
        lo = float(row["holm_ci_lo"].iloc[0])
        ok &= lo > 0
        p = f", p_holm = {float(row['p_holm'].iloc[0]):.4g} (information only)" if "p_holm" in row else ""
        parts.append(f"{fault}: holm_ci_lo = {lo:.3f}{p}")
    return _v("H2", ok, "; ".join(parts) + " (need > 0 in both)")


def h3(table: pd.DataFrame) -> dict:
    """At tight slack, a fault level counts if the unadjusted 95% CI upper bound of P6 - P5 is
    below 0 in at least 3 of that level's 4 cells (k in {1, 4} x n in {8, 32}). Supported if at
    least 2 of the 3 fault levels count."""
    rows = table[table["contrast"] == H3_CONTRAST]
    counted, parts = 0, []
    for fault in FAULT_LEVELS:
        cells = cell_rows(rows, fault=fault, slack="tight")
        cells = cells[cells["k"].astype(str).isin(("1", "4")) & cells["n"].astype(int).isin((8, 32))]
        if len(cells) != 4:
            return not_evaluated("H3", f"{fault}/tight has {len(cells)} P6-P5 cells, expected 4")
        below = int((cells["ci_hi"] < 0).sum())
        counts = below >= H3_CELLS_NEEDED
        counted += counts
        parts.append(f"{fault}: {below}/4 cells with ci_hi < 0{' (counts)' if counts else ''}")
    return _v("H3", counted >= H3_LEVELS_NEEDED,
              f"{counted} of 3 fault levels count (need {H3_LEVELS_NEEDED}); " + "; ".join(parts))


def h4(neg_table: pd.DataFrame) -> dict:
    """Every seed in every negative-control cell has exactly identical loss for P3, P4, P5, P6
    (the simulation is deterministic, so no tolerance)."""
    if neg_table is None or not len(neg_table):
        return not_evaluated("H4", "no negative_control run")
    equal, seeds = int(neg_table["seeds_all_equal"].sum()), int(neg_table["seeds"].sum())
    return _v("H4", equal == seeds, f"{equal} of {seeds} seeds equal across P3-P6 in {len(neg_table)} cells")


def h5(counts: dict | None) -> dict:
    """From analysis/crash_matrix.h5_counts."""
    if counts is None:
        return not_evaluated("H5", "no crash_matrix run")
    return _v("H5", bool(counts["Supported"]),
              f"{counts['CellsHold']} of {counts['CellsTotal']} (cell, policy) pairs of P4-P6 hold; "
              f"false closures {counts['FalseClosures']}, duplicate effects {counts['DuplicateEffects']}, "
              f"min crashed runs per pair {counts['MinCrashed']}")


def c1_attempts(attempts: list[dict]) -> dict:
    """C1 over all attempts (analysis/stripe_mapping.attempt), in chronological order. The
    attempts on the latest commit decide (after an adapter fix, the post-fix run decides).
    Among them, the first complete one (no transient failure) within C1_MAX_ATTEMPTS decides.
    If none of those is complete, C1 is not supported. Every attempt is listed."""
    if not attempts:
        return not_evaluated("C1", "no conformance run given (--conformance)")
    listing = "; ".join(f"{a['name']} (commit {(a['commit'] or '?')[:12]}, dirty {a['dirty']}, "
                        f"{'complete' if a['complete'] else 'transient' if 'TestsRun' in a['counts'] else 'no tests.jsonl'})"
                        for a in attempts)
    latest = [a for a in attempts if a["commit"] == attempts[-1]["commit"]]
    if not any("TestsRun" in a["counts"] for a in latest):
        return not_evaluated("C1", f"no attempt on the latest commit has tests.jsonl (it predates the freeze); "
                                   f"attempts: {listing}")
    deciding = next((a for a in latest[:C1_MAX_ATTEMPTS] if a["complete"]), None)
    if deciding is None:
        return _v("C1", False, f"no complete run within {C1_MAX_ATTEMPTS} attempts on commit "
                               f"{(latest[0]['commit'] or '?')[:12]}; attempts: {listing}")
    v = c1(deciding["counts"])
    v["detail"] = f"deciding run {deciding['name']}: {v['detail']}; attempts: {listing}"
    v["deciding"] = deciding["name"]
    return v


def c1(counts: dict | None) -> dict:
    """From analysis/stripe_mapping.counts of one run: every mapping-table row matched and every
    conformance test ran and passed."""
    if counts is None:
        return not_evaluated("C1", "no conformance run given (--conformance)")
    if "TestsRun" not in counts:
        return not_evaluated("C1", "the conformance run has no tests.jsonl (it predates the freeze)")
    rows_ok = counts["MappingRowsMatched"] == counts["MappingRows"]
    tests_ok = counts["TestsRun"] == counts["TestsPassed"] == counts["ConformanceTests"]
    return _v("C1", rows_ok and tests_ok,
              f"mapping rows matched {counts['MappingRowsMatched']} of {counts['MappingRows']}; "
              f"conformance tests run {counts['TestsRun']}, passed {counts['TestsPassed']}, "
              f"expected {counts['ConformanceTests']}")


def negligible_fc(primary: pd.DataFrame | None) -> dict:
    """§9: P3 false closures below 1% of P3's reported CLOSED_CANCELLED dispositions at both low
    and high faults, each summed over the primary grid's cells at that fault level."""
    if primary is None:
        return {"hypothesis": "S9", "verdict": NOT_EVALUATED, "detail": "no primary run"}
    parts, ok = [], True
    for fault in FAULTY:
        g = primary[(primary["policy"] == "lm_ack") & (primary["fault"] == fault)]
        fc, closed = int(g["false_closure"].sum()), int(g["closed_cancelled"].sum())
        share = fc / closed if closed else float("inf")
        ok &= share < NEGLIGIBLE_FC_SHARE
        parts.append(f"{fault}: {fc} of {closed} CLOSED_CANCELLED ({100 * share:.2f}%)")
    return {"hypothesis": "S9", "verdict": MET if ok else NOT_MET,
            "detail": "P3 false closures; " + "; ".join(parts) + f" (negligible iff < {100 * NEGLIGIBLE_FC_SHARE:g}% at both)"}


ASSURED = ("lm_verify", "lm_reconcile", "adaptive")   # P4-P6
CONTRACT_COUNTS = ("false_closure", "false_exposure", "duplicate_effects")
PRE_FIX = " (pre-fix)"
SCOPE = {"crash_matrix": "H5"}                         # every other run is under H1


def classify(rows: pd.DataFrame) -> tuple[int, int]:
    """(contract violations, other not-ok runs) in one run's per-run rows. Columns: policy,
    checker_ok, the CONTRACT_COUNTS and other_problems (every other failing count). A contract
    violation is a P4-P6 run with a CONTRACT_COUNT > 0 and no other problem."""
    contract = rows["policy"].isin(ASSURED) & (rows[list(CONTRACT_COUNTS)].sum(axis=1) > 0)
    clean_contract = contract & (rows["other_problems"] == 0)
    other = (rows["checker_ok"] == 0) & ~clean_contract
    return int(clean_contract.sum()), int(other.sum())


def gate(final: list[dict], counts: dict[str, tuple[int, int]]) -> list[dict]:
    """§8, from classify() per run name. Contract violations: verdicts are computed and labelled
    pre-fix; the hypothesis in scope is forced to not supported (pre-fix). Other not-ok runs
    block every verdict that is not forced."""
    violations = {n: c for n, (c, _) in counts.items() if c}
    blocking = {n: o for n, (_, o) in counts.items() if o}
    if not violations and not blocking:
        return final
    forced = {SCOPE.get(n, "H1") for n in violations}
    out = []
    for v in final:
        if v["hypothesis"] in forced:
            runs = {n: c for n, c in violations.items() if SCOPE.get(n, "H1") == v["hypothesis"]}
            out.append({**v, "verdict": NOT_SUPPORTED + PRE_FIX, "pre_fix": True,
                        "detail": f"P4-P6 contract violations (false closure, false exposure or duplicate "
                                  f"effect) in {runs}; must be reported with the post-fix result (PREREG §8); "
                                  f"rule as computed: {v['verdict']}; {v['detail']}"})
        elif blocking:
            out.append({**v, "verdict": BLOCKED,
                        "detail": f"checker not-ok runs: {blocking}; resolve via the deviation procedure "
                                  f"(PREREG §8) (unblocked: {v['verdict']}; {v['detail']})"})
        else:
            out.append({**v, "verdict": v["verdict"] + PRE_FIX, "pre_fix": True,
                        "detail": f"pre-fix run (contract violations in {violations}); {v['detail']}"})
    return out


def with_pre_fix(final: list[dict], pre_fix: list[dict] | None) -> list[dict]:
    """Attach the superseded run's verdicts (its verdicts.json) to the post-fix ones, so both
    are reported (PREREG §8)."""
    if not pre_fix:
        return final
    old = {v["hypothesis"]: v for v in pre_fix}
    return [{**v, "pre_fix_verdict": old[v["hypothesis"]]["verdict"]} if v["hypothesis"] in old else v
            for v in final]


def render(verdicts: list[dict]) -> str:
    if any("pre_fix_verdict" in v for v in verdicts):
        lines = ["| hypothesis | verdict | pre-fix verdict (superseded run, PREREG §8) | detail |", "|---|---|---|---|"]
        lines += [f"| {v['hypothesis']} | {v['verdict']} | {v.get('pre_fix_verdict', '')} | {v['detail']} |"
                  for v in verdicts]
    else:
        lines = ["| hypothesis | verdict | detail |", "|---|---|---|"]
        lines += [f"| {v['hypothesis']} | {v['verdict']} | {v['detail']} |" for v in verdicts]
    return "\n".join(lines) + "\n"
