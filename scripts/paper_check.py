"""Checks for the paper: page limit, fonts, citations, banned words, typed numbers, venue leakage.

  python scripts/paper_check.py lint  [--paper paper] [--final]
  python scripts/paper_check.py check --venue agent|icrito --pdf paper/build/main-agent.pdf [--final]

`lint` needs no PDF. `check` runs lint plus the PDF checks (poppler: pdfinfo, pdffonts, pdftotext).
Exit status 0 = all checks passed. Typed numbers warn by default and fail with --final.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE_LIMITS = {"agent": 8, "icrito": 6}

BANNED = [
    r"testament", r"beacon", r"delve\w*", r"tapestry", r"moreover", r"furthermore",
    r"further studies are needed", r"not only", r"crucial role", r"revolutioni[sz]\w*",
    r"groundbreaking", r"it is worth noting", r"in conclusion", r"pave(?:s|d)? the way",
    r"seamless(?:ly)?", r"perfect(?:ly)?", r"highly superior", r"unprecedented",
]
BANNED_RE = re.compile(r"\b(?:" + "|".join(BANNED) + r")\b", re.IGNORECASE)

CITE_RE = re.compile(r"\\(?:no)?cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^}]*)\}")
BIBKEY_RE = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,")
VERIFIED_KEY_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|", re.MULTILINE)

# Commands whose (first) argument is not prose for the number lint.
SKIP_ARG_CMDS = ("label", "ref", "eqref", "autoref", "cref", "Cref", "cite", "citep", "citet", "nocite",
                 "includegraphics", "input", "include", "begin", "end", "url", "href", "lit",
                 "MISSING", "TODO", "CONFIRM", "CITEPENDING", "bibliography", "bibliographystyle",
                 "hypersetup", "texttt", "vspace", "hspace")
ALLOWED_TOKENS = re.compile(
    r"\bP[0-6]b?\b|\bH[1-5]\b|\bC[0-5]\b|\bPH[1-9]\b|\bT[1-4]\b|\bF[1-6]\b|"
    r"\b\d+(?:\.\d+)?(?:pt|mm|cm|em|ex|in|bp)\b|"  # TeX lengths (layout, not data)
    r"\b[kn]\s*=\s*(?:1|4|8|32|128|\\infty)\b|\$[kn]\s*=\s*(?:1|4|8|32|128|\\infty)\$|"
    r"\b(?:19|20)\d\d\b")
LEAK_STRINGS = ("aditya11997", "/Users/", "~/p4", "acct_", "sk_test_")


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.errors


def strip_comments(text: str) -> str:
    return "\n".join(re.sub(r"(?<!\\)%.*", "", line) for line in text.splitlines())


def drop_braced_arg(text: str, cmd: str) -> str:
    """Remove \\cmd[opt]{...} including nested braces (argument only; keeps line count)."""
    out, i, pat = [], 0, re.compile(r"\\" + cmd + r"\*?(?:\[[^\]]*\])*\{")
    while True:
        m = pat.search(text, i)
        if not m:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:m.start()])
        depth, j = 1, m.end()
        while j < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
        out.append("\n" * text.count("\n", m.start(), j))
        i = j


def section_files(paper: Path) -> list[Path]:
    return sorted((paper / "sections").glob("*.tex"))


def lint(paper: Path, final: bool = False, verified_md: Path | None = None) -> Report:
    rep = Report()
    paper = paper.resolve()
    verified_md = verified_md or (paper.parent / "docs" / "REFERENCES_VERIFIED.md")
    bib = paper / "refs.bib"
    bib_keys = set(BIBKEY_RE.findall(bib.read_text(encoding="utf-8"))) if bib.exists() else set()
    verified = set(VERIFIED_KEY_RE.findall(verified_md.read_text(encoding="utf-8"))) if verified_md.exists() else set()
    for f in section_files(paper):
        raw = f.read_text(encoding="utf-8")
        text = strip_comments(raw)
        rel = f.relative_to(paper)
        # venue switches belong in venue.tex/meta.tex only
        for n, line in enumerate(text.splitlines(), 1):
            if re.search(r"\\if(anon|icrito|final)\b", line):
                rep.errors.append(f"{rel}:{n}: venue switch in a section file")
        # citations
        for m in CITE_RE.finditer(text):
            n = text.count("\n", 0, m.start()) + 1
            for key in (k.strip() for k in m.group(1).split(",") if k.strip()):
                if key not in bib_keys:
                    rep.errors.append(f"{rel}:{n}: \\cite{{{key}}} not in refs.bib")
                if key not in verified:
                    rep.errors.append(f"{rel}:{n}: \\cite{{{key}}} not logged in REFERENCES_VERIFIED.md")
        # banned words (prose only: drop arguments of markers and references)
        prose = text
        for cmd in ("label", "ref", "cite", "includegraphics", "input", "url", "href"):
            prose = drop_braced_arg(prose, cmd)
        for n, line in enumerate(prose.splitlines(), 1):
            for m in BANNED_RE.finditer(line):
                rep.errors.append(f"{rel}:{n}: banned word or phrase '{m.group(0)}'")
        # typed numbers
        num_text = text
        for cmd in SKIP_ARG_CMDS:
            num_text = drop_braced_arg(num_text, cmd)
        num_text = re.sub(r"\\[A-Za-z@]+", " ", num_text)          # macro names carry no digits anyway
        for n, line in enumerate(num_text.splitlines(), 1):
            cleaned = ALLOWED_TOKENS.sub(" ", line)
            for m in re.finditer(r"\d+(?:[.,]\d+)*", cleaned):
                msg = f"{rel}:{n}: typed number '{m.group(0)}' (use a macro from paper/generated/ or \\lit{{}})"
                (rep.errors if final else rep.warnings).append(msg)
    return rep


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def pdf_checks(pdf: Path, venue: str, paper: Path, rep: Report) -> None:
    if not pdf.exists():
        rep.errors.append(f"{pdf}: PDF not found (build first)")
        return
    m = re.search(r"^Pages:\s+(\d+)", run(["pdfinfo", str(pdf)]), re.MULTILINE)
    pages, limit = int(m.group(1)) if m else -1, PAGE_LIMITS[venue]
    print(f"pages: {pages} of {limit} allowed ({venue}, references included)")
    if pages > limit:
        rep.errors.append(f"page limit exceeded: {pages} > {limit}")
    for line in run(["pdffonts", str(pdf)]).splitlines()[2:]:
        cols = line.split()
        # columns from the right: emb sub uni object-id(2)
        if len(cols) >= 6:
            if cols[-5] != "yes":
                rep.errors.append(f"font not embedded: {line.strip()}")
            if "Type 3" in line:
                rep.errors.append(f"Type 3 font: {line.strip()}")
    main = (paper / "main.tex").read_text(encoding="utf-8")
    if r"\documentclass[conference,10pt]{IEEEtran}" not in main or "compsoc" in main:
        rep.errors.append(r"main.tex must use \documentclass[conference,10pt]{IEEEtran} without compsoc")
    if venue == "icrito":
        text = run(["pdftotext", str(pdf), "-"])
        meta = (paper / "meta.tex").read_text(encoding="utf-8")
        names = re.findall(r"\\newcommand\{\\Author(?:Name|Email)\}\{([^}]*)\}", meta)
        needles = list(LEAK_STRINGS) + [n for n in names if n and "CONFIRM" not in n]
        for needle in needles:
            if needle and needle.lower() in text.lower():
                rep.errors.append(f"anonymized PDF contains identifying string '{needle}'")
        info = run(["pdfinfo", str(pdf)])
        a = re.search(r"^Author:[ \t]*(.*)$", info, re.MULTILINE)
        if a and a.group(1).strip():
            rep.errors.append(f"anonymized PDF metadata has Author: {a.group(1).strip()}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["lint", "check"])
    ap.add_argument("--paper", type=Path, default=ROOT / "paper")
    ap.add_argument("--venue", choices=sorted(PAGE_LIMITS), default="agent")
    ap.add_argument("--pdf", type=Path)
    ap.add_argument("--final", action="store_true")
    args = ap.parse_args(argv)
    args.paper = args.paper.resolve()
    rep = lint(args.paper, final=args.final)
    if args.mode == "check":
        pdf_checks(args.pdf or args.paper / "build" / f"main-{args.venue}.pdf", args.venue, args.paper, rep)
    for w in rep.warnings:
        print("WARN ", w)
    for e in rep.errors:
        print("ERROR", e)
    print(f"{args.mode}: {'OK' if rep.ok() else 'FAILED'} ({len(rep.errors)} error(s), {len(rep.warnings)} warning(s))")
    return 0 if rep.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
