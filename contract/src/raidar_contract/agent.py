"""Agent definitions: ``lab/agents/<name>.yaml``, released as ``<name>@N``.

An agent is a harness Raidar has an adapter for, at a pinned CLI version, with the models
(and each model's reasoning levels) an experiment may run it with. The pin is the single
source of the harness version: the runtime pack's baked-in pin must equal it (RT5).

``settings`` states the isolation the adapter applies and cannot be configured away: the
CLI runs with its own home created for the run (holding only its sign-in) and removed after
it, no user config file reaches it, the CLI features named in ``features_off`` are switched
off on every command, and the skills bundled with the pinned CLI (``bundled_skills``) are
switched off by path. ``placement`` says where a workflow's skills and instructions are put
for this harness. ``resume`` says whether a turn can continue the previous turn's session.

``capabilities`` declares what the harness gives an agent, by the fixed names of
``Capabilities`` (factory-x-loops' harness parity rule, ``harness/config.py``
``CAPABILITIES``), each with the mechanism this CLI delivers it by. A definition declares
every name or is refused; arms whose declarations differ are refused by pooling and smoke
admission, so two arms differ only in what an experiment varies.

The content hash covers the resolved definition (every default filled), so editing the file
needs a new release in ``lab/releases.yaml``.

This module is the pure schema. Whether an adapter exists for ``adapter`` and supports each
model is a runtime fact the orchestrator checks (``raidar.subject.agent.definition``).
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from raidar_contract.canonical import digest_json

AGENT_SCHEMA = 1
AGENT_SUFFIX = ".yaml"
PINNED_VERSION_PATTERN = r"^[0-9]+(\.[0-9]+)*([-+][0-9A-Za-z][0-9A-Za-z.-]*)?$"
NAME_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
FEATURE_PATTERN = r"^[a-z][a-z0-9_]*$"
"""A CLI feature name as ``codex features list`` prints it."""
SKILL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
"""A bundled skill's folder name."""
Text = Annotated[str, Field(min_length=1)]


class AgentName(StrEnum):
    """Harbor ``AgentName`` ids an experiment's agent entry may use."""

    CODEX = "codex"
    CLAUDE_CODE = "claude-code"


class AgentDefinitionError(ValueError):
    """An agent definition is missing, malformed or not supported by its adapter."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolPin(_Model):
    """The agent CLI: its npm package, executable and pinned release."""

    package: Text
    binary: Text
    version: str = Field(pattern=PINNED_VERSION_PATTERN)


class Isolation(_Model):
    """Isolation the adapter applies on every command (stated, never switched off)."""

    isolated_home: Literal[True]
    ignore_user_config: Literal[True]
    features_off: tuple[Annotated[str, Field(pattern=FEATURE_PATTERN)], ...] = ()
    bundled_skills: tuple[Annotated[str, Field(pattern=SKILL_PATTERN)], ...] = ()

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        for name, values in (
            ("features_off", self.features_off),
            ("bundled_skills", self.bundled_skills),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"settings.{name} must not repeat a name")
        return self


class Capabilities(_Model):
    """The capability declaration: every ``CAPABILITIES`` name with this CLI's mechanism."""

    permissions: Text
    network: Text
    workflow_skills: Text
    global_instructions: Text
    host_isolation: Text
    isolation_proof: Literal["full", "partial"]
    command_output: Text
    structured_output: Text
    version_pin: Text


class Placement(_Model):
    """Where a workflow's skills and instructions go for this harness (``{home}``: its home)."""

    skills: Text
    instructions: Text


class ModelEntry(_Model):
    """One model the agent may run: the id passed to the CLI and its reasoning levels."""

    canonical: Text
    reasoning: tuple[Text, ...] = ()


class AgentDefinition(_Model):
    schema_: Literal[1] = Field(alias="schema")
    name: str = Field(pattern=NAME_PATTERN)
    adapter: AgentName
    tool: ToolPin
    settings: Isolation
    placement: Placement
    resume: bool
    models: dict[Text, ModelEntry] = Field(min_length=1)
    capabilities: Capabilities

    @model_validator(mode="after")
    def _pin_declared(self) -> Self:
        if self.capabilities.version_pin != self.tool.version:
            raise ValueError(
                f"capabilities.version_pin {self.capabilities.version_pin!r} must equal "
                f"tool.version {self.tool.version!r}"
            )
        return self

    def to_json(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True)

    @property
    def digest(self) -> str:
        """Content hash of the resolved definition: what a release registers."""
        return digest_json(self.to_json())


def agent_path(root: Path, name: str) -> Path:
    return root / f"{name}{AGENT_SUFFIX}"


def load_agent(root: Path, name: str) -> AgentDefinition:
    """Load and validate ``<root>/<name>.yaml``; its ``name`` must equal the file name."""
    path = agent_path(root, name)
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        definition = AgentDefinition.model_validate(document)
    except (OSError, yaml.YAMLError) as exc:
        raise AgentDefinitionError(f"{path}: unreadable agent definition: {exc}") from exc
    except ValidationError as exc:
        raise AgentDefinitionError(f"{path}: invalid agent definition: {exc}") from exc
    if definition.name != name:
        raise AgentDefinitionError(f"{path}: name {definition.name!r} must equal the file name")
    return definition


def agent_names(root: Path) -> list[str]:
    """Every agent defined under ``root``."""
    return sorted(path.stem for path in root.glob(f"*{AGENT_SUFFIX}"))
