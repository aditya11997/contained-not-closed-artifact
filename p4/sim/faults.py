"""Transport fault layer between the remediator and the sink (fixes CODE_AUDIT D2).

A call costs the caller's worker time (`s_cancel` for a cancel, `q` for a status read) and
reaches the sink when that time has elapsed: the call is an event on the shared clock at
t_sent + latency, so every capture due in (t_sent, t_sent + latency] fires first (captures
are scheduled at sink construction, so they also win ties). At that moment one of these
happens:
- outage window: the call times out; a cancel still commits with prob `r`, a status read
  never reaches the sink;
- drop_req (prob f_req): the call times out and the sink is untouched;
- drop_resp (prob f_resp): the sink applies the call, and the caller gets a timeout (a real
  lost ACK);
- otherwise the sink's reply is delivered.

`timeout_factor` m (post hoc, exploratory; docs/POSTHOC.md PH3): a timed-out call's timeout
reaches the caller at t_sent + m * latency instead of t_sent + latency; the sink effect and the
fault are unchanged. m = 1 (the default, and every frozen result) is the path above.

Fairness (CLAUDE.md rule 2): a call's fault depends only on (seed, op, uid, attempt), where
`attempt` counts that op on that payment. Outage windows depend only on the seed. Every
policy for a seed therefore faces the same realization.
"""
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .clock import EventClock
from .protocol import Processor, Reply, StatusReply
from .streams import STREAM_FAULT, STREAM_OUTAGE, stable_hash

OPS = {"cancel": 0, "status": 1}
DEFAULT_PRESETS = Path(__file__).resolve().parents[2] / "configs" / "faults.toml"


@dataclass(frozen=True)
class FaultConfig:
    name: str = "none"
    f_req: float = 0.0
    f_resp: float = 0.0
    outage_rate: float = 0.0       # Poisson arrival rate lambda, per time unit
    outage_length: int = 0         # L, time units
    outage_commit_prob: float = 0.0  # r: prob a cancel commits during an outage

    def __post_init__(self):
        for name in ("f_req", "f_resp", "outage_commit_prob"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.outage_rate < 0 or self.outage_length < 0:
            raise ValueError("outage_rate and outage_length must be >= 0")


def load_fault_presets(path=DEFAULT_PRESETS) -> dict[str, FaultConfig]:
    with Path(path).open("rb") as fh:
        data = tomllib.load(fh)
    return {name: FaultConfig(name=name, **params) for name, params in data.items()}


@dataclass(frozen=True)
class FaultDraw:
    drop_req: bool
    drop_resp: bool
    outage_commit: bool


def fault_draw(seed: int, cfg: FaultConfig, op: str, uid: str, attempt: int) -> FaultDraw:
    """Pure function of (seed, op, uid, attempt). Always consumes the same three uniforms."""
    u = np.random.default_rng([seed, STREAM_FAULT, OPS[op], stable_hash(uid), attempt]).random(3)
    return FaultDraw(bool(u[0] < cfg.f_req), bool(u[1] < cfg.f_resp),
                     bool(u[2] < cfg.outage_commit_prob))


def outage_windows(seed: int, cfg: FaultConfig, until: int) -> list[tuple[int, int]]:
    """Half-open windows [start, start + L) with Poisson(lambda) starts in [0, until).

    Windows are generated sequentially, so a larger `until` only appends windows.
    """
    if cfg.outage_rate <= 0 or cfg.outage_length <= 0:
        return []
    rng = np.random.default_rng([seed, STREAM_OUTAGE])
    windows, t = [], 0.0
    while True:
        t += rng.exponential(1.0 / cfg.outage_rate)
        start = int(np.floor(t))
        if start >= until:
            return windows
        windows.append((start, start + cfg.outage_length))


@dataclass(frozen=True)
class CallRecord:
    t_sent: int
    t_arrive: int
    op: str
    uid: str
    attempt: int
    fault: str  # ok | drop_req | drop_resp | outage_commit | outage_lost
    reply: object = field(compare=False)


class FaultyTransport:
    """Implements the Processor protocol for the remediator, on top of a sink."""

    def __init__(self, processor: Processor, clock: EventClock, fault_cfg: FaultConfig, seed: int,
                 *, cancel_latency: Mapping[str, int], status_latency: int, timeout_factor: int = 1):
        if isinstance(timeout_factor, bool) or not isinstance(timeout_factor, int) or timeout_factor < 1:
            raise ValueError(f"timeout_factor must be a positive integer, got {timeout_factor!r}")
        self._sink = processor
        self._clock = clock
        self.cfg = fault_cfg
        self.seed = seed
        self._cancel_latency = dict(cancel_latency)
        self._status_latency = status_latency
        self.timeout_factor = timeout_factor
        self._attempts: dict[tuple[str, str], int] = {}
        self._windows: list[tuple[int, int]] = []
        self._windows_until = 0
        self.trace: list[CallRecord] = []

    # -- outages -------------------------------------------------------------------------
    def in_outage(self, t: int) -> bool:
        if t >= self._windows_until:
            self._windows_until = max(2 * self._windows_until, t + 1, 64)
            self._windows = outage_windows(self.seed, self.cfg, self._windows_until)
        return any(a <= t < b for a, b in self._windows)

    # -- asynchronous API (for k parallel workers) -------------------------------------
    def submit_cancel(self, actor: str, uid: str, idem_key: str,
                      on_reply: Callable[[Reply], None]) -> None:
        self._submit("cancel", uid, self._latency(uid), lambda: self._sink.cancel(actor, uid, idem_key),
                     Reply(timeout=True), on_reply)

    def submit_status(self, actor: str, uid: str, on_reply: Callable[[StatusReply], None]) -> None:
        self._submit("status", uid, self._status_latency, lambda: self._sink.status(actor, uid),
                     StatusReply(timeout=True), on_reply)

    # -- synchronous Processor protocol (one worker: blocks until the reply) -----------
    def cancel(self, actor: str, uid: str, idem_key: str) -> Reply:
        return self._wait(lambda cb: self.submit_cancel(actor, uid, idem_key, cb))

    def status(self, actor: str, uid: str) -> StatusReply:
        return self._wait(lambda cb: self.submit_status(actor, uid, cb))

    # -- internals -----------------------------------------------------------------------
    def _latency(self, uid: str) -> int:
        if uid not in self._cancel_latency:
            raise ValueError(f"no cancel latency for uid {uid!r}")
        return self._cancel_latency[uid]

    def _submit(self, op, uid, latency, call, timeout_reply, on_reply) -> None:
        attempt = self._attempts.get((op, uid), 0)
        self._attempts[(op, uid)] = attempt + 1
        draw = fault_draw(self.seed, self.cfg, op, uid, attempt)
        t_sent = self._clock.now()

        def arrive():
            t = self._clock.now()
            reply: object = timeout_reply
            if self.in_outage(t):
                if op == "cancel" and draw.outage_commit:
                    call()
                    fault = "outage_commit"
                else:
                    fault = "outage_lost"
            elif draw.drop_req:
                fault = "drop_req"
            elif draw.drop_resp:
                call()
                fault = "drop_resp"
            else:
                reply = call()
                fault = "ok"
            self.trace.append(CallRecord(t_sent, t, op, uid, attempt, fault, reply))
            if fault == "ok" or self.timeout_factor == 1:
                on_reply(reply)
            else:
                self._clock.schedule(t_sent + self.timeout_factor * latency, lambda: on_reply(reply))

        self._clock.schedule(t_sent + latency, arrive)

    def _wait(self, submit) -> object:
        box: list = []
        submit(box.append)
        while not box:
            target = self._clock.next_time()
            if target is None:
                raise RuntimeError("call was never delivered")
            self._clock.run_until(target)
        return box[0]
