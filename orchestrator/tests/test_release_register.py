"""The release register: what ``name@N`` resolves to, what drift it refuses, and the check."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from raidar_contract.releases import Release, ReleaseError

from raidar.cli import main
from raidar.contract.paths import agents_root
from raidar.subject.agent.definition import load_agent
from raidar.subject.release_check import release_base, release_issues
from raidar.subject.releases import released_agent, released_workflow
from tests.release_support import edit_agent, release_tree, write_register, write_workflow


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return release_tree(tmp_path)


def test_the_registered_latest_releases_resolve(tree: Path) -> None:
    assert str(released_agent("codex@1", repo_root=tree).release) == "codex@1"
    assert released_workflow("one-shot@1", repo_root=tree).definition.name == "one-shot"


@pytest.mark.parametrize("ref", ["ghost@1", "codex@2"])
def test_an_unregistered_agent_release_is_refused(tree: Path, ref: str) -> None:
    with pytest.raises(ReleaseError, match="not in lab/releases.yaml"):
        released_agent(ref, repo_root=tree)


def test_an_unregistered_workflow_release_is_refused(tree: Path) -> None:
    with pytest.raises(ReleaseError, match="not in lab/releases.yaml"):
        released_workflow("one-shot@2", repo_root=tree)


def test_only_the_latest_version_of_a_name_resolves(tree: Path) -> None:
    digest = load_agent(agents_root(tree), "codex").digest
    write_register(tree, extra_agents={"codex@2": digest})

    assert str(released_agent("codex@2", repo_root=tree).release) == "codex@2"
    with pytest.raises(ReleaseError, match=r"codex@1 is not .* the tree holds \(codex@2\)"):
        released_agent("codex@1", repo_root=tree)


def test_a_definition_changed_since_its_release_is_refused(tree: Path) -> None:
    edit_agent(tree, lambda doc: doc["models"]["gpt-6-luna"].update(reasoning=[]))

    with pytest.raises(ReleaseError, match="changed since codex@1 was released"):
        released_agent("codex@1", repo_root=tree)


def test_a_workflow_changed_since_its_release_is_refused(tree: Path) -> None:
    write_workflow(
        tree,
        {"schema": 1, "name": "one-shot", "stages": {"build": {"prompt": "Different %%%TASK%%%"}}},
    )

    with pytest.raises(ReleaseError, match="changed since one-shot@1 was released"):
        released_workflow("one-shot@1", repo_root=tree)


def test_a_namespaced_release_keeps_its_namespace_and_leaf_distinct() -> None:
    release = Release.parse("raidar/review-notes@12")
    assert release.name == "raidar/review-notes"
    assert release.leaf == "review-notes"
    assert release.version == 12


@pytest.mark.parametrize(
    "ref",
    [
        "codex",
        "codex@0",
        "codex@one",
        "Codex@1",
        "@1",
        "codex@01",
        "../codex@1",
        "/codex@1",
        "a/b/c@1",
        "a//b@1",
        "codex@latest",
        "codex@main",
    ],
)
def test_a_malformed_release_reference_is_refused(tree: Path, ref: str) -> None:
    with pytest.raises(ReleaseError, match="invalid release"):
        released_agent(ref, repo_root=tree)


def test_a_definition_that_no_longer_loads_is_a_release_error(tree: Path) -> None:
    edit_agent(tree, lambda doc: doc["settings"].update(isolated_home=False))

    with pytest.raises(ReleaseError, match="invalid agent definition"):
        released_agent("codex@1", repo_root=tree)


# ---- release_issues --------------------------------------------------------------------


def test_a_consistent_tree_has_no_issues(tree: Path) -> None:
    assert release_issues(tree) == []


def test_a_definition_with_no_registration_is_reported(tree: Path) -> None:
    shutil.copy(agents_root(tree) / "codex.yaml", agents_root(tree) / "extra.yaml")
    edit_agent_named = agents_root(tree) / "extra.yaml"
    edit_agent_named.write_text(
        edit_agent_named.read_text(encoding="utf-8").replace("name: codex", "name: extra"),
        encoding="utf-8",
    )

    issues = release_issues(tree)

    assert len(issues) == 1
    assert "agents/extra is not released" in issues[0]
    assert "extra@1" in issues[0]


def test_hash_drift_is_reported_with_the_next_version(tree: Path) -> None:
    write_workflow(
        tree,
        {"schema": 1, "name": "one-shot", "stages": {"build": {"prompt": "Different %%%TASK%%%"}}},
    )

    issues = release_issues(tree)

    assert len(issues) == 1
    assert "workflows/one-shot changed since one-shot@1" in issues[0]
    assert "one-shot@2" in issues[0]


def test_a_registered_name_with_no_definition_is_reported(tree: Path) -> None:
    write_register(tree, extra_agents={"ghost@1": "0" * 64})

    issues = release_issues(tree)

    assert len(issues) == 1
    assert "'ghost'" in issues[0]


def test_an_agent_pin_that_differs_from_the_runtime_pack_is_reported(tree: Path) -> None:
    def repin(doc: dict) -> None:
        doc["tool"].update(version="0.0.1")
        doc["capabilities"].update(version_pin="0.0.1")

    edit_agent(tree, repin)
    write_register(tree)  # registered at the new hash: only the pack pin disagrees

    issues = release_issues(tree)

    assert len(issues) == 1
    assert "agents/codex" in issues[0]
    assert "differs from pack" in issues[0]
    assert "0.0.1" in issues[0]


def test_an_unreadable_register_is_reported(tree: Path) -> None:
    (tree / "lab" / "releases.yaml").write_text("agents: {codex@1: not-a-hash}\n", encoding="utf-8")

    issues = release_issues(tree)

    assert len(issues) == 1
    assert "unreadable release register" in issues[0]


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


# ---- raidar releases check -----------------------------------------------------------------


def test_releases_check_passes_on_the_real_tree() -> None:
    result = CliRunner().invoke(main, ["releases", "check"])

    assert result.exit_code == 0, result.output
    assert "releases: ok" in result.output


def test_releases_check_exits_nonzero_naming_each_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("raidar.cli.releases.release_base", lambda: None)
    monkeypatch.setattr(
        "raidar.cli.releases.release_issues", lambda **_: ["agents/codex changed since codex@1"]
    )

    result = CliRunner().invoke(main, ["releases", "check"])

    assert result.exit_code == 1
    assert "agents/codex changed since codex@1" in result.output
