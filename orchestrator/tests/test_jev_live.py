"""Live Jev mode: every attempt recorded raw, pinned model, no credential, key off the sandbox."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from raidar.engine.trial.environment import RunScopedDockerEnvironment
from raidar.judge.client import (
    FailureReason,
    JevClient,
    JevFailure,
    JevSuccess,
    TransportError,
    TransportTimeoutError,
)
from raidar.judge.live import LIVE_MODEL, RecordedJev
from raidar.judge.protocol import JEV_MODEL, JEV_PINNED_MODEL, NoulQuestion
from raidar.judge.secrets import (
    JUDGE_KEY_ENV,
    SecretIsolationError,
    absence_evidence,
    exec_env,
    leaked_names,
    require_key_absent,
)
from tests.jev_support import TEST_KEY, RecordedTransport, decision_body, noul_answer

QUESTIONS = {
    "q1": NoulQuestion(instructions="Is it so?"),
    "q2": NoulQuestion(instructions="Again?"),
}
STATE = {"criterion": "AC1", "tests": ["def test_x(): ..."]}
ENV = {JUDGE_KEY_ENV: TEST_KEY}


def _live(tmp_path: Path, transport: RecordedTransport) -> RecordedJev:
    return RecordedJev(tmp_path / "judge", transport=transport, environ=ENV)


def test_success_records_raw_request_and_response(tmp_path: Path) -> None:
    transport = RecordedTransport(answer_for=lambda _id, _q: noul_answer(0.9))
    live = _live(tmp_path, transport)
    result = live.decide(STATE, QUESTIONS)
    assert isinstance(result, JevSuccess)
    assert result.answers["q1"].noul == 0.9  # type: ignore[union-attr]
    (call,) = live.calls
    root = tmp_path / "judge"
    request = (root / call["request_file"]).read_bytes()
    assert (
        request
        == json.dumps(transport.calls[0]["body"], ensure_ascii=False, sort_keys=True).encode()
    )
    assert (root / call["response_file"]).read_bytes() == decision_body(
        {"q1": noul_answer(0.9), "q2": noul_answer(0.9)}
    )
    assert call["result"]["ok"] is True
    assert call["resolved_model"] == JEV_PINNED_MODEL
    assert call["seconds"] >= 0
    assert (root / call["result_file"]).exists()
    assert json.loads((root / "calls.jsonl").read_text().splitlines()[0])["index"] == 1


def test_request_names_the_immutable_model_not_the_alias(tmp_path: Path) -> None:
    transport = RecordedTransport()
    _live(tmp_path, transport).decide(STATE, QUESTIONS)
    assert transport.calls[0]["body"]["model"] == LIVE_MODEL == JEV_PINNED_MODEL
    assert LIVE_MODEL != JEV_MODEL


def test_unpinned_response_model_is_a_recorded_failure(tmp_path: Path) -> None:
    live = _live(tmp_path, RecordedTransport(model="typesafe/jev-1.13"))
    result = live.decide(STATE, QUESTIONS)
    assert isinstance(result, JevFailure)
    assert result.reason is FailureReason.MODEL_MISMATCH
    assert live.calls[0]["response_file"] is not None
    assert live.calls[0]["result"]["reason"] == "model_mismatch"


def test_a_client_for_another_model_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="needs a client for"):
        RecordedJev(tmp_path, client=JevClient(TEST_KEY, transport=RecordedTransport()))


@pytest.mark.parametrize(
    ("transport", "reason", "status"),
    [
        (RecordedTransport(status=503), FailureReason.HTTP_ERROR, 503),
        (RecordedTransport(status=402), FailureReason.CREDITS, 402),
        (RecordedTransport(raw_body=b"not json"), FailureReason.INVALID_RESPONSE, None),
    ],
)
def test_failed_attempts_are_recorded_one_per_attempt(
    tmp_path: Path, transport: RecordedTransport, reason: FailureReason, status: int | None
) -> None:
    live = _live(tmp_path, transport)
    for _ in range(3):
        result = live.decide(STATE, QUESTIONS)
        assert isinstance(result, JevFailure)
        assert result.reason is reason
    assert [call["index"] for call in live.calls] == [1, 2, 3]
    assert len({call["request_file"] for call in live.calls}) == 3
    assert all(call["response_file"] for call in live.calls)
    assert live.calls[0]["http_status"] == status
    assert len(list((tmp_path / "judge").glob("call-*.response.json"))) == 3


@pytest.mark.parametrize(
    ("raises", "reason"),
    [(TransportTimeoutError, FailureReason.TIMEOUT), (TransportError, FailureReason.NETWORK)],
)
def test_transport_failures_record_the_request_without_a_response(
    tmp_path: Path, raises: type[Exception], reason: FailureReason
) -> None:
    live = _live(tmp_path, RecordedTransport(raises=raises))
    result = live.decide(STATE, QUESTIONS)
    assert isinstance(result, JevFailure)
    assert result.reason is reason
    (call,) = live.calls
    assert call["request_file"]
    assert call["response_file"] is None
    assert call["transport_error"] == raises.__name__


def test_missing_key_is_a_recorded_failure_and_sends_nothing(tmp_path: Path) -> None:
    transport = RecordedTransport()
    live = RecordedJev(tmp_path / "judge", transport=transport, environ={})
    result = live.decide(STATE, QUESTIONS)
    assert isinstance(result, JevFailure)
    assert result.reason is FailureReason.MISSING_KEY
    assert transport.calls == []
    (call,) = live.calls
    assert call["request_file"] is None
    assert call["response_file"] is None
    assert call["result"]["reason"] == "missing_key"
    assert (tmp_path / "judge" / call["result_file"]).exists()


def test_state_too_large_is_recorded_without_a_request(tmp_path: Path) -> None:
    transport = RecordedTransport()
    live = _live(tmp_path, transport)
    result = live.decide({"blob": "x" * 100_000}, QUESTIONS)
    assert isinstance(result, JevFailure)
    assert result.reason is FailureReason.STATE_TOO_LARGE
    assert transport.calls == []
    assert live.calls[0]["request_file"] is None


def test_an_echoed_key_never_reaches_a_record(tmp_path: Path) -> None:
    echoed = json.dumps({"error": f"bad credential {TEST_KEY}"}).encode()
    live = _live(tmp_path, RecordedTransport(status=401, raw_body=echoed))
    live.decide(STATE, QUESTIONS)
    live.decide({"note": f"state holding {TEST_KEY}"}, QUESTIONS)
    for path in (tmp_path / "judge").iterdir():
        assert TEST_KEY.encode() not in path.read_bytes(), path.name
    assert b"[REDACTED]" in (tmp_path / "judge" / live.calls[0]["response_file"]).read_bytes()
    for call in live.calls:
        assert "Authorization" not in json.dumps(call)


def test_offline_client_still_sends_the_alias_and_needs_no_recorder() -> None:
    transport = RecordedTransport()
    result = JevClient(TEST_KEY, transport=transport).decide(STATE, QUESTIONS)
    assert isinstance(result, JevSuccess)
    assert transport.calls[0]["body"]["model"] == JEV_MODEL
    assert transport.calls[0]["headers"]["Authorization"] == f"Bearer {TEST_KEY}"


def test_key_is_refused_by_name_and_by_value() -> None:
    host = {JUDGE_KEY_ENV: TEST_KEY}
    assert leaked_names({"PATH": "/bin"}, host) == []
    assert leaked_names({JUDGE_KEY_ENV: ""}, host) == [JUDGE_KEY_ENV]
    assert leaked_names({"ALIAS": f"Bearer {TEST_KEY}"}, host) == ["ALIAS"]
    with pytest.raises(SecretIsolationError) as refused:
        require_key_absent({"ALIAS": TEST_KEY}, "the task environment", host)
    assert TEST_KEY not in str(refused.value)
    assert "ALIAS" in str(refused.value)
    assert exec_env(["exec", "-T", "-e", "A=1", "-e", f"B={TEST_KEY}", "main", "ls"]) == {
        "A": "1",
        "B": TEST_KEY,
    }


def test_absence_evidence_lists_names_only() -> None:
    evidence = absence_evidence({"task": ["B", "A"], "agent": ["OPENAI_API_KEY"]}, ENV)
    assert evidence["sandbox_env_names"] == {"task": ["A", "B"], "agent": ["OPENAI_API_KEY"]}
    assert evidence["judge_key_in_sandbox"] is False
    assert evidence["host_key_present"] is True
    assert TEST_KEY not in json.dumps(evidence)
    assert absence_evidence({"task": [JUDGE_KEY_ENV]}, {})["judge_key_in_sandbox"] is True


def _bare_environment(monkeypatch: pytest.MonkeyPatch) -> RunScopedDockerEnvironment:
    monkeypatch.setenv(JUDGE_KEY_ENV, TEST_KEY)
    environment = object.__new__(RunScopedDockerEnvironment)
    environment._main_container = None
    environment._persistent_env = {}
    environment._compose_task_env = {}
    environment._post_start = []
    return environment


def test_sandbox_commands_never_carry_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    environment = _bare_environment(monkeypatch)
    for leaked in (
        ["exec", "-e", f"{JUDGE_KEY_ENV}=x", "main", "ls"],
        ["exec", "-e", f"HELPER={TEST_KEY}", "main", "ls"],
    ):
        with pytest.raises(SecretIsolationError):
            asyncio.run(environment._run_docker_compose_command(leaked))


def test_sandbox_start_refuses_the_key_in_task_or_trial_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _bare_environment(monkeypatch)
    environment._compose_task_env = {"X": TEST_KEY}  # a task env of "${OPENROUTER_API_KEY}"
    with pytest.raises(SecretIsolationError, match="task environment"):
        asyncio.run(environment.start(False))
    environment._compose_task_env = {}
    environment._persistent_env = {JUDGE_KEY_ENV: TEST_KEY}
    with pytest.raises(SecretIsolationError, match="trial environment"):
        asyncio.run(environment.start(False))


def test_concurrent_attempts_get_distinct_indices_and_files(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    live = _live(tmp_path, RecordedTransport())
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _n: live.decide(STATE, QUESTIONS), range(32)))
    assert all(isinstance(result, JevSuccess) for result in results)
    assert [call["index"] for call in live.calls] == list(range(1, 33))
    assert len(list((tmp_path / "judge").glob("call-*.request.json"))) == 32
    assert len((tmp_path / "judge" / "calls.jsonl").read_text().splitlines()) == 32
