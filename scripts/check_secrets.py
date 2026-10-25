#!/usr/bin/env python3
"""Fail if a Stripe secret key appears in any tracked or to-be-committed file.

Scans `git ls-files --cached --others --exclude-standard`, so new files are caught before
they are committed. Run by `make lint` and by the pre-commit hook from `make hooks`.

Pattern: any live key (`sk_live_` followed by key characters) or a test key with 10+ key
characters. A bare `sk_live_` prefix with no key body (e.g. in docs describing the rule)
is not a secret and is not reported.
"""
import re
import subprocess
import sys
from pathlib import Path

PATTERN = re.compile(r"sk_live_[A-Za-z0-9]+|sk_test_[A-Za-z0-9]{10,}")


def candidate_files(root: Path):
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root, check=True, capture_output=True,
    ).stdout
    return [root / p for p in out.decode().split("\0") if p]


def scan(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return []
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in PATTERN.finditer(line):
            # Never echo the key itself.
            hits.append((lineno, m.group()[:8] + "…"))
    return hits


def main():
    root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], check=True,
                               capture_output=True, text=True).stdout.strip())
    found = 0
    for path in candidate_files(root):
        for lineno, redacted in scan(path):
            print(f"{path.relative_to(root)}:{lineno}: possible Stripe secret ({redacted})")
            found += 1
    if found:
        print(f"check_secrets: {found} possible secret(s) found", file=sys.stderr)
        return 1
    print("check_secrets: no secrets found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
