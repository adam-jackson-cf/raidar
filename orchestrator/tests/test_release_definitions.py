"""Agent and workflow definitions: what the loaders refuse."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any

import pytest

from raidar.contract.paths import agents_root, workflows_root
from raidar.subject.agent.definition import AgentDefinitionError, load_agent
from raidar.subject.workflow.components import PlacementError, skill_files
from raidar.subject.workflow.definition import (
    TASK_TOKEN,
    UNITS_TOKEN,
    VERIFY_TOKEN,
    WorkflowDefinition,
    WorkflowDefinitionError,
    load_workflow,
)
from tests.release_support import REAL_LAB, edit_agent, release_tree, write_workflow


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return release_tree(tmp_path)


def _workflow(stages: dict[str, Any], *, name: str = "one-shot") -> dict[str, Any]:
    return {"schema": 1, "name": name, "stages": stages}


def _build(prompt: str) -> dict[str, Any]:
    return {"build": {"prompt": prompt}}


def test_the_real_definitions_load(tree: Path) -> None:
    assert load_agent(agents_root(tree), "codex").name == "codex"
    assert load_workflow(workflows_root(tree), "one-shot").enabled_stages == ("build",)
    verifying = load_workflow(REAL_LAB / "workflows", "one-shot-verify")
    assert verifying.enabled_stages == ("build", "verify")
    defining = load_workflow(REAL_LAB / "workflows", "define-build-verify")
    assert defining.enabled_stages == ("define", "build", "verify")


# ---- workflows -----------------------------------------------------------------------


def test_a_workflow_with_an_unknown_stage_is_refused(tree: Path) -> None:
    write_workflow(tree, _workflow({**_build(f"do {TASK_TOKEN}"), "polish": {"prompt": "x"}}))

    with pytest.raises(WorkflowDefinitionError, match="unknown stage type 'polish'"):
        load_workflow(workflows_root(tree), "one-shot")


def test_the_define_prompt_needs_the_units_token_once() -> None:
    real = load_workflow(REAL_LAB / "workflows", "define-build-verify")
    stages: Any = copy.deepcopy(real.to_json()["stages"])
    stages["define"]["prompt"] = "no units"

    with pytest.raises(ValueError, match=UNITS_TOKEN):
        WorkflowDefinition.model_validate(
            {"schema": 1, "name": "x", "stages": stages, "components": real.components}
        )


@pytest.mark.parametrize("enabled", [True, False])
def test_the_verify_token_is_in_the_build_prompt_exactly_when_verify_runs(
    tree: Path, enabled: bool
) -> None:
    real = load_workflow(REAL_LAB / "workflows", "one-shot-verify").to_json()
    stages: Any = real["stages"]
    stages["verify"]["enabled"] = enabled
    without = f"do {TASK_TOKEN}"
    stages["build"]["prompt"] = without if enabled else f"{VERIFY_TOKEN}\n{without}"
    write_workflow(tree, _workflow(stages))

    with pytest.raises(WorkflowDefinitionError, match=VERIFY_TOKEN):
        load_workflow(workflows_root(tree), "one-shot")


@pytest.mark.parametrize(
    "prompt", ["no task here", f"{TASK_TOKEN} twice {TASK_TOKEN}"], ids=["zero", "two"]
)
def test_a_build_prompt_needs_the_task_token_exactly_once(tree: Path, prompt: str) -> None:
    write_workflow(tree, _workflow(_build(prompt)))

    with pytest.raises(WorkflowDefinitionError, match="exactly once"):
        load_workflow(workflows_root(tree), "one-shot")


def test_a_workflow_without_a_build_stage_is_refused(tree: Path) -> None:
    write_workflow(tree, _workflow({}))

    with pytest.raises(WorkflowDefinitionError):
        load_workflow(workflows_root(tree), "one-shot")


def test_a_workflow_file_outside_its_skills_is_refused(tree: Path) -> None:
    write_workflow(tree, _workflow(_build(f"do {TASK_TOKEN}")))
    (workflows_root(tree) / "one-shot" / "notes.md").write_text("extra\n", encoding="utf-8")

    with pytest.raises(WorkflowDefinitionError, match=r"notes\.md"):
        load_workflow(workflows_root(tree), "one-shot")


def test_a_skill_no_stage_lists_is_refused(tree: Path) -> None:
    write_workflow(tree, _workflow(_build(f"do {TASK_TOKEN}")))
    skill = workflows_root(tree) / "one-shot" / "skills" / "orphan"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("x\n", encoding="utf-8")

    with pytest.raises(WorkflowDefinitionError, match="orphan"):
        load_workflow(workflows_root(tree), "one-shot")


def test_the_skill_bytes_are_part_of_the_workflow_hash(tmp_path: Path) -> None:
    root = tmp_path / "workflows"
    shutil.copytree(REAL_LAB / "workflows", root)
    before = load_workflow(root, "define-build-verify")
    skill = root / "define-build-verify" / "skills" / "aspect-writing" / "SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert load_workflow(root, "define-build-verify").digest != before.digest


def test_skills_are_placed_for_the_enabled_stages_only(tmp_path: Path) -> None:
    definition = load_workflow(REAL_LAB / "workflows", "define-build-verify")

    placed = skill_files(definition, ".agents/skills")

    assert sorted(path.split("/")[2] for path in placed) == [
        "aspect-writing",
        "feature-definition",
        "pytest-templates",
        "test-per-aspect",
    ]
    assert all(path.endswith("SKILL.md") for path in placed)
    with pytest.raises(PlacementError):
        skill_files(definition, "{home}/skills")


def test_a_workflow_must_be_named_for_its_directory(tree: Path) -> None:
    write_workflow(tree, _workflow(_build(f"do {TASK_TOKEN}"), name="other"))

    with pytest.raises(WorkflowDefinitionError, match="must equal its directory"):
        load_workflow(workflows_root(tree), "one-shot")


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
