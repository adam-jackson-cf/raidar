"""Harbor adapter ids and Raidar's agent isolation/admission policy."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator
from raidar_contract.agent import AgentDefinition as PortableAgentDefinition
from raidar_contract.agent import Text
from raidar_contract.agent import load_agent as load_portable_agent

FEATURE_PATTERN = r"^[a-z][a-z0-9_]*$"
SKILL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class AgentName(StrEnum):
    """Harbor agent ids the lab can execute."""

    CODEX = "codex"
    CLAUDE_CODE = "claude-code"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Isolation(_Model):
    """Isolation the adapter applies on every command (never configurable away)."""

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
    """The lab's required capability declarations and isolation-proof level."""

    permissions: Text
    network: Text
    workflow_skills: Text
    global_instructions: Text
    host_isolation: Text
    isolation_proof: Literal["full", "partial"]
    command_output: Text
    structured_output: Text
    version_pin: Text


class AgentDefinition(PortableAgentDefinition[Isolation, Capabilities]):
    """The portable agent schema specialized to this lab's fixed policy."""

    adapter: AgentName

    @model_validator(mode="after")
    def _pin_declared(self) -> Self:
        if self.capabilities.version_pin != self.tool.version:
            raise ValueError(
                f"capabilities.version_pin {self.capabilities.version_pin!r} must equal "
                f"tool.version {self.tool.version!r}"
            )
        return self


def load_agent(root: Path, name: str) -> AgentDefinition:
    """Read an agent with the lab's unchanged acceptance/refusal policy."""
    return cast(AgentDefinition, load_portable_agent(root, name, definition_type=AgentDefinition))
