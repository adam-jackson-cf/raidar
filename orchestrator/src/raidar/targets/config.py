"""Execution targets: which Docker engine runs trials and graders (mechanics-v1 §3.1).

A target is a named Docker engine, read from ``~/.config/raidar/targets.toml``
(override: ``RAIDAR_TARGETS_FILE``). With no file and nothing selected, the
*implicit* target is whatever ``DOCKER_CONTEXT`` or ``docker context show``
names. Resolution is lazy: only commands that touch Docker call it.

Selection precedence: ``--target NAME`` > ``RAIDAR_TARGET`` > the file's
``default`` > the implicit target.
"""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

ENV_TARGETS_FILE = "RAIDAR_TARGETS_FILE"
ENV_TARGET = "RAIDAR_TARGET"
IMPLICIT_TARGET_NAME = "default"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*$")
_DOCKER_TIMEOUT_S = 20

TARGETS_TEMPLATE = """\
# Raidar execution targets. `raidar target status` shows the selected one.
# Selection: --target NAME (or TARGET=NAME with make) > $RAIDAR_TARGET > `default`.
default = "local"

[targets.local]
kind = "docker"
docker_context = "orbstack"               # a `docker context ls` name
# data_check = ["orbstack-data-status"]   # argv run on this host; must exit 0
# data_check_expect = "external_available=True"   # and print this, when set
# max_concurrency = 3                     # caps the plan's concurrency

# A remote Docker daemon reached over an SSH-forwarded unix socket. Raidar
# keeps the tunnel up (`raidar target up|down|ensure`) and owns the Docker
# context `raidar-mbp`. Remote targets are untrusted unless `trusted = true`:
# agent credentials and hidden tests only go to trusted targets.
# [targets.mbp]
# kind = "docker"
# trusted = true
# max_concurrency = 3
# grader = "mbp"                          # target that runs hidden-test graders (default: itself)
#
# [targets.mbp.transport]
# kind = "ssh-socket"
# host = "user@host"
# identity = "~/.ssh/key"                 # optional
# remote_socket = "/Users/user/.orbstack/run/docker.sock"
"""


class TargetError(RuntimeError):
    """A targets file or a target selection that cannot be used."""


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TransportSpec(_Strict):
    kind: Literal["ssh-socket"]
    host: str = Field(min_length=1)
    identity: str | None = None
    remote_socket: str = Field(min_length=1)


class TargetSpec(_Strict):
    kind: Literal["docker"] = "docker"
    docker_context: str | None = None
    trusted: bool | None = None
    max_concurrency: int | None = Field(default=None, ge=1)
    grader: str | None = None
    data_check: tuple[str, ...] = ()
    data_check_expect: str | None = None
    transport: TransportSpec | None = None


class TargetsFileSpec(_Strict):
    default: str | None = None
    targets: dict[str, TargetSpec] = Field(default_factory=dict)


@dataclass(frozen=True)
class Transport:
    host: str
    identity: str | None
    remote_socket: str


@dataclass(frozen=True)
class Target:
    name: str
    kind: str
    docker_context: str
    trusted: bool
    max_concurrency: int | None
    grader: str
    data_check: tuple[str, ...]
    data_check_expect: str | None
    transport: Transport | None

    @property
    def supports_bind_mounts(self) -> bool:
        """Host bind mounts resolve on the daemon's host: true only for a local daemon.

        Temporary guard until the copy-only path (mechanics-v1 steps 2-3) lands.
        """
        return self.transport is None

    def effective_concurrency(self, requested: int) -> int:
        return requested if self.max_concurrency is None else min(requested, self.max_concurrency)


@dataclass(frozen=True)
class Selection:
    """The target trials run on and the target that runs the hidden-test grader."""

    trial: Target
    grader: Target

    @classmethod
    def of(cls, target: Target) -> Selection:
        return cls(trial=target, grader=target)


@dataclass(frozen=True)
class TargetBook:
    targets: Mapping[str, Target]
    default: str | None
    source: Path | None

    def get(self, name: str) -> Target:
        found = self.targets.get(name)
        if found is None:
            defined = ", ".join(sorted(self.targets)) or "none (no targets file)"
            raise TargetError(f"unknown target {name!r}; defined targets: {defined}")
        return found


def targets_file() -> Path:
    override = os.environ.get(ENV_TARGETS_FILE)
    return Path(override).expanduser() if override else Path.home() / ".config/raidar/targets.toml"


def implicit_target(docker_context: str) -> Target:
    """A trusted, check-free, transport-free target for an existing Docker context."""
    return Target(
        name=IMPLICIT_TARGET_NAME,
        kind="docker",
        docker_context=docker_context,
        trusted=True,
        max_concurrency=None,
        grader=IMPLICIT_TARGET_NAME,
        data_check=(),
        data_check_expect=None,
        transport=None,
    )


def _field_errors(path: Path, error: ValidationError) -> TargetError:
    lines = [
        f"  {'.'.join(str(part) for part in item['loc']) or '<file>'}: {item['msg']}"
        for item in error.errors()
    ]
    return TargetError(f"invalid targets file {path}:\n" + "\n".join(lines))


def _build(name: str, spec: TargetSpec) -> Target:
    if not _NAME.match(name):
        raise TargetError(f"targets.{name}: names use letters, digits, '-' and '_'")
    transport = None
    if spec.transport is not None:
        if spec.docker_context is not None:
            raise TargetError(
                f"targets.{name}: docker_context cannot be set with a transport "
                f"(Raidar owns the context raidar-{name})"
            )
        t = spec.transport
        transport = Transport(t.host, t.identity, t.remote_socket)
        context = f"raidar-{name}"
    elif spec.docker_context is None:
        raise TargetError(f"targets.{name}: set docker_context or a transport")
    else:
        context = spec.docker_context
    return Target(
        name=name,
        kind=spec.kind,
        docker_context=context,
        trusted=spec.trusted if spec.trusted is not None else transport is None,
        max_concurrency=spec.max_concurrency,
        grader=spec.grader or name,
        data_check=spec.data_check,
        data_check_expect=spec.data_check_expect,
        transport=transport,
    )


def parse_targets(text: str, source: Path) -> TargetBook:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise TargetError(f"invalid targets file {source}: {error}") from error
    try:
        spec = TargetsFileSpec.model_validate(raw)
    except ValidationError as error:
        raise _field_errors(source, error) from error
    targets = {name: _build(name, item) for name, item in spec.targets.items()}
    for target in targets.values():
        if target.grader not in targets:
            raise TargetError(
                f"targets.{target.name}.grader: {target.grader!r} is not a defined target"
            )
    if spec.default is not None and spec.default not in targets:
        raise TargetError(f"default: {spec.default!r} is not a defined target")
    return TargetBook(targets, spec.default, source)


def load_targets(path: Path | None = None) -> TargetBook:
    """The targets file, or an empty book when there is none."""
    path = path or targets_file()
    if not path.is_file():
        return TargetBook({}, None, None)
    return parse_targets(path.read_text(), path)


def current_docker_context() -> str:
    """``DOCKER_CONTEXT``, else what ``docker context show`` reports."""
    from_env = os.environ.get("DOCKER_CONTEXT")
    if from_env:
        return from_env
    try:
        shown = subprocess.run(
            ["docker", "context", "show"],  # noqa: S607 - docker resolves from PATH
            capture_output=True,
            text=True,
            timeout=_DOCKER_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise TargetError(f"cannot read the current Docker context: {error}") from error
    context = shown.stdout.strip()
    if shown.returncode != 0 or not context:
        raise TargetError(f"`docker context show` failed: {shown.stderr.strip() or 'no output'}")
    return context


def resolve_target(name: str | None = None, *, book: TargetBook | None = None) -> Target:
    """Apply the selection precedence; the implicit target when nothing selects one."""
    book = book or load_targets()
    chosen = name or os.environ.get(ENV_TARGET) or book.default
    if chosen:
        return book.get(chosen)
    return implicit_target(current_docker_context())


def resolve_selection(name: str | None = None, *, book: TargetBook | None = None) -> Selection:
    book = book or load_targets()
    trial = resolve_target(name, book=book)
    grader = trial if trial.grader == trial.name else book.get(trial.grader)
    return Selection(trial=trial, grader=grader)


def activate(target: Target) -> None:
    """Point this process's Docker CLI (and Harbor's Compose children) at ``target``."""
    os.environ["DOCKER_CONTEXT"] = target.docker_context
    os.environ.pop("DOCKER_HOST", None)


def require_bind_mounts(target: Target, purpose: str) -> None:
    """Refuse work that bind-mounts host paths on a remote daemon (they would resolve there)."""
    if not target.supports_bind_mounts:
        raise TargetError(
            f"target {target.name!r} is a remote daemon; host bind mounts would resolve on the "
            f"remote host, so it cannot be used for {purpose}. Remote targets need the "
            "copy-only path (mechanics-v1 steps 2-3)"
        )


def require_trusted(target: Target, purpose: str) -> None:
    """Credentials and hidden tests only go to trusted targets."""
    if not target.trusted:
        raise TargetError(
            f"target {target.name!r} is not trusted, so it cannot be used for {purpose}; "
            f"set targets.{target.name}.trusted = true in {targets_file()} to allow it"
        )
