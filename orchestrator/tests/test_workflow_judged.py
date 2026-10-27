"""The judged define-build-verify loop: a scripted builder, the real gate and a fake Jev transport.

Each scenario runs one checkpoint through the real runner, the real define tool and the real gate;
only the agent (scripted edits and replies) and the network (a transport answering Jev's
decisions endpoint) are faked. The judge runs on the host: the key is read from the host's
environment by the recorder and the sandbox never sees it.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from harbor.models.agent.context import AgentContext

from raidar.judge.client import TransportResponse
from raidar.judge.live import RecordedJev
from raidar.judge.protocol import JEV_PINNED_MODEL
from raidar.subject.workflow import asking, define
from raidar.subject.workflow.definition import load_workflow
from raidar.subject.workflow.runner import WorkflowRunner
from raidar.subject.workflow.stage import Checkpoint
from tests.release_support import REAL_LAB
from tests.workflow_support import (
    CODE,
    FAILING,
    PASSING,
    AgentTurn,
    LocalEnvironment,
    ScriptedAgent,
    commit,
    write,
)

SECRET = "sk-or-v1-host-only-0123456789abcdef"
TASK = "Add an add function that returns the sum of its two numbers."
TEST_ID = "test_calc.py::test_add"
BLOCK = """title: Addition
objective: Add two numbers and return their sum.
feature F1: Addition
outcome: Callers can add two numbers.
ac F1.AC1: Addition returns the sum of two numbers.
source: U1
aspect: two numbers are supplied -> their sum is returned
example: `2` and `3` -> `5`
"""
MORE = PASSING + "\n\ndef test_other() -> None:\n    assert 1 + 1 == 2\n"


def reply(*tests: str, **extra: Any) -> str:
    """A builder's reply: its account, no ADR and each given test mapped to F1.AC1."""
    mapped = [{"criterion": "F1.AC1", "tests": list(tests)}] if tests else []
    return "Done.\n" + json.dumps({"account": "built", "adrs": [], "ac_tests": mapped, **extra})


class Jev:
    """Answers each aspect's question from ``answer``; records what the transport received."""

    def __init__(self, answer: Callable[[int, str], float | int]) -> None:
        self.answer = answer
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []

    def post(
        self, url: str, *, headers: Mapping[str, str], body: bytes, timeout_s: float
    ) -> TransportResponse:
        request = json.loads(body)
        self.requests.append(request)
        self.headers.append(dict(headers))
        given = self.answer(len(self.requests), request["state"]["aspect"]["situation"])
        if isinstance(given, int) and not isinstance(given, bool) and given >= 400:
            return TransportResponse(given, b'{"error": "boom"}')
        answers = {name: {"type": "noul", "noul": given} for name in request["questions"]}
        document = {
            "id": f"gen-{len(self.requests)}",
            "model": JEV_PINNED_MODEL,
            "provider": "TypeSafe",
            "answers": answers,
            "usage": {"input_tokens": 100, "output_tokens": 1, "cost": 0.001},
        }
        return TransportResponse(200, json.dumps(document).encode())


class RecordingEnvironment(LocalEnvironment):
    """The container as the host's shell, remembering every command it was asked to run."""

    def __init__(self, env: dict[str, str]) -> None:
        super().__init__(env)
        self.commands: list[str] = []

    async def exec(self, command: str, user: str | None = None, **rest: Any) -> Any:
        self.commands.append(command)
        return await super().exec(command, user, **rest)


@pytest.fixture
def define_paths(
    tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> define.DefinePaths:
    paths = define.DefinePaths(
        workspace=str(workspace),
        home=str(tmp_path / "define-opt"),
        command=str(tmp_path / "define"),
    )
    monkeypatch.setattr(define, "PATHS", paths)
    monkeypatch.setattr(
        define,
        "tools_install_command",
        lambda p: f"mkdir -p {shlex.quote(p.home)} && ln -sfn {shlex.quote(sys.prefix)} "
        f"{shlex.quote(p.home + '/tools')}",
    )
    return paths


@pytest.fixture
def jev(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[[int, str], float | int]], Jev]:
    """Install a fake Jev; the recorder reads its key from the host environment as in a trial."""
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    monkeypatch.setattr(asking, "BACKOFF_S", 0.0)

    def install(answer: Callable[[int, str], float | int]) -> Jev:
        fake = Jev(answer)
        monkeypatch.setattr(
            asking,
            "new_jev",
            lambda directory, timeout_s: RecordedJev(
                directory,
                timeout_s=timeout_s,
                transport=fake,
                environ={"OPENROUTER_API_KEY": SECRET},
            ),
        )
        return fake

    return install


def call(paths: define.DefinePaths, env: dict[str, str], *args: str, block: str = "") -> None:
    done = subprocess.run(
        [paths.command, *args], input=block, text=True, capture_output=True, env=env, check=False
    )
    assert done.returncode == 0, done.stderr + done.stdout


def run(
    tmp_path: Path,
    env: dict[str, str],
    turns: list[AgentTurn],
    *,
    max_passes: int = 3,
    timeout_s: float | None = None,
) -> tuple[dict[str, Any], RecordingEnvironment]:
    ScriptedAgent.turns = list(turns)
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    definition: Any = load_workflow(REAL_LAB / "workflows", "define-build-verify").to_json()
    definition.pop("components")
    for stage in definition["stages"].values():
        stage.pop("skills", None)
    definition["stages"]["verify"]["max_passes"] = max_passes
    environment = RecordingEnvironment(env)
    runner = WorkflowRunner(
        logs_dir=logs,
        environment_logs_dir=PurePosixPath(logs),
        model_name="openai/gpt-6-luna",
        inner_agent={"name": "codex"},
        workflow="define-build-verify@3",
        definition=definition,
        phase_timeout_s=timeout_s,
    )
    asyncio.run(runner.run(TASK, environment, AgentContext()))  # type: ignore[arg-type]
    return json.loads((logs / "workflow" / "workflow.json").read_text()), environment


def first_build(
    paths: define.DefinePaths, env: dict[str, str], text: str | None = None, *, tests: str = PASSING
) -> AgentTurn:
    """The builder's first turn: define, build, test, commit, and reply."""

    def turn(ws: Path, prompt: str) -> str:
        call(paths, env, "put", block=BLOCK)
        call(paths, env, "done", "ready")
        write(ws, calc=CODE, test_calc=tests)
        assert commit(ws, env) == 0
        return reply(TEST_ID) if text is None else text

    return turn


def rework(env: dict[str, str], seen: list[str]) -> AgentTurn:
    """A rework turn that adds a test, commits and re-lists the criterion's tests."""

    def turn(ws: Path, prompt: str) -> str:
        seen.append(prompt)
        write(ws, test_calc=MORE)
        assert commit(ws, env) == 0
        return reply(TEST_ID)

    return turn


def kinds(record: dict[str, Any]) -> list[str]:
    return [turn["kind"] for turn in record["turns"]]


def test_every_criterion_closed_on_the_first_pass_is_verified(
    tmp_path: Path, define_paths: define.DefinePaths, agent_env: dict[str, str], jev: Any
) -> None:
    fake = jev(lambda call_number, situation: 0.95)

    record, _ = run(tmp_path, agent_env, [first_build(define_paths, agent_env)])

    stage = record["verify"]
    assert (stage["ending"], stage["gate_ending"], stage["passes"]) == (
        "verified",
        "gate_passed",
        1,
    )
    assert kinds(record) == ["first_build"]
    (judged,) = stage["judged"]
    assert (judged["closed"], judged["open"], judged["undecided"]) == (["F1.AC1"], [], [])
    (criterion,) = judged["criteria"]
    (aspect,) = criterion["aspects"]
    assert (criterion["status"], criterion["tests"], criterion["test_results"]) == (
        "closed",
        [TEST_ID],
        {TEST_ID: "passed"},
    )
    assert (aspect["probability"], aspect["status"], aspect["calls"]) == (0.95, "closed", [0])
    (call_record,) = stage["judge_calls"]
    assert (call_record["index"], call_record["pass"], call_record["attempt"]) == (0, 1, 1)
    logs = tmp_path / "logs"
    request = json.loads((logs / call_record["request_file"]).read_text())
    assert request["state"]["aspect"] == {
        "situation": "two numbers are supplied",
        "outcome": "their sum is returned",
    }
    assert request["state"]["tests"][0]["id"] == TEST_ID
    assert (
        json.loads((logs / call_record["response_file"]).read_text())["model"] == JEV_PINNED_MODEL
    )
    assert call_record["result"]["ok"]
    assert call_record["resolved_model"] == JEV_PINNED_MODEL
    assert stage["judge"]["model"] == JEV_PINNED_MODEL
    assert record["build"]["ac_tests"] == {"F1.AC1": [TEST_ID]}
    assert record["build"]["sources"] == "workflow/sources/final"
    assert (logs / "workflow/sources/final/test_calc.py").read_text() == PASSING
    assert (logs / judged["sources"] / "test_calc.py").is_file()
    assert len(fake.requests) == 1


def test_an_open_criterion_comes_back_as_pushback_and_closes_after_the_rework(
    tmp_path: Path, define_paths: define.DefinePaths, agent_env: dict[str, str], jev: Any
) -> None:
    jev(lambda call_number, situation: 0.1 if call_number == 1 else 0.95)
    seen: list[str] = []

    record, _ = run(
        tmp_path, agent_env, [first_build(define_paths, agent_env), rework(agent_env, seen)]
    )

    stage = record["verify"]
    assert (stage["ending"], stage["passes"]) == ("verified", 2)
    assert kinds(record) == ["first_build", "rework"]
    assert [turn["stage"] for turn in record["turns"]] == ["build", "verify"]
    first, second = stage["judged"]
    assert (first["open"], first["closed"]) == (["F1.AC1"], [])
    assert (second["open"], second["closed"]) == ([], ["F1.AC1"])
    (prompt,) = seen
    assert "F1.AC1.A1 (p 0.10): no test sets up two numbers are supplied" in prompt
    assert "> " in prompt
    assert "The gate's result on the work the engine checked:" in prompt
    assert "verify-gate: passed" in prompt
    assert "%%%" not in prompt
    assert "Reply with ONLY one JSON object of this shape" in prompt
    assert [gate["turn_kind"] for gate in stage["gates"]] == ["first_build", "rework"]
    assert [call["pass"] for call in stage["judge_calls"]] == [1, 2]
    assert record["build"]["repairs"] == {"syntax": 0, "semantic": 0}


def test_a_judge_that_fails_every_attempt_leaves_the_aspect_undecided_and_the_run_unverified(
    tmp_path: Path, define_paths: define.DefinePaths, agent_env: dict[str, str], jev: Any
) -> None:
    fake = jev(lambda call_number, situation: 503)

    record, _ = run(tmp_path, agent_env, [first_build(define_paths, agent_env)], max_passes=1)

    stage = record["verify"]
    assert stage["ending"] == "pass_limit"
    (judged,) = stage["judged"]
    assert (judged["closed"], judged["open"], judged["undecided"]) == ([], [], ["F1.AC1"])
    (aspect,) = judged["criteria"][0]["aspects"]
    assert (aspect["probability"], aspect["status"], aspect["calls"]) == (
        None,
        "undecided",
        [0, 1, 2, 3],
    )
    assert len(fake.requests) == 4  # the first attempt and three retries
    assert [call["attempt"] for call in stage["judge_calls"]] == [1, 2, 3, 4]
    assert {call["result"]["reason"] for call in stage["judge_calls"]} == {"http_error"}
    assert all(call["probability"] is None for call in stage["judge_calls"])


def test_a_failure_that_cannot_improve_is_not_retried(
    tmp_path: Path, define_paths: define.DefinePaths, agent_env: dict[str, str], jev: Any
) -> None:
    fake = jev(lambda call_number, situation: 404)

    record, _ = run(tmp_path, agent_env, [first_build(define_paths, agent_env)], max_passes=1)

    assert len(fake.requests) == 1
    assert record["verify"]["judged"][0]["undecided"] == ["F1.AC1"]


def test_a_rework_that_does_not_fit_the_time_left_is_declined_and_the_tree_stays_judged(
    tmp_path: Path,
    define_paths: define.DefinePaths,
    agent_env: dict[str, str],
    jev: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jev(lambda call_number, situation: 0.1)
    monkeypatch.setattr(Checkpoint, "remaining", lambda self: 0.5)  # less than a gate run

    record, _ = run(tmp_path, agent_env, [first_build(define_paths, agent_env)])

    stage = record["verify"]
    assert (stage["ending"], stage["passes"]) == ("budget_exhausted", 1)
    assert kinds(record) == ["first_build"]
    assert stage["judged"][0]["open"] == ["F1.AC1"]
    assert "restored" not in stage


def test_no_judge_pass_starts_after_the_deadline_and_the_graded_tree_is_what_the_agent_left(
    tmp_path: Path,
    define_paths: define.DefinePaths,
    agent_env: dict[str, str],
    jev: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = jev(lambda call_number, situation: 0.95)
    monkeypatch.setattr(Checkpoint, "remaining", lambda self: -1.0)

    record, _ = run(tmp_path, agent_env, [first_build(define_paths, agent_env)])

    stage = record["verify"]
    assert (stage["ending"], stage["judged"], stage["judge_calls"]) == ("deadline", [], [])
    assert "restored" not in stage
    assert stage["gates"][0]["tree"]
    assert fake.requests == []


def test_the_key_stays_on_the_host(
    tmp_path: Path, define_paths: define.DefinePaths, agent_env: dict[str, str], jev: Any
) -> None:
    fake = jev(lambda call_number, situation: 0.95)

    record, environment = run(tmp_path, agent_env, [first_build(define_paths, agent_env)])

    assert fake.headers[0]["Authorization"] == f"Bearer {SECRET}"  # the host used it
    assert not [command for command in environment.commands if SECRET in command]
    assert SECRET not in json.dumps(environment.env)
    persisted = [
        path.read_text(errors="replace")
        for path in (tmp_path / "logs").rglob("*")
        if path.is_file()
    ]
    assert persisted
    assert not [text for text in persisted if SECRET in text]
    assert record["verify"]["ending"] == "verified"


def test_a_reply_with_no_json_is_repaired_once_and_an_unmapped_criterion_once_more(
    tmp_path: Path, define_paths: define.DefinePaths, agent_env: dict[str, str], jev: Any
) -> None:
    jev(lambda call_number, situation: 0.95)
    prompts: list[str] = []

    def again(text: str) -> AgentTurn:
        def turn(ws: Path, prompt: str) -> str:
            prompts.append(prompt)
            return text

        return turn

    record, _ = run(
        tmp_path,
        agent_env,
        [
            first_build(define_paths, agent_env, "I built it."),
            again(reply()),
            again(reply(TEST_ID, stray="x")),
        ],
    )

    assert kinds(record) == ["first_build", "repair", "repair"]
    assert record["build"]["repairs"] == {"syntax": 1, "semantic": 1}
    assert "reply holds no JSON object" in prompts[0]
    assert "`ac_tests` maps no test to `F1.AC1`" in prompts[1]
    assert all("Reply with ONLY one JSON object of this shape" in prompt for prompt in prompts)
    replies = record["build"]["replies"]
    assert [item["fault"] for item in replies] == ["syntax", "semantic", None]
    assert replies[2]["dropped"] == ["/stray"]
    assert record["build"]["ac_tests"] == {"F1.AC1": [TEST_ID]}
    assert record["verify"]["ending"] == "verified"
    saved = json.loads((tmp_path / "logs" / replies[0]["after"] / "reply.json").read_text())
    assert saved["fault"] == "syntax"
    assert saved["value"] is None


def test_each_kind_of_fault_is_repaired_once_and_a_second_semantic_fault_stands(
    tmp_path: Path, define_paths: define.DefinePaths, agent_env: dict[str, str], jev: Any
) -> None:
    jev(lambda call_number, situation: 0.95)

    def again(text: str) -> AgentTurn:
        return lambda ws, prompt: text

    record, _ = run(
        tmp_path,
        agent_env,
        [first_build(define_paths, agent_env, reply()), again(reply()), again("never")],
        max_passes=1,
    )

    assert kinds(record) == ["first_build", "repair"]  # no second semantic repair
    assert record["build"]["repairs"] == {"syntax": 0, "semantic": 1}
    assert [item["ok"] for item in record["build"]["replies"]] == [False, False]
    assert record["verify"]["judged"][0]["criteria"][0]["failing"] == ["unmapped"]
    assert record["verify"]["ending"] == "pass_limit"


def test_a_mapped_test_in_a_file_the_tree_lacks_is_a_semantic_fault_and_repaired(
    tmp_path: Path, define_paths: define.DefinePaths, agent_env: dict[str, str], jev: Any
) -> None:
    jev(lambda call_number, situation: 0.95)
    prompts: list[str] = []

    def fixed(ws: Path, prompt: str) -> str:
        prompts.append(prompt)
        return reply(TEST_ID)

    record, _ = run(
        tmp_path,
        agent_env,
        [first_build(define_paths, agent_env, reply("missing/test_nowhere.py::test_x")), fixed],
    )

    assert "files the working tree does not have" in prompts[0]
    assert "`missing/test_nowhere.py::test_x`" in prompts[0]
    assert [item["fault"] for item in record["build"]["replies"]] == ["semantic", None]
    assert record["verify"]["ending"] == "verified"


def test_a_regression_criterion_closes_on_a_test_that_passed_on_the_base(
    tmp_path: Path, define_paths: define.DefinePaths, agent_env: dict[str, str], jev: Any
) -> None:
    jev(lambda call_number, situation: 0.95)
    regression = BLOCK + (
        "ac F1.AC2: Earlier behaviour keeps working.\nsource: U1\nkind: regression\n"
    )
    ScriptedAgent.workspace.joinpath("base_test.py").write_text(
        "def test_base() -> None:\n    assert 1 + 1 == 2\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=ScriptedAgent.workspace, check=True)
    subprocess.run(["git", "add", "-A"], cwd=ScriptedAgent.workspace, check=True, env=agent_env)
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"],
        cwd=ScriptedAgent.workspace,
        env=agent_env,
        check=False,
    )
    assert done.returncode == 0

    def build(ws: Path, prompt: str) -> str:
        call(define_paths, agent_env, "put", block=regression)
        call(define_paths, agent_env, "done", "ready")
        write(ws, calc=CODE, test_calc=PASSING)
        assert commit(ws, agent_env) == 0
        mapped = [
            {"criterion": "F1.AC1", "tests": [TEST_ID]},
            {"criterion": "F1.AC2", "tests": ["base_test.py::test_base"]},
        ]
        return "ok " + json.dumps({"account": "built", "adrs": [], "ac_tests": mapped})

    record, _ = run(tmp_path, agent_env, [build])

    stage = record["verify"]
    assert stage["baseline_passed"] == ["base_test.py::test_base"]
    assert stage["baseline"]["status"] in ("passed", "failed")
    (judged,) = stage["judged"]
    assert judged["closed"] == ["F1.AC1", "F1.AC2"]
    regression_record = judged["criteria"][1]
    assert (regression_record["kind"], regression_record["aspects"]) == ("regression", [])
    assert stage["ending"] == "verified"


def test_a_refusal_that_outlasts_its_fix_rounds_ends_gate_refused_with_the_tree_judged(
    tmp_path: Path, define_paths: define.DefinePaths, agent_env: dict[str, str], jev: Any
) -> None:
    fake = jev(lambda call_number, situation: 0.95)

    def build(ws: Path, prompt: str) -> str:
        write(ws, calc=CODE, test_calc=PASSING)
        return reply(TEST_ID)

    def again(ws: Path, prompt: str) -> str:
        return reply(TEST_ID)

    record, _ = run(tmp_path, agent_env, [build, again, again, again])

    stage = record["verify"]
    assert record["define"]["ending"] == "definition_unusable"  # never sealed: the gate refuses
    assert kinds(record) == ["first_build", "define", "gate_fix", "gate_fix"]
    assert (stage["ending"], stage["gate_ending"], stage["fix_rounds"]) == (
        "gate_refused",
        "gate_refused",
        2,
    )
    assert [gate["verdict"] for gate in stage["gates"]] == ["refused"] * 3
    assert stage["judged"][0]["criteria"] == []
    assert fake.requests == []


def test_a_draft_sealed_after_the_define_ended_unusable_never_passes_the_gate_or_verifies(
    tmp_path: Path, define_paths: define.DefinePaths, agent_env: dict[str, str], jev: Any
) -> None:
    fake = jev(lambda call_number, situation: 0.95)

    def build(ws: Path, prompt: str) -> str:
        write(ws, calc=CODE, test_calc=PASSING)
        return reply(TEST_ID)

    def repair(ws: Path, prompt: str) -> str:
        return reply(TEST_ID)

    def seal_late(ws: Path, prompt: str) -> str:
        call(define_paths, agent_env, "put", block=BLOCK)
        call(define_paths, agent_env, "done", "ready")
        assert commit(ws, agent_env) == 1  # the lab's closed state keeps the prerequisite failing
        return reply(TEST_ID)

    def commit_again(ws: Path, prompt: str) -> str:
        write(ws, calc=CODE + "\n")
        assert commit(ws, agent_env) == 1
        return reply(TEST_ID)

    record, _ = run(tmp_path, agent_env, [build, repair, seal_late, commit_again])

    stage = record["verify"]
    assert (record["define"]["ending"], record["define"]["closed"]) == ("definition_unusable", True)
    assert record["define"]["definition"] is None
    assert (stage["ending"], stage["gate_ending"]) == ("gate_refused", "gate_refused")
    assert [gate["verdict"] for gate in stage["gates"]] == ["refused"] * 3
    assert stage["judged"][0]["criteria"] == []
    assert fake.requests == []


def test_a_fix_turn_that_does_not_fit_is_declined_and_the_refused_tree_is_judged(
    tmp_path: Path,
    define_paths: define.DefinePaths,
    agent_env: dict[str, str],
    jev: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jev(lambda call_number, situation: 0.95)
    monkeypatch.setattr(Checkpoint, "remaining", lambda self: 0.5)

    def failing(ws: Path, prompt: str) -> str:
        call(define_paths, agent_env, "put", block=BLOCK)
        call(define_paths, agent_env, "done", "ready")
        write(ws, calc=CODE, test_calc=FAILING)
        return reply(TEST_ID)

    record, _ = run(tmp_path, agent_env, [failing])

    stage = record["verify"]
    assert (stage["ending"], stage["gate_ending"], stage["fix_rounds"]) == (
        "budget_exhausted",
        "gate_refused",
        0,
    )
    assert kinds(record) == ["first_build"]
    (judged,) = stage["judged"]
    assert judged["gate_verdict"] == "refused"
    assert judged["criteria"][0]["test_results"] == {TEST_ID: "failed"}
    assert judged["criteria"][0]["failing"] == ["precheck"]
