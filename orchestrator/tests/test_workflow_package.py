"""The generic package runtime: setup, frame, canary, stops in workflow order, outputs, evidence."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar

import pytest

from raidar.subject.package.manifest import Output, StagePackage
from raidar.subject.package.registry import load_stage_package
from raidar.subject.primitive.gate import Attribution, GateOutcome
from raidar.subject.primitive.site import TrialInvalid
from raidar.subject.workflow import package as package_module
from raidar.subject.workflow.package import PackageCheckpoint
from raidar.subject.workflow.package_member import PackageSpec
from raidar.subject.workflow.stage import REPAIR, REWORK, Checkpoint, Spent
from tests.lifecycle_support import (
    NOTE_STATE,
    PACK,
    PRIMITIVES,
    FakeContainer,
    ScriptedRunner,
    ToolOutcome,
    install_container,
    installed,
    note_tool,
    stage_check_record,
)

BUILD = "Build:\n%%%NOTE_STATE%%%\n%%%NOTE_TWO%%%\nTask: %%%TASK%%%"


def package(name: str, gated: bool = False) -> StagePackage:
    """The note-state package without its gate (the gate runs in-container), under ``name``."""
    base = load_stage_package(NOTE_STATE)
    watch = f"/app/.raidar/{name}"
    manifest = base.manifest.model_copy(
        update={
            "name": name,
            "gate": base.manifest.gate if gated else (),
            "on_stop": base.manifest.on_stop.model_copy(update={"watch": watch}),
            "outputs": {"note": Output(path=f"{watch}/note.md")},
        }
    )
    return replace(base, manifest=manifest)


class Rig:
    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        deadline: float | None,
        gated: bool = False,
    ):
        self.container = FakeContainer(tmp_path / "container")
        self.container.root.mkdir()
        install_container(monkeypatch, self.container)
        self.packages = [package("note-state", gated), package("note-two")]
        self.checkpoint = Checkpoint(
            self.container, tmp_path / "logs", "the task", deadline=deadline
        )
        specs = [
            PackageSpec(
                p.name,
                p,
                installed(p, self.container),
                p.effective_params(),
                stage_check_record(p, self.container),
            )
            for p in self.packages
        ]
        self.runtime = PackageCheckpoint(
            self.checkpoint,
            specs,
            build=BUILD,
            pack=PACK,
            primitives=PRIMITIVES,
            runner=ScriptedRunner(self.container, tmp_path / "logs"),
        )

    def write_note(self, name: str, text: str = "enough words in this note") -> None:
        path = self.container.host(f"/app/.raidar/{name}/note.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    return Rig(tmp_path, monkeypatch, deadline=None)


async def test_prepare_frames_the_prompt_after_setup_and_the_canaries(rig: Rig) -> None:
    frame = await rig.runtime.prepare()

    assert frame.prompt.count("Say what you changed.") == 2
    assert frame.prompt.endswith("Task: the task")
    assert "at least 3 words" in frame.prompt
    assert "Keep a note in `/app/.raidar/note-state/note.md`." in frame.prompt
    assert frame.instructions
    primitives = [primitive for primitive, _, _ in rig.container.calls]
    assert primitives == ["setup/1", "setup/1", "stop/1", "stop/1"]  # setup first, then canaries


async def test_the_first_requested_resume_in_workflow_order_wins(rig: Rig) -> None:
    await rig.runtime.prepare()
    rig.container.calls.clear()
    rig.write_note("note-state")

    resume = await rig.runtime.check("turn-1")

    assert resume is not None
    assert resume.kind == REWORK
    assert "at least 3 words" in resume.message
    states = [context["state"] for _, _, context in rig.container.calls]
    assert states == ["/app/.raidar/note-state", "/app/.raidar/note-two"]  # first is done
    rig.write_note("note-two")
    rig.container.calls.clear()

    assert await rig.runtime.check("turn-2") is None


async def test_the_first_resume_stops_later_packages_from_being_called_this_turn(rig: Rig) -> None:
    await rig.runtime.prepare()
    rig.container.calls.clear()

    resume = await rig.runtime.check("turn-1")

    assert resume is not None
    assert [context["state"] for _, _, context in rig.container.calls] == [
        "/app/.raidar/note-state"
    ]


async def test_an_unusable_result_resumes_the_same_session_as_a_repair(rig: Rig) -> None:
    await rig.runtime.prepare()
    rig.write_note("note-two")

    def tool(container: FakeContainer, argv: Sequence[str], context: dict[str, Any]) -> ToolOutcome:
        if context["state"] == "/app/.raidar/note-state":
            return ToolOutcome(b"junk")
        return note_tool(container, argv, context)

    rig.container.tool = tool

    first = await rig.runtime.check("turn-1")
    second = await rig.runtime.check("turn-2")

    assert first is not None
    assert first.kind == REPAIR
    assert second is None  # one repair, then closed: the checkpoint ends as far as packages go
    assert rig.runtime.record()["stop/1"]["note-state"]["closed"] is True


async def test_a_resume_that_cannot_fit_the_deadline_is_cut_and_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, monkeypatch, deadline=time.monotonic() - 1)
    await rig.runtime.prepare()

    assert await rig.runtime.check("turn-1") is None
    assert rig.runtime.record()["endings"] == {"note-state": "deadline"}


async def test_close_copies_outputs_and_the_record_is_keyed_by_primitive(rig: Rig) -> None:
    await rig.runtime.prepare()
    rig.write_note("note-state")
    await rig.runtime.close("deadline")

    record = rig.runtime.record()

    assert {"frame/1", "install/1", "setup/1", "stop/1", "outputs/1"} <= set(record)
    assert record["outputs/1"]["note-state"]["note"]["error"] is None
    assert record["outputs/1"]["note-state"]["cut"] == "deadline"
    assert record["outputs/1"]["note-two"]["note"]["error"] == "missing"
    assert "gate/1" not in record


async def test_an_invalid_setup_invalidates_the_trial_before_the_agent(rig: Rig) -> None:
    rig.container.tool = lambda *_: ToolOutcome(b"", exit_code=1)

    with pytest.raises(TrialInvalid):
        await rig.runtime.prepare()


async def test_a_stop_without_its_stage_check_record_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, monkeypatch, deadline=None)
    p = rig.packages[0]
    spec = PackageSpec(p.name, p, installed(p, rig.container), p.effective_params())

    with pytest.raises(ValueError, match="stage-check record"):
        PackageCheckpoint(rig.checkpoint, [spec], build=BUILD, pack=PACK, primitives=PRIMITIVES)


class StubGate:
    """The gate's seat in ``PackageCheckpoint``: records its packages, runs nothing."""

    packages: ClassVar[list[Any]] = []
    seconds: ClassVar[float] = 0.0

    def __init__(self, environment: Any, logs_dir: Path, packages: Any, settings: Any) -> None:
        type(self).packages = list(packages)

    async def prepare(self, session_paths: Any) -> None:
        return None

    async def seal(self, after: str) -> GateOutcome:
        return GateOutcome(Attribution.PASSED, {"checks": []}, "")

    def record(self) -> dict[str, Any]:
        return {"gates": [{"seconds": type(self).seconds}]}

    async def close(self) -> None:
        return None


def gated_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, deadline: float | None) -> Rig:
    monkeypatch.setattr(package_module, "GateRun", StubGate)
    rig = Rig(tmp_path, monkeypatch, deadline=deadline, gated=True)
    StubGate.seconds = 0.0
    return rig


async def test_a_closed_package_leaves_the_flag_its_before_checks_refuse_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = gated_rig(tmp_path, monkeypatch, None)
    await rig.runtime.prepare()
    flag = StubGate.packages[0].closed_flag
    site = rig.runtime.members[0].site
    assert flag == f"/var/lib/raidar-calls/closed/{site.scope}-note-state"
    assert flag not in rig.container.root_files
    rig.container.host("/app/.raidar/note-state").mkdir(parents=True)
    rig.container.host("/app/.raidar/note-state/abort").write_text("")  # the tool closes

    resume = await rig.runtime.check("turn-1")

    assert resume is not None  # the other package still resumes the session
    assert rig.runtime.members[0].stop.closed
    assert flag in rig.container.root_files  # what the gate's before checks refuse on


async def test_a_resume_turn_must_fit_together_with_the_gate_run_that_follows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = gated_rig(tmp_path, monkeypatch, time.monotonic() + 10)
    rig.checkpoint.turns.append(Spent(REWORK, 6.0))
    StubGate.seconds = 5.0  # a 6 s turn alone fits in 10 s; the turn plus a 5 s gate does not
    await rig.runtime.prepare()

    assert await rig.runtime.check("turn-1") is None
    assert rig.runtime.record()["endings"] == {"note-state": "deadline"}


async def test_a_resume_turn_that_fits_alone_resumes_when_the_gate_is_quick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = gated_rig(tmp_path, monkeypatch, time.monotonic() + 10)
    rig.checkpoint.turns.append(Spent(REWORK, 6.0))
    StubGate.seconds = 1.0
    await rig.runtime.prepare()

    resume = await rig.runtime.check("turn-1")

    assert resume is not None
    assert resume.kind == REWORK
