"""Synthetic pre-revocation payments: hidden truth, policy observations, seed partitions.

Times are integer simulated units, not card-network deadlines. Parameter ranges follow
the legacy pilot (research_code/p4lab/model.py).
"""
import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .streams import STREAM_SCENARIO, SUB_JITTER, SUB_LABEL, SUB_OBSERVED

SLACK_FACTORS = {"tight": 0.7, "normal": 1.0, "loose": 1.5}
JITTER_KINDS = ("none", "uniform", "early_only")
SCORE_KINDS = ("calibrated", "shift", "noisy")
PARTITIONS = {"dev": range(100000, 200000), "holdout": range(900000, 1000000)}


@dataclass(frozen=True)
class PaymentTruth:
    uid: str
    amount: int
    d_nominal: int
    D_actual: int       # hidden: actual capture time
    s_cancel: int
    p_score: float
    y_suspect: bool     # hidden: oracle label
    permitted: bool


@dataclass(frozen=True)
class Observation:
    """What a policy sees: everything except D_actual and y_suspect."""
    uid: str
    amount: int
    d_nominal: int
    s_cancel: int
    p_score: float
    permitted: bool


@dataclass(frozen=True)
class JitterConfig:
    """D = d + eps, eps integer: `uniform` on {-j..j}, `early_only` on {-j..0}; D >= 1."""
    kind: str = "none"
    j: int = 0

    def __post_init__(self):
        if self.kind not in JITTER_KINDS:
            raise ValueError(f"jitter kind must be one of {JITTER_KINDS}, got {self.kind!r}")
        if self.j < 0:
            raise ValueError("jitter j must be >= 0")


@dataclass(frozen=True)
class ScoreConfig:
    """True suspect probability given score p: `calibrated` p, `shift` clip(p + delta),
    `noisy` logistic(logit(p) + N(0, sigma))."""
    kind: str = "calibrated"
    delta: float = 0.0
    sigma: float = 0.0

    def __post_init__(self):
        if self.kind not in SCORE_KINDS:
            raise ValueError(f"score kind must be one of {SCORE_KINDS}, got {self.kind!r}")
        if self.sigma < 0:
            raise ValueError("score sigma must be >= 0")


@dataclass(frozen=True)
class ScenarioConfig:
    n: int = 8
    slack: str = "normal"
    amount_min: int = 100
    amount_max: int = 2000
    amount_step: int = 100
    d_min: int = 5
    d_max: int = 18
    d_floor: int = 2
    s_min: int = 1
    s_max: int = 5
    p_min: float = 0.18
    p_max: float = 0.92
    permit_prob: float = 0.94
    q: int = 1  # status-read cost, consumed from the caller's worker time
    jitter: JitterConfig = field(default_factory=JitterConfig)
    score: ScoreConfig = field(default_factory=ScoreConfig)

    def __post_init__(self):
        if self.slack not in SLACK_FACTORS:
            raise ValueError(f"slack must be one of {tuple(SLACK_FACTORS)}, got {self.slack!r}")
        if self.n < 1 or self.q < 0:
            raise ValueError("n must be >= 1 and q >= 0")
        if not (0 < self.amount_min <= self.amount_max and self.amount_step > 0
                and 1 <= self.d_min <= self.d_max and 1 <= self.s_min <= self.s_max
                and 0 < self.p_min <= self.p_max < 1 and 0 <= self.permit_prob <= 1):
            raise ValueError("inconsistent scenario parameter ranges")

    @classmethod
    def from_dict(cls, data: dict) -> "ScenarioConfig":
        data = dict(data)
        known = {f.name for f in dataclasses.fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown scenario config keys: {sorted(unknown)}")
        if "jitter" in data:
            data["jitter"] = JitterConfig(**data["jitter"])
        if "score" in data:
            data["score"] = ScoreConfig(**data["score"])
        return cls(**data)

    @classmethod
    def from_toml(cls, path) -> "ScenarioConfig":
        with Path(path).open("rb") as fh:
            return cls.from_dict(tomllib.load(fh))


@dataclass(frozen=True)
class Scenario:
    payments: tuple[PaymentTruth, ...]
    seed: int | None = None
    config: ScenarioConfig | None = None

    def observations(self) -> tuple[Observation, ...]:
        return tuple(Observation(p.uid, p.amount, p.d_nominal, p.s_cancel, p.p_score, p.permitted)
                     for p in self.payments)

    @property
    def horizon(self) -> int:
        """First time after every capture."""
        return max(p.D_actual for p in self.payments) + 1


def _rng(seed: int, sub: int) -> np.random.Generator:
    return np.random.default_rng([seed, STREAM_SCENARIO, sub])


DEFAULT_CONFIG = ScenarioConfig()


def make_scenario(seed: int, cfg: ScenarioConfig = DEFAULT_CONFIG) -> Scenario:
    n = cfg.n
    obs = _rng(seed, SUB_OBSERVED)
    steps = (cfg.amount_max - cfg.amount_min) // cfg.amount_step
    amounts = cfg.amount_min + cfg.amount_step * obs.integers(0, steps + 1, size=n)
    scores = np.round(obs.uniform(cfg.p_min, cfg.p_max, size=n), 3)
    base_d = obs.integers(cfg.d_min, cfg.d_max + 1, size=n)
    durations = obs.integers(cfg.s_min, cfg.s_max + 1, size=n)
    permitted = obs.random(size=n) < cfg.permit_prob
    factor = SLACK_FACTORS[cfg.slack]
    d_nominal = [max(cfg.d_floor, round(int(b) * factor)) for b in base_d]

    jit = _rng(seed, SUB_JITTER)
    j = cfg.jitter.j
    if cfg.jitter.kind == "uniform":
        eps = jit.integers(-j, j + 1, size=n)
    elif cfg.jitter.kind == "early_only":
        eps = jit.integers(-j, 1, size=n)
    else:
        eps = np.zeros(n, dtype=int)

    lab = _rng(seed, SUB_LABEL)
    u = lab.random(size=n)  # drawn first so every score model shares these uniforms
    if cfg.score.kind == "shift":
        true_p = np.clip(scores + cfg.score.delta, 0.0, 1.0)
    elif cfg.score.kind == "noisy":
        z = lab.normal(0.0, cfg.score.sigma, size=n)
        true_p = np.clip(1.0 / (1.0 + np.exp(-(np.log(scores / (1 - scores)) + z))), 0.0, 1.0)
    else:
        true_p = scores

    payments = tuple(
        PaymentTruth(uid=f"P{i:03d}", amount=int(amounts[i]), d_nominal=int(d_nominal[i]),
                     D_actual=max(1, int(d_nominal[i] + eps[i])), s_cancel=int(durations[i]),
                     p_score=float(scores[i]), y_suspect=bool(u[i] < true_p[i]),
                     permitted=bool(permitted[i]))
        for i in range(n))
    return Scenario(payments=payments, seed=seed, config=cfg)


def partition_seeds(name: str) -> range:
    """Seed partitions (CLAUDE.md rule 3). Holdout use is gated by the runner (WP4)."""
    if name not in PARTITIONS:
        raise ValueError(f"unknown seed partition {name!r}; expected one of {tuple(PARTITIONS)}")
    return PARTITIONS[name]
