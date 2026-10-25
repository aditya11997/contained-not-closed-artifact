"""Golden regression (CODE_AUDIT E5): configs/smoke.toml on 20 dev seeds must reproduce the
committed golden_trials.csv byte for byte. Regenerate deliberately with `make golden` and log
why in docs/CHANGELOG_RESEARCH.md."""
from pathlib import Path

from experiments import run as runner
from experiments.config import CONFIGS

SMOKE = str(CONFIGS / "smoke.toml")

GOLDEN = Path(__file__).resolve().parent / "golden_trials.csv"


def test_smoke_matches_golden(tmp_path):
    out = tmp_path / "smoke"
    code = runner.main(["--config", SMOKE, "--partition", "dev", "--out", str(out),
                        "--workers", "1", "--no-keep-runs"])
    assert code == 0
    assert (out / "trials.csv").read_bytes() == GOLDEN.read_bytes()
