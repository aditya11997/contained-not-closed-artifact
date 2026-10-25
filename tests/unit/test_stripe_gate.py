"""Live Stripe tests need an explicit opt-in on top of the key (tests/helpers/stripe_gate.py)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from stripe_gate import KEY_ENV, OPT_IN_ENV, stripe_skip_reason

REPO = Path(__file__).resolve().parents[2]
FAKE_KEY = "sk_" + "test_not_a_real_key"


@pytest.mark.parametrize("flag, env, runs", [
    (False, {}, False),
    (False, {KEY_ENV: FAKE_KEY}, False),                     # a key in the shell is not an opt-in
    (False, {KEY_ENV: FAKE_KEY, OPT_IN_ENV: "0"}, False),
    (False, {KEY_ENV: FAKE_KEY, OPT_IN_ENV: "yes"}, False),
    (True, {}, False),                                       # opt-in without a key
    (False, {OPT_IN_ENV: "1"}, False),
    (True, {KEY_ENV: FAKE_KEY}, True),
    (False, {KEY_ENV: FAKE_KEY, OPT_IN_ENV: "1"}, True),
])
def test_stripe_skip_reason(flag, env, runs):
    assert (stripe_skip_reason(flag, env) is None) == runs


def test_plain_pytest_with_key_skips_every_live_test(tmp_path):
    """`pytest -q` with STRIPE_TEST_KEY set but no opt-in collects the live tests and skips all
    of them, so it never reaches the network."""
    env = {k: v for k, v in os.environ.items() if k != OPT_IN_ENV}
    env.update({KEY_ENV: FAKE_KEY, "P4_CONFORMANCE_OUT": str(tmp_path / "out")})
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
                           "tests/conformance"], cwd=REPO, env=env, capture_output=True, text=True, check=False,
                          timeout=300)
    tail = proc.stdout.strip().splitlines()[-1]
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "skipped" in tail and "passed" not in tail and "failed" not in tail and "error" not in tail, tail
    assert f"need --run-stripe or {OPT_IN_ENV}=1" in proc.stdout
    assert not (tmp_path / "out").exists()
