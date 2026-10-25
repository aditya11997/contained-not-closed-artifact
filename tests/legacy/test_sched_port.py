"""p4.sched EDF and greedy reproduce the legacy planners (k = 1) on dev seeds."""
import pytest
from p4lab.model import make_scenario, policy_view
from p4lab.policies import plan

from p4.sched import plan_edf, plan_greedy
from p4.sim.scenario import Observation


def to_new(o):
    return Observation(o.uid, o.amount, o.capture_at, o.remedy_minutes, o.risk_estimate, o.permitted)


@pytest.mark.parametrize("urgency", ["tight", "normal", "loose"])
@pytest.mark.parametrize("c", [0.5, 1.0])
def test_edf_and_greedy_match_legacy(urgency, c):
    for seed in range(100000, 100300):
        legacy = policy_view(make_scenario(seed, 8, urgency))
        new = tuple(to_new(o) for o in legacy)
        assert plan_edf(new, c) == plan(legacy, "edf", c), seed
        assert plan_greedy(new, c) == plan(legacy, "value_greedy", c), seed
