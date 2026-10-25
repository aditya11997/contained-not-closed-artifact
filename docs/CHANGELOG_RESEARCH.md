# Research changelog

Records changes that affect experiments, baselines, seeds or claims. Newest first.

## Fix before the prereg freeze (2026-10-27)

Open items that must be settled, recorded here with their rationale, and frozen in `docs/PREREG.md` before the `prereg-v1` tag. Items marked *dev* are tuned on dev seeds only; never tune on holdout.

- [x] *dev* — Outage parameters λ, L, r in `configs/faults.toml` are placeholders (WP1 entry). *Fixed at G1 (2026-10-20): high λ = 0.02, L = 4, r = 0.5; low λ = 0.005, L = 2, r = 0.5 (unchanged values; see the G1 entry).*
- [x] *dev* — P6's ρ̂ prior, currently Beta(1, 1), is a placeholder (WP2 entry). *Accepted Beta(1, 1) at G1, then moot: the G1 P6 redesign removed ρ̂ (see the G1 entry).*
- [x] The negative-control config (`k = ∞`, faults none; WP4 `configs/negative_control.toml`) must use jitter = none. With late captures, H4's equality would also depend on `late_cancel` timing rather than only on assurance. *Done in WP4; asserted by `tests/unit/test_runner.py`.*
- [x] The secondary analysis runs `late_cancel = false` for P4–P6 as an ablation (WP2 follow-up entry). *Done in WP4 (`configs/secondary.toml`, last block). See the WP4 entry: on dev seeds the ablation is currently vacuous. Changed at G1: the ablation now runs at j = 6 with late_cancel on and off (see the G1 entry).*
- [x] *dev* — Sweep timing (end-of-run vs per-deadline reads): decide on dev seeds at G1 (Oct 25), only if dev runs show meaningful late-capture exposure. Current: a single end-of-run sweep (WP2 follow-up entry, "Known limitation"). *Decided at G1: keep the single end-of-run sweep (see the G1 entry).*
- [x] *dev* — Primary capture jitter `uniform3` (j = 3, `configs/presets.toml`; also `early3`) is a WP4 placeholder (WP4 entry). *Fixed at G1: j = 3.*

## Threats to validity: not tested live

Paths that exist in code and in offline tests (simulator, `tests/helpers/fake_stripe.py`) but were never exercised against Stripe test mode. The paper's threats-to-validity section must list each of them. Add to this list; strike an item only when a live run covers it, and name that run.

- **Dropped requests** (`drop_req`): only `drop_resp` (lost ACK) was injected live (run `stripe-20261023T033550Z`).
- **Outages** (`outage_lost`, `outage_commit`): never injected live.
- **429 and 5xx handling**: the token bucket, the 429 / `idempotency_key_in_use` retry and the mapping of unmapped responses (5xx) to errors with no effect are tested offline only. Stripe never returned them in a live run.
- ~~**Wall-clock capture timing** (`WallClockDriver`): every live conformance run used the logical clock. The WP6 Part B case study is meant to be the first wall-clock run.~~ *Covered live by the WP6 case study, e.g. run `cs-20261024T171623Z-stripe-100601` (P6 on the stop's `WallClockDriver`, merchant captures on Stripe test mode at their wall time).*
- **Authorization expiry**: uncaptured PaymentIntents expire after days. Test runs last minutes, and the sim has no expiry (docs/STRIPE_MAPPING.md, "Known differences").
- **A concurrent agent thread** (stop while the agent is still working; denied calls during the episode): tested offline only (2026-10-22 entry below), until WP6 Part B runs. *Partly covered by the WP6 Stripe runs (e.g. `cs-20261024T171623Z-stripe-100601`): stop while the agent thread is running, and post-stop agent calls DENIED and in the episode log. Still not tested live: a denied agent call **during** the episode. In every WP6 run, the post-stop calls landed at the stop instant, before the episode started.*
- **Live fault injection covered dropped responses only.** Every live fault was a lost ACK (`drop_resp`). So C1's 13 mapped rows say nothing live about dropped requests or outages, and those are the faults behind every P3 false closure. *(Added 2026-10-24 from the post-hoc review, docs/POSTHOC.md PH1.)*

## Threats to validity: model and design (post-hoc review, 2026-10-24)

From the post-holdout reviewer session (docs/POSTHOC.md PH1). The paper's threats-to-validity section must list each of these. None of them changes a frozen result or verdict.

- **Timeouts are as fast as replies.** A timed-out call returns its timeout at `t_sent + latency`, the same moment a successful reply would arrive (`p4/sim/faults.py`, `FaultyTransport._submit`). Real client timeouts are usually much longer than the median latency. Cheap timeouts favour every policy that retries or verifies after a timeout (P4, P5, P6). The post-hoc sweep PH3 (`--timeout-factor`, dev seeds only) measures how much the P4/P5 contrasts with P3 depend on this. It is exploratory.
- **Stale and reordered reads are not modelled.** A status read returns the sink's current state at arrival, and replies are never reordered or duplicated. The only faults are drops, lost ACKs and outages, and none of them fabricates a wrong definitive answer. So H1's second half (P4–P6 never close falsely) holds by construction of the evidence rule under this fault model. It verifies the implementation; it is not an empirical finding about real processors with eventually consistent reads.
- **P3 has no late cancel and no sweep.** `late_cancel` and the accounting sweep apply to P4–P6 only (`LATE_CANCEL_POLICIES`). So P4 − P3 and P5 − P3 include the effect of post-deadline cancels, which P3 cannot make. On the holdout primary grid this is small: the only faultless seeds where P4 ≠ P3 are late cancels, and `analysis/posthoc/p4_vs_p3.py` reports the "recovered late" class. The paper must state it.
- **H4 is a weak control.** With k = ∞, no faults and no jitter, every policy sends every planned cancel at t = 0 and every call succeeds. Equality across P3–P6 then follows almost directly from the shared Lawler–Moore plan. H4 checks the plumbing (same plan, no spurious calls). It doesn't show that assurance is free in any regime with failures.
- **P3's outbox recovery fails after two crashes on one in-flight payment.** Two crashes before the same cancel's result is journaled leave two orphaned intents for it. P3 (`AckTrust.recover_orphans`) then queues it twice and hits its own "second call in flight" assertion. This was found while testing P3b with a two-crash hook. The holdout crash matrix crashes at most once per run (`restarts` ≤ 1 in every row of `results/holdout/crash_matrix/crash_runs.csv`), so no frozen result is affected. P3 is not changed, because it is a frozen baseline. P3b's recovery dedupes orphans.
- **For k > 1 the shared scheduler is a weak heuristic, and P2 beats it.** P3–P6 all use the k = 1 Lawler–Moore selection plus an EDD fill pass (`p4/sched/lawler_moore.py`). At k = 4, n = 32, P2's weighted Moore–Hodgson has much lower loss than P3 with no faults, so the scheduler alone causes the gap. It also has lower loss than P5 at every fault level, so in those cells an ACK-trust heuristic beats every evidence-bounded runbook in the study. The P4/P5/P6 − P3 contrasts stay fair, because all four share the scheduler. But the paper may call Lawler–Moore exact, or the strongest scheduling baseline, only at k = 1. Numbers: `paper/generated/posthoc/scheduler_k.csv` (PH8, `analysis/posthoc/scheduler_k.py`). *(Added 2026-10-25 from the G2 review.)*
- **H5 covers one crash per episode; multi-crash resumption evaluated only exploratorily (PH7).** The frozen crash matrix crashes at most once per run. Two crashes per episode (on the same in-flight payment, or at different points) were run only in the post-hoc PH7 analysis, on dev seeds. That result is exploratory and cannot extend H5. *(PH7 is the analysis requested as "PH4"; it was renumbered because PH4 was already taken.)*

## 2026-10-25 — Gate G2: framing and title (holdout results only; not a deviation)

- **Review.** A fresh-context session regenerated `paper/generated/` from `results/holdout` at `d0d335f`. Every file matched byte for byte, except `stripe.tex`, which `analysis.stripe_mapping` writes and `analysis.report` does not. The verdicts in `paper/generated/verdicts.md` are unchanged. Nothing was tuned or rerun.
- **Framing: contract + cost-of-assurance benchmark, not a method paper.** PREREG §9 makes this a consequence of H3 being not supported. P6 equals P5 on every seed at k = 1, and no P6 − P5 CI excludes 0 at k = 4 (`contrasts_primary_secondary.csv`). P6 is reported as a pre-registered null. The finding the results lead with is in `contrasts_primary.csv`:
  - verify-before-next (P4) costs loss at k = 1 (H2);
  - reconcile-after (P5) is never above P3 at the Holm level, and is below it in most faulty cells, while having no false closures.

  The P5 result carries the existing caveats: cheap timeouts (PH3) and P3's lack of late cancels.
- **Title** (commit `4741078`): *Contained Is Not Closed: Evidence-Bounded Disposition and Safe Resumption of Remediation for Pre-Revocation Agent Payments*.
  - "Deadline-Aware" was dropped because it reads as a method claim, which H3 rules out.
  - "Safe Resumption" stays by the PREREG §4 rule (H5 supported). The paper must scope it to one crash per episode.
- **New threat** (k > 1 scheduler; list above). The analysis is PH8 in `docs/POSTHOC.md`, with the plan committed before the code.

## 2026-10-24: Post-hoc PH7, multi-crash resumption; post-hoc dev runs moved (not a deviation)

- **Plan first.** The PH7 plan was committed in `docs/POSTHOC.md` (d455d20) before its code or run. The PH3/H2 caveat for the paper's H2 discussion is recorded there too ("Findings to carry into the paper"), with no typed numbers.
- **Design.** `experiments/posthoc_multicrash.py` imports the frozen `experiments/crash_matrix.py` cells, scenarios, faults, k and grading unchanged. Each run's first crash is the crash-matrix hook of its cell, and the restarted instance crashes once more, in one of two variants:
  - `same`: same crash point, same payment;
  - `different`: next crash point, its first call.
- **Scope.** Policies P4, P5, P6 and P3b. P3 is not run and not changed. Dev seeds 112000–112499.
- **Outcome.** Per POSTHOC.md's rule, every P4–P6 (variant, cell, policy) pair holds, with enough double crashes in each, so the stop rule was not triggered. P3b has no failed invariants; its false closures are measurements.
- **Non-vacuity.** `tests/unit/test_posthoc_multicrash.py` shows the same hook reaches the frozen P3's double-crash failure.
- **Where the numbers are.** `paper/generated/posthoc/multicrash.csv`, from `analysis/posthoc/multicrash.py`. This is exploratory only; H5 is unchanged.
- **Runs moved.** All post-hoc dev runs moved from `~/p4-posthoc/results/dev/` to `~/p4/results/dev/`, with their console logs in `results/dev/posthoc-logs/`. Every file was verified against its SHA-256 and size in `docs/RESULTS_ARCHIVE.md` after the move, and the files hashed earlier were unchanged. The `~/p4-posthoc` worktree is removed once this is merged and pushed.

## 2026-10-24: Post-hoc exploratory analyses PH1–PH6 (not a deviation)

- **Plan first.** `docs/POSTHOC.md` was committed (3929fcd) before any post-hoc code or run. It fixes every design choice in advance.
- **What these analyses do not touch.** All post-hoc runs use dev seeds 110000–111999. No holdout seed was run. PH5 and PH6 only read `results/holdout/`. No frozen file changed, and the frozen-file and golden tests pass. `verdicts.json` and `verdicts.md` are byte-identical after regeneration. The paper may cite these analyses only in a section labelled "post hoc, exploratory".
- **Code added.** Defaults leave the frozen behaviour unchanged.
  - P3b `lm_ack_retry` (`p4/runbooks/ack_retry.py`).
  - Transport `timeout_factor` (default 1), with a dev-only runner flag `--timeout-factor` that is refused for holdout.
  - A static clock-internals test (`tests/leakage/test_no_clock_internals.py`). This is post-hoc verification; it passes on the frozen runbooks.
  - Bonferroni-level CI columns in `contrasts_primary.{csv,tex}`. Only these columns are new; the old columns are unchanged.
  - Scripts in `analysis/posthoc/`.
- **Generated outputs** are in `paper/generated/posthoc/`:
  - PH2: `p3b_*`
  - PH3: `timeout_*`
  - PH5: `p4_vs_p3_*`
- **Runs.** The raw dev runs are hashed in `docs/RESULTS_ARCHIVE.md`. The first timeout-sweep runs had dirty manifests, because tracked docs and tests were edited while they ran. They were rerun on a clean commit, which gave byte-identical `trials.csv`. Both sets are kept.
- **PH3 surprise.** P4 − P3 in H2's cells *shrinks* as m grows, which POSTHOC.md listed as unexpected. Run archives were not kept, so this was inspected through trial-level means (`timeout_policy_means.csv`), not traces: P3 also waits out its own timeouts, so its cancel calls fall and its loss rises with m. The cost of assurance that H2 measures is largest when timeouts are cheap. This must be stated next to H2 as a boundary condition, not as a change to H2.

## 2026-10-24 — WP6 Part B: LLM agent case study (Ollama llama3.1:8b; Stripe test mode and sim)

A case study outside the pre-registered contrasts, on dev seeds only. Nothing under `p4/` or `checker/` changed. Report: `docs/CASE_STUDY.md` (tables and figure generated by `python -m agent.summarize`).

- **What ran.** `agent/procure.py`: a local Ollama model (`llama3.1:8b`, digest in each manifest) chooses `create_purchase` / `get_payment_status` calls. The mandate (budget, vendor allowlist, per-purchase cap) is enforced in code before the gateway. After `k_stop` purchases the harness calls `incident_stop` while the agent thread is running. P6 then runs live on the stop's `WallClockDriver`; P3, P5 and P6 run on a sim replay of the same scenario and fault realization. The checker scores every episode. Current runs (from commit `4b23759`, clean tree): `cs-20261024T171623Z-stripe-100601`, `cs-20261024T171755Z-stripe-100602`, `cs-20261024T171917Z-stripe-100603`, `cs-20261024T172055Z-sim-100611`, `cs-20261024T172210Z-sim-100612`, `cs-20261024T172333Z-sim-100613`. All meet the WP6 acceptance; `revoked_actor_effects` = 0 in every episode.
- **Harness fix (v1 → v2).** In the first run (`cs-20261024T164731Z-sim-100611`) the model issued the whole brief as one batch of parallel tool calls. The wrapper executed the batch before the harness thread could take the stop lock, so the stop fired after more than `k_stop` purchases, and that run has no post-stop agent call (fails acceptance; kept). Fix: the `k_stop`-th purchase pauses the agent thread until `incident_stop` completes, so the harness controls exactly when the stop fires and the rest of the batch runs post-stop. Regression test: `agent/tests/test_case_study_sim.py::test_stop_fires_at_k_stop_inside_a_batch`. This fixes the harness against the WP6 spec. It is not a change to the agent, the mandate, a runbook or the checker, and it was made before any Stripe run.
- **Superseded runs (kept).** The first seven runs (the v1 run and six v2 runs, `cs-20261024T16*`) were made before `agent/` was committed. Their manifests name the base commit `235c883`, because the manifest's dirty flag ignores untracked files. They are labelled "pre-commit, superseded" in `results/case_study/superseded.json` and listed in `docs/CASE_STUDY.md`. The case study was then rerun from committed code. Each rerun's tool-call sequence is identical to its superseded v2 run's.
- **Odd behaviour, reported as is.** In every run the model issued all purchases in one batch, then wrote a summary. Post-stop calls are the rest of that batch, and the model never reacted to a DENIED with further calls. In one sim seed, the model misspelled an allowlisted vendor and the mandate wrapper rejected those calls. P3, P5 and P6 reach identical final states and loss in every run (few payments, `k = 1`, `low` faults): a demonstration, not evidence about the runbooks.
- **Finding: the agent's final summary was wrong in every run.** No final reply mentions the revocation or any DENIED call. Every reply's account of placed orders disagrees with the gateway log, and some list items that were never successfully ordered (generated comparison in `docs/CASE_STUDY.md`, "The agent's own report is wrong"). This supports using processor-side evidence, not the agent's self-report, for disposition.
- **Tests.** `pyproject.toml` testpaths now include `agent/tests`, so `pytest -q` runs the sim-backend integration test (fake chat client; no Ollama or Stripe needed).

## 2026-10-24: 51 per-run files deleted during disk cleanup; regenerated from prereg-v1; all hashes match (reproducibility check, not a deviation)

- After the holdout results were committed, a disk cleanup deleted 51 untracked per-run files: `results/holdout/primary/checker_verdicts.jsonl` and run archives under `results/holdout/{primary,secondary}/runs/`. No tracked file was affected.
- Primary and secondary were re-run with the PREREG §7 commands (`--workers 10`, as in the original manifests) from a clean worktree at `prereg-v1` (`430c6a0`), into a separate folder `results/holdout-regen/`. Nothing was written over existing results.
- Every regenerated run archive and `checker_verdicts.jsonl` matched the SHA-256 and size recorded in `docs/RESULTS_ARCHIVE.md`. That covers the 51 deleted files and the ones still present. The regenerated `trials.csv` and `checker_summary.json` were byte-identical too.
- Only the 51 missing files were copied back. After the copy, all files listed in `docs/RESULTS_ARCHIVE.md` for `results/holdout/` and the conformance runs match their recorded hashes. `results/holdout-regen/` was deleted.
- Results, verdicts and paper outputs are unchanged. This is a reproducibility check, not a deviation.
- Also: `results/dev/` is now `.gitignore`d and hashed in `docs/RESULTS_ARCHIVE.md` ("Dev runs (exploratory, not evidence)"). It is not committed.

## 2026-10-24 — Holdout complete, no deviations

- All five holdout runs (primary, secondary, negative_control, scaling, crash_matrix) and the official C1 run `stripe-20261024T024751Z` ran on tag `prereg-v1` (`430c6a0`) with a clean tree, on holdout seeds starting at 900000. No code, config or rule changed after the tag.
- No deviation under PREREG §8. Every holdout run is checker-ok (`not_ok` = 0 in each `checker_summary.json`). P4–P6 have no false closures, false exposures or duplicate effects. Every false closure is a P0–P3 measurement, and all scaling false closures are P1–P3. Breakdown: `pandas.read_csv("results/holdout/scaling/trials.csv").groupby("policy").false_closure.sum()`. No "(pre-fix)" report was needed.
- Verdicts are in `paper/generated/verdicts.md`. They are unchanged, including the negative ones: H3 not supported, S9 not met.
- `paper/generated/*` and `paper/figs/*` were regenerated from `results/holdout` and matched byte for byte.
- Compact results are committed. Per-run data (`runs/*.tar.gz`, `checker_verdicts.jsonl`, most `timing.csv`) stays local and untracked. It is listed with SHA-256 and size in `docs/RESULTS_ARCHIVE.md` and will be archived externally later. The pre-freeze Stripe runs (`stripe-20261023T*`) are hashed there too, labelled "pre-freeze, not evidence", and are not committed.

## 2026-10-23 — §8: contract violations reported as "not supported (pre-fix)" (PI)

Before the tag. No holdout seed used.

- A P4–P6 false closure, false exposure or duplicate effect no longer only blocks the verdicts. They are still computed, and that run's hypothesis is reported as "not supported (pre-fix)": H1 for the four `experiments.run` configs, H5 for the crash matrix. It sits alongside the post-fix result after the deviation procedure.
- A pre-fix failure of H1 or H5 must appear in the paper. `make results PRE_FIX=<pre-fix verdicts.json>` adds a pre-fix column and `\VerdictPreFix…` macros.
- The other verdicts of a pre-fix report are labelled "(pre-fix)". Any other checker problem still blocks them.
- Code: `analysis/verdicts.py` (`classify`, `gate`, `with_pre_fix`) and `analysis/report.py`, which reads each run's checker verdicts for this. Tests: `tests/unit/test_verdicts.py`, `tests/unit/test_crash_matrix_runner.py`.

## 2026-10-23 — Final pre-freeze rule edits (PI): H1, H2, H4, H5, title, C1, §8, §9

Before the tag. No holdout seed used. These are the PI's decisions on the ambiguities listed after the H3 edit. `analysis/verdicts.py` and its tests match them.

- **H1:** false closures are summed over all seeds per cell. The P0/P4–P6 total must be 0 across the four `experiments.run` configs. Crash-matrix runs are governed by H5.
- **H2:** the Holm-level CI lower bound alone decides; p-values are information only.
- **H4:** exact equality of losses per seed.
- **H5:** counted over all runs, crashed or not. P4–P6 also need 0 false exposures (added to `analysis/crash_matrix.py`). The failed-invariant and ≥ 100-crashed-runs conditions stay.
- **Title:** "Safe Resumption" stays if and only if H5 is supported.
- **C1:** transient failures (429, 5xx, network errors) may be retried up to 2 times. The first complete run decides; the post-fix run decides after an adapter fix; all attempts are reported.
  - `analysis/stripe_mapping.py` now classifies attempts. The conformance conftest keeps each failure's first line in `tests.jsonl` for this.
  - `report --conformance` takes every attempt.
  - Added so the code has a rule: with no complete run in 3 attempts, C1 is not supported.
  - The row-to-test mapping is frozen by citing `analysis/stripe_mapping.py`'s SHA-256.
- **§8:** after any bug fix, rerun all holdout configs. Any checker not-ok run blocks every verdict ("blocked") until resolved; P0–P3 false closures never make the checker not-ok.
- **§9:** "negligible" means P3 false closures < 1% of P3's CLOSED_CANCELLED at both low and high. Added so the code has a rule: each level is summed over the primary cells at that level, and a level with no CLOSED_CANCELLED does not meet the condition.

## 2026-10-23 — H3 rule made exact; every verdict computed by script

Before the tag. No holdout seed used.

- H3's decision rule in `docs/PREREG.md` is replaced with the PI's exact text. At tight slack, a fault level counts if the unadjusted 95% CI upper bound of P6 − P5 is below 0 in at least 3 of its 4 cells. H3 is supported if at least 2 of the 3 levels count. The old wording didn't say when a level counts.
- `analysis/verdicts.py` computes H1–H5 and C1 from their PREREG §4 rules. `analysis/report.py` prints the verdicts, writes `verdicts.json` and `verdicts.md`, and adds `\Verdict…` macros. C1 needs `--conformance <run dir>`, or `make results CONFORMANCE=…`. A missing input gives "not evaluated", never support.
- Tests: `tests/unit/test_verdicts.py` checks each rule on toy tables, the H3 thresholds included (exactly 3 of 4 cells, exactly 2 levels, ci_hi = 0 not counted, normal-slack cells ignored).

## 2026-10-23 — WP7 freeze: docs/PREREG.md; H5 crash-matrix runner

Branch `wp7-freeze`, before the `prereg-v1` tag. No holdout seed used. No change to `p4/` or `checker/`.

- `docs/PREREG.md` is frozen from `PREREG_DRAFT.md`, which is marked superseded:
  - H1, H2 (n = 32 primary, n = 8 secondary), H3 (secondary), H4 and C1, as decided earlier;
  - **new H5** (crash safety), which decides whether the title keeps "Safe Resumption";
  - the Holm family {P5 − P3, P4 − P3} × 24;
  - holdout seeds 900000–901999;
  - the WP6 case study, declared descriptive;
  - the deviation policy, and Appendix A (dev findings, exploratory).
  
  The config files are cited by SHA-256. `tests/unit/test_prereg_frozen.py` checks the hashes, the fault table, policies, cells, seeds, statistics constants and H5 settings against the code, and after the tag that PREREG.md and the cited files are unchanged.
- **H5 needed a holdout runner.** The crash matrix existed only as a dev-seed test helper. Its definitions move unchanged to `experiments/crash_matrix.py`, which adds:
  - a CLI that uses the holdout guard and writes `crash_runs.csv`, `checker_summary.json` and `manifest.json`;
  - `analysis/crash_matrix.py`, the H5 decision: per (cell, policy) of P4–P6, 0 false closures, 0 duplicate effects, 0 failed invariants and ≥ 100 crashed runs. `analysis/report.py` picks it up, with the `\Hfive…` macros.
  
  `tests/helpers/crash_matrix.py` is now a wrapper with the old signatures plus the test-only P3 fixture. `scripts/crash_matrix_summary.py 3` printed byte-identical output before and after the move.
- **Non-vacuity floor of 100 crashed runs.** It comes from a 20-seed dev run (`results/dev/crash-matrix-20`), where the rarest cells crashed in about a quarter of runs.
- `configs/primary.toml`: header comment only, which named the superseded Holm family and called j = 3 a placeholder. Cell ids are unchanged.
- `scripts/estimate_holdout_runtime.py` times the runners on dev seeds; its output is pasted verbatim into PREREG §7 (total estimate 2.3 h on 10 workers).

## 2026-10-23 — Stripe counts generated by script: mapping rows vs conformance tests

- Two Stripe numbers were being mixed up. Commit c655f41 said "all 13 mapped transitions": that is the 13 **rows** of the STRIPE_MAPPING transition table. The changelog's "all 15 tests" is the conformance **tests** (9 scripted transitions + 6 episodes). Both were right but under ambiguous names. History is not rewritten.
- `analysis/stripe_mapping.py --tex` now writes both counts as macros, from a run directory. Row → outcome evidence is declared in `TABLE_ROWS`, and a test keeps it in step with the doc table and the collected conformance tests.
- The conformance conftest now writes `tests.jsonl` with pytest's own per-test outcomes. Before this, a test failing ahead of its outcome record would have left no trace in the run directory.
- Script output for the kept runs, for the record (not paper numbers; the paper uses the post-tag C1 run): run 1 `stripe-20261023T033212Z` gives 12 of 13 rows matched, because it predates the lost-ACK script, so row 12 has no record. Runs 2 and 3 give 13 of 13 rows and 15 of 15 outcome records matched.
- No experiment, baseline, seed or `p4/` code changed.

## 2026-10-22 — Live Stripe tests opt-in; Stripe policy case study dropped; WP6 Part B readiness

Branch `wp5-stripe`, before the prereg freeze. No experiment, baseline, seed partition or default code path changed. The golden test is unchanged.

- **Live Stripe tests need an explicit opt-in.** `@pytest.mark.stripe` tests run only with `STRIPE_TEST_KEY` set **and** `pytest --run-stripe` or `P4_RUN_STRIPE=1` (`tests/conftest.py`, `tests/helpers/stripe_gate.py`). A plain `pytest -q` skips them, with the key set or not; `tests/unit/test_stripe_gate.py` checks this in a subprocess.
  - Reason: run `stripe-20261023T033737Z` (commit 44a7058, dirty tree; argv `pytest -q`) was a full live session started by a plain test run with the key still in the shell. It is kept in `results/conformance/`, and every outcome row matched (`python -m analysis.stripe_mapping`). It is not evidence, because the tree was dirty.
  - The live command is now `pytest -q --run-stripe -m stripe tests/conformance`.
- **Dropped: the WP5 Stripe policy case study** (30 scenarios × n = 8 × {P3, P5, P6} × fault {none, high}). This is cut-list item 2 (REVAMP_PLAN). It was never run, so no results are discarded. The Stripe evidence is the C1 conformance: run `stripe-20261023T033550Z`, where the sim and Stripe event logs and replies were identical for every mapped transition and episode (docs/STRIPE_MAPPING.md).
- **WP6 Part B readiness (changes to `p4/` before the freeze).** WP6 Part B may not touch `p4/`. Its automated tests use the episode (logical) clock. The actual case-study runs use `WallClockDriver`, with the agent loop running in its own thread and `incident_stop` fired while the agent is still working. Two things in `p4/` did not support that, so they are fixed now:
  - *Wall time couldn't reach the episode.* `run_episode` always built its own `EventClock`, and the old `WallClockDriver` (a wrapper with `advance_to`) was never on the runbook's path. Fixes:
    - `WallClockDriver` is now an `EventClock` subclass in `p4/sim/clock.py` (still importable from `p4.adapters.stripe_test`). Its `run_until` sleeps until each event's wall time and records `max_lag` when the caller falls behind.
    - Both gateways take `clock_factory=`, so the stop's clock starts at t = 0 at the stop.
    - `run_episode(..., clock=)` runs the episode on that clock.
  - *Concurrent agent calls weren't safe.*
    - A create in flight when the stop fired could finish after the scenario was frozen. That would leave a Stripe PaymentIntent that is outside the scenario and that nobody remediates.
    - DENIED lines from the agent thread could interleave with the remediator's appends and break the hash chain.
    - On the Stripe adapter, any agent call between the stop and the episode made `attach()` raise. On the sim, the episode's new sink silently dropped that call's DENIED line.
    - Fixes: create and stop are serialized per gateway. Event-log and call-log appends are locked (`SimProcessor`, `StripeTestProcessor`, `CallLog`). `attach()` on the stop's own clock keeps the stop's sink and its log.
  - Tests: `tests/unit/test_clock.py` (wall-time firing, lag). `tests/unit/test_stripe_adapter.py` (a create in flight at the stop finishes first and is in the scenario; the stop clock keeps pre-episode calls; a wall-clock episode captures at `0.5 · D_actual` s on fake time). `tests/unit/test_agent_gateway.py` (a real agent thread through the stop and a `WallClockDriver` episode; every post-stop call is DENIED and in the episode log; checker ok).
  - Holdout: not run yet. The default path (`clock=None`, no `clock_factory`) is unchanged, so the holdout runs are unaffected.

## 2026-10-22 — WP5 Stripe conformance (C1) and WP6 Part A create path

Branch `wp5-stripe`. No experiment, baseline or seed partition changed. The golden test is unchanged, and `run_episode`'s default path is untouched.

- **Simulator interface additions** (needed before the freeze, because WP6 Part B may not touch `p4/`):
  - `p4/sim/agent_gateway.py` adds `create_payment` and `incident_stop`.
  - `SimProcessor.record_denied` logs a refused create as a DENIED line.
  - `run_episode(..., sink_factory=)` adds a hook.
  - `STREAM_AGENT = 4` is the stream for the capture timing of agent-created payments.
  - `CreateReply`, `StopReceipt`, `AgentGateway` and `PROCUREMENT_AGENT` are added in `p4/sim/protocol.py`.
- **Stripe test-mode adapter** `p4/adapters/stripe_test.py`. Its documented behaviour, with URLs, is in `docs/STRIPE_MAPPING.md`.
- **C1 conformance, live** (`pytest -q -m stripe tests/conformance`; `results/conformance/`, not committed):
  - Run 2 is `stripe-20261023T033550Z` at commit 44a7058, clean. All 15 tests passed, and every mapped transition matched the simulator line for line (event log and replies).
  - It also ran P3/P5/P6 × fault {none, high} on agent-created payments. In each, the sim and Stripe logs and reports were identical, and the checker was ok on both.
  - Run 1 is `stripe-20261023T033212Z` at commit b401227. It passed, but its `high` episodes injected 0 faults, so it is not evidence for the lost-ACK row.
  - The fix was a scripted `drop_resp` test plus episode seed 100906, chosen by a deterministic offline search (dev). The test now asserts non-vacuity.
  - Only `drop_resp` was exercised live; `drop_req` and outages were not.
- **Simulator fixes from conformance: none needed.** No mismatch was observed.

## 2026-10-22 — Holm family {P5−P3, P4−P3}; H3 secondary; old P6 tagged

- Decided with the PI: the primary Holm family is {P5 − P3, P4 − P3} × 24 cells = 48 contrasts (`analysis/stats.py` `PRIMARY_CONTRASTS`, test `tests/unit/test_runner.py::test_primary_contrast_family`). P6 − P5 moves out of the family. Reason: on dev, P6 ≈ P5 (G1 follow-up entry below: identical at k = 1; at k = 4, n = 32 no CI excluded 0 and the sign flipped between the design and sanity ranges). H3 stays pre-registered, and P6 still runs in every holdout cell. `analysis/report.py` writes P6 − P5 over the primary grid as `contrasts_primary_secondary.{csv,tex}` with unadjusted 95% CIs (`SECONDARY_PRIMARY_GRID_CONTRASTS`), plus its `Diff*` macros. PREREG_DRAFT §3 is updated.
- H2 primary cells confirmed: tight, k = 1, n = 32 at fault low and high.
- The pre-G1 P6 code state is tagged `dev-g1-old-p6` (commit 3229674), which reproduces `analysis/g1/rho_prior.py` and the pre-G1 P6 rows in `results/dev/primary-500` and `results/dev/g1-*`. The merged `g1-dev` branch is deleted locally.

## 2026-10-21 — G1 follow-up: redesigned P6 vs P5 on the design and sanity ranges

- Design range: `python -m experiments.run --config configs/primary.toml --partition dev --seed-start 100500 --n-seeds 500 --out results/dev/primary-p6b-design-100500` (commit de3e958, clean; 84,000 runs, checker not ok 0). No tuning was done: option (b) has no free parameter, and none was added.
- Sanity range, run once: same command with `--seed-start 101000 --out results/dev/primary-p6b-sanity-101000` (commit b9ae716, clean, the criterion committed beforehand; 84,000 runs, checker not ok 0).
- `python -m analysis.g1.p6_check --design … --sanity … --out results/dev/primary-p6b-sanity-101000/analysis` (table: `p6_check.md` there). Pasted verbatim:

    - design: `results/dev/primary-p6b-design-100500` seeds 100500–100999, commit de3e958d97e6 (dirty False): P6 better in 0 cells, worse in 0 of 24; mean over cells of the per-cell mean P6 − P5 = 0.9; criterion PASSED.
    - sanity: `results/dev/primary-p6b-sanity-101000` seeds 101000–101499, commit b9ae71627b40 (dirty False): P6 better in 0 cells, worse in 0 of 24; mean over cells of the per-cell mean P6 − P5 = 1.6; criterion PASSED.

- Per the PI's rule, P6 stays in the holdout. Observations (same files): P6 and P5 are identical on every seed in all k = 1 cells and in nearly all k = 4, n = 8 seeds; they differ only at k = 4, n = 32, where the sign of the mean difference flips between the two ranges and no CI excludes 0. On dev, H3 (P6 better than P5) is therefore not supported; a holdout null for H3 is the expected outcome and is reportable (PREREG §7).

## 2026-10-20 — G1: dev exploration, fix-before-freeze decisions, P6 redesign

All work on dev seeds; nothing on holdout. Branch `g1-dev`. Generated outputs (not committed, under `results/`): `results/dev/primary-500/analysis/g1_summary.md` (sections 1–5), `g1_p6_traces.md`, `g1_*.csv`; runs `results/dev/g1-sensitivity-200` and `results/dev/g1-rho-prior-200`. Scripts: `analysis/g1/` (commits 24f81ab, 68c50ca, 3229674). Every number below is copied from those generated files.

**Infrastructure.** `experiments.run` gains dev-only `--faults` / `--presets` (refused on holdout; the manifest records the files' sha256) so that exploratory presets (`configs/dev/g1_*.toml`) never touch the canonical ones (commit 2eac6f0, test in `tests/unit/test_runner.py`).

**Findings (exploratory; primary-500 = dev seeds 100000–100499 at commit 2251661 unless noted).**
- *Effect sizes* (`g1_section_1_effects.md`, unadjusted 95% CIs). P5 − P3 < 0 in 10 of 24 cells, never > 0. P4 − P3 > 0 at k = 1 in 7 of 8 faulty cells (cost of assurance); at k = 4, n = 8 P4 beats P3 in 3 of 4 faulty cells. Pre-G1 P6 − P5: CI excluded 0 in 5 cells, all with P6 worse; never better. False closures: P3 1,079 (low) and 4,012 (high); P0 and P4–P6 0 at every level.
- *Late-capture opportunity* (`g1_section_2_late_capture.md`). Pasted verbatim: "Largest P5 missed net share: 0.515% (high/normal/k1/n8; 0.014 payments/seed) → does not meet the threshold." The current sweep's late cancels rescue 0 payments for P5 and P6 at j = 3; P5's apparent rescues are pre-deadline replays landing after d (P5 replays are not deadline-checked). At j = 6 (sensitivity grid) the recoverable share averages 1.733% of P5 loss (max 3.874%; `g1_section_2b_late_capture_grid.md`).
- *Pre-G1 P6 vs P5* (`g1_section_3_p6_vs_p5.md`, `g1_p6_traces.md`). Mechanism at the first divergence, pasted verbatim (seeds, mean P6 − P5): `p6_replays_first | 1902 | 108.7`, `p5_replays_p6_not | 1078 | 46.3`, `replan_no_unknown | 714 | -67.6`, `replan_with_unknown | 179 | 69.0`. P6 replayed sooner after a timeout than P5 and its replays timed out more often (the outage that caused the timeout was usually still on); re-planning with no unknown outstanding was its only gain. The realized share of timed-out cancels that the sink had applied was about 0.5 in every cell, matching ρ̂ ≈ 0.50, so ρ̂ was not the problem. Caveat: after the first divergence, calls shift relative to outage windows, so per-seed differences are partly outage-timing luck.
- *Sensitivity* (`g1_section_4_sensitivity.md`; 200 cells × seeds 100000–100199 × 7 policies, commit 2eac6f0, checker not ok 0). H1 (P3 false closures none < low < high, high > low in 8/8 cells, P4–P6 = 0) held at all 11 grid points. H2 (P4 − P3 > 0 at tight, k = 1) held at n = 32 at every point; at n = 8 with high faults the CI excluded 0 at 7 of 11 points (not at λ = 0.005, r = 0, the (0.005, 2) corner, or j = 6) and never with low faults. Pasted verbatim: "Centre-point check: 33600 of 33600 centre rows matched to primary-500 rows (same factors, seed, policy); metric values differing: 0 (must be 0)."
- *ρ̂ prior probe* (`g1_section_5_rho_prior.md`, pre-G1 P6). Priors with mean 0.75–0.9 lowered P6 loss at n = 32 only by suppressing early replays, not through better calibration; Beta(1, 1) reproduced primary-500's P6 exactly (0 mismatches).

**Decisions (PI, 2026-10-20).**
1. Outage presets unchanged and fixed: high λ = 0.02, L = 4, r = 0.5; low λ = 0.005, L = 2, r = 0.5. Jitter j = 3 (`uniform3`). Single end-of-run accounting sweep. (ρ̂ prior Beta(1, 1) was accepted; it is moot after item 5.) Presets were chosen without using P6 results.
2. `late_cancel` ablation: the j = 3 `late_cancel = false` block is removed from `configs/secondary.toml`; a j = 6 block (`uniform6`, new in `configs/presets.toml`) runs late_cancel on and off over fault × slack at k = 1, n = 32 (12 cells; secondary grows from 48 to 54 cells). `analysis/report.py` pairs each off-cell with the on-cell with the same other factors, now looked up in the secondary run first (test `tests/unit/test_report_ablation.py`). At the primary j = 3, late_cancel is effectively inactive (≤ 0.515% of P5 loss recoverable on dev); the paper must say so.
3. H2: the primary claim is at tight, k = 1, n = 32; n = 8 is reported as secondary, with the dev fragility above stated in PREREG_DRAFT.
4. Primary contrast family (Holm): {P5 − P3, P4 − P3, P6 − P5} × 24 cells (`analysis/stats.py` `PRIMARY_CONTRASTS`). P6 − P3 is dropped from the family. *Superseded on 2026-10-22: P6 − P5 left the family too (see that entry).*
5. P6 redesign, option (b): P6 = P5 + per-event Lawler–Moore re-planning over the not-yet-attempted candidates using the true worker-free times; no replay-first and no ρ̂ (commit de3e958). P0–P5 are unchanged. The golden file was regenerated: only the 7 smoke rows of policy `adaptive` changed. P6 is designed and tuned only on dev 100500–100999, then checked once on dev 101000–101499 with no tuning. **Criterion, fixed before the sanity range is run** (`analysis/g1/p6_check.py`): P6 is "at least as good as P5" iff in no primary cell the unadjusted 95% bootstrap CI of the mean paired difference P6 − P5 lies entirely above 0. If the sanity range fails it, P6 is dropped from the holdout. Results: see the 2026-10-21 follow-up entry (criterion passed on both ranges).
6. From now on, the full `pytest -q` runs before every commit (CLAUDE.md). Commits 68c50ca and 3229674 ran only targeted tests before committing; the full suite passed afterwards.

Superseded by this entry: the dev results for policy `adaptive` in `results/dev/primary-500` and `results/dev/g1-*` describe the pre-G1 P6. They are kept (rule 5), not rerun.

## 2026-10-19 — WP4: runner, configs, seed partitions, analysis

- New `experiments/` (`run.py`, `config.py`, `guard.py`, `manifest.py`), `analysis/` (`stats.py`, `tables.py`, `figures.py`, `report.py`), configs `smoke`, `primary`, `secondary`, `negative_control`, `scaling` (+ `presets.toml` for jitter and score models), Makefile targets `smoke`, `golden`, `results`, `results-dev`.
- **Fixes CODE_AUDIT C2 (dev/holdout partitions; holdout guarded), C3 (Holm over the primary contrasts), C4 (win/tie/loss, per-seed traces in trials.csv, Wilson intervals), C5 (manifest.json per run), C6 (TOML configs, hashed into the manifest and into cell ids), C7 (k ∈ {1, 4, ∞} as a factor), A7 (shifted and noisy score models in the secondary config), E5 (golden regression `tests/golden/`).**
- **Holdout guard.** `--partition holdout` is refused (exit 2, nothing written) unless the tag `prereg-v1` exists, HEAD descends from it, `docs/PREREG.md` at HEAD and in the working tree is byte-identical to the tagged version, and the tracked tree is clean (`tests/unit/test_runner_guard.py`).
- Decided with the PI on 2026-10-19:
  - Jitter placeholder `uniform3` (j = 3) for the primary grid, to be fixed on dev before the freeze (list above).
  - Holm p-values by inverting the percentile bootstrap: p = min(1, 2·min(#{b* ≤ 0} + 1, #{b* ≥ 0} + 1) / (B + 1)), B = 10,000; each contrast also gets the CI at its Holm level.
  - Secondary design: each one-factor variation (score shift ±0.2, noisy σ = 1, early-only jitter, c = 1, n = 128, k = ∞, late_cancel = false) over fault × slack at k = 1, n = 32 (48 cells). Reference cells are the matching primary cells.
  - The holdout guard also refuses a dirty tracked tree.
- My defaults (open for review):
  - Outcomes in `trials.csv` come from the checker's verdict; the runner cross-checks the checker's loss against `p4.sim.oracle.score` and stops on a mismatch.
  - `n_cancel_calls` counts every cancel sent to the transport, replays included; `n_replays` counts journaled replay intents; `time_to_full_disposition` is the latest `reported_at` if every payment is closed, else blank.
  - `wall_ms` is written to `timing.csv`, not `trials.csv`, so that `trials.csv` stays byte-identical across runs and worker counts.
  - The cell id hashes the factor values *and* the resolved fault and scenario parameters, so a retuned preset yields new cell ids.
  - Exported runs are kept in `runs/<cell_id>.tar.gz` (deterministic archives); outputs under `paper/` are refused unless every run's manifest says partition = holdout.
- Acceptance run (dev seeds 100000–100499, commit 2251661, clean tree): `python -m experiments.run --config configs/primary.toml --partition dev --n-seeds 500 --out results/dev/primary-500`, 141.85 s wall time. Last line of its output, pasted verbatim:

    primary: 24 cells x 500 seeds x 7 policies = 84000 runs; checker not ok: 0; malformed_lines=0, chain_broken=0, anchor_mismatch=0, non_monotone=0, duplicate_effects=0, cancel_after_capture=0, revoked_actor_effects=0, truth_mismatch=0, key_violations=0, inconsistent_replies=0, report_malformed=0, unsupported_evidence=0, false_closure=15489, false_exposure=0

  All false closures are from ack-trust runbooks (P1 edf 5526, P3 lm_ack 5091, P2 mh 4872; P0 and P4–P6 0), per a groupby of that run's `trials.csv`. Exploratory; not evidence for any hypothesis.
- **Dev finding (exploratory): the `late_cancel = false` ablation is currently vacuous.** On a 30-seed dev run of `configs/secondary.toml` (seeds 100000–100029), the ablation cells and their matching primary cells had identical loss and identical cancel-call counts for P4–P6 in every pair. Output of an ad hoc pandas comparison of the two `trials.csv` files, pasted verbatim: `540 pairs; cancel calls differ in 0 ; loss differs in 0`. This is the known sweep-timing limitation (late cancels start only at the last `d_nominal`); it bears on the sweep-timing item above.
## 2026-10-19 — WP3: independent checker, durable journal, crash matrix, mutation tests

- New `p4/schema.py` (TypedDicts and constants only: event log line, report, truth, journal records, verdict) and `checker/` (stdlib + `p4.schema` only). `python -m checker.check` grades a run from `events.jsonl`, `report.json` and `truth.json`, or a batch of runs (`--batch`). `p4/harness.py` gains `export_run` (writes the four files; `truth.json` is built from the `Scenario` after the run) and `check_runs` (the checker as a subprocess per batch). `experiments/run.py` is WP4; it must call `check_runs` with each run's policy.
- `p4/journal.py` is now durable when given a path (JSONL + fsync per record, index replaced by temp + fsync + atomic rename, torn tails truncated on reopen). In-memory use is unchanged.
- **Fixes CODE_AUDIT D1 (the verifier is a separate process that reads exported files), D3 (the journal is used and crash-tested), A5 (the checker asserts `revoked_actor_effects` = 0), E2 (the leakage test now covers `checker/`).**
- Runbook changes, made for restart only: `Runbook.fresh()` skips payments already attempted, and P4 skips stale follow-ups. Uninterrupted runs are unchanged: `python scripts/runbook_fingerprint.py` prints `8400 d5cc020be11f5c4cbeeef3367d2a13de8b9de31fb207b2e33dbd9c774da25996` both on `main` (3dff3a4) and on this branch.
- Model choices that affect later results. Proposed as my defaults and decided with the PI on 2026-10-19 (P3 restart and `--policy` changed; the rest kept):
  - **Crash model.** A call that has left the remediator still reaches the sink; replies to a crashed process are lost. The new instance starts on the same sink, transport, clock and journal after `restart_delay` time units (harness parameter, default 0), during which there is no remediator: calls already sent still land and captures still fire. Instant restart is therefore an explicit default, not a hidden assumption. `tests/crash_matrix/test_crash_with_restart_delay` runs every applicable cell with `restart_delay = 3` under the same assertions, for all four policies.
  - **Resume.** The restarted runbook re-plans exactly as at t = 0, skips what the journal shows was attempted, and replays journaled results through the same evidence rules and policy hooks. An intent with no journaled result: P4–P6 journal it as a timeout (`recovered: true`) and resolve it with sink evidence. **P1–P3 (ack-trust) use outbox recovery:** each unresolved journaled cancel is re-sent once, with the same key, before any new cancel, and the ACK-trust rule applies to that reply (a timeout is trusted). This applies to the ack-trust runbook class, so P1 and P2 also get it; the crash matrix covers P3. The first version (trust the unresolved cancel without re-sending) is kept only as the test fixture `lm_ack_trust_orphans`, not as a policy. After a restart, the old instance's in-flight call and a new call for the same payment can both be in flight; the one-key rule makes this safe.
  - **Checker verdict.** Integrity counters, `report_malformed` and `unsupported_evidence` fail any run. `false_closure` and `false_exposure` fail only evidence-bounded runs (P4–P6); for P0–P3 they are measurements. The policy is **mandatory** (`--policy`, the `policy` field of each batch row, the `policies` argument of `check_runs`; missing or unknown ⇒ exit 2): it is taken from the runner, never from the report, so no run can skip the P4–P6 rule by relabelling. `truth.json` carries a log anchor (length, head hash) recorded outside the log, so a re-chained log fails too.
  - **Crash-matrix fixtures (tests only, not experiment presets; dev seeds only; kept as is by the PI).** `crash_outage` = λ 0.1, L 3, r 0.5, so most runs meet an outage. The late-cancel cells use jitter uniform(15), because with uniform(6) only about 6% of runs reach a late cancel.
- Results (dev seeds only). Mutation tests: all 7 tamperings are flagged in every applicable run of the 20 valid P4–P6 runs: raw, re-chained by the forger, and re-chained with no anchor. No flag is raised on the unmutated runs (`tests/checker_mutation/`). Crash matrix: output of `.venv/bin/python scripts/crash_matrix_summary.py 25` after the PI's decisions, pasted verbatim:

    Dev seeds 25 x faults ['none', 'high', 'crash_outage'] x k [1, 3]; scenario n=8, slack=tight, jitter=uniform(6); late_cancel cells jitter=uniform(15); restart_delay=0.
    fc_crash / fc_no_crash: false closures summed over crashed runs, with the crash and for the same runs without it. runs_fc_up: crashed runs with more false closures than without.

    | cell | policy | runs | crashed | checker_not_ok | dup_effects | fc_crash | fc_no_crash | runs_fc_up |
    |---|---|---|---|---|---|---|---|---|
    | a | lm_ack | 150 | 150 | 0 | 0 | 45 | 45 | 0 |
    | a | lm_verify | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | a | lm_reconcile | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | a | adaptive | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | a | lm_ack_trust_orphans | 150 | 150 | 0 | 0 | 170 | 45 | 126 |
    | b | lm_ack | 150 | 150 | 0 | 0 | 36 | 45 | 4 |
    | b | lm_verify | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | b | lm_reconcile | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | b | adaptive | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | b | lm_ack_trust_orphans | 150 | 150 | 0 | 0 | 76 | 45 | 41 |
    | c | lm_ack | 150 | 150 | 0 | 0 | 36 | 45 | 12 |
    | c | lm_verify | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | c | lm_reconcile | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | c | adaptive | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | c | lm_ack_trust_orphans | 150 | 150 | 0 | 0 | 110 | 45 | 56 |
    | d | lm_ack | 150 | 150 | 0 | 0 | 43 | 45 | 3 |
    | d | lm_verify | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | d | lm_reconcile | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | d | adaptive | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | d | lm_ack_trust_orphans | 150 | 150 | 0 | 0 | 80 | 45 | 28 |
    | e | lm_ack | 150 | 30 | 0 | 0 | 20 | 35 | 1 |
    | e | lm_verify | 150 | 54 | 0 | 0 | 0 | 0 | 0 |
    | e | lm_reconcile | 150 | 54 | 0 | 0 | 0 | 0 | 0 |
    | e | adaptive | 150 | 54 | 0 | 0 | 0 | 0 | 0 |
    | e | lm_ack_trust_orphans | 150 | 30 | 0 | 0 | 39 | 35 | 4 |
    | sweep_read_a | lm_verify | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_a | lm_reconcile | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_a | adaptive | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_b | lm_verify | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_b | lm_reconcile | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_b | adaptive | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_c | lm_verify | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_c | lm_reconcile | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_c | adaptive | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_d | lm_verify | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_d | lm_reconcile | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_d | adaptive | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_a | lm_verify | 150 | 35 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_a | lm_reconcile | 150 | 31 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_a | adaptive | 150 | 33 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_b | lm_verify | 150 | 35 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_b | lm_reconcile | 150 | 31 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_b | adaptive | 150 | 33 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_c | lm_verify | 150 | 35 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_c | lm_reconcile | 150 | 31 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_c | adaptive | 150 | 33 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_d | lm_verify | 150 | 35 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_d | lm_reconcile | 150 | 31 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_d | adaptive | 150 | 33 | 0 | 0 | 0 | 0 | 0 |

  With outbox recovery, P3 (`lm_ack`) sums to as many false closures with a crash as without at (a), and fewer at (b), (c), (d) and (e). The re-send is a second attempt with a fresh fault draw, so it sometimes settles a call that timed out in the uncrashed run. A few runs still go up (`runs_fc_up` 4, 12, 3, 1 at b–e): there the re-send itself times out under a fault or outage and is trusted. The `lm_ack_trust_orphans` rows are the superseded rule run as a fixture; they reproduce the superseded table's P3 numbers exactly.

  **Superseded** (first WP3 run, 2026-10-19, before the PI's decisions: P3 trusted an unresolved journaled cancel as a timeout instead of re-sending it; same command; its header predates the restart_delay field):

    Dev seeds 25 x faults ['none', 'high', 'crash_outage'] x k [1, 3]; scenario n=8, slack=tight, jitter=uniform(6); late_cancel cells jitter=uniform(15).
    fc_crash / fc_no_crash: false closures summed over crashed runs, with the crash and for the same runs without it. runs_fc_up: crashed runs with more false closures than without.

    | cell | policy | runs | crashed | checker_not_ok | dup_effects | fc_crash | fc_no_crash | runs_fc_up |
    |---|---|---|---|---|---|---|---|---|
    | a | lm_ack | 150 | 150 | 0 | 0 | 170 | 45 | 126 |
    | a | lm_verify | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | a | lm_reconcile | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | a | adaptive | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | b | lm_ack | 150 | 150 | 0 | 0 | 76 | 45 | 41 |
    | b | lm_verify | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | b | lm_reconcile | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | b | adaptive | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | c | lm_ack | 150 | 150 | 0 | 0 | 110 | 45 | 56 |
    | c | lm_verify | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | c | lm_reconcile | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | c | adaptive | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | d | lm_ack | 150 | 150 | 0 | 0 | 80 | 45 | 28 |
    | d | lm_verify | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | d | lm_reconcile | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | d | adaptive | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | e | lm_ack | 150 | 30 | 0 | 0 | 39 | 35 | 4 |
    | e | lm_verify | 150 | 54 | 0 | 0 | 0 | 0 | 0 |
    | e | lm_reconcile | 150 | 54 | 0 | 0 | 0 | 0 | 0 |
    | e | adaptive | 150 | 54 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_a | lm_verify | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_a | lm_reconcile | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_a | adaptive | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_b | lm_verify | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_b | lm_reconcile | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_b | adaptive | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_c | lm_verify | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_c | lm_reconcile | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_c | adaptive | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_d | lm_verify | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_d | lm_reconcile | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | sweep_read_d | adaptive | 150 | 150 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_a | lm_verify | 150 | 35 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_a | lm_reconcile | 150 | 31 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_a | adaptive | 150 | 33 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_b | lm_verify | 150 | 35 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_b | lm_reconcile | 150 | 31 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_b | adaptive | 150 | 33 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_c | lm_verify | 150 | 35 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_c | lm_reconcile | 150 | 31 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_c | adaptive | 150 | 33 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_d | lm_verify | 150 | 35 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_d | lm_reconcile | 150 | 31 | 0 | 0 | 0 | 0 | 0 |
    | late_cancel_d | adaptive | 150 | 33 | 0 | 0 | 0 | 0 | 0 |

  Superseded note: The WP3 spec expected P3 false closures for crash points b/c. They occur there, and also at a (intent journaled, cancel never sent, then trusted) and d (with k > 1, the other workers' in-flight replies are lost). In b/c the cancel usually lands, but its lost reply could have said TOO_LATE_CAPTURED under capture jitter, or the call could have been dropped by a fault.

## 2026-10-19 — WP2 follow-up: `late_cancel` flag (P4–P6)

- Decided with the PI: P4–P6 may cancel after `d_nominal` when a status read still says PENDING and the payment is permitted with gain > 0 (a late capture may still be preventable). Controlled by `late_cancel`, default **true**, applied identically to P4, P5 and P6 (`make_runbook(..., late_cancel=)`, `run_episode(..., late_cancel=)`); P0–P3 ignore it.
- Mechanism: in the accounting sweep after the last nominal deadline, a PENDING read is followed by a cancel with the payment's one key; an unresolved late cancel is read again within the sweep's retry budget. The evidence contract is unchanged: CLOSED_CANCELLED still needs a sink reply/read of CANCELLED.
- Permission is required in addition to gain > 0 (an unpermitted cancel can only be DENIED).
- Known limitation, not changed: late cancels start only when the sweep starts (the last `d_nominal` across all payments), so a payment with an earlier `d_nominal` and a short late window is usually captured before it is read.
- Ablation: `late_cancel = false` in the secondary analysis (see the list above).

## 2026-10-19 — WP2: schedulers, runbooks P0–P6, disposition contract

- New `p4/sched` (EDF, greedy, weighted Moore–Hodgson, Lawler–Moore, subset DP as test oracle) and `p4/runbooks` (P0–P6), `p4/contract.py`, an in-memory `p4/journal.py` (WP3 makes it durable), and `p4/harness.py` (one policy × one seed; WP4 builds on it). `p4/sim/protocol.py` gains an additive `AsyncProcessor` protocol (`submit_cancel` / `submit_status`, already implemented by `FaultyTransport`) so k workers can run in parallel.
- **Fixes CODE_AUDIT B1 (LM is the baseline), B2 (MH "competent manual"), B3 (closed-loop runbooks), E2 (static leakage test with an import allowlist), E3 (fairness test).**
- Model choices that affect later results. Decided with the PI on 2026-10-19 unless marked otherwise:
  - **LM for k > 1:** k = 1 LM selection on the earliest-free worker, then an EDD fill pass (add each remaining job if the EDD list schedule on k workers stays feasible). Exact only for k = 1; for k > 1 it is a heuristic, and the paper must say so.
  - **P4 on a PENDING read:** re-send the cancel with the same key if it can still finish before `d_nominal`, then verify again.
  - **P0:** ack-trust with an empty plan (every payment CLOSED_EXPOSURE after d, no evidence).
  - **MH fill pass (P2 changed from the WP2 spec rule; it makes the baseline stronger, never weaker).** The spec rule (EDD; while infeasible drop the smallest gain/s) can drop a job that does not by itself restore feasibility, and that room is then wasted. Weighted MH now ends with the same EDD fill pass as k > 1 LM. Evidence, output of `.venv/bin/python scripts/mh_fill_evidence.py` pasted verbatim (dev seeds only):

    Dev seeds 100000-100999, slack in ('tight', 'normal'), n = 8, c = 0.5, k = 1 (2000 instances). MH expected gain >= EDF:
    - spec drop rule only: 1839/2000 = 92.0%
    - drop rule + fill pass: 1921/2000 = 96.0%

    Example: seed 100016, slack tight. Candidates in EDD order (gain, s, d, gain/s):

    | uid | gain | s | d | gain/s |
    |---|---|---|---|---|
    | P003 | 69.0 | 2 | 4 | 34.5 |
    | P001 | 582.4 | 2 | 5 | 291.2 |
    | P002 | 86.8 | 3 | 7 | 28.9 |
    | P004 | 948.1 | 2 | 7 | 474.1 |
    | P006 | 328.0 | 3 | 7 | 109.3 |
    | P005 | 229.3 | 3 | 10 | 76.5 |
    | P007 | 601.0 | 3 | 10 | 200.3 |

    - EDF: P003, P001, P004, P007 (expected gain 2200.5)
    - MH, drop rule only: P001, P004, P007 (expected gain 2131.5)
    - MH, drop rule + fill: P003, P001, P004, P007 (expected gain 2200.5)

  - Not decided with the PI (my defaults, open for review):
    - Evidence-bounded runbooks (P4–P6) end with an accounting sweep after the last `d_nominal`: one status read per open payment, retried up to 3 times on timeout. Without it the contract can never report CLOSED_EXPOSURE for unplanned payments, and H4 cannot hold.
    - Every runbook skips a planned cancel that can no longer finish before `d_nominal` (never triggers for ack-trust, whose timing is deterministic).
    - P6's ρ̂ uses a Beta(1, 1) prior. A replay shows the original cancel applied iff its effect line precedes the replay's own log line (`effect_seq < seq`). The prior is a design parameter to fix on dev seeds before the prereg freeze.
    - P5 starts replays once every planned cancel has been sent, earliest `d_nominal` first.

## 2026-10-19 — WP1: event-sourced sink, clock, scenarios, fault layer

- New `p4.sim` replaces the legacy sink for all new work. `research_code/` is untouched.
- **Fixes CODE_AUDIT A1–A4, A5 (metric), A6 (jitter), D2.**
  - The sink owns an integer discrete-event clock (A3).
  - Authorization is checked before idempotency, and denials are not cached (A2).
  - Keys are scoped to `(actor, key)` and bound to `(uid, "cancel")`; a mismatched reuse is an error with no effect (A1).
  - The log is append-only and SHA-256-chained (A4).
  - `denied_calls` and `revoked_actor_effects` replace `authority_violations` (A5).
  - Capture jitter makes `D_actual` hidden (A6).
  - A transport fault layer produces real lost ACKs (D2).
- The WP0 strict-xfail tests for A1, A2, A3 and A5 now target `p4.sim` and pass (`tests/unit/test_audit_regressions.py`). Legacy defects remain documented in CODE_AUDIT and `make audit`.
- Model choices that affect later results (REVAMP_PLAN §2 left these open):
  - Time is integer. A cancel completing at `t == D_actual` is too late (strict, as in legacy).
  - Jitter is a discrete uniform.
  - Scenario sub-streams keep observations identical across jitter and score models for a seed.
  - Fault draws are keyed by `(seed, op, sha256(uid), attempt-per-(op, uid))`. The spec's Python `hash(uid)` is salted per process and would break determinism.
- **Placeholder — must be fixed on dev seeds before the prereg freeze on Oct 27 (2026-10-27).** The outage parameters in `configs/faults.toml` are unjustified placeholders chosen in WP1. They are not calibrated and not derived from any source:

  | Preset | λ (`outage_rate`) | L (`outage_length`) | r (`outage_commit_prob`) |
  |---|---|---|---|
  | `low` | 0.005 | 2 | 0.5 |
  | `high` | 0.02 | 4 | 0.5 |

  Set them on dev seeds only (WP4 / Gate G1), record the final values and rationale here, and freeze them in `docs/PREREG.md`. Never tune them on holdout. (`f_req`/`f_resp` come from the WP1 spec.)

## 2026-10-18 — WP0: repo hygiene and failing regression tests

- Added the `p4` package skeleton (`pyproject.toml`, extras `[dev]` and `[stripe]`), pytest config with `slow` and `stripe` markers, and a `Makefile` (`test`, `test-legacy`, `audit`, `lint`).
- `tests/legacy/test_legacy_suite.py` runs `research_code/test_lab.py` through unittest discovery and requires all 10 tests to run and pass (guards CODE_AUDIT E1).
- `tests/legacy/test_audit_defects.py` adds strict-xfail tests for A1, A2, A3 and A5 that state the correct behaviour against legacy `p4lab`. WP1 re-targets them at `p4.sim`.
- `tests/legacy/test_scheduling_equivalence.py` checks that Lawler–Moore matches the legacy subset DP objective on dev seeds 100000–100299 for 4 configs (records B1). This is a sanity check, not a paper result.
- Marked the legacy results as exploratory and not cited (`research_code/results/README_EXPLORATORY.md`), because the pilot and main seeds overlap (C2). No result files were changed or deleted.
- Added `.gitignore` and `scripts/check_secrets.py` (Stripe key scan, run by `make lint`).
