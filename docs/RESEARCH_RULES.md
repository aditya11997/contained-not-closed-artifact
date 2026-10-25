# Research rules

Code comments cite these rules as "CLAUDE.md rule N" (the file's name in the working repository).

## Non-negotiable research rules
1. **Oracle isolation.** Nothing under `p4/sched/` or `p4/runbooks/` may import `p4.sim.processor` internals, `checker/`, or read hidden labels (`y`, actual capture time `D`). `tests/leakage/` enforces this statically. Don't weaken that test.
2. **Fairness.** Every policy for a given seed gets the identical scenario, observation stream, capacity `k`, authority and fault realization (fault RNG keyed by `(seed, call_index_per_payment)`, not by global call order). `tests/fairness/` enforces this.
3. **Seeds.** `dev` = 100000–199999 (explore freely). `holdout` = 900000–999999 (only after the `prereg-v1` git tag; the runner refuses otherwise). Never tune anything on holdout results.
4. **Never fabricate or hand-type numbers** into docs or the paper. All numbers come from `analysis/` scripts reading result files; the paper uses generated `.tex` macros.
5. **Preserve negative results.** Don't delete runs, don't drop seeds post hoc, and don't change a baseline to make the candidate look better. If a baseline is found buggy, fix it, rerun *all* policies, and log it in `docs/CHANGELOG_RESEARCH.md`.
6. **Claims.** Lawler–Moore is a known algorithm and the scheduling baseline. "ACK ≠ completion" is Liu et al.'s idea (arXiv:2610.15991); cite it, don't claim it. Simulated capture times aren't card-network deadlines.
7. **Frozen files.** `research_code/` (legacy pilot) and, after tagging, `docs/PREREG.md` are read-only.

## Safety
- No real cards, PAN, customer data or live payments. The Stripe adapter must refuse any key not starting with `sk_test_`. Keys come only from the env var `STRIPE_TEST_KEY`; never write them to files, logs or commits.
- No cloud provisioning, paid model calls, public repos or deployments.

