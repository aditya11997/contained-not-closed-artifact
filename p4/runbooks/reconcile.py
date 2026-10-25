"""P5 reconcile (saga-style compensate-then-reconcile): fire every planned cancel; then
replay each timed-out cancel with the same key until a definitive reply or its nominal
deadline passes; then the accounting sweep."""
from collections import deque

from ..sched import plan_lm
from .base import CANCEL, REPLAY, Action, Runbook


class Reconcile(Runbook):
    name = "lm_reconcile"

    def __init__(self, c: float = 0.5, incident_id: str = "INC-0", planner=plan_lm,
                 late_cancel: bool = True):
        super().__init__(c, incident_id, late_cancel)
        self.planner = planner

    def start(self) -> None:
        self.queue = deque(self.planner(tuple(self.obs.values()), self.c, self.k))

    def next_action(self, now: int) -> Action | None:
        while self.queue:
            uid = self.queue.popleft()
            if self.fresh(uid) and self.can_finish(uid, now):
                return Action(CANCEL, uid)
        replayable = [u for u in self.unknown
                      if u not in self.inflight and now < self.obs[u].d_nominal]
        if replayable:
            return Action(REPLAY, min(replayable, key=lambda u: (self.obs[u].d_nominal, u)))
        return self.sweep_action()
