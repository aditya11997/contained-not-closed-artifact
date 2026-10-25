"""Run one policy on one scenario under one fault realization (the per-(seed, policy) core
the WP4 runner builds on).

The harness is evaluator-side: it holds the hidden Scenario and builds the sink, so it may
import p4.sim.processor. The runbook gets only `scenario.observations()`, the transport,
the clock, a journal and k. After the runbook returns, the harness advances the clock to
the true horizon so every capture is in the log.

Crash runs (WP3): with a `crash_hook`, a SimulatedCrash kills the runbook instance. The clock
then runs on for `restart_delay` time units with no remediator (calls already sent still land,
captures still fire), and the harness starts a fresh instance on the same sink, transport,
clock and journal (reopened from `journal_path` when durable) and lets it finish.
`runbook_factory` (tests only) builds every instance instead of `make_runbook(policy)`.
`sink_factory(scenario, clock)` builds the sink instead of `SimProcessor(scenario, clock)`; the
WP6 agent gateway uses it so the revoked agent's post-stop calls land in the episode's log.
`timeout_factor` (post hoc, exploratory; docs/POSTHOC.md PH3) delays every timeout reply to
t_sent + m * latency; 1 is the frozen behaviour.
`clock` replaces the episode's fresh EventClock; WP6 passes the gateway's stop clock (an
EventClock or a WallClockDriver, still at t = 0), which also keeps the calls the agent made
between the stop and the episode.

`export_run` writes the files the independent checker reads (p4/schema.py); `check_runs`
runs the checker as a separate process over a batch of exported runs.
"""
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from . import schema
from .contract import DispositionReport
from .journal import Journal
from .runbooks import SimulatedCrash, effective_k, make_runbook
from .sim.clock import EventClock
from .sim.faults import (
    OPS,
    CallRecord,
    FaultConfig,
    FaultyTransport,
    fault_draw,
    outage_windows,
)
from .sim.processor import SimProcessor
from .sim.scenario import Scenario

# Fault draws hashed into the fairness fingerprint: attempts 0..N-1 per (op, uid).
FINGERPRINT_ATTEMPTS = 8


@dataclass(frozen=True)
class Episode:
    report: DispositionReport
    log: tuple
    trace: tuple[CallRecord, ...]
    journal: tuple
    inputs_sha256: str
    end_of_run: int
    restarts: int = 0


def inputs_fingerprint(observations, k, q: int, seed: int, fault_cfg: FaultConfig,
                       horizon: int, timeout_factor: int = 1) -> str:
    """SHA-256 of everything a policy is handed or faces: observations, k, status-read cost,
    fault config, outage windows up to the horizon and the per-(op, uid, attempt) fault
    draws, and the timeout factor when it is not 1 (post hoc; 1 keeps the frozen hash)."""
    uids = [o.uid for o in observations]
    body = {
        "observations": [asdict(o) for o in observations],
        "k": k if isinstance(k, int) else str(k),
        "q": q,
        "seed": seed,
        "fault_cfg": asdict(fault_cfg),
        "outage_windows": outage_windows(seed, fault_cfg, horizon),
        "draws": [[op, uid, a, *asdict(fault_draw(seed, fault_cfg, op, uid, a)).values()]
                  for op in OPS for uid in uids for a in range(FINGERPRINT_ATTEMPTS)],
    }
    if timeout_factor != 1:
        body["timeout_factor"] = timeout_factor
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def run_episode(scenario: Scenario, policy: str, fault_cfg: FaultConfig, seed: int, k, *,
                c: float = 0.5, incident_id: str = "INC-0", late_cancel: bool = True,
                runbook=None, crash_hook=None, journal_path=None, journal_sync: bool = True,
                max_restarts: int = 3, restart_delay: int = 0, runbook_factory=None,
                sink_factory=None, clock: EventClock | None = None, timeout_factor: int = 1) -> Episode:
    if restart_delay < 0:
        raise ValueError("restart_delay must be >= 0")
    observations = scenario.observations()
    q = scenario.config.q if scenario.config is not None else 1
    if clock is None:
        clock = EventClock()
    elif clock.now() != 0:
        raise ValueError("run_episode needs a clock at t=0 (revocation time)")
    sink = SimProcessor(scenario, clock) if sink_factory is None else sink_factory(scenario, clock)
    transport = FaultyTransport(sink, clock, fault_cfg, seed,
                                cancel_latency={o.uid: o.s_cancel for o in observations},
                                status_latency=q, timeout_factor=timeout_factor)
    journal = Journal(journal_path, sync=journal_sync)

    def new_runbook():
        if runbook_factory is not None:
            return runbook_factory()
        return make_runbook(policy, c=c, incident_id=incident_id, late_cancel=late_cancel)

    runbook = runbook or new_runbook()
    effective_k(k, len(observations))  # validate before running
    runbook_inputs = tuple(observations)
    restarts = 0
    while True:
        runbook.crash_hook = crash_hook
        try:
            report = runbook.run(runbook_inputs, transport, clock, journal, k)
            break
        except SimulatedCrash:
            restarts += 1
            if restarts > max_restarts:
                raise
            clock.run_until(clock.now() + restart_delay)  # downtime: no remediator
            if journal_path is not None:
                journal = Journal(journal_path, sync=journal_sync)  # what survived on disk
            runbook = new_runbook()
    end_of_run = clock.now()
    clock.run_until(max(end_of_run, scenario.horizon))
    # Fingerprint what the runbook and its transport actually held, not this function's args.
    fingerprint = inputs_fingerprint(runbook_inputs, k, q, transport.seed, transport.cfg,
                                     scenario.horizon, transport.timeout_factor)
    return Episode(report=report, log=sink.log, trace=tuple(transport.trace),
                   journal=journal.entries, inputs_sha256=fingerprint, end_of_run=end_of_run,
                   restarts=restarts)


def _jsonl(rows) -> str:
    return "".join(json.dumps(dict(r), sort_keys=True, separators=(",", ":")) + "\n" for r in rows)


def truth_dict(scenario: Scenario, c: float, log=None) -> dict:
    """truth.json: the hidden scenario, c and (if `log` is given) the log's length and head
    hash, recorded outside the log so a re-chained log cannot pass."""
    truth = {
        "schema": schema.TRUTH_SCHEMA,
        "seed": scenario.seed,
        "c": c,
        "horizon": scenario.horizon,
        "payments": [asdict(p) for p in scenario.payments],
    }
    if log is not None:
        truth["log_anchor"] = {"length": len(log),
                               "head_hash": log[-1]["hash"] if log else schema.GENESIS_HASH}
    return truth


def export_run(ep: Episode, scenario: Scenario, c: float, out_dir) -> dict[str, Path]:
    """Write events.jsonl, report.json, truth.json and journal.jsonl into `out_dir`."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {name: out / f for name, f in (("log", schema.EVENTS_FILE), ("report", schema.REPORT_FILE),
                                           ("truth", schema.TRUTH_FILE), ("journal", schema.JOURNAL_FILE))}
    paths["log"].write_text(_jsonl(ep.log), encoding="utf-8")
    report = {"schema": schema.REPORT_SCHEMA, **ep.report.to_dict()}
    paths["report"].write_text(json.dumps(report, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    paths["truth"].write_text(json.dumps(truth_dict(scenario, c, ep.log), sort_keys=True, indent=1) + "\n",
                              encoding="utf-8")
    paths["journal"].write_text(_jsonl(ep.journal), encoding="utf-8")
    return paths


def check_runs(runs, policies, out_path) -> list[dict]:
    """Run the independent checker in a separate process over exported runs.

    `runs` is a list of `export_run` results and `policies` the policy of each (required);
    verdicts come back in the same order.
    """
    if len(policies) != len(runs):
        raise ValueError("check_runs needs one policy per run")
    out_path = Path(out_path)
    batch = out_path.with_name(out_path.name + ".batch.jsonl")
    rows = [{"id": i, "log": str(r["log"]), "report": str(r["report"]), "truth": str(r["truth"]),
             "policy": policies[i]} for i, r in enumerate(runs)]
    batch.write_text(_jsonl(rows), encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "checker.check", "--batch", str(batch), "--out", str(out_path)],
                          cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                          check=False)
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"checker failed ({proc.returncode}): {proc.stderr.strip()}")
    verdicts = [json.loads(x) for x in out_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if [v["id"] for v in verdicts] != list(range(len(runs))):
        raise RuntimeError("checker returned verdicts out of order")
    return verdicts
