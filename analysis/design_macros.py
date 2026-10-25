"""Design-parameter macros for the paper (Section IV), read from the frozen configs and code.

    python -m analysis.design_macros --out paper/generated/design.tex

These are inputs to the experiments, not results: fault presets (configs/faults.toml), the
scenario defaults (configs/scenario_default.toml), slack factors and seed partitions
(p4/sim/scenario.py), the primary grid's fixed values (configs/primary.toml), the accounting
sweep's retry count (p4/runbooks/base.py) and the statistics settings (analysis/stats.py).
They are exported so that no parameter value is typed by hand into the paper (CLAUDE.md rule 4).
"""
import argparse
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python <= 3.10
    import tomli as tomllib

REPO = Path(__file__).resolve().parents[1]
WORDS = {"none": "None", "low": "Low", "high": "High", "tight": "Tight", "normal": "Normal"}


def fmt(x) -> str:
    """Integers with a thin-space-free LaTeX thousands separator; floats as written in the config."""
    if isinstance(x, bool):
        raise TypeError("booleans are not design numbers")
    if isinstance(x, int):
        return f"{x:,}".replace(",", "{,}")
    s = repr(float(x))
    return s[:-2] if s.endswith(".0") else s


def design_macros(repo: Path = REPO) -> dict[str, str]:
    from analysis import stats
    from p4.runbooks.base import SWEEP_RETRIES
    from p4.sim.scenario import PARTITIONS, SLACK_FACTORS

    faults = tomllib.loads((repo / "configs" / "faults.toml").read_text(encoding="utf-8"))
    scen = tomllib.loads((repo / "configs" / "scenario_default.toml").read_text(encoding="utf-8"))
    primary = tomllib.loads((repo / "configs" / "primary.toml").read_text(encoding="utf-8"))
    m: dict[str, str] = {}
    for level in ("low", "high"):
        f, w = faults[level], WORDS[level]
        m[f"DesignFReq{w}"] = fmt(f["f_req"])
        m[f"DesignFResp{w}"] = fmt(f["f_resp"])
        m[f"DesignOutageRate{w}"] = fmt(f["outage_rate"])
        m[f"DesignOutageLength{w}"] = fmt(f["outage_length"])
        m[f"DesignOutageCommit{w}"] = fmt(f["outage_commit_prob"])
    for s in ("tight", "normal"):
        m[f"DesignSlack{WORDS[s]}"] = fmt(SLACK_FACTORS[s])
    for key, name in (("amount_min", "AmountMin"), ("amount_max", "AmountMax"), ("d_min", "DMin"),
                      ("d_max", "DMax"), ("s_min", "SMin"), ("s_max", "SMax"), ("p_min", "PMin"),
                      ("p_max", "PMax"), ("permit_prob", "PermitProb"), ("q", "Q")):
        m[f"Design{name}"] = fmt(scen[key])
    block = primary["block"][0]
    (c,) = block["c"]
    (jitter,) = block["jitter"]
    presets = tomllib.loads((repo / "configs" / "presets.toml").read_text(encoding="utf-8"))
    m["DesignC"] = fmt(c)
    m["DesignJitter"] = fmt(presets["jitter"][jitter]["j"])
    m["DesignCells"] = fmt(len(block["fault"]) * len(block["slack"]) * len(block["k"]) * len(block["n"]))
    m["DesignSeedsPerCell"] = fmt(primary["n_seeds"])
    m["DesignPolicies"] = fmt(len(primary["policies"]))
    m["DesignSweepRetries"] = fmt(SWEEP_RETRIES)
    m["DesignBoot"] = fmt(stats.N_BOOT)
    m["DesignAlpha"] = fmt(stats.ALPHA)
    hold, dev = PARTITIONS["holdout"], PARTITIONS["dev"]
    m["DesignHoldoutFirst"], m["DesignHoldoutLast"] = fmt(hold.start), fmt(hold.stop - 1)
    m["DesignDevFirst"], m["DesignDevLast"] = fmt(dev.start), fmt(dev.stop - 1)
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m analysis.design_macros", description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    from analysis.tables import numbers_tex
    macros = design_macros()
    text = numbers_tex(macros, "Design parameters from configs/ and frozen code (inputs, not results)",
                       source="analysis/design_macros.py")
    text = text.replace("from results files. Do not edit", "from configs and frozen code. Do not edit")
    args.out.write_text(text, encoding="utf-8")
    print(f"design_macros: {len(macros)} macros -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
