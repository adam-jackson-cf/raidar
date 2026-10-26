"""The release register: what ``name@N`` resolves to, what drift it refuses, and the check."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.contract.paths import agents_root
from raidar.contract.releases import ReleaseError
from raidar.subject.agent.definition import load_agent
from raidar.subject.releases import release_issues, released_agent, released_workflow
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


@pytest.mark.parametrize("ref", ["codex", "codex@0", "codex@one", "Codex@1", "@1", "codex@01"])
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


# ---- raidar releases check -----------------------------------------------------------------


def test_releases_check_passes_on_the_real_tree() -> None:
    result = CliRunner().invoke(main, ["releases", "check"])

    assert result.exit_code == 0, result.output
    assert "releases: ok" in result.output


def test_releases_check_exits_nonzero_naming_each_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "raidar.cli.releases.release_issues", lambda: ["agents/codex changed since codex@1"]
    )

    result = CliRunner().invoke(main, ["releases", "check"])

    assert result.exit_code == 1
    assert "agents/codex changed since codex@1" in result.output
