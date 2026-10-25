"""Shared helpers for the post-hoc scripts (docs/POSTHOC.md)."""
import json
from pathlib import Path

import pandas as pd

from analysis.stats import load_trials

CELL_COLUMNS = ("cell_id", "fault", "slack", "k", "n")


def load_dev_run(run_dir) -> tuple[pd.DataFrame, dict]:
    """trials.csv and manifest.json of a run directory; PH2 and PH3 use dev seeds only."""
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("partition") != "dev":
        raise ValueError(f"{run_dir}: partition {manifest.get('partition')!r}; post-hoc runs are dev only")
    if manifest.get("checker", {}).get("not_ok", 1) != 0:
        raise ValueError(f"{run_dir}: checker not-ok runs {manifest.get('checker')}; inspect before analysing")
    return load_trials(run_dir), manifest


def false_closures(trials: pd.DataFrame, policies) -> pd.DataFrame:
    """Summed false closures and CLOSED_CANCELLED per fault level and policy."""
    t = trials[trials["policy"].isin(policies)]
    g = t.groupby(["fault", "policy"], sort=False).agg(
        runs=("seed", "size"), false_closures=("false_closure", "sum"),
        closed_cancelled=("closed_cancelled", "sum"), cancel_calls=("n_cancel_calls", "mean"),
        replays=("n_replays", "mean"), status_calls=("n_status_calls", "mean")).reset_index()
    g["fc_share_of_closed_cancelled"] = g["false_closures"] / g["closed_cancelled"]
    return g
