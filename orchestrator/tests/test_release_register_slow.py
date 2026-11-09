"""The release register is frozen against git history: what the base commit released stays."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from raidar.subject.release_check import release_base, release_issues
from tests.release_support import edit_agent, release_tree, write_register

pytestmark = pytest.mark.slow


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return release_tree(tmp_path)


# ---- released means frozen: the register only grows ------------------------------------


def git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return done.stdout.strip()


def commit(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def history(tree: Path) -> tuple[Path, str]:
    """The tree as a git repository on ``main`` with its register committed."""
    git(tree, "init", "-q", "-b", "main")
    return tree, commit(tree, "release")


def test_re_hashing_a_released_version_is_refused(history: tuple[Path, str]) -> None:
    tree, base = history
    edit_agent(tree, lambda doc: doc["models"]["gpt-6-luna"].update(reasoning=[]))
    write_register(tree)  # codex@1 now carries the edited definition's hash

    issues = release_issues(tree, base=base)

    assert len(issues) == 1
    assert "release codex@1 was re-hashed" in issues[0]


def test_the_base_is_the_parent_on_main_and_the_merge_base_off_main(
    history: tuple[Path, str],
) -> None:
    tree, base = history
    tip = commit(tree, "next")
    assert release_base(tree) == base

    git(tree, "checkout", "-q", "-b", "feature")
    commit(tree, "work")

    assert release_base(tree) == tip


def test_a_base_that_is_not_a_commit_is_reported(history: tuple[Path, str]) -> None:
    tree, _ = history

    assert release_issues(tree, base="no-such-ref") == [
        "release base 'no-such-ref' is not a commit"
    ]
