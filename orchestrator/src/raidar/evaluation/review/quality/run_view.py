"""One trial's Quality view: the C·V·E·H·R·G·I·S strip, drill-down evidence, Jev decisions
and, for a trial that ran behind a commit gate, its gate effort (``gate_view``).

Checkpoint cells come straight from the stored observations:

- C is ``correctness@1`` ``strict_pass_rate`` at the checkpoint;
- V, E, H are ``code-quality@3`` at the checkpoint;
- R, G are ``code-quality@3`` on the transition *into* the checkpoint, so
  the first checkpoint has none (``not_applicable``).

The checkpoint-level I and S are a review view, not a headline: they apply
the headline metric's weights and formula to one checkpoint's components.

The problem-level row is the headline, and it is the analysis' trial outcome
(``raidar.evaluation.analysis.assessed.build_outcome``), so the Quality element, the
analysis report and the review projection agree for one trial: C is
the mean of the per-checkpoint case pass rates (ADR-001 §2.4), V..G are the ``problem_*`` means,
and S is the headline metric applied to them. A trial truncated by
``max_checkpoints`` has no problem-level outcome (every cell is
``truncated``). Only I is derived here, from the same policy score. Nothing
unmeasured becomes a zero: every missing value keeps its reason.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from raidar.evaluation.analysis.assessed import build_outcome
from raidar.evaluation.analysis.policy_inputs import ComponentInput, score_inputs
from raidar.evaluation.review.quality.gate_view import build_gate_view
from raidar.evaluation.review.quality.models import (
    COMPONENTS,
    Cell,
    CellStatus,
    CheckpointRow,
    CheckpointSignals,
    EvidenceItem,
    ExclusionItem,
    JevDecisionItem,
    PolicyInfo,
    QualityRunDetail,
    QualityRunSummary,
    cell_status,
)
from raidar.evaluation.review.quality.sources import TrialSource
from raidar.scoring.catalog.outcomes import (
    CODE_QUALITY_REF,
    CORRECTNESS_METRIC,
    CORRECTNESS_REF,
    HEADLINE_SCORE,
    QUALITY_COMPONENTS,
)
from raidar.scoring.framework.metrics import (
    COMPONENT_PLACES,
    DEFAULT_METRIC_REGISTRY,
    IneligibilityReason,
    Metric,
    MetricScore,
)
from raidar.scoring.framework.result import TRANSITION_SEPARATOR, Observation
from raidar.scoring.framework.store import Assessment
from raidar.scoring.judge.audit import DecisionRecord, LabelRecord

CHECKPOINT_METRICS = {"V": "verbosity", "E": "erosion", "H": "architecture"}
TRANSITION_METRICS = {"R": "rework", "G": "regression"}
EVIDENCE_COMPONENT = {
    "verbosity": "V",
    "erosion": "E",
    "architecture": "H",
    "rework": "R",
    "regression": "G",
}
HIGH_COMPLEXITY_KIND = "high_complexity"
_NOT_REACHED: CellStatus = "not_applicable"
_TRUNCATED: CellStatus = "truncated"


def resolve_policy(from_lock: str | None, checkpoints: int) -> tuple[Metric, PolicyInfo]:
    """The lock's headline metric, or (no matching lock) the default for ``checkpoints``."""
    policy = (
        DEFAULT_METRIC_REGISTRY.get(from_lock)
        if from_lock
        else DEFAULT_METRIC_REGISTRY.default_for_checkpoints(checkpoints)
    )
    info = PolicyInfo(
        ref=policy.ref,
        source="lock" if from_lock else "default_by_k",
        inertia_weights={key: float(value) for key, value in policy.inertia_weights.items()},
        not_applicable=tuple(sorted(policy.not_applicable)),
    )
    return policy, info


def _cell(observation: Observation | None, missing: str) -> Cell:
    if observation is None:
        return Cell(status="unavailable", reason=missing)
    if observation.status.value == "ok":
        return Cell(status="ok", value=observation.value)
    return Cell(
        status=cell_status(observation.status.value), reason=observation.reason or "no reason given"
    )


class _Observations:
    def __init__(self, assessment: Assessment | None, missing: str) -> None:
        self.missing = missing
        self.present = assessment is not None
        self._items: dict[tuple[str, str], Observation] = {}
        if assessment is not None:
            for item in assessment.result.observations:
                self._items[(item.metric, item.subject)] = item

    def cell(self, metric: str, subject: str) -> Cell:
        return _cell(self._items.get((metric, subject)), self.missing)

    def value(self, metric: str, subject: str) -> float | None:
        item = self._items.get((metric, subject))
        return item.value if item is not None else None


def _inertia(policy: Metric, cells: Mapping[str, Cell]) -> Cell:
    weights = policy.inertia_weights
    missing = {key: cells[key] for key in weights if cells[key].status != "ok"}
    if missing:
        if all(cell.status == _NOT_REACHED for cell in missing.values()):
            first = next(iter(missing.values()))
            return Cell(status=_NOT_REACHED, reason=first.reason)
        reason = "; ".join(f"{key}: {cell.reason}" for key, cell in missing.items())
        return Cell(status="ineligible", reason=reason)
    inertia = sum(
        (weight * Decimal(str(cells[key].value)) for key, weight in weights.items()), Decimal(0)
    )
    return Cell(status="ok", value=float(inertia.quantize(COMPONENT_PLACES)))


def _score(policy: Metric, correctness: Cell, inertia: Cell) -> Cell:
    """Per-checkpoint S; under ``zero_correctness`` a measured C = 0 is S = 0 (``@2``)."""
    if policy.zero_correctness and correctness.status == "ok" and correctness.value == 0:
        return Cell(status="ok", value=0.0)
    for key, cell in (("C", correctness), ("I", inertia)):
        if cell.status != "ok":
            status: CellStatus = "not_applicable" if cell.status == _NOT_REACHED else "ineligible"
            return Cell(status=status, reason=f"{key}: {cell.reason}")
    assert correctness.value is not None
    assert inertia.value is not None
    value = (
        100
        * Decimal(str(correctness.value))
        * (Decimal("0.70") + Decimal("0.30") * Decimal(str(inertia.value)))
    )
    return Cell(status="ok", value=float(value.quantize(Decimal("0.000001"))))


@dataclass(frozen=True)
class _Inputs:
    trial: TrialSource
    checkpoints: tuple[str, ...]
    quality: Assessment | None
    correctness: Assessment | None


def _checkpoints(trial: TrialSource, *assessments: Assessment | None) -> tuple[str, ...]:
    for assessment in assessments:
        if assessment is not None:
            steps = assessment.inputs.get("steps") or []
            return tuple(str(step["step"]) for step in steps)
    return trial.record.trial.steps


def _row_cells(
    policy: Metric,
    checkpoint: str,
    previous: str | None,
    quality: _Observations,
    correctness: _Observations,
) -> dict[str, Cell]:
    cells = {"C": correctness.cell("strict_pass_rate", checkpoint)}
    for key, metric in CHECKPOINT_METRICS.items():
        cells[key] = quality.cell(metric, checkpoint)
    for key, metric in TRANSITION_METRICS.items():
        if previous is None:
            cells[key] = Cell(
                status="not_applicable", reason="no transition into the first checkpoint"
            )
        else:
            cells[key] = quality.cell(metric, f"{previous}{TRANSITION_SEPARATOR}{checkpoint}")
    for key in policy.not_applicable:
        cells[key] = Cell(status="not_applicable", reason=f"{policy.ref} does not use {key}")
    cells["I"] = _inertia(policy, cells)
    cells["S"] = _score(policy, cells["C"], cells["I"])
    return {key: cells[key] for key in COMPONENTS}


def _as_int(value: float | None) -> int | None:
    return int(value) if value is not None else None


def _density(findings: float | None, loc: float | None) -> float | None:
    """Findings per 1000 production lines (``None`` when either is unmeasured or LOC is 0)."""
    if findings is None or not loc:
        return None
    return 1000 * findings / loc


def _signals(
    checkpoint: str, quality: _Observations, evidence: Sequence[EvidenceItem]
) -> CheckpointSignals:
    lint = quality.value("lint_findings", checkpoint)
    loc = quality.value("production_loc", checkpoint)
    erosion = quality.value("erosion", checkpoint)
    high = [
        item
        for item in evidence
        if item.kind == HIGH_COMPLEXITY_KIND and item.subject == checkpoint
    ]
    measured = erosion is not None
    return CheckpointSignals(
        lint_findings=_as_int(lint),
        production_loc=_as_int(loc),
        lint_per_kloc=_density(lint, loc),
        high_cc_count=len(high) if measured else None,
        high_cc_mass_share=1 - erosion if erosion is not None else None,
        high_cc_function_lines=tuple(item.lines[0][1] - item.lines[0][0] + 1 for item in high),
    )


def _snapshot_roots(trial: TrialSource) -> dict[str, Path]:
    return {
        step_result.step: trial.trial_dir / step_result.snapshot.path
        for step_result in trial.record.step_results
        if step_result.snapshot is not None
    }


def _evidence(trial: TrialSource, assessment: Assessment | None) -> tuple[EvidenceItem, ...]:
    if assessment is None:
        return ()
    roots = _snapshot_roots(trial)
    items = []
    for item in assessment.result.evidence:
        component = EVIDENCE_COMPONENT.get(item.metric)
        if component is None:
            continue
        prior, _, current = item.subject.rpartition(TRANSITION_SEPARATOR)
        checkpoint = current or item.subject
        side = prior if item.kind.endswith(":removed") and prior else checkpoint
        root = roots.get(side)
        items.append(
            EvidenceItem(
                component=component,
                subject=item.subject,
                checkpoint=checkpoint,
                path=item.path,
                lines=tuple((span.line, span.end_line or span.line) for span in item.spans),
                kind=item.kind,
                message=item.message,
                sha256=item.sha256,
                snapshot_path=trial.portable_path(root / item.path) if root is not None else None,
            )
        )
    return tuple(items)


def _reason_text(reason: IneligibilityReason) -> str:
    component = f" {reason.component}" if reason.component else ""
    return f"{reason.code.value}{component}: {reason.detail}"


def _policy_score(policy: Metric, checkpoints: int, cells: Mapping[str, Cell]) -> MetricScore:
    """The headline metric applied to the problem-level cells it weighs."""
    inputs = {
        key: ComponentInput(value=Decimal(str(cell.value)))
        if cell.status == "ok"
        else ComponentInput(gap=f"{cell.status}: {cell.reason}")
        for key, cell in cells.items()
        if key not in policy.not_applicable and key not in ("I", "S")
    }
    return score_inputs(policy, checkpoints, inputs)


def _problem_cells(
    trial: TrialSource,
    policy: Metric,
    quality: Assessment | None,
    correctness: Assessment | None,
) -> tuple[dict[str, Cell], tuple[str, ...]]:
    """The analysis's trial outcome as cells, plus the inertia of the same policy score."""
    outcome = build_outcome(
        trial.record,
        {
            CORRECTNESS_REF: correctness.result if correctness is not None else None,
            CODE_QUALITY_REF: quality.result if quality is not None else None,
        },
        policy=policy,
    )
    cells = {"C": Cell.of_value(outcome.metrics[CORRECTNESS_METRIC])}
    for name, key in QUALITY_COMPONENTS.items():
        cells[key] = Cell.of_value(outcome.metrics[name])
    cells["S"] = Cell.of_value(outcome.metrics[HEADLINE_SCORE])
    if trial.truncation is not None:
        cells["I"] = Cell(status=_TRUNCATED, reason=cells["S"].reason)
        return {key: cells[key] for key in COMPONENTS}, ()
    for key in policy.not_applicable:
        cells[key] = Cell(status="not_applicable", reason=f"{policy.ref} does not use {key}")
    scored = _policy_score(policy, len(outcome.steps), cells)
    reasons = tuple(_reason_text(reason) for reason in scored.reasons)
    if scored.inertia is not None:
        cells["I"] = Cell(status="ok", value=float(scored.inertia))
    else:
        cells["I"] = _inertia(policy, cells)
    return {key: cells[key] for key in COMPONENTS}, reasons


def _jev_items(
    decisions: Sequence[DecisionRecord], labels: Mapping[str, LabelRecord]
) -> tuple[JevDecisionItem, ...]:
    return tuple(
        JevDecisionItem(
            request_id=record.request_id,
            policy=record.policy,
            subject=record.subject,
            stage=record.stage.value,
            verdict=record.verdict,
            rule=record.rule,
            label=labels[record.request_id].label if record.request_id in labels else None,
        )
        for record in decisions
    )


def _exclusions(quality: Assessment | None) -> tuple[ExclusionItem, ...]:
    if quality is None:
        return ()
    return tuple(
        ExclusionItem(checkpoint=item.subject, path=item.path, reason=item.reason)
        for item in quality.result.exclusions
    )


def _notes(trial: TrialSource, jev_note: str | None) -> tuple[str, ...]:
    notes = [trial.experiment.note, jev_note]
    if trial.record.agent_override:
        notes.append(f"agent override: {trial.record.agent_override}")
    if trial.truncation is not None:
        notes.append(f"development job, {trial.truncation}: no problem-level outcome")
    return tuple(note for note in notes if note)


def _problem_k(trial: TrialSource, checkpoints: Sequence[str]) -> int:
    """The problem's K: a truncated trial's steps are cut; its step results cover every step."""
    if trial.truncation is not None and trial.record.step_results:
        return len(trial.record.step_results)
    return len(checkpoints)


def _rows(
    trial: TrialSource,
    policy: Metric,
    checkpoints: Sequence[str],
    observations: tuple[_Observations, _Observations],
    evidence: Sequence[EvidenceItem],
) -> tuple[CheckpointRow, ...]:
    quality, correctness = observations
    states = {result.step: result.checkpoint_state.value for result in trial.record.step_results}
    previous: list[str | None] = [None, *checkpoints[:-1]]
    return tuple(
        CheckpointRow(
            checkpoint=checkpoint,
            state=states.get(checkpoint, "not_planned"),
            cells=_row_cells(policy, checkpoint, prior, quality, correctness),
            signals=_signals(checkpoint, quality, evidence),
        )
        for checkpoint, prior in zip(checkpoints, previous, strict=True)
    )


def _key(assessment: Assessment | None) -> str | None:
    return assessment.key if assessment is not None else None


def build_run_detail(
    trial: TrialSource,
    quality: Assessment | None,
    correctness: Assessment | None,
    *,
    decisions: Sequence[DecisionRecord] = (),
    labels: Mapping[str, LabelRecord] | None = None,
    jev_note: str | None = None,
    gate: Assessment | None = None,
) -> QualityRunDetail:
    record = trial.record
    checkpoints = _checkpoints(trial, quality, correctness)
    policy, info = resolve_policy(trial.experiment.metric, _problem_k(trial, checkpoints))
    quality_obs = _Observations(quality, f"no {CODE_QUALITY_REF} assessment matches this trial")
    correctness_obs = _Observations(
        correctness, f"no {CORRECTNESS_REF} assessment matches this trial"
    )
    evidence = _evidence(trial, quality)
    rows = _rows(trial, policy, checkpoints, (quality_obs, correctness_obs), evidence)
    problem, reasons = _problem_cells(trial, policy, quality, correctness)
    summary = QualityRunSummary(
        id=trial.review_id,
        experiment=record.experiment,
        job=trial.job_id,
        trial=record.trial.trial_id,
        arm=record.trial.arm,
        problem=record.trial.problem,
        attempt=record.trial.attempt,
        valid=record.valid,
        checkpoints=len(checkpoints),
        truncated_to=record.truncated_to,
        score=problem["S"],
        sources={CODE_QUALITY_REF: _key(quality), CORRECTNESS_REF: _key(correctness)},
        trial_record=trial.trial_record,
    )
    return QualityRunDetail(
        run=summary,
        policy=info,
        rows=rows,
        problem=problem,
        policy_reasons=reasons,
        evidence=evidence,
        exclusions=_exclusions(quality),
        jev_decisions=_jev_items(decisions, labels or {}),
        notes=_notes(trial, jev_note),
        gate=build_gate_view(trial, gate, checkpoints),
    )
