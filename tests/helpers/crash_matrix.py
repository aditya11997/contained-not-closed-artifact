"""Crash matrix test helpers. The matrix itself (cells, crash hook, scenario, faults, k) lives in
experiments/crash_matrix.py, which also runs it on holdout seeds for H5; this module keeps the
dev-seed signatures the tests use and adds a test-only fixture.

`lm_ack_trust_orphans` is a test fixture, not a policy: P3 with the superseded first WP3
restart rule (an unresolved journaled cancel is trusted as a timeout instead of re-sent). It
reports as lm_ack and is kept so the effect of outbox recovery stays measurable.
"""
from experiments import crash_matrix as cm
from experiments.crash_matrix import (  # noqa: F401  (re-exported for the tests)
    ASSURED,
    CELLS,
    FAULTS,
    KS,
    LATE_SCENARIO,
    OUTAGE,
    POLICIES,
    SCENARIO,
    SWEEP_TARGETS,
    CrashAt,
    CrashRun,
    applies,
    cell_label,
    grade,
    scenario_for,
)
from p4.runbooks import AckTrust, Runbook
from p4.sched import plan_lm
from p4.sim.scenario import partition_seeds


class TrustOrphansAckTrust(AckTrust):
    """Superseded P3 restart rule (test fixture only)."""
    recover_orphans = Runbook.recover_orphans


# fixture name -> (policy it reports as, factory)
FIXTURES = {"lm_ack_trust_orphans": ("lm_ack", lambda: TrustOrphansAckTrust(plan_lm, "lm_ack"))}


def report_policy(policy: str) -> str:
    return FIXTURES[policy][0] if policy in FIXTURES else policy


def seeds(n: int) -> list[int]:
    return list(partition_seeds("dev"))[:n]


def run_cell(policy, point, target, seed, fault, k, journal_path, sync=False,
             baseline=False, restart_delay=0) -> CrashRun:
    graded_as, factory = FIXTURES.get(policy, (policy, None))
    return cm.run_cell(policy, point, target, seed, fault, k, journal_path, sync, baseline, restart_delay,
                       graded_as=graded_as, runbook_factory=factory)


def run_matrix(tmp_dir, n_seeds: int, policies=POLICIES, cells=CELLS, sync=False, baseline=False,
               restart_delay=0):
    """Yield a CrashRun for every applicable (policy, cell, seed, fault, k) on the first
    `n_seeds` dev seeds."""
    return cm.run_matrix(tmp_dir, seeds(n_seeds), policies, cells, sync, baseline, restart_delay,
                         factories=FIXTURES)
