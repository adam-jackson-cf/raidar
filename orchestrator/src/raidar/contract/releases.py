"""The release register: ``lab/releases.yaml`` maps ``name@N`` to a content hash.

Agents and workflows are edited in place under ``lab/agents/`` and ``lab/workflows/``; the
version is data, never part of a file name. Releasing a definition records its content hash
under the next integer version of its name::

    agents:
      codex@1: <sha256 of the resolved definition>
    workflows:
      one-shot@1: <sha256 of the resolved definition>

Earlier versions stay in the register as history; the tree holds only the definition of each
name's latest version, so only that version resolves (older ones live in archive tags).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

RELEASE_PATTERN = r"^[a-z0-9][a-z0-9-]*@[1-9][0-9]*$"
_RELEASE_RE = re.compile(RELEASE_PATTERN)
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ReleaseKind(StrEnum):
    AGENT = "agents"
    WORKFLOW = "workflows"


class ReleaseError(ValueError):
    """A release reference is malformed, unregistered or not the one the tree holds."""


@dataclass(frozen=True, order=True)
class Release:
    """A parsed ``name@N`` release reference."""

    name: str
    version: int

    @classmethod
    def parse(cls, text: str) -> Release:
        if not _RELEASE_RE.fullmatch(text):
            raise ReleaseError(f"invalid release {text!r}: expected <name>@<N> (N from 1)")
        name, version = text.split("@", 1)
        return cls(name, int(version))

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"


class _Register(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agents: dict[str, Sha256] = Field(default_factory=dict)
    workflows: dict[str, Sha256] = Field(default_factory=dict)


@dataclass(frozen=True)
class ReleaseRegister:
    """Every registered release, by kind: ``{Release: content hash}``."""

    entries: Mapping[ReleaseKind, Mapping[Release, str]]

    def of(self, kind: ReleaseKind) -> Mapping[Release, str]:
        return self.entries.get(kind, {})

    def latest(self, kind: ReleaseKind, name: str) -> Release | None:
        """The highest registered version of ``name``: the one the tree must hold."""
        versions = [release for release in self.of(kind) if release.name == name]
        return max(versions, default=None)

    def names(self, kind: ReleaseKind) -> frozenset[str]:
        return frozenset(release.name for release in self.of(kind))


def load_register(path: Path) -> ReleaseRegister:
    """Parse the register; raise ``ReleaseError`` naming the first malformed entry."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        register = _Register.model_validate(document)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ReleaseError(f"{path}: unreadable release register: {exc}") from exc
    entries = {
        ReleaseKind.AGENT: {Release.parse(ref): digest for ref, digest in register.agents.items()},
        ReleaseKind.WORKFLOW: {
            Release.parse(ref): digest for ref, digest in register.workflows.items()
        },
    }
    return ReleaseRegister(entries)
