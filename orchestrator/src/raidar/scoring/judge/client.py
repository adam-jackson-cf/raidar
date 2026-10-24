"""One Jev decision over HTTP, ported from ``jev.ts`` ``decide``.

``JevClient.decide`` never raises for transport, provider or schema failures:
it returns a ``JevFailure`` with a typed reason and a safe message, never the
provider body (bodies can echo the input). Question blocks are validated before
any network call and malformed ones raise, because they are programmer errors.

The key is read from ``OPENROUTER_API_KEY`` only. Raidar does not reach into a
personal OMP install for it (design memo §3.5).
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import requests
from pydantic import ValidationError

from raidar.scoring.judge.protocol import (
    DEFAULT_TIMEOUT_S,
    JEV_ENDPOINT,
    JEV_MODEL,
    JEV_PINNED_MODEL,
    JEV_STATE_CHAR_LIMIT,
    DecisionResponse,
    JevAnswers,
    JevQuestions,
    answer_matches,
    questions_wire,
    safe_model_id,
    validate_questions,
)

API_KEY_ENV = "OPENROUTER_API_KEY"
_HTTP_PAYMENT_REQUIRED = 402


class FailureReason(StrEnum):
    """Why no usable decision exists; mirrors the OMP watchdog failure vocabulary."""

    MISSING_KEY = "missing_key"
    STATE_TOO_LARGE = "state_too_large"
    NETWORK = "network"
    TIMEOUT = "timeout"
    HTTP_ERROR = "http_error"
    CREDITS = "credits"
    INVALID_RESPONSE = "invalid_response"
    MODEL_MISMATCH = "model_mismatch"
    INVALID_ANSWERS = "invalid_answers"
    CACHE_MISS = "cache_miss"
    CACHE_INVALID = "cache_invalid"


@dataclass(frozen=True)
class JevSuccess:
    answers: JevAnswers
    resolved_model: str
    provider_response_id: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    latency_ms: int


@dataclass(frozen=True)
class JevFailure:
    reason: FailureReason
    error: str
    latency_ms: int
    http_status: int | None = None
    resolved_model: str | None = None


JevResult = JevSuccess | JevFailure


class TransportTimeoutError(Exception):
    """The request timed out or was cancelled."""


class TransportError(Exception):
    """The request did not reach the provider."""


@dataclass(frozen=True)
class TransportResponse:
    status: int
    body: bytes


class Transport(Protocol):
    """Sends one POST. Raises ``TransportTimeoutError`` or ``TransportError``."""

    def post(
        self, url: str, *, headers: Mapping[str, str], body: bytes, timeout_s: float
    ) -> TransportResponse: ...


class RequestsTransport:
    """POST with a total deadline, like ``AbortSignal.timeout`` in ``jev.ts``.

    ``requests`` timeouts bound the connect and each socket read, so a response
    that trickles in can run far past them. The request runs on a daemon worker
    that streams the body and stops at the deadline; the caller waits at most
    ``timeout_s`` in total and then raises ``TransportTimeoutError``.
    """

    _CHUNK = 16_384

    def post(
        self, url: str, *, headers: Mapping[str, str], body: bytes, timeout_s: float
    ) -> TransportResponse:
        deadline = time.monotonic() + timeout_s
        outcome: queue.Queue[TransportResponse | Exception] = queue.Queue(maxsize=1)

        def work() -> None:
            try:
                outcome.put(self._send(url, headers, body, timeout_s, deadline))
            except Exception as error:  # handed to the caller, which re-raises it
                outcome.put(error)

        threading.Thread(target=work, name="jev-request", daemon=True).start()
        try:
            result = outcome.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            raise TransportTimeoutError from None
        if isinstance(result, Exception):
            raise result
        return result

    def _send(
        self,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_s: float,
        deadline: float,
    ) -> TransportResponse:
        try:
            with requests.post(
                url, headers=dict(headers), data=body, timeout=timeout_s, stream=True
            ) as response:
                chunks = []
                for chunk in response.iter_content(self._CHUNK):
                    if time.monotonic() > deadline:
                        raise TransportTimeoutError
                    chunks.append(chunk)
                return TransportResponse(response.status_code, b"".join(chunks))
        except requests.Timeout as error:
            raise TransportTimeoutError from error
        except requests.RequestException as error:
            raise TransportError from error


def state_text(state: Any) -> str:
    """The state exactly as serialised into the request body."""
    return json.dumps(state, ensure_ascii=False, allow_nan=False, sort_keys=True)


def api_key_from_env(environ: Mapping[str, str] | None = None) -> str | None:
    value = (environ if environ is not None else os.environ).get(API_KEY_ENV, "").strip()
    return value or None


class JevClient:
    """Pinned-model Jev client. The key never leaves the Authorization header."""

    def __init__(
        self,
        api_key: str,
        *,
        transport: Transport | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if not api_key:
            raise ValueError("JevClient needs an API key")
        self._api_key = api_key
        self._transport = transport or RequestsTransport()
        self._timeout_s = timeout_s

    @property
    def api_key(self) -> str:
        return self._api_key

    def decide(self, state: Any, questions: JevQuestions) -> JevResult:
        validate_questions(questions)
        started = time.monotonic()
        serialised = state_text(state)
        if len(serialised) > JEV_STATE_CHAR_LIMIT:
            return JevFailure(
                FailureReason.STATE_TOO_LARGE,
                f"Jev state is {len(serialised)} characters, over {JEV_STATE_CHAR_LIMIT}.",
                0,
            )
        body = json.dumps(
            {"model": JEV_MODEL, "state": state, "questions": questions_wire(questions)},
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        ).encode("utf-8")
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        try:
            response = self._transport.post(
                JEV_ENDPOINT, headers=headers, body=body, timeout_s=self._timeout_s
            )
        except TransportTimeoutError:
            return _failure(FailureReason.TIMEOUT, "Jev request timed out.", started)
        except TransportError:
            return _failure(
                FailureReason.NETWORK, "Jev request failed to reach OpenRouter.", started
            )
        return _interpret(response, questions, started)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _failure(
    reason: FailureReason,
    error: str,
    started: float,
    *,
    http_status: int | None = None,
    resolved_model: str | None = None,
) -> JevFailure:
    return JevFailure(reason, error, _elapsed_ms(started), http_status, resolved_model)


def _interpret(response: TransportResponse, questions: JevQuestions, started: float) -> JevResult:
    if not 200 <= response.status < 300:
        if response.status == _HTTP_PAYMENT_REQUIRED:
            return _failure(
                FailureReason.CREDITS,
                "OpenRouter is out of credits or at its spending limit (HTTP 402).",
                started,
                http_status=response.status,
            )
        return _failure(
            FailureReason.HTTP_ERROR,
            f"Jev Decisions API returned HTTP {response.status}.",
            started,
            http_status=response.status,
        )
    try:
        parsed = DecisionResponse.model_validate_json(response.body)
    except ValidationError:
        return _failure(
            FailureReason.INVALID_RESPONSE, "Jev returned an invalid decision response.", started
        )
    resolved = safe_model_id(parsed.model)
    if resolved != JEV_PINNED_MODEL:
        return _failure(
            FailureReason.MODEL_MISMATCH,
            "Jev resolved to an unexpected model version; re-evaluate before trusting it.",
            started,
            resolved_model=resolved,
        )
    for question_id, question in questions.items():
        if not answer_matches(question, parsed.answers.get(question_id)):
            return _failure(
                FailureReason.INVALID_ANSWERS,
                f'Jev answer for "{question_id}" is missing or does not match the question.',
                started,
                resolved_model=resolved,
            )
    answers = {question_id: parsed.answers[question_id] for question_id in questions}
    return JevSuccess(
        answers=answers,
        resolved_model=resolved,
        provider_response_id=parsed.id,
        cost_usd=parsed.usage.cost,
        input_tokens=int(parsed.usage.input_tokens),
        output_tokens=int(parsed.usage.output_tokens),
        latency_ms=_elapsed_ms(started),
    )
