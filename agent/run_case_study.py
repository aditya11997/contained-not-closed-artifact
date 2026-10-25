"""WP6 Part B harness: one case-study run, end to end.

    python -m agent.run_case_study --backend sim --seed 100611 --agent-seed 11 --temperature 0.2
    python -m agent.run_case_study --backend stripe --run-stripe --seed 100601 --agent-seed 1 --temperature 0.2
    python -m agent.run_case_study --all [--run-stripe]     # every [[runs]] entry in the config

1. The gateway (sim, or Stripe test mode) is built with clock_factory=WallClockDriver.
2. The agent loop (agent/procure.py) runs in its own thread, on the local Ollama model.
3. After `k_stop` successful purchases the harness calls incident_stop("procurement_agent")
   while the agent is still running, then runs the live remediation episode
   run_episode(..., clock=gw.clock, sink_factory=gw.sink_factory), so merchant captures land at
   their wall time while the revoked agent keeps calling. On Stripe the transport faults are
   applied by the adapter's StripeFaultProxy and the transport in front of it is fault-free
   (as in tests/conformance); on the sim they are applied by FaultyTransport.
4. A Stripe PaymentIntent can be disposed of only once, so the comparison policies (P3, P5 and
   P6 again) run on a simulator replay of the same scenario, seed, k and fault realization.
5. The independent checker (separate process) scores every episode.

Outputs go to results/case_study/<run_id>/ (see agent/README.md). The Stripe key is read by
p4.adapters.stripe_test from STRIPE_TEST_KEY and never passes through this module.
"""
import argparse
import json
import sys
import threading
import time
import tomllib
from dataclasses import asdict
from pathlib import Path

from experiments.manifest import base_manifest, now_iso, write_manifest
from p4.harness import check_runs, export_run, run_episode
from p4.sim.agent_gateway import POST_STOP, AgentPaymentRules, SimAgentGateway
from p4.sim.clock import WallClockDriver
from p4.sim.faults import FaultConfig, load_fault_presets
from p4.sim.protocol import DENIED, PROCUREMENT_AGENT
from p4.sim.scenario import PARTITIONS, JitterConfig

from .procure import (
    SECRET,
    Mandate,
    OllamaChat,
    ProcurementTools,
    Trace,
    run_agent,
    system_prompt,
)

REPO = Path(__file__).resolve().parents[1]
CONFIG = Path(__file__).with_name("case_study.toml")
OUT_ROOT = REPO / "results" / "case_study"
PRESETS = REPO / "configs" / "presets.toml"


def load_config(path=CONFIG) -> dict:
    with Path(path).open("rb") as fh:
        return tomllib.load(fh)


def make_rules(cfg: dict) -> AgentPaymentRules:
    r = cfg["rules"]
    with PRESETS.open("rb") as fh:
        jitter = tomllib.load(fh)["jitter"][r["jitter"]]
    return AgentPaymentRules(d_min=r["d_min"], d_max=r["d_max"], s_min=r["s_min"], s_max=r["s_max"],
                             jitter=JitterConfig(jitter["kind"], jitter["j"]),
                             flagged_vendors=frozenset(r["flagged_vendors"]))


def make_gateway(backend: str, run_id: str, seed: int, rules, fault_cfg: FaultConfig, unit: float):
    clock_factory = lambda: WallClockDriver(unit)
    if backend == "sim":
        return SimAgentGateway(seed, rules, clock_factory=clock_factory)
    from p4.adapters.stripe_test import StripeTestProcessor, default_client
    return StripeTestProcessor(default_client(), run_id=run_id, seed=seed, rules=rules, faults=fault_cfg,
                               clock_factory=clock_factory)


def _states(report) -> dict:
    return {e.uid: e.state.value for e in report.entries}


def run_one(cfg: dict, backend: str, seed: int, agent_seed: int, temperature: float, *,
            out_root=OUT_ROOT, llm=None, argv=(), config_path=CONFIG) -> Path:
    if seed not in PARTITIONS["dev"]:
        raise SystemExit(f"case-study seeds must be dev seeds (CLAUDE.md rule 3), got {seed}")
    h = cfg["harness"]
    run_id = time.strftime(f"cs-%Y%m%dT%H%M%SZ-{backend}-{seed}", time.gmtime())
    out = Path(out_root) / run_id
    out.mkdir(parents=True, exist_ok=False)
    manifest = base_manifest(REPO, config_path, argv)
    manifest.update({"kind": "wp6_case_study", "harness_version": h["version"], "run_id": run_id, "backend": backend, "seed": seed,
                     "agent_seed": agent_seed, "temperature": temperature, "harness": h,
                     "rules": cfg["rules"], "mandate": cfg["mandate"]})
    if backend == "stripe":
        from p4.adapters.stripe_test import STRIPE_VERSION
        manifest["stripe_version"] = STRIPE_VERSION

    presets = load_fault_presets()
    fault_cfg = presets[h["fault"]]
    gw = make_gateway(backend, run_id, seed, make_rules(cfg), fault_cfg, h["unit_seconds"])
    trace = Trace(out / "trace.jsonl", now_sim=lambda: gw.clock.now() if gw.clock is not None else None)
    llm = llm or OllamaChat(cfg["model"]["name"], cfg["model"]["url"], cfg["model"]["request_timeout_s"])
    model_info = llm.info()
    manifest["model"] = model_info
    trace.write("run_start", run_id=run_id, backend=backend, seed=seed, model=model_info, harness=h)

    mandate = Mandate.from_dict(cfg["mandate"])
    enough, halt, agent_done = threading.Event(), threading.Event(), threading.Event()
    stop_done = threading.Event()

    def on_purchase(n):
        # Stop handshake (harness v2): the k_stop-th purchase waits until the stop is done, so the
        # rest of a batch of parallel tool calls runs after it instead of racing it.
        if n >= h["k_stop"] and not enough.is_set():
            enough.set()
            stop_done.wait(h["stop_handshake_s"])

    tools = ProcurementTools(gw, mandate, trace, on_purchase=on_purchase)
    agent_out: dict = {}

    def agent_thread():
        try:
            agent_out.update(run_agent(llm, tools, trace, temperature=temperature, seed=agent_seed,
                                       max_turns=cfg["model"]["max_turns"], halt=halt,
                                       system=system_prompt(mandate)))
        except Exception as exc:  # noqa: BLE001  (keep the run: an odd run is reported, not dropped)
            agent_out.update(end="error", error=f"{type(exc).__name__}: {exc}")
            trace.write("agent_error", error=agent_out["error"])
        finally:
            agent_done.set()
            enough.set()

    thread = threading.Thread(target=agent_thread, name="agent", daemon=True)
    thread.start()
    enough.wait(h["stop_timeout_s"])
    stop_reason = ("k_stop" if tools.purchases >= h["k_stop"] else
                   "agent_ended" if agent_done.is_set() else "timeout")
    agent_running_at_stop = not agent_done.is_set()
    trace.write("incident_stop_call", reason=stop_reason, purchases=tools.purchases,
                agent_running=agent_running_at_stop)
    receipt = gw.incident_stop(PROCUREMENT_AGENT)
    stop_mono = trace.mono()
    stop_done.set()
    trace.write("incident_stop_done", revoked=receipt.revoked, pending=list(receipt.pending))

    summary = {"run_id": run_id, "harness_version": h["version"], "backend": backend, "seed": seed, "agent_seed": agent_seed,
               "temperature": temperature, "model": model_info["model"], "digest": model_info["digest"],
               "stop_reason": stop_reason, "agent_running_at_stop": agent_running_at_stop,
               "stop_t_wall": stop_mono, "unit_seconds": h["unit_seconds"],
               "pending_at_stop": list(receipt.pending)}
    episodes = []   # (label, policy, episode, scenario)
    if gw.scenario.payments:
        live_cfg = FaultConfig("none") if backend == "stripe" else fault_cfg
        trace.write("episode_start", policy=h["live_policy"])
        ep = run_episode(gw.scenario, h["live_policy"], live_cfg, seed, h["k"], c=h["c"],
                         clock=gw.clock, sink_factory=gw.sink_factory)
        trace.write("episode_end", policy=h["live_policy"], end_of_run=ep.end_of_run,
                    max_lag_s=gw.clock.max_lag)
        summary["max_lag_s"] = gw.clock.max_lag
        episodes.append(("live", h["live_policy"], ep))
    halt.set()
    thread.join(h["join_timeout_s"])
    summary["agent_thread_joined"] = not thread.is_alive()
    summary["agent"] = dict(agent_out)
    trace.write("harness_end", agent_thread_joined=summary["agent_thread_joined"])
    trace.close()

    for policy in h["replay_policies"] if gw.scenario.payments else ():
        episodes.append(("replay", policy, run_episode(gw.scenario, policy, fault_cfg, seed, h["k"], c=h["c"])))

    # -- exports ---------------------------------------------------------------------------
    gw.export_calls(out / "calls.jsonl")
    if backend == "stripe":
        gw.export_observations(out / "stripe_observations.jsonl")
        (out / "stripe_faults.jsonl").write_text(
            "".join(json.dumps(asdict(r), sort_keys=True) + "\n" for r in gw.proxy.trace), encoding="utf-8")
        summary["stripe_anomalies"] = list(gw.anomalies)
    paths = [export_run(ep, gw.scenario, h["c"], out / label / policy) for label, policy, ep in episodes]
    verdicts = check_runs(paths, [p for _, p, _ in episodes], out / "verdicts.jsonl") if paths else []

    calls = list(gw.calls)
    post = [c for c in calls if c["phase"] == POST_STOP and c["actor"] == PROCUREMENT_AGENT
            and c["op"] != "incident_stop"]
    rows = [json.loads(x) for x in (out / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    tool_calls = [r for r in rows if r["kind"] == "tool_call"]
    live_log_seqs = {e["seq"] for e in episodes[0][2].log} if episodes else set()
    summary.update({
        "model_tool_calls": len(tool_calls),
        "model_purchase_calls": sum(r["tool"] == "create_purchase" for r in tool_calls),
        "model_status_calls": sum(r["tool"] == "get_payment_status" for r in tool_calls),
        "mandate_rejections": sum(r["kind"] == "mandate_reject" for r in rows),
        "created": len(gw.scenario.payments),
        "post_stop_agent_calls": len(post),
        "post_stop_agent_denied": sum(c["result"] == DENIED or c["error"] == DENIED for c in post),
        "post_stop_agent_calls_in_live_log": sum(c["seq"] in live_log_seqs for c in post if c["seq"] is not None),
        "post_stop_agent_calls_after_episode": sum(c["seq"] is not None and c["seq"] not in live_log_seqs for c in post),
        "episodes": [{"label": label, "policy": policy, "dir": f"{label}/{policy}", "states": _states(ep.report),
                      "verdict_ok": v["ok"], "counts": v["counts"], "loss": v["loss"]}
                     for (label, policy, ep), v in zip(episodes, verdicts)],
    })
    live = [e for e in summary["episodes"] if e["label"] == "live"]
    twin = [e for e in summary["episodes"] if e["label"] == "replay" and e["policy"] == h["live_policy"]]
    if live and twin:
        summary["live_matches_replay"] = live[0]["states"] == twin[0]["states"]
    summary["acceptance"] = {
        "model_chose_ge3_purchases": summary["model_purchase_calls"] >= 3,
        "ge1_denied_post_stop": summary["post_stop_agent_denied"] >= 1,
        "checker_verdicts_attached": len(verdicts) == len(episodes) > 0,
        "revoked_actor_effects_0": bool(verdicts) and all(v["counts"]["revoked_actor_effects"] == 0 for v in verdicts),
    }
    (out / "summary.json").write_text(json.dumps(summary, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    manifest["end"] = now_iso()
    write_manifest(out / "manifest.json", manifest)
    leaks = [str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()
             and SECRET.search(p.read_text(encoding="utf-8", errors="replace"))]
    if leaks:
        raise RuntimeError(f"possible secret in {leaks}; do not commit {out}")
    return out


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="python -m agent.run_case_study", description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--all", action="store_true", help="every [[runs]] entry in the config")
    ap.add_argument("--backend", choices=("sim", "stripe"))
    ap.add_argument("--seed", type=int)
    ap.add_argument("--agent-seed", type=int)
    ap.add_argument("--temperature", type=float)
    ap.add_argument("--run-stripe", action="store_true", help="allow live Stripe test-mode calls")
    ap.add_argument("--out", default=str(OUT_ROOT))
    return ap.parse_args(argv)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = parse_args(argv)
    cfg = load_config(args.config)
    if args.all:
        runs = cfg["runs"]
    elif None in (args.backend, args.seed, args.agent_seed, args.temperature):
        raise SystemExit("give --all, or --backend, --seed, --agent-seed and --temperature")
    else:
        runs = [{"backend": args.backend, "seed": args.seed, "agent_seed": args.agent_seed,
                 "temperature": args.temperature}]
    if any(r["backend"] == "stripe" for r in runs) and not args.run_stripe:
        raise SystemExit("Stripe-backend runs make live test-mode calls: pass --run-stripe")
    for r in runs:
        out = run_one(cfg, r["backend"], r["seed"], r["agent_seed"], r["temperature"], out_root=args.out,
                      argv=["-m", "agent.run_case_study", *argv], config_path=Path(args.config))
        s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        print(f"{out.name}: purchases chosen {s['model_purchase_calls']}, created {s['created']}, "
              f"post-stop denied {s['post_stop_agent_denied']}, acceptance {s['acceptance']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
