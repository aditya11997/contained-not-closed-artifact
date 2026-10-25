"""Shared test helpers (tests/helpers) are importable from every test directory.

Live Stripe tests (`@pytest.mark.stripe`) are skipped unless STRIPE_TEST_KEY is set and the run
opts in with `--run-stripe` or P4_RUN_STRIPE=1 (tests/helpers/stripe_gate.py)."""
import os
import sys
from pathlib import Path

import pytest

HELPERS = Path(__file__).resolve().parent / "helpers"

if str(HELPERS) not in sys.path:
    sys.path.insert(0, str(HELPERS))

from stripe_gate import FLAG, stripe_skip_reason


def pytest_addoption(parser):
    parser.addoption(FLAG, action="store_true", default=False,
                     help="run live Stripe test-mode tests (also needs STRIPE_TEST_KEY)")


def pytest_collection_modifyitems(config, items):
    reason = stripe_skip_reason(config.getoption(FLAG), os.environ)
    if reason is None:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if "stripe" in item.keywords:
            item.add_marker(skip)
