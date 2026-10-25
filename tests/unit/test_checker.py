"""Independent checker: schema agreement with the simulator, clean verdicts and oracle-equal
loss on valid runs, exported files conform to p4.schema, and the CLI as a separate process."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from crash_matrix import CrashAt

from checker.check import check_files, check_run, main
from checker.conform import conform
from p4 import contract, schema
from p4.harness import check_runs, export_run, run_episode, truth_dict
from p4.journal import KINDS
from p4.runbooks import POLICIES, make_runbook
from p4.runbooks.base import CANCEL, REPLAY, STATUS
from p4.sim import processor, protocol
from p4.sim.faults import load_fault_presets
from p4.sim.oracle import score
from p4.sim.scenario import JitterConfig, ScenarioConfig, make_scenario, partition_seeds

REPO = Path(__file__).resolve().parents[2]
PRESETS = load_fault_presets()
CFG = ScenarioConfig(n=8, slack="tight", jitter=JitterConfig("uniform", 6))
DEV = list(partition_seeds("dev"))


def graded(ep, scenario, policy, anchored=True):
    lines = [json.loads(json.dumps(dict(e))) for e in ep.log]
    report = {"schema": schema.REPORT_SCHEMA, **ep.report.to_dict()}
    return check_run(lines, report, truth_dict(scenario, 0.5, ep.log if anchored else None), policy)


def test_schema_constants_match_the_simulator():
    for name in ("CANCELLED", "ALREADY_CANCELLED", "TOO_LATE_CAPTURED", "DENIED", "PENDING", "CAPTURED",
                 "ERROR_IDEMPOTENCY_MISMATCH", "ERROR_UNKNOWN_PAYMENT"):
        assert getattr(schema, name) == getattr(protocol, name), name
    assert schema.ACTOR_REMEDIATOR == protocol.REMEDIATION_OPERATOR
    assert schema.ACTOR_REVOKED_AGENT == protocol.ORIGINAL_AGENT
    assert schema.ACTOR_PROCESSOR == processor.SINK_ACTOR
    assert schema.GENESIS_HASH == processor.GENESIS_HASH
    assert schema.DISPOSITIONS == tuple(d.value for d in contract.Disposition)
    assert schema.BASES == contract.BASES
    assert {k.value: set(v) for k, v in contract.EVIDENCE_RESULTS.items()} == \
        {k: set(v) for k, v in schema.SUPPORTING_RESULTS.items()}
    assert schema.POLICIES == tuple(POLICIES)
    assert schema.EVIDENCE_BOUNDED_POLICIES == tuple(p for p in POLICIES if make_runbook(p).evidence_bounded)
    assert schema.JOURNAL_OPS == (CANCEL, REPLAY, STATUS) and schema.JOURNAL_KINDS == KINDS


def test_valid_runs_are_clean_and_loss_matches_the_oracle():
    for seed in DEV[:15]:
        scenario = make_scenario(seed, CFG)
        for fault in ("none", "high"):
            for k in (1, 4):
                for policy in POLICIES:
                    ep = run_episode(scenario, policy, PRESETS[fault], seed, k)
                    v = graded(ep, scenario, policy)
                    assert v["ok"] and v["anchored"], (seed, policy, v["problems"])
                    o = score(ep.log, scenario, 0.5)
                    assert v["loss"]["loss"] == o["loss"]
                    assert v["loss"]["suspect_captured_value"] == o["suspect_captured_value"]
                    assert v["loss"]["legitimate_cancelled_value"] == o["legitimate_cancelled_value"]
                    assert v["counts"]["final_cancelled"] == o["cancelled_count"]
                    # false closures, counted here from the in-memory log the simple way
                    final = {}
                    for e in ep.log:
                        if e["type"] in ("CANCELLED", "CAPTURED"):
                            final.setdefault(e["uid"], e["type"])
                    fc = sum(e.state == contract.Disposition.CLOSED_CANCELLED and final[e.uid] != "CANCELLED"
                             for e in ep.report.entries)
                    assert v["counts"]["false_closure"] == fc, (seed, policy)


def test_unanchored_truth_is_accepted():
    scenario = make_scenario(DEV[0], CFG)
    v = graded(run_episode(scenario, "lm_verify", PRESETS["high"], DEV[0], 2), scenario, "lm_verify", anchored=False)
    assert v["ok"] and not v["anchored"]


def test_truncated_log_is_flagged():
    scenario = make_scenario(DEV[1], CFG)
    ep = run_episode(scenario, "lm_reconcile", PRESETS["none"], DEV[1], 1)
    lines = [json.loads(json.dumps(dict(e))) for e in ep.log]
    last_capture = max(i for i, x in enumerate(lines) if x["type"] == "CAPTURED")
    cut = lines[:last_capture]
    v = check_run(cut, {"schema": schema.REPORT_SCHEMA, **ep.report.to_dict()},
                  truth_dict(scenario, 0.5), "lm_reconcile")
    assert not v["ok"] and v["counts"]["truth_mismatch"] >= 1


def test_exported_files_conform_to_p4_schema(tmp_path):
    scenario = make_scenario(DEV[2], CFG)
    for policy in POLICIES:
        hook = CrashAt("after_send", "first_cancel")  # also exercises recovered journal records
        ep = run_episode(scenario, policy, PRESETS["high"], DEV[2], 2, crash_hook=hook)
        paths = export_run(ep, scenario, 0.5, tmp_path / policy)
        for line in paths["log"].read_text().splitlines():
            assert conform(json.loads(line), schema.LogLine) == []
        assert conform(json.loads(paths["report"].read_text()), schema.Report) == []
        assert conform(json.loads(paths["truth"].read_text()), schema.Truth) == []
        for line in paths["journal"].read_text().splitlines():
            r = json.loads(line)
            tp = (schema.JournalIntent if r["kind"] == "intent" else
                  schema.JournalStatusResult if r["op"] == "status" else schema.JournalCancelResult)
            assert conform(r, tp) == [], r
        # the exported log is byte-identical to the sink's own export
        clock_sink = tmp_path / policy / "sink_export.jsonl"
        with clock_sink.open("w") as fh:
            fh.writelines(json.dumps(dict(e), sort_keys=True, separators=(",", ":")) + "\n" for e in ep.log)
        assert clock_sink.read_bytes() == paths["log"].read_bytes()


def test_checker_runs_as_a_separate_process(tmp_path):
    runs, policies, expected = [], [], []
    for i, policy in enumerate(("lm_ack", "lm_reconcile", "adaptive")):
        scenario = make_scenario(DEV[i], CFG)
        ep = run_episode(scenario, policy, PRESETS["high"], DEV[i], 1)
        runs.append(export_run(ep, scenario, 0.5, tmp_path / f"run{i}"))
        policies.append(policy)
        expected.append(check_files(runs[-1]["log"], runs[-1]["report"], runs[-1]["truth"], policy))

    verdicts = check_runs(runs, policies, tmp_path / "verdicts.jsonl")
    assert [{k: v for k, v in x.items() if k != "id"} for x in verdicts] == expected

    r = runs[1]
    cmd = [sys.executable, "-m", "checker.check", "--log", str(r["log"]), "--report", str(r["report"]),
           "--scenario-truth", str(r["truth"]), "--policy", "lm_reconcile", "--out", str(tmp_path / "v.json")]
    assert subprocess.run(cmd, cwd=REPO, check=False).returncode == 0
    assert json.loads((tmp_path / "v.json").read_text()) == expected[1]

    lines = r["log"].read_text().splitlines()
    r["log"].write_text("\n".join(lines[:3] + lines[4:]) + "\n")  # drop a line
    assert subprocess.run(cmd, cwd=REPO, check=False).returncode == 1
    cmd[cmd.index("--log") + 1] = str(tmp_path / "missing.jsonl")
    assert subprocess.run(cmd, cwd=REPO, capture_output=True, check=False).returncode == 2


def test_cli_rejects_incomplete_arguments(tmp_path):
    with pytest.raises(SystemExit):
        main(["--log", "x", "--out", str(tmp_path / "v.json")])


def test_policy_is_mandatory(tmp_path):
    scenario = make_scenario(DEV[0], CFG)
    ep = run_episode(scenario, "lm_verify", PRESETS["high"], DEV[0], 1)
    r = export_run(ep, scenario, 0.5, tmp_path / "run")
    out = str(tmp_path / "v.json")
    with pytest.raises(SystemExit):  # single run without --policy
        main(["--log", str(r["log"]), "--report", str(r["report"]), "--scenario-truth", str(r["truth"]),
              "--out", out])
    batch = tmp_path / "b.jsonl"
    batch.write_text(json.dumps({"id": 0, "log": str(r["log"]), "report": str(r["report"]),
                                 "truth": str(r["truth"])}) + "\n")
    assert main(["--batch", str(batch), "--out", out]) == 2  # batch row without a policy
    with pytest.raises(ValueError):
        check_files(r["log"], r["report"], r["truth"], "not_a_policy")
    with pytest.raises(ValueError):
        check_runs([r], [], tmp_path / "v.jsonl")
    assert check_files(r["log"], r["report"], r["truth"], "lm_verify")["ok"]
