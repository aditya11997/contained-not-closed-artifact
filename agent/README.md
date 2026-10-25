# agent/ — WP6 Part B case study

- `procure.py`: the tool-calling loop on a local Ollama model, the two tools, the mandate wrapper, the trace.
- `run_case_study.py`: one run end to end (agent thread, `incident_stop`, live P6 episode on a
  `WallClockDriver`, sim replays of P3/P5/P6, the checker). `--all --run-stripe` runs every `[[runs]]`.
- `summarize.py`: tables, the annotated timeline and `fig1_timeline.{pdf,png}` from `results/case_study/`,
  and the GENERATED blocks of `docs/CASE_STUDY.md`.
- `case_study.toml`: model, mandate, harness, payment rules, the declared runs and the figure rule.
- `tests/`: sim-backend integration tests with a fake chat client (`pytest -q agent/tests`).

Per run, `results/case_study/<run_id>/` holds `manifest.json`, `summary.json`, `trace.jsonl` (model name and
digest, prompts, model replies, tool calls and results, timestamps), `calls.jsonl` (gateway call log),
`live/<policy>/` and `replay/<policy>/` (`events.jsonl` processor log, `report.json` disposition report,
`truth.json`, `journal.jsonl`), `verdicts.jsonl` (checker, in episode order), and on Stripe
`stripe_observations.jsonl` (sanitized responses: no bodies, no keys) and `stripe_faults.jsonl`.

Results of these runs are a case study, outside the pre-registered contrasts; see `docs/CASE_STUDY.md`.
