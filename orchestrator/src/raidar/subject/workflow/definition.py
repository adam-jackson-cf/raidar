"""Workflow definitions: ``lab/workflows/<name>/workflow.yaml``, released as ``<name>@N``.

A workflow names the stages a checkpoint runs through. There are three stage types
(``define``, ``build``, ``verify``), each with one implementation in Raidar; a workflow holds
its own settings for each stage it uses. This Raidar implements ``build`` only: a definition
that names any other stage is refused, never accepted as inert.

``build.prompt`` is the text of the checkpoint's build turn; ``%%%TASK%%%`` (exactly once)
is replaced by the checkpoint's task text. A workflow directory holds only ``workflow.yaml``
while no implemented stage delivers files (skills, instructions).

The content hash covers the resolved definition (every default filled), so editing the file
needs a new release in ``lab/releases.yaml``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from raidar.contract.canonical import IGNORED_TREE_NAMES, digest_json

WORKFLOW_FILE = "workflow.yaml"
StageType = Literal["define", "build", "verify"]
STAGE_TYPES: tuple[StageType, ...] = ("define", "build", "verify")
IMPLEMENTED_STAGES: tuple[StageType, ...] = ("build",)
TASK_TOKEN = "%%%TASK%%%"
NAME_PATTERN = r"^[a-z0-9][a-z0-9-]*$"


class WorkflowDefinitionError(ValueError):
    """A workflow definition is missing, malformed or uses a stage this Raidar lacks."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BuildStage(_Model):
    prompt: str = Field(min_length=1)

    @model_validator(mode="after")
    def _one_task_token(self) -> Self:
        count = self.prompt.count(TASK_TOKEN)
        if count != 1:
            raise ValueError(f"build.prompt must contain {TASK_TOKEN} exactly once (found {count})")
        return self

    def render(self, task: str) -> str:
        """The build turn's prompt for one checkpoint's task text."""
        return self.prompt.replace(TASK_TOKEN, task)


class Stages(_Model):
    build: BuildStage

    @model_validator(mode="before")
    @classmethod
    def _implemented_only(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for name in data:
                if name not in STAGE_TYPES:
                    raise ValueError(
                        f"unknown stage type {name!r}; stage types are {', '.join(STAGE_TYPES)}"
                    )
                if name not in IMPLEMENTED_STAGES:
                    raise ValueError(
                        f"stage type {name!r} is not implemented in this Raidar; "
                        f"a workflow may use only {', '.join(IMPLEMENTED_STAGES)}"
                    )
        return data


class WorkflowDefinition(_Model):
    schema_: Literal[1] = Field(alias="schema")
    name: str = Field(pattern=NAME_PATTERN)
    stages: Stages

    @property
    def enabled_stages(self) -> tuple[StageType, ...]:
        """The stage types this workflow runs, in stage order."""
        return IMPLEMENTED_STAGES

    def to_json(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True)

    @property
    def digest(self) -> str:
        """Content hash of the resolved definition: what a release registers."""
        return digest_json(self.to_json())


def workflow_dir(root: Path, name: str) -> Path:
    return root / name


def load_workflow(root: Path, name: str) -> WorkflowDefinition:
    """Load and validate ``<root>/<name>/workflow.yaml``; its ``name`` must equal the dir."""
    folder = workflow_dir(root, name)
    path = folder / WORKFLOW_FILE
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        definition = WorkflowDefinition.model_validate(document)
    except (OSError, yaml.YAMLError) as exc:
        raise WorkflowDefinitionError(f"{path}: unreadable workflow definition: {exc}") from exc
    except ValidationError as exc:
        raise WorkflowDefinitionError(f"{path}: invalid workflow definition: {exc}") from exc
    if definition.name != name:
        raise WorkflowDefinitionError(f"{path}: name {definition.name!r} must equal its directory")
    extra = sorted(
        relative
        for item in folder.rglob("*")
        if item.is_file() and item != path
        for relative in [item.relative_to(folder).as_posix()]
        if not IGNORED_TREE_NAMES.intersection(relative.split("/"))
    )
    if extra:
        raise WorkflowDefinitionError(
            f"{folder}: holds {extra}; no implemented stage delivers workflow files yet"
        )
    return definition


def workflow_names(root: Path) -> list[str]:
    """Every workflow defined under ``root``."""
    return sorted(path.parent.name for path in root.glob(f"*/{WORKFLOW_FILE}"))
