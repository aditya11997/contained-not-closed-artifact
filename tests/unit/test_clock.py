import pytest

from p4.sim.clock import EventClock, WallClockDriver


def test_events_fire_in_time_then_insertion_order():
    clock = EventClock()
    fired = []
    clock.schedule(5, lambda: fired.append(("b", clock.now())))
    clock.schedule(3, lambda: fired.append(("a", clock.now())))
    clock.schedule(5, lambda: fired.append(("c", clock.now())))
    clock.run_until(10)
    assert fired == [("a", 3), ("b", 5), ("c", 5)]
    assert clock.now() == 10


def test_run_until_is_inclusive_and_stops_there():
    clock = EventClock()
    fired = []
    clock.schedule(4, lambda: fired.append(4))
    clock.schedule(6, lambda: fired.append(6))
    clock.run_until(4)
    assert fired == [4] and clock.now() == 4
    assert clock.pending() == 1 and clock.next_time() == 6


def test_events_scheduled_while_running_fire_if_due():
    clock = EventClock()
    fired = []
    clock.schedule(2, lambda: clock.schedule(3, lambda: fired.append(3)))
    clock.run_until(3)
    assert fired == [3]


def test_time_never_moves_backwards():
    clock = EventClock()
    clock.run_until(5)
    with pytest.raises(ValueError):
        clock.run_until(4)
    with pytest.raises(ValueError):
        clock.schedule(4, lambda: None)
    clock.schedule(5, lambda: None)  # "now" is allowed


class FakeTime:
    def __init__(self):
        self.t, self.sleeps = 0.0, []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def test_wall_clock_fires_each_event_at_its_wall_time():
    ft = FakeTime()
    clock = WallClockDriver(0.5, monotonic=ft.monotonic, sleep=ft.sleep)
    fired = []
    clock.schedule(3, lambda: fired.append((clock.now(), ft.t)))
    clock.schedule(1, lambda: fired.append((clock.now(), ft.t)))
    clock.run_until(4)
    assert fired == [(1, 0.5), (3, 1.5)] and clock.now() == 4 and ft.t == 2.0 and clock.max_lag == 0


def test_wall_clock_behind_schedule_fires_in_order_and_records_lag():
    ft = FakeTime()
    clock = WallClockDriver(1.0, monotonic=ft.monotonic, sleep=ft.sleep)
    fired = []
    clock.schedule(2, lambda: fired.append(2))
    clock.schedule(1, lambda: fired.append(1))
    ft.t = 3.5   # the caller was busy (e.g. a slow HTTP call)
    clock.run_until(2)
    assert fired == [1, 2] and ft.sleeps == [] and clock.max_lag == 2.5
    with pytest.raises(ValueError):
        clock.run_until(1)


def test_wall_clock_rejects_non_positive_unit():
    with pytest.raises(ValueError):
        WallClockDriver(0)
