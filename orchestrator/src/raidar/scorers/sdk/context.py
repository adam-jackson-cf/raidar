"""What a scorer sees (``AssessContext``) and the scorer interface itself.

Scorers observe immutable per-step snapshots, the oracle's host-side step
records and the agent trace. They never decide success and never compare arms
(F6). ``labels_blinded`` means the structured inputs carry no arm or model
label: ``agent_result`` is reduced to usage totals and anonymous per-model
usage. Raw trace files are passed as stored and can still name a model, so a
trace-reading scorer must not branch on such names.

``steps`` lists every checkpoint the problem plans, in order. A checkpoint the
trial never reached (a ``halt_on_failure`` stop, or an errored trial) is
present with ``reached=False`` and no inputs, so every trajectory has the same
subjects and a scorer reports such checkpoints with an explicit status.
Correctness comes from ``raidar-oracle/steps/*/record.json``; Harbor's
trial-level aggregated rewards are not offered, because they count missing
keys as zero.

``problem_config`` carries the problem's own facts that scorers take as config
defaults (for example ``entry_file``, the snapshot-relative entrypoint). It
comes from the oracle bundle the trial was graded with, so the Assessment key
already covers it through the problem reference and oracle record digests.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from raidar.scorers.inventory import Inventory, InventoryPolicy, build_inventory
from raidar.scorers.sdk.manifest import MetricLevel, ScorerManifest
from raidar.scorers.sdk.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
    transition_subject,
)
from raidar.verifier.records import StepRecord


@dataclass(frozen=True)
class StepInputs:
    """One checkpoint of a trajectory, in execution order.

    ``reached`` is False when the trial stopped before this checkpoint; all
    inputs are then None. ``record`` is None when the oracle did not grade the
    step (not reached, or the trial failed before verification).
    ``agent_result`` is the step's blinded Harbor usage, never its reward.
    ``agent_execution_s`` is the duration of Harbor's ``agent_execution``
    phase for the step, or None when Harbor did not record both ends of it.
    """

    step: str
    reached: bool
    checkpoint: str
    snapshot: Path | None
    snapshot_digest: str | None
    record: StepRecord | None
    record_digest: str | None
    trace_dir: Path | None
    trace_digest: str | None
    agent_result: Mapping[str, Any] | None
    agent_execution_s: float | None = None


@dataclass
class AssessContext:
    """Read-only inputs for one trajectory (one problem step_result)."""

    problem: str
    steps: tuple[StepInputs, ...]
    config: Mapping[str, Any] = field(default_factory=dict)
    problem_config: Mapping[str, Any] = field(default_factory=dict)
    starter: Path | None = None
    inventory_policy: InventoryPolicy = field(default_factory=InventoryPolicy)
    labels_blinded: bool = True
    _inventories: dict[str, Inventory] = field(default_factory=dict, repr=False)

    def step(self, name: str) -> StepInputs:
        for inputs in self.steps:
            if inputs.step == name:
                return inputs
        raise KeyError(f"unknown step {name!r}")

    def prior(self, name: str) -> StepInputs | None:
        names = [inputs.step for inputs in self.steps]
        index = names.index(name)
        return self.steps[index - 1] if index > 0 else None

    def inventory(self, name: str) -> Inventory | None:
        """Assessor-owned inventory of a step's snapshot (None when there is no snapshot)."""
        inputs = self.step(name)
        if inputs.snapshot is None:
            return None
        if name not in self._inventories:
            self._inventories[name] = build_inventory(inputs.snapshot, self.inventory_policy)
        return self._inventories[name]

    def subjects(self, level: MetricLevel) -> tuple[str, ...]:
        """Every subject a metric of ``level`` must report on, in order."""
        names = [inputs.step for inputs in self.steps]
        if level is MetricLevel.CHECKPOINT:
            return tuple(names)
        if level is MetricLevel.TRANSITION:
            return tuple(transition_subject(a, b) for a, b in zip(names, names[1:], strict=False))
        return (TRAJECTORY_SUBJECT,)


@runtime_checkable
class Scorer(Protocol):
    """An observation-only plugin. ``assess`` must be deterministic for tier=deterministic."""

    @property
    def manifest(self) -> ScorerManifest: ...

    def assess(self, ctx: AssessContext) -> AssessResult: ...


@runtime_checkable
class RawPayloadScorer(Scorer, Protocol):
    """A scorer that also hands back raw payloads (unparsed tool output) for audit.

    The store keeps each payload content-addressed and the Assessment names it
    by digest, so replays must reproduce the raw bytes as well as the result.
    """

    def assess_with_raw(self, ctx: AssessContext) -> tuple[AssessResult, Mapping[str, bytes]]: ...


@runtime_checkable
class AwaitingAnswersScorer(Scorer, Protocol):
    """A scorer whose last ``assess`` may have left external answers outstanding.

    ``awaiting_answers()`` is True when some decision had no answer yet for a
    reason a later run can remove (for example Jev with no API key or no cached
    answer). The store keeps that Assessment as provisional: a later rescore
    runs it again, and a complete result supersedes it under the same key.
    """

    def awaiting_answers(self) -> bool: ...


def statused_result(
    manifest: ScorerManifest, ctx: AssessContext, status: AssessStatus, reason: str
) -> AssessResult:
    """A complete result in which every declared observation carries ``status``.

    Use it when a scorer cannot measure at all (for example an unsupported
    runtime): every metric is still present and explicitly statused, never zero.
    """
    if status is AssessStatus.OK:
        raise ValueError("statused_result is for non-ok outcomes")
    observations = tuple(
        Observation(metric=metric.id, subject=subject, status=status, reason=reason)
        for metric in manifest.metrics
        for subject in ctx.subjects(metric.level)
    )
    return AssessResult(status=status, reason=reason, observations=observations)
