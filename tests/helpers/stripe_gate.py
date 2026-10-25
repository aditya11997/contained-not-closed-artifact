"""When live Stripe tests (`@pytest.mark.stripe`) may run: only with STRIPE_TEST_KEY set AND an
explicit opt-in (`pytest --run-stripe` or P4_RUN_STRIPE=1). A key left in the shell must never
turn a plain `pytest -q` into live API calls."""
from collections.abc import Mapping

FLAG = "--run-stripe"
OPT_IN_ENV = "P4_RUN_STRIPE"
KEY_ENV = "STRIPE_TEST_KEY"


def stripe_skip_reason(run_flag: bool, environ: Mapping[str, str]) -> str | None:
    """None if live Stripe tests may run, else why they are skipped."""
    if not (run_flag or environ.get(OPT_IN_ENV) == "1"):
        return f"live Stripe tests need {FLAG} or {OPT_IN_ENV}=1"
    if not environ.get(KEY_ENV):
        return f"{KEY_ENV} not set (Stripe test mode)"
    return None
