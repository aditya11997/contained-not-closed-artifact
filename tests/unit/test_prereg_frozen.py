"""docs/PREREG.md is complete, matches the code and configs it cites, and (once tagged) never
changes (CLAUDE.md rule 7; WP7)."""
import hashlib
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from analysis import crash_matrix as acm
from analysis import stats
from experiments import crash_matrix as cm
from experiments.config import load_config
from experiments.guard import PREREG_PATH, PREREG_TAG
from p4.sim.scenario import PARTITIONS, SLACK_FACTORS

REPO = Path(__file__).resolve().parents[2]
PREREG = REPO / PREREG_PATH
TEXT = PREREG.read_text(encoding="utf-8")
HOLDOUT_FIRST, HOLDOUT_SEEDS = 900000, 2000
EXPERIMENTS = ("primary", "secondary", "negative_control", "scaling")
P_IDS = {"none": "P0", "edf": "P1", "mh": "P2", "lm_ack": "P3", "lm_verify": "P4", "lm_reconcile": "P5",
         "adaptive": "P6"}


def section(title: str) -> str:
    return TEXT.split(title, 1)[1].split("\n## ", 1)[0]


def md_table(text: str) -> list[list[str]]:
    rows = [ln.strip().strip("|").split("|") for ln in text.splitlines() if ln.startswith("|")]
    return [[c.strip().strip("`") for c in r] for r in rows[2:]]   # drop header and rule


def test_no_placeholders_left():
    for marker in ("⟨", "⟩", "TODO", "TBD", "RUNTIME_TABLE", "XXX"):
        assert marker not in TEXT, marker


def test_cited_file_hashes_match_this_commit():
    cited = dict(md_table(section("## 1. Identity")))
    expected = {f"configs/{n}.toml" for n in ("faults", "presets", "scenario_default", *EXPERIMENTS)}
    assert set(cited) == expected | {"experiments/crash_matrix.py", "analysis/stripe_mapping.py"}
    for path, digest in cited.items():
        assert hashlib.sha256((REPO / path).read_bytes()).hexdigest() == digest, path


def test_fault_table_matches_faults_toml():
    presets = tomllib.loads((REPO / "configs" / "faults.toml").read_text())
    table = [r for r in md_table(section("### 5.1")) if r[0] in presets]
    assert [r[0] for r in table] == ["none", "low", "high"]
    keys = ("f_req", "f_resp", "outage_rate", "outage_length", "outage_commit_prob")
    for name, *values in table:
        assert [float(v) for v in values] == [float(presets[name][k]) for k in keys], name


def test_policies_match_configs():
    rows = {r[1]: r[0] for r in md_table(section("## 3. Policies"))}
    assert rows == P_IDS
    for name in EXPERIMENTS:
        assert set(load_config(REPO / "configs" / f"{name}.toml").policies) == set(P_IDS), name


def test_design_values_match_configs():
    primary = load_config(REPO / "configs" / "primary.toml")
    assert len(primary.cells) == 24 and "24 cells" in TEXT
    assert {c.factors["jitter"] for c in primary.cells} == {"uniform3"}
    assert {c.factors["c"] for c in primary.cells} == {0.5} and "c = 0.5" in TEXT
    assert {c.factors["late_cancel"] for c in primary.cells} == {True}
    assert len(load_config(REPO / "configs" / "secondary.toml").cells) == 54 and "54 cells" in TEXT
    neg = load_config(REPO / "configs" / "negative_control.toml")
    assert len(neg.cells) == 4 and {c.factors["jitter"] for c in neg.cells} == {"none"}
    assert {c.factors["fault"] for c in neg.cells} == {"none"}
    assert f"tight = {SLACK_FACTORS['tight']}" in TEXT and f"normal = {SLACK_FACTORS['normal']}" in TEXT


def test_seeds():
    assert PARTITIONS["holdout"].start == HOLDOUT_FIRST
    assert f"Holdout seeds {HOLDOUT_FIRST}–{HOLDOUT_FIRST + HOLDOUT_SEEDS - 1}" in TEXT
    for name in ("primary", "secondary", "negative_control"):
        assert load_config(REPO / "configs" / f"{name}.toml").n_seeds == HOLDOUT_SEEDS, name
    scaling = load_config(REPO / "configs" / "scaling.toml").n_seeds
    assert f"{HOLDOUT_FIRST}–{HOLDOUT_FIRST + scaling - 1}" in TEXT
    assert f"--n-seeds {HOLDOUT_SEEDS} --out results/holdout/crash_matrix" in TEXT


def test_statistics_match_analysis():
    assert stats.PRIMARY_CONTRASTS == (("lm_reconcile", "lm_ack"), ("lm_verify", "lm_ack"))
    assert "{P5 − P3, P4 − P3} × 24 primary cells = 48 contrasts" in TEXT
    assert stats.SECONDARY_PRIMARY_GRID_CONTRASTS == (("adaptive", "lm_reconcile"),)
    assert f"N_BOOT = {stats.N_BOOT:,}" in TEXT and f"BOOT_SEED = {stats.BOOT_SEED}" in TEXT
    assert f"ALPHA = {stats.ALPHA}" in TEXT
    assert stats.NEGATIVE_CONTROL_POLICIES == ("lm_ack", "lm_verify", "lm_reconcile", "adaptive")


def test_h5_matches_the_crash_matrix_code():
    assert acm.H5_POLICIES == cm.ASSURED == ("lm_verify", "lm_reconcile", "adaptive")
    assert f"≥ {acm.MIN_CRASHED} crashed runs" in TEXT
    pairs = sum(cm.applies(p, t) for p in cm.POLICIES for _, t in cm.CELLS)
    assert f"{pairs} (cell, policy) pairs" in TEXT
    assert tuple(cm.FAULTS) == ("none", "high", "crash_outage") and cm.KS == (1, 3)
    o = cm.OUTAGE
    assert (o.outage_rate, o.outage_length, o.outage_commit_prob, o.f_req, o.f_resp) == (0.1, 3, 0.5, 0, 0)
    assert "outage_rate 0.1, outage_length 3, outage_commit_prob 0.5" in TEXT
    assert (cm.SCENARIO.n, cm.SCENARIO.slack, cm.SCENARIO.jitter.j, cm.LATE_SCENARIO.jitter.j) == (8, "tight", 6, 15)
    assert cm.C == 0.5


def test_every_hypothesis_is_declared():
    for h in ("**H1**", "**H2** (primary)", "H3 (secondary)", "**H4** (negative control)", "**H5** (crash safety)",
              "**C1**"):
        assert h in section("## 4. Hypotheses"), h
    assert "**descriptive**" in section("### 5.7")


def _git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, check=False)


def test_unchanged_since_the_tag():
    tag = _git("rev-parse", "-q", "--verify", f"refs/tags/{PREREG_TAG}^{{commit}}")
    if tag.returncode != 0:
        pytest.skip(f"{PREREG_TAG} not tagged yet")
    tagged = _git("cat-file", "blob", f"{tag.stdout.decode().strip()}:{PREREG_PATH}")
    assert tagged.returncode == 0 and PREREG.read_bytes() == tagged.stdout
    cited = dict(md_table(section("## 1. Identity")))
    for path in cited:   # the cited files are frozen too
        at_tag = _git("cat-file", "blob", f"{PREREG_TAG}:{path}")
        assert at_tag.returncode == 0 and (REPO / path).read_bytes() == at_tag.stdout, path
    assert re.search(r"Freeze date: \d{4}-\d{2}-\d{2}", TEXT)
