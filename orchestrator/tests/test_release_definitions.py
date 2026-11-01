"""Agent and workflow definitions: what the loaders refuse."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from raidar_contract.agent import AgentDefinitionError
from raidar_contract.workflow import (
    TASK_TOKEN,
    PackageWorkflowError,
    load_package_workflow,
)

from raidar.contract.paths import agents_root, workflows_root
from raidar.subject.agent.definition import load_agent
from tests.release_support import edit_agent, release_tree, write_workflow

ONE_SHOT = "raidar/one-shot"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return release_tree(tmp_path)


def _workflow(**over: Any) -> dict[str, Any]:
    return {"schema": 2, "name": "one-shot", "build": {"prompt": f"do {TASK_TOKEN}"}, **over}


def _load(tree: Path) -> Any:
    return load_package_workflow(workflows_root(tree) / ONE_SHOT, ONE_SHOT)


# ---- workflows -----------------------------------------------------------------------


def test_a_workflow_that_is_not_schema_2_is_refused(tree: Path) -> None:
    write_workflow(tree, {"schema": 1, "name": "one-shot", "stages": {}})

    with pytest.raises(PackageWorkflowError, match="invalid workflow definition"):
        _load(tree)


def test_a_workflow_with_an_unknown_key_is_refused(tree: Path) -> None:
    write_workflow(tree, _workflow(polish={"prompt": "x"}))

    with pytest.raises(PackageWorkflowError, match="invalid workflow definition"):
        _load(tree)


@pytest.mark.parametrize(
    "prompt", ["no task here", f"{TASK_TOKEN} twice {TASK_TOKEN}"], ids=["zero", "two"]
)
def test_a_build_prompt_needs_the_task_token_exactly_once(tree: Path, prompt: str) -> None:
    write_workflow(tree, _workflow(build={"prompt": prompt}))

    with pytest.raises(PackageWorkflowError, match="exactly once"):
        _load(tree)


def test_a_workflow_without_a_build_prompt_is_refused(tree: Path) -> None:
    document = _workflow()
    del document["build"]
    write_workflow(tree, document)

    with pytest.raises(PackageWorkflowError, match="build"):
        _load(tree)


def test_a_package_listed_twice_is_refused(tree: Path) -> None:
    use = {"use": "acme/check@1"}
    write_workflow(tree, _workflow(stages=[use, use]))

    with pytest.raises(PackageWorkflowError, match="listed twice"):
        _load(tree)


def test_a_workflow_folder_holds_only_its_definition(tree: Path) -> None:
    write_workflow(tree, _workflow())
    (workflows_root(tree) / ONE_SHOT / "notes.md").write_text("x")

    with pytest.raises(PackageWorkflowError, match=r"notes\.md"):
        _load(tree)


def test_a_workflow_must_be_named_for_its_directory(tree: Path) -> None:
    write_workflow(tree, _workflow(name="other"))

    with pytest.raises(PackageWorkflowError, match="must equal its directory"):
        _load(tree)


# ---- agents --------------------------------------------------------------------------


_AGENT_REFUSALS: dict[str, tuple[Callable[[dict[str, Any]], object], str]] = {
    "model the adapter does not offer": (
        lambda d: d["models"]["gpt-6-luna"].update(canonical="gpt-nonexistent"),
        "gpt-nonexistent",
    ),
    "reasoning level the adapter does not offer": (
        lambda d: d["models"]["gpt-6-luna"].update(reasoning=["low", "ultra"]),
        "ultra",
    ),
    "isolated home switched off": (
        lambda d: d["settings"].update(isolated_home=False),
        "isolated_home",
    ),
    "user config not ignored": (
        lambda d: d["settings"].update(ignore_user_config=False),
        "ignore_user_config",
    ),
    "unknown adapter": (lambda d: d.update(adapter="ghost-cli"), "adapter"),
    "name differs from the file": (lambda d: d.update(name="other"), "must equal the file name"),
    "unpinned tool version": (lambda d: d["tool"].update(version="latest"), "version"),
    "missing permissions": (lambda d: d["capabilities"].pop("permissions"), "permissions"),
    "missing host isolation": (
        lambda d: d["capabilities"].pop("host_isolation"),
        "host_isolation",
    ),
    "missing isolation proof": (
        lambda d: d["capabilities"].pop("isolation_proof"),
        "isolation_proof",
    ),
    "capability outside the fixed names": (
        lambda d: d["capabilities"].update(telepathy="yes"),
        "telepathy",
    ),
    "version pin differing from the tool pin": (
        lambda d: d["capabilities"].update(version_pin="0.1.0"),
        "version_pin",
    ),
    "isolation proof neither full nor partial": (
        lambda d: d["capabilities"].update(isolation_proof="some"),
        "isolation_proof",
    ),
    "repeated feature": (
        lambda d: d["settings"].update(features_off=["plugins", "plugins"]),
        "features_off",
    ),
    "feature that is not a name": (
        lambda d: d["settings"].update(features_off=["Plugins"]),
        "features_off",
    ),
    "bundled skill that is not a folder name": (
        lambda d: d["settings"].update(bundled_skills=["../escape"]),
        "bundled_skills",
    ),
}


@pytest.mark.parametrize("case", sorted(_AGENT_REFUSALS))
def test_an_agent_definition_that_breaks_a_rule_is_refused(tree: Path, case: str) -> None:
    mutate, match = _AGENT_REFUSALS[case]
    edit_agent(tree, mutate)

    with pytest.raises(AgentDefinitionError, match=match):
        load_agent(agents_root(tree), "codex")
