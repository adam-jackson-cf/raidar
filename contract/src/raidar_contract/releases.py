"""The release register: ``lab/releases.yaml`` maps ``name@N`` to a content hash.

Agents and workflows are edited in place under ``lab/agents/`` and ``lab/workflows/``; stage
packages live under ``lab/stages/<ns>/<name>/``. The version is data, never part of a file
name. Releasing a definition records its content hash under the next integer version of its
name::

    agents:
      codex@1: <sha256 of the resolved definition>
    workflows:
      one-shot@1: <sha256 of the resolved definition>
    stages:
      docs-check@1: <sha256 stage-tree/1 digest of the package>

Earlier versions stay in the register as history; the tree holds only the definition of each
name's latest version, so only that version resolves (older ones live in archive tags). The
register is append-only: a released ``name@N`` keeps its hash forever (``rewritten_releases``).
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

RELEASE_PATTERN = r"^(?:[a-z0-9][a-z0-9-]*/)?[a-z0-9][a-z0-9-]*@[1-9][0-9]*$"
STAGE_RELEASE_PATTERN = r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*@[1-9][0-9]*$"
_RELEASE_RE = re.compile(RELEASE_PATTERN)
_STAGE_RELEASE_RE = re.compile(STAGE_RELEASE_PATTERN)
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ReleaseKind(StrEnum):
    AGENT = "agents"
    WORKFLOW = "workflows"
    STAGE = "stages"


class ReleaseError(ValueError):
    """A release reference is malformed, unregistered or not the one the tree holds."""


@dataclass(frozen=True, order=True)
class Release:
    """A parsed immutable release reference: ``name@N``, or ``ns/name@N`` for a stage."""

    name: str
    version: int

    @classmethod
    def parse(cls, text: str) -> Release:
        if not _RELEASE_RE.fullmatch(text):
            raise ReleaseError(f"invalid release {text!r}: expected <name>@<N> (N from 1)")
        name, version = text.split("@", 1)
        return cls(name, int(version))

    @classmethod
    def parse_stage(cls, text: str) -> Release:
        """A stage reference: the namespace is required (``<ns>/<name>@<N>``)."""
        if not _STAGE_RELEASE_RE.fullmatch(text):
            raise ReleaseError(
                f"invalid stage release {text!r}: expected <ns>/<name>@<N> "
                "(a namespace is required; N from 1)"
            )
        return cls.parse(text)

    @property
    def leaf(self) -> str:
        return self.name.rsplit("/", 1)[-1]

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"


class _Register(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agents: dict[str, Sha256] = Field(default_factory=dict)
    workflows: dict[str, Sha256] = Field(default_factory=dict)
    stages: dict[str, Sha256] = Field(default_factory=dict)


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


def parse_register(text: str, source: str) -> ReleaseRegister:
    """Parse register ``text`` (read from ``source``); ``ReleaseError`` names a malformed entry."""
    try:
        document = yaml.safe_load(text) or {}
        register = _Register.model_validate(document)
    except (yaml.YAMLError, ValidationError) as exc:
        raise ReleaseError(f"{source}: unreadable release register: {exc}") from exc
    entries = {
        ReleaseKind.AGENT: {Release.parse(ref): digest for ref, digest in register.agents.items()},
        ReleaseKind.WORKFLOW: {
            Release.parse(ref): digest for ref, digest in register.workflows.items()
        },
        ReleaseKind.STAGE: {
            Release.parse_stage(ref): digest for ref, digest in register.stages.items()
        },
    }
    return ReleaseRegister(entries)


def load_register(path: Path) -> ReleaseRegister:
    """Parse the register file; raise ``ReleaseError`` naming the first malformed entry."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseError(f"{path}: unreadable release register: {exc}") from exc
    return parse_register(text, str(path))


def resolve_registered(
    register: ReleaseRegister, kind: ReleaseKind, text: str, *, draft: bool = False
) -> tuple[Release, str | None]:
    """The parsed release ``text`` and its registered hash.

    Only the register's latest version of a name resolves. A ``draft`` (smoke experiments only)
    may name an unregistered or older release: its hash is ``None`` or whatever is registered.
    """
    release = Release.parse_stage(text) if kind is ReleaseKind.STAGE else Release.parse(text)
    registered = register.of(kind).get(release)
    if registered is None:
        if draft:
            return release, None
        raise ReleaseError(f"{kind.value} release {text} is not in lab/releases.yaml")
    latest = register.latest(kind, release.name)
    if latest != release and not draft:
        raise ReleaseError(
            f"{text} is not the {kind.value} release the tree holds ({latest}); "
            "earlier releases live in archive tags"
        )
    return release, registered


def changed_message(release: Release, registered: str, now: str) -> str:
    """Why a release no longer matches its register entry, naming the next release to add."""
    return (
        f"{release.name} changed since {release} was released (registered {registered[:12]}, "
        f"now {now[:12]}): register {release.name}@{release.version + 1}"
    )


def rewritten_releases(earlier: ReleaseRegister, now: ReleaseRegister) -> list[str]:
    """Every release of ``earlier`` that ``now`` removed or re-hashed (released means frozen)."""
    issues = []
    for kind in ReleaseKind:
        current = now.of(kind)
        for release, digest in sorted(earlier.of(kind).items()):
            if release not in current:
                issues.append(f"{kind.value} release {release} was removed: releases are frozen")
            elif current[release] != digest:
                issues.append(
                    f"{kind.value} release {release} was re-hashed ({digest[:12]} -> "
                    f"{current[release][:12]}): releases are frozen; register "
                    f"{release.name}@<next> instead"
                )
    return issues
