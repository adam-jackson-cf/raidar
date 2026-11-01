"""Ask Jev one question per aspect, host-side, recording every attempt.

The judge runs in the Raidar process, never in the sandbox: ``RecordedJev``
(``raidar.judge.live``) reads ``OPENROUTER_API_KEY`` from the host's environment and the
sandbox is never given it. Up to ``WORKERS`` requests run at once, each on its own
``RecordedJev`` (so each attempt's raw request and response are the files its own record names);
an attempt that fails for a transient reason (a timeout, a network error, a malformed answer, HTTP
408, 429 or 5xx) is asked again after a backoff, at most ``RETRIES`` times, then the aspect has no
answer and is undecided (design D3: a judge never ends a run). Each attempt runs inside a
``request.judge`` host window of the primitive's ``TIMEOUT_S``, so liveness does not read the
wait as the agent being idle.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from harbor.environments.base import BaseEnvironment

from raidar.contract.activity import host_window
from raidar.judge.client import FailureReason, JevFailure, JevSuccess
from raidar.judge.live import LIVE_MODEL, RecordedJev
from raidar.judge.protocol import NoulAnswer
from raidar.subject.primitive.judge import RETRIES, TIMEOUT_S, JudgeConfig
from raidar.subject.workflow.judging import ASPECT_ASSERTED, Request, questions

WORKERS = 8
BACKOFF_S = 1.0
RETRYABLE = frozenset(
    {
        FailureReason.TIMEOUT,
        FailureReason.NETWORK,
        FailureReason.INVALID_RESPONSE,
        FailureReason.INVALID_ANSWERS,
    }
)
TRANSIENT_STATUS = frozenset({408, 429})
SERVER_ERROR = 500
JUDGE_DIR = "workflow/judge"
HOST_WINDOW = "request.judge"

Answers = dict[str, tuple[float | None, tuple[int, ...]]]


def new_jev(directory: Path, timeout_s: float) -> RecordedJev:
    """One live Jev recorder under ``directory`` (tests replace this with a fake transport)."""
    return RecordedJev(directory, timeout_s=timeout_s)


def retryable(failure: JevFailure) -> bool:
    """Whether another attempt may succeed where this one failed."""
    if failure.reason in RETRYABLE:
        return True
    status = failure.http_status
    return (
        failure.reason is FailureReason.HTTP_ERROR
        and status is not None
        and (status in TRANSIENT_STATUS or status >= SERVER_ERROR)
    )


class Asker:
    """Asks the aspects of every pass of a checkpoint and keeps every attempt's record."""

    def __init__(
        self,
        settings: JudgeConfig,
        logs_dir: Path,
        environment: BaseEnvironment,
        workers: int = WORKERS,
    ) -> None:
        if settings.model != LIVE_MODEL:
            raise ValueError(f"judge.model {settings.model!r} is not the pinned {LIVE_MODEL!r}")
        self.environment = environment
        self.calls: list[dict[str, Any]] = []
        self._free: asyncio.Queue[tuple[str, RecordedJev]] = asyncio.Queue()
        for number in range(1, workers + 1):
            relative = f"{JUDGE_DIR}/w{number}"
            self._free.put_nowait((relative, new_jev(logs_dir / relative, TIMEOUT_S)))

    async def ask(self, number: int, asked: Sequence[tuple[str, Request]]) -> Answers:
        """Ask every ``(criterion id, request)``; each aspect's probability and its call indices."""
        found = await asyncio.gather(*(self._aspect(number, ident, item) for ident, item in asked))
        return {item.aspect: answer for (_, item), answer in zip(asked, found, strict=True)}

    async def _aspect(
        self, number: int, criterion: str, request: Request
    ) -> tuple[float | None, tuple[int, ...]]:
        indices: list[int] = []
        probability: float | None = None
        for attempt in range(1, RETRIES + 2):
            probability, failure, record = await self._attempt(request)
            indices.append(len(self.calls))
            self.calls.append(
                {
                    "index": len(self.calls),
                    "pass": number,
                    "criterion": criterion,
                    "aspect": request.aspect,
                    "attempt": attempt,
                    "probability": probability,
                    **record,
                }
            )
            if failure is None or not retryable(failure) or attempt > RETRIES:
                break
            await asyncio.sleep(BACKOFF_S * 2 ** (attempt - 1))
        return probability, tuple(indices)

    async def _attempt(
        self, request: Request
    ) -> tuple[float | None, JevFailure | None, dict[str, Any]]:
        relative, jev = await self._free.get()
        try:
            with host_window(self.environment, HOST_WINDOW, TIMEOUT_S):
                result = await asyncio.to_thread(jev.decide, request.state, questions())
            kept = jev.calls[-1]
        finally:
            self._free.put_nowait((relative, jev))
        record = {
            **{key: kept[key] for key in ("result", "seconds", "model", "resolved_model")},
            "http_status": kept["http_status"],
            "transport_error": kept["transport_error"],
            **{
                key: f"{relative}/{kept[key]}" if kept[key] else None
                for key in ("request_file", "response_file", "result_file")
            },
        }
        if isinstance(result, JevSuccess):
            answer = result.answers.get(ASPECT_ASSERTED)
            return (answer.noul if isinstance(answer, NoulAnswer) else None), None, record
        return None, result, record
