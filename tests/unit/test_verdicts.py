"""analysis/verdicts.py: each pre-registered decision rule (docs/PREREG.md §4) on toy tables."""
import itertools

import pandas as pd
import pytest

from analysis import verdicts as V


def h3_table(below: dict[str, int], normal_below: int = 4) -> pd.DataFrame:
    """P6 - P5 rows over the primary grid; `below[fault]` tight cells have ci_hi < 0. Normal
    slack cells get `normal_below` negatives, which the rule must ignore."""
    rows = []
    for fault, slack in itertools.product(V.FAULT_LEVELS, ("tight", "normal")):
        n_neg = below[fault] if slack == "tight" else normal_below
        for i, (k, n) in enumerate(itertools.product(("1", "4"), (8, 32))):
            rows.append({"contrast": V.H3_CONTRAST, "fault": fault, "slack": slack, "k": k, "n": n,
                         "ci_hi": -1.0 if i < n_neg else 0.5})
    rows.append({"contrast": "lm_ack-lm_reconcile", "fault": "low", "slack": "tight", "k": "1", "n": 8, "ci_hi": -9})
    return pd.DataFrame(rows)


@pytest.mark.parametrize("below, verdict", [
    ({"none": 3, "low": 3, "high": 0}, V.SUPPORTED),        # exactly 3 of 4 in exactly 2 levels
    ({"none": 4, "low": 4, "high": 4}, V.SUPPORTED),
    ({"none": 0, "low": 4, "high": 3}, V.SUPPORTED),
    ({"none": 2, "low": 2, "high": 2}, V.NOT_SUPPORTED),    # 2 of 4 never counts
    ({"none": 4, "low": 2, "high": 2}, V.NOT_SUPPORTED),    # only 1 level counts
    ({"none": 0, "low": 0, "high": 0}, V.NOT_SUPPORTED),    # what dev predicts
])
def test_h3_rule(below, verdict):
    assert V.h3(h3_table(below, normal_below=4 if verdict == V.NOT_SUPPORTED else 0))["verdict"] == verdict


def test_h3_ci_hi_exactly_zero_is_not_below_zero():
    t = h3_table({"none": 4, "low": 4, "high": 0})
    sel = (t["contrast"] == V.H3_CONTRAST) & (t["fault"] == "low") & (t["slack"] == "tight")
    t.loc[sel, "ci_hi"] = [0.0, 0.0, -1, -1]
    v = V.h3(t)
    assert v["verdict"] == V.NOT_SUPPORTED and "low: 2/4" in v["detail"]


def test_h3_missing_cells_is_not_evaluated():
    t = h3_table({"none": 4, "low": 4, "high": 4})
    t = t[~((t["fault"] == "high") & (t["slack"] == "tight") & (t["n"] == 32) & (t["k"] == "4"))]
    assert V.h3(t)["verdict"] == V.NOT_EVALUATED


def trials(name_policy_fc: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame([{"cell_id": c, "fault": f, "policy": p, "false_closure": fc}
                         for c, f, p, fc in name_policy_fc])


def h1_runs(p3_low=2, p3_high=3, p4_fc=0, scaling_p0=0):
    primary = trials([("c1", "low", "lm_ack", p3_low), ("c2", "high", "lm_ack", p3_high),
                      ("c0", "none", "lm_ack", 0), ("c1", "low", "lm_verify", p4_fc),
                      ("c2", "high", "edf", 9)])   # P1 false closures are not part of H1
    other = trials([("s", "high", "adaptive", 0)])
    return {"primary": primary, "secondary": other, "negative_control": other,
            "scaling": trials([("x", "none", "none", scaling_p0)])}


def test_h1_rule():
    v = V.h1(h1_runs())
    assert v["verdict"] == V.SUPPORTED and "in total: 0" in v["detail"]
    assert V.h1(h1_runs(p3_low=0))["verdict"] == V.NOT_SUPPORTED          # a faulty P3 cell without one
    assert V.h1(h1_runs(p4_fc=1))["verdict"] == V.NOT_SUPPORTED
    assert V.h1(h1_runs(scaling_p0=1))["verdict"] == V.NOT_SUPPORTED      # P0 counts, in every config
    runs = h1_runs()
    del runs["scaling"]
    assert V.h1(runs)["verdict"] == V.NOT_EVALUATED


def holm(lo_low, lo_high):
    rows = [{"contrast": V.H2_CONTRAST, "fault": f, "slack": "tight", "k": "1", "n": 32, "holm_ci_lo": lo}
            for f, lo in (("low", lo_low), ("high", lo_high))]
    rows.append({"contrast": V.H2_CONTRAST, "fault": "low", "slack": "tight", "k": "1", "n": 8, "holm_ci_lo": -5})
    return pd.DataFrame(rows)


def test_h2_rule():
    assert V.h2(holm(0.1, 2.0))["verdict"] == V.SUPPORTED
    t = holm(0.1, 2.0).assign(p_holm=0.9)          # p-values are information only
    v = V.h2(t)
    assert v["verdict"] == V.SUPPORTED and "information only" in v["detail"]
    assert V.h2(holm(0.0, 2.0))["verdict"] == V.NOT_SUPPORTED
    assert V.h2(holm(3.0, -0.1))["verdict"] == V.NOT_SUPPORTED
    assert V.h2(holm(1, 1).iloc[1:])["verdict"] == V.NOT_EVALUATED


def test_h4_rule():
    t = pd.DataFrame({"seeds": [2000, 2000], "seeds_all_equal": [2000, 2000]})
    assert V.h4(t)["verdict"] == V.SUPPORTED
    t.loc[1, "seeds_all_equal"] = 1999
    assert V.h4(t)["verdict"] == V.NOT_SUPPORTED
    assert V.h4(None)["verdict"] == V.NOT_EVALUATED


GOOD_C1 = {"MappingRows": 13, "MappingRowsMatched": 13, "ConformanceTests": 15, "TestsRun": 15, "TestsPassed": 15}


def attempt(name, commit="tag", complete=True, **changes):
    return {"name": name, "commit": commit, "dirty": False, "complete": complete, "counts": {**GOOD_C1, **changes}}


def test_c1_first_complete_attempt_decides():
    v = V.c1_attempts([attempt("r1", complete=False, TestsPassed=10), attempt("r2"), attempt("r3", TestsPassed=1)])
    assert v["verdict"] == V.SUPPORTED and v["deciding"] == "r2" and "r1" in v["detail"] and "r3" in v["detail"]
    v = V.c1_attempts([attempt("r1", MappingRowsMatched=12), attempt("r2")])   # a complete failing run decides
    assert v["verdict"] == V.NOT_SUPPORTED and v["deciding"] == "r1"


def test_c1_at_most_two_retries():
    three_transient = [attempt(f"r{i}", complete=False) for i in range(3)]
    assert V.c1_attempts(three_transient)["verdict"] == V.NOT_SUPPORTED
    assert V.c1_attempts([*three_transient, attempt("r4")])["verdict"] == V.NOT_SUPPORTED   # 4th attempt not counted


def test_c1_post_fix_run_decides():
    v = V.c1_attempts([attempt("r1", MappingRowsMatched=12), attempt("r2", commit="fix")])
    assert v["verdict"] == V.SUPPORTED and v["deciding"] == "r2" and "r1" in v["detail"]
    v = V.c1_attempts([attempt("r1"), attempt("r2", commit="fix", complete=False),
                       attempt("r3", commit="fix", TestsPassed=14)])
    assert v["verdict"] == V.NOT_SUPPORTED and v["deciding"] == "r3"


def test_c1_attempts_without_tests_jsonl_are_not_evaluated():
    old = {"name": "old", "commit": "tag", "dirty": False, "complete": False,
           "counts": {k: v for k, v in GOOD_C1.items() if not k.startswith("Tests")}}
    assert V.c1_attempts([old])["verdict"] == V.NOT_EVALUATED
    assert V.c1_attempts([])["verdict"] == V.NOT_EVALUATED


def check_rows(*rows):
    cols = ("policy", "checker_ok", "false_closure", "false_exposure", "duplicate_effects", "other_problems")
    return pd.DataFrame([dict(zip(cols, r)) for r in rows], columns=cols)


def test_classify_separates_contract_violations_from_other_problems():
    rows = check_rows(("lm_verify", 0, 1, 0, 0, 0),     # contract violation (false closure)
                      ("adaptive", 0, 0, 1, 0, 0),      # contract violation (false exposure)
                      ("lm_reconcile", 0, 0, 0, 1, 0),  # contract violation (duplicate effect)
                      ("lm_verify", 0, 1, 0, 0, 2),     # false closure plus a chain problem: other
                      ("lm_ack", 1, 7, 0, 0, 0),        # P3 false closures are measurements
                      ("lm_ack", 0, 0, 0, 1, 0),        # P3 duplicate effect: other
                      ("adaptive", 1, 0, 0, 0, 0))
    assert V.classify(rows) == (3, 2)


ALL_SUPPORTED = [V._v(h, True, "x") for h in V.ORDER]


def test_gate_passes_clean_runs_through():
    assert V.gate(ALL_SUPPORTED, {"primary": (0, 0), "crash_matrix": (0, 0)}) == ALL_SUPPORTED


def test_contract_violation_in_primary_is_h1_not_supported_pre_fix():
    out = {v["hypothesis"]: v for v in V.gate(ALL_SUPPORTED, {"primary": (2, 0), "crash_matrix": (0, 0)})}
    assert out["H1"]["verdict"] == "not supported (pre-fix)" and "rule as computed: supported" in out["H1"]["detail"]
    assert all(out[h]["verdict"] == "supported (pre-fix)" for h in ("H2", "H3", "H4", "H5", "C1"))
    assert all(v["pre_fix"] for v in out.values())


def test_contract_violation_in_the_crash_matrix_is_h5_not_supported_pre_fix():
    out = {v["hypothesis"]: v["verdict"] for v in V.gate(ALL_SUPPORTED, {"primary": (0, 0), "crash_matrix": (1, 0)})}
    assert out["H5"] == "not supported (pre-fix)" and out["H1"] == "supported (pre-fix)"


def test_other_problems_still_block_but_not_the_forced_verdict():
    out = {v["hypothesis"]: v["verdict"] for v in V.gate(ALL_SUPPORTED, {"primary": (1, 0), "secondary": (0, 3)})}
    assert out["H1"] == "not supported (pre-fix)"
    assert {out[h] for h in ("H2", "H3", "H4", "H5", "C1")} == {V.BLOCKED}
    only_other = V.gate(ALL_SUPPORTED, {"secondary": (0, 1)})
    assert {v["verdict"] for v in only_other} == {V.BLOCKED}
    assert all("unblocked: supported" in v["detail"] for v in only_other)


def test_pre_fix_verdicts_are_reported_alongside_the_post_fix_ones():
    pre = V.gate(ALL_SUPPORTED, {"primary": (1, 0)})
    final = V.with_pre_fix(ALL_SUPPORTED, pre)
    h1 = next(v for v in final if v["hypothesis"] == "H1")
    assert h1["verdict"] == V.SUPPORTED and h1["pre_fix_verdict"] == "not supported (pre-fix)"
    table = V.render(final)
    assert "pre-fix verdict" in table and "| H1 | supported | not supported (pre-fix) |" in table
    assert V.with_pre_fix(ALL_SUPPORTED, None) == ALL_SUPPORTED


def test_negligible_false_closure_condition():
    def p3(fc_low, closed_low, fc_high, closed_high):
        return pd.DataFrame([{"policy": "lm_ack", "fault": f, "false_closure": fc, "closed_cancelled": cc}
                             for f, fc, cc in (("low", fc_low, closed_low), ("high", fc_high, closed_high))]
                            + [{"policy": "lm_verify", "fault": "low", "false_closure": 999, "closed_cancelled": 1}])
    assert V.negligible_fc(p3(9, 1000, 0, 50))["verdict"] == V.MET
    assert V.negligible_fc(p3(10, 1000, 0, 50))["verdict"] == V.NOT_MET       # exactly 1% is not below 1%
    assert V.negligible_fc(p3(0, 1000, 5, 100))["verdict"] == V.NOT_MET
    assert V.negligible_fc(p3(0, 0, 0, 10))["verdict"] == V.NOT_MET           # no closures: undefined, not met
    assert V.negligible_fc(None)["verdict"] == V.NOT_EVALUATED


def test_h5_and_c1_rules():
    h5 = {"Supported": 1, "CellsHold": 39, "CellsTotal": 39, "FalseClosures": 0, "DuplicateEffects": 0, "MinCrashed": 500}
    assert V.h5(h5)["verdict"] == V.SUPPORTED and V.h5({**h5, "Supported": 0})["verdict"] == V.NOT_SUPPORTED
    assert V.h5(None)["verdict"] == V.NOT_EVALUATED
    c = GOOD_C1
    assert V.c1(c)["verdict"] == V.SUPPORTED
    assert V.c1({**c, "MappingRowsMatched": 12})["verdict"] == V.NOT_SUPPORTED
    assert V.c1({**c, "TestsPassed": 14})["verdict"] == V.NOT_SUPPORTED
    assert V.c1({**c, "TestsRun": 14, "TestsPassed": 14})["verdict"] == V.NOT_SUPPORTED
    assert V.c1({k: v for k, v in c.items() if not k.startswith("Tests")})["verdict"] == V.NOT_EVALUATED
    assert V.c1(None)["verdict"] == V.NOT_EVALUATED
