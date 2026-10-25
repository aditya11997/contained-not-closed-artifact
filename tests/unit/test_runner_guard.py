"""CLAUDE.md rule 3: holdout seeds are refused unless HEAD descends from the git tag prereg-v1,
docs/PREREG.md is byte-identical to its tagged version (at HEAD and in the working tree), and
the tracked tree is clean. Each case builds a throwaway git repository."""
import os
import subprocess

import pytest

from experiments import run as runner
from experiments.config import CONFIGS
from experiments.guard import HoldoutRefused, check_holdout_allowed

SMOKE = str(CONFIGS / "smoke.toml")

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, env=ENV, check=True, capture_output=True)


def commit_file(repo, rel, text, msg):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    git(repo, "add", rel)
    git(repo, "commit", "-q", "-m", msg)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit_file(tmp_path, "README", "x\n", "init")
    return tmp_path


@pytest.fixture
def tagged(repo):
    commit_file(repo, "docs/PREREG.md", "# Prereg\nfrozen\n", "freeze")
    git(repo, "tag", "prereg-v1")
    return repo


def refused(repo, match):
    with pytest.raises(HoldoutRefused, match=match):
        check_holdout_allowed(repo)


def test_refused_without_tag(repo):
    commit_file(repo, "docs/PREREG.md", "# Prereg\n", "prereg, untagged")
    refused(repo, "does not exist")


def test_allowed_at_tag_and_after_unrelated_commits(tagged):
    tag_commit = check_holdout_allowed(tagged)
    commit_file(tagged, "code.py", "x = 1\n", "later work")
    assert check_holdout_allowed(tagged) == tag_commit


def test_allowed_with_annotated_tag(repo):
    commit_file(repo, "docs/PREREG.md", "# Prereg\n", "freeze")
    git(repo, "tag", "-a", "prereg-v1", "-m", "frozen")
    check_holdout_allowed(repo)


def test_refused_when_head_does_not_descend_from_tag(tagged):
    git(tagged, "checkout", "-q", "-b", "side", "HEAD~1")
    commit_file(tagged, "docs/PREREG.md", "# Prereg\nfrozen\n", "same text, other history")
    refused(tagged, "does not descend")


def test_refused_when_prereg_edited_in_working_tree(tagged):
    (tagged / "docs/PREREG.md").write_text("# Prereg\nfrozen\nedited\n")
    refused(tagged, "working-tree")


def test_refused_when_prereg_whitespace_differs(tagged):
    (tagged / "docs/PREREG.md").write_bytes(b"# Prereg\r\nfrozen\r\n")
    refused(tagged, "working-tree")


def test_refused_when_prereg_changed_in_later_commit(tagged):
    commit_file(tagged, "docs/PREREG.md", "# Prereg\nrewritten\n", "edit prereg")
    refused(tagged, "at HEAD differs")


def test_refused_when_prereg_changed_and_reverted_only_in_worktree(tagged):
    commit_file(tagged, "docs/PREREG.md", "# Prereg\nrewritten\n", "edit prereg")
    (tagged / "docs/PREREG.md").write_text("# Prereg\nfrozen\n")
    refused(tagged, "at HEAD differs")


def test_refused_when_prereg_deleted(tagged):
    git(tagged, "rm", "-q", "docs/PREREG.md")
    git(tagged, "commit", "-q", "-m", "delete prereg")
    refused(tagged, "at HEAD differs")


def test_refused_when_prereg_missing_at_tag(repo):
    git(repo, "tag", "prereg-v1")
    refused(repo, "does not exist at prereg-v1")


def test_refused_when_tracked_tree_dirty(tagged):
    commit_file(tagged, "code.py", "x = 1\n", "code")
    (tagged / "code.py").write_text("x = 2\n")
    refused(tagged, "uncommitted")


def test_untracked_files_do_not_block(tagged):
    (tagged / "results").mkdir()
    (tagged / "results" / "out.csv").write_text("a\n")
    check_holdout_allowed(tagged)


def test_cli_refuses_holdout_before_running(repo, tmp_path, monkeypatch, capsys):
    """The runner exits 2 and writes nothing when the guard refuses."""
    monkeypatch.setattr(runner, "REPO", repo)
    out = tmp_path / "out"
    code = runner.main(["--config", SMOKE, "--partition", "holdout", "--out", str(out),
                        "--workers", "1"])
    assert code == 2
    assert not out.exists()
    assert "holdout refused" in capsys.readouterr().err


def test_cli_rejects_seeds_outside_partition(tmp_path, capsys):
    out = tmp_path / "out"
    code = runner.main(["--config", SMOKE, "--partition", "dev", "--seed-start", "199990",
                        "--out", str(out)])
    assert code == 2
    assert not out.exists()
    assert "not all in the dev partition" in capsys.readouterr().err
