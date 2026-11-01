"""Execution policy owned by ``jev-judge/1``, not authored workflow settings.

The workflow supplies only what ``PackageJudge`` declares (model, thresholds, pass limit and the
rework prompt); everything else here is the primitive's constant, as the spike's
``define-build-verify@4`` declared it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from raidar_contract.workflow import PackageJudge

PRIMITIVE = "jev-judge/1"
QUESTION_SET = "dbv5-aspect/1"
RETRIES = 3
TIMEOUT_S = 60.0
CONTEXT_TOKENS = 32_000
STALL_REPEATS = 2
FIT_REWORK = True
REGRESSION_NEEDS_BASELINE = True


@dataclass(frozen=True)
class JudgeConfig:
    """What one checkpoint's judge runs on: the workflow's inputs and the primitive's policy."""

    model: str
    closed_at: float
    open_at: float
    question_set: str = QUESTION_SET
    regression_needs_baseline: bool = REGRESSION_NEEDS_BASELINE
    retries: int = RETRIES
    timeout_s: float = TIMEOUT_S
    context_tokens: int = CONTEXT_TOKENS
    stall_repeats: int = STALL_REPEATS
    fit_rework: bool = FIT_REWORK

    def record(self) -> dict[str, Any]:
        return asdict(self)


def config_of(judge: PackageJudge) -> JudgeConfig:
    """The judge's configuration for a workflow's ``judge`` block."""
    return JudgeConfig(judge.model, judge.closed_at, judge.open_at)
