"""The "observed" column of docs/STRIPE_MAPPING.md and the paper's Stripe counts, from a
conformance run directory.

    python -m analysis.stripe_mapping results/conformance/stripe-<UTC time> [--tex stripe.tex]

Prints one markdown row per recorded transition: ✅ if the Stripe outcome matched the
simulator's (identical event logs and replies, no adapter anomaly), ❌ otherwise; plus the
run's commit and dirty flag. Nothing is typed by hand.

Two different counts come out of a run, and the paper states both:
  - mapping-table rows: the rows of the transition table in docs/STRIPE_MAPPING.md (TABLE_ROWS
    below names the outcome records each row rests on); a row counts as matched when every
    one of its outcome records is present and matched;
  - conformance tests: one per scripted transition (SCRIPTS) and one per episode (EPISODES).
    Outcome records come from outcomes.jsonl. Tests run and passed come from tests.jsonl,
    which the conformance conftest writes from pytest's own reports. Runs before 2026-10-23
    have no tests.jsonl, so for them those macros are not written rather than guessed.

Transient failures (PREREG §5.5, C1 retries): a run is *transient* if at least one test failed
and either Stripe answered 429 or 5xx (observations.jsonl) or a failure names a network error
(TRANSIENT_ERRORS). A run with tests.jsonl that is not transient is *complete*.
"""
import argparse
import json
import sys
from pathlib import Path

from analysis.tables import macro, numbers_tex

SCRIPTS = (
    "create_manual_capture",
    "cancel_in_requires_capture",
    "capture_after_cancel",
    "cancel_after_capture",
    "replay_same_key_same_params",
    "same_key_different_params",
    "cancel_already_cancelled",
    "drop_resp_lost_ack_then_replay",
    "revoked_agent_post_stop",
)
EPISODES = tuple(f"episode/{policy}/{fault}" for policy in ("lm_ack", "lm_reconcile", "adaptive")
                 for fault in ("none", "high"))

TRANSIENT_ERRORS = ("StripeUnavailable", "TransportError", "URLError", "TimeoutError", "ConnectionError")

# Row number of the docs/STRIPE_MAPPING.md transition table -> the outcome records it rests on.
TABLE_ROWS: dict[int, tuple[str, ...]] = {
    1: ("create_manual_capture",),
    2: ("cancel_in_requires_capture",),
    3: ("cancel_after_capture",),
    4: ("capture_after_cancel", "cancel_in_requires_capture"),
    5: ("cancel_after_capture", "same_key_different_params"),
    6: ("cancel_already_cancelled",),
    7: ("replay_same_key_same_params",),
    8: ("same_key_different_params", "create_manual_capture"),
    9: ("create_manual_capture",),
    10: SCRIPTS[:7],   # the status read after each of rows 1-9
    11: ("revoked_agent_post_stop",),
    12: ("drop_resp_lost_ack_then_replay",),
    13: EPISODES,
}


def rows(run_dir: Path) -> list[dict]:
    return [json.loads(x) for x in (run_dir / "outcomes.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]


def test_results(run_dir: Path) -> list[dict] | None:
    path = run_dir / "tests.jsonl"
    if not path.exists():
        return None
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def transient(run_dir: Path) -> bool:
    tests = test_results(run_dir) or []
    failed = [t for t in tests if t["outcome"] in ("failed", "error")]
    if not failed:
        return False
    obs_path = run_dir / "observations.jsonl"
    statuses = [json.loads(x).get("http") for x in obs_path.read_text(encoding="utf-8").splitlines()
                if x.strip()] if obs_path.exists() else []
    if any(isinstance(s, int) and (s == 429 or s >= 500) for s in statuses):
        return True
    return any(name in t.get("error", "") for t in failed for name in TRANSIENT_ERRORS)


def attempt(run_dir: Path) -> dict:
    """One C1 attempt: its name, commit, dirty flag, counts, and whether it is complete."""
    run_dir = Path(run_dir)
    git = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")).get("git", {})
    c = counts(run_dir)
    return {"name": run_dir.name, "commit": git.get("commit"), "dirty": git.get("dirty"), "counts": c,
            "complete": "TestsRun" in c and not transient(run_dir)}


def render(run_dir: Path) -> str:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    git = manifest.get("git", {})
    out = [(f"Run `{run_dir.name}` · commit `{(git.get('commit') or '?')[:12]}` · dirty {git.get('dirty')} · "
            f"Stripe-Version `{manifest.get('stripe_version')}`"), "",
           "| transition | observed | detail |", "|---|---|---|"]
    for r in rows(run_dir):
        detail = []
        if "verdicts" in r:
            detail.append("checker ok " + "/".join(str(v["ok"]).lower() for v in r["verdicts"]))
        if r.get("faults"):
            faults = [f for f in r["faults"] if f != "ok"]
            detail.append(f"{len(faults)} injected faults of {len(r['faults'])} calls")
        if r.get("anomalies"):
            detail.append(f"{len(r['anomalies'])} anomalies")
        out.append(f"| `{r['transition']}` | {'✅' if r['match'] else '❌'} | {'; '.join(detail)} |")
    return "\n".join(out)


def counts(run_dir: Path) -> dict[str, int]:
    """Every Stripe count the paper uses, from one run directory."""
    recorded = rows(run_dir)
    matched = {r["transition"] for r in recorded if r["match"]}
    # A transition recorded twice counts as matched only if every record matched.
    matched -= {r["transition"] for r in recorded if not r["match"]}
    c = {
        "MappingRows": len(TABLE_ROWS),
        "MappingRowsMatched": sum(all(t in matched for t in ev) for ev in TABLE_ROWS.values()),
        "TransitionScripts": len(SCRIPTS),
        "Episodes": len(EPISODES),
        "ConformanceTests": len(SCRIPTS) + len(EPISODES),
        "OutcomesRecorded": len(recorded),
        "OutcomesMatched": sum(r["match"] for r in recorded),
        "ScriptsMatched": sum(t in matched for t in SCRIPTS),
        "EpisodesMatched": sum(t in matched for t in EPISODES),
    }
    tests = test_results(run_dir)
    if tests is not None:
        c["TestsRun"] = sum(t["outcome"] != "skipped" for t in tests)
        c["TestsPassed"] = sum(t["outcome"] == "passed" for t in tests)
    return c


def stripe_tex(run_dir: Path) -> str:
    macros = dict(macro("Stripe" + name, str(value)) for name, value in counts(run_dir).items())
    return numbers_tex(macros, f"Stripe conformance counts from {run_dir.name}",
                       source="analysis/stripe_mapping.py")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m analysis.stripe_mapping", description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--tex", type=Path, help="also write the counts as LaTeX macros (\\Stripe...)")
    args = ap.parse_args(argv)
    print(render(args.run_dir))
    if args.tex:
        args.tex.parent.mkdir(parents=True, exist_ok=True)
        args.tex.write_text(stripe_tex(args.run_dir), encoding="utf-8")
        print(f"wrote {args.tex}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
