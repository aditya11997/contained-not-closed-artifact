# Pre-registration (frozen; git tag `prereg-v1`)

This file is frozen at the commit tagged `prereg-v1` (CLAUDE.md rule 7). The holdout runners refuse to run unless it is byte-identical to its tagged version (`experiments/guard.py`). It supersedes `docs/PREREG_DRAFT.md`. Everything here was decided on dev seeds 100000–199999 only. No holdout seed has been used.

## 1. Identity

- Researcher: Aditya Krishnan.
- Freeze date: 2026-10-23.
- Frozen code: the commit tagged `prereg-v1`. That covers the simulator (`p4/`), the checker (`checker/`), the runners (`experiments/`) and the analysis (`analysis/`). A file cannot name the commit that contains it, so the tag is the reference; every holdout `manifest.json` records `prereg_tag_commit`.
- The files that define the experiments are pinned by SHA-256 at that commit (`tests/unit/test_prereg_frozen.py` checks them):

| file | sha256 |
|---|---|
| `configs/faults.toml` | `c942377f6886e997a1a9bdda3c2502badff05398e105c3809497f0d8a2f832cc` |
| `configs/presets.toml` | `a4f899974155c9c7775895552c5d6b935e0b36ff2a21db71cff1b4f12c221f02` |
| `configs/scenario_default.toml` | `f3807eb5fd9ca19a9924c8fb0ed7492ce768e3687faeff0284a48fbeefa84b4a` |
| `configs/primary.toml` | `dd826f20a3512e1303028cf4c71eb8f1d0e370c3b2a0272ec41586b7be97b39d` |
| `configs/secondary.toml` | `015f81b91290364701bd48d16bd4eec31e2aaf1794e301bcc62d408e565266f9` |
| `configs/negative_control.toml` | `72eeb33afac75f20cb65c35182b4123c0dbd6e2790851ff9f2a55ab307913c21` |
| `configs/scaling.toml` | `8b43ab2e92fea697d7b38042ae802ed56f131596134fb26c99f0e624fb5d898b` |
| `experiments/crash_matrix.py` | `9bd29e06c1afae2ae561ce3b2ac3e25e8ae4163f0f73f0591869ff9755d71e5d` |
| `analysis/stripe_mapping.py` | `3becdf896b8c95b5f4f89140b06c1c490589d5333aba3fd928254d213fd37c1d` |

`experiments/crash_matrix.py` is listed because it holds the H5 settings (cells, scenario, faults, k). `analysis/stripe_mapping.py` is listed because it holds C1's row-to-test mapping (`TABLE_ROWS`) and the transient/complete rule (§5.5). The only change to `configs/primary.toml` since the dev runs is its header comment, which now names the current Holm family. Cell ids depend on the factor values, not on the file, so dev and holdout cell ids match.

## 2. Research questions

- **RQ1:** After perfect revocation, how much pre-revocation payment exposure remains? How much of it can a separate cancel-only authority prevent before capture?
- **RQ2:** How often do runbooks that treat acknowledgments as completion report false closures under transport faults?
- **RQ3:** What does evidence-bounded disposition cost in exposure under deadlines (the cost of assurance)?
- **RQ4:** Does an adaptive evidence-bounded runbook reduce that cost relative to standard reconcile-after?

"ACK ≠ completion" is the idea of Liu et al. (arXiv:2610.15991). We cite it and do not claim it. Lawler–Moore is the known scheduling baseline. Simulated capture times are not card-network deadlines.

## 3. Policies

These are the policies of `docs/BASELINES.md` at the frozen commit. The names are the ones used in configs and in `trials.csv`.

| ID | name | selection | execution |
|---|---|---|---|
| P0 | `none` | — | no calls; reports CLOSED_EXPOSURE after d (motivation only) |
| P1 | `edf` | EDF | ACK-trust |
| P2 | `mh` | weighted Moore–Hodgson + fill | ACK-trust |
| P3 | `lm_ack` | Lawler–Moore | ACK-trust |
| P4 | `lm_verify` | Lawler–Moore | verify each timeout before the next cancel |
| P5 | `lm_reconcile` | Lawler–Moore | fire all, then same-key replays (reconcile-after) |
| P6 | `adaptive` | Lawler–Moore, re-planned at each free-worker event | P5's replays and sweep |

- P4–P6 are evidence-bounded. Each ends with a single end-of-run accounting sweep (one status read per open payment, retried up to 3 times on timeout).
- `late_cancel = true` is the default.
- P6 has no free parameters. It is P5 plus per-event Lawler–Moore re-planning over the not-yet-attempted candidates, with no replay-first and no ρ̂.

## 4. Hypotheses and decision rules

All analysis uses `analysis/` at the frozen commit:

- per-seed paired differences within a cell;
- percentile bootstrap of the mean with `N_BOOT = 10,000` resamples, seeded from `BOOT_SEED = 20261027` and the contrast label;
- the bootstrap-inverted p-value;
- `ALPHA = 0.05`.

Loss is the primary outcome (§6). A contrast a − b < 0 means a has lower loss.

Every verdict below is computed from its rule by `analysis/verdicts.py`; `make results` prints them and writes `verdicts.json` and `verdicts.md`. A missing input gives "not evaluated", never support.

| H | Statement | Data | Supported iff |
|---|---|---|---|
| **H1** | ACK-trust closes falsely under faults; evidence-bounded runbooks never do | exact false-closure counts per cell and policy from the checker, with Wilson 95% intervals (`stats.false_closure_table`) | False closures are summed over all seeds per cell. P3's sum is > 0 in every faulty cell (fault ∈ {low, high}) of the primary grid, **and** the P0, P4, P5 and P6 total is 0 across the holdout runs of `primary`, `secondary`, `negative_control` and `scaling`. Crash-matrix runs are governed by H5, not H1. P1 and P2 are reported, with no claim. |
| **H2** (primary) | Assurance costs exposure: P4 loss > P3 loss at tight slack, k = 1, n = 32, under faults | contrast P4 − P3 in cells tight/k1/n32 × fault ∈ {low, high}, inside the Holm family (§5) | in **both** cells the Holm-level CI lower bound (`holm_ci_lo` in `contrasts_primary.csv`) is > 0. This bound alone decides; p-values (raw and Holm-adjusted) are reported for information only. |
| H2 (secondary, n = 8) | as H2 at n = 8 | P4 − P3 in cells tight/k1/n8 × {low, high}, taken from the same Holm table | reported only, with no claim (dev fragility, Appendix A) |
| H3 (secondary) | P6 is better than P5 | P6 − P5 per primary cell, unadjusted 95% CI (`contrasts_primary_secondary.csv`) | H3 (secondary): P6 is better than P5. At tight slack, a fault level counts if the unadjusted 95% CI upper bound of P6−P5 is below 0 in at least 3 of that level's 4 cells (k ∈ {1,4} × n ∈ {8,32}). H3 is supported if at least 2 of the 3 fault levels count; otherwise it is reported as not supported. Dev data predicts not supported. |
| **H4** (negative control) | With k = ∞, no faults and no jitter, assurance is free: P3 = P4 = P5 = P6 | `negative_control` run; per-seed loss equality across P3–P6 (`stats.negative_control_table`) | every seed in every cell has exactly equal loss for P3, P4, P5 and P6, with no tolerance, since the simulation is deterministic (`\NegCtrlSeedsEqual` = `\NegCtrlSeeds`) |
| **H5** (crash safety) | Crashing the remediator and restarting it from its journal never causes a false closure or a duplicate effect for P4–P6 | the crash matrix on holdout seeds (§5.4), graded by the checker (`analysis/crash_matrix.py`) | counted over **all** runs in every crash-matrix cell, crashed or not: for **every** (cell, policy) pair with policy ∈ {P4, P5, P6}, 0 false closures, 0 false exposures, 0 duplicate effects, 0 failed invariants (integrity problems, malformed report, unsupported evidence, report validation errors) **and** ≥ 100 crashed runs (`MIN_CRASHED`, so that a zero is not for lack of crashes). `\HfiveSupported` = 1. P3's crash rows are reported, not part of H5. |
| **C1** | Sim ≡ Stripe test mode on the mapped transitions | the official live conformance run on the tagged commit, with its retry rule (§5.5) | all mapping-table rows are matched (`\StripeMappingRowsMatched` = `\StripeMappingRows`) **and** every conformance test ran and passed (`\StripeTestsRun` = `\StripeTestsPassed` = `\StripeConformanceTests`). Both counts are reported. |

H5 decides the title: the title keeps "Safe Resumption" if and only if H5 is supported.

## 5. Design

### 5.1 Primary grid and the Holm family (`configs/primary.toml`)

- **Factors:** fault ∈ {none, low, high} × slack ∈ {tight, normal} × k ∈ {1, 4} × n ∈ {8, 32}, giving 24 cells.
- **Fixed:** score = calibrated, jitter = `uniform3` (ε ∈ {−3, …, +3}, j = 3), c = 0.5, q = 1, late_cancel = true, and a single end-of-run accounting sweep.
- Fault presets (`configs/faults.toml`, fixed on dev at G1). They are design choices used as sensitivity bands, not calibrated failure rates. λ is per time unit.

| preset | f_req | f_resp | outage_rate λ | outage_length L | outage_commit_prob r |
|---|---|---|---|---|---|
| none | 0.0 | 0.0 | 0.0 | 0 | 0.0 |
| low | 0.02 | 0.05 | 0.005 | 2 | 0.5 |
| high | 0.05 | 0.15 | 0.02 | 4 | 0.5 |

- **Scenario parameters:** `configs/scenario_default.toml`, with slack tight = 0.7 and normal = 1.0, scaling `d_nominal`.
- **Holm family:** {P5 − P3, P4 − P3} × 24 primary cells = 48 contrasts (`analysis/stats.py` `PRIMARY_CONTRASTS`), with Holm–Bonferroni step-down at α = 0.05. P5 − P3 has no named hypothesis; it answers RQ1/RQ3 and is reported in full. P6 − P5 is not in the family (§5.2).

### 5.2 Secondary analyses (reported; not in the Holm family; no confirmatory claims)

- **H3, P6 − P5 over the 24 primary cells**, with unadjusted 95% CIs (`SECONDARY_PRIMARY_GRID_CONTRASTS`). It is secondary because on dev the redesigned P6 was identical to P5 in every k = 1 cell. It differed only at k = 4, n = 32, where no CI excluded 0 and the sign of the mean difference flipped between two dev seed ranges (Appendix A). P6 still runs in every holdout cell.
- **H2 at n = 8** (§4).
- **`configs/secondary.toml`**: 54 cells. Each block varies one factor from the primary fixed values over fault × slack at k = 1, n = 32:
  - score model: `shift_up` (+0.2), `shift_down` (−0.2), `noisy1` (σ = 1 on the logit scale);
  - jitter sign: `early3` (ε ∈ {−3, …, 0});
  - collateral weight c = 1.0;
  - n = 128;
  - k = ∞ with faults (the fault-free k = ∞ case is the H4 control);
  - **late_cancel on/off at j = 6** (`uniform6`), both arms in this config. At the primary j = 3, late cancels are effectively inactive on dev (≤ 0.515% of P5 loss recoverable), so the ablation would be vacuous there. `analysis/report.py` pairs each off-cell with its on-cell.
- **Scaling** (`configs/scaling.toml`): wall time per episode vs n ∈ {8, 32, 128, 512} at fault {none, high}, normal slack, k = 4. This is not a hypothesis test.

### 5.3 Negative control (`configs/negative_control.toml`)

k = ∞, fault none, jitter none, slack {tight, normal} × n {8, 32}: 4 cells. Jitter must be none, because with late captures the equality would also depend on late-cancel timing.

### 5.4 Crash matrix (H5; `experiments/crash_matrix.py`)

- **Cells.** A crash point (a) before_send, (b) after_send, (c) after_reply or (d) after_journal, at each of these targets:
  - the first pre-deadline cancel (cells a–d);
  - the first crash point of any call during an outage (cell e);
  - the first accounting-sweep status read (`sweep_read_a`–`d`);
  - the first post-deadline cancel (`late_cancel_a`–`d`).
- **Pairs.** P3 has no sweep, so it gets 5 cells, and P4–P6 get 13 each: 44 (cell, policy) pairs.
- **Runs.** Each pair runs every seed under faults {none, high, crash_outage} × k ∈ {1, 3}.
  - The scenario is n = 8, tight slack, jitter `uniform` j = 6. The late_cancel cells use j = 15, so that runs reach a post-deadline cancel.
  - `crash_outage` = outage_rate 0.1, outage_length 3, outage_commit_prob 0.5, with no request/response drops. It makes outages frequent enough that cell e is reached.
  - c = 0.5; restart delay 0; the journal is not fsynced. The fsync path is covered by `tests/crash_matrix/`.
- **Grading.** A run whose crash point is never reached finishes uncrashed and is still graded. Every run is graded by the independent checker.

### 5.5 C1 Stripe conformance (official run)

On the tagged commit, with a clean tree and `STRIPE_TEST_KEY` set in the shell only:

```
pytest -q --run-stripe -m stripe tests/conformance
python -m analysis.stripe_mapping results/conformance/stripe-<UTC time> --tex paper/generated/stripe.tex
```

- The run is live in Stripe **test mode** and needs the explicit opt-in. The scripted transitions use fixed dev seeds by design: conformance compares backends, it does not sample scenarios.
- The script reports **both** counts, which measure different things:
  - mapping-table rows (`\StripeMappingRows`, `\StripeMappingRowsMatched`);
  - conformance tests (`\StripeConformanceTests` = `\StripeTransitionScripts` + `\StripeEpisodes`, plus `\StripeTestsRun` and `\StripeTestsPassed` from pytest's own outcomes).
- **Retries.** Transient failures (HTTP 429, HTTP 5xx, network errors) may be retried up to 2 times, so there are at most 3 attempts. The first complete run decides, and every attempt is reported.
  - "Transient" and "complete" are as coded in `analysis/stripe_mapping.py`. A run is transient if a test failed and Stripe answered 429 or 5xx, or a failure names a network error. A run with `tests.jsonl` that is not transient is complete.
  - If none of the 3 attempts is complete, C1 is not supported.
  - After an adapter fix (§8), the post-fix run decides, with the same retry rule. The pre-fix and post-fix runs are both reported.
  - `make results CONFORMANCE="<every attempt's run dir>"` applies this rule (`analysis/verdicts.py` `c1_attempts`).
- **Row-to-test mapping.** Which test results each mapping-table row rests on (`TABLE_ROWS`) is frozen as coded in `analysis/stripe_mapping.py` at the tagged commit; that file's SHA-256 is in §1.
- The earlier live runs (`stripe-20261023T033212Z`, `…033550Z`, `…033737Z`) predate the freeze and are not the C1 evidence.
- Paths not tested live are listed in `docs/CHANGELOG_RESEARCH.md` ("Threats to validity: not tested live").

### 5.6 Seeds

- **Holdout seeds 900000–901999** (2,000 per cell), with the same seeds in every cell and for every policy: `primary`, `secondary`, `negative_control` and the crash matrix.
- **Scaling** uses the first 100 of them (900000–900099), its config's `n_seeds`.
- Every policy for a seed gets the identical scenario, observations, k, authority and fault realization (`tests/fairness/`).

### 5.7 Out of scope: the WP6 agent case study

The WP6 LLM-agent case study (`agent/`, Stripe test mode and sim backends) is **descriptive**. It is outside every hypothesis above, is reported separately, and no claim in §4 depends on it.

## 6. Outcomes

- **Primary:** per-seed modeled loss = suspect captured + c · legit cancelled. The checker computes it and it is cross-checked against `p4.sim.oracle`.
- **Safety:** false_closure count (checker); duplicate effects (checker).
- **Secondary:** false_exposure, unresolved counts, cancel/status calls, replays, time to full disposition, and wall time.

## 7. Holdout commands

Run in this order on the tagged commit with a clean tree, each under `caffeinate -i`:

```
python -m experiments.run --config configs/primary.toml          --partition holdout --out results/holdout/primary
python -m experiments.run --config configs/secondary.toml        --partition holdout --out results/holdout/secondary
python -m experiments.run --config configs/negative_control.toml --partition holdout --out results/holdout/negative_control
python -m experiments.run --config configs/scaling.toml          --partition holdout --out results/holdout/scaling
python -m experiments.crash_matrix --partition holdout --n-seeds 2000 --out results/holdout/crash_matrix
make results CONFORMANCE="results/conformance/stripe-<UTC time of each C1 attempt> ..."
```

The runners use the configs' `n_seeds` (2,000; scaling 100) starting at 900000. `make results` writes every table, macro and figure, H5 included, from `results/holdout/*` only, plus the verdict of every hypothesis. C1 comes from the conformance attempts named by `CONFORMANCE`, one or more run directories (§5.5).

### Estimated holdout runtime

Output of `python scripts/estimate_holdout_runtime.py --workers 10` (dev seeds only, run on 2026-10-23 on the uncommitted freeze tree), pasted verbatim. It extrapolates linearly from two dev sizes. It runs with `--no-keep-runs`, so the holdout runs, which archive every exported run, will take somewhat longer. It is an estimate, not a result.

Machine: macOS-26.6.2-arm64-arm-64bit-Mach-O, 10 CPUs, --workers 10; dev seeds 10 and 40.

| config | holdout seeds | t(small) s | t(large) s | s per seed | estimate |
|---|---|---|---|---|---|
| primary | 2,000 | 5.8 | 21.0 | 0.51 | 17 min |
| secondary | 2,000 | 21.0 | 86.1 | 2.17 | 72 min |
| negative_control | 2,000 | 1.7 | 5.8 | 0.14 | 5 min |
| scaling | 100 | 24.4 | 100.4 | 2.54 | 4 min |
| crash_matrix | 2,000 | 12.1 | 47.8 | 1.19 | 40 min |
| **total** | | | | | **2.3 h** |

## 8. Exclusions and deviations

- **No exclusions.** No seed, cell or run is dropped. A run with a checker problem (e.g. `chain_broken > 0`), a crash or a failed invariant is reported, not dropped; it indicates a bug.
- **Blocking rule.** Any checker not-ok run in any holdout run blocks **all** verdicts until it is resolved through the deviation procedure below. The one exception is a false closure by P0–P3, which is a measurement and never makes the checker not-ok.
  - For `experiments.run` runs, a run is not-ok when the checker's `ok` is false.
  - For the crash matrix, it is `checker_ok = 0` in `crash_runs.csv`.
  - `analysis/verdicts.py` `gate` reports every verdict as "blocked" while any such run exists, except as below.
- **Contract violations are reported, not only blocked.** If any P4–P6 run has a false closure, a false exposure or a duplicate effect, the verdicts for that holdout run are still computed and reported as "not supported (pre-fix)", alongside the post-fix result after the deviation procedure.
  - That run's hypothesis is H1 for the `primary`, `secondary`, `negative_control` and `scaling` runs, and H5 for the crash matrix.
  - The other verdicts of the same report are labelled "(pre-fix)". A run with any other checker problem still blocks them.
  - **A pre-fix failure of H1 or H5 must appear in the paper.** The post-fix report carries it via `make results PRE_FIX=<pre-fix verdicts.json>` (`--pre-fix-verdicts`), which adds a pre-fix column and `\VerdictPreFix…` macros.
- **Bugs found after the freeze:**
  - fix the bug;
  - log it as a *deviation* in `docs/CHANGELOG_RESEARCH.md`;
  - rerun **all** holdout configs (`primary`, `secondary`, `negative_control`, `scaling` and the crash matrix), with all policies, on the same holdout seeds, whatever the bug touched;
  - report both the pre-fix and the post-fix results (the pre-fix ones in an appendix).
  
  A baseline is never changed to make the candidate look better (CLAUDE.md rule 5). Nothing is tuned on holdout results.
- **Stripe adapter:** fixes to `p4/adapters/stripe_test.py` that touch neither the simulator (`p4/sim/`) nor the runbooks (`p4/runbooks/`, `p4/sched/`) are allowed after the freeze. Each is logged as a deviation, and C1 is re-run on the fixed commit, with both runs reported.
- **WP6 Part B:** may add files under `agent/` after the freeze. A change to `p4/` or `checker/` it cannot avoid is a deviation, and must be shown not to affect the holdout results.

## 9. What would change our claims

- H3 not supported ⇒ no "better method" claim; the paper is framed as contract + cost-of-assurance benchmark.
- H5 not supported ⇒ "Safe Resumption" leaves the title, and the failing (cell, policy) pairs are reported.
- C1 mismatch that cannot be fixed ⇒ simulator claims are limited to the matched transitions.
- If P5 = P6 = P3 in loss *and* P3 has negligible false closure at our fault levels ⇒ we report that assurance is cheap and false closure rare under these assumptions (a null finding). "Negligible" means: P3's false closures are below 1% of P3's reported CLOSED_CANCELLED dispositions at both low and high faults, each summed over the primary grid's cells at that fault level. `analysis/verdicts.py` `negligible_fc` reports it.

## Appendix A — Dev-seed exploration (exploratory; not evidence)

Everything here used dev seeds only. These results motivated the design above, and none of them is a paper result. Details, commands and the generated files they come from are in `docs/CHANGELOG_RESEARCH.md`. The numbers below are copied from those entries.

- **G1 (2026-10-20; changelog "G1" entry; `results/dev/primary-500`, seeds 100000–100499).**
  - P5 − P3 < 0 in 10 of 24 cells and never > 0. P4 − P3 > 0 at k = 1 in 7 of 8 faulty cells.
  - False closures: P3 1,079 (low) and 4,012 (high); P0 and P4–P6 0 at every level.
  - At j = 3, the largest share of P5 loss recoverable by late cancels was 0.515%. This is why the late_cancel ablation runs at j = 6.
  - Fault presets, j = 3 and the single sweep were fixed then, without using P6 results.
- **Sensitivity grid (G1; `results/dev/g1-sensitivity-200`, seeds 100000–100199, 11 grid points around the presets).**
  - H1 held at all points.
  - H2 at tight, k = 1, n = 32 held at every point. At n = 8 the CI excluded 0 at 7 of 11 points with high faults and at none with low faults. This is why H2 at n = 8 is secondary.
- **P6 redesign (G1, option (b), commit de3e958).**
  - The pre-G1 P6 (replay-first, weighted by 1 − ρ̂) was worse than P5 on dev: its CI excluded 0 in 5 cells, all with P6 worse. It is kept at tag **`dev-g1-old-p6`** (commit 3229674), which reproduces the pre-G1 P6 rows of `results/dev/primary-500` and `results/dev/g1-*`.
  - The redesigned P6 was designed on dev 100500–100999 and checked once on 101000–101499. The criterion was committed beforehand: no primary cell with a P6 − P5 CI entirely above 0. It passed on both ranges, with 0 cells better and 0 worse of 24.
  - P6 equalled P5 on every seed at k = 1 and differed only at k = 4, n = 32, where the sign flipped between ranges (changelog "G1 follow-up" entry). Hence H3 is secondary and expected to be null.
- **Holm family (2026-10-22).** {P5 − P3, P4 − P3}. P6 − P5 left the family for the reason above (changelog entry of that date).
- **Crash matrix runner (2026-10-23).** A 20-seed dev run of `experiments/crash_matrix.py` had 0 failed invariants and 0 P4–P6 false closures. Its rarest cells (e and late_cancel) crashed in about a quarter of runs, so a 2,000-seed holdout should be far above the H5 floor of 100 crashed runs per pair. The run is kept in `results/dev/crash-matrix-20`; it ran on the uncommitted runner just before this commit, so its manifest says dirty.
