"""The release register: what ``name@N`` resolves to, what drift it refuses, and the check."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from raidar_contract.releases import Release, ReleaseError, ReleaseKind, parse_register

from raidar.cli import main
from raidar.contract.paths import agents_root
from raidar.subject.agent.definition import load_agent
from raidar.subject.release_check import release_issues
from raidar.subject.releases import released_agent, released_workflow
from tests.release_support import edit_agent, release_tree, write_register, write_workflow


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return release_tree(tmp_path)


def test_the_registered_latest_releases_resolve(tree: Path) -> None:
    assert str(released_agent("codex@1", repo_root=tree).release) == "codex@1"
    assert released_workflow("raidar/one-shot@1", repo_root=tree).definition.name == "one-shot"


@pytest.mark.parametrize("ref", ["ghost@1", "codex@2"])
def test_an_unregistered_agent_release_is_refused(tree: Path, ref: str) -> None:
    with pytest.raises(ReleaseError, match="not in lab/releases.yaml"):
        released_agent(ref, repo_root=tree)


def test_an_unregistered_workflow_release_is_refused(tree: Path) -> None:
    with pytest.raises(ReleaseError, match="not in lab/releases.yaml"):
        released_workflow("raidar/one-shot@2", repo_root=tree)


@pytest.mark.parametrize("ref", ["one-shot@1", "one-shot-verify@3", "define-build-verify@4"])
def test_a_bare_workflow_reference_never_resolves_though_frozen_in_the_register(
    tree: Path, ref: str
) -> None:
    path = tree / "lab" / "releases.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["workflows"][ref] = "0" * 64
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ReleaseError, match="a namespace is required"):
        released_workflow(ref, repo_root=tree)
    with pytest.raises(ReleaseError, match="a namespace is required"):
        released_workflow(ref, repo_root=tree, draft=True)
    assert release_issues(tree) == []


def test_the_register_accepts_a_bare_workflow_only_as_history_not_as_a_tree_name() -> None:
    register = parse_register(f"workflows:\n  old@1: {'a' * 64}\n  raidar/new@1: {'b' * 64}\n", "t")

    assert register.names(ReleaseKind.WORKFLOW) == {"raidar/new"}
    assert Release("old", 1) in register.of(ReleaseKind.WORKFLOW)


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
        {"schema": 2, "name": "one-shot", "build": {"prompt": "Different %%%TASK%%%"}},
    )

    with pytest.raises(ReleaseError, match=r"changed since raidar/one-shot@1 was released"):
        released_workflow("raidar/one-shot@1", repo_root=tree)


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
        {"schema": 2, "name": "one-shot", "build": {"prompt": "Different %%%TASK%%%"}},
    )

    issues = release_issues(tree)

    assert len(issues) == 1
    assert "workflows/raidar/one-shot changed since raidar/one-shot@1" in issues[0]
    assert "raidar/one-shot@2" in issues[0]


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
