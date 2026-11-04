"""The workflow runner: one build turn per checkpoint, its logs, record and context totals.

The runner-level evidence guarantees live here too: what the agent writes into its mounted logs is
replaced by the host-kept records, links are named and never copied, and a rewritten session
store is a named integrity violation. A workflow with no package is the smallest real runtime.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from harbor.models.agent.context import AgentContext
from raidar_contract.agent_records import ZERO
from raidar_contract.workflow import load_package_workflow

from raidar.subject.primitive.site import TrialInvalid
from raidar.subject.workflow.package_stage import PackageStage
from raidar.subject.workflow.runner import (
    EVIDENCE_DIRS,
    PROMPT_FILE,
    RECORD_FILE,
    SessionWatch,
    WorkflowRunner,
)
from raidar.subject.workflow.stage import REWORK, Checkpoint, Resume
from raidar.subject.workflow.turns import (
    TRAJECTORY_FILE,
    WORKFLOW_DIR,
    Run,
    Turn,
    native_account,
    session_id,
)
from tests.native_support import CodexNative
from tests.release_support import REAL_LAB

INSTRUCTION = "Add a function `add(a, b)` to main.py."
ONE_SHOT_PROMPT = f"Work the issue below per the repository's own rules.\n\n{INSTRUCTION}"
SESSION = "session-1"


class FakeInner(CodexNative):
    """A native agent that records its prompt and leaves a trajectory and token usage behind."""

    prompts: list[str] = []
    tokens: tuple[int, int, int, float] | None = (120, 20, 30, 0.02)
    failure: BaseException | None = None
    on_run: Callable[[], None] | None = None

    @staticmethod
    def name() -> str:
        return "fake-inner"

    def version(self) -> str | None:
        return None

    async def setup(self, environment: Any) -> None:
        return None

    async def run(self, instruction: str, environment: Any, context: AgentContext) -> None:
        type(self).prompts.append(instruction)
        on_run = type(self).on_run
        if on_run is not None:
            on_run()
        (self.logs_dir / TRAJECTORY_FILE).write_text(
            json.dumps({"session_id": SESSION, "steps": [{"message": "done"}]})
        )
        failure, tokens = type(self).failure, type(self).tokens
        if failure is not None:
            raise failure
        if tokens is not None:
            n_input, n_cached, n_output, cost = tokens
            context.n_input_tokens = n_input
            context.n_cache_tokens = n_cached
            context.n_output_tokens = n_output
            context.cost_usd = cost

    async def resume(self, instruction: str, environment: Any, context: AgentContext) -> None:
        await self.run(instruction, environment, context)


class FakeEnvironment:
    """A mounted-logs environment: the in-container copy of the turn's logs is a no-op here."""

    capabilities = SimpleNamespace(mounted=True)

    def __init__(self) -> None:
        self.commands: list[str] = []

    async def exec(self, command: str, **_: Any) -> Any:
        self.commands.append(command)
        return SimpleNamespace(return_code=0, stdout="", stderr="")


@pytest.fixture(autouse=True)
def inner(monkeypatch: pytest.MonkeyPatch) -> type[FakeInner]:
    FakeInner.prompts = []
    FakeInner.tokens = (120, 20, 30, 0.02)
    FakeInner.failure = None
    FakeInner.on_run = None

    def create(runner: Any, spec: Any, logs_dir: Path, *, model_name: str | None) -> FakeInner:
        return FakeInner(logs_dir=logs_dir, model_name=model_name)

    monkeypatch.setattr("raidar.subject.workflow.runner.create_inner", create)
    return FakeInner


def _runner(logs_dir: Path) -> WorkflowRunner:
    definition = load_package_workflow(
        REAL_LAB / "workflows" / "raidar" / "one-shot", "raidar/one-shot"
    )
    return WorkflowRunner(
        logs_dir=logs_dir,
        model_name="openai/gpt-6-luna",
        inner_agent={"name": "codex", "kwargs": {"version": "0.156.1"}},
        workflow="raidar/one-shot@1",
        definition=definition.to_json(),
    )


def _run(logs_dir: Path) -> tuple[AgentContext, FakeEnvironment]:
    context = AgentContext()
    environment = FakeEnvironment()
    asyncio.run(_runner(logs_dir).run(INSTRUCTION, environment, context))  # type: ignore[arg-type]
    return context, environment


def test_a_one_shot_checkpoint_is_one_build_turn_sent_the_workflow_prompt(tmp_path: Path) -> None:
    context, _ = _run(tmp_path)

    turn_dir = tmp_path / WORKFLOW_DIR / "build-1"
    assert (turn_dir / PROMPT_FILE).read_text() == ONE_SHOT_PROMPT
    assert FakeInner.prompts == [ONE_SHOT_PROMPT]

    record = json.loads((tmp_path / WORKFLOW_DIR / RECORD_FILE).read_text())
    assert record["kind"] == "raidar-workflow"
    assert record["workflow"] == "raidar/one-shot@1"
    (turn,) = record["turns"]
    assert (turn["stage"], turn["turn"], turn["log_dir"]) == ("build", 1, "workflow/build-1")
    assert (turn["session_id"], turn["resumed"], turn["error"]) == (SESSION, False, None)
    expected = {"input": 120, "cached": 20, "output": 30, "cost_usd": 0.02}
    assert turn["tokens"] == turn["session_total"] == record["tokens"] == expected

    assert (context.n_input_tokens, context.n_cache_tokens, context.n_output_tokens) == (
        120,
        20,
        30,
    )
    assert context.cost_usd == 0.02
    assert context.metadata is not None
    assert context.metadata["workflow"] == record
    assert (tmp_path / TRAJECTORY_FILE).read_bytes() == (turn_dir / TRAJECTORY_FILE).read_bytes()


def test_context_totals_are_set_only_when_every_turns_usage_is_known(tmp_path: Path) -> None:
    FakeInner.tokens = None

    context, _ = _run(tmp_path)

    assert (
        context.n_input_tokens,
        context.n_cache_tokens,
        context.n_output_tokens,
        context.cost_usd,
    ) == (None, None, None, None)
    record = json.loads((tmp_path / WORKFLOW_DIR / RECORD_FILE).read_text())
    assert record["tokens"] is None
    assert record["turns"][0]["tokens"] is None
    assert (tmp_path / WORKFLOW_DIR / "build-1" / PROMPT_FILE).read_text() == ONE_SHOT_PROMPT


def test_a_failing_turn_is_recorded_and_the_error_still_reaches_harbor(tmp_path: Path) -> None:
    FakeInner.failure = RuntimeError("agent crashed")
    context = AgentContext()

    with pytest.raises(RuntimeError, match="agent crashed"):
        asyncio.run(
            _runner(tmp_path).run(INSTRUCTION, FakeEnvironment(), context)  # type: ignore[arg-type]
        )

    record = json.loads((tmp_path / WORKFLOW_DIR / RECORD_FILE).read_text())
    assert record["turns"][0]["error"] == "RuntimeError: agent crashed"
    assert (tmp_path / TRAJECTORY_FILE).is_file()
    assert context.n_input_tokens is None


def test_records_the_agent_wrote_into_the_mounted_logs_are_replaced_by_the_host_kept_ones(
    tmp_path: Path,
) -> None:
    forged = tmp_path / WORKFLOW_DIR / "forged"

    def plant() -> None:
        forged.mkdir(parents=True)
        (forged / "extra.json").write_text("{}")
        (tmp_path / WORKFLOW_DIR / RECORD_FILE).write_text('{"kind": "forged"}')

    FakeInner.on_run = plant

    _run(tmp_path)

    record = json.loads((tmp_path / WORKFLOW_DIR / RECORD_FILE).read_text())
    assert record["kind"] == "raidar-workflow"
    assert not forged.exists()


def test_a_link_planted_in_the_agents_logs_is_named_and_never_copied(tmp_path: Path) -> None:
    secret = tmp_path / "host-secret.txt"
    secret.write_text("host secret")

    def plant() -> None:
        (tmp_path / "planted.txt").symlink_to(secret)
        (tmp_path / "sessions").mkdir(exist_ok=True)
        (tmp_path / "sessions" / "deep.txt").symlink_to(secret)

    FakeInner.on_run = plant

    _run(tmp_path)

    record = json.loads((tmp_path / WORKFLOW_DIR / RECORD_FILE).read_text())
    names = [item["name"] for item in record["integrity"]]
    assert names.count("source_symlink") >= 2
    published = tmp_path / WORKFLOW_DIR / "build-1"
    assert not (published / "planted.txt").exists()
    assert not (published / "planted.txt").is_symlink()
    assert not (published / "sessions" / "deep.txt").is_symlink()


class RemoteEnvironment(FakeEnvironment):
    """A container whose ``/logs/agent`` is not mounted: Harbor downloads it after the run."""

    capabilities = SimpleNamespace(mounted=False)

    def __init__(self, refuse: str | None = None) -> None:
        super().__init__()
        self.refuse = refuse
        self.downloaded: list[str] = []

    async def exec(self, command: str, **options: Any) -> Any:
        if self.refuse is not None and command.removeprefix("umask 022; ").startswith(self.refuse):
            raise ConnectionError("container gone")
        return await super().exec(command, **options)

    async def download_dir(self, source: str, target: Path) -> None:
        self.downloaded.append(source)


def test_a_container_that_cannot_lock_the_logs_does_not_lose_the_record(tmp_path: Path) -> None:
    environment = RemoteEnvironment(refuse="rm -rf")

    asyncio.run(
        _runner(tmp_path).run(INSTRUCTION, environment, AgentContext())  # type: ignore[arg-type]
    )

    record = json.loads((tmp_path / WORKFLOW_DIR / RECORD_FILE).read_text())
    assert record["turns"][0]["error"] is None


def _seen(watch: SessionWatch, staged: Path, ctime: int | None, *, grew: bool = True) -> list[str]:
    """The violation names of observing ``staged`` with every file's container ``ctime``."""
    names = [path.relative_to(staged).as_posix() for path in staged.rglob("*") if path.is_file()]
    ctimes = {name: ctime for name in names if ctime is not None}
    found = watch.observe(staged, ctimes or None, ("sessions",), grew_expected=grew)
    return [item["name"] for item in found]


def test_a_session_log_that_only_grows_is_clean_and_one_written_without_growing_is_touched(
    tmp_path: Path,
) -> None:
    log = tmp_path / "sessions" / "r.jsonl"
    log.parent.mkdir()
    watch = SessionWatch()

    log.write_bytes(b"a\n")
    assert _seen(watch, tmp_path, 1) == []
    log.write_bytes(b"a\nb\n")
    assert _seen(watch, tmp_path, 2) == []  # an append moves the ctime and the size
    assert _seen(watch, tmp_path, 3) == ["session_log_touched"]  # moved, same bytes
    assert _seen(watch, tmp_path, 4, grew=False) == []  # a turn that failed was not owed growth
    assert _seen(watch, tmp_path, 4) == []  # a ctime that did not move is not a touch


def test_a_session_log_whose_container_ctime_is_unknown_is_never_called_touched(
    tmp_path: Path,
) -> None:
    log = tmp_path / "sessions" / "r.jsonl"
    log.parent.mkdir()
    log.write_bytes(b"a\n")
    watch = SessionWatch()

    assert _seen(watch, tmp_path, None) == []
    assert _seen(watch, tmp_path, None) == []  # no ctime now, none before
    assert _seen(watch, tmp_path, 7) == []  # one now, none before
    assert _seen(watch, tmp_path, None) == []  # none now, one before


@pytest.mark.parametrize(
    "later",
    [b"a\n", b"a\nc\n", b"x\nb\nc\n"],
    ids=["shortened", "same-length-rewritten", "longer-with-altered-prefix"],
)
def test_a_session_log_whose_earlier_bytes_changed_is_rewritten(
    tmp_path: Path, later: bytes
) -> None:
    log = tmp_path / "sessions" / "r.jsonl"
    log.parent.mkdir()
    log.write_bytes(b"a\nb\n")
    watch = SessionWatch()
    assert _seen(watch, tmp_path, 1) == []

    log.write_bytes(later)

    found = watch.observe(tmp_path, {"sessions/r.jsonl": 2}, ("sessions",), grew_expected=True)
    assert [item["name"] for item in found] == ["session_log_rewritten"]
    assert "sessions/r.jsonl" in found[0]["detail"]


def test_a_removed_session_file_is_named_and_a_link_in_the_store_is_not_part_of_it(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "a.jsonl").write_bytes(b"1")
    (sessions / "b.jsonl").write_bytes(b"2")
    (tmp_path / "head.json").write_bytes(b"h")
    (sessions / "link.jsonl").symlink_to(tmp_path / "head.json")
    store = ("sessions", "head.json", "absent")
    watch = SessionWatch()
    assert watch.observe(tmp_path, None, store, grew_expected=True) == []

    for gone in (sessions / "b.jsonl", sessions / "a.jsonl", tmp_path / "head.json"):
        gone.unlink()
    found = watch.observe(tmp_path, None, store, grew_expected=True)

    assert [(item["name"], item["detail"].split(":")[0]) for item in found] == [
        ("session_log_removed", "head.json"),
        ("session_log_removed", "sessions/a.jsonl"),
        ("session_log_removed", "sessions/b.jsonl"),
    ]


def test_an_instrument_failure_in_a_turn_is_recorded_invalid_and_not_raised_as_the_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeInner.failure = TrialInvalid("integrity", "the lock moved")
    original_exec = FakeEnvironment.exec

    async def readable_metadata(self: FakeEnvironment, command: str, **options: Any) -> Any:
        result = await original_exec(self, command, **options)
        if command.removeprefix("umask 022; ").startswith("python3 "):
            result.stdout = "{}"
        return result

    monkeypatch.setattr(FakeEnvironment, "exec", readable_metadata)

    context, _ = _run(tmp_path)  # does not raise: the trial is invalid, not the agent wrong

    record = json.loads((tmp_path / WORKFLOW_DIR / RECORD_FILE).read_text())
    assert record["invalid"]["kind"] == "integrity"
    assert record["invalid"]["detail"] == "the lock moved"
    assert [(item["name"], item["turn"]) for item in record["integrity"]] == [
        ("package_integrity", "packages")
    ]
    assert context.n_input_tokens is None


def test_a_cancelled_turn_is_recorded_as_cut_and_the_cancellation_still_propagates(
    tmp_path: Path,
) -> None:
    FakeInner.failure = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            _runner(tmp_path).run(
                INSTRUCTION,
                FakeEnvironment(),  # type: ignore[arg-type]
                AgentContext(),
            )
        )

    record = json.loads((tmp_path / WORKFLOW_DIR / RECORD_FILE).read_text())
    assert record["turns"][0]["error"].startswith("CancelledError")


# --- resumed turns, copy failures, closing and publication ----------------------------


def _record(logs_dir: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((logs_dir / WORKFLOW_DIR / RECORD_FILE).read_text())
    return loaded


def test_a_check_that_asks_resumes_the_session_with_its_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers: list[Resume | None] = [Resume("fix it", REWORK), None]

    async def check(self: PackageStage, checkpoint: Checkpoint, after: str) -> Resume | None:
        return answers.pop(0)

    monkeypatch.setattr(PackageStage, "check", check)

    _run(tmp_path)

    record = _record(tmp_path)
    assert [(item["kind"], item["resumed"]) for item in record["turns"]] == [
        ("first_build", False),
        ("rework", True),
    ]
    assert record["turns"][0]["session_id"] == record["turns"][1]["session_id"] == SESSION
    assert record["tokens"] is not None


class BrokenDownload(RemoteEnvironment):
    async def download_dir(self, source: str, target: Path) -> None:
        raise OSError("disk full")


def test_logs_that_cannot_be_copied_are_recorded_on_the_turn_and_its_usage_stays_unknown(
    tmp_path: Path,
) -> None:
    context = AgentContext()

    asyncio.run(
        _runner(tmp_path).run(INSTRUCTION, BrokenDownload(), context)  # type: ignore[arg-type]
    )

    (turn,) = _record(tmp_path)["turns"]
    assert turn["settle_error"] == "OSError: disk full"
    assert turn["tokens"] is None
    assert context.n_input_tokens is None


def test_a_native_agent_with_no_session_store_needs_no_ctime_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeInner, "session_store", lambda self: ())

    _, environment = _run(tmp_path)

    assert _record(tmp_path)["integrity"] == []
    assert not [
        line
        for line in environment.commands
        if line.removeprefix("umask 022; ").startswith("python3 ")
    ]


def test_an_instrument_failure_when_the_packages_close_is_recorded_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def close(self: PackageStage, checkpoint: Checkpoint, cut: str | None) -> None:
        raise TrialInvalid("integrity", "the lock moved at close")

    monkeypatch.setattr(PackageStage, "close", close)

    _run(tmp_path)

    record = _record(tmp_path)
    assert record["invalid"]["detail"] == "the lock moved at close"
    assert "package_integrity" in [item["name"] for item in record["integrity"]]


def test_a_recorded_host_path_is_rewritten_only_when_it_leads_into_the_staging(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    runner.staging = tmp_path / "stage"
    inside = f"{runner.staging}/gate/g.json"
    outside = f"{tmp_path}/elsewhere/g.json"
    climbing = f"{runner.staging}/../elsewhere/g.json"
    value = {
        "evidence": [{"host_path": inside}, {"host_path": outside}, {"host_path": climbing}],
        "host_path": str(runner.staging),
        "note": {"host_path": "relative/g.json", "other": inside},
        "count": 3,
    }

    found = runner._portable(value)

    assert found["evidence"] == [
        {"host_path": "gate/g.json"},
        {"host_path": outside},
        {"host_path": climbing},
    ]
    assert found["host_path"] == str(runner.staging)
    assert found["note"] == {"host_path": "relative/g.json", "other": inside}
    assert found["count"] == 3


def test_publication_replaces_what_the_agent_left_and_publishes_no_linked_evidence(
    tmp_path: Path,
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    runner = _runner(logs)
    runner.staging = tmp_path / "stage"
    kept, forged, linked = EVIDENCE_DIRS[1], EVIDENCE_DIRS[2], EVIDENCE_DIRS[3]
    (runner.staging / kept).mkdir(parents=True)
    (runner.staging / kept / "g.json").write_text("host-kept")
    (runner.staging / WORKFLOW_DIR).mkdir()
    (runner.staging / WORKFLOW_DIR / RECORD_FILE).write_text("{}")
    (tmp_path / "outside").mkdir()
    (runner.staging / linked).symlink_to(tmp_path / "outside")
    (logs / forged).mkdir(parents=True)
    (logs / forged / "forged.json").write_text("agent-written")
    (logs / kept).mkdir()
    (logs / kept / "old.json").write_text("agent-written")

    runner._publish(runner.staging / WORKFLOW_DIR, runner.staging / "no-such-trajectory.json")

    assert (logs / kept / "g.json").read_text() == "host-kept"
    assert not (logs / kept / "old.json").exists()
    assert not (logs / forged).exists()
    assert not (logs / linked).exists()
    assert (logs / WORKFLOW_DIR / RECORD_FILE).read_text() == "{}"
    assert not (logs / TRAJECTORY_FILE).exists()
    assert not runner.staging.exists()


# --- the session evidence of a turn ------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (None, None),
        ("not json", None),
        ("[1]", None),
        ('{"session_id": ""}', None),
        ('{"session_id": 7}', None),
        ('{"session_id": "abc"}', "abc"),
    ],
)
def test_a_turns_session_id_is_the_trajectorys_non_empty_string_or_none(
    tmp_path: Path, content: str | None, expected: str | None
) -> None:
    if content is not None:
        (tmp_path / TRAJECTORY_FILE).write_text(content)

    assert session_id(tmp_path) == expected


class Meter:
    def __init__(self) -> None:
        self.calls = 0

    def accounting(self, context: AgentContext, previous: Any) -> Any:
        self.calls += 1
        return ZERO, ZERO


def _turn(index: int, *, resumed: bool) -> Turn:
    return Turn("build", "k", index, resumed, "sha", "then", 0.0, session_id="s")


def test_a_resumed_turn_with_no_measured_earlier_total_has_unknown_tokens() -> None:
    first, second = _turn(1, resumed=False), _turn(2, resumed=True)
    meter = Meter()

    native_account(second, Run([first, second]), meter, AgentContext())

    assert second.tokens is None
    assert meter.calls == 0

    first.session_total = ZERO
    native_account(second, Run([first, second]), meter, AgentContext())

    assert second.tokens == ZERO
    assert meter.calls == 1
