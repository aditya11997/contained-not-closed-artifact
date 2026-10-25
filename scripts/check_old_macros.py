"""Check that every macro of a reference numbers.tex still exists with a byte-identical value.

    python scripts/check_old_macros.py [--ref main] [--file paper/generated/numbers.tex]

The reference is `git show <ref>:<file>` (or --ref-file PATH). New macros are allowed; a
missing or changed one prints the macro and exits 1.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from collect_macros import DEF_RE


def macros(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        m = DEF_RE.match(line.strip())
        if m:
            if m.group(1) in out:
                raise ValueError(f"\\{m.group(1)} defined twice")
            out[m.group(1)] = line.strip()
    return out


def compare(old: dict[str, str], new: dict[str, str]) -> list[str]:
    problems = [f"MISSING \\{n}" for n in sorted(old) if n not in new]
    problems += [f"CHANGED \\{n}: {old[n]} -> {new[n]}" for n in sorted(old) if n in new and new[n] != old[n]]
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ref", default="main")
    ap.add_argument("--ref-file", type=Path, help="compare with this file instead of git show <ref>:<file>")
    ap.add_argument("--file", default="paper/generated/numbers.tex")
    args = ap.parse_args(argv)
    old_text = args.ref_file.read_text(encoding="utf-8") if args.ref_file else subprocess.run(
        ["git", "show", f"{args.ref}:{args.file}"], cwd=REPO, check=True, capture_output=True, text=True).stdout
    old, new = macros(old_text), macros((REPO / args.file).read_text(encoding="utf-8"))
    problems = compare(old, new)
    for p in problems:
        print(p, file=sys.stderr)
    print(f"check_old_macros: {len(old)} reference macros, {len(old) - len(problems)} unchanged, "
          f"{len(problems)} missing or changed; {len(set(new) - set(old))} new")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
