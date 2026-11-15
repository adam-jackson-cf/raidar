"""``install/1``: a stage package's tool tree, installed root-owned, offline and hash-checked.

Host side: the package's regular shipped bytes and the hash-pinned wheels of its ``uv.lock``
(``declared-wheels/1``: wheels only, Linux CPython 3.12, the container's architecture) are
staged and uploaded; the root payload verifies every digest, copies the raw source as data,
makes a venv from the pack's pinned interpreter, installs the wheels with ``uv --offline
--no-index --require-hashes --only-binary :all:`` and writes command wrappers. Nothing authored
runs as root and nothing is built or downloaded in the container. The installed tree's digest
is recorded host-side and checked before and after every authored call (``verify``).
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from raidar.contract.wheel_cache import fetch_locked
from raidar.contract.wheels import (
    LOCKED_FLAGS,
    OFFLINE_FLAGS,
    LockedWheel,
    WheelPolicyError,
    hashed_requirements,
    uv_lock_wheels,
)
from raidar.subject.primitive import calls
from raidar.subject.primitive.payload.supervise import BASE_PATH

LOCK_PATH = "tool/uv.lock"
TOOL_PREFIX = "tool/"
LINKS_DIR = "/usr/local/bin"
INSTALL_TIMEOUT_S = 900
_ARCHES = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
_REFERENCE = re.compile(r"[a-z0-9][a-z0-9-]*(/[a-z0-9][a-z0-9-]*)?")


class InstallError(RuntimeError):
    """The package cannot be installed (policy refusal or a failed root install); not infra."""


class ShippedFile(Protocol):
    """One regular file of the package (``StageFile``)."""

    @property
    def path(self) -> str: ...

    @property
    def sha256(self) -> str: ...

    @property
    def executable(self) -> bool: ...

    @property
    def data(self) -> bytes: ...


@dataclass(frozen=True)
class ShippedPackage:
    """A package to install or measure in a container.

    ``reference`` is ``<ns>/<name>``; ``files`` are the package's shipped bytes and ``commands``
    its tool's commands. ``owner`` is the required owner uid of the installed tree (``None``
    skips the check, tests only).
    """

    reference: str
    package_digest: str
    files: Sequence[ShippedFile] = ()
    commands: Mapping[str, Sequence[str]] = field(default_factory=dict)
    owner: int | None = 0


@dataclass(frozen=True)
class _InstallFacts:
    """What the install record keeps of one package's install in one container."""

    reference: str
    package_digest: str
    arch: str
    payload: str
    wheels: Sequence[LockedWheel]
    commands: Mapping[str, Sequence[str]]


@dataclass(frozen=True)
class WheelRecord:
    name: str
    version: str
    filename: str
    sha256: str


@dataclass(frozen=True)
class TreeCheck:
    """The installed tree against its recorded digest."""

    ok: bool
    digest: str
    expected: str
    violation: str | None = None


@dataclass(frozen=True)
class InstalledPackage:
    """An installed, immutable stage tool tree."""

    environment: Any
    reference: str
    root: str
    digest: str
    package_digest: str
    commands: Mapping[str, tuple[str, ...]]
    wheels: tuple[WheelRecord, ...]
    record: dict[str, Any]
    owner: int | None = 0

    @property
    def bin(self) -> str:
        return f"{self.root}/bin"

    @property
    def path(self) -> str:
        """The ``PATH`` value for calls into this package."""
        return f"{self.bin}:{BASE_PATH}"

    @property
    def python(self) -> str:
        return f"{self.root}/venv/bin/python"

    async def verify(self) -> TreeCheck:
        """Re-walk the installed tree (no-follow, root-owned) and compare it with the record."""
        found = await calls.tree_manifest(
            self.environment,
            self.root,
            unbounded=calls.Unbounded(links=True, owner=self.owner),
            entries=False,
        )
        ok = found.violation is None and found.digest == self.digest
        return TreeCheck(ok, found.digest, self.digest, found.violation)


def _arch(machine: str) -> str:
    arch = _ARCHES.get(machine.strip().lower())
    if arch is None:
        raise InstallError(f"unsupported container architecture {machine.strip()!r}")
    return arch


def _tool_files(files: Sequence[ShippedFile]) -> list[ShippedFile]:
    chosen = [item for item in files if item.path.startswith(TOOL_PREFIX)]
    for item in chosen:
        parts = PurePosixPath(item.path).parts
        if PurePosixPath(item.path).is_absolute() or ".." in parts:
            raise InstallError(f"unsafe shipped path {item.path!r}")
    return chosen


def _plan(tool: Sequence[ShippedFile], arch: str) -> tuple[LockedWheel, ...]:
    if not tool:
        return ()
    lock = next((item for item in tool if item.path == LOCK_PATH), None)
    if lock is None:
        raise InstallError(f"the tool has no {LOCK_PATH}")
    try:
        return uv_lock_wheels(lock.data.decode("utf-8"), arch)
    except (WheelPolicyError, ValueError) as error:
        raise InstallError(f"{LOCK_PATH}: {error}") from error


def _stage(
    host: Path, tool: Sequence[ShippedFile], wheels: Sequence[LockedWheel], cache_root: Path | None
) -> None:
    """Lay the upload out on the host: ``files/``, ``wheels/`` and ``requirements.txt``."""
    for item in tool:
        target = host / "files" / item.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.data)
    fetch_locked(wheels, host / "wheels", cache_root=cache_root)
    (host / "requirements.txt").write_text(hashed_requirements(wheels), encoding="utf-8")


def _spec(stage: str, uv: str, tool: Sequence[ShippedFile], facts: _InstallFacts) -> dict[str, Any]:
    wheels, commands = facts.wheels, facts.commands
    return {
        "stage": stage,
        "root": f"{calls.STAGES_ROOT}/{facts.reference}",
        "uv": uv,
        "python": calls.TRUSTED_PYTHON,
        "venv": bool(tool),
        "flags": [*OFFLINE_FLAGS.split(), *LOCKED_FLAGS.split()],
        "links": LINKS_DIR,
        "files": [{"path": i.path, "sha256": i.sha256, "exec": i.executable} for i in tool],
        "wheels": [{"file": w.filename, "sha256": w.sha256} for w in wheels],
        "commands": {name: list(argv) for name, argv in commands.items()},
    }


async def _run_install(environment: Any, host: Path, spec: dict[str, Any]) -> None:
    """Upload the staged bytes and run the root install; the stage is always removed."""
    stage = spec["stage"]
    try:
        await calls.root_command(environment, f"mkdir -m 0700 {shlex.quote(stage)}")
        await calls.upload_dir(environment, host, stage)
        outcome = await calls.run_payload(
            environment, "install", spec, timeout_sec=INSTALL_TIMEOUT_S
        )
    finally:
        await calls.trusted_exec(
            environment, f"rm -rf {shlex.quote(stage)}", user="root", timeout_sec=60
        )
    if not outcome.get("ok"):
        raise InstallError(str(outcome.get("error", "the root install failed")))


def _record(facts: _InstallFacts, found: calls.TreeManifest) -> dict[str, Any]:
    return {
        "schema": "install/1",
        "reference": facts.reference,
        "package_digest": facts.package_digest,
        "arch": facts.arch,
        "payload_digest": facts.payload,
        "tree_digest": found.digest,
        "tree_files": found.files,
        "tree_bytes": found.bytes,
        "commands": {name: list(argv) for name, argv in sorted(facts.commands.items())},
        "wheels": [
            {"name": w.name, "version": w.version, "filename": w.filename, "sha256": w.sha256}
            for w in facts.wheels
        ],
    }


async def install_package(
    environment: Any,
    logs_dir: Path,
    package: ShippedPackage,
    *,
    cache_root: Path | None = None,
) -> InstalledPackage:
    """Install ``package``'s shipped bytes as ``/opt/raidar/stages/<reference>``.

    Refuses (``InstallError``) a tool without a ``uv.lock``, any sdist/source/direct reference or
    missing wheel for the container's architecture, and a tree that is not root-owned
    afterwards. A missing index with a cold wheel cache raises ``DependencyInfraError``;
    container failures raise ``calls.TransportError``.
    """
    reference = package.reference
    if _REFERENCE.fullmatch(reference) is None:
        raise InstallError(f"bad package reference {reference!r}")
    payload = await calls.ensure_trusted(environment)
    arch = _arch(await calls.root_command(environment, "uname -m"))
    tool = _tool_files(package.files)
    wheels = _plan(tool, arch)
    uv = (await calls.root_command(environment, "command -v uv")).strip()
    stage = f"{calls.CALLS_ROOT}/install-{uuid.uuid4().hex}"
    facts = _InstallFacts(
        reference, package.package_digest, arch, payload, wheels, dict(package.commands)
    )
    with tempfile.TemporaryDirectory(prefix="raidar-install-") as scratch:
        host = Path(scratch)
        await asyncio.to_thread(_stage, host, tool, wheels, cache_root)
        await _run_install(environment, host, _spec(stage, uv, tool, facts))
    return await _measured(environment, logs_dir, facts, package.owner)


async def _measured(
    environment: Any, logs_dir: Path, facts: _InstallFacts, owner: int | None
) -> InstalledPackage:
    """Measure the installed tree (no-follow, owner checked) and record it host-side."""
    reference, wheels, wanted = facts.reference, facts.wheels, facts.commands
    root = f"{calls.STAGES_ROOT}/{reference}"
    found = await calls.tree_manifest(
        environment, root, unbounded=calls.Unbounded(links=True, owner=owner), entries=False
    )
    if found.violation is not None:
        raise InstallError(f"installed tree {root}: {found.violation}")
    record = _record(facts, found)
    target = Path(logs_dir) / "install" / f"{reference.replace('/', '--')}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return InstalledPackage(
        environment,
        reference,
        root,
        found.digest,
        facts.package_digest,
        {name: tuple(argv) for name, argv in wanted.items()},
        tuple(WheelRecord(w.name, w.version, w.filename, w.sha256) for w in wheels),
        record,
        owner,
    )


async def reuse_installed(
    environment: Any, logs_dir: Path, package: ShippedPackage
) -> InstalledPackage | None:
    """The package an earlier checkpoint installed in this container, measured now; else ``None``.

    Nothing is deleted, repaired or trusted: ``digest`` is the tree as it is found, so the caller
    compares it with the expected digest and a changed tree stays visible. ``files`` (the shipped
    bytes) only supply the wheel list recorded beside it. A tree that is not root-owned or holds
    a special file raises ``InstallError``.
    """
    reference, files = package.reference, package.files
    if _REFERENCE.fullmatch(reference) is None:
        raise InstallError(f"bad package reference {reference!r}")
    if not await calls.root_file_exists(environment, f"{calls.STAGES_ROOT}/{reference}"):
        return None
    payload = await calls.ensure_trusted(environment)
    arch = _arch(await calls.root_command(environment, "uname -m"))
    wheels = _plan(_tool_files(files), arch) if files else ()
    facts = _InstallFacts(
        reference, package.package_digest, arch, payload, wheels, dict(package.commands)
    )
    return await _measured(environment, logs_dir, facts, package.owner)
