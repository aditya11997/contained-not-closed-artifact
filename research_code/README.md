# P4 Pilot: Payment Disposition Research Artifact

**Synthetic research code.** No payments, money transfers, live card details, patient data, cloud services, or external APIs are used.

## Run in Python 3.10+

From this folder:

```bash
python -m unittest -v test_lab.py
python -m p4lab.experiments --n 50 --payments 8 --out results/pilot
python -m p4lab.experiments --n 1000 --payments 8 --out results/main
```

Outputs: `trials.csv` (every scenario/policy/configuration) and `summary.csv` (aggregate means and seed-paired bootstrap 95% intervals of differences).

## Design

Each synthetic payment is **authorized at creation**. At t=0, an incident removes authority from the originating agent (perfect quiescence assumed). A separate **remediation_operator** can selectively cancel eligible pre-capture payments. An independently modeled processor has an assumed `capture_at` time. These are synthetic business event times, **not** published scheme deadlines. Idempotent cancel and role restrictions are enforced. Outcome truth is hidden from policies and visible only to the evaluator.

The research target is quality of remediation under limited operator capacity and heterogenous remedy windows, **not** whether agents may make purchases. Policies: no remediation, earliest-deadline-first (strong runbook), value-per-minute greedy (strong runbook), and a small exact subset-scheduling dynamic program using estimated net avoided loss. DP is **not inherently novel**: it is a transparent candidate to determine whether a meaningful problem exists at all.

Limitations: single serialized remediation operator; no real payment networks; fixed capture times, static observed risk, modeled collateral harm; no dynamic status uncertainty in v0; clinical case out of scope. The paper must not claim finance-industry calibrated results until parameters and semantics are independently checked.

## Ethics and claims

Results show outcomes of this simulator only. No claims of safety, deployed financial compliance, clinical effects, production validation, or state-of-the-art novelty. Never report manufactured positives; report negative results as well. Consult original paper-venue policies on AI-assisted writing and disclosure.

## Source links

- https://github.com/google-agentic-commerce/AP2/blob/main/docs/ap2/specification.md
- https://docs.stripe.com/api/payment_intents/cancel
- https://docs.stripe.com/api/idempotent_requests
- https://conf.researchr.org/track/icse-2027/icse-2027-research-track
- https://conf.researchr.org/track/icse-2027/icse-2027-artifact-evaluation

## Additional files

- `RESEARCH_PROTOCOL_TEMPLATE.md` to freeze an actual new confirmatory study before viewing its outcomes. The bundled runs are **exploratory pilot results**, not a pre-registered confirmation.
- `p4lab/assurance.py` and `demo_recovery.py` exercise a lost response, unavailable status, and idempotent replay. They do not implement a fully distributed evidence store.
- `results/main` contains pilot output from 1,200 seeds in each of 6 configurations and all 4 policies. The existence of outputs is not evidence of industry calibration.
