# Exploratory results — not cited as evidence

Everything under `research_code/results/` was produced by the legacy pilot (`p4lab`) before the revamp. It is **exploratory** and is **not cited** in the paper or used as evidence for any claim. It is kept unchanged so that negative and superseded results stay on record (CLAUDE.md rule 5).

## Why these results can't be confirmatory

- **Pilot/main seed overlap (CODE_AUDIT C2).** `pilot` used seeds 71231–71480 and `main` used 71231–72430, so every pilot seed is also in `main`. `main` isn't independent of the pilot, and no holdout exists. To reproduce the overlap count, run `python3 scripts/audit_scheduling_equivalence.py`; the line `pilot seeds … main seeds … overlap …` prints it.
- All six configs share one seed range, and none of the 12 comparisons has multiplicity control (C3).
- Plans are executed with no faults, and the sink always answers truthfully (C1). The sink also has the idempotency and clock defects A1–A3.
- The "dp" candidate matches the classical Lawler–Moore algorithm's objective (B1). These results don't show a novel method.
- `authority_violations` counts denied calls and is vacuous (A5).

The revamped study uses disjoint seed partitions: `dev` 100000–199999 and `holdout` 900000–999999. See `docs/REVAMP_PLAN.md` and `docs/CODE_AUDIT.md`.
