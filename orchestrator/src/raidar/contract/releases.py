"""The release register: ``lab/releases.yaml`` maps ``name@N`` to a content hash.

Agents live under ``lab/agents/``, workflows under ``lab/workflows/<ns>/<name>/`` and stage
packages under ``lab/stages/<ns>/<name>/``. The version is data, never part of a file
name. Releasing a definition records its content hash under the next integer version of its
name::

    agents:
      codex@1: <sha256 of the resolved definition>
    workflows:
      raidar/one-shot@1: <sha256 of the resolved definition>
    stages:
      docs-check@1: <sha256 stage-tree/1 digest of the package>

Earlier versions stay in the register as history; the tree holds only the definition of each
name's latest version, so only that version resolves. A definition is edited in place: its
registered hash is updated to the current bytes (the check prints the line to put in the
register), then the affected experiments are relocked and rerun. A registered ``name@N`` may be
re-hashed but never removed (``removed_releases``). Archive tags are optional history.

Workflow and stage references require a namespace (``<ns>/<name>@<N>``). Bare workflow entries
of the pre-namespace register stay as frozen history: they parse, never resolve (active
references always use ``Release.parse_workflow``) and name no tree definition.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from raidar_contract.agent import AgentDefinitionError
from raidar_contract.package import StagePackageError
from raidar_contract.references import Release, ReleaseError
from raidar_contract.workflow import PackageWorkflowError

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ReleaseKind(StrEnum):
    AGENT = "agents"
    WORKFLOW = "workflows"
    STAGE = "stages"


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
        """The names that must have a definition in the tree (a bare workflow is history)."""
        return frozenset(
            release.name
            for release in self.of(kind)
            if kind is not ReleaseKind.WORKFLOW or "/" in release.name
        )


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


def _parse_reference(kind: ReleaseKind, text: str) -> Release:
    """An active reference: stages and workflows need their namespace."""
    if kind is ReleaseKind.STAGE:
        return Release.parse_stage(text)
    if kind is ReleaseKind.WORKFLOW:
        return Release.parse_workflow(text)
    return Release.parse(text)


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
    release = _parse_reference(kind, text)
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
    """Why a release no longer matches its register entry, naming the line to update."""
    return (
        f"{release.name} changed since {release} was registered (registered {registered[:12]}, "
        f"now {now[:12]}): update {release}: {now} in lab/releases.yaml, then relock"
    )


def removed_releases(earlier: ReleaseRegister, now: ReleaseRegister) -> list[str]:
    """Every release of ``earlier`` that ``now`` removed (re-hashing in place is allowed)."""
    return [
        f"{kind.value} release {release} was removed: registered versions stay as history"
        for kind in ReleaseKind
        for release in sorted(earlier.of(kind))
        if release not in now.of(kind)
    ]


def registration_issues(
    kind: ReleaseKind,
    names: list[str],
    digest_of: Callable[[str], str],
    register: ReleaseRegister,
) -> list[str]:
    issues = []
    for name in names:
        try:
            digest = digest_of(name)
        except (
            AgentDefinitionError,
            PackageWorkflowError,
            StagePackageError,
            ReleaseError,
            OSError,
        ) as exc:
            issues.append(str(exc))
            continue
        latest = register.latest(kind, name)
        if latest is None:
            issues.append(f"{kind.value}/{name} is not released: register {name}@1: {digest}")
        elif register.of(kind)[latest] != digest:
            issues.append(
                f"{kind.value}/{name} changed since {latest} was registered: update "
                f"{latest}: {digest}"
            )
    issues.extend(
        f"{kind.value} release name {name!r} is registered but not defined in the tree"
        for name in sorted(register.names(kind) - set(names))
    )
    return issues
