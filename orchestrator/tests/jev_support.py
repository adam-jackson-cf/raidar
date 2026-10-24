"""Recorded-response transport and builders for Jev tests (no network)."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from raidar.scoring.judge.audit import AuditLog, InputSufficiency, SufficiencyLevel
from raidar.scoring.judge.cache import DecisionCache
from raidar.scoring.judge.client import (
    JevClient,
    TransportError,
    TransportResponse,
    TransportTimeoutError,
)
from raidar.scoring.judge.policies import (
    POLICY_DIR,
    CalibrationSplit,
    JevPolicy,
    PolicyMode,
    PolicyRegistry,
    Promotion,
)
from raidar.scoring.judge.protocol import JEV_PINNED_MODEL
from raidar.scoring.judge.runtime import JevRuntime

TEST_KEY = "sk-or-v1-testkey-0123456789abcdef"
PARKED_MODE = 'mode: "off"'


def shadow_policy_dir(target: Path) -> Path:
    """The repo policies with every parked (``off``) mode back at ``shadow``.

    Jev is parked (2026-10-04): the repo policies are ``off``. The dormant
    calibration and promotion workflow starts from shadow, so its tests run on
    this copy; comments and every other field stay byte for byte.
    """
    target.mkdir(parents=True)
    for source in sorted(POLICY_DIR.glob("*.yaml")):
        text = source.read_text(encoding="utf-8")
        assert PARKED_MODE in text, f"{source.name} is no longer parked"
        (target / source.name).write_text(text.replace(PARKED_MODE, "mode: shadow"))
    return target


SUFFICIENT = InputSufficiency(level=SufficiencyLevel.SUFFICIENT)
HEX = "a" * 64


def noul_answer(probability: float) -> dict[str, Any]:
    return {"type": "noul", "noul": probability}


def decision_body(
    answers: Mapping[str, Any], *, model: str = JEV_PINNED_MODEL, cost: float = 0.0021
) -> bytes:
    return json.dumps(
        {
            "id": "gen-recorded-1",
            "model": model,
            "provider": "TypeSafe",
            "answers": dict(answers),
            "usage": {"input_tokens": 812, "output_tokens": 3, "cost": cost},
        }
    ).encode()


@dataclass
class RecordedTransport:
    """Answers every question of a request from ``answer_for``; records each call."""

    answer_for: Callable[[str, dict[str, Any]], dict[str, Any]] = lambda _id, _q: noul_answer(0.2)
    model: str = JEV_PINNED_MODEL
    cost: float = 0.0021
    status: int = 200
    raw_body: bytes | None = None
    raises: type[Exception] | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def post(
        self, url: str, *, headers: Mapping[str, str], body: bytes, timeout_s: float
    ) -> TransportResponse:
        request = json.loads(body)
        self.calls.append(
            {"url": url, "headers": dict(headers), "body": request, "timeout_s": timeout_s}
        )
        if self.raises is not None:
            raise self.raises
        if self.raw_body is not None or self.status != 200:
            return TransportResponse(self.status, self.raw_body or b'{"error":"echo"}')
        answers = {
            question_id: self.answer_for(question_id, question)
            for question_id, question in request["questions"].items()
        }
        return TransportResponse(200, decision_body(answers, model=self.model, cost=self.cost))


def policy(
    policy_id: str = "code-quality.rubric-smells",
    mode: PolicyMode = PolicyMode.SHADOW,
    *,
    threshold: float = 0.7,
) -> JevPolicy:
    promotion = None
    if mode is PolicyMode.ENFORCE:
        promotion = Promotion(
            threshold=threshold,
            labelled=40,
            minority=8,
            input_sufficiency=0.9,
            runtimes=("python",),
            split=CalibrationSplit(calibration=("trial-a",), evaluation=("trial-b",)),
            regression_corpus=HEX,
        )
    return JevPolicy(
        id=policy_id,
        version="1",
        description="test policy",
        mode=mode,
        question_set="test@1",
        runtimes=("python",),
        promotion=promotion,
    )


def make_runtime(
    root: Path,
    *,
    transport: RecordedTransport | None = None,
    policies: tuple[JevPolicy, ...] = (),
    replay_only: bool = False,
    trial: str | None = None,
) -> JevRuntime:
    client = None
    if transport is not None:
        client = JevClient(TEST_KEY, transport=transport, timeout_s=7.5)
    return JevRuntime(
        PolicyRegistry(policies or (policy(),)),
        audit=AuditLog.for_store(root),
        cache=DecisionCache(root / "jev-cache"),
        client=client,
        replay_only=replay_only,
        trial=trial,
    )


__all__ = [
    "SUFFICIENT",
    "TEST_KEY",
    "RecordedTransport",
    "TransportError",
    "TransportTimeoutError",
    "decision_body",
    "make_runtime",
    "noul_answer",
    "policy",
]
