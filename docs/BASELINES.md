# Baselines and policies (WP2)

All policies get the same observations, capacity `k`, authority and fault realization per seed (`tests/fairness/`). None sees `D_actual` or `y_suspect` (`tests/leakage/`). Planners maximize expected gain `a·(p − c(1−p))` over permitted payments with positive gain; a cancel is feasible iff it completes strictly before `d_nominal`. "To verify" means the source has not yet been opened and checked; it must be checked before the related-work pass (WP8).

**Selection (`p4/sched/`)**

- **EDF** (`edf.py`). Earliest deadline first, accept-if-feasible; port of the legacy planner. Anchor: Jackson's EDD rule (1955) — *to verify*.
- **Value/time greedy** (`greedy.py`). Order by gain / s, accept-if-feasible; legacy port. Folklore value-density heuristic; not one of the seven policies.
- **Weighted Moore–Hodgson** (`mh.py`). EDD order; while infeasible, drop the scheduled job with the smallest gain/s; then re-add dropped jobs that still fit (fill pass; see CHANGELOG_RESEARCH, WP2). Heuristic for weighted jobs. Anchor: Moore, *Management Science* 15(1), 1968, incl. Hodgson's algorithm — *to verify*.
- **Lawler–Moore** (`lawler_moore.py`). Exact for 1‖ΣwⱼUⱼ: EDD order + DP over completion time, O(n·Σs). Equals the subset DP on 1,000 random instances (`tests/unit/test_lawler_moore.py`). Exact **only for k = 1**; for k > 1, LM selection + EDD list scheduling + fill pass is a **heuristic**, and the paper must say so. Anchor: Lawler & Moore, *Management Science* 16(1), 1969 — *to verify*.
- **Subset DP** (`subset_dp.py`). Brute-force legacy port; test oracle only, never in tables.

**Runbooks (`p4/runbooks/`)** — all use one idempotency key per payment, `{incident}/{uid}/cancel/v1`, on every retry.

- **P0 none.** No calls. Reports every payment CLOSED_EXPOSURE after its nominal deadline (no evidence). Motivation only.
- **P1 edf / P2 mh / P3 lm_ack** (`ack_trust.py`). Send planned cancels in order; a CANCELLED reply *or a timeout* ⇒ CLOSED_CANCELLED; TOO_LATE ⇒ CLOSED_EXPOSURE; unplanned ⇒ CLOSED_EXPOSURE after d. Mirrors an ops dashboard that marks "cancel sent" as done. That ACK ≠ completion is Liu et al., arXiv:2610.15991 — *to verify*.
- **P4 lm_verify** (`verify_each.py`). After a timed-out cancel, read status until a definitive answer or d passes; a PENDING read ⇒ re-send the same key if still feasible. The next cancel on that worker starts only after that. Conservative assurance; no specific anchor.
- **P5 lm_reconcile** (`reconcile.py`). Fire all planned cancels, then replay each timeout with the same key until a definitive reply or d passes. Saga-style compensate-then-reconcile: Garcia-Molina & Salem, "Sagas", SIGMOD 1987 — *to verify*. Same-key retry as safe probe: Stripe idempotent requests docs (docs.stripe.com/api/idempotent_requests) — *to verify*.
- **P6 adaptive** (`adaptive.py`, candidate). P5 plus per-event re-planning: at each free-worker event, re-solve LM over the not-yet-attempted candidates using the true worker-free times and send the plan's first job (P5 follows its t = 0 plan). Once no fresh cancel fits, it replays timeouts exactly like P5, then runs the sweep. Redesigned at G1 (2026-10-20, CHANGELOG): the earlier version also ranked timeouts against fresh cancels with weight (1 − ρ̂)·gain and replayed them first, which made it worse than P5 on dev. Designed on dev seeds only.

**Evidence contract (P4–P6).** CLOSED_CANCELLED only with a sink reply/read of CANCELLED; CLOSED_EXPOSURE only with CAPTURED or TOO_LATE; otherwise UNRESOLVED_PENDING or UNRESOLVED_UNKNOWN. After the last nominal deadline, each runs an accounting sweep: a status read of every open payment, retried up to 3 times on timeout. With `late_cancel` (default true, identical for P4–P6), a sweep read that still says PENDING for a permitted payment with gain > 0 is followed by a same-key cancel; `late_cancel = false` is a secondary-analysis ablation, run at jitter j = 6 because at the primary j = 3 late cancels are effectively inactive (G1).

**Restart from the journal (WP3).** Every runbook journals intent → result for each call. After a crash, a fresh instance re-plans as at t = 0, skips payments it already attempted, and replays the journal through the same rules. An intent with no result is re-sent once with its same key by P1–P3 (outbox recovery; the ACK-trust rule then applies to the reply), and counts as a timeout that P4–P6 resolve with sink evidence (`tests/crash_matrix/`). The restart may be delayed (`restart_delay`, default 0). Uninterrupted runs are unchanged (`scripts/runbook_fingerprint.py`).
