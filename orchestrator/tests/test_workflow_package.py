"""The generic package runtime: setup, frame, canary, stops in workflow order, outputs, evidence."""

from __future__ import annotations

import hashlib
import shutil
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from raidar_contract.package.manifest import Output, StagePackage
from raidar_contract.package.registry import load_stage_package, stage_folder
from raidar_contract.workflow import PackageWorkflowDefinition

from raidar.subject.primitive.calls import TransportError
from raidar.subject.primitive.gate import Attribution, GateInvalid, GateOutcome
from raidar.subject.primitive.site import TrialInvalid
from raidar.subject.workflow import package as package_module
from raidar.subject.workflow.package import CheckpointPlan, PackageCheckpoint
from raidar.subject.workflow.package_member import PackageSpec
from raidar.subject.workflow.package_stage import PackageStage
from raidar.subject.workflow.stage import FIRST_BUILD, GATE_FIX, REPAIR, REWORK, Checkpoint, Spent
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
from tests.package_support import (
    ARCH,
    PLACEMENT,
    RELEASE,
    TASK,
    TREE,
    WATCH,
    Lab,
    ScriptedGate,
    definition_json,
    make_lab,
    passed,
    refused,
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
            CheckpointPlan(BUILD, PACK, PRIMITIVES),
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
        PackageCheckpoint(rig.checkpoint, [spec], CheckpointPlan(BUILD, PACK, PRIMITIVES))


class StubGate:
    """The gate's seat in ``PackageCheckpoint``: records its packages, runs nothing."""

    packages: ClassVar[list[Any]] = []
    seconds: ClassVar[float] = 0.0
    outcome: ClassVar[GateOutcome] = GateOutcome(Attribution.PASSED, {"checks": []}, "")
    retained: ClassVar[list[int]] = []

    def __init__(self, environment: Any, logs_dir: Path, packages: Any, settings: Any) -> None:
        type(self).packages = list(packages)

    async def prepare(self, session_paths: Any) -> None:
        return None

    async def seal(self, after: str) -> GateOutcome:
        return type(self).outcome

    async def retain_prefix(self, fixes: int) -> None:
        type(self).retained.append(fixes)

    def record(self) -> dict[str, Any]:
        return {"gates": [{"seconds": type(self).seconds}]}

    async def close(self) -> None:
        return None


def gated_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, deadline: float | None) -> Rig:
    monkeypatch.setattr(package_module, "GateRun", StubGate)
    rig = Rig(tmp_path, monkeypatch, deadline=deadline, gated=True)
    StubGate.seconds = 0.0
    StubGate.outcome = GateOutcome(Attribution.PASSED, {"checks": []}, "")
    StubGate.retained = []
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


async def test_the_first_rework_is_expected_to_take_as_long_as_the_first_build_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No rework has run yet, so the estimate was 0 and a rework the deadline would cut started."""
    rig = gated_rig(tmp_path, monkeypatch, time.monotonic() + 10)
    rig.checkpoint.turns.append(Spent(FIRST_BUILD, 60.0))
    await rig.runtime.prepare()

    assert rig.checkpoint.expected_seconds(REWORK) == 60.0
    assert await rig.runtime.check("turn-1") is None
    assert rig.runtime.record()["endings"] == {"note-state": "deadline"}


def test_a_later_rework_is_expected_to_take_as_long_as_the_last_one() -> None:
    checkpoint = Checkpoint(cast(Any, None), Path("."), "t")
    checkpoint.turns.extend([Spent(FIRST_BUILD, 60.0), Spent(REWORK, 8.0), Spent(REPAIR, 2.0)])

    assert checkpoint.expected_seconds(REWORK) == 10.0  # the last rework and its repair
    assert checkpoint.expected_seconds(GATE_FIX) == 0.0


async def test_a_rework_with_no_first_build_turn_has_no_estimate_and_is_never_scheduled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = gated_rig(tmp_path, monkeypatch, time.monotonic() + 10)
    await rig.runtime.prepare()

    assert rig.checkpoint.expected_seconds(REWORK) is None
    assert await rig.runtime.check("turn-1") is None
    assert rig.runtime.record()["endings"] == {"note-state": "deadline"}


def _refused(*names: str, reason: str = "work is wrong") -> GateOutcome:
    checks = [{"name": name, "status": "refused"} for name in names]
    return GateOutcome(Attribution.REFUSED, {"checks": checks}, reason)


async def _sealed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: GateOutcome,
    *,
    rounds: int = 0,
    deadline: float | None = None,
) -> tuple[Rig, Any]:
    rig = gated_rig(tmp_path, monkeypatch, deadline)
    rig.runtime.plan = replace(rig.runtime.plan, max_fix_rounds=rounds)
    StubGate.outcome = outcome
    await rig.runtime.prepare()
    rig.write_note("note-state")
    rig.write_note("note-two")
    return rig, await rig.runtime.check("turn-1")


async def test_a_passing_seal_stands_as_passed_and_asks_for_no_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = GateOutcome(Attribution.PASSED, {"checks": []}, "")
    rig, resume = await _sealed(tmp_path, monkeypatch, outcome, rounds=2)

    assert resume is None
    assert rig.runtime.gate_stand == package_module.GATE_PASSED
    assert rig.runtime.sealed is outcome


async def test_a_refused_seal_with_budget_orders_a_fix_naming_the_failed_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, resume = await _sealed(tmp_path, monkeypatch, _refused("lint", "tests"), rounds=1)

    assert resume is not None
    assert resume.kind == GATE_FIX
    assert "(lint, tests)" in resume.message
    assert "work is wrong" in resume.message
    assert rig.runtime.gate_stand is None  # a fix turn was ordered
    assert StubGate.retained == [1]  # the pre-fix snapshot is kept

    again = await rig.runtime.check("turn-2")  # the budget of one round is spent
    assert again is None
    assert rig.runtime.gate_stand == package_module.GATE_REFUSED

    rig.runtime.new_pass()  # a judged pass sent the work back: rounds count afresh
    assert (await rig.runtime.check("turn-3")) is not None
    assert StubGate.retained == [1, 2]


async def test_a_fix_message_without_check_detail_still_names_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, resume = await _sealed(tmp_path, monkeypatch, _refused(reason="nothing ran"), rounds=1)

    assert resume is not None
    assert resume.message.startswith("The checks did not pass: nothing ran.")


@pytest.mark.parametrize(
    ("outcome", "stand"),
    [
        (_refused("lint"), package_module.GATE_BUDGET),
        (GateOutcome(Attribution.AGENT_ERROR, {"checks": []}, "crash"), package_module.GATE_ERROR),
    ],
)
async def test_a_fix_that_does_not_fit_the_time_left_is_never_ordered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: GateOutcome, stand: str
) -> None:
    rig = gated_rig(tmp_path, monkeypatch, time.monotonic() + 10)
    rig.runtime.plan = replace(rig.runtime.plan, max_fix_rounds=2)
    StubGate.seconds = 100.0  # the gate run that follows a fix cannot fit in 10 s
    StubGate.outcome = outcome
    await rig.runtime.prepare()
    rig.write_note("note-state")
    rig.write_note("note-two")

    assert await rig.runtime.check("turn-1") is None
    assert rig.runtime.gate_stand == stand
    assert StubGate.retained == []


async def test_an_error_seal_with_no_budget_stands_as_an_error_not_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = GateOutcome(Attribution.AGENT_ERROR, {"checks": []}, "crash")
    rig, resume = await _sealed(tmp_path, monkeypatch, outcome, rounds=0)

    assert resume is None
    assert rig.runtime.gate_stand == package_module.GATE_ERROR


async def test_a_done_stop_owes_its_outputs_and_a_closed_one_does_not(rig: Rig) -> None:
    await rig.runtime.prepare()
    assert not rig.runtime.reported_done("note-state")
    rig.write_note("note-state")
    rig.write_note("note-two")
    assert await rig.runtime.check("turn-1") is None

    assert rig.runtime.reported_done("note-state")
    assert not rig.runtime.reported_done("nobody")
    assert not rig.runtime.producer_closed("note-state")
    copy = rig.runtime.output_copy("note-state", "note")
    assert copy is not None
    assert copy["error"] is None
    assert Path(str(copy["host_path"])).read_text() == "enough words in this note"
    assert rig.runtime.output_copy("note-state", "missing") is None
    assert rig.runtime.output_copy("nobody", "note") is None


async def test_a_closed_producer_is_reported_closed_and_has_no_output_copy(rig: Rig) -> None:
    await rig.runtime.prepare()
    rig.container.host("/app/.raidar/note-state").mkdir(parents=True)
    rig.container.host("/app/.raidar/note-state/abort").write_text("")
    rig.write_note("note-two")

    await rig.runtime.check("turn-1")

    assert rig.runtime.producer_closed("note-state")
    assert not rig.runtime.producer_closed("note-two")
    assert rig.runtime.output_copy("note-state", "note") is None


# the package stage over the lab: the lock, skills, prompt, gate fixes and the record ------------


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Lab:
    return make_lab(tmp_path, monkeypatch)


async def test_an_unusable_judge_invalidates_before_any_package_code_runs(lab: Lab) -> None:
    body = definition_json()
    body["build"]["reply"] = "acceptance-reply/1"
    body["judge"] = {
        "criteria": "note-state.note",
        "model": "someone/else-1",
        "closed_at": 0.8,
        "open_at": 0.3,
        "max_passes": 1,
        "rework": "Fix:\n%%%PUSHBACK%%%\n%%%GATE%%%",
    }
    stage = PackageStage(
        PackageWorkflowDefinition.model_validate(body),
        locked=lab.lock(),
        pack=PACK,
        skills_placement=PLACEMENT,
        repo_root=lab.repo,
    )
    checkpoint = Checkpoint(lab.container, lab.tmp_path / "judge-logs", TASK)

    with pytest.raises(TrialInvalid) as invalid:
        await stage.prepare(checkpoint)

    assert invalid.value.kind == "instrument"
    assert lab.installs.count == 0
    assert lab.container.calls == []
    assert stage.package_record()["jev-judge/1"]["passes"] == 0


# the lock holds the package to its bytes, installed tree and check record ------------------------


async def test_a_package_the_lock_does_not_list_is_refused_before_anything_installs(
    lab: Lab,
) -> None:
    stage, checkpoint = lab.stage({})

    with pytest.raises(TrialInvalid, match="not in the lock") as refused_:
        await stage.prepare(checkpoint)

    assert refused_.value.kind == "integrity"
    assert lab.installs.count == 0


async def test_package_bytes_other_than_the_locked_digest_are_refused(lab: Lab) -> None:
    stage, checkpoint = lab.stage(lab.lock(sha256="0" * 64))

    with pytest.raises(TrialInvalid, match="on disk, the lock holds 000000000000") as refused_:
        await stage.prepare(checkpoint)

    assert refused_.value.kind == "integrity"
    assert lab.installs.count == 0


async def test_a_package_missing_from_the_tree_is_an_integrity_failure_not_an_os_error(
    lab: Lab,
) -> None:
    shutil.rmtree(stage_folder(RELEASE, lab.repo))
    stage, checkpoint = lab.stage()

    with pytest.raises(TrialInvalid) as refused_:
        await stage.prepare(checkpoint)

    assert refused_.value.kind == "integrity"
    assert str(RELEASE) in refused_.value.detail


async def test_a_changed_installed_tree_is_refused_and_never_reinstalled_over(lab: Lab) -> None:
    stage, checkpoint = lab.stage(lab.lock(installed={ARCH: "u" * 64}))

    with pytest.raises(TrialInvalid, match="differs from the locked uuuuuuuuuuuu") as first:
        await stage.prepare(checkpoint)
    with pytest.raises(TrialInvalid, match="differs from the locked"):
        await lab.stage(lab.lock(installed={ARCH: "u" * 64}))[0].prepare(checkpoint)

    assert first.value.kind == "integrity"
    assert first.value.record == {"schema": "install/1", "arch": ARCH}
    assert lab.installs.count == 1  # the second checkpoint measured the first install


async def test_a_lock_without_this_architecture_is_refused(lab: Lab) -> None:
    stage, checkpoint = lab.stage(lab.lock(installed={"amd64": TREE}))

    with pytest.raises(TrialInvalid, match=r"none for arm64"):
        await stage.prepare(checkpoint)


async def test_an_install_failure_is_an_integrity_failure_naming_the_cause(lab: Lab) -> None:
    lab.installs.failure = "wheel hash mismatch"
    stage, checkpoint = lab.stage()

    with pytest.raises(TrialInvalid, match="wheel hash mismatch") as refused_:
        await stage.prepare(checkpoint)

    assert refused_.value.kind == "integrity"
    assert lab.container.calls == []  # nothing ran


@pytest.mark.parametrize(
    ("overrides", "stored", "message"),
    [
        ({"check": "f" * 64}, True, "differs from the locked one"),
        ({}, False, "the stage-check record is missing"),
        ({"drop": ["check"]}, False, "no stage-check record"),
    ],
    ids=["different-record", "locked-record-not-stored", "nothing-recorded"],
)
async def test_a_stop_needs_the_locked_stage_check_record(
    lab: Lab, overrides: dict[str, Any], stored: bool, message: str
) -> None:
    lab.stored_check = stored
    stage, checkpoint = lab.stage(lab.lock(**overrides))

    with pytest.raises(TrialInvalid, match=message) as refused_:
        await stage.prepare(checkpoint)

    assert refused_.value.kind == "instrument"
    assert lab.container.calls == []  # the agent's setup never ran


# the stage's prepare: skills, params, prompt and instructions -----------------------------------


async def test_prepare_places_the_packages_skills_and_records_their_bytes(lab: Lab) -> None:
    stage, checkpoint = lab.stage(params={"min_words": 6})

    await stage.prepare(checkpoint)

    (skill,) = [item for item in lab.package.files if item.path.startswith("skills/")]
    relative = f"{PLACEMENT}/note-keeping/SKILL.md"
    assert lab.container.host(f"/app/{relative}").read_bytes() == skill.data
    record = f"workflow/components/note-state/{relative}"
    assert stage.components == [{"path": relative, "sha256": skill.sha256, "record": record}]
    assert (checkpoint.logs_dir / record).read_bytes() == skill.data


async def test_prepare_frames_one_prompt_with_overridden_params_and_runs_setup_with_them(
    lab: Lab,
) -> None:
    stage, checkpoint = lab.stage(params={"min_words": 6})

    await stage.prepare(checkpoint)

    assert "at least 6 words" in stage.prompt
    assert stage.prompt.endswith(f"Task: {TASK}")
    assert "Say what you changed." in stage.prompt
    assert [c["params"]["min_words"] for c in lab.container.tool_calls("setup/1")] == [6]


async def test_the_workflow_instructions_are_each_packages_text_in_order(lab: Lab) -> None:
    stage, _ = lab.stage()
    stage.load()
    assert stage.instructions_text == (NOTE_STATE / "instructions.md").read_text().strip("\n")

    lab.without_instructions()
    bare, _ = lab.stage(lab.lock())
    bare.load()

    assert bare.instructions_text is None


def test_the_prompt_is_not_available_before_prepare(lab: Lab) -> None:
    stage, _ = lab.stage()

    with pytest.raises(AssertionError):
        _ = stage.prompt


# the stage's check, close and record -------------------------------------------------------------


async def test_close_collects_outputs_then_refuses_an_installed_tree_that_changed(
    lab: Lab,
) -> None:
    stage, checkpoint = lab.stage()
    await stage.prepare(checkpoint)
    lab.container.write_note("a note of enough words here")
    lab.trees["digest"] = "x" * 64  # the agent changed the tool tree during the trial

    with pytest.raises(TrialInvalid, match="changed during the trial") as refused_:
        await stage.close(checkpoint, None)

    assert refused_.value.kind == "integrity"
    outputs = stage.package_record()["outputs/1"]["note-state"]
    assert outputs["note"]["error"] is None  # the evidence was kept before the refusal
    assert outputs["cut"] is None


async def test_a_stage_that_never_prepared_closes_empty_and_records_nothing(lab: Lab) -> None:
    stage, checkpoint = lab.stage()

    await stage.close(checkpoint, "invalid")

    assert stage.package_record() == {}
    assert stage.checkpoint is None


# gate fix budget and order -----------------------------------------------------------------------


async def test_gate_fixes_stop_at_the_budget_and_retain_each_rounds_evidence(lab: Lab) -> None:
    stage, checkpoint = lab.stage(fix_rounds=2)
    await stage.prepare(checkpoint)
    lab.container.write_note("a note of enough words here")
    ScriptedGate.outcomes = [refused(), refused(), refused()]

    first = await stage.check(checkpoint, "turn-1")
    second = await stage.check(checkpoint, "turn-2")
    third = await stage.check(checkpoint, "turn-3")

    message = "The checks did not pass (note-ready): lint failed. Fix your work, then finish."
    assert first is not None
    assert (first.kind, first.message) == (GATE_FIX, message)
    assert second is not None
    assert (second.kind, second.message) == (GATE_FIX, message)
    assert third is None  # the budget is spent: the failing gate is left for scoring
    assert ScriptedGate.retained == [1, 2]


async def test_no_fix_budget_leaves_a_refused_gate_unresumed(lab: Lab) -> None:
    stage, checkpoint = lab.stage(fix_rounds=0)
    await stage.prepare(checkpoint)
    lab.container.write_note("a note of enough words here")
    ScriptedGate.outcomes = [refused()]

    assert await stage.check(checkpoint, "turn-1") is None
    assert ScriptedGate.retained == []


async def test_an_agent_error_is_fixed_like_a_refusal_but_a_pass_is_not(lab: Lab) -> None:
    stage, checkpoint = lab.stage(fix_rounds=2)
    await stage.prepare(checkpoint)
    lab.container.write_note("a note of enough words here")
    broke = GateOutcome(Attribution.AGENT_ERROR, {"checks": []}, "a check was killed")
    ScriptedGate.outcomes = [passed(), broke]

    assert await stage.check(checkpoint, "turn-1") is None
    resume = await stage.check(checkpoint, "turn-2")

    assert resume is not None
    assert resume.message == (
        "The checks did not pass: a check was killed. Fix your work, then finish."
    )
    assert ScriptedGate.retained == [1]


async def test_a_gate_fix_that_cannot_fit_the_deadline_with_its_rerun_is_not_started(
    lab: Lab,
) -> None:
    stage, checkpoint = lab.stage(fix_rounds=1, deadline=time.monotonic() + 10)
    checkpoint.turns.append(Spent(GATE_FIX, 6.0))
    ScriptedGate.seconds = 5.0
    await stage.prepare(checkpoint)
    lab.container.write_note("a note of enough words here")
    ScriptedGate.outcomes = [refused()]

    assert await stage.check(checkpoint, "turn-1") is None
    assert ScriptedGate.retained == []  # no round was consumed or retained


async def test_a_refused_gate_is_fixed_before_any_stop_is_asked(lab: Lab) -> None:
    stage, checkpoint = lab.stage(fix_rounds=1)
    await stage.prepare(checkpoint)  # no note: the stop would ask for a rework
    lab.container.calls.clear()
    ScriptedGate.outcomes = [refused()]

    resume = await stage.check(checkpoint, "turn-1")

    assert resume is not None
    assert resume.kind == GATE_FIX
    assert lab.container.tool_calls("stop/1") == []


async def test_the_canary_compares_the_loaded_packages_own_code_not_the_arms_locked_identity(
    lab: Lab,
) -> None:
    """The stage-check record carries ``package_primitives`` (adapter-independent); the arm's
    locked ``primitive_code`` is adapter-inclusive. A package whose record was taken against other
    code is refused before the agent starts; the real record runs the stop, and a ``done`` stop
    keeps its output."""
    lab.primitives = {"stop/1": "p" * 64}
    stale, checkpoint = lab.stage()
    with pytest.raises(TrialInvalid) as refused_:
        await stale.prepare(checkpoint)
    assert refused_.value.kind == "instrument"

    lab.primitives = None
    stage, checkpoint = lab.stage()
    await stage.prepare(checkpoint)
    lab.container.write_note("a note of enough words here")

    assert await stage.check(checkpoint, "turn-1") is None
    await stage.close(checkpoint, None)

    record = stage.package_record()
    (decision,) = [
        item for item in record["stop/1"]["note-state"]["decisions"] if item["action"] == "done"
    ]
    assert decision["outputs"]["note"]["error"] is None
    kept = record["outputs/1"]["note-state"]["note"]
    assert kept["sha256"] == hashlib.sha256(b"a note of enough words here").hexdigest()


@pytest.mark.parametrize(
    ("attribution", "kind"),
    [(Attribution.INSTRUMENT, "instrument"), (Attribution.INTEGRITY, "integrity")],
)
async def test_an_invalid_gate_is_an_invalid_trial_even_with_fix_budget_left(
    lab: Lab, attribution: Attribution, kind: str
) -> None:
    stage, checkpoint = lab.stage(fix_rounds=3)
    await stage.prepare(checkpoint)
    ScriptedGate.outcomes = [GateOutcome(attribution, {"why": "broker"}, "gate state changed")]

    with pytest.raises(TrialInvalid, match="gate state changed") as refused_:
        await stage.check(checkpoint, "turn-1")

    assert refused_.value.kind == kind
    assert refused_.value.record == {"why": "broker"}
    assert ScriptedGate.retained == []


async def test_a_gate_that_cannot_run_or_reach_the_container_is_an_invalid_trial(
    lab: Lab,
) -> None:
    stage, checkpoint = lab.stage(fix_rounds=1)
    await stage.prepare(checkpoint)
    ScriptedGate.outcomes = [GateInvalid("instrument", "gate payload missing")]
    with pytest.raises(TrialInvalid, match="gate payload missing") as invalid:
        await stage.check(checkpoint, "turn-1")
    ScriptedGate.outcomes = [TransportError("exec failed")]
    with pytest.raises(TrialInvalid, match="container transport failed: exec failed") as moved:
        await stage.check(checkpoint, "turn-2")

    assert invalid.value.kind == "instrument"
    assert moved.value.kind == "integrity"


async def test_a_stop_call_over_a_changed_tool_tree_is_an_invalid_trial(lab: Lab) -> None:
    stage, checkpoint = lab.stage()
    await stage.prepare(checkpoint)
    lab.container.tampered = True

    with pytest.raises(TrialInvalid, match="installed tree changed") as refused_:
        await stage.check(checkpoint, "turn-1")

    assert refused_.value.kind == "integrity"


async def test_a_stop_that_gives_unusable_output_repairs_then_ends_the_checkpoint(
    lab: Lab,
) -> None:
    stage, checkpoint = lab.stage()
    await stage.prepare(checkpoint)
    healthy = lab.container.tool

    def corrupt_agent_state(
        container: FakeContainer, argv: Sequence[str], context: dict[str, Any]
    ) -> ToolOutcome:
        if context["state"] == WATCH:  # only the agent's own state; the canary control stays sound
            return ToolOutcome(b"junk")
        return healthy(container, argv, context)

    lab.container.tool = corrupt_agent_state

    first = await stage.check(checkpoint, "turn-1")
    second = await stage.check(checkpoint, "turn-2")

    assert first is not None
    assert first.kind == REPAIR
    assert second is None
    assert stage.package_record()["stop/1"]["note-state"]["closed"] is True
