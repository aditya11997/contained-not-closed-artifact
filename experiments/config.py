"""Experiment configs (configs/*.toml) expanded into cells.

A config has `name`, `policies`, a default `n_seeds`, an optional `[base]` table of
ScenarioConfig overrides (on top of configs/scenario_default.toml), and one or more
`[[block]]` tables. Each block lists values for every factor in FACTORS; its cells are the
cross product. The config's cells are the union of its blocks in order, without duplicates.

`score` and `jitter` name presets in configs/presets.toml, `fault` names a preset in
configs/faults.toml, and `k = inf` (a TOML float) means unlimited capacity. The cell id is a
stable hash of the factor values *and* the resolved parameters (fault, scenario), so a
retuned preset yields new cell ids.
"""
import dataclasses
import hashlib
import itertools
import json
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

from p4.runbooks import POLICIES
from p4.sim.faults import FaultConfig, load_fault_presets
from p4.sim.scenario import ScenarioConfig

REPO = Path(__file__).resolve().parents[1]
CONFIGS = REPO / "configs"
FAULTS_FILE = CONFIGS / "faults.toml"
PRESETS_FILE = CONFIGS / "presets.toml"
SCENARIO_FILE = CONFIGS / "scenario_default.toml"

FACTORS = ("fault", "slack", "k", "n", "score", "jitter", "c", "late_cancel")
INF = "inf"


@dataclass(frozen=True)
class Cell:
    cell_id: str
    factors: dict          # FACTORS -> value as written to trials.csv (k is int or "inf")
    fault_cfg: FaultConfig
    scenario_cfg: ScenarioConfig

    @property
    def k(self):
        return math.inf if self.factors["k"] == INF else self.factors["k"]


@dataclass(frozen=True)
class ExperimentConfig:
    name: str
    policies: tuple[str, ...]
    n_seeds: int
    cells: tuple[Cell, ...]
    path: Path


def _load_toml(path) -> dict:
    with Path(path).open("rb") as fh:
        return tomllib.load(fh)


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _k_value(k):
    if isinstance(k, float) and math.isinf(k) and k > 0:
        return INF
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError(f"k must be a positive integer or inf, got {k!r}")
    return k


def make_cell(factors: dict, faults: dict, presets: dict, base: dict) -> Cell:
    if factors["fault"] not in faults:
        raise ValueError(f"unknown fault preset {factors['fault']!r}; expected one of {sorted(faults)}")
    for kind in ("jitter", "score"):
        if factors[kind] not in presets.get(kind, {}):
            raise ValueError(f"unknown {kind} preset {factors[kind]!r}")
    if not isinstance(factors["late_cancel"], bool):
        raise ValueError("late_cancel must be true or false")  # noqa: TRY004
    if not isinstance(factors["c"], (int, float)) or isinstance(factors["c"], bool) or factors["c"] < 0:
        raise ValueError(f"c must be a number >= 0, got {factors['c']!r}")
    f = dict(factors, k=_k_value(factors["k"]), c=float(factors["c"]))
    scenario = ScenarioConfig.from_dict({**base, "n": f["n"], "slack": f["slack"],
                                         "jitter": presets["jitter"][f["jitter"]],
                                         "score": presets["score"][f["score"]]})
    fault_cfg = faults[f["fault"]]
    body = {"factors": f, "fault": dataclasses.asdict(fault_cfg),
            "scenario": dataclasses.asdict(scenario)}
    cell_id = hashlib.sha256(canonical_json(body).encode()).hexdigest()[:12]
    return Cell(cell_id, f, fault_cfg, scenario)


def load_config(path, faults_path=FAULTS_FILE, presets_path=PRESETS_FILE,
                scenario_path=SCENARIO_FILE) -> ExperimentConfig:
    path = Path(path)
    data = _load_toml(path)
    unknown = set(data) - {"name", "policies", "n_seeds", "base", "block"}
    if unknown:
        raise ValueError(f"{path}: unknown keys {sorted(unknown)}")
    policies = tuple(data["policies"])
    bad = [p for p in policies if p not in POLICIES]
    if bad or len(set(policies)) != len(policies) or not policies:
        raise ValueError(f"{path}: bad policy list {list(policies)}")
    n_seeds = data["n_seeds"]
    if not isinstance(n_seeds, int) or n_seeds < 1:
        raise ValueError(f"{path}: n_seeds must be a positive integer")

    faults = load_fault_presets(faults_path)
    presets = _load_toml(presets_path)
    base = _load_toml(scenario_path)
    base.pop("jitter", None)
    base.pop("score", None)
    base.update(data.get("base", {}))
    for key in ("n", "slack", "jitter", "score"):
        if key in data.get("base", {}):
            raise ValueError(f"{path}: {key!r} is a factor; set it in the blocks, not [base]")

    blocks = data.get("block", [])
    if not blocks:
        raise ValueError(f"{path}: no [[block]]")
    cells, seen = [], set()
    for i, block in enumerate(blocks):
        if set(block) != set(FACTORS):
            raise ValueError(f"{path}: block {i} must list exactly the factors {FACTORS}; "
                             f"got {sorted(block)}")
        for combo in itertools.product(*(block[f] for f in FACTORS)):
            cell = make_cell(dict(zip(FACTORS, combo)), faults, presets, base)
            if cell.cell_id not in seen:
                seen.add(cell.cell_id)
                cells.append(cell)
    return ExperimentConfig(data["name"], policies, n_seeds, tuple(cells), path)


def presets_for_manifest(faults_path=FAULTS_FILE, presets_path=PRESETS_FILE) -> dict:
    """Names and sha256 of the files the config resolved against."""
    return {Path(p).name: hashlib.sha256(Path(p).read_bytes()).hexdigest()
            for p in (faults_path, presets_path, SCENARIO_FILE)}

