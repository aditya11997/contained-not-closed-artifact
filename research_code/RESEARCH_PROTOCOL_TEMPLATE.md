# P4 pre-registration template (NOT preregistered yet)

**Status:** Draft. Date and commit hash MUST be recorded before confirming results on an independent holdout.

Date:
Researcher:
Commit hash:
Hypotheses H1/H2/H3:
- H1: Under constrained synthetic deadlines, the candidate improves paired modeled loss relative to both EDF and value-per-minute runbooks. Null: no improvement.
- H2: An advantage persists in at least two of three deadline environments and under both directions of score miscalibration; otherwise narrow the claim.
- H3: An unavailable sink-status query never results in a verified canceled disposition. Testable in unit tests and adversarial fault matrix.

## Scope / assumptions
- One previously authorized procurement agent, perfectly stopped at t=0.
- Only pending synthetic card payment captures. Merchant `capture_at` is a mock event time, NOT a card-network settlement deadline.
- One serialized cancel-only remediation operator.
- Consequences: synthetic suspect capture value + c * synthetic benign canceled value.
- Policy cannot inspect oracle class labels, processor internal truth, or extra authority.
- Costs and distributions are exploratory, not industry-calibrated.

## Confirmatory design
- Holdout base seed: ______ (different from exploratory seeds 71231 onwards).
- Count and sample size: ______ scenarios, n=8 candidate effects per seed.
- Primary outcome: paired difference in modeled loss; 95% seed-paired bootstrap interval.
- Secondary: suspicious captured value, legitimate canceled value, cancel counts, denial counts, elapsed time.
- Planned sensitivity: deadline tight/normal/loose, score miscalibration ±0.2, c=0.5 and c=1.0.
- No outcome-driven exclusions.
- Report negative/zero outcomes and all families.

## Baseline fairness audit
- Same observations: [ ]
- Same cost and estimated risk: [ ]
- Same remediation identity: [ ]
- Same capacity / deadlines: [ ]
- Same allowable remedies: [ ]
- Same status access: [ ]

## Stop rules
- If there is no difference against competent deadline-first and value-aware scheduling, do not call the method better.
- If the novelty audit finds an equivalent prior scheduling/compensation mechanism, do not call the algorithm original.
- If source/processor realism is not validated, keep claims simulation-limited.
- Do not claim top-conference suitability or acceptance from pilot plots alone.

## Planned extensions (do not treat as already implemented)
- Stronger operations-runbook baseline with status queries and escalations.
- Independent evidence checker as separate process with adversarial mutations.
- Uncertain outcomes and restart fault matrix at scale, rather than just the supplied demo and tests.
- Optional Stripe test-mode state transition check using no live charges.
- External parameter calibration, practitioner review, independent replication.
