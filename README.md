# Contained Is Not Closed: research artifact

Artifact for *Contained Is Not Closed: Evidence-Bounded Disposition and Safe Resumption of Remediation for Pre-Revocation Agent Payments* (Aditya Krishnan, Independent Researcher).

When an agent that makes payments is revoked, the payments it created before the stop are still pending. This artifact contains the simulator, runbooks, independent checker, Stripe test-mode adapter, experiment runner, analysis code and results behind the paper. It measures what it costs to close such an incident only on processor-side evidence.

## Contents

| Path | What it is |
|---|---|
| `p4/` | Simulator (sink, clock, transport faults, scenarios), schedulers, runbooks P0–P6, disposition contract, write-ahead journal, Stripe test-mode adapter |
| `checker/` | Independent checker, run as a separate process; imports only the shared log schema |
| `experiments/`, `configs/` | Runner, holdout guard and the frozen experiment configurations |
| `analysis/` | Statistics, tables, figures, verdicts and the post-hoc analyses (PH2–PH8) |
| `agent/` | Case study: a local tool-calling language-model agent, revoked mid-run |
| `results/` | Holdout trial tables, conformance run, case-study runs |
| `paper/` | LaTeX sources; every number comes from `paper/generated/` |
| `docs/` | Pre-registration (`PREREG.md`, frozen at tag `prereg-v1` in the working repository), research rules, baselines, known code defects, post-hoc plans, research changelog, Stripe mapping, case study, archive hashes, verified references |
| `tests/` | Unit, property, fairness, leakage, crash-matrix, checker-mutation, golden and conformance tests |
| `research_code/` | The earlier exploratory pilot (frozen, exploratory only) |

## Setup

Python ≥ 3.11.

```
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q
```

Stripe tests are skipped unless `STRIPE_TEST_KEY` holds a **test-mode** key (`sk_test_…`) and `--run-stripe` is given. The adapter refuses every other key. No real cards or live payments are involved.

## Reproducing the results

- **Tables, from this repository alone** (about a minute):
  ```
  python -m analysis.report --results results/holdout --out regen --figs regen/figs \
      --conformance results/conformance/stripe-20261024T024751Z --narrow --tables-only
  ```
  This regenerates every table and CSV the paper uses from the committed holdout trials, byte-identical to `paper/generated/`. It covers the paired contrasts (Table III), false closures, the crash matrix (Table IV), the negative control, the late-cancel ablation and scaling. The figures are regenerated too; their PDF bytes depend on the matplotlib version.
- **Verdicts, with the per-run data:** the pre-registered verdicts (`paper/generated/verdicts.md`) also gate on the checker's per-run integrity results (`checker_verdicts.jsonl`, several GB). These are archived on Zenodo, https://doi.org/10.5281/zenodo.23650008: download the bundles, check them against `SHA256SUMS`, extract them at the root of this repository (`tar -xf holdout-<config>.tar`), then rerun the command above without `--tables-only` to regenerate everything, including `verdicts.md`. `docs/RESULTS_ARCHIVE.md` lists every per-run file with its SHA-256.
- **From scratch:** `python -m experiments.run --config configs/<name>.toml --partition dev --out results/dev/<name>` runs a configuration. The same configuration and seed give byte-identical outputs. Holdout runs are guarded by the `prereg-v1` git tag of the working repository; this snapshot has no history, so the guard refuses holdout seeds here.
- **Paper:** `make -C paper check` builds the paper with the bundled IEEE class and checks the page limit, fonts, citations, and that no number is typed by hand.

## License

MIT (see `LICENSE`). The vendored `paper/IEEEtran.cls` and `paper/IEEEtran.bst` are under the LaTeX Project Public License.
