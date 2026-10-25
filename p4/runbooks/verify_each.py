"""P4 verify-each: Lawler-Moore plan; a worker finishes each payment before taking the next.

After a cancel that times out, read status until a definitive answer or the nominal
deadline passes. A PENDING read means the cancel never landed: re-send it with the same key
if it can still finish before d_nominal, then verify again. Then the accounting sweep.
"""
from collections import deque

from ..sched import plan_lm
from ..sim.protocol import PENDING
from .base import CANCEL, STATUS, Action, Runbook


class VerifyEach(Runbook):
    name = "lm_verify"

    def __init__(self, c: float = 0.5, incident_id: str = "INC-0", planner=plan_lm,
                 late_cancel: bool = True):
        super().__init__(c, incident_id, late_cancel)
        self.planner = planner

    def start(self) -> None:
        self.queue = deque(self.planner(tuple(self.obs.values()), self.c, self.k))
        self.followups: deque[str] = deque()  # payments whose worker owes a next step

    def on_reply(self, action: Action, reply) -> None:
        uid = action.uid
        if uid in self.unknown or self.last_read.get(uid) == PENDING:
            self.followups.append(uid)

    def next_action(self, now: int) -> Action | None:
        while self.followups:
            uid = self.followups.popleft()
            if not self.is_open(uid):  # only after a restart replays stale follow-ups
                continue
            if uid in self.unknown and now < self.obs[uid].d_nominal:
                return Action(STATUS, uid)
            if uid not in self.unknown and self.can_finish(uid, now):  # read said PENDING
                return Action(CANCEL, uid)
        while self.queue:
            uid = self.queue.popleft()
            if self.fresh(uid) and self.can_finish(uid, now):
                return Action(CANCEL, uid)
        return self.sweep_action()
