"""A pack container double for the stage-check fixture service (``check_package``/``replay_trial``).

Everything below the service is a double: the container (``FakeContainer`` of
``lifecycle_support``), the confined tool, the gate engine and the container transport. The
service's own decisions (which cases run, what they must show, the recorded floors) are real.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from raidar.subject.package.manifest import StagePackage
from raidar.subject.package.registry import load_stage_package
from raidar.subject.primitive import calls
from raidar.subject.primitive.calls import ResourceFloors, TreeManifest
from raidar.subject.primitive.gate import Attribution, GateOutcome
from raidar.subject.primitive.outputs import OutputsResult
from raidar.subject.workflow import stage_check
from tests.lifecycle_support import (
    FakeContainer,
    ScriptedRunner,
    ToolOutcome,
    _make_state_dir,
    _upload_state,
    install_container,
    installed,
    stop_json,
)

TREE_DIGEST = "t" * 64
NOTE_LINE = re.compile(r"The note has \w+ words\.\s*")

Behaviour = Callable[[Path, int], GateOutcome]


def strict_note_tool(
    container: FakeContainer, argv: Sequence[str], context: dict[str, Any]
) -> ToolOutcome:
    """A note verifier that only accepts the note's own sentence form: damaged text never passes."""
    state = container.host(context["state"])
    try:
        text = (state / "note.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    words = len(text.split())
    if (state / "abort").exists():
        return stop_json("closed", carry={"words": words})
    if not NOTE_LINE.fullmatch(text):
        return stop_json("resume", "Write the note.", {"words": words})
    return stop_json("done", carry={"words": words})


def lax_tool(container: FakeContainer, argv: Sequence[str], context: dict[str, Any]) -> ToolOutcome:
    """A verifier that reports ``done`` for any state at all."""
    return stop_json("done")


def gate_outcome(
    attribution: Attribution, statuses: dict[str, str], reason: str = "", exit_code: int = 0
) -> GateOutcome:
    """A sealed gate record of ``gate-check`` whose checks ended with ``statuses``."""
    checks = [
        {"name": f"gate-check.{name}", "status": status, "exit": exit_code}
        for name, status in statuses.items()
    ]
    return GateOutcome(attribution, {"verdict": attribution.value, "checks": checks}, reason)


def honest_gate(workspace: Path, run: int) -> GateOutcome:
    """The gate of ``gate-check``: the bad spec in the workspace makes the pytest check fail."""
    if (workspace / "spec_bad.py").exists():
        return gate_outcome(
            Attribution.REFUSED,
            {"no-marker": "passed", "unit-specs": "failed"},
            "unit-specs failed",
            exit_code=1,
        )
    return gate_outcome(Attribution.PASSED, {"no-marker": "passed", "unit-specs": "passed"})


class _Pack:
    """``PackContainer``: an async context handing out the rig's containers."""

    def __init__(self, rig: CheckRig) -> None:
        self.rig = rig
        self.container: FakeContainer | None = None

    async def __aenter__(self) -> FakeContainer:
        self.container = self.rig.enter()
        return self.container

    async def __aexit__(self, *exc: object) -> None:
        self.rig.exited += 1


class _Runner(ScriptedRunner):
    """The scripted call runner, recording what ``CallRunner`` records and obeying the rig."""

    def __init__(self, container: FakeContainer, logs_dir: Path, rig: CheckRig) -> None:
        super().__init__(container, logs_dir)
        self.rig = rig

    async def run(  # type: ignore[override]
        self, package: str, primitive: str, argv: Sequence[str], context: Any, **options: Any
    ) -> Any:
        if self.rig.ignore_bounds:
            options.pop("state", None)
        result = await super().run(package, primitive, argv, context, **options)
        result.record["stdout"] = result.stdout.decode("utf-8", errors="replace")
        result.record["exit_code"] = result.exit_code
        result.record["duration_s"] = self.rig.duration_s
        return result


class _Gate:
    """``GateRun``: the sealed outcome comes from the rig's behaviour over the workspace."""

    def __init__(self, rig: CheckRig, container: FakeContainer) -> None:
        self.rig = rig
        self.container = container
        self.evidence = SimpleNamespace(canary={"verdict": "passed"})

    async def prepare(self) -> None:
        return None

    async def seal(self, label: str) -> GateOutcome:
        run = self.rig.seals
        self.rig.seals += 1
        return self.rig.gate(self.container.host(stage_check.GATE_WORKSPACE), run)

    async def close(self) -> None:
        self.rig.closed.append(self.container)


@dataclass
class CheckRig:
    """The doubles behind ``stage_check`` for one package."""

    root: Path
    package: StagePackage
    tool: Callable[..., ToolOutcome] = strict_note_tool
    gate: Behaviour = honest_gate
    shared: bool = True
    tree_digest: str = TREE_DIGEST
    duration_s: float = 0.1
    ignore_bounds: bool = False
    failing_commands: tuple[str, ...] = ()
    floors: list[ResourceFloors] = field(default_factory=list)
    containers: list[FakeContainer] = field(default_factory=list)
    closed: list[FakeContainer] = field(default_factory=list)
    entered: int = 0
    exited: int = 0
    seals: int = 0
    shell: list[str] = field(default_factory=list)

    def enter(self) -> FakeContainer:
        self.entered += 1
        if self.shared and self.containers:
            return self.containers[0]
        container = FakeContainer(self.root / f"container-{len(self.containers)}", tool=self.tool)
        container.root.mkdir()
        self.containers.append(container)
        return container

    @property
    def container(self) -> FakeContainer:
        return self.containers[0]

    async def run_check(
        self, reference: str | None = None, *, logs: Path | None = None
    ) -> stage_check.CheckReport:
        return await stage_check.check_package(
            self.package,
            reference or self.package.name,
            image="image",
            docker_context="context",
            pack="pack-recipe-1",
            logs_dir=logs or self.root / "logs",
        )


def _trusted_exec(rig: CheckRig) -> Callable[..., Any]:
    """Raidar's own state-building shell commands, applied to the container's files."""

    async def run(container: FakeContainer, command: str, **_: Any) -> Any:
        rig.shell.append(command)
        if command.startswith(rig.failing_commands):
            return SimpleNamespace(return_code=1, stdout="", stderr="no space left")
        words = command.split()
        if words[0] == "mkfifo":
            os.mkfifo(container.host(words[1]))
        elif words[:2] == ["ln", "-s"]:
            os.symlink(words[2], container.host(words[3]))
        elif words[0] == "truncate":
            with container.host(words[3]).open("wb") as big:
                big.truncate(int(words[2]))
        elif words[0] == "echo":
            container.host(words[3]).write_text("data\n", encoding="utf-8")
        else:
            raise AssertionError(f"unexpected trusted command {command!r}")
        return SimpleNamespace(return_code=0, stdout="", stderr="")

    return run


def install_rig(
    root: Path, monkeypatch: pytest.MonkeyPatch, package: StagePackage | None = None, **fields: Any
) -> CheckRig:
    """Route ``stage_check`` and the primitives below it to a ``CheckRig``."""
    root.mkdir(parents=True, exist_ok=True)
    package = package or load_stage_package(stop_only_note_state(root / "pkg"))
    rig = CheckRig(root, package, **fields)
    if rig.shared:
        install_container(monkeypatch, rig.enter())
        rig.entered = 0
    monkeypatch.setattr(stage_check, "PackContainer", lambda image, context: _Pack(rig))
    monkeypatch.setattr(stage_check, "CallRunner", lambda c, logs: _Runner(c, logs, rig))

    async def install(
        container: FakeContainer, logs: Path, package: StagePackage, *rest: Any
    ) -> Any:
        return installed(package, container)

    async def make_state_dir(container: FakeContainer, target: str) -> None:
        await _make_state_dir(container, None, target)

    async def upload_dir(container: FakeContainer, source: Path, target: str) -> None:
        await _upload_state(container, None, source, target)

    async def root_command(container: FakeContainer, command: str, **_: Any) -> str:
        return ""

    async def resource_floors(container: FakeContainer, paths: Sequence[str]) -> ResourceFloors:
        return rig.floors.pop(0) if rig.floors else container.floors

    async def tree(container: Any, path: str, **_: Any) -> TreeManifest:
        return TreeManifest(path, True, (), rig.tree_digest, 0, 0, None)

    async def collect(site: Any, label: str) -> OutputsResult:
        return OutputsResult()

    monkeypatch.setattr(stage_check, "_install", install)
    monkeypatch.setattr(stage_check, "make_state_dir", make_state_dir)
    monkeypatch.setattr(stage_check, "upload_dir", upload_dir)
    monkeypatch.setattr(stage_check, "root_command", root_command)
    monkeypatch.setattr(stage_check, "resource_floors", resource_floors)
    monkeypatch.setattr(stage_check, "trusted_exec", _trusted_exec(rig))
    monkeypatch.setattr(calls, "tree_manifest", tree)
    monkeypatch.setattr(stage_check, "collect_outputs", collect)
    monkeypatch.setattr(
        stage_check,
        "GateRun",
        lambda container, logs, packages: _Gate(rig, container),
    )
    return rig


def _note_state() -> Path:
    return Path(__file__).parent / "fixtures" / "stages" / "note-state"


def copy_package(name: str, destination: Path) -> Path:
    """A mutable copy of the fixture package ``name``."""
    target = destination / name
    shutil.copytree(Path(__file__).parent / "fixtures" / "stages" / name, target)
    return target


def stop_only_note_state(destination: Path) -> Path:
    """A copy of note-state with its gate removed: only the stop's fixtures are mandatory."""
    root = copy_package("note-state", destination)
    manifest = root / "stage.yaml"
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    del data["gate"]
    del data["requires"]["gate"]
    manifest.write_text(yaml.safe_dump(data), encoding="utf-8")
    for case in ("pass", "refuse"):
        shutil.rmtree(root / "fixtures" / case)
    return root
