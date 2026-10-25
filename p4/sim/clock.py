"""Discrete-event clock owned by the sink and the harness (CODE_AUDIT A3).

Time is integer simulated units, not card-network time. It only moves forward.
"""
import heapq
import time
from collections.abc import Callable


class EventClock:
    def __init__(self) -> None:
        self._now = 0
        self._seq = 0
        self._queue: list[tuple[int, int, Callable[[], None]]] = []

    def now(self) -> int:
        return self._now

    def schedule(self, t: int, event: Callable[[], None]) -> None:
        """Run `event()` at time `t`. Ties run in insertion order."""
        if t < self._now:
            raise ValueError(f"cannot schedule at t={t} < now={self._now}")
        heapq.heappush(self._queue, (t, self._seq, event))
        self._seq += 1

    def run_until(self, t: int) -> None:
        """Fire every event with time <= t (including ones scheduled meanwhile); now := t."""
        if t < self._now:
            raise ValueError(f"cannot run back to t={t} from now={self._now}")
        while self._queue and self._queue[0][0] <= t:
            when, _, event = heapq.heappop(self._queue)
            self._now = when
            event()
        self._now = t

    def pending(self) -> int:
        return len(self._queue)

    def next_time(self) -> int | None:
        return self._queue[0][0] if self._queue else None


class WallClockDriver(EventClock):
    """An EventClock tied to wall time, for live runs (WP6 case study): construction is t = 0,
    and an event at time t fires no earlier than `unit_seconds * t` later. `run_until` sleeps
    until each event is due, so merchant captures land at their wall time while another thread
    (the agent) keeps calling. `now()` is still the logical time of the last event reached, never
    a fraction. If the caller falls behind, events fire late but in order; `max_lag` is the
    largest such lateness in seconds."""

    def __init__(self, unit_seconds: float = 1.0, *,
                 monotonic: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        if unit_seconds <= 0:
            raise ValueError("unit_seconds must be > 0")
        super().__init__()
        self.unit = unit_seconds
        self._monotonic, self._sleep = monotonic, sleep
        self._t0 = monotonic()
        self.max_lag = 0.0

    def _wait_for(self, t: int) -> None:
        wait = self._t0 + t * self.unit - self._monotonic()
        if wait > 0:
            self._sleep(wait)
        else:
            self.max_lag = max(self.max_lag, -wait)

    def run_until(self, t: int) -> None:
        if t < self._now:
            raise ValueError(f"cannot run back to t={t} from now={self._now}")
        while (nxt := self.next_time()) is not None and nxt <= t:
            self._wait_for(nxt)
            super().run_until(nxt)
        self._wait_for(t)
        super().run_until(t)
