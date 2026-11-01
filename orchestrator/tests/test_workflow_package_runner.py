"""The generic schema-2 runner path: the whole ``WorkflowRunner`` over a scripted agent.

A scripted native agent and the lifecycle container double stand in for the model and the sandbox;
the runner's own shell reads (the session-store ctime) run on the host, which keeps this file in the
slow tier. The stage-level scenarios (lock, skills, prompt, gate fixes, record) are the fast tests
of ``test_workflow_package``.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

import pytest
from harbor.models.agent.context import AgentContext

from raidar.subject.workflow.runner import RECORD_FILE, WorkflowRunner
from raidar.subject.workflow.stage import GATE_FIX, REWORK
from raidar.subject.workflow.turns import WORKFLOW_DIR
from tests.lifecycle_support import NOTE_STATE, PACK, PRIMITIVES
from tests.native_support import CodexNative
from tests.package_support import (
    ARCH,
    PLACEMENT,
    TASK,
    WATCH,
    Env,
    Lab,
    ScriptedGate,
    definition_json,
    make_lab,
    passed,
    refused,
)
from tests.workflow_support import LocalEnvironment

pytestmark = pytest.mark.slow


class HostEnv(Env):
    """The lifecycle container whose own shell commands (the session-store ctime read) run on the
    host, as the existing workflow environment double does."""

    async def exec(self, command: str, **options: Any) -> Any:
        return await LocalEnvironment(dict(os.environ)).exec(command, **options)


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Lab:
    return make_lab(tmp_path, monkeypatch, HostEnv)


# the whole runner over a scripted agent ----------------------------------------------------------

Script = Callable[[Env], None]


class ScriptedInner(CodexNative):
    """The model's seat: each turn runs the next script step against the container."""

    script: ClassVar[list[Script]] = []
    prompts: ClassVar[list[tuple[bool, str]]] = []
    specs: ClassVar[list[dict[str, Any]]] = []
    container: ClassVar[Env]
    failure: ClassVar[Exception | None] = None

    @staticmethod
    def name() -> str:
        return "scripted-inner"

    def version(self) -> str | None:
        return None

    async def setup(self, environment: Any) -> None:
        return None

    async def run(self, instruction: str, environment: Any, context: AgentContext) -> None:
        self._turn(instruction, False, context)

    async def resume(self, instruction: str, environment: Any, context: AgentContext) -> None:
        self._turn(instruction, True, context)

    def _turn(self, instruction: str, resumed: bool, context: AgentContext) -> None:
        cls = type(self)
        cls.prompts.append((resumed, instruction))
        turns = len(cls.prompts)
        context.n_input_tokens, context.n_cache_tokens = 1000 * turns, 600 * turns
        context.n_output_tokens, context.cost_usd = 50 * turns, None
        (self.logs_dir / "trajectory.json").write_text(json.dumps({"session_id": "session-1"}))
        cls.script.pop(0)(cls.container)
        if cls.failure is not None:
            raise cls.failure


def write(text: str) -> Script:
    return lambda env: env.write_note(text)


def close_it(env: Env) -> None:
    env.host(WATCH).mkdir(parents=True, exist_ok=True)
    env.host(f"{WATCH}/abort").write_text("")


def tamper(env: Env) -> None:
    env.tampered = True


@pytest.fixture
def inner(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> type[ScriptedInner]:
    ScriptedInner.script = []
    ScriptedInner.prompts = []
    ScriptedInner.specs = []
    ScriptedInner.failure = None
    ScriptedInner.container = lab.container

    def create(runner: Any, spec: Any, logs_dir: Path, *, model_name: str | None) -> Any:
        ScriptedInner.specs.append(dict(spec.kwargs))
        return ScriptedInner(logs_dir=logs_dir, model_name=model_name)

    monkeypatch.setattr("raidar.subject.workflow.runner.create_inner", create)
    return ScriptedInner


async def run_workflow(
    lab: Lab,
    *,
    locked: dict[str, dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
    fix_rounds: int = 0,
) -> tuple[dict[str, Any], WorkflowRunner]:
    logs = lab.tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    runner = WorkflowRunner(
        logs_dir=logs,
        environment_logs_dir=PurePosixPath(logs),
        model_name="openai/gpt-6-luna",
        inner_agent={"name": "codex"},
        workflow="acme/note-flow@1",
        definition=definition_json(params, fix_rounds),
        locked_packages=lab.lock() if locked is None else locked,
        primitives=PRIMITIVES,
        pack=PACK,
        skills_placement=PLACEMENT,
    )
    await runner.run(TASK, lab.container, AgentContext())  # type: ignore[arg-type]
    return recorded(lab), runner


def recorded(lab: Lab) -> dict[str, Any]:
    record: dict[str, Any] = json.loads(
        (lab.tmp_path / "logs" / WORKFLOW_DIR / RECORD_FILE).read_text()
    )
    return record


async def test_a_gate_fix_then_a_rework_then_done_resume_one_session_in_that_order(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    inner.script = [write("two words"), write("still two"), write("now a note of seven words")]
    ScriptedGate.outcomes = [refused(), passed(), passed()]

    record, runner = await run_workflow(lab, params={"min_words": 6}, fix_rounds=1)

    assert [(t["stage"], t["kind"], t["resumed"]) for t in record["turns"]] == [
        ("build", "first_build", False),
        ("build", GATE_FIX, True),
        ("build", REWORK, True),
    ]
    first, fix, rework = (text for _, text in inner.prompts)
    assert runner.package_stage is not None
    assert first == runner.package_stage.prompt
    assert "at least 6 words" in first
    assert first.endswith(f"Task: {TASK}")
    assert fix == "The checks did not pass (note-ready): lint failed. Fix your work, then finish."
    assert rework == "Write a note of at least 6 words."
    assert ScriptedGate.retained == [1]
    assert record["stop/1"]["note-state"]["closed"] is False
    assert record["outputs/1"]["note-state"]["note"]["error"] is None
    assert record["outputs/1"]["note-state"]["cut"] is None
    assert "invalid" not in record
    assert record["integrity"] == []
    for key in ("frame/1", "install/1", "setup/1", "gate/1"):
        assert key in record
    (skill,) = [item for item in lab.package.files if item.path.startswith("skills/")]
    assert [c["sha256"] for c in record["components"]] == [skill.sha256]
    expected = (NOTE_STATE / "instructions.md").read_text().strip("\n")
    assert {spec["instructions"] for spec in inner.specs} == {expected}


async def test_a_package_that_closes_ends_after_one_turn_with_its_stop_recorded_closed(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    inner.script = [close_it]

    record, _ = await run_workflow(lab)

    assert [t["kind"] for t in record["turns"]] == ["first_build"]
    assert record["stop/1"]["note-state"]["closed"] is True
    assert "invalid" not in record


@pytest.mark.parametrize(
    ("lock", "kind", "fragment"),
    [
        ({"sha256": "0" * 64}, "integrity", "on disk"),
        ({"installed": {ARCH: "u" * 64}}, "integrity", "differs from the locked"),
        ({"check": "f" * 64}, "instrument", "differs from the locked one"),
    ],
    ids=["bytes", "installed-tree", "check-record"],
)
async def test_a_locked_difference_is_an_invalid_trial_and_the_agent_never_starts(
    lab: Lab,
    inner: type[ScriptedInner],
    lock: dict[str, Any],
    kind: str,
    fragment: str,
) -> None:
    record, _ = await run_workflow(lab, locked=lab.lock(**lock))

    assert inner.prompts == []
    assert record["turns"] == []
    assert record["invalid"]["kind"] == kind
    assert fragment in record["invalid"]["detail"]
    assert record["integrity"] == [
        {"name": f"package_{kind}", "detail": record["invalid"]["detail"], "turn": "packages"}
    ]
    assert "outputs/1" not in record  # no checkpoint ever began


async def test_an_unlocked_package_is_invalid_with_nothing_installed(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    record, _ = await run_workflow(lab, locked={})

    assert record["invalid"]["kind"] == "integrity"
    assert "not in the lock" in record["invalid"]["detail"]
    assert inner.prompts == []
    assert lab.installs.count == 0


async def test_a_tool_tree_changed_during_a_turn_cuts_the_checkpoint_as_invalid(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    inner.script = [tamper]

    record, _ = await run_workflow(lab)

    assert len(record["turns"]) == 1  # the agent ran; its check found the instrument changed
    assert record["invalid"]["kind"] == "integrity"
    assert "installed tree changed" in record["invalid"]["detail"]
    assert record["outputs/1"]["note-state"]["cut"] == "invalid"


async def test_an_installed_tree_changed_by_the_end_marks_invalid_after_keeping_outputs(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    def change_tree(env: Env) -> None:
        env.write_note("a note of enough words here")
        lab.trees["digest"] = "x" * 64

    inner.script = [change_tree]

    record, _ = await run_workflow(lab)

    assert [t["kind"] for t in record["turns"]] == ["first_build"]
    assert record["invalid"]["kind"] == "integrity"
    assert "changed during the trial" in record["invalid"]["detail"]
    assert record["outputs/1"]["note-state"]["note"]["error"] is None


async def test_a_failing_turn_still_reaches_harbor_and_cuts_the_outputs_as_a_turn_error(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    inner.script = [write("a note of enough words here")]
    inner.failure = RuntimeError("agent crashed")

    with pytest.raises(RuntimeError, match="agent crashed"):
        await run_workflow(lab)

    record = recorded(lab)
    assert record["turns"][0]["error"] == "RuntimeError: agent crashed"
    assert record["outputs/1"]["note-state"]["cut"] == "turn_error"
    assert "invalid" not in record


def plant(path: Path, text: str) -> Script:
    def step(env: Env) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    return step


def both(*steps: Script) -> Script:
    def run_all(env: Env) -> None:
        for step in steps:
            step(env)

    return run_all


async def test_a_native_session_log_rewritten_between_turns_is_a_named_integrity_violation(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    session = lab.tmp_path / "logs" / "sessions" / "rollout.jsonl"
    inner.script = [
        both(write("two words"), plant(session, '{"turn": 1}\n')),
        both(
            write("now a note of seven words"),
            plant(session, '{"turn": "rewritten"}\n{"turn": 2}\n'),
        ),
    ]

    record, _ = await run_workflow(lab, params={"min_words": 6})

    assert [(i["name"], i["turn"]) for i in record["integrity"]] == [
        ("session_log_rewritten", "workflow/build-2")
    ]
    assert record["turns"][1]["integrity"][0]["name"] == "session_log_rewritten"


# published evidence -------------------------------------------------------------------------------


async def test_a_published_output_path_leads_to_the_kept_evidence_below_the_step_logs(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    inner.script = [write("a note of enough words here")]

    record, runner = await run_workflow(lab)

    logs = lab.tmp_path / "logs"
    copied = record["outputs/1"]["note-state"]["note"]
    relative = PurePosixPath(copied["host_path"])
    assert copied["error"] is None
    assert not relative.is_absolute()
    assert ".." not in relative.parts
    assert relative.parts[0] == "stages"
    kept = logs.joinpath(*relative.parts)
    assert kept.is_file()
    assert hashlib.sha256(kept.read_bytes()).hexdigest() == copied["sha256"]
    assert not runner.staging.exists()  # the record never needed the deleted staging directory
    assert str(runner.staging) not in json.dumps(record)


async def test_the_context_metadata_carries_the_same_portable_record(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    inner.script = [write("a note of enough words here")]
    context = AgentContext()
    logs = lab.tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    runner = WorkflowRunner(
        logs_dir=logs,
        environment_logs_dir=PurePosixPath(logs),
        model_name="openai/gpt-6-luna",
        inner_agent={"name": "codex"},
        workflow="acme/note-flow@1",
        definition=definition_json(),
        locked_packages=lab.lock(),
        primitives=PRIMITIVES,
        pack=PACK,
        skills_placement=PLACEMENT,
    )

    await runner.run(TASK, lab.container, context)  # type: ignore[arg-type]

    assert context.metadata is not None
    assert context.metadata["workflow"] == recorded(lab)


async def test_evidence_the_agent_planted_under_a_published_name_is_replaced(
    lab: Lab, inner: type[ScriptedInner]
) -> None:
    forged = lab.tmp_path / "logs" / "stages" / "forged.txt"
    inner.script = [both(write("a note of enough words here"), plant(forged, "forged"))]

    await run_workflow(lab)

    assert not forged.exists()
