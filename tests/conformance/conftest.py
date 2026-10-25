"""Stripe conformance (WP5, hypothesis C1): live, so skipped unless STRIPE_TEST_KEY is set and
the run opts in with `--run-stripe` or P4_RUN_STRIPE=1 (tests/conftest.py).

Each live session writes results/conformance/stripe-<UTC time>/ (root overridable with
P4_CONFORMANCE_OUT) with manifest.json,
outcomes.jsonl (one row per scripted transition: sim outcome, Stripe outcome, match),
tests.jsonl (pytest's own outcome for every conformance test, written at session end, so a
test that fails before recording its outcome is still counted; a failure also keeps the first
line of its exception, so analysis/stripe_mapping.py can tell a transient failure from a real
one) and
observations.jsonl (sanitized Stripe responses: HTTP status, error type/code, PaymentIntent
status, Idempotent-Replayed, request id; never a body or a key). docs/STRIPE_MAPPING.md's
"observed" column is filled from outcomes.jsonl by `python -m analysis.stripe_mapping`.
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

from experiments.manifest import base_manifest, now_iso, write_manifest

REPO = Path(__file__).resolve().parents[2]

_RESULTS: dict[str, str] = {}   # nodeid -> passed / failed / skipped / error
_ERRORS: dict[str, str] = {}    # nodeid -> first line of the failure (exception type and message)
_SESSION_DIR: list[Path] = []


def pytest_runtest_logreport(report):
    if report.when == "call" or report.outcome != "passed":
        failed_outside_call = report.when != "call" and report.outcome == "failed"
        _RESULTS[report.nodeid] = "error" if failed_outside_call else report.outcome
        crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
        if report.outcome == "failed" and crash is not None:
            _ERRORS[report.nodeid] = str(crash.message).splitlines()[0][:300] if crash.message else ""


def pytest_sessionfinish(session):
    if _SESSION_DIR:
        with (_SESSION_DIR[0] / "tests.jsonl").open("w", encoding="utf-8") as fh:
            fh.writelines(json.dumps({"test": k, "outcome": v, **({"error": _ERRORS[k]} if k in _ERRORS else {})},
                                     sort_keys=True) + "\n"
                          for k, v in sorted(_RESULTS.items()))


class Recorder:
    def __init__(self, out: Path):
        self.out = out
        self.session = out.name
        out.mkdir(parents=True, exist_ok=True)
        self.manifest = base_manifest(REPO, Path(__file__).with_name("test_stripe_vs_sim.py"), sys.argv)
        self.manifest.update({"kind": "stripe_conformance", "backend": "stripe-test-mode"})

    def _append(self, name, rows):
        with (self.out / name).open("a", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r, sort_keys=True) + "\n" for r in rows)

    def outcome(self, transition: str, sim, stripe, match: bool, **extra):
        self._append("outcomes.jsonl", [{"transition": transition, "sim": sim, "stripe": stripe,
                                         "match": match, **extra}])

    def observations(self, transition: str, adapter):
        self._append("observations.jsonl", [{"transition": transition, "run_id": adapter.run_id, **o.__dict__}
                                            for o in adapter.observations])

    def close(self):
        self.manifest["end"] = now_iso()
        from p4.adapters.stripe_test import STRIPE_VERSION
        self.manifest["stripe_version"] = STRIPE_VERSION
        write_manifest(self.out / "manifest.json", self.manifest)


@pytest.fixture(scope="session")
def record():
    root = Path(os.environ.get("P4_CONFORMANCE_OUT", REPO / "results" / "conformance"))
    out = root / time.strftime("stripe-%Y%m%dT%H%M%SZ", time.gmtime())
    rec = Recorder(out)
    _SESSION_DIR.append(out)
    yield rec
    rec.close()
