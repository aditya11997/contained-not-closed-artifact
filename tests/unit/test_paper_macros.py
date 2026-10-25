"""Paper macros (WP paper-macros): every new function on small fixture CSVs, with the expected
values worked out here from the fixture."""
import importlib.util
import io
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from agent import summarize
from analysis import crash_matrix, report, tables
from analysis.posthoc import macros as phm
from analysis.stats import wilson

ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    modname = f"paper_macros_scripts_{name}"
    spec = importlib.util.spec_from_file_location(modname, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


va = _load("verify_archive")
com = _load("check_old_macros")


def csv(text: str) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(text), dtype={"cell_id": str, "k": str})


# -- H1 / S9: false-closure share of CLOSED_CANCELLED ----------------------------------------
TRIALS = """cell_id,fault,policy,seed,false_closure,closed_cancelled
c1,low,lm_ack,1,3,10
c1,low,lm_ack,2,1,10
c2,low,lm_ack,1,0,20
c2,low,lm_ack,2,0,20
c3,high,lm_ack,1,5,25
c3,high,lm_ack,2,5,25
c4,none,lm_ack,1,0,30
c1,low,lm_verify,1,0,40
"""


def test_fc_share_macros():
    m = report.fc_share_macros(csv(TRIALS))
    # low: 4 false closures of 60 CLOSED_CANCELLED (c1 + c2); high: 10 of 50; "none" is not faulty
    assert m["FCShareAckLow"] == tables.fmt_rate(4 / 60) == "6.67\\%"
    assert m["FCShareAckHigh"] == "20.00\\%"
    assert m["FCShareLoAckLow"] == tables.fmt_rate(wilson(4, 60)[0])
    assert m["FCShareHiAckHigh"] == tables.fmt_rate(wilson(10, 50)[1])
    assert m["FCClosedCancelledAckLow"] == "60"
    assert m["NFaultyCellsAckFC"] == "2" and m["NFaultyCells"] == "3"   # c2 never false-closes


# -- H2 / H3 summaries and per-cell Holm macros ----------------------------------------------
CONTRASTS = """fault,slack,k,n,contrast,a,b,mean_diff,ci_lo,ci_hi,p_holm,holm_reject,holm_ci_lo,holm_ci_hi
low,tight,1,32,lm_verify-lm_ack,lm_verify,lm_ack,20.0,12.0,28.0,0.01,True,10.04,30.0
high,tight,1,32,lm_verify-lm_ack,lm_verify,lm_ack,50.0,40.0,60.0,0.01,True,35.06,65.0
low,tight,4,32,lm_verify-lm_ack,lm_verify,lm_ack,-5.0,-9.0,-1.0,0.02,True,-11.0,1.0
low,tight,1,32,lm_reconcile-lm_ack,lm_reconcile,lm_ack,-3.0,-4.0,-2.0,0.03,True,-5.0,-1.0
"""


def test_holm_macros_and_h2_minimum():
    t = csv(CONTRASTS)
    m = report.contrast_macros(t)
    assert m["HolmLoVerifyAckLowTightKOneNThirtyTwo"] == "10.0"
    assert m["HolmHiVerifyAckHighTightKOneNThirtyTwo"] == "65.0"
    h2 = report.h2_summary_macros(t, m)
    # only the tight/k1/n32 P4 - P3 rows are H2 cells: the k4 row (-11.0) and the P5 row are not
    assert h2 == {"HTwoMinHolmLoLow": "10.0", "HTwoMinHolmLoHigh": "35.1"}


def test_h2_minimum_must_match_the_cell_macro():
    t = csv(CONTRASTS)
    m = report.contrast_macros(t)
    m["HolmLoVerifyAckLowTightKOneNThirtyTwo"] = "9.9"
    with pytest.raises(ValueError, match="HTwoMinHolmLoLow"):
        report.h2_summary_macros(t, m)


H3 = """contrast,k,n_seeds,ties,ci_lo,ci_hi
adaptive-lm_reconcile,1,10,10,0.0,0.0
adaptive-lm_reconcile,1,10,10,0.0,0.0
adaptive-lm_reconcile,4,10,7,-2.0,3.0
adaptive-lm_reconcile,4,10,4,1.0,3.0
adaptive-lm_reconcile,4,10,9,-3.0,-0.5
"""


def test_h3_summary_macros():
    m = report.h3_summary_macros(csv(H3))
    assert m["HThreeNonzeroSeedsKOne"] == "0" and m["HThreeSeedsKOne"] == "20"
    assert m["HThreeNonzeroSeedsKFour"] == str(3 + 6 + 1) and m["HThreeSeedsKFour"] == "30"
    assert m["HThreeCellsExcludeZeroKFour"] == "2" and m["HThreeCellsKFour"] == "3"   # rows 2 and 3
    assert m["HThreeCellsExcludeZeroKOne"] == "0"


# -- one-column contrast table --------------------------------------------------------------
def test_contrast_tex_narrow_has_five_columns_and_the_same_numbers():
    t = csv("""fault,slack,k,n,contrast,mean_diff,ci_lo,ci_hi,p,p_holm,a_better,ties,a_worse,bonf_ci_lo,bonf_ci_hi
low,tight,inf,32,lm_verify-lm_ack,1234.56,-1.04,2.0,0.5,0.0004,3,4,5,-9.0,9.0
""")
    wide, narrow = tables.contrast_tex(t, "C.", "tab:x"), tables.contrast_tex_narrow(t, "C.", "tab:x")
    assert "\\begin{tabular}{llrrr}" in narrow
    row = next(line for line in narrow.splitlines() if line.startswith("low/"))
    assert row.count("&") == 4
    assert row == "low/tight/$\\infty$/32 & P4$-$P3 & 1{,}234.6 [$-$1.0, 2.0] & $<$0.001 & 3/4/5 \\\\"
    for number in ("1{,}234.6", "$-$1.0", "2.0", "$<$0.001", "3/4/5", "[$-$9.0, 9.0]"):
        assert number in wide
    assert "\\begin{table*}" in wide and "\\begin{table}[t]" in narrow


def test_tex_num_uses_a_math_minus():
    assert tables.tex_num(-1234.56) == "$-$1{,}234.6"
    assert tables.tex_num(2.0) == "2.0"
    assert tables.fmt_num(-1234.56) == "-1{,}234.6"      # macros keep the hyphen


def test_contrast_faulty_tex_holm_ci_and_dagger():
    t = csv("""cell_id,fault,slack,k,n,contrast,mean_diff,holm_ci_lo,holm_ci_hi,holm_reject
c0,none,tight,1,32,lm_verify-lm_ack,0.0,0.0,0.0,False
c0,none,tight,1,32,lm_reconcile-lm_ack,0.0,0.0,0.0,False
c1,low,tight,1,32,lm_reconcile-lm_ack,-6.5,-15.8,0.7,False
c1,low,tight,1,32,lm_verify-lm_ack,158.7,108.9,211.3,True
c2,high,normal,4,8,lm_verify-lm_ack,-143.7,-190.0,-99.0,True
c2,high,normal,4,8,lm_reconcile-lm_ack,-1.0,-2.0,3.0,False
""")
    tex = tables.contrast_faulty_tex(t, ("lm_verify-lm_ack", "lm_reconcile-lm_ack"), ("low", "high"), 0.05,
                                     "tab:contrasts-faulty")
    body = [line for line in tex.splitlines() if "/" in line and "&" in line and not line.startswith("Cell")]
    # the faultless cell is left out; P4 - P3 comes first whatever the CSV order
    assert body == ["low/tight/1/32 & 158.7 [108.9, 211.3]$^\\dagger$ & $-$6.5 [$-$15.8, 0.7] \\\\",
                    "high/normal/4/8 & $-$143.7 [$-$190.0, $-$99.0]$^\\dagger$ & $-$1.0 [$-$2.0, 3.0] \\\\"]
    assert "the 2 faulty primary cells" in tex and "p \\le 0.05" in tex
    assert "\\label{tab:contrasts-faulty}" in tex


# -- crash matrix table (T3) -------------------------------------------------------------------
def test_crash_table_tex_sums_cells_per_policy():
    t = pd.DataFrame([
        {"cell": "a", "policy": "lm_ack", "crashed": 200, "false_closures": 7, "false_exposures": 0,
         "duplicate_effects": 0, "failed_invariants": 0, "h5_holds": None},
        {"cell": "a", "policy": "lm_verify", "crashed": 150, "false_closures": 0, "false_exposures": 0,
         "duplicate_effects": 0, "failed_invariants": 0, "h5_holds": True},
        {"cell": "b", "policy": "lm_verify", "crashed": 1100, "false_closures": 0, "false_exposures": 1,
         "duplicate_effects": 2, "failed_invariants": 3, "h5_holds": False}])
    tex = crash_matrix.table_tex(t)
    assert "\\begin{tabular}{lrrrr}" in tex
    assert "P3 & -- & 200 (200) & 7 & 0/0/0 \\\\" in tex
    assert "P4 & 1/2 & 1{,}250 (150) & 0 & 1/2/3 \\\\" in tex
    assert "Crashed (min) & FC & FE/Dup/Inv" in tex


# -- post-hoc macros ---------------------------------------------------------------------------
def test_ph2():
    fc = csv("""fault,policy,false_closures,fc_share_of_closed_cancelled
none,lm_ack,0,0.0
low,lm_ack_retry,1234,0.0251
""")
    con = csv("""fault,contrast,mean_diff,ci_lo,ci_hi
none,lm_ack_retry-lm_ack,-99.0,-100.0,-98.0
low,lm_ack_retry-lm_ack,-5.0,-8.0,-1.0
high,lm_ack_retry-lm_ack,6.0,-1.0,9.0
low,lm_reconcile-lm_ack_retry,-2.0,-3.0,-1.0
""")
    m = phm.ph2(fc, con)
    assert m["PHTwoFCAckRetryLow"] == "1{,}234" and m["PHTwoFCShareAckRetryLow"] == "2.51\\%"
    assert "PHTwoFCAckNone" not in m
    # the faultless row is excluded, so the min is -5.0, not -99.0
    assert (m["PHTwoAckRetryAckCellsBelow"], m["PHTwoAckRetryAckCellsAbove"], m["PHTwoAckRetryAckCells"]) == \
        ("1", "0", "2")
    assert m["PHTwoAckRetryAckMin"] == "-5.0" and m["PHTwoAckRetryAckMax"] == "6.0"
    assert m["PHTwoReconcileAckRetryCellsBelow"] == "1"


def test_ph3():
    t = csv("""m,fault,slack,k,n,contrast,mean_diff,ci_lo,ci_hi
1,low,tight,1,32,lm_verify-lm_ack,170.9,140.3,202.3
4,low,tight,1,32,lm_verify-lm_ack,5.5,-4.0,14.8
4,low,tight,4,32,lm_verify-lm_ack,1.0,0.0,2.0
4,none,tight,1,32,lm_verify-lm_ack,0.0,0.0,0.0
4,low,tight,4,8,lm_reconcile-lm_ack,-9.0,-12.0,-6.0
4,high,tight,4,8,lm_reconcile-lm_ack,-1.0,-3.0,1.0
""")
    m = phm.ph3(t)
    assert (m["PHThreeVerifyAckLowMFour"], m["PHThreeVerifyAckLowMFourLo"], m["PHThreeVerifyAckLowMFourHi"]) == \
        ("5.5", "-4.0", "14.8")
    assert m["PHThreeVerifyAckLowMOne"] == "170.9"
    assert not any("None" in k for k in m)                      # faultless rows are not exported
    assert m["PHThreeReconcileAckKFourNEightMFourCellsBelow"] == "1"
    assert m["PHThreeReconcileAckKFourNEightMFourCells"] == "2"


def test_ph5_only_cells_where_p4_beats_p3():
    attr = csv("""cell_id,fault,slack,k,n,mean_diff,recovered_resend_per_seed,other_per_seed
x,low,tight,4,8,-20.0,-15.0,-5.0
y,low,tight,1,32,30.0,-2.0,32.0
""")
    split = csv("""cell_id,p3_fc0_mean,p3_fc0_ci_lo,p3_fc0_ci_hi
x,0.5,-1.0,2.0
y,9.0,8.0,10.0
""")
    m = phm.ph5(attr, split)
    assert m["PHFiveCellsVerifyBetter"] == "1" and m["PHFiveCells"] == "2"
    assert m["PHFiveResendShareLowTightKFourNEight"] == "75.00\\%"          # -15 / -20
    assert m["PHFiveNoFCLoLowTightKFourNEight"] == "-1.0"
    assert not any(k.endswith("KOneNThirtyTwo") for k in m)


def test_ph6():
    t = csv("""fault,slack,k,n,contrast,bonf_ci_lo,bonf_ci_hi
low,tight,1,32,lm_verify-lm_ack,108.81,211.37
high,tight,1,32,lm_verify-lm_ack,343.47,517.79
high,tight,4,32,lm_verify-lm_ack,1.0,2.0
""")
    m = phm.ph6(t)
    assert m == {"PHSixFamilySize": "3", "PHSixBonfLoVerifyAckLow": "108.8", "PHSixBonfHiVerifyAckLow": "211.4",
                 "PHSixBonfLoVerifyAckHigh": "343.5", "PHSixBonfHiVerifyAckHigh": "517.8"}


def test_ph7():
    t = csv("""variant,cell,policy,runs,crashed_twice,false_closures,false_exposures,duplicate_effects,failed_invariants,holds
same,a,lm_verify,10,8,0,0,0,0,holds
same,a,adaptive,10,3,0,0,0,0,too few double crashes
same,a,lm_ack_retry,10,9,4,0,0,0,
""")
    m = phm.ph7(t)
    assert (m["PHSevenPairs"], m["PHSevenPairsHold"], m["PHSevenRuns"], m["PHSevenMinCrashedTwice"]) == \
        ("2", "1", "20", "3")
    assert m["PHSevenFalseClosures"] == "0" and m["PHSevenAckRetryFalseClosures"] == "4"


def test_ph8_min_max_per_fault_and_overall_for_k_above_one():
    t = csv("""fault,slack,k,n,contrast,mean_diff,ci_lo,ci_hi
none,tight,4,32,mh-lm_ack,-1200.0,-1300.0,-1100.0
none,normal,4,32,mh-lm_ack,-900.0,-950.0,-850.0
low,tight,4,32,mh-lm_ack,-1100.0,-1150.0,-1050.0
low,normal,4,32,mh-lm_ack,-800.0,-850.0,-750.0
low,tight,1,32,mh-lm_ack,-1.0,-2.0,0.0
""")
    m = phm.ph8(t)
    assert m["PHEightMhAckMinAllKFourNThirtyTwo"] == "-1{,}200.0"
    assert m["PHEightMhAckMinSlackAllKFourNThirtyTwo"] == "tight"
    assert m["PHEightMhAckMaxAllKFourNThirtyTwo"] == "-800.0"
    assert m["PHEightMhAckMaxHiAllKFourNThirtyTwo"] == "-750.0"
    assert m["PHEightMhAckMinNoneKFourNThirtyTwo"] == "-1{,}200.0"
    assert m["PHEightMhAckMaxLowKFourNThirtyTwo"] == "-800.0"
    assert m["PHEightMhAckMinLoLowKFourNThirtyTwo"] == "-1{,}150.0"
    assert not any("KOne" in k for k in m)


# -- case study ----------------------------------------------------------------------------------
def _run(root: Path, name: str, reply: str, effects: int, status="current"):
    d = root / name
    d.mkdir()
    (d / "summary.json").write_text(json.dumps({
        "run_id": name, "model": "llama3.1:8b", "episodes": [
            {"counts": {"revoked_actor_effects": effects}}, {"counts": {"revoked_actor_effects": 0}}]}))
    (d / "manifest.json").write_text(json.dumps({"git": {"commit": "abc"}}))
    (d / "trace.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"kind": "model_response", "tool_calls": [], "content": "first"},
        {"kind": "model_response", "tool_calls": [], "content": reply}]) + "\n")


def test_case_study_macros(tmp_path):
    _run(tmp_path, "cs-1", "All 9 orders were placed.", 0)
    _run(tmp_path, "cs-2", "Some calls were DENIED after the agent was revoked.", 0)
    _run(tmp_path, "cs-3", "Placed what I could.", 1)
    _run(tmp_path, "cs-4", "Done.", 5)
    (tmp_path / "superseded.json").write_text(json.dumps({"runs": {"cs-4": "superseded"}}))
    runs = [s for s in summarize.load_runs(tmp_path) if s["status"] == "current"]
    m = summarize.case_study_macros(runs)
    # cs-4 is superseded; of cs-1..3 only cs-2's final reply mentions the stop ("DENIED", "revoked")
    assert m == {"CaseStudyRuns": "3", "CaseStudySummariesOmitStop": "2", "CaseStudyRevokedActorEffects": "1",
                 "CaseStudyModel": "llama3.1:8b"}


def test_mentions_stop_and_tex_text():
    assert summarize.mentions_stop("the agent was Revoked")
    assert summarize.mentions_stop("incident stop")
    assert not summarize.mentions_stop("all orders placed")
    assert summarize._tex_text("a_b 50% & #1") == "a\\_b 50\\% \\& \\#1"


# -- reproducibility ---------------------------------------------------------------------------
def _archive(tmp_path, files: dict[str, bytes], listed: dict[str, bytes]) -> Path:
    import hashlib
    for rel, data in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    rows = [f"| `{hashlib.sha256(d).hexdigest()}` | {len(d):,} | `{rel}` | yes |" for rel, d in listed.items()]
    a = tmp_path / "ARCHIVE.md"
    a.write_text("| SHA-256 | bytes | path | in git |\n|---|--:|---|---|\n" + "\n".join(rows)
                 + "\n| `" + "0" * 64 + "` | 1 | `results/dev/x` | no |\n")
    return a


def test_verify_archive_counts_holdout_rows_only(tmp_path):
    data = {"results/holdout/p/a.csv": b"abc", "results/holdout/p/b.csv": b"x" * 2000}
    a = _archive(tmp_path, data, data)
    tex = tmp_path / "repro.tex"
    assert va.main(["--archive", str(a), "--root", str(tmp_path), "--tex", str(tex)]) == 0
    text = tex.read_text()
    assert "\\ReproHoldoutFilesListed}{2}" in text and "\\ReproHoldoutFilesMatched}{2}" in text
    assert "\\ReproHoldoutFilesMissing}{0}" in text


def test_verify_archive_fails_on_missing_or_changed_files(tmp_path, capsys):
    listed = {"results/holdout/p/a.csv": b"abc", "results/holdout/p/b.csv": b"def"}
    a = _archive(tmp_path, {"results/holdout/p/a.csv": b"abX"}, listed)
    tex = tmp_path / "repro.tex"
    assert va.main(["--archive", str(a), "--root", str(tmp_path), "--tex", str(tex)]) == 1
    err = capsys.readouterr().err
    assert "MISSING results/holdout/p/b.csv" in err and "MISMATCHED results/holdout/p/a.csv" in err
    assert not tex.exists()
    rows = va.archive_rows(a.read_text())
    result = va.verify(rows, tmp_path)
    assert va.macros(rows, result)["ReproHoldoutFilesMissing"] == "1"


def test_check_old_macros_compare():
    old = com.macros("\\newcommand{\\A}{1}\n\\newcommand{\\B}{2}\n")
    assert com.compare(old, com.macros("\\newcommand{\\A}{1}\n\\newcommand{\\B}{2}\n\\newcommand{\\C}{3}\n")) == []
    problems = com.compare(old, com.macros("\\newcommand{\\A}{1.0}\n"))
    assert problems == ["MISSING \\B", "CHANGED \\A: \\newcommand{\\A}{1} -> \\newcommand{\\A}{1.0}"]
