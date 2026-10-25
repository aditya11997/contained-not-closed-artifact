"""Verify every archived holdout file against its recorded SHA-256 and size; write the macros.

    python scripts/verify_archive.py [--archive docs/RESULTS_ARCHIVE.md] [--tex paper/generated/repro.tex]

Reads every `results/holdout/` row of docs/RESULTS_ARCHIVE.md (`| `sha256` | bytes | `path` | in git |`),
hashes the file on disk now, and writes \\ReproHoldoutFilesListed, \\ReproHoldoutFilesMatched and
\\ReproHoldoutFilesMissing. It verifies the archived holdout files as they are on disk today. It
does NOT count the 2026-10-24 regeneration event (docs/CHANGELOG_RESEARCH.md): which files were
regenerated is recorded only in that entry's prose.

If any listed file is missing, or differs in size or hash, it prints the paths and exits 1
without writing the .tex, so a machine without the local (untracked) results cannot produce a
low "matched" count silently.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from analysis.tables import fmt_int, numbers_tex

ROW_RE = re.compile(r"^\|\s*`([0-9a-f]{64})`\s*\|\s*([\d,]+)\s*\|\s*`(results/holdout/[^`]+)`\s*\|")


def archive_rows(text: str) -> list[tuple[str, int, str]]:
    """(sha256, bytes, path) of every results/holdout/ row, in file order; duplicates are an error."""
    rows = [(m.group(1), int(m.group(2).replace(",", "")), m.group(3))
            for m in map(ROW_RE.match, text.splitlines()) if m]
    paths = [p for _, _, p in rows]
    if len(set(paths)) != len(paths):
        raise ValueError("a results/holdout/ path is listed twice in the archive")
    return rows


def sha256(path: Path, chunk: int = 1 << 22) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def verify(rows, root: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"matched": [], "missing": [], "mismatched": []}
    for digest, size, rel in rows:
        p = root / rel
        if not p.is_file():
            out["missing"].append(rel)
        elif p.stat().st_size != size or sha256(p) != digest:
            out["mismatched"].append(rel)
        else:
            out["matched"].append(rel)
    return out


def macros(rows, result) -> dict[str, str]:
    return {"ReproHoldoutFilesListed": fmt_int(len(rows)),
            "ReproHoldoutFilesMatched": fmt_int(len(result["matched"])),
            "ReproHoldoutFilesMissing": fmt_int(len(result["missing"]))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--archive", type=Path, default=REPO / "docs" / "RESULTS_ARCHIVE.md")
    ap.add_argument("--root", type=Path, default=REPO, help="directory the archive paths are relative to")
    ap.add_argument("--tex", type=Path, help="write the macros here (only if every file matches)")
    args = ap.parse_args(argv)
    rows = archive_rows(args.archive.read_text(encoding="utf-8"))
    if not rows:
        print(f"verify_archive: no results/holdout/ rows in {args.archive}", file=sys.stderr)
        return 1
    result = verify(rows, args.root)
    for kind in ("missing", "mismatched"):
        for rel in result[kind]:
            print(f"{kind.upper()} {rel}", file=sys.stderr)
    print(f"verify_archive: {len(rows)} listed, {len(result['matched'])} matched, "
          f"{len(result['missing'])} missing, {len(result['mismatched'])} mismatched")
    if result["missing"] or result["mismatched"]:
        return 1
    if args.tex:
        args.tex.write_text(numbers_tex(macros(rows, result), f"Holdout files verified against {args.archive.name}",
                                        source="scripts/verify_archive.py"), encoding="utf-8")
        print(f"wrote {args.tex}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
