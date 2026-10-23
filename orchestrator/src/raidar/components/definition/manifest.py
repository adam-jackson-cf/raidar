"""Component manifest and definition protocol (F5, plan WS-C C1).

A component is a pluggable starting condition of one of five kinds. Its
manifest declares what it is and what it needs:

- ``kind``: workspace, harness, prompt, workflow or service;
- ``supports``: the harnesses it has a mapping (and a discovery probe) for;
- ``network``: egress hosts the component itself needs (empty means none);
- ``secrets``: host secret names it needs, checked by presence only;
- ``fingerprint``: the files whose content identifies the implementation;
- ``discovery_probe``: how a trial proves the component took effect.

A definition pairs the manifest with ``contribute`` (the Harbor inputs it adds
for one use on one harness) and ``probe`` (the check over one step's evidence).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from raidar.agents.config import Harness

if TYPE_CHECKING:
    from raidar.components.definition.contribution import Contribution
    from raidar.components.definition.evidence import StepEvidence


class ComponentKind(StrEnum):
    WORKSPACE = "workspace"
    HARNESS = "harness"
    PROMPT = "prompt"
    WORKFLOW = "workflow"
    SERVICE = "service"


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NetworkNeeds(ManifestModel):
    """Egress the component adds to the trial allowlist (N2: network is allowlisted)."""

    egress_hosts: tuple[str, ...] = ()


class DiscoveryProbe(ManifestModel):
    """What evidence proves the component took effect in a step."""

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence: tuple[str, ...] = Field(min_length=1)


class ComponentManifest(ManifestModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    version: str = Field(pattern=r"^[0-9]+$")
    kind: ComponentKind
    description: str = Field(min_length=1)
    supports: tuple[Harness, ...] = Field(min_length=1)
    network: NetworkNeeds = NetworkNeeds()
    secrets: tuple[str, ...] = ()
    fingerprint: tuple[str, ...] = Field(
        min_length=1,
        description="Package-relative source files whose bytes identify this implementation",
    )
    discovery_probe: DiscoveryProbe

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


class ComponentConfigError(ValueError):
    """A use of a component is invalid for its definition or harness."""


@dataclass(frozen=True)
class ComponentUseContext:
    """One use of a component in a condition, resolved against the experiment dir."""

    slug: str
    harness: Harness
    base_dir: Path
    files: tuple[str, ...] = ()
    with_: Mapping[str, JsonValue] = field(default_factory=dict)

    def file_path(self, relative: str) -> Path:
        return self.base_dir / relative


@dataclass(frozen=True)
class ProbeOutcome:
    passed: bool
    detail: str
    evidence: tuple[str, ...] = ()


@runtime_checkable
class ComponentDefinition(Protocol):
    """A built-in or third-party component implementation."""

    manifest: ComponentManifest

    def contribute(self, use: ComponentUseContext) -> Contribution:
        """Harbor inputs for this use; raise ``ComponentConfigError`` when invalid."""
        ...

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        """Check one step's evidence that this use took effect."""
        ...
