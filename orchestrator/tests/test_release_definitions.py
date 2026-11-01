"""Agent and workflow definitions: what the loaders refuse."""

from __future__ import annotations

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
from tests.release_support import REAL_LAB, edit_agent, release_tree, write_workflow

ONE_SHOT = "raidar/one-shot"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return release_tree(tmp_path)


def _workflow(**over: Any) -> dict[str, Any]:
    return {"schema": 2, "name": "one-shot", "build": {"prompt": f"do {TASK_TOKEN}"}, **over}


def _load(tree: Path) -> Any:
    return load_package_workflow(workflows_root(tree) / ONE_SHOT, ONE_SHOT)


def test_the_real_definitions_load(tree: Path) -> None:
    assert load_agent(agents_root(tree), "codex").name == "codex"
    assert _load(tree).enabled_stages == ()
    shipped = REAL_LAB / "workflows" / "raidar"
    verifying = load_package_workflow(shipped / "one-shot-verify", "raidar/one-shot-verify")
    assert verifying.enabled_stages == ("raidar/verify",)
    defining = load_package_workflow(shipped / "define-build-verify", "raidar/define-build-verify")
    assert defining.enabled_stages == ("raidar/define", "raidar/verify")
    assert defining.judge is not None
    assert defining.build.reply == "acceptance-reply/1"


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


def test_an_agent_model_the_adapter_does_not_offer_is_refused(tree: Path) -> None:
    edit_agent(tree, lambda doc: doc["models"]["gpt-6-luna"].update(canonical="gpt-nonexistent"))

    with pytest.raises(AgentDefinitionError, match="gpt-nonexistent"):
        load_agent(agents_root(tree), "codex")


def test_an_agent_reasoning_level_the_adapter_does_not_offer_is_refused(tree: Path) -> None:
    edit_agent(tree, lambda doc: doc["models"]["gpt-6-luna"].update(reasoning=["low", "ultra"]))

    with pytest.raises(AgentDefinitionError, match="ultra"):
        load_agent(agents_root(tree), "codex")


@pytest.mark.parametrize("flag", ["isolated_home", "ignore_user_config"])
def test_isolation_settings_cannot_be_switched_off(tree: Path, flag: str) -> None:
    edit_agent(tree, lambda doc: doc["settings"].update({flag: False}))

    with pytest.raises(AgentDefinitionError, match=flag):
        load_agent(agents_root(tree), "codex")


def test_an_unknown_adapter_is_refused(tree: Path) -> None:
    edit_agent(tree, lambda doc: doc.update(adapter="ghost-cli"))

    with pytest.raises(AgentDefinitionError, match="adapter"):
        load_agent(agents_root(tree), "codex")


def test_an_agent_must_be_named_for_its_file(tree: Path) -> None:
    edit_agent(tree, lambda doc: doc.update(name="other"))

    with pytest.raises(AgentDefinitionError, match="must equal the file name"):
        load_agent(agents_root(tree), "codex")


def test_an_agent_tool_version_must_be_pinned(tree: Path) -> None:
    edit_agent(tree, lambda doc: doc["tool"].update(version="latest"))

    with pytest.raises(AgentDefinitionError, match="version"):
        load_agent(agents_root(tree), "codex")


@pytest.mark.parametrize("name", ["permissions", "host_isolation", "isolation_proof"])
def test_an_agent_must_declare_every_capability(tree: Path, name: str) -> None:
    edit_agent(tree, lambda doc: doc["capabilities"].pop(name))

    with pytest.raises(AgentDefinitionError, match=name):
        load_agent(agents_root(tree), "codex")


def test_an_agent_declares_no_capability_outside_the_fixed_names(tree: Path) -> None:
    edit_agent(tree, lambda doc: doc["capabilities"].update(telepathy="yes"))

    with pytest.raises(AgentDefinitionError, match="telepathy"):
        load_agent(agents_root(tree), "codex")


def test_the_declared_version_pin_is_the_tool_pin(tree: Path) -> None:
    edit_agent(tree, lambda doc: doc["capabilities"].update(version_pin="0.1.0"))

    with pytest.raises(AgentDefinitionError, match="version_pin"):
        load_agent(agents_root(tree), "codex")


def test_an_isolation_proof_is_full_or_partial(tree: Path) -> None:
    edit_agent(tree, lambda doc: doc["capabilities"].update(isolation_proof="some"))

    with pytest.raises(AgentDefinitionError, match="isolation_proof"):
        load_agent(agents_root(tree), "codex")


@pytest.mark.parametrize(
    ("setting", "value"),
    [("features_off", ["plugins", "plugins"]), ("features_off", ["Plugins"]),
     ("bundled_skills", ["../escape"])],
    ids=["repeated", "not-a-feature-name", "not-a-folder-name"],
)  # fmt: skip
def test_isolation_name_lists_are_checked(tree: Path, setting: str, value: list[str]) -> None:
    edit_agent(tree, lambda doc: doc["settings"].update({setting: value}))

    with pytest.raises(AgentDefinitionError, match=setting):
        load_agent(agents_root(tree), "codex")
