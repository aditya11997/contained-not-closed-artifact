"""Shared helpers for the G1 scripts: run archives, journals and Markdown tables."""
import json
import tarfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from analysis.stats import FACTOR_COLUMNS, cells, load_trials  # noqa: F401  (re-exported)

P_NAMES = {"none": "P0", "edf": "P1", "mh": "P2", "lm_ack": "P3", "lm_verify": "P4",
           "lm_reconcile": "P5", "adaptive": "P6"}
RUN_FILES = ("events.jsonl", "report.json", "truth.json", "journal.jsonl")


def manifest(results: Path) -> dict:
    return json.loads((Path(results) / "manifest.json").read_text())


def cell_params(results: Path) -> dict[str, dict]:
    """cell_id -> {"factors", "fault", "scenario"} from manifest.json."""
    return {c["cell_id"]: c for c in manifest(results)["cells"]}


def _parse(name: str, raw: bytes):
    if name.endswith(".jsonl"):
        return [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    return json.loads(raw)


def iter_runs(results: Path, cell_id: str, policies=None):
    """Yield (seed, policy, {file: parsed}) for every archived run of one cell, in archive
    order (seed chunks, then policy). Reads the tar.gz directly; nothing is extracted."""
    path = Path(results) / "runs" / f"{cell_id}.tar.gz"
    current, files = None, {}
    with tarfile.open(path, "r:gz") as tar:
        for member in tar:
            _, seed, policy, name = member.name.split("/")
            key = (int(seed), policy)
            if current is not None and key != current:
                yield (*current, files)
                files = {}
            current = key
            if policies is not None and policy not in policies:
                continue
            files[name] = _parse(name, tar.extractfile(member).read())
    if current is not None and files:
        yield (*current, files)


def iter_seed_groups(results: Path, cell_id: str, policies):
    """Yield (seed, {policy: files}) once every requested policy of a seed has been read."""
    pending = defaultdict(dict)
    for seed, policy, files in iter_runs(results, cell_id, policies):
        if policy in policies and files:
            pending[seed][policy] = files
            if len(pending[seed]) == len(policies):
                yield seed, pending.pop(seed)


def intents(journal) -> list[dict]:
    return [r for r in journal if r["kind"] == "intent"]


def timeout_applied(journal, events) -> list[bool]:
    """For every cancel/replay that timed out: did the sink apply a cancel for that payment
    at the moment the call arrived (drop_resp or outage commit)? The sink applies a cancel at
    arrival time, which is the journaled result time."""
    applied_at = {(e["uid"], e["t"]) for e in events if e["type"] == "CANCELLED"}
    return [(r["uid"], r["t"]) in applied_at for r in journal
            if r["kind"] == "result" and r["op"] in ("cancel", "replay") and r["timeout"]
            and not r["recovered"]]


def fmt(x, digits: int = 1) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "–"
    if isinstance(x, (int, np.integer)):
        return str(int(x))
    return f"{x:.{digits}f}"


def ci_str(mean, lo, hi, digits: int = 1) -> str:
    star = "*" if (lo > 0 or hi < 0) else ""
    return f"{fmt(mean, digits)} [{fmt(lo, digits)}, {fmt(hi, digits)}]{star}"


def md_table(df: pd.DataFrame) -> str:
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join(str(v) for v in row) + " |")
    return "\n".join(lines)


def cell_label(row) -> str:
    return f"{row['fault']}/{row['slack']}/k{row['k']}/n{row['n']}"


def write_section(out: Path, name: str, text: str) -> Path:
    path = Path(out) / f"g1_section_{name}.md"
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return path
