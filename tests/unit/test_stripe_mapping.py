"""analysis/stripe_mapping.py: the two Stripe counts (mapping-table rows vs conformance tests)
and their ties to docs/STRIPE_MAPPING.md and tests/conformance."""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from analysis.stripe_mapping import EPISODES, SCRIPTS, TABLE_ROWS, counts, stripe_tex

REPO = Path(__file__).resolve().parents[2]
CONFORMANCE = REPO / "tests" / "conformance"


def table_rows() -> dict[int, str]:
    """Row number -> 'Observed in test' cell of the transition table in STRIPE_MAPPING.md."""
    text = (REPO / "docs" / "STRIPE_MAPPING.md").read_text(encoding="utf-8")
    section = text.split("## Transition table", 1)[1].split("\n## ", 1)[0]
    out = {}
    for line in section.splitlines():
        m = re.match(r"\| (\d+) \|", line)
        if m:
            out[int(m.group(1))] = line.rstrip(" |").rsplit(" | ", 1)[1]
    return out


def test_table_rows_match_the_doc():
    doc = table_rows()
    assert sorted(doc) == sorted(TABLE_ROWS) == list(range(1, len(TABLE_ROWS) + 1))
    known = set(SCRIPTS) | set(EPISODES)
    for n, cell in doc.items():
        cited = set(re.findall(r"`([^`]+)`", cell)) & known
        assert cited <= set(TABLE_ROWS[n]), (n, cited)
    assert set().union(*TABLE_ROWS.values()) == known   # every conformance test backs some row


def test_scripts_and_episodes_are_the_conformance_tests():
    source = (CONFORMANCE / "test_stripe_vs_sim.py").read_text(encoding="utf-8")
    assert tuple(re.findall(r'\.finish\("([^"]+)"', source)) == SCRIPTS
    proc = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                           str(CONFORMANCE)], cwd=REPO, capture_output=True, text=True, check=False, timeout=300)
    ids = [x for x in proc.stdout.splitlines() if "::" in x]
    assert len(ids) == len(SCRIPTS) + len(EPISODES), proc.stdout
    params = sorted(re.findall(r"test_episode_logs_match_and_check_clean\[(\w+)-(\w+)\]", proc.stdout))
    assert sorted(f"episode/{p}/{f}" for p, f in params) == sorted(EPISODES)


def fake_run(tmp_path, *, mismatch=(), missing=(), tests=None):
    run = tmp_path / "stripe-test"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"git": {"commit": "0" * 40, "dirty": False}}))
    (run / "outcomes.jsonl").write_text("".join(
        json.dumps({"transition": t, "match": t not in mismatch}) + "\n"
        for t in SCRIPTS + EPISODES if t not in missing))
    if tests is not None:
        (run / "tests.jsonl").write_text("".join(json.dumps({"test": f"t{i}", "outcome": o}) + "\n"
                                                 for i, o in enumerate(tests)))
    return run


def test_counts_all_matched(tmp_path):
    c = counts(fake_run(tmp_path, tests=["passed"] * 15))
    assert c == {"MappingRows": 13, "MappingRowsMatched": 13, "TransitionScripts": 9, "Episodes": 6,
                 "ConformanceTests": 15, "OutcomesRecorded": 15, "OutcomesMatched": 15,
                 "ScriptsMatched": 9, "EpisodesMatched": 6, "TestsRun": 15, "TestsPassed": 15}


def test_a_mismatch_or_missing_record_unmatches_every_row_resting_on_it(tmp_path):
    c = counts(fake_run(tmp_path, mismatch=("same_key_different_params",), missing=("episode/adaptive/high",),
                        tests=["passed"] * 13 + ["failed", "skipped"]))
    assert c["MappingRowsMatched"] == 13 - 4   # rows 5, 8, 10 (mismatch) and 13 (missing episode)
    assert (c["OutcomesRecorded"], c["OutcomesMatched"]) == (14, 13)
    assert (c["ScriptsMatched"], c["EpisodesMatched"]) == (8, 5)
    assert (c["TestsRun"], c["TestsPassed"]) == (14, 13)


def test_runs_without_tests_jsonl_get_no_test_macros(tmp_path):
    run = fake_run(tmp_path)
    assert "TestsRun" not in counts(run)
    tex = stripe_tex(run)
    assert "\\newcommand{\\StripeMappingRows}{13}" in tex and "\\newcommand{\\StripeConformanceTests}{15}" in tex
    assert "StripeTestsRun" not in tex and "analysis/stripe_mapping.py" in tex


@pytest.fixture
def conformance_conftest():
    spec = importlib.util.spec_from_file_location("conformance_conftest_under_test", CONFORMANCE / "conftest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_conftest_records_pytests_own_outcome_per_test(tmp_path, conformance_conftest):
    m = conformance_conftest
    rep = lambda node, when, outcome: SimpleNamespace(nodeid=node, when=when, outcome=outcome)
    for r in [rep("a", "setup", "passed"), rep("a", "call", "passed"), rep("a", "teardown", "passed"),
              rep("b", "setup", "passed"), rep("b", "call", "failed"), rep("b", "teardown", "passed"),
              rep("c", "setup", "skipped"), rep("d", "setup", "failed"),
              rep("e", "setup", "passed"), rep("e", "call", "passed"), rep("e", "teardown", "failed")]:
        m.pytest_runtest_logreport(r)
    m.pytest_sessionfinish(None)               # no live session: nothing written
    assert not list(tmp_path.iterdir())
    m._SESSION_DIR.append(tmp_path)
    m.pytest_sessionfinish(None)
    got = [json.loads(x) for x in (tmp_path / "tests.jsonl").read_text().splitlines()]
    assert got == [{"test": "a", "outcome": "passed"}, {"test": "b", "outcome": "failed"},
                   {"test": "c", "outcome": "skipped"}, {"test": "d", "outcome": "error"},
                   {"test": "e", "outcome": "error"}]


def with_failure(tmp_path, error="", http=200, outcome="failed"):
    run = fake_run(tmp_path, tests=["passed"] * 14 + [outcome])
    lines = (run / "tests.jsonl").read_text().splitlines()
    last = {**json.loads(lines[-1]), **({"error": error} if error else {})}
    (run / "tests.jsonl").write_text("\n".join(lines[:-1] + [json.dumps(last)]) + "\n")
    (run / "observations.jsonl").write_text(json.dumps({"http": 200}) + "\n" + json.dumps({"http": http}) + "\n")
    return run


@pytest.mark.parametrize("error, http, transient", [
    ("AssertionError: assert stripe_log == sim_log", 200, False),     # a real mismatch
    ("p4.adapters.stripe_test.StripeUnavailable: merchant capture of A000: no response", 200, True),
    ("AssertionError: anomalies", 503, True),
    ("AssertionError: anomalies", 429, True),
    ("urllib.error.URLError: <urlopen error>", 200, True),
])
def test_transient_failures(tmp_path, error, http, transient):
    from analysis.stripe_mapping import attempt
    from analysis.stripe_mapping import transient as is_transient
    run = with_failure(tmp_path, error, http)
    assert is_transient(run) == transient
    a = attempt(run)
    assert a["complete"] == (not transient) and a["commit"] == "0" * 40 and a["name"] == run.name


def test_a_429_without_a_failed_test_is_not_transient(tmp_path):
    from analysis.stripe_mapping import attempt
    run = fake_run(tmp_path, tests=["passed"] * 15)
    (run / "observations.jsonl").write_text(json.dumps({"http": 429}) + "\n")
    assert attempt(run)["complete"]


def test_run_without_tests_jsonl_is_not_complete(tmp_path):
    from analysis.stripe_mapping import attempt
    assert not attempt(fake_run(tmp_path))["complete"]


def test_conftest_keeps_the_failure_line(tmp_path, conformance_conftest):
    m = conformance_conftest
    crash = SimpleNamespace(message="StripeUnavailable: no response\nmore")
    m.pytest_runtest_logreport(SimpleNamespace(nodeid="x", when="call", outcome="failed",
                                               longrepr=SimpleNamespace(reprcrash=crash)))
    m.pytest_runtest_logreport(SimpleNamespace(nodeid="y", when="call", outcome="passed", longrepr=None))
    m._SESSION_DIR.append(tmp_path)
    m.pytest_sessionfinish(None)
    got = [json.loads(x) for x in (tmp_path / "tests.jsonl").read_text().splitlines()]
    assert got == [{"test": "x", "outcome": "failed", "error": "StripeUnavailable: no response"},
                   {"test": "y", "outcome": "passed"}]
