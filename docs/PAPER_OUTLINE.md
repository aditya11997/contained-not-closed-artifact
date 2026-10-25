# Paper outline (IEEE conference format; adjust to the venue's page limit)

Budget assumes 8 pages + references; scale proportionally.

| § | Title | Pages | Content | Source |
|---|---|---|---|---|
| – | Abstract | – | Problem, the contained/closed split, contract, the cost-of-assurance finding, conformance, artifact | written last (11/6) |
| 1 | Introduction | 1.0 | Agent payments (AP2 context); a revoked agent leaves accepted pending payments; Liu et al. show when revocation completes, we address what remains open; contributions (3–4 bullets, scoped) | REVAMP_PLAN §1 |
| 2 | Background & related work | 1.0 | Manual capture and cancel semantics (Stripe); revocation completion (Liu et al.); agent action evidence (Proof of Execution, Notarized Agents, AgentBound); sagas/compensation; 1‖ΣwⱼUⱼ scheduling | WP8 related-work prompt |
| 3 | Problem model | 1.0 | Fig. 1 timeline; payments, deadlines, jitter, authority partition, capacity, fault model; loss; false closure | REVAMP_PLAN §2 |
| 4 | Disposition contract and runbooks | 1.25 | States + evidence rule; invariant + short proof sketch (closure ⇒ sink evidence); P1–P6; adaptive replay-as-probe; complexity | `p4/contract.py`, BASELINES.md |
| 5 | Experimental design | 1.0 | Fig. 2 architecture (sim, fault layer, separate checker, Stripe adapter); fairness controls; seeds/prereg; grid; stats | PREREG.md |
| 6 | Results | 1.5 | RQ1 exposure remaining; RQ2 false closure (F4); RQ3 cost of assurance (F5); RQ4 P6 vs P5 (F3, null allowed); negative control; crash matrix (Table 3); Stripe conformance (Table 1) + case study | generated tables/figs |
| 7 | Threats to validity | 0.5 | Synthetic parameters; fault presets as sensitivity bands; single processor; k > 1 heuristic; Stripe test mode ≠ live; no real incidents | |
| 8 | Discussion & conclusion | 0.25 | Ops implication: "cancel sent" ≠ closed; when assurance is cheap vs costly | |

Tables: T1 Stripe mapping/conformance · T2 baseline fairness matrix · T3 crash matrix · T4 primary contrasts (Holm).
Figures: F1 timeline · F2 architecture · F3 paired loss vs P5 · F4 false closure vs fault · F5 cost-of-assurance frontier · (F6 negative control/scaling if space).
