"""P6 adaptive (candidate): P5 plus per-event Lawler-Moore re-planning. Designed on dev seeds
only (G1 decision, 2026-10-20; docs/CHANGELOG_RESEARCH.md).

At every free-worker event, re-solve Lawler-Moore over the payments not yet attempted
(permitted, gain > 0), using the true times at which each worker becomes free, and send the
first job of that plan. P5 instead follows the plan it made at t = 0. Everything else is P5:
once no fresh cancel fits, replay each timed-out cancel with the same key, earliest d_nominal
first, while now < d_nominal; then the accounting sweep.

The pre-G1 P6 also ranked timed-out cancels against fresh ones (weight (1 - rho_hat) * gain)
and replayed them first when they topped the plan. On dev seeds that replay-first rule was
what made it worse than P5: replays sent right after a timeout mostly hit the same outage
(G1 diagnosis, analysis/g1/p6_vs_p5.py). It and rho_hat were removed.
"""
from ..sched import Job, gain, lawler_moore
from .base import CANCEL, REPLAY, Action, Runbook


class Adaptive(Runbook):
    name = "adaptive"

    def start(self) -> None:
        self.todo = {o.uid for o in self.obs.values() if o.permitted and gain(o, self.c) > 0}

    def next_action(self, now: int) -> Action | None:
        free = self.k - len(self.inflight)
        worker_free_at = [now] * free + sorted(max(t, now) for t in self.inflight.values())
        jobs = [Job(uid, gain(self.obs[uid], self.c), self.obs[uid].s_cancel, self.obs[uid].d_nominal)
                for uid in self.order if uid in self.todo and self.fresh(uid) and uid not in self.inflight]
        plan = lawler_moore(jobs, worker_free_at=worker_free_at) if jobs else ()
        if plan:
            return Action(CANCEL, plan[0])
        replayable = [u for u in self.unknown
                      if u not in self.inflight and now < self.obs[u].d_nominal]
        if replayable:
            return Action(REPLAY, min(replayable, key=lambda u: (self.obs[u].d_nominal, u)))
        return self.sweep_action()
