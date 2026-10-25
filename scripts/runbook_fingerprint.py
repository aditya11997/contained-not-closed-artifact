"""SHA-256 over the report, sink log and call trace of 8,400 uninterrupted episodes (dev seeds
100000-100099 x jitter {none, uniform 6} x faults {none, low, high} x k {1, 4} x P0-P6).

Run it before and after a runbook change that must not alter normal runs; the two hashes
must be equal. Usage: python scripts/runbook_fingerprint.py
"""
import hashlib
import json

from p4.harness import run_episode
from p4.runbooks import POLICIES
from p4.sim.faults import load_fault_presets
from p4.sim.scenario import JitterConfig, ScenarioConfig, make_scenario, partition_seeds


def main() -> None:
    presets = load_fault_presets()
    h, n = hashlib.sha256(), 0
    for jitter in (JitterConfig(), JitterConfig("uniform", 6)):
        for seed in list(partition_seeds("dev"))[:100]:
            scenario = make_scenario(seed, ScenarioConfig(n=8, slack="tight", jitter=jitter))
            for fault in ("none", "low", "high"):
                for k in (1, 4):
                    for policy in POLICIES:
                        ep = run_episode(scenario, policy, presets[fault], seed, k)
                        body = {"r": ep.report.to_dict(), "log": [dict(e) for e in ep.log],
                                "trace": [(r.t_sent, r.t_arrive, r.op, r.uid, r.attempt, r.fault)
                                          for r in ep.trace]}
                        h.update(json.dumps(body, sort_keys=True).encode())
                        n += 1
    print(n, h.hexdigest())


if __name__ == "__main__":
    main()
