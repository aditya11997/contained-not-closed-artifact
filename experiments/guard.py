"""Holdout guard (CLAUDE.md rule 3; CODE_AUDIT C2).

Holdout seeds may be used only if all of these hold, checked before any run starts:
1. the git tag `prereg-v1` exists;
2. HEAD descends from it (or is it);
3. docs/PREREG.md exists at the tag, and both HEAD's committed copy and the working-tree file
   are byte-identical to the tagged version;
4. the tracked working tree is clean (no staged or unstaged changes to tracked files), so the
   results correspond to an exact commit.
"""
import subprocess
from pathlib import Path

PREREG_TAG = "prereg-v1"
PREREG_PATH = "docs/PREREG.md"


class HoldoutRefused(RuntimeError):
    pass


def _git(repo: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)


def _blob(repo: Path, rev: str) -> bytes | None:
    proc = _git(repo, "cat-file", "blob", f"{rev}:{PREREG_PATH}")
    return proc.stdout if proc.returncode == 0 else None


def check_holdout_allowed(repo) -> str:
    """Return the commit `prereg-v1` points to; raise HoldoutRefused otherwise."""
    repo = Path(repo)
    tag = _git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{PREREG_TAG}^{{commit}}")
    if tag.returncode != 0:
        raise HoldoutRefused(f"git tag {PREREG_TAG} does not exist; freeze docs/PREREG.md and tag it first")
    tag_commit = tag.stdout.decode().strip()
    if _git(repo, "merge-base", "--is-ancestor", tag_commit, "HEAD").returncode != 0:
        raise HoldoutRefused(f"HEAD does not descend from {PREREG_TAG} ({tag_commit[:12]})")
    tagged = _blob(repo, tag_commit)
    if tagged is None:
        raise HoldoutRefused(f"{PREREG_PATH} does not exist at {PREREG_TAG}")
    if _blob(repo, "HEAD") != tagged:
        raise HoldoutRefused(f"{PREREG_PATH} at HEAD differs from {PREREG_TAG}")
    worktree = repo / PREREG_PATH
    if not worktree.is_file() or worktree.read_bytes() != tagged:
        raise HoldoutRefused(f"working-tree {PREREG_PATH} differs from {PREREG_TAG}")
    status = _git(repo, "status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0:
        raise HoldoutRefused(f"git status failed: {status.stderr.decode().strip()}")
    if status.stdout.strip():
        raise HoldoutRefused("tracked files have uncommitted changes; commit them before a holdout run")
    return tag_commit
