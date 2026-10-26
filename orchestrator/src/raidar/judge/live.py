"""Live Jev decisions on the host, every attempt recorded raw (no cache, no replay).

``RecordedJev`` asks Jev through OpenRouter and writes, per attempt, under its directory:

- ``call-NNNN.request.json``: the request body exactly as sent (no headers, so no credential);
- ``call-NNNN.response.json``: the response body exactly as received, with the key replaced
  by ``[REDACTED]`` should the provider echo it (absent when the transport raised);
- ``call-NNNN.result.json``: the interpreted result, success or safe failure.

A failed attempt is recorded the same way as a good one; ``decide`` never raises for a provider
or key problem. Without ``OPENROUTER_API_KEY`` the attempt is a recorded ``missing_key`` failure
and nothing is sent. Retrying is the caller's job, one ``decide`` per attempt.

The request names the immutable ``JEV_PINNED_MODEL``, not Raidar's ``~typesafe/jev-latest``
alias nor FXL's ``typesafe/jev-1.13``: a dated id keeps a trial's judgement reproducible, while
the other two can move to a new Jev build between jobs. The response is validated against the
same id (``model_mismatch`` otherwise). This path is host-only: the key is read from the
host process environment and no sandbox ever receives it (``raidar.judge.secrets``).
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from raidar.judge.client import (
    Exchange,
    FailureReason,
    JevClient,
    JevFailure,
    JevResult,
    JevSuccess,
    Transport,
    api_key_from_env,
)
from raidar.judge.protocol import JEV_PINNED_MODEL, JevQuestions, answers_wire

LIVE_MODEL = JEV_PINNED_MODEL
DEFAULT_LIVE_TIMEOUT_S = 60.0
REDACTED = b"[REDACTED]"
CALLS_LOG = "calls.jsonl"


def result_record(result: JevResult) -> dict[str, Any]:
    """The interpreted result as a JSON record (answers in wire form, no credential)."""
    if isinstance(result, JevSuccess):
        return {
            "ok": True,
            "answers": answers_wire(result.answers),
            "resolved_model": result.resolved_model,
            "provider_response_id": result.provider_response_id,
            "cost_usd": result.cost_usd,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "latency_ms": result.latency_ms,
        }
    return {
        "ok": False,
        "reason": result.reason.value,
        "error": result.error,
        "http_status": result.http_status,
        "resolved_model": result.resolved_model,
        "latency_ms": result.latency_ms,
    }


@dataclass
class RecordedJev:
    """Pinned live Jev, host-side, recording every attempt under ``directory``.

    ``client`` (for tests, or a caller-owned transport) must name ``LIVE_MODEL``; otherwise a
    client is built from ``OPENROUTER_API_KEY`` in ``environ`` (the process environment by
    default) over ``transport``. ``calls`` lists one record per attempt, in order:
    ``index``, ``request_file`` / ``response_file`` (paths relative to ``directory``, None when
    nothing was sent / received), ``result_file``, ``result`` (``result_record``), ``seconds``,
    ``model`` (requested), ``resolved_model``, ``http_status``, ``transport_error``.
    """

    directory: Path
    timeout_s: float = DEFAULT_LIVE_TIMEOUT_S
    client: JevClient | None = None
    transport: Transport | None = None
    environ: Mapping[str, str] | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.directory = Path(self.directory)
        if self.client is None:
            key = api_key_from_env(self.environ)
            if key:
                self.client = JevClient(
                    key, transport=self.transport, timeout_s=self.timeout_s, model=LIVE_MODEL
                )
        elif self.client.model != LIVE_MODEL:
            raise ValueError(
                f"RecordedJev needs a client for {LIVE_MODEL}, not {self.client.model}"
            )

    def decide(self, state: Any, questions: JevQuestions) -> JevResult:
        """One attempt; recorded whatever its outcome."""
        started = time.monotonic()
        client = self.client
        if client is None:
            exchange = Exchange(
                JevFailure(
                    FailureReason.MISSING_KEY,
                    "OPENROUTER_API_KEY is not set on the host; no Jev request was sent.",
                    0,
                ),
                None,
                None,
            )
            key = None
        else:
            exchange = client.exchange(state, questions)
            key = client.api_key
        self._record(exchange, key, time.monotonic() - started)
        return exchange.result

    def _record(self, exchange: Exchange, key: str | None, seconds: float) -> None:
        """Number and write one attempt; the lock covers only this local bookkeeping."""
        with self._lock:
            self._write_record(exchange, key, seconds)

    def _write_record(self, exchange: Exchange, key: str | None, seconds: float) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        index = len(self.calls) + 1
        stem = f"call-{index:04d}"
        secret = key.encode("utf-8") if key else b""

        def write(suffix: str, data: bytes | None) -> str | None:
            if data is None:
                return None
            name = f"{stem}.{suffix}.json"
            clean = data.replace(secret, REDACTED) if secret else data
            (self.directory / name).write_bytes(clean)
            return name

        request_file = write("request", exchange.request_body)
        response_file = write("response", exchange.response.body if exchange.response else None)
        result = result_record(exchange.result)
        if secret:
            result = json.loads(
                json.dumps(result, ensure_ascii=False).replace(key or "", REDACTED.decode())
            )
        result_file = f"{stem}.result.json"
        (self.directory / result_file).write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        record: dict[str, Any] = {
            "index": index,
            "request_file": request_file,
            "response_file": response_file,
            "result_file": result_file,
            "result": result,
            "seconds": round(seconds, 3),
            "model": self.client.model if self.client else LIVE_MODEL,
            "resolved_model": result.get("resolved_model"),
            "http_status": result.get("http_status"),
            "transport_error": exchange.transport_error,
        }
        self.calls.append(record)
        with (self.directory / CALLS_LOG).open("a", encoding="utf-8") as log:
            log.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
