"""analysis/design_macros.py exports the frozen design parameters exactly as configured."""
import tomllib
from pathlib import Path

from analysis.design_macros import design_macros, fmt

REPO = Path(__file__).resolve().parents[2]


def test_fmt():
    assert fmt(2000) == "2{,}000"
    assert fmt(0.05) == "0.05"
    assert fmt(1.0) == "1"
    assert fmt(4) == "4"


def test_fault_presets_match_config():
    faults = tomllib.loads((REPO / "configs" / "faults.toml").read_text())
    m = design_macros(REPO)
    for level, word in (("low", "Low"), ("high", "High")):
        assert m[f"DesignFReq{word}"] == fmt(faults[level]["f_req"])
        assert m[f"DesignFResp{word}"] == fmt(faults[level]["f_resp"])
        assert m[f"DesignOutageRate{word}"] == fmt(faults[level]["outage_rate"])
        assert m[f"DesignOutageLength{word}"] == fmt(faults[level]["outage_length"])
        assert m[f"DesignOutageCommit{word}"] == fmt(faults[level]["outage_commit_prob"])


def test_grid_and_names():
    m = design_macros(REPO)
    primary = tomllib.loads((REPO / "configs" / "primary.toml").read_text())
    b = primary["block"][0]
    assert m["DesignCells"] == fmt(len(b["fault"]) * len(b["slack"]) * len(b["k"]) * len(b["n"]))
    assert m["DesignSeedsPerCell"] == fmt(primary["n_seeds"])
    assert all(name.isalpha() for name in m)


def test_generated_file_is_current():
    """paper/generated/design.tex must equal a fresh export (rerun `make paper` after a config change)."""
    from analysis.tables import numbers_tex  # noqa: F401  (same writer as the CLI)
    out = (REPO / "paper" / "generated" / "design.tex").read_text()
    for name, value in design_macros(REPO).items():
        assert f"\\newcommand{{\\{name}}}{{{value}}}" in out
