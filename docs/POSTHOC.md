# Post-hoc analyses (exploratory; written before any of them was run)

**Status.** These analyses are **post hoc and exploratory**. They come from the post-holdout results review of 2026-10-24 (a fresh-context reviewer session). They are not pre-registered. They change no frozen file (`docs/PREREG.md`, the files pinned in its §1), no holdout result and no verdict in `paper/generated/verdicts.md`. The paper may report them only in a section or appendix labelled "post hoc, exploratory". No confirmatory claim rests on them.

**Written first.** This file was committed on branch `posthoc` before any code for PH2 or PH3 existed and before any run. Every design choice below (policy rules, retry bound, seeds, contrasts, metrics, interpretation) is fixed here. If something must change after a run, the change and its reason go in a dated "Amendments" section at the end, and the original text stays.

**Seeds.**
- PH2 and PH3 use **dev seeds 110000–111999** (2,000 per cell, the same count as the holdout). No earlier run used this range: the changelog's dev runs used 100000–101499.
- **No holdout seed is run.** PH5 and PH6 only *read* the existing holdout files (`results/holdout/`, read-only) to describe them. Nothing is tuned on them.

**Statistics (PH2, PH3).**
- Paired per-seed differences within a cell.
- Percentile bootstrap of the mean (`analysis/stats.py`, `N_BOOT` = 10,000, seeded from `BOOT_SEED` and the contrast label).
- Unadjusted 95% CIs, plus win/tie/loss counts.
- No multiplicity correction and no decision rule. The CIs are descriptive. The interpretation tables below say what a pattern would *suggest*, not what it would prove.

**Code changes these analyses need, and why they leave the frozen results unchanged.**
- PH2 adds a policy (`lm_ack_retry`) to the registries.
- PH3 adds a transport parameter (`timeout_factor`, default 1). With 1, the code path is the existing one.
- Neither changes cell ids, the fault draws, the presets or any existing policy.
- The golden test (`tests/golden`) and the frozen-file test (`tests/unit/test_prereg_frozen.py`) must still pass byte for byte.

## PH1. Threats to validity (documentation)

Add these to "Threats to validity" in `docs/CHANGELOG_RESEARCH.md`:
- the timeout-timing assumption;
- stale or reordered reads are not modelled;
- P3 has no late cancel and no sweep;
- H4 is a weak control;
- the live tests injected dropped responses only.

No run.

## PH2. At-least-once ACK-trust baseline (P3b, `lm_ack_retry`)

**Question.** Is P4's and P5's lower loss than P3 under faults (e.g. k = 4, n = 8) explained by *retrying* a timed-out cancel, or by *evidence-bounded* disposition? P3 never retries: it trusts the first timeout.

**Policy P3b `lm_ack_retry`.** P3 with at-least-once retries.
- **Plan.** The same Lawler–Moore plan as P3, the same list scheduling over k workers, and the same feasibility rule (`now + s_cancel < d_nominal`) for fresh cancels.
- **Retry.** After a cancel times out, re-send it with the **same idempotency key** (a replay) if all of these hold:
  - it has been retried fewer than **MAX_RETRIES = 3** times (at most 4 sends per payment; 3 matches `SWEEP_RETRIES`);
  - the retry can still finish before `d_nominal` (`now + s_cancel < d_nominal`).
- **Priority.** Retries come before the next fresh cancel, as with a client library that retries in place.
- **Trust any definitive reply.** CANCELLED or ALREADY_CANCELLED gives CLOSED_CANCELLED. TOO_LATE_CAPTURED gives CLOSED_EXPOSURE.
- **Trust the final timeout.** When no retry is allowed, the last timeout is reported CLOSED_CANCELLED (basis `timeout_trusted`), as P3 does.
- **Otherwise as P3.**
  - Unplanned payments are CLOSED_EXPOSURE at their nominal deadline.
  - There are no status reads, no sweep and no late cancel.
  - Restart: outbox recovery as P3.
- P3b is ack-trust (`evidence_bounded = False`), so its false closures are measurements, as for P0–P3.
- P3b is not part of the crash matrix (`experiments/crash_matrix.py` is frozen).

**Design.**
- `configs/posthoc/primary_p3b.toml`: the 24 primary cells (the same factors as `configs/primary.toml`).
- Policies: `lm_ack` (P3), `lm_ack_retry` (P3b), `lm_reconcile` (P5).
- Seeds 110000–111999.
- Output: `results/dev/posthoc-p3b/`.

**Metrics.** Loss (primary), false closures per cell and policy, cancel calls, replays.

**Contrasts.** P3b − P3 and P5 − P3b, per cell, on loss.

**What results would mean.**

| pattern (faulty cells) | suggests |
|---|---|
| P3b − P3 < 0 where P5 − P3 < 0, and P5 − P3b ≈ 0 (CI covers 0) | P5's loss advantage over P3 comes from retrying, not from evidence-bounded disposition. Evidence-bounded runbooks are then distinguished by false-closure prevention, not by loss. The paper must say so. |
| P5 − P3b < 0 (CI below 0) | P5 reduces loss beyond plain retry. Candidate causes: retries after the plan instead of in place, the sweep, late cancels. PH2 can't separate them. |
| P5 − P3b > 0 (CI above 0) | Plain in-place retry beats P5 on loss. Assurance then has a loss cost relative to a retrying ACK-trust runbook, even where it has none relative to P3. |
| P3b − P3 > 0 at k = 1 | In-place retries push out fresh cancels, as P4's verification does (H2). |
| P3b false closures ≪ P3's but > 0 | Retries remove most of P3's false closures. RQ2's "ACK-trust closes falsely" should be stated per runbook, with P3b as the stronger ACK-trust variant. |
| P3b false closures ≈ P3's | Retrying doesn't fix the trusted final timeout, so false closure is about trust, not retry. |

## PH3. Timeout-multiplier sweep

**Question.** The simulator delivers a timeout at `t_sent + latency`, as fast as a reply. How much do P4's and P5's contrasts with P3 depend on that?

**Change.** A transport parameter `timeout_factor` m (a positive integer, default 1).
- A call's effect on the sink, and the fault draw, happen exactly as now, at `t_sent + latency`.
- A **successful reply** is delivered at `t_sent + latency`, as now.
- A **timeout**, of any kind (`drop_req`, `drop_resp`, `outage_lost`, `outage_commit`), is delivered to the caller at `t_sent + m · latency`. The worker stays busy until then.
- m = 1 is the existing behaviour, byte for byte.
- m applies to cancels, replays and status reads alike. It is the same for every policy in a run, and it goes into the inputs fingerprint when m ≠ 1.

**Design.**
- `configs/posthoc/primary_timeout.toml`: the 24 primary cells.
- Policies: `lm_ack` (P3), `lm_verify` (P4), `lm_reconcile` (P5).
- Seeds 110000–111999.
- m ∈ {1, 2, 4}: one run each, `results/dev/posthoc-timeout-m{1,2,4}/`, via a dev-only runner flag `--timeout-factor` (refused for holdout).

**Metrics.** Loss, false closures, cancel/status calls.

**Contrasts.**
- P4 − P3 and P5 − P3, per cell, at each m.
- The interaction, paired by seed across runs: (P5 − P3)@m − (P5 − P3)@1, and the same for P4 − P3, for m ∈ {2, 4}.
- Faultless cells are expected to be identical across m, because no timeout ever happens there. This is a check on the implementation.

**What results would mean.**

| pattern | suggests |
|---|---|
| P5 − P3 stays < 0 (CI below 0) at m = 4 in the faulty k = 4 cells | P5's advantage doesn't rest on cheap timeouts. |
| P5 − P3 moves toward 0 or flips sign as m grows | P5's advantage depends on cheap timeouts. The paper must state the timeout cost as a boundary condition. |
| P4 − P3 at k = 4, n = 8 flips from < 0 to > 0 as m grows | P4's advantage there depends on cheap timeouts. |
| P4 − P3 at tight / k1 / n32 (H2's cells) grows with m | Expected: verification costs more when waiting is costlier. H2 is conservative at m = 1. |
| P4 − P3 at H2's cells shrinks with m | Unexpected. Inspect traces before interpreting. |
| Faultless cells differ across m | An implementation bug. Fix it and rerun all three m. |

## PH4. Static test: runbooks don't read clock internals

**Question.** The leakage test does not ban the shared event clock's queue. That queue holds the sink's scheduled captures, so `clock.next_time()`, `clock.pending()` or `clock._queue` would reveal capture times.

**Design.**
- An AST test over `p4/runbooks/*.py`. It fails if any of `next_time`, `pending`, `_queue` or `_seq` is accessed as an attribute, or named.
- The only exception is the one engine-loop call in `Runbook.run` (`p4/runbooks/base.py`). The test checks that exactly that single call exists.
- A self-test plants each violation and checks that the walker finds it.

**Interpretation.** This is post-hoc verification, not a result. If it fails on today's code, a runbook can read capture times. That would be a leakage bug, handled through the PREREG §8 deviation procedure.

## PH5. P4-vs-P3 attribution and per-seed split (read-only on holdout)

**Question.** Reproduce, with committed scripts (CLAUDE.md rule 4), the reviewer's two tables explaining P4 < P3 at k = 4, n = 8 under faults.

**Design.** `analysis/posthoc/p4_vs_p3.py`, reading `results/holdout/primary` (`trials.csv` and `runs/*.tar.gz`, read-only), for every faulty primary cell (fault ∈ {low, high}).

1. **Per-seed split.** The P4 − P3 loss difference in seeds where P3 had **no** false closure, and in seeds where it had **at least one**. For each: seed count, mean, and 95% bootstrap CI.
2. **Attribution.** For every payment P3 reported CLOSED_CANCELLED although the log's final state is not CANCELLED (the checker's false-closure definition), classify P4's outcome for that payment, and sum the per-payment loss difference (P4 − P3, using the scenario's c) per class, per seed:
   - **recovered by re-send:** P4 cancelled it before `d_nominal` after 2 or more cancel sends;
   - **recovered on the first send:** P4 cancelled it before `d_nominal` with one send (different timing);
   - **recovered late:** P4 cancelled it at or after `d_nominal`;
   - **lost in both:** captured under P4 too.

   "Everything else" is the total P4 − P3 minus the sum over these payments. It covers capacity cost and late cancels on other payments.

**Outputs.** `paper/generated/posthoc/p4_vs_p3_split.csv` and `p4_vs_p3_attribution.csv`.

**Interpretation.** Descriptive only.
- If "recovered by re-send" accounts for about all of P4 − P3 at k = 4, n = 8, and the no-false-closure split is about 0, the reviewer's explanation (recovering lost cancels with idle workers) is confirmed.
- If not, the paper may not use that explanation.

## PH6. Bonferroni-level CIs in the contrast report

**Change.**
- `stats.contrast_table` adds `bonf_ci_lo` and `bonf_ci_hi` to Holm-family tables: the percentile CI at level 1 − α/m, with m the family size (48 for the primary family).
- `analysis/report.py` writes them into `contrasts_primary.csv`, and the `.tex` table gets a column.

**What stays the same.** These are added information only.
- The H2 rule still uses `holm_ci_lo` (PREREG §4), so `verdicts.json` and `verdicts.md` must be byte-identical before and after.
- Regenerating `paper/generated/` from `results/holdout` should change only the contrast table files.

## PH7. Multi-crash resumption (written 2026-10-24, before any PH7 code or run)

The user called this "PH4". It is numbered PH7 because PH4 (the clock-internals test) is already referenced in commits, tests and the changelog.

**Question.** H5 crashes the remediator at most once per episode. Do P4–P6 still give no false closure, no false exposure, no duplicate effect and no failed invariant when it crashes **twice**? This includes two crashes on the same in-flight payment, the case that breaks the frozen P3's outbox recovery (PH1 threats list). P3b is included as the exploratory ack-trust comparison.

**What doesn't change.** Nothing frozen is modified. `experiments/crash_matrix.py` is pinned by SHA-256, so PH7 imports its cells, scenarios, fault settings, k values, c and grading function without editing the file. P3 is neither changed nor run.

**Design.** A new runner, `experiments/posthoc_multicrash.py`.
- **Cells.** The 13 crash-matrix cells for P4, P5 and P6 (`lm_verify`, `lm_reconcile`, `adaptive`) and the 5 non-sweep cells for P3b (`lm_ack_retry`), via `crash_matrix.applies`.
- **Settings.** Each cell's first crash is exactly the crash-matrix hook (`CrashAt`: the cell's point and target). Faults {none, high, crash_outage}, k ∈ {1, 3}, `restart_delay` 0, journal not fsynced, `max_restarts` 3: all as in the crash matrix.
- **Variant `same`.** After the restart, a second crash at the **same crash point**, on the restarted instance's **first call for the same payment** as the first crash. For cell e, the first crash point reached by that call. This is the two-crashes-on-one-in-flight-payment case.
- **Variant `different`.** After the restart, a second crash on the restarted instance's **first call for any payment**, at the crash point that **follows** the first crash's point cyclically (before_send → after_send → after_reply → after_journal → before_send).
- **What counts as a second crash.** A second crash fires only in the restarted instance, so each run has at most two crashes. Runs where the first or second crash point is never reached still finish, are graded, and are counted by how many crashes actually happened.
- **Seeds.** Dev seeds **112000–112499** (500), unused so far.

**Metrics,** per (variant, cell, policy):
- runs, runs crashed once, runs crashed twice;
- false closures, false exposures, duplicate effects;
- failed invariants: integrity problems, malformed report, unsupported evidence, report validation errors, or an exception escaping the episode (for example a runbook assertion such as "second call in flight"). An escaped exception is recorded as a failed run, not dropped.

**Reading.**
- **P4–P6.** A (variant, cell, policy) pair **holds** if it has 0 false closures, 0 false exposures, 0 duplicate effects and 0 failed invariants over all its runs, **and** at least **100** runs crashed twice. With fewer double crashes it is "too few double crashes", not "holds".
- **Stop rule.** If any P4–P6 run fails (a false closure, false exposure, duplicate effect, failed invariant or escaped exception), stop. Report the failing runs and fix nothing in this round.
- **P3b.** False closures are measurements, as in H5. Failed invariants are reported and not fixed in this round.
- **No upgrade to H5.** PH7 is exploratory and cannot upgrade H5. If everything holds, the paper may say that multi-crash resumption was checked exploratorily (PH7), with no failures, under these settings.

**Outputs.**
- Raw rows: `results/dev/posthoc-multicrash/crash_runs.csv` and `manifest.json`.
- Table: `paper/generated/posthoc/multicrash.csv`, from `analysis/posthoc/multicrash.py`.

## PH8. The k > 1 scheduler against P2 (read-only on holdout; written 2026-10-25, before any PH8 code)

**Origin.** The G2 results review (2026-10-25, fresh-context session) saw in `results/holdout/primary/trials.csv` that P2 `mh` has a much lower mean loss than P3 `lm_ack` at k = 4, n = 32, at every fault level. So this plan is written *after* the pattern was seen. It only describes it, with committed code (CLAUDE.md rule 4). It tests nothing and decides nothing.

**Question.** For k > 1, P3–P6 share one scheduler: the k = 1 Lawler–Moore selection on the earliest-free worker, then an EDD fill pass (`p4/sched/lawler_moore.py`, a documented heuristic for k > 1). How much loss does it give up against P2's weighted Moore–Hodgson selection? Does that change which runbook has the lowest loss?

**Design.** `analysis/posthoc/scheduler_k.py`, reading `results/holdout/primary/trials.csv` read-only, for all 24 primary cells.
- **`mh − lm_ack`.** Both trust ACKs, so with fault = none the execution is the same and the difference is the scheduler alone. With faults it also includes how each plan meets the faults.
- **`mh − lm_reconcile`.** ACK-trust MH against P5. P5 is the evidence-bounded runbook that is never worse than P3 in the Holm family.
- **Per cell:** paired per-seed differences, mean, unadjusted 95% percentile bootstrap CI (`N_BOOT`, seeded from `BOOT_SEED` and the label `posthoc|<cell_id>|<a>-<b>`), and a-better / ties / a-worse counts. No multiplicity correction and no decision rule.

**What doesn't change.** No run, no policy, no frozen file and no verdict. P2 and P3 stay as they are (frozen baselines). An MH-based evidence-bounded runbook would be a new exploratory policy on dev seeds and a separate PH. It is not part of PH8.

**Reading.**
- If `mh − lm_ack` is below 0 at k = 4 with fault = none, the gap comes from the k > 1 heuristic, not from ACK trust. The paper must then:
  - say that LM is exact only at k = 1;
  - limit "strongest scheduling baseline" to k = 1;
  - report that at k > 1 an ACK-trust heuristic can have lower loss than every evidence-bounded runbook in the study.
- The comparisons P4/P5/P6 − P3 stay fair, because all four share the scheduler.

**Output.** `paper/generated/posthoc/scheduler_k.csv`.

## Findings to carry into the paper (recorded 2026-10-24; not yet written into the paper)

### PH3 and H2: the cost of assurance depends on how long a timeout takes

- **Finding (exploratory, dev seeds 110000–111999).** P4 − P3 in H2's cells (tight slack, k = 1, n = 32, low and high faults) shrinks as the timeout factor m grows from 1 to 2 to 4. At m = 4 with low faults its 95% CI covers 0. This was listed as "unexpected" in PH3's table above. The prediction was that H2 would be conservative at m = 1, and the opposite holds.
- **Mechanism (from trial-level means; PH3 kept no traces).** A longer timeout also blocks P3's own workers. P3 waits out every timed-out cancel before its next one, so P3 sends fewer cancels and its loss rises with m. P4's loss rises less, so the two converge. P5 − P3 shrinks toward 0 for the same reason, but stays below 0 at m = 4 in all four k = 4, n = 8 cells with faults.
- **Caveat for the H2 discussion.**
  - H2 is confirmed on the holdout (PREREG §4) only under the simulator's timing assumption: a timeout returns as fast as a reply (CHANGELOG threats list, "Timeouts are as fast as replies").
  - The *size* of the cost of assurance that H2 measures is largest under that assumption. It falls when timeouts are slower, because ACK-trust then pays for its own timeouts too.
  - The paper must present this as a boundary condition on H2. It must not be presented as a change to H2's verdict, which stands, or as evidence that assurance is free.
- **Where the numbers come from** (rule 4: no numbers are typed here).
  - `paper/generated/posthoc/timeout_contrasts.csv`: P4 − P3 and P5 − P3 per cell and m.
  - `timeout_interaction.csv`: the change from m = 1.
  - `timeout_policy_means.csv`: per-policy loss and calls by m.
  - Generated by `analysis/posthoc/timeout_sweep.py` from `results/dev/posthoc-timeout-clean-m{1,2,4}`.

### PH8: at k > 1 the shared scheduler, not assurance, sets the loss level (recorded 2026-10-25)

- **Finding (descriptive, holdout primary, read-only).** At k = 4, n = 32, P2 `mh` has lower loss than P3 `lm_ack` with no faults, and lower than P5 at every fault level. At k = 4, n = 8 the gap to P3 is about 0. So the k > 1 Lawler–Moore heuristic (k = 1 selection + EDD fill) gives up loss that an MH selection keeps.
- **For the paper.** Say Lawler–Moore is exact only at k = 1. Present the P4/P5/P6 − P3 contrasts as a comparison of evidence policies under one shared scheduler, not against the best available scheduler. Put this in threats to validity.
- **Where the numbers come from.** `paper/generated/posthoc/scheduler_k.csv`, from `analysis/posthoc/scheduler_k.py --results results/holdout/primary`.
