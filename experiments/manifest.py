"""manifest.json for every experiment run (CODE_AUDIT C5): git commit and dirty flag,
Python/OS, package versions, config and preset hashes, CLI args, seed range and times."""
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

PACKAGES = ("p4", "numpy", "pandas", "matplotlib")


def _git(repo, *args) -> str | None:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def git_state(repo) -> dict:
    status = _git(repo, "status", "--porcelain", "--untracked-files=no")
    return {"commit": _git(repo, "rev-parse", "HEAD"),
            "branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": None if status is None else bool(status)}


def package_versions() -> dict:
    out = {}
    for name in PACKAGES:
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            out[name] = None
    return out


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def base_manifest(repo, config_path, argv) -> dict:
    return {
        "schema": "p4.manifest/v1",
        "git": git_state(repo),
        "python": sys.version,
        "platform": platform.platform(),
        "packages": package_versions(),
        "config": {"path": str(config_path), "sha256": sha256_file(config_path)},
        "argv": list(argv),
        "start": now_iso(),
    }


def write_manifest(path, manifest: dict) -> None:
    Path(path).write_text(json.dumps(manifest, sort_keys=True, indent=1) + "\n", encoding="utf-8")
