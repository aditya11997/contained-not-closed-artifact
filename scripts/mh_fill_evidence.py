#!/usr/bin/env python3
"""Why P2 (weighted Moore-Hodgson) has a fill pass: prints, as Markdown, how often MH's
expected gain is >= EDF's on dev seeds 100000-100999 (n = 8, c = 0.5, k = 1, slack tight and
normal) with and without the fill pass, and the seed 100016 (tight) worked example.

Dev seeds only. Output is pasted verbatim into docs/CHANGELOG_RESEARCH.md (CLAUDE.md rule 4).
Run: .venv/bin/python scripts/mh_fill_evidence.py
"""
from p4.sched import edf, jobs_from, mh
from p4.sched.common import edd_key
from p4.sim.scenario import ScenarioConfig, make_scenario

SEEDS = range(100000, 101000)
SLACKS = ("tight", "normal")
C = 0.5
EXAMPLE = (100016, "tight")


def value(jobs, uids):
    w = {j.uid: j.w for j in jobs}
    return sum(w[u] for u in uids)


def jobs_for(seed, slack):
    return jobs_from(make_scenario(seed, ScenarioConfig(n=8, slack=slack)).observations(), C)


def main():
    total = 0
    ok = {False: 0, True: 0}
    for seed in SEEDS:
        for slack in SLACKS:
            jobs = jobs_for(seed, slack)
            v_edf = value(jobs, edf(jobs))
            total += 1
            for fill in (False, True):
                ok[fill] += value(jobs, mh(jobs, fill=fill)) >= v_edf - 1e-9
    print(f"Dev seeds {SEEDS.start}-{SEEDS.stop - 1}, slack in {SLACKS}, n = 8, c = {C}, k = 1 "
          f"({total} instances). MH expected gain >= EDF:")
    for fill, label in ((False, "spec drop rule only"), (True, "drop rule + fill pass")):
        print(f"- {label}: {ok[fill]}/{total} = {100 * ok[fill] / total:.1f}%")

    seed, slack = EXAMPLE
    jobs = jobs_for(seed, slack)
    print(f"\nExample: seed {seed}, slack {slack}. Candidates in EDD order (gain, s, d, gain/s):\n")
    print("| uid | gain | s | d | gain/s |\n|---|---|---|---|---|")
    for j in sorted(jobs, key=edd_key):
        print(f"| {j.uid} | {j.w:.1f} | {j.s} | {j.d} | {j.w / j.s:.1f} |")
    print()
    for label, uids in (("EDF", edf(jobs)), ("MH, drop rule only", mh(jobs, fill=False)),
                        ("MH, drop rule + fill", mh(jobs))):
        print(f"- {label}: {', '.join(uids)} (expected gain {value(jobs, uids):.1f})")


if __name__ == "__main__":
    main()
