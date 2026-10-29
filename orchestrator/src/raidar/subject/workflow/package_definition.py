"""Package workflows (``schema: 2``): a build frame and an ordered list of released packages.

::

    schema: 2
    name: one-shot-verify
    build:
      prompt: "...%%%VERIFY%%%...%%%TASK%%%"
    stages:                              # order = setup, gate and stop order
      - {use: raidar/verify@1, params: {test_timeout: 90}}
    gate: {max_fix_rounds: 0}

``use`` is a released ``<ns>/<name>@N`` (a bare name, a name without a namespace, ``latest``, a
branch or a path is refused here, when the workflow is parsed). The file is the whole workflow:
skills, tools and prompts
belong to the packages. The definition's own digest covers this resolved file; the workflow's
identity (``package_workflow_digest``) also folds in each package's release, digest and effective
params, so a package edit or a param change moves the identity of every workflow that uses it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from raidar.contract.canonical import digest_json
from raidar.contract.releases import STAGE_RELEASE_PATTERN, Release
from raidar.subject.package.manifest import ParamValue
from raidar.subject.workflow.definition import NAME_PATTERN, TASK_TOKEN, WORKFLOW_FILE

PACKAGE_SCHEMA = 2
FOLD_SCHEME = "workflow-packages/1"


class PackageWorkflowError(ValueError):
    """A package workflow definition is missing or malformed."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StageUse(_Model):
    """One package the workflow runs: its release reference and its param overrides."""

    use: Annotated[str, Field(pattern=STAGE_RELEASE_PATTERN)]
    params: dict[str, ParamValue] = Field(default_factory=dict)

    @property
    def release(self) -> Release:
        return Release.parse_stage(self.use)


class PackageBuild(_Model):
    prompt: str = Field(min_length=1)

    @model_validator(mode="after")
    def _one_task_token(self) -> Self:
        count = self.prompt.count(TASK_TOKEN)
        if count != 1:
            raise ValueError(f"build.prompt must contain {TASK_TOKEN} exactly once (found {count})")
        return self


class PackageGate(_Model):
    max_fix_rounds: int = Field(default=0, ge=0)


class PackageWorkflowDefinition(_Model):
    schema_: Literal[2] = Field(alias="schema")
    name: str = Field(pattern=NAME_PATTERN)
    build: PackageBuild
    stages: tuple[StageUse, ...] = ()
    gate: PackageGate = Field(default_factory=PackageGate)

    @model_validator(mode="after")
    def _each_package_once(self) -> Self:
        names = [use.release.name for use in self.stages]
        if len(set(names)) != len(names):
            raise ValueError(f"a package is listed twice: {sorted(names)}")
        return self

    @property
    def enabled_stages(self) -> tuple[str, ...]:
        """The package names this workflow runs, in order."""
        return tuple(use.release.name for use in self.stages)

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    @property
    def digest(self) -> str:
        """Hash of the resolved file alone; the workflow's identity also folds the packages."""
        return digest_json(self.to_json())


def package_workflow_digest(
    definition: PackageWorkflowDefinition, packages: Sequence[Mapping[str, object]]
) -> str:
    """The workflow's identity: its file plus each package's ``{use, sha256, params}`` in order."""
    return digest_json(
        {
            "scheme": FOLD_SCHEME,
            "definition": definition.model_dump(mode="json", by_alias=True, exclude={"stages"}),
            "packages": [dict(entry) for entry in packages],
        }
    )


def load_package_workflow(folder: Path, name: str) -> PackageWorkflowDefinition:
    """``<folder>/workflow.yaml`` of a ``schema: 2`` workflow; nothing else may be in the folder."""
    path = folder / WORKFLOW_FILE
    stray = sorted(item.name for item in folder.iterdir() if item.name != WORKFLOW_FILE)
    if stray:
        raise PackageWorkflowError(
            f"{folder}: a package workflow holds only {WORKFLOW_FILE}: {stray}"
        )
    try:
        definition = PackageWorkflowDefinition.model_validate(
            yaml.safe_load(path.read_text(encoding="utf-8"))
        )
    except (OSError, yaml.YAMLError) as exc:
        raise PackageWorkflowError(f"{path}: unreadable workflow definition: {exc}") from exc
    except ValidationError as exc:
        raise PackageWorkflowError(f"{path}: invalid workflow definition: {exc}") from exc
    if definition.name != name:
        raise PackageWorkflowError(f"{path}: name {definition.name!r} must equal its directory")
    return definition
