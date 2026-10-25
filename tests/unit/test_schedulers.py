"""Schedulers are pure functions of Observations: deterministic, no mutation, only permitted
positive-gain payments, plans feasible under k-worker list scheduling."""
import copy

from hypothesis import given, settings
from hypothesis import strategies as st

from p4.sched import PLANNERS, Job, gain, list_schedule, schedule_feasible
from p4.sim.scenario import Observation, ScenarioConfig, make_scenario

obs_strategy = st.lists(
    st.builds(Observation,
              uid=st.just(""),  # replaced below with unique uids
              amount=st.integers(100, 2000), d_nominal=st.integers(1, 30),
              s_cancel=st.integers(1, 5), p_score=st.floats(0.05, 0.95),
              permitted=st.booleans()),
    max_size=12,
).map(lambda xs: tuple(Observation(f"P{i:03d}", o.amount, o.d_nominal, o.s_cancel, o.p_score,
                                   o.permitted) for i, o in enumerate(xs)))


@settings(max_examples=150, deadline=None)
@given(obs=obs_strategy, k=st.sampled_from([1, 2, 4]), c=st.sampled_from([0.5, 1.0]))
def test_plans_are_pure_permitted_and_feasible(obs, k, c):
    by = {o.uid: o for o in obs}
    for name, planner in PLANNERS.items():
        before = copy.deepcopy(obs)
        plan = planner(obs, c, k)
        assert obs == before
        assert planner(obs, c, k) == plan, name
        assert len(set(plan)) == len(plan)
        assert all(by[u].permitted and gain(by[u], c) > 0 for u in plan), name
        jobs = [Job(u, gain(by[u], c), by[u].s_cancel, by[u].d_nominal) for u in plan]
        assert schedule_feasible(jobs, [0] * k), (name, plan)


def test_list_schedule_uses_earliest_free_worker_lowest_index_on_ties():
    jobs = [Job("A", 1, 3, 99), Job("B", 1, 1, 99), Job("C", 1, 1, 99), Job("D", 1, 1, 99)]
    got = [(j.uid, w, s, f) for j, w, s, f in list_schedule(jobs, [0, 0])]
    assert got == [("A", 0, 0, 3), ("B", 1, 0, 1), ("C", 1, 1, 2), ("D", 1, 2, 3)]


def test_planners_see_only_observation_fields():
    obs = make_scenario(100000, ScenarioConfig(n=16)).observations()
    assert all(type(o) is Observation for o in obs)
    for planner in PLANNERS.values():
        planner(obs, 0.5, 1)
