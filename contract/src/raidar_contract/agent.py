"""Transport-neutral agent definitions and content identity.

An agent binds an adapter identifier, tool pin, instruction placement, resume support and model
catalogue. Settings and capability declarations are consumer-defined schema parameters; the lab's
Harbor ids, isolation requirements and admission rules are not part of this distribution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from raidar_contract.canonical import digest_json

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


class Placement(_Model):
    """Where a workflow's skills and instructions go for this harness (``{home}``: its home)."""

    skills: Text
    instructions: Text


class ModelEntry(_Model):
    """One model the agent may run: the id passed to the CLI and its reasoning levels."""

    canonical: Text
    reasoning: tuple[Text, ...] = ()


class AgentDefinition[Settings, Capability](_Model):
    schema_: Literal[1] = Field(alias="schema")
    name: str = Field(pattern=NAME_PATTERN)
    adapter: Text
    tool: ToolPin
    settings: Settings
    placement: Placement
    resume: bool
    models: dict[Text, ModelEntry] = Field(min_length=1)
    capabilities: Capability

    def to_json(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True)

    @property
    def digest(self) -> str:
        """Content hash of the resolved definition: what a release registers."""
        return digest_json(self.to_json())


def agent_path(root: Path, name: str) -> Path:
    return root / f"{name}{AGENT_SUFFIX}"


def load_agent(
    root: Path,
    name: str,
    *,
    definition_type: type[AgentDefinition[Any, Any]] = AgentDefinition[
        dict[str, JsonValue], dict[str, JsonValue]
    ],
) -> AgentDefinition[Any, Any]:
    """Load and validate ``<root>/<name>.yaml``; its ``name`` must equal the file name."""
    path = agent_path(root, name)
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        definition = definition_type.model_validate(document)
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
