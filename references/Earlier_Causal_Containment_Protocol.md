# P4: Research protocol and novelty gate (working draft)

**Draft title**: Verifiable Causal Containment of Stateful AI Agents: Coordinating Emergency Revocation, Delegated Execution, and Persistent Memory.

## Scope and scientific question

At emergency stop time `t0`, a root agent has a dynamically evolving causal descendant set involving delegated agents, memories, queues and tool effects. We want a **bounded, explicitly scoped outcome contract**, not merely a kill-switch callback success:

- **VERIFIED**: For the predeclared set of controlled effect boundaries and a finite observation interval, independently available evidence supports that no prohibited root-derived effect committed after `t0`. This does *not* mean all possible real-world effects everywhere are impossible.
- **VIOLATED**: At least one prohibited post-stop root-derived effect is independently observed.
- **INDETERMINATE**: At least one potentially consequential operation lacks sufficient trustworthy evidence, including a known unmanaged endpoint without receipt. Never silently substitute a missing log for non-occurrence.

## Primary research questions

- **RQ1: Causal closure.** Can an enforceable provenance model identify descendants of a stopped agent across delegation and memory reuse with bounded overhead, including missing-link handling?
- **RQ2: Selective containment.** Can a control plane freeze affected operations/memories without stopping unrelated permitted workflows, compared with a broad global shutdown?
- **RQ3: Honest outcome classification.** Can independently verifiable tool effects and attestation completeness sharply reduce falsely asserted containment, especially under lost acknowledgments or unmanaged endpoints?
- **RQ4: Recovery.** Can a revoked workflow be safely resumed under new policy versions without replaying stale memory-derived instructions or duplicating external effects?

## Hypotheses (must be tested, not pre-assumed)

H1: A causal-lineage-aware selective barrier blocks more prohibited *post-stop committed effects* than a strong revocation-and-memory baseline in the same real tool workload.

H2: The barrier's evidence contract avoids falsely declaring containment when effect evidence is missing, but this creates explicit INDETERMINATE cases.

H3: Selective containment preserves a greater fraction of independent authorized operations than an equally strong global stop, at an acceptable p95 latency penalty.

H4: After adversarially missing/corrupted causal edges, a conservative fail-closed design degrades safely rather than claiming proof based on incomplete lineage.

## Novelty constraints and nearest known art (October 17, 2026)

1. Microsoft Agent Governance Toolkit documents action policy enforcement, multi-agent controls, kill switches, saga patterns, and explicit limitations concerning kill-switch status, outcome logging, knowledge flow, and cross-session combinations. We cannot claim discovery of agent kill switches or governance layers.
   https://microsoft.github.io/agent-governance-toolkit/LIMITATIONS/
2. Liu, Yu, Zhang, *Revocation Is Not Complete at Receipt* (arXiv:2610.15991, submitted October 11, 2026). Defines and experimentally evaluates bounded effect-aware revocation outcomes at distributed gateways. **Direct competitor**: Do not reproduce its C0–C5 contract as new work. Distinguish on dynamically evolving delegation/memory causal closure and selective recovery.
   https://arxiv.org/abs/2610.15991
3. Anthropic client memory tool: the application controls memory file operations. Anthropic managed-agent memory: immutable memory versions and persistence. Novelty cannot be 'adding memory versions' or 'persistent memory' alone.
   https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
   https://platform.claude.com/docs/en/managed-agents/memory
4. Distributed fencing tokens are a well-established technique; do not claim novelty in fencing a stale worker. 
   https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html
5. Transactional outbox and saga orchestration are established methods for distributed side effects and compensation. Use as stronger baselines, not inventions.
   https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html
6. SailPoint announced AI-agent identity governance and kill-switch capability October 6, 2026. Product announcements do not prove all features exist or do not exist in deployment. Do not imply vendor vulnerabilities without direct tests.
   https://investor.sailpoint.com/news-releases/news-release-details/chaos-control-sailpoint-unlocks-secure-agentic-era-autonomous

## Next studies: required before paper drafting

| Study | Setup | Baseline | Measurement | Novelty relevance |
|---|---|---|---|---|
| E1: Cross-system delayed effects | Two real MCP or HTTP tools; process killed while request in flight | Stop, revoke, tool-side fence | committed effects, missed effects, p95 | Establishes semantics but not alone novel |
| E2: Missing lineage and fork-join DAG | 2–5 agents, memory derivative, replay and orphan descendants | Exact ancestor graph / per-agent halt / broad freeze | missed descendant rates, overhead, collateral effects | Core proposed new algorithm |
| E3: Persistent memory after kill | Same memory read by successor in new session after old credentials revoked | No quarantine / coarse quarantine / provenance scoped | contaminated actions, legitimate tasks retained | Cross-layer novelty |
| E4: Evidence completeness | Lost ACK, read-only and write-only tools, restart during validation | optimistic acknowledgement, C0–C5-style baseline | false-verified, indeterminate, verifier time | Must substantially extend the Oct 2026 paper |
| E5: Safe resume | Restart after containment with policy changes and idempotency keys | ad hoc restart / replay / saga only | duplicate effects, stale authority, recovery latency | Differentiates from ordinary stop mechanism |
| E6: Operational overhead | 100s–1000s tool actions and graph nodes | OPA-style per-call governance, global freeze | p50/p95 latency, throughput, storage, false blocking | Publication viability |

## Stage-0 limitations and interpretation

We have run two local feasibility exercises. The 500-seed event simulation is *not experimental evidence about production agent systems*. The 30-per-arm SQLite experiment forces an authorization-to-commit race rather than sampling naturally arising races. Neither should appear in a research paper as external validity evidence. A main conference paper needs meaningful experiments E1–E4 with real adapters, stronger baselines and documented uncertainty.

## Go/no-go threshold before conference submission

- Novel method beyond simply composing existing controls, with a clear algorithm or formal property.
- Demonstrated cross-layer failure not addressed in documented control baselines, with reproducible test harness and comparable permissions.
- Correctness tests include counterexamples: unmanaged tools, incomplete provenance, unknown outcomes, race after dispatch, remote crashes, and legitimate unaffected actions.
- Independent ground-truth effect ledger, separate from agent or governance intent logs.
- Measurements include uncertainties and costs, not only success rate; compare against strong, fairly implemented controls.
- No production sensitive or customer banking data; use synthetic accounts and mock transactions only.
- Authorship and conference claims should follow actual novelty and evidence, not speculative EB-1A benefits.

## Pragmatic next architecture

A FastAPI or FastMCP tool adapter writes to a SQLite/Postgres ledger; an agent orchestrator dispatches work and persists notes; a policy/memory adapter maintains identity-linked provenance; an emergency controller changes versioned authorizations; a separate validator queries effect records. The test harness controls injected delays/timeouts, records an append-only event trace, and checks a predeclared bounded post-stop window. Prefer no real external APIs in first phase.
