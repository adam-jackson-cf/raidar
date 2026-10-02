"""``correctness@1``: oracle verdicts per checkpoint and over the trajectory (plan G1).

Every value is read from the oracle's host-side step record
(``raidar-oracle/steps/<step>/record.json``), never from Harbor's trial-level
rewards, so each checkpoint observation equals what the oracle recorded:

- ``solved`` is the record's strict all-cases verdict;
- ``strict_pass_rate`` and ``<group>_pass_rate`` are passed / graded cases
  (skipped cases leave the denominator, as in SCB);
- ``regression_failures`` counts graded regression cases that did not pass.

A checkpoint without a verdict is statused, never zero: ``not_applicable``
when it was not reached, ``unavailable`` when the oracle did not grade it or
grading hit an infrastructure failure, and ``ineligible`` when the oracle
detected a leak (the verifier then withholds the verdict). A group rate with
no graded cases is ``not_applicable``.

Trajectory metrics follow the progression the trial ran under (scorer config
``progression``, ``continue`` by default):

- ``reached`` (strict view) counts consecutive solved checkpoints from the
  first. It is known once a checkpoint fails or every checkpoint is solved; a
  missing verdict before that makes it ``unavailable``.
- ``solved_checkpoints`` (continue view) needs a verdict for every planned
  checkpoint. Under ``halt_on_failure``, checkpoints after the failed one that
  stopped the trial were never attempted and count as unsolved.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import ValidationError

from raidar.scorers.builtin_manifests import CORRECTNESS
from raidar.scorers.sdk.context import AssessContext, StepInputs, statused_result
from raidar.scorers.sdk.manifest import ScorerManifest, SdkModel
from raidar.scorers.sdk.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
)
from raidar.verifier.models import CaseGroup, CaseStatus, VerifierGrade

LEAK_REWARD_KEY = "oracle_leak"
_GROUP_METRICS = {group: f"{group.value}_pass_rate" for group in CaseGroup}
_CHECKPOINT_METRICS = (
    "solved",
    "strict_pass_rate",
    *_GROUP_METRICS.values(),
    "regression_failures",
)


class CorrectnessConfig(SdkModel):
    """Scorer config: the progression the assessed trials ran under."""

    progression: Literal["continue", "halt_on_failure"] = "continue"


def verdict_gap(inputs: StepInputs) -> tuple[AssessStatus, str] | None:
    """Why a checkpoint has no oracle verdict, or None when it has one."""
    record = inputs.record
    if not inputs.reached:
        return AssessStatus.NOT_APPLICABLE, "checkpoint not reached"
    if record is None:
        return AssessStatus.UNAVAILABLE, "the oracle did not grade this step"
    if record.rewards.get(LEAK_REWARD_KEY) == 1.0:
        return AssessStatus.INELIGIBLE, "oracle leak detected; the verdict is withheld"
    if record.grade.infra_failure is not None:
        failure = record.grade.infra_failure
        return AssessStatus.UNAVAILABLE, f"oracle infrastructure failure: {failure.code}"
    return None


def _ok(
    metric: str, subject: str, value: float, ratio: tuple[int, int] | None = None
) -> Observation:
    numerator, denominator = ratio if ratio is not None else (None, None)
    return Observation(
        metric=metric,
        subject=subject,
        status=AssessStatus.OK,
        value=value,
        numerator=numerator,
        denominator=denominator,
    )


def _rate(metric: str, subject: str, passed: int, total: int, label: str) -> Observation:
    if total == 0:
        return Observation(
            metric=metric,
            subject=subject,
            status=AssessStatus.NOT_APPLICABLE,
            reason=f"no graded {label} cases",
        )
    return _ok(metric, subject, passed / total, (passed, total))


def _graded_observations(subject: str, grade: VerifierGrade) -> list[Observation]:
    counts = grade.group_counts()
    passed = sum(count.passed for count in counts.values())
    total = sum(count.total for count in counts.values())
    regression = counts[CaseGroup.REGRESSION]
    observations = [
        _ok("solved", subject, 1.0 if grade.solved else 0.0),
        _rate("strict_pass_rate", subject, passed, total, "oracle"),
        *(
            _rate(metric, subject, counts[group].passed, counts[group].total, group.value)
            for group, metric in _GROUP_METRICS.items()
        ),
    ]
    if regression.total == 0:
        observations.append(
            Observation(
                metric="regression_failures",
                subject=subject,
                status=AssessStatus.NOT_APPLICABLE,
                reason="no graded regression cases",
            )
        )
    else:
        failed = sum(
            1
            for case in grade.cases
            if case.group is CaseGroup.REGRESSION
            and case.status not in (CaseStatus.PASSED, CaseStatus.SKIPPED)
        )
        observations.append(_ok("regression_failures", subject, failed))
    return observations


def checkpoint_observations(inputs: StepInputs) -> list[Observation]:
    """Every checkpoint metric for one step, from its oracle record only."""
    gap = verdict_gap(inputs)
    if gap is None and inputs.record is not None:
        return _graded_observations(inputs.step, inputs.record.grade)
    status, reason = gap or (AssessStatus.UNAVAILABLE, "the oracle did not grade this step")
    return [
        Observation(metric=metric, subject=inputs.step, status=status, reason=reason)
        for metric in _CHECKPOINT_METRICS
    ]


def _verdicts(steps: Iterable[StepInputs]) -> list[bool | None]:
    """Per planned checkpoint: solved, failed, or None when there is no verdict."""
    return [
        None if verdict_gap(step) is not None or step.record is None else step.record.grade.solved
        for step in steps
    ]


def _trajectory(metric: str, value: int | None, reason: str) -> Observation:
    if value is None:
        return Observation(
            metric=metric,
            subject=TRAJECTORY_SUBJECT,
            status=AssessStatus.UNAVAILABLE,
            reason=reason,
        )
    return _ok(metric, TRAJECTORY_SUBJECT, value)


def reached_count(verdicts: list[bool | None]) -> int | None:
    """Consecutive solved checkpoints from the first; None when a gap hides the answer."""
    for index, verdict in enumerate(verdicts):
        if verdict is None:
            return None
        if not verdict:
            return index
    return len(verdicts)


def _halted_verdicts(steps: tuple[StepInputs, ...]) -> list[bool | None] | None:
    """Verdicts up to a ``halt_on_failure`` stop, or None if the stop was not a halt.

    A halt stops after a checkpoint that failed the gate; later ones were never tried.
    """
    verdicts = _verdicts(steps)
    reached = [step.reached for step in steps]
    if all(reached):
        return None
    stop = reached.index(False)
    return verdicts[:stop] if stop > 0 and verdicts[stop - 1] is False else None


def solved_count(steps: tuple[StepInputs, ...], progression: str) -> int | None:
    """Solved checkpoints over the plan; None when some checkpoint's verdict is unknown."""
    stopped = not all(step.reached for step in steps)
    counted = (
        _halted_verdicts(steps)
        if stopped and progression == "halt_on_failure"
        else _verdicts(steps)
    )
    if counted is None or None in counted:
        return None
    return sum(1 for verdict in counted if verdict)


def solved_gap(steps: tuple[StepInputs, ...], progression: str) -> str:
    """Why ``solved_checkpoints`` is unknown, naming a stop that looks like a halt.

    The scorer cannot see the gate the trial ran under; the experiment spec's
    ``protocol.progression`` must arrive as scorer config.
    """
    reason = "a planned checkpoint has no oracle verdict"
    if progression == "continue" and _halted_verdicts(steps) is not None:
        last = [step.step for step in steps if step.reached][-1]
        reason += (
            f"; the trial stopped after failed checkpoint {last}. If it ran under "
            "halt_on_failure, rescore with progression=halt_on_failure"
        )
    return reason


class CorrectnessScorer:
    """Deterministic scorer over oracle step records."""

    @property
    def manifest(self) -> ScorerManifest:
        return CORRECTNESS

    def assess(self, ctx: AssessContext) -> AssessResult:
        try:
            config = CorrectnessConfig.model_validate(dict(ctx.config))
        except ValidationError as exc:
            return statused_result(
                CORRECTNESS, ctx, AssessStatus.ERROR, f"invalid correctness config: {exc}"
            )
        observations = [
            observation for step in ctx.steps for observation in checkpoint_observations(step)
        ]
        observations.append(
            _trajectory(
                "solved_checkpoints",
                solved_count(ctx.steps, config.progression),
                solved_gap(ctx.steps, config.progression),
            )
        )
        observations.append(
            _trajectory(
                "reached",
                reached_count(_verdicts(ctx.steps)),
                "a checkpoint before the first failure has no oracle verdict",
            )
        )
        measured = any(item.status is AssessStatus.OK for item in observations)
        return AssessResult(
            status=AssessStatus.OK if measured else AssessStatus.UNAVAILABLE,
            reason=None if measured else "no checkpoint has an oracle verdict",
            observations=tuple(observations),
        )


def correctness_scorer() -> CorrectnessScorer:
    """Registry factory for ``correctness@1``."""
    return CorrectnessScorer()
