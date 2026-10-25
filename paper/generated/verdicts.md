| hypothesis | verdict | detail |
|---|---|---|
| H1 | supported | P3 false closures > 0 in 16 of 16 faulty primary cells; P0/P4/P5/P6 false closures in total: 0 ({'negative_control': 0, 'primary': 0, 'secondary': 0, 'scaling': 0}) |
| H2 | supported | low: holm_ci_lo = 108.851, p_holm = 0.009599 (information only); high: holm_ci_lo = 346.385, p_holm = 0.009599 (information only) (need > 0 in both) |
| H3 | not supported | 0 of 3 fault levels count (need 2); none: 0/4 cells with ci_hi < 0; low: 0/4 cells with ci_hi < 0; high: 0/4 cells with ci_hi < 0 |
| H4 | supported | 8000 of 8000 seeds equal across P3-P6 in 4 cells |
| H5 | supported | 39 of 39 (cell, policy) pairs of P4-P6 hold; false closures 0, duplicate effects 0, min crashed runs per pair 3126 |
| C1 | supported | deciding run stripe-20261024T024751Z: mapping rows matched 13 of 13; conformance tests run 15, passed 15, expected 15; attempts: stripe-20261024T024751Z (commit 430c6a0279ff, dirty False, complete) |
| S9 | not met | P3 false closures; low: 4961 of 117610 CLOSED_CANCELLED (4.22%); high: 15890 of 121803 CLOSED_CANCELLED (13.05%) (negligible iff < 1% at both) |
