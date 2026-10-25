"""experiments/crash_matrix.py (the H5 runner) and analysis/crash_matrix.py (the H5 decision)."""
import json

import pandas as pd
import pytest

from analysis import crash_matrix as acm
from experiments import crash_matrix as cm
from experiments.guard import HoldoutRefused


def test_cli_refuses_holdout_before_running(tmp_path, monkeypatch, capsys):
    def refuse(repo):
        raise HoldoutRefused("no tag")

    monkeypatch.setattr(cm, "check_holdout_allowed", refuse)
    out = tmp_path / "out"
    assert cm.main(["--partition", "holdout", "--n-seeds", "1", "--out", str(out), "--workers", "1"]) == 2
    assert not out.exists() and "holdout refused" in capsys.readouterr().err


def test_cli_rejects_seeds_outside_partition(tmp_path, capsys):
    out = tmp_path / "out"
    assert cm.main(["--partition", "dev", "--seed-start", "199999", "--n-seeds", "2", "--out", str(out)]) == 2
    assert not out.exists()


@pytest.mark.slow
def test_dev_run_writes_every_applicable_run_and_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert cm.main(["--partition", "dev", "--n-seeds", "1", "--out", str(a), "--workers", "1"]) == 0
    assert cm.main(["--partition", "dev", "--n-seeds", "1", "--out", str(b), "--workers", "2"]) == 0
    assert (a / "crash_runs.csv").read_bytes() == (b / "crash_runs.csv").read_bytes()
    runs = pd.read_csv(a / "crash_runs.csv")
    pairs = [(p, cm.cell_label(pt, t)) for p in cm.POLICIES for pt, t in cm.CELLS if cm.applies(p, t)]
    assert len(pairs) == 44 and len(runs) == len(pairs) * len(cm.FAULTS) * len(cm.KS)
    m = json.loads((a / "manifest.json").read_text())
    assert m["experiment"] == "crash_matrix" and m["partition"] == "dev" and m["end"]
    assert m["seeds"] == {"first": 100000, "last": 100000, "count": 1}
    summary = json.loads((a / "checker_summary.json").read_text())
    assert summary["runs"] == len(runs) and summary["not_ok"] == 0 and summary["counts"]["chain_broken"] == 0
    assert not (a / "_work").exists()


def rows(policy, cell, crashed, fc=0, dup=0, integrity=0, fe=0, uncrashed_fc=0):
    base = dict.fromkeys(cm.RUN_COLUMNS, 0)
    n = acm.MIN_CRASHED + 10
    return [{**base, "policy": policy, "cell": cell, "crashed": int(i < crashed), "checker_ok": 1,
             "false_closure": (fc if i == 0 else 0) + (uncrashed_fc if i == n - 1 else 0),
             "false_exposure": fe if i == 0 else 0, "duplicate_effects": dup if i == 0 else 0,
             "integrity_problems": integrity if i == 0 else 0} for i in range(n)]


def h5(*groups):
    return acm.h5_counts(acm.cell_table(pd.DataFrame([r for g in groups for r in g])))


def test_h5_supported_only_if_every_assured_pair_holds():
    ok = [rows(p, "a", acm.MIN_CRASHED) for p in acm.H5_POLICIES]
    p3_fc = rows("lm_ack", "a", acm.MIN_CRASHED, fc=5)        # P3 false closures are not part of H5
    c = h5(*ok, p3_fc)
    assert (c["Supported"], c["CellsHold"], c["CellsTotal"], c["FalseClosures"]) == (1, 3, 3, 0)
    for bad in ({"fc": 1}, {"fe": 1}, {"dup": 1}, {"integrity": 1}, {"uncrashed_fc": 1}):   # crashed or not
        c = h5(*ok[1:], rows("lm_verify", "a", acm.MIN_CRASHED, **bad))
        assert (c["Supported"], c["CellsHold"]) == (0, 2), bad
    assert h5(*ok[1:], rows("lm_verify", "a", acm.MIN_CRASHED - 1))["Supported"] == 0   # vacuous cell
    assert h5(rows("lm_ack", "a", 200, dup=1), *ok)["DuplicateEffects"] == 1


def test_h5_tex_macros_are_letters_only():
    tex = acm.h5_tex("run", acm.cell_table(pd.DataFrame(rows("adaptive", "a", acm.MIN_CRASHED))))
    assert "\\newcommand{\\HfiveSupported}{1}" in tex and "analysis/crash_matrix.py" in tex


def test_report_build_reads_a_crash_matrix_run(tmp_path):
    from analysis import report
    run = tmp_path / "results" / "crash"
    run.mkdir(parents=True)
    pd.DataFrame([r for p in acm.H5_POLICIES for r in rows(p, "a", acm.MIN_CRASHED)]).to_csv(
        run / "crash_runs.csv", index=False)
    (run / "manifest.json").write_text(json.dumps({"experiment": "crash_matrix", "partition": "dev",
                                                   "seeds": {"count": 1}, "git": {"commit": "0" * 40}}))
    (run / "checker_summary.json").write_text(json.dumps({"runs": 1, "not_ok": 0, "counts": {"chain_broken": 0}}))
    report.build(tmp_path / "results", tmp_path / "out", tmp_path / "figs")
    numbers = (tmp_path / "out" / "numbers.tex").read_text()
    assert "\\newcommand{\\HfiveSupported}{1}" in numbers and (tmp_path / "out" / "crash_matrix.csv").exists()
    found = {v["hypothesis"]: v["verdict"] for v in json.loads((tmp_path / "out" / "verdicts.json").read_text())}
    assert found == {"H1": "not evaluated", "H2": "not evaluated", "H3": "not evaluated",
                     "H4": "not evaluated", "H5": "supported", "C1": "not evaluated", "S9": "not evaluated"}
    assert "\\newcommand{\\VerdictHFive}{supported}" in numbers


def test_report_blocks_all_verdicts_on_a_checker_not_ok_crash_run(tmp_path):
    from analysis import report
    run = tmp_path / "results" / "crash"
    run.mkdir(parents=True)
    runs = [r for p in acm.H5_POLICIES for r in rows(p, "a", acm.MIN_CRASHED)]
    runs[0]["checker_ok"] = 0
    pd.DataFrame(runs).to_csv(run / "crash_runs.csv", index=False)
    (run / "manifest.json").write_text(json.dumps({"experiment": "crash_matrix", "partition": "dev",
                                                   "seeds": {"count": 1}, "git": {"commit": "0" * 40}}))
    (run / "checker_summary.json").write_text(json.dumps({"runs": 1, "not_ok": 0, "counts": {"chain_broken": 0}}))
    report.build(tmp_path / "results", tmp_path / "out", tmp_path / "figs")
    found = json.loads((tmp_path / "out" / "verdicts.json").read_text())
    assert {v["verdict"] for v in found} == {"blocked"} and "crash_matrix" in found[0]["detail"]


def crash_results(tmp_path, runs):
    run = tmp_path / "results" / "crash"
    run.mkdir(parents=True)
    pd.DataFrame(runs).to_csv(run / "crash_runs.csv", index=False)
    (run / "manifest.json").write_text(json.dumps({"experiment": "crash_matrix", "partition": "dev",
                                                   "seeds": {"count": 1}, "git": {"commit": "0" * 40}}))
    (run / "checker_summary.json").write_text(json.dumps({"runs": 1, "not_ok": 0, "counts": {"chain_broken": 0}}))
    return tmp_path / "results"


def test_report_h5_contract_violation_is_not_supported_pre_fix_and_reaches_the_paper_macros(tmp_path):
    from analysis import report
    runs = [r for p in acm.H5_POLICIES for r in rows(p, "a", acm.MIN_CRASHED)]
    runs[0].update(false_closure=1, checker_ok=0)          # a P4 false closure after a crash
    report.build(crash_results(tmp_path / "pre", runs), tmp_path / "pre" / "out", tmp_path / "pre" / "figs")
    pre = json.loads((tmp_path / "pre" / "out" / "verdicts.json").read_text())
    found = {v["hypothesis"]: v["verdict"] for v in pre}
    assert found["H5"] == "not supported (pre-fix)" and found["H1"] == "not evaluated (pre-fix)"

    fixed = [r for p in acm.H5_POLICIES for r in rows(p, "a", acm.MIN_CRASHED)]   # the post-fix rerun
    report.build(crash_results(tmp_path / "post", fixed), tmp_path / "post" / "out", tmp_path / "post" / "figs",
                 pre_fix_verdicts=tmp_path / "pre" / "out" / "verdicts.json")
    post = {v["hypothesis"]: v for v in json.loads((tmp_path / "post" / "out" / "verdicts.json").read_text())}
    assert post["H5"]["verdict"] == "supported" and post["H5"]["pre_fix_verdict"] == "not supported (pre-fix)"
    numbers = (tmp_path / "post" / "out" / "numbers.tex").read_text()
    assert "\\newcommand{\\VerdictHFive}{supported}" in numbers
    assert "\\newcommand{\\VerdictPreFixHFive}{not supported (pre-fix)}" in numbers
    assert "| H5 | supported | not supported (pre-fix) |" in (tmp_path / "post" / "out" / "verdicts.md").read_text()
