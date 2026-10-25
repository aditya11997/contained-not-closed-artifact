"""Paper lint and macro-collection checks (scripts/paper_check.py, scripts/collect_macros.py)."""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    modname = f"paper_scripts_{name}"
    spec = importlib.util.spec_from_file_location(modname, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod   # dataclasses look the module up while it executes
    spec.loader.exec_module(mod)
    return mod


pc = _load("paper_check")
cm = _load("collect_macros")

VERIFIED = "| key | x |\n|---|---|\n| `good2026` | y |\n"
BIB = "@article{good2026,\n  title = {T},\n}\n@article{unverified2026,\n  title = {U},\n}\n"


def make_paper(tmp_path: Path, section: str) -> Path:
    paper = tmp_path / "paper"
    (paper / "sections").mkdir(parents=True)
    (paper / "sections" / "01-x.tex").write_text(section, encoding="utf-8")
    (paper / "refs.bib").write_text(BIB, encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "REFERENCES_VERIFIED.md").write_text(VERIFIED, encoding="utf-8")
    return paper


def errors(tmp_path, section, final=False):
    return pc.lint(make_paper(tmp_path, section), final=final)


def test_clean_section_passes(tmp_path):
    rep = errors(tmp_path, "We evaluate P3 against P5 at $k = 1$ in 2026~\\cite{good2026}. \\NumberMacro{}\n")
    assert rep.ok() and not rep.warnings, (rep.errors, rep.warnings)


@pytest.mark.parametrize("word", ["Moreover", "delve", "seamless", "In conclusion", "not only", "perfectly"])
def test_banned_word_caught(tmp_path, word):
    rep = errors(tmp_path, f"{word}, the cancel is acknowledged.\n")
    assert any("banned" in e for e in rep.errors)


def test_banned_word_in_comment_ignored(tmp_path):
    assert errors(tmp_path, "% moreover this is a comment\nText.\n").ok()


def test_typed_number_warns_and_fails_under_final(tmp_path):
    section = "P3 closes falsely in 4.22 percent of reports.\n"
    rep = errors(tmp_path, section)
    assert rep.ok() and any("4.22" in w for w in rep.warnings)
    rep = pc.lint(make_paper(tmp_path / "f", section), final=True)
    assert any("4.22" in e for e in rep.errors)


def test_lit_and_markers_exempt_from_number_lint(tmp_path):
    rep = errors(tmp_path, "Problem \\lit{$1\\|\\sum w_jU_j$}. \\MISSING{rate at 13.05}\n", final=True)
    assert rep.ok(), rep.errors


def test_unknown_cite_caught(tmp_path):
    rep = errors(tmp_path, "See~\\cite{nosuch2026}.\n")
    assert any("not in refs.bib" in e for e in rep.errors)


def test_cite_in_bib_but_not_verified_caught(tmp_path):
    rep = errors(tmp_path, "See~\\cite{good2026,unverified2026}.\n")
    assert [e for e in rep.errors if "REFERENCES_VERIFIED" in e] and "unverified2026" in " ".join(rep.errors)
    assert not any("good2026" in e for e in rep.errors)


@pytest.mark.parametrize("switch", ["\\ifanon", "\\ificrito", "\\iffinal"])
def test_venue_switch_in_section_caught(tmp_path, switch):
    rep = errors(tmp_path, f"{switch} hidden\\fi\n")
    assert any("venue switch" in e for e in rep.errors)


def _gen(tmp_path, files: dict[str, str]) -> Path:
    paper = tmp_path / "paper"
    (paper / "generated").mkdir(parents=True)
    for name, body in files.items():
        (paper / "generated" / name).write_text(body, encoding="utf-8")
    return paper


def test_collect_skips_identical_subset(tmp_path):
    paper = _gen(tmp_path, {"numbers.tex": "\\newcommand{\\A}{1}\n\\newcommand{\\B}{2}\n",
                            "stripe.tex": "\\newcommand{\\B}{2}\n"})
    assert cm.main(["--paper", str(paper)]) == 0
    out = (paper / "macros-generated.tex").read_text()
    assert "\\input{generated/numbers}" in out and "\\input{generated/stripe}" not in out


def test_collect_fails_on_conflicting_values(tmp_path):
    paper = _gen(tmp_path, {"numbers.tex": "\\newcommand{\\A}{1}\n\\newcommand{\\B}{2}\n",
                            "other.tex": "\\newcommand{\\B}{3}\n"})
    assert cm.main(["--paper", str(paper)]) == 1


def test_collect_fails_on_hand_written_collision(tmp_path):
    paper = _gen(tmp_path, {"numbers.tex": "\\newcommand{\\TODO}{1}\n"})
    (paper / "macros-paper.tex").write_text("\\newcommand{\\TODO}[1]{#1}\n", encoding="utf-8")
    assert cm.main(["--paper", str(paper)]) == 1


def test_repo_generated_macros_collect_cleanly(tmp_path):
    """The real paper/generated/ set has no conflicting definitions."""
    _included, _skipped, conflicts, n = cm.collect(ROOT / "paper")
    assert not conflicts and n > 0
