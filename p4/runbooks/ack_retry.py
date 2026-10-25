"""P3b at-least-once ack-trust (POST HOC, EXPLORATORY; docs/POSTHOC.md PH2). Not a
pre-registered policy and not in any frozen result.

P3 plus in-place retries: a timed-out cancel is re-sent with the same key (a replay) before the
next fresh cancel, at most MAX_RETRIES times and only while it can still finish before
d_nominal. Any definitive reply is trusted; when no retry is allowed, the last timeout is
trusted as CLOSED_CANCELLED, as P3 does. No status reads, no sweep, no late cancel.
"""
from ..contract import BASIS_TIMEOUT_TRUSTED, Disposition
from ..sched import plan_lm
from .ack_trust import AckTrust
from .base import CANCEL, REPLAY, Action

MAX_RETRIES = 3


class AckRetry(AckTrust):
    def __init__(self, c: float = 0.5, incident_id: str = "INC-0"):
        super().__init__(plan_lm, "lm_ack_retry", c, incident_id)

    def start(self) -> None:
        super().start()
        self.sends: dict[str, int] = {}
        self.retry: dict[str, None] = {}   # ordered set of payments owed a retry

    def recover_orphans(self, intents) -> None:
        # Re-send each orphaned cancel once, even if several crashes left several intents for
        # it; the outbox re-send replaces any retry it was owed.
        for uid in dict.fromkeys(r["uid"] for r in intents if r["op"] != "status"):
            self.outbox.append(uid)
            self.retry.pop(uid, None)

    def next_action(self, now: int) -> Action | None:
        while self.outbox:
            uid = self.outbox.popleft()
            if self.is_open(uid):
                return Action(CANCEL, uid)
        while self.retry:
            uid = next(iter(self.retry))
            del self.retry[uid]
            if self.is_open(uid):
                return Action(REPLAY, uid)
        return super().next_action(now)

    def on_reply(self, action: Action, reply) -> None:
        uid = action.uid
        self.sends[uid] = self.sends.get(uid, 0) + 1
        if uid in self.closed or not reply.timeout:
            return
        now = self.clock.now() if self._replay_t is None else self._replay_t
        if self.sends[uid] <= MAX_RETRIES and self.can_finish(uid, now):
            self.retry[uid] = None
        else:
            self.close(uid, Disposition.CLOSED_CANCELLED, BASIS_TIMEOUT_TRUSTED)
