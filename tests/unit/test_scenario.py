import dataclasses
from pathlib import Path

import numpy as np
import pytest

from p4.sim.scenario import (
    JitterConfig,
    Observation,
    ScenarioConfig,
    ScoreConfig,
    make_scenario,
    partition_seeds,
)

REPO = Path(__file__).resolve().parents[2]


def _with(cfg=None, **kw):
    return dataclasses.replace(cfg or ScenarioConfig(), **kw)


def test_observation_hides_oracle_fields():
    names = {f.name for f in dataclasses.fields(Observation)}
    assert "D_actual" not in names and "y_suspect" not in names
    obs = make_scenario(100000).observations()
    assert all(isinstance(o, Observation) for o in obs)
    assert not hasattr(obs[0], "D_actual") and not hasattr(obs[0], "y_suspect")


def test_observation_copies_visible_fields():
    sc = make_scenario(100001)
    for p, o in zip(sc.payments, sc.observations()):
        assert (o.uid, o.amount, o.d_nominal, o.s_cancel, o.p_score, o.permitted) == \
            (p.uid, p.amount, p.d_nominal, p.s_cancel, p.p_score, p.permitted)


def test_deterministic_per_seed():
    cfg = _with(jitter=JitterConfig("uniform", 3), score=ScoreConfig("noisy", sigma=1.0))
    assert make_scenario(100002, cfg) == make_scenario(100002, cfg)
    assert make_scenario(100002, cfg) != make_scenario(100003, cfg)


def test_legacy_parameter_ranges():
    for seed in range(100000, 100200):
        for p in make_scenario(seed).payments:
            assert 100 <= p.amount <= 2000 and p.amount % 100 == 0
            assert 0.18 <= p.p_score <= 0.92
            assert 1 <= p.s_cancel <= 5
            assert 5 <= p.d_nominal <= 18
            assert p.D_actual == p.d_nominal  # default jitter is none


@pytest.mark.parametrize("slack,factor", [("tight", 0.7), ("normal", 1.0), ("loose", 1.5)])
def test_slack_scales_nominal_deadline(slack, factor):
    base = make_scenario(100004, _with(slack="normal"))
    scaled = make_scenario(100004, _with(slack=slack))
    for b, s in zip(base.payments, scaled.payments):
        assert s.d_nominal == max(2, round(b.d_nominal * factor))


def test_jitter_models():
    uni = _with(jitter=JitterConfig("uniform", 3))
    early = _with(jitter=JitterConfig("early_only", 3))
    seen_neg = seen_pos = False
    for seed in range(100000, 100300):
        for p in make_scenario(seed, uni).payments:
            assert p.D_actual >= 1 and (abs(p.D_actual - p.d_nominal) <= 3 or p.D_actual == 1)
            seen_neg |= p.D_actual < p.d_nominal
            seen_pos |= p.D_actual > p.d_nominal
        for p in make_scenario(seed, early).payments:
            assert 1 <= p.D_actual <= p.d_nominal
    assert seen_neg and seen_pos


def test_observations_identical_across_jitter_and_score_models():
    # Paired cells: changing hidden models must not change what policies see.
    cfgs = [ScenarioConfig(),
            _with(jitter=JitterConfig("uniform", 3)),
            _with(jitter=JitterConfig("early_only", 2)),
            _with(score=ScoreConfig("shift", delta=0.2)),
            _with(score=ScoreConfig("noisy", sigma=1.0))]
    for seed in range(100000, 100020):
        views = {make_scenario(seed, c).observations() for c in cfgs}
        assert len(views) == 1


def test_calibrated_labels_match_mean_score_within_2pp():
    ps, ys = [], []
    for seed in range(100000, 100625):  # 625 x 8 = 5,000 payments
        for p in make_scenario(seed).payments:
            ps.append(p.p_score)
            ys.append(p.y_suspect)
    assert len(ys) == 5000
    assert abs(np.mean(ys) - np.mean(ps)) < 0.02


def test_shift_moves_realized_rate():
    def rate(cfg):
        return np.mean([p.y_suspect for s in range(100000, 100500)
                        for p in make_scenario(s, cfg).payments])
    base = rate(ScenarioConfig())
    assert rate(_with(score=ScoreConfig("shift", delta=0.2))) > base + 0.1
    assert rate(_with(score=ScoreConfig("shift", delta=-0.2))) < base - 0.1


def test_noisy_sigma_zero_equals_calibrated():
    a = make_scenario(100005)
    b = make_scenario(100005, _with(score=ScoreConfig("noisy", sigma=0.0)))
    assert [p.y_suspect for p in a.payments] == [p.y_suspect for p in b.payments]


def test_horizon_is_after_last_capture():
    sc = make_scenario(100006, _with(jitter=JitterConfig("uniform", 3)))
    assert sc.horizon == max(p.D_actual for p in sc.payments) + 1


def test_partition_seeds():
    assert partition_seeds("dev") == range(100000, 200000)
    assert partition_seeds("holdout") == range(900000, 1000000)
    with pytest.raises(ValueError):
        partition_seeds("pilot")


def test_config_from_toml_and_validation(tmp_path):
    path = tmp_path / "s.toml"
    path.write_text('n = 12\nslack = "tight"\n[jitter]\nkind = "uniform"\nj = 2\n'
                    '[score]\nkind = "noisy"\nsigma = 0.5\n')
    cfg = ScenarioConfig.from_toml(path)
    assert cfg.n == 12 and cfg.slack == "tight"
    assert cfg.jitter == JitterConfig("uniform", 2) and cfg.score.sigma == 0.5
    assert len(make_scenario(100007, cfg).payments) == 12
    assert ScenarioConfig.from_toml(REPO / "configs" / "scenario_default.toml") == ScenarioConfig()
    with pytest.raises(ValueError):
        ScenarioConfig.from_dict({"slack": "medium"})
    with pytest.raises(ValueError):
        ScenarioConfig.from_dict({"jitter": {"kind": "gaussian"}})
    with pytest.raises(ValueError):
        ScenarioConfig.from_dict({"unknown_key": 1})
