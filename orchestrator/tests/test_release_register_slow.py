"""The release register is frozen against git history: what the base commit released stays."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from raidar_contract.releases import ReleaseError

from raidar.contract.paths import agents_root
from raidar.subject.agent.definition import load_agent
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


def register_text(tree: Path) -> str:
    return (tree / "lab" / "releases.yaml").read_text(encoding="utf-8")


def test_re_hashing_a_released_version_is_refused(history: tuple[Path, str]) -> None:
    tree, base = history
    edit_agent(tree, lambda doc: doc["models"]["gpt-6-luna"].update(reasoning=[]))
    write_register(tree)  # codex@1 now carries the edited definition's hash

    issues = release_issues(tree, base=base)

    assert issues == [issues[0]]
    assert "release codex@1 was re-hashed" in issues[0]


def test_removing_a_released_version_is_refused(tree: Path) -> None:
    digest = load_agent(agents_root(tree), "codex").digest
    write_register(tree, extra_agents={"codex@2": digest})
    git(tree, "init", "-q", "-b", "main")
    base = commit(tree, "release codex@2")
    write_register(tree)

    issues = release_issues(tree, base=base)

    assert any("release codex@2 was removed" in issue for issue in issues)


def test_a_new_version_appended_beside_the_old_one_passes(history: tuple[Path, str]) -> None:
    tree, base = history
    edit_agent(tree, lambda doc: doc["models"]["gpt-6-luna"].update(reasoning=[]))
    digest = load_agent(agents_root(tree), "codex").digest
    path = tree / "lab" / "releases.yaml"
    path.write_text(
        register_text(tree).replace("agents:\n", f"agents:\n  codex@2: {digest}\n"),
        encoding="utf-8",
    )

    assert release_issues(tree, base=base) == []


def test_the_base_is_the_merge_base_with_main_off_main(history: tuple[Path, str]) -> None:
    tree, base = history
    git(tree, "checkout", "-q", "-b", "feature")
    commit(tree, "work")

    assert release_base(tree) == base


def test_the_base_on_main_is_the_parent_commit(history: tuple[Path, str]) -> None:
    tree, base = history
    commit(tree, "next")

    assert release_base(tree) == base


def test_without_a_main_branch_the_base_is_an_error(tree: Path) -> None:
    git(tree, "init", "-q", "-b", "trunk")
    commit(tree, "release")

    with pytest.raises(ReleaseError, match="no main branch"):
        release_base(tree)


def test_a_base_that_is_not_a_commit_is_reported(history: tuple[Path, str]) -> None:
    tree, _ = history

    assert release_issues(tree, base="no-such-ref") == [
        "release base 'no-such-ref' is not a commit"
    ]
