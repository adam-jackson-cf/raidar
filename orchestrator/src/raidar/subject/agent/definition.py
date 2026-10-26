"""Agent definitions: ``lab/agents/<name>.yaml``, released as ``<name>@N``.

An agent is a harness Raidar has an adapter for, at a pinned CLI version, with the models
(and each model's reasoning levels) an experiment may run it with. The pin is the single
source of the harness version: the runtime pack's baked-in pin must equal it (RT5).

``settings`` states the isolation the adapter applies and cannot be configured away: the
CLI runs with its own home created for the run and removed after it, and no user config
file reaches it. ``placement`` says where a workflow's skills and instructions are put for
this harness. ``resume`` says whether a turn can continue the previous turn's session.

The content hash covers the resolved definition (every default filled), so editing the file
needs a new release in ``lab/releases.yaml``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from raidar.contract.canonical import digest_json
from raidar.subject.agent.adapters.factory import adapter_class_for_harness
from raidar.subject.agent.adapters.harbor_cli import HarborCliAdapter
from raidar.subject.agent.config import AgentName, Harness, agent_cli

AGENT_SCHEMA = 1
AGENT_SUFFIX = ".yaml"
PINNED_VERSION_PATTERN = r"^[0-9]+(\.[0-9]+)*([-+][0-9A-Za-z][0-9A-Za-z.-]*)?$"
NAME_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
Text = Annotated[str, Field(min_length=1)]


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
    """Isolation the adapter applies on every run (stated, never switched off)."""

    isolated_home: Literal[True]
    ignore_user_config: Literal[True]


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

    @model_validator(mode="after")
    def _adapter_supports_models(self) -> Self:
        adapter = harbor_adapter(self.adapter)
        for key, entry in self.models.items():
            profile = adapter.SUPPORTED_MODELS.get(entry.canonical)
            if profile is None:
                raise ValueError(
                    f"models.{key}: {adapter.ADAPTER_LABEL} has no model {entry.canonical!r}"
                )
            unsupported = sorted(set(entry.reasoning) - set(profile.reasoning_levels))
            if unsupported:
                raise ValueError(
                    f"models.{key}: {adapter.ADAPTER_LABEL} allows reasoning "
                    f"{list(profile.reasoning_levels)} for {entry.canonical!r}, not {unsupported}"
                )
        return self

    @property
    def harness(self) -> Harness:
        """The CLI install id (the runtime pack's harness key)."""
        return agent_cli(self.adapter)

    @property
    def provider(self) -> str:
        return harbor_adapter(self.adapter).REQUIRED_PROVIDER

    def to_json(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True)

    @property
    def digest(self) -> str:
        """Content hash of the resolved definition: what a release registers."""
        return digest_json(self.to_json())


def harbor_adapter(name: AgentName) -> type[HarborCliAdapter]:
    """The adapter class of a Harbor-installed agent; refuse any other adapter kind."""
    adapter = adapter_class_for_harness(agent_cli(name))
    if not issubclass(adapter, HarborCliAdapter):
        raise ValueError(f"adapter {name.value!r} is not a Harbor-installed agent adapter")
    return adapter


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
