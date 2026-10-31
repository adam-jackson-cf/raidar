"""A scripted container for the lifecycle primitives' contract tests.

The container's file system is a host temp directory; transport functions (state copy, manifests,
uploads, resource probes, root files) and ``CallRunner.run`` are replaced by in-process doubles
that follow the real transport's contract: bounds are enforced before a tool is called, a missing
state directory is a state and not a violation, and nothing is followed through links. The
package's tool is a Python callable (the double of the confined child): no package code is
executed, imported or built on the host.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from raidar_contract.package.manifest import StagePackage
from raidar_contract.package.registry import load_stage_package

from raidar.subject.primitive import canary, outputs, site, stop
from raidar.subject.primitive.calls import (
    STATE_LIMITS,
    CallResult,
    CallRunner,
    ManifestEntry,
    ResourceFloors,
    TransportError,
    TreeManifest,
)
from raidar.subject.primitive.canary import Canary, StageCheckRecord
from raidar.subject.primitive.install import InstalledPackage
from raidar.subject.primitive.site import PackageSite

FIXTURES = Path(__file__).parent / "fixtures" / "stages" / "acme"
NOTE_STATE = FIXTURES / "note-state"
WATCH = "/app/.raidar/note-state"
NOTE = f"{WATCH}/note.md"
PACK = "pack-recipe-1"
PRIMITIVES = {"stop/1": "p" * 64}


@dataclass
class ToolOutcome:
    """What the confined child did for one call."""

    stdout: bytes = b""
    exit_code: int | None = 0
    timed_out: bool = False
    overflow: bool = False
    signal: int | None = None


Tool = Callable[["FakeContainer", Sequence[str], dict[str, Any]], ToolOutcome]


def stop_json(
    status: str, message: str = "", carry: dict[str, Any] | None = None, **extra: Any
) -> ToolOutcome:
    """A stop call's stdout as the package tool prints it."""
    document = {"status": status, "message": message, "tokens": {}, "carry": carry or {}}
    return ToolOutcome(json.dumps({**document, **extra}).encode())


def note_tool(
    container: FakeContainer, argv: Sequence[str], context: dict[str, Any]
) -> ToolOutcome:
    """The double of the note-state package's tool: setup, check and inspect over its state."""
    command = argv[1]
    state = container.host(context["state"])
    if command == "setup":
        return ToolOutcome(json.dumps({"tokens": {"GUIDANCE": "Say what you changed."}}).encode())
    try:
        words = len((state / "note.md").read_text(encoding="utf-8").split())
    except (OSError, UnicodeDecodeError):
        words = 0
    needed = context["params"]["min_words"]
    if (state / "abort").exists():
        return stop_json("closed", carry={"words": words})
    if words < needed:
        return stop_json("resume", f"Write a note of at least {needed} words.", {"words": words})
    return stop_json("done", carry={"words": words})


@dataclass
class FakeContainer:
    """The container's files and the behaviour of its tool."""

    root: Path
    tool: Tool = note_tool
    floors: ResourceFloors = field(
        default_factory=lambda: ResourceFloors({"/app": 10**9, "/tmp": 10**9}, 10**9)
    )
    calls: list[tuple[str, list[str], dict[str, Any]]] = field(default_factory=list)
    root_files: dict[str, bytes] = field(default_factory=dict)
    tampered: bool = False
    transport_down: bool = False
    upload_down: bool = False

    def host(self, path: str) -> Path:
        return self.root / path.lstrip("/")

    def write_note(self, text: str = "a short note of words") -> None:
        note = self.host(NOTE)
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(text, encoding="utf-8")

    def tool_calls(self, primitive: str) -> list[dict[str, Any]]:
        return [context for name, _, context in self.calls if name == primitive]


def _walk(container: FakeContainer, path: str) -> TreeManifest:
    base = container.host(path)
    if not os.path.lexists(base):
        return TreeManifest(path, False, (), "", 0, 0, "missing")
    entries: list[ManifestEntry] = []
    violation: str | None = None
    total = 0

    def visit(item: Path, depth: int) -> None:
        nonlocal violation, total
        if violation is not None:
            return
        mode = os.lstat(item).st_mode
        name = str(item.relative_to(base)) if item != base else item.name
        if depth > STATE_LIMITS["max_depth"]:
            violation = f"deeper than {STATE_LIMITS['max_depth']}"
        elif stat.S_ISDIR(mode):
            for child in sorted(item.iterdir()):
                visit(child, depth + 1)
        elif not stat.S_ISREG(mode):
            violation = f"{name} is a symlink or special file"
        else:
            size = item.stat().st_size
            total += size
            digest = hashlib.sha256(item.read_bytes()).hexdigest() if size < 1 << 20 else "big"
            entries.append(ManifestEntry(name, "file", size, digest, mode & 0o777))
            if len(entries) > STATE_LIMITS["max_files"] or total > STATE_LIMITS["max_bytes"]:
                violation = "over the byte or file cap"

    visit(base, 0)
    digest = hashlib.sha256(json.dumps([e.path for e in entries]).encode()).hexdigest()
    return TreeManifest(path, True, tuple(entries), digest, len(entries), total, violation)


class ScriptedRunner(CallRunner):
    """``CallRunner`` whose child is the container's tool double."""

    def __init__(self, container: FakeContainer, logs_dir: Path) -> None:
        super().__init__(container, logs_dir)
        self.container = container

    async def run(  # type: ignore[override]
        self, package: str, primitive: str, argv: Sequence[str], context: Any, **options: Any
    ) -> CallResult:
        container = self.container
        if container.transport_down:
            raise TransportError("exec failed")
        container.calls.append((primitive, list(argv), dict(context)))
        record = {"package": package, "primitive": primitive, "argv": list(argv)}
        path = self.logs_dir / "call.json"
        state = options.get("state")
        if state is not None:
            found = _walk(container, state)
            if found.violation is not None:
                return CallResult(
                    record, b"", b"", None, None, False, False, None, found.violation, path
                )
        out = container.tool(container, argv, dict(context))
        problem = "installed tree changed during the call" if container.tampered else None
        return CallResult(
            record,
            out.stdout,
            b"",
            out.exit_code,
            out.signal,
            out.timed_out,
            out.overflow,
            problem,
            None,
            path,
            ran=True,
        )


async def _tree_manifest(container: FakeContainer, env: Any, path: str, **_: Any) -> TreeManifest:
    return _walk(container, path)


async def _snapshot_state(
    container: FakeContainer, env: Any, path: str, destination: Path
) -> TreeManifest:
    found = _walk(container, path)
    if found.violation is None:
        source = container.host(path)
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copy(source, destination / source.name)
    return found


async def _upload_state(container: FakeContainer, env: Any, source: Path, target: str) -> None:
    if container.upload_down:
        raise TransportError("disk full")
    shutil.copytree(source, container.host(target), dirs_exist_ok=True)


async def _make_state_dir(container: FakeContainer, env: Any, target: str) -> None:
    if container.upload_down:
        raise TransportError("disk full")
    container.host(target).mkdir(parents=True, exist_ok=True)


async def _resource_floors(
    container: FakeContainer, env: Any, paths: Sequence[str]
) -> ResourceFloors:
    return container.floors


async def _put_root_file(
    container: FakeContainer, env: Any, path: str, data: bytes, mode: int = 0
) -> None:
    container.root_files[path] = data


async def _root_command(container: FakeContainer, env: Any, command: str, **_: Any) -> str:
    """The canary's only root command: removing its own stale folder."""
    verb, flags, path = shlex.split(command)
    assert (verb, flags) == ("rm", "-rf")
    shutil.rmtree(container.host(path), ignore_errors=True)
    return ""


def install_container(monkeypatch: pytest.MonkeyPatch, container: FakeContainer) -> None:
    """Route the primitives' container transport to ``container``."""

    def bound(function: Callable[..., Any]) -> Callable[..., Any]:
        async def call(*args: Any, **kwargs: Any) -> Any:
            return await function(container, *args, **kwargs)

        return call

    for module, name, function in (
        (site, "snapshot_state", _snapshot_state),
        (outputs, "snapshot_state", _snapshot_state),
        (canary, "tree_manifest", _tree_manifest),
        (canary, "upload_state", _upload_state),
        (canary, "make_state_dir", _make_state_dir),
        (canary, "resource_floors", _resource_floors),
        (stop, "put_root_file", _put_root_file),
        (canary, "root_command", _root_command),
    ):
        monkeypatch.setattr(module, name, bound(function))


def installed(package: StagePackage, container: FakeContainer) -> InstalledPackage:
    return InstalledPackage(
        environment=container,
        reference=package.name,
        root=f"/opt/raidar/stages/{package.name}",
        digest="t" * 64,
        package_digest=package.digest,
        commands={},
        wheels=(),
        record={"schema": "install/1"},
    )


def note_digest(container: FakeContainer, case: str = "done") -> dict[str, str]:
    """Output digest of a fixture's state, as stage-check records it."""
    note = NOTE_STATE / "fixtures" / case / "state" / "note.md"
    return {"note": hashlib.sha256(note.read_bytes()).hexdigest()}


def stage_check_record(package: StagePackage, container: FakeContainer) -> StageCheckRecord:
    """The passing stage-check record of the note-state ``done`` fixture."""
    state = NOTE_STATE / "fixtures" / "done" / "state"
    words = len((state / "note.md").read_text().split())
    stdout = stop_json("done", carry={"words": words}).stdout
    return StageCheckRecord(
        package=package.digest,
        pack=PACK,
        primitives=PRIMITIVES,
        stdout_sha256=hashlib.sha256(stdout).hexdigest(),
        outputs=note_digest(container),
        disk_free=1_000,
        memory_available=1_000,
    )


@dataclass
class Rig:
    """A package site over a scripted container with its canary."""

    container: FakeContainer
    package: StagePackage
    site: PackageSite
    canary: Canary
    expected: StageCheckRecord


def build_rig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, package: StagePackage | None = None
) -> Rig:
    container = FakeContainer(tmp_path / "container")
    container.root.mkdir()
    install_container(monkeypatch, container)
    package = package or load_stage_package(NOTE_STATE)
    expected = stage_check_record(package, container)
    package_site = PackageSite(
        environment=container,
        runner=ScriptedRunner(container, tmp_path / "logs"),
        logs_dir=tmp_path / "logs",
        package=package,
        reference=package.name,
        installed=installed(package, container),
        task="the task",
        params=package.effective_params(),  # type: ignore[arg-type]
    )
    return Rig(container, package, package_site, Canary(package_site, expected), expected)
