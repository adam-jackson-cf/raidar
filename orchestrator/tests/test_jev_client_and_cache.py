"""H1: the Jev client contract, pinned-model check, redaction and $0 cached replay."""

from __future__ import annotations

import http.server
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import requests

from raidar.jev.audit import DecisionStage
from raidar.jev.cache import decision_key
from raidar.jev.client import (
    FailureReason,
    JevClient,
    JevFailure,
    JevSuccess,
    RequestsTransport,
    api_key_from_env,
)
from raidar.jev.protocol import (
    INVALID_MODEL_ID,
    JEV_ENDPOINT,
    JEV_MODEL,
    JEV_PINNED_MODEL,
    JEV_STATE_CHAR_LIMIT,
    ChoiceQuestion,
    NoulQuestion,
    ScoreQuestion,
)
from raidar.jev.redaction import clip, redact
from tests.jev_support import (
    SUFFICIENT,
    TEST_KEY,
    RecordedTransport,
    TransportError,
    TransportTimeoutError,
    make_runtime,
    noul_answer,
)

QUESTIONS = {"swallows_errors": NoulQuestion(instructions="Does the handler swallow errors?")}
STATE = {"symbol": {"path": "app.py", "source": "try:\n    run()\nexcept Exception:\n    pass\n"}}


def test_cached_replay_makes_no_provider_call_and_costs_nothing(tmp_path: Path) -> None:
    transport = RecordedTransport(answer_for=lambda _id, _q: noul_answer(0.91))
    live = make_runtime(tmp_path, transport=transport)
    first = live.decide(
        "code-quality.rubric-smells",
        subject="cp1:app.py::main",
        state=STATE,
        questions=QUESTIONS,
        sufficiency=SUFFICIENT,
    )
    assert first.noul("swallows_errors") == pytest.approx(0.91)
    assert (first.record.cost_usd, first.record.cache_hit) == (pytest.approx(0.0021), False)

    for replay in (
        make_runtime(tmp_path, transport=transport),
        make_runtime(tmp_path, replay_only=True),
    ):
        again = replay.decide(
            "code-quality.rubric-smells",
            subject="cp1:app.py::main",
            state=STATE,
            questions=QUESTIONS,
            sufficiency=SUFFICIENT,
        )
        assert again.answers == first.answers
        assert again.record.cost_usd == 0.0
        assert again.record.cache_hit is True
        assert (replay.totals.provider_calls, replay.totals.cost_usd) == (0, 0.0)
    assert len(transport.calls) == 1
    cached = live.cache.get(first.record.cache_key or "")
    assert cached is not None
    assert cached.original_cost_usd == pytest.approx(0.0021)


def _tamper(field: str, value: object) -> Callable[[dict], str]:
    return lambda entry: json.dumps({**entry, field: value})


@pytest.mark.parametrize(
    "rewrite",
    [
        lambda _entry: "{garbage",
        _tamper("resolved_model", "typesafe/jev-0.1-evil"),
        _tamper("key", "b" * 64),
        _tamper("answers", {"swallows_errors": {"type": "choice", "choice": "x"}}),
        _tamper("answers", {"other_question": noul_answer(0.5)}),
    ],
)
def test_invalid_cache_entries_are_audited_failures_never_replays(
    tmp_path: Path, rewrite: Callable[[dict], str]
) -> None:
    transport = RecordedTransport(answer_for=lambda _id, _q: noul_answer(0.91))
    runtime = make_runtime(tmp_path, transport=transport)
    first = runtime.decide(
        "code-quality.rubric-smells",
        subject="s",
        state=STATE,
        questions=QUESTIONS,
        sufficiency=SUFFICIENT,
    )
    path = runtime.cache.path_for(first.record.cache_key or "")
    path.write_text(rewrite(json.loads(path.read_text())))

    again = runtime.decide(
        "code-quality.rubric-smells",
        subject="s",
        state=STATE,
        questions=QUESTIONS,
        sufficiency=SUFFICIENT,
    )
    assert again.answers is None
    assert again.record.failure_reason == FailureReason.CACHE_INVALID.value
    assert (again.record.cache_hit, again.record.counts_toward_verdicts) == (False, False)
    assert len(transport.calls) == 1
    assert len(runtime.audit.decisions("code-quality.rubric-smells")) == 2


def test_replay_only_miss_fails_as_cache_miss_without_calling(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, replay_only=True)
    outcome = runtime.decide(
        "code-quality.rubric-smells",
        subject="s",
        state=STATE,
        questions=QUESTIONS,
        sufficiency=SUFFICIENT,
    )
    assert not outcome.ok
    assert outcome.record.failure_reason == FailureReason.CACHE_MISS.value
    assert outcome.record.stage is DecisionStage.JEV_ERROR


@pytest.mark.parametrize(
    ("model", "audited_model"),
    [
        ("typesafe/jev-1.14-20261101", "typesafe/jev-1.14-20261101"),
        ("typesafe/jev latest; drop", INVALID_MODEL_ID),
    ],
)
def test_resolved_model_other_than_pin_is_rejected_and_never_cached(
    tmp_path: Path, model: str, audited_model: str
) -> None:
    transport = RecordedTransport(model=model)
    runtime = make_runtime(tmp_path, transport=transport)
    for _ in range(2):
        outcome = runtime.decide(
            "code-quality.rubric-smells",
            subject="s",
            state=STATE,
            questions=QUESTIONS,
            sufficiency=SUFFICIENT,
        )
        assert outcome.answers is None
        assert outcome.record.failure_reason == FailureReason.MODEL_MISMATCH.value
        assert outcome.record.resolved_model == audited_model
        assert outcome.record.counts_toward_verdicts is False
    assert len(transport.calls) == 2
    assert transport.calls[0]["body"]["model"] == JEV_MODEL


def test_cache_key_binds_state_questions_and_resolved_model() -> None:
    base = decision_key(STATE, QUESTIONS, JEV_PINNED_MODEL)
    assert base == decision_key(json.loads(json.dumps(STATE)), dict(QUESTIONS), JEV_PINNED_MODEL)
    other_questions = {"swallows_errors": NoulQuestion(instructions="Is the handler silent?")}
    assert (
        len(
            {
                base,
                decision_key({**STATE, "extra": 1}, QUESTIONS, JEV_PINNED_MODEL),
                decision_key(STATE, other_questions, JEV_PINNED_MODEL),
                decision_key(STATE, QUESTIONS, "typesafe/jev-1.14-20261101"),
            }
        )
        == 4
    )


def _decide_with(
    transport: RecordedTransport, questions: dict | None = None
) -> JevSuccess | JevFailure:
    return JevClient(TEST_KEY, transport=transport).decide(
        STATE, QUESTIONS if questions is None else questions
    )


@pytest.mark.parametrize(
    ("questions", "answer"),
    [
        (QUESTIONS, {"type": "choice", "choice": "a", "probabilities": {"a": 1}, "confidence": 1}),
        (
            {"pick": ChoiceQuestion(instructions="Pick", criteria={"a": None, "b": None})},
            {
                "type": "choice",
                "choice": "c",
                "probabilities": {"a": 0.5, "b": 0.5},
                "confidence": 0.5,
            },
        ),
        (
            {"pick": ChoiceQuestion(instructions="Pick", criteria={"a": None, "b": None})},
            {"type": "choice", "choice": "a", "probabilities": {"a": 0.9}, "confidence": 0.9},
        ),
        (
            {"level": ScoreQuestion(instructions="Rate", criteria=("low", "high"))},
            {"type": "score", "score": 2, "probabilities": {"0": 0.1}, "confidence": 0.9},
        ),
    ],
)
def test_answers_that_do_not_fit_their_question_are_rejected(questions: dict, answer: dict) -> None:
    result = _decide_with(RecordedTransport(answer_for=lambda _id, _q: answer), questions)
    assert isinstance(result, JevFailure)
    assert result.reason is FailureReason.INVALID_ANSWERS


def test_matching_choice_and_score_answers_are_accepted() -> None:
    questions = {
        "pick": ChoiceQuestion(instructions="Pick", criteria={"a": "first", "b": None}),
        "level": ScoreQuestion(instructions="Rate", criteria=("low", "mid", "high")),
    }
    answers = {
        "pick": {
            "type": "choice",
            "choice": "b",
            "probabilities": {"a": 0.2, "b": 0.8},
            "confidence": 0.8,
        },
        "level": {"type": "score", "score": 2, "probabilities": {"2": 0.7}, "confidence": 0.7},
    }
    result = _decide_with(RecordedTransport(answer_for=lambda qid, _q: answers[qid]), questions)
    assert isinstance(result, JevSuccess)
    assert set(result.answers) == {"pick", "level"}


def _raw_noul(value: object) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    return lambda _id, _q: {"type": "noul", "noul": value}


@pytest.mark.parametrize(
    ("transport", "reason", "status"),
    [
        (RecordedTransport(raw_body=b'{"id": "x"}'), FailureReason.INVALID_RESPONSE, None),
        (
            RecordedTransport(answer_for=lambda _id, _q: noul_answer(1.5)),
            FailureReason.INVALID_RESPONSE,
            None,
        ),
        *(
            (
                RecordedTransport(answer_for=_raw_noul(value)),
                FailureReason.INVALID_RESPONSE,
                None,
            )
            for value in ("0.4", True, None)
        ),
        (RecordedTransport(status=402), FailureReason.CREDITS, 402),
        (RecordedTransport(status=500), FailureReason.HTTP_ERROR, 500),
        (RecordedTransport(raises=TransportTimeoutError), FailureReason.TIMEOUT, None),
        (RecordedTransport(raises=TransportError), FailureReason.NETWORK, None),
    ],
)
def test_provider_and_transport_failures_are_typed_and_never_echo_the_body(
    transport: RecordedTransport, reason: FailureReason, status: int | None
) -> None:
    result = _decide_with(transport)
    assert isinstance(result, JevFailure)
    assert (result.reason, result.http_status) == (reason, status)
    assert "echo" not in result.error


@pytest.mark.parametrize(
    "questions",
    [
        {},
        {"Bad-Id": NoulQuestion(instructions="x")},
        {"ok\n": NoulQuestion(instructions="x")},
        {"blank": NoulQuestion(instructions="  ")},
        {"one": ChoiceQuestion(instructions="x", criteria={"only": None})},
        {"many": ScoreQuestion(instructions="x", criteria=tuple(str(i) for i in range(11)))},
    ],
)
def test_malformed_questions_raise_before_any_network_call(questions: dict) -> None:
    transport = RecordedTransport()
    with pytest.raises(ValueError, match="Jev"):
        _decide_with(transport, questions)
    assert transport.calls == []


def test_request_carries_endpoint_selector_timeout_and_state_cap() -> None:
    transport = RecordedTransport()
    assert isinstance(_decide_with(transport), JevSuccess)
    call = transport.calls[0]
    assert (call["url"], call["timeout_s"]) == (JEV_ENDPOINT, 15.0)
    assert call["headers"]["Authorization"] == f"Bearer {TEST_KEY}"
    oversized = JevClient(TEST_KEY, transport=transport).decide(
        {"blob": "x" * JEV_STATE_CHAR_LIMIT}, QUESTIONS
    )
    assert isinstance(oversized, JevFailure)
    assert oversized.reason is FailureReason.STATE_TOO_LARGE
    assert len(transport.calls) == 1


def test_secrets_are_redacted_from_request_audit_and_cache(tmp_path: Path) -> None:
    transport = RecordedTransport()
    runtime = make_runtime(tmp_path, transport=transport)
    leaky = {
        "source": f"KEY = '{TEST_KEY}'\nheaders = {{'Authorization': 'Bearer abc.def'}}\n"
        "token = ghp_abcdefghijklmnop\npassword: hunter2\n",
        "count": 3,
    }
    outcome = runtime.decide(
        "code-quality.rubric-smells",
        subject=f"uses {TEST_KEY}",
        state=leaky,
        questions=QUESTIONS,
        sufficiency=SUFFICIENT,
    )
    sent = json.dumps(transport.calls[0]["body"])
    stored = "".join(path.read_text() for path in tmp_path.rglob("*.json*"))
    for secret in (TEST_KEY, "ghp_abcdefghijklmnop", "hunter2", "abc.def"):
        assert secret not in sent
        assert secret not in stored
    assert transport.calls[0]["body"]["state"]["count"] == 3
    audit_file = runtime.audit.decisions_path(outcome.record)
    assert audit_file.stat().st_mode & 0o777 == 0o600


def test_redaction_and_clipping_rules() -> None:
    assert redact("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----") == (
        "[REDACTED PRIVATE KEY]"
    )
    assert redact("jwt eyJhbGc.eyJzdWI.sig") == "jwt [REDACTED]"
    assert redact("AKIAABCDEFGHIJKLMNOP") == "[REDACTED]"
    short = clip("abc", 10)
    assert (short.text, short.clipped) == ("abc", False)
    long = clip("h" * 900 + "t" * 100, 500)
    assert long.clipped
    assert long.text.startswith("h" * 400)
    assert long.text.endswith("t" * 100)
    assert "[500 characters omitted]" in long.text


@pytest.mark.parametrize(
    ("raised", "reason"),
    [
        (requests.Timeout, FailureReason.TIMEOUT),
        (requests.ConnectionError, FailureReason.NETWORK),
    ],
)
def test_requests_transport_maps_library_errors(
    monkeypatch: pytest.MonkeyPatch, raised: type[Exception], reason: FailureReason
) -> None:
    def post(*_args: object, **_kwargs: object) -> None:
        raise raised

    monkeypatch.setattr(requests, "post", post)
    result = JevClient(TEST_KEY).decide(STATE, QUESTIONS)
    assert isinstance(result, JevFailure)
    assert result.reason is reason


def test_api_key_comes_only_from_the_environment_variable() -> None:
    assert api_key_from_env({"OPENROUTER_API_KEY": "  "}) is None
    assert api_key_from_env({}) is None
    assert api_key_from_env({"OPENROUTER_API_KEY": TEST_KEY}) == TEST_KEY
    with pytest.raises(ValueError, match="API key"):
        JevClient("")


class _TrickleHandler(http.server.BaseHTTPRequestHandler):
    """Answers 200, then sends one byte every 50 ms for about two seconds."""

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Length", "40")
        self.end_headers()
        for _ in range(40):
            self.wfile.write(b" ")
            self.wfile.flush()
            time.sleep(0.05)

    def log_message(self, *_args: object) -> None:
        return


def test_requests_transport_timeout_bounds_the_whole_request_not_each_read() -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _TrickleHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/decisions"
        started = time.monotonic()
        with pytest.raises(TransportTimeoutError):
            RequestsTransport().post(url, headers={}, body=b"{}", timeout_s=0.4)
        assert time.monotonic() - started < 1.0
    finally:
        server.shutdown()
        server.server_close()
