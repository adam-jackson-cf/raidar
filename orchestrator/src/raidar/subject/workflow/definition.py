"""Workflow definitions: ``lab/workflows/<name>/workflow.yaml``, released as ``<name>@N``.

A workflow names the stages a checkpoint runs through. There are three stage types
(``define``, ``build``, ``verify``), each with one implementation in Raidar; a workflow holds
its own settings for each stage it uses. This Raidar implements ``build`` and ``verify``: a
definition that names ``define`` is refused, never accepted as inert.

``build.prompt`` frames the checkpoint's one prompt: ``%%%TASK%%%`` (exactly once) is replaced
by the checkpoint's task text and, when ``verify`` is enabled, ``%%%VERIFY%%%`` (exactly once)
by the verify stage's prompt; a workflow without verify may not carry ``%%%VERIFY%%%``.

``verify`` (design section 5.2, spike-sized) gates the work through a pre-commit hook and the
runner's own commit after every turn: ``checks`` are the gate's checks (one ``tests`` check,
which is pytest with at least one test and none failing; the others run their pinned tool's
command over the workspace's Python files), ``max_fix_rounds`` bounds the fix turns a refusal
starts, ``test_timeout`` stops a single test, and ``rules_file`` is where the gate's rules are
written for the agent. Its ``prompt`` may use the tokens ``%%%MAX_FIX_ROUNDS%%%``,
``%%%RULES_FILE%%%`` and ``%%%TEST_TIMEOUT_SECONDS%%%`` and no other.

A workflow directory holds only ``workflow.yaml`` while no implemented stage delivers files
(skills, instructions). The content hash covers the resolved definition (every default
filled; a stage the workflow does not name is absent), so editing the file needs a new release
in ``lab/releases.yaml``.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from raidar.contract.canonical import IGNORED_TREE_NAMES, digest_json

WORKFLOW_FILE = "workflow.yaml"
StageType = Literal["define", "build", "verify"]
STAGE_TYPES: tuple[StageType, ...] = ("define", "build", "verify")
IMPLEMENTED_STAGES: tuple[StageType, ...] = ("build", "verify")
TASK_TOKEN = "%%%TASK%%%"
VERIFY_TOKEN = "%%%VERIFY%%%"
VERIFY_PROMPT_TOKENS = frozenset({"MAX_FIX_ROUNDS", "RULES_FILE", "TEST_TIMEOUT_SECONDS"})
NAME_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
PIN_PATTERN = r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==[0-9][A-Za-z0-9.+!-]*$"
TOKEN = re.compile(r"%%%([A-Z_]+)%%%")
CheckCategory = Literal["lint", "format", "types", "tests"]


class WorkflowDefinitionError(ValueError):
    """A workflow definition is missing, malformed or uses a stage this Raidar lacks."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BuildSettings(_Model):
    prompt: str = Field(min_length=1)

    @model_validator(mode="after")
    def _one_task_token(self) -> Self:
        count = self.prompt.count(TASK_TOKEN)
        if count != 1:
            raise ValueError(f"build.prompt must contain {TASK_TOKEN} exactly once (found {count})")
        return self


class GateCheck(_Model):
    """One gate check: a pinned tool and, unless it is the tests check, the command it runs."""

    name: str = Field(pattern=NAME_PATTERN)
    category: CheckCategory
    tool: str = Field(pattern=PIN_PATTERN)
    run: str | None = None
    blocking: bool

    @model_validator(mode="after")
    def _command_runs_the_pinned_tool(self) -> Self:
        if self.category == "tests":
            if self.run is not None:
                raise ValueError(f"check {self.name!r}: the tests check is pytest and takes no run")
            if self.package != "pytest":
                raise ValueError(f"check {self.name!r}: the tests check's tool is pytest")
            return self
        argv = shlex.split(self.run or "")
        if not argv or argv[0] != self.package:
            raise ValueError(f"check {self.name!r}: run must start with its tool {self.package!r}")
        return self

    @property
    def package(self) -> str:
        found = re.match(PIN_PATTERN, self.tool)
        assert found is not None  # the field's pattern
        return found["name"]

    @property
    def argv(self) -> list[str]:
        return shlex.split(self.run or "")


class GateTriggers(_Model):
    """When the gate runs: on the agent's own commits (the hook) and after every turn (the seal)."""

    on_commit: Literal[True]
    after_turn: Literal[True]


class VerifySettings(_Model):
    enabled: bool
    language: Literal["python"]
    prompt: str = Field(min_length=1)
    gate: GateTriggers
    max_fix_rounds: int = Field(ge=0)
    max_passes: Literal[1]
    test_timeout: int = Field(gt=0)
    rules_file: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    checks: tuple[GateCheck, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        unknown = sorted(set(TOKEN.findall(self.prompt)) - VERIFY_PROMPT_TOKENS)
        if unknown:
            raise ValueError(f"verify.prompt uses unknown token(s) {unknown}")
        names = [check.name for check in self.checks]
        if len(set(names)) != len(names):
            raise ValueError(f"verify.checks repeats a name: {names}")
        tests = [check for check in self.checks if check.category == "tests"]
        if len(tests) != 1 or not tests[0].blocking:
            raise ValueError("verify.checks needs exactly one blocking tests check")
        return self

    def render(self) -> str:
        """The verify prompt with its tokens filled from these settings."""
        values = {
            "MAX_FIX_ROUNDS": str(self.max_fix_rounds),
            "RULES_FILE": self.rules_file,
            "TEST_TIMEOUT_SECONDS": str(self.test_timeout),
        }
        return TOKEN.sub(lambda found: values[found[1]], self.prompt)

    @property
    def pins(self) -> list[str]:
        """Every tool pin the gate installs, once each, sorted."""
        return sorted({check.tool for check in self.checks})


class Stages(_Model):
    build: BuildSettings
    verify: VerifySettings | None = None

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

    @model_validator(mode="after")
    def _verify_token_iff_verify(self) -> Self:
        count = self.build.prompt.count(VERIFY_TOKEN)
        expected = 1 if self.verify is not None and self.verify.enabled else 0
        if count != expected:
            raise ValueError(
                f"build.prompt must contain {VERIFY_TOKEN} {expected} time(s) "
                f"(found {count}): exactly once when verify is enabled, never otherwise"
            )
        return self


class WorkflowDefinition(_Model):
    schema_: Literal[1] = Field(alias="schema")
    name: str = Field(pattern=NAME_PATTERN)
    stages: Stages

    @property
    def enabled_stages(self) -> tuple[StageType, ...]:
        """The stage types this workflow runs, in stage order."""
        verify = self.stages.verify
        return ("build", "verify") if verify is not None and verify.enabled else ("build",)

    def to_json(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

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
