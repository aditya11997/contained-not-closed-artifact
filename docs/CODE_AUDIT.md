# Code and experiment audit — research_code/ (as of 2026-10-18)

Every finding below was reproduced on Python 3.13 against the archive as shipped.
Re-run: `python3 scripts/audit_sink_defects.py` and `python3 scripts/audit_scheduling_equivalence.py`
from the repo root (they import `research_code/p4lab`).

Severity: **S1** = invalidates a claim or produces a wrong outcome · **S2** = weakens validity / reviewer will object · **S3** = hygiene.

## A. Processor sink (`p4lab/model.py :: SyntheticProcessor`)

| # | Sev | Finding | Evidence | Fix (WP) |
|---|---|---|---|---|
| A1 | S1 | **Idempotency key not bound to the request.** Reusing key `K` for a *different* payment returns the cached `CANCELLED`, although payment B was never cancelled and later captures. The sink itself emits a false closure. | `cancel("A",…,"K") → CANCELLED; cancel("B",…,"K") → CANCELLED; state(B) = CAPTURED` | Key scope = (actor, key); store request fingerprint (uid, action); mismatch → `IDEMPOTENCY_MISMATCH` error (mirrors Stripe behaviour, to be verified in WP5). (WP1) |
| A2 | S1 | **Key poisoning across actors.** An unauthorized actor's `DENIED` is cached under `K`; the legitimate operator's later call with `K` is also `DENIED`, and the payment captures. | `cancel(…,"original_agent","K") → DENIED; cancel(…,"remediation_operator","K") → DENIED` | Authorize *before* idempotency lookup; namespace keys per actor; don't cache auth failures. (WP1) |
| A3 | S1 | **Caller controls the clock.** `cancel(uid, finish_at, …)` trusts the caller's `finish_at`. Back-dating to `0` after capture still yields `CANCELLED`. `independent_state(uid, at_time)` also takes caller time. | `capture_at=3; cancel("A", 0, …) → CANCELLED` | Sink-owned discrete-event clock; callers can't supply time. (WP1) |
| A4 | S2 | No capture events. Captures are implied by comparing `at_time >= capture_at`; nothing records *when* capture happened, so there's no event log to check independently. | code read | Event-sourced, append-only, hash-chained log: `ACCEPTED`, `CANCEL_REQ`, `CANCELLED`, `CAPTURED`, `DENIED`, `STATUS_READ`. (WP1) |
| A5 | S2 | `authority_violations` counts `DENIED` replies, and is **always 0** (0/3,600 runs) because every policy pre-filters `permitted`. It's a vacuous metric, and the name is misleading: a denial is a *rejection*, not a violation. | audit script | Rename to `denied_calls`; add a real violation metric (effects by revoked identity after t0 = 0 by construction, asserted by the checker). (WP1/WP3) |
| A6 | S2 | Capture time is deterministic and known exactly to the policy. There's no race, so the "finality" story is trivial. | code read | Capture jitter (early or late relative to the observed nominal time); policy sees nominal only. (WP1) |
| A7 | S2 | Risk scores are perfectly calibrated in the base configs (mean risk 0.549 vs realized 0.554), so the planner's objective **equals** the evaluator's expected loss. | audit script | Keep calibrated as one cell; add a *noisy* (not only shifted) score model. (WP4) |

## B. Policies (`p4lab/policies.py`)

| # | Sev | Finding | Evidence | Fix |
|---|---|---|---|---|
| B1 | S1 | **The "candidate" is a textbook problem.** Selecting and ordering cancels against capture deadlines to maximize expected gain is 1‖ΣwⱼUⱼ. The classical Lawler–Moore (1969) EDD + knapsack DP matches the subset DP's objective in **1,200/1,200 seeds in every config**, and runs 5–9× faster. The DP-vs-EDF result is "an exact optimizer beats heuristics on its own objective." | `audit_scheduling_equivalence.py` | Lawler–Moore becomes the **scheduling baseline**. Keep the subset DP only as a brute-force test oracle. (WP2) |
| B2 | S2 | EDF "accept-if-feasible" isn't a competent runbook. It never drops an earlier, low-value job to make room, so it's close to a strawman. | code read | Add a Moore–Hodgson-style weighted drop heuristic as the "competent manual" baseline. (WP2) |
| B3 | S2 | All policies are **open-loop**: they plan once and never observe outcomes. With no faults this is fine; with faults it makes assurance irrelevant. | code read | Runbooks layer (WP2): ack-trust, verify-each, batch-then-reconcile, adaptive. |
| B4 | S3 | Subset DP caps experiments at n ≤ 12. Lawler–Moore scales pseudo-polynomially. | runtime table | Scale to n ∈ {8, 32, 128}. (WP4) |

## C. Execution and experiments (`p4lab/experiments.py`)

| # | Sev | Finding | Fix |
|---|---|---|---|
| C1 | S1 | `evaluate()` executes plans with **no faults**, and the sink always answers truthfully. `assurance.py` is never used in an experiment, so H3 / false closure is never measured. | Fault layer and runbooks in the experiment loop. (WP1–WP4) |
| C2 | S1 | **Seed overlap.** All 250 pilot seeds (71231–71480) are reused inside `main` (71231–72430). Main isn't independent of the pilot, and no holdout exists. | Partitions: `dev` 100000–199999 and `holdout` 900000–999999. Holdout use is blocked by the runner unless `PREREG.md` is frozen at a git tag. (WP4) |
| C3 | S2 | All 6 configs share one seed range, so the configs are correlated. That's acceptable if stated. There are 12 primary comparisons with no multiplicity control. | Holm correction over pre-registered primary contrasts; state seed-sharing explicitly. (WP4) |
| C4 | S2 | Only means plus a percentile bootstrap. No win/tie/loss counts, no effect sizes, no per-seed traces. | Analysis module. (WP4) |
| C5 | S2 | No run manifest (git hash, Python version, config hash, CLI args). | Manifest JSON per run. (WP4) |
| C6 | S3 | Configs are hard-coded in Python. | JSON/TOML config files under `configs/`, hashed into the manifest. (WP4) |
| C7 | S2 | Single serialized operator with 1–5 "minutes" per cancel, unjustified. A reviewer will ask: "why not cancel everything via API in parallel?" Capacity binds in 70% of main seeds but only 25% of loose seeds. | Make capacity a factor `k ∈ {1, 4, ∞}`; justify per-cancel time as maker-checker approval; `k = ∞` is the negative control. (WP4) |

## D. Assurance (`p4lab/assurance.py`, `demo_recovery.py`)

| # | Sev | Finding | Fix |
|---|---|---|---|
| D1 | S2 | The "independent" verifier calls `processor.independent_state`, the **same object** as the sink. That isn't independent. | Separate checker *process* reading an exported event log. It must not import the policy/runbook packages (enforced by test). (WP3) |
| D2 | S2 | `lose_ack` only relabels the reply after the fact; `query_available` is a caller flag. Status reads cost no time. The tests are true by construction. | Transport fault layer: drop-before-commit, drop-after-commit, outage windows; a status read costs time and capacity. (WP1) |
| D3 | S3 | `Journal` is never used in experiments. | Remediator receipt journal used by every runbook; crash-matrix tests. (WP3) |

## E. Tests (`test_lab.py`)

| # | Sev | Finding | Fix |
|---|---|---|---|
| E1 | S2 | `if __name__=="__main__": unittest.main()` sits **before** `class AssuranceTests`, so `python test_lab.py` runs **7 tests, not 10**, and silently skips all assurance tests. It only works via `python -m unittest`. | Move to pytest; one file per area. (WP0) |
| E2 | S2 | The oracle-leak test only checks that one dataclass lacks an attribute. Nothing stops `policies.py` from importing `Payment` or calling `score_truth`. | AST/static import test over `policies/` and `runbooks/`. (WP0/WP3) |
| E3 | S2 | No fairness test that all policies get identical observations, capacity and authority. | Fairness test: hash of the observation stream per seed is identical across policies. (WP2) |
| E4 | S2 | No tests for the A1–A3 defects (that's how they survived). | Regression tests that fail on today's code. (WP0) |
| E5 | S3 | `test_repeatable_and_no_authority_violation` is trivially true. | Replace with a golden-file regression on a small seeded run. (WP4) |
| E6 | S3 | No property-based tests. | `hypothesis` for sink invariants (at most one effect per payment; captured is never cancelled; terminal states absorbing). (WP1) |

## F. Documentation and scope

- `AGENTS.md`, `CODEX_START_HERE.md` and `MILESTONE_01.md` target Codex + LangGraph + Azure. They're **superseded** by `CLAUDE.md` and `docs/REVAMP_PLAN.md`. Keep `SCIENTIFIC_GUARDRAILS.md`; its rules still apply.
- The title promises "Safe Resumption", but nothing implements resumption of the *agent*. Only remediator crash and replay exist.
- The earlier causal-containment protocol overlaps heavily with Liu et al., arXiv:2610.15991 (DAI '26). That direction is dropped.

## What is sound and must be preserved

Observation firewall concept · cancel-only separate authority · disposition vocabulary (CLOSED_CANCELLED / CLOSED_EXPOSURE / UNRESOLVED_*) · paired-seed design · stop rules and claim discipline · the existing `results/` (archived as exploratory, never cited as evidence).
