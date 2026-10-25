"""P0-P3 ack-trust runbooks, parameterized by scheduler.

Send cancels in plan order (k workers take the next planned cancel as they free up, which
is list scheduling). A CANCELLED reply *or a timeout* is reported CLOSED_CANCELLED; TOO_LATE
is CLOSED_EXPOSURE; an unplanned payment is CLOSED_EXPOSURE after its nominal deadline.
This mirrors an ops dashboard that marks a "cancel sent" job as done. P0 has an empty plan.

After a restart, each journaled cancel with no recorded result is re-sent once with its same
key before any new cancel (outbox recovery), and the ACK-trust rule applies to that reply.
"""
from collections import deque

from ..contract import (
    BASIS_NOMINAL_DEADLINE,
    BASIS_TIMEOUT_TRUSTED,
    Disposition,
    DispositionEntry,
)
from .base import CANCEL, Action, Runbook


class AckTrust(Runbook):
    evidence_bounded = False

    def __init__(self, planner, name: str, c: float = 0.5, incident_id: str = "INC-0"):
        super().__init__(c, incident_id)
        self.planner = planner
        self.name = name

    def start(self) -> None:
        self.queue = deque(self.planner(tuple(self.obs.values()), self.c, self.k))
        self.outbox: deque[str] = deque()

    def recover_orphans(self, intents) -> None:
        self.outbox.extend(r["uid"] for r in intents if r["op"] == CANCEL)

    def next_action(self, now: int) -> Action | None:
        if self.outbox:
            return Action(CANCEL, self.outbox.popleft())
        while self.queue:
            uid = self.queue.popleft()
            if self.fresh(uid) and self.can_finish(uid, now):
                return Action(CANCEL, uid)
        return None

    def on_reply(self, action: Action, reply) -> None:
        if action.uid not in self.closed and reply.timeout:
            self.close(action.uid, Disposition.CLOSED_CANCELLED, BASIS_TIMEOUT_TRUSTED)

    def finalize_open(self, uid: str, now: int) -> DispositionEntry:
        if uid in self.attempted:  # a cancel error: nothing to trust
            return super().finalize_open(uid, now)
        return DispositionEntry(uid, Disposition.CLOSED_EXPOSURE, BASIS_NOMINAL_DEADLINE, now)


def no_plan(observations, c, k) -> tuple[str, ...]:
    return ()
