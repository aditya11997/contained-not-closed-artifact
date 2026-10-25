"""Runner: outputs are independent of the worker count; configs expand as specified; the
negative control and the late_cancel ablation are present; the paper-output guard."""
import json
import math

import pytest

from analysis.report import check_paper_output
from experiments import run as runner
from experiments.config import CONFIGS, load_config

SMOKE = str(CONFIGS / "smoke.toml")

DETERMINISTIC = ("trials.csv", "checker_verdicts.jsonl", "checker_summary.json")


def run_smoke(out, workers):
    code = runner.main(["--config", SMOKE, "--partition", "dev", "--n-seeds", "5",
                        "--out", str(out), "--workers", str(workers)])
    assert code == 0
    return out


def test_one_vs_four_workers_identical(tmp_path):
    a, b = run_smoke(tmp_path / "w1", 1), run_smoke(tmp_path / "w4", 4)
    for name in DETERMINISTIC:
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    runs_a = sorted(p.name for p in (a / "runs").iterdir())
    assert runs_a == sorted(p.name for p in (b / "runs").iterdir()) and len(runs_a) == 2
    for name in runs_a:
        assert (a / "runs" / name).read_bytes() == (b / "runs" / name).read_bytes()
    manifest = json.loads((a / "manifest.json").read_text())
    assert manifest["partition"] == "dev" and manifest["seeds"] == {"first": 100000, "last": 100004, "count": 5}
    assert manifest["checker"] == {"runs": 70, "not_ok": 0}


def test_refuses_nonempty_out(tmp_path, capsys):
    out = tmp_path / "out"
    out.mkdir()
    (out / "x").write_text("keep me")
    assert runner.main(["--config", SMOKE, "--partition", "dev", "--out", str(out)]) == 2
    assert (out / "x").read_text() == "keep me"


@pytest.mark.parametrize("name", ["smoke", "primary", "secondary", "negative_control", "scaling"])
def test_configs_load_with_unique_cells(name):
    cfg = load_config(CONFIGS / f"{name}.toml")
    assert cfg.name == name
    assert len({c.cell_id for c in cfg.cells}) == len(cfg.cells)


def test_primary_grid():
    cfg = load_config(CONFIGS / "primary.toml")
    assert len(cfg.cells) == 24 and len(cfg.policies) == 7 and cfg.n_seeds == 2000
    assert {(c.factors["fault"], c.factors["slack"], c.factors["k"], c.factors["n"]) for c in cfg.cells} == {
        (f, s, k, n) for f in ("none", "low", "high") for s in ("tight", "normal") for k in (1, 4) for n in (8, 32)}


def test_smoke_has_two_cells_and_twenty_seeds():
    cfg = load_config(CONFIGS / "smoke.toml")
    assert len(cfg.cells) == 2 and cfg.n_seeds == 20


def test_negative_control_is_k_inf_no_faults_no_jitter():
    cfg = load_config(CONFIGS / "negative_control.toml")
    assert cfg.cells
    for c in cfg.cells:
        assert c.factors["k"] == "inf" and math.isinf(c.k)
        assert c.factors["fault"] == "none" and c.fault_cfg.f_req == c.fault_cfg.f_resp == c.fault_cfg.outage_rate == 0
        assert c.factors["jitter"] == "none" and c.scenario_cfg.jitter.kind == "none"


def test_secondary_has_late_cancel_ablation():
    """G1: the ablation runs at j = 6 with both flag values (at j = 3 it is vacuous)."""
    cfg = load_config(CONFIGS / "secondary.toml")
    abl = [c for c in cfg.cells if c.factors["late_cancel"] is False]
    assert len(abl) == 6 and all(c.factors["jitter"] == "uniform6" for c in abl)
    on = {tuple(sorted((k, str(v)) for k, v in c.factors.items() if k != "late_cancel"))
          for c in cfg.cells if c.factors["late_cancel"] is True and c.factors["jitter"] == "uniform6"}
    assert {tuple(sorted((k, str(v)) for k, v in c.factors.items() if k != "late_cancel")) for c in abl} == on
    assert {"lm_verify", "lm_reconcile", "adaptive"} <= set(cfg.policies)


def test_primary_contrast_family():
    """Holm family {P5-P3, P4-P3} (2026-10-22); P6-P5 (H3) is secondary, outside the family."""
    from analysis.stats import PRIMARY_CONTRASTS, SECONDARY_PRIMARY_GRID_CONTRASTS
    assert set(PRIMARY_CONTRASTS) == {("lm_reconcile", "lm_ack"), ("lm_verify", "lm_ack")}
    assert SECONDARY_PRIMARY_GRID_CONTRASTS == (("adaptive", "lm_reconcile"),)
    assert not set(PRIMARY_CONTRASTS) & set(SECONDARY_PRIMARY_GRID_CONTRASTS)


def test_scaling_sizes():
    cfg = load_config(CONFIGS / "scaling.toml")
    assert {c.factors["n"] for c in cfg.cells} == {8, 32, 128, 512}


def test_cell_id_tracks_preset_parameters(tmp_path):
    faults = (CONFIGS / "faults.toml").read_text().replace("f_resp = 0.15", "f_resp = 0.16")
    (tmp_path / "faults.toml").write_text(faults)
    base = {c.cell_id for c in load_config(CONFIGS / "primary.toml").cells}
    tuned = {c.cell_id for c in load_config(CONFIGS / "primary.toml", faults_path=tmp_path / "faults.toml").cells}
    assert len(base & tuned) == 16   # only the 8 high-fault cells change


def test_paper_outputs_only_from_holdout(tmp_path):
    run = tmp_path / "primary"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"experiment": "primary", "partition": "dev"}))
    with pytest.raises(ValueError, match="non-holdout"):
        check_paper_output({"primary": run}, runner.REPO / "paper" / "generated")
    check_paper_output({"primary": run}, tmp_path / "analysis")
    (run / "manifest.json").write_text(json.dumps({"experiment": "primary", "partition": "holdout"}))
    check_paper_output({"primary": run}, runner.REPO / "paper" / "generated")


def test_alternative_presets_dev_only(tmp_path, capsys):
    faults = (CONFIGS / "faults.toml").read_text().replace("f_resp = 0.15", "f_resp = 0.16")
    (tmp_path / "alt_faults.toml").write_text(faults)
    args = ["--config", SMOKE, "--n-seeds", "2", "--faults", str(tmp_path / "alt_faults.toml"),
            "--presets", str(CONFIGS / "presets.toml"), "--no-keep-runs", "--workers", "1"]
    assert runner.main([*args, "--partition", "holdout", "--out", str(tmp_path / "h")]) == 2
    assert not (tmp_path / "h").exists()
    assert runner.main([*args, "--partition", "dev", "--out", str(tmp_path / "d")]) == 0
    manifest = json.loads((tmp_path / "d" / "manifest.json").read_text())
    assert "alt_faults.toml" in manifest["presets_sha256"] and "faults.toml" not in manifest["presets_sha256"]
