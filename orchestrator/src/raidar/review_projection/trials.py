"""One trial (a Harbor multi-step trial) projected as a review run.

The span tree is::

    <problem>@<arm>                             TRACE
      <harness> · <model>                       AGENT_ROOT
        <checkpoint>                            one per attempted checkpoint
          system/user prompt, assistant message, tool calls   (ATIF)
      verification gates                        gate:<checkpoint>, gate:infra, gate:component-probes
      scoring                                   scorer:* / metric:* (headline metric)
      execution validity                        validity checks, reached, progression
      process metrics                           N1 phases, usage, unweighted scorer observations
      artifacts & evidence                      trial dir, snapshots, verifier records, assessments
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.execution.models import CheckpointState, TrialRecord
from raidar.review_projection.assessments import TrialAssessments
from raidar.review_projection.atif import project_trajectory
from raidar.review_projection.gates import GateContext, failed_gates, project_gates
from raidar.review_projection.headline import CORRECTNESS_SCORER, QUALITY_SCORER, Headline
from raidar.review_projection.notes import Finding, finding_counts, to_annotations
from raidar.review_projection.scoring import ScoringContext, project_scoring
from raidar.review_projection.spans import SpanBuilder, repo_path, to_ms
from raidar.schemas.review import ReviewRunDetail, ReviewRunRecord, ReviewStatus

_POLICY_SCORERS = frozenset({CORRECTNESS_SCORER, QUALITY_SCORER})


@dataclass(frozen=True)
class ArmLabels:
    """How a trial's arm reads on the surface.

    ``arm`` is the label of the arm's condition (``experiment_epoch._condition_labels``),
    so arms of one condition on different harnesses share it.
    """

    experiment_id: str
    problem: str
    arm: str
    harness: str
    model: str
    reasoning_effort: str | None
    agent_spec: str


@dataclass(frozen=True)
class TrialInput:
    review_id: str
    job_dir: Path
    run_started: str | None
    record: TrialRecord
    labels: ArmLabels
    assessments: TrialAssessments
    headline: Headline
    synthetic: bool

    @property
    def trial_dir(self) -> Path:
        return self.job_dir / self.record.trial_dir


def _harbor_result(trial_dir: Path) -> dict[str, Any]:
    path = trial_dir / "result.json"
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _timing(trial: TrialInput) -> tuple[int, int]:
    """Start (epoch ms) and duration of the trial from Harbor's result, else the job manifest."""
    result = _harbor_result(trial.trial_dir)
    started = to_ms(result.get("started_at")) or to_ms(trial.run_started) or 0
    finished = to_ms(result.get("finished_at"))
    if finished is not None and finished >= started:
        return started, finished - started
    return started, round(sum(sample.seconds for sample in trial.record.phases) * 1000)


def _tokens(record: TrialRecord) -> tuple[int, int]:
    """Uncached input and output tokens over every step (Harbor per-step usage)."""
    uncached = output = 0
    for step_result in record.step_results:
        usage = step_result.usage
        if usage is None:
            continue
        uncached += (usage.n_input_tokens or 0) - (usage.n_cache_tokens or 0)
        output += usage.n_output_tokens or 0
    return uncached, output


def _status(trial: TrialInput) -> ReviewStatus:
    return "OK" if trial.headline.composite is not None and trial.record.valid else "ERROR"


def _agent_spans(builder: SpanBuilder, trial: TrialInput, agent: str) -> None:
    for step_result in trial.record.step_results:
        if step_result.checkpoint_state is CheckpointState.NOT_ATTEMPTED:
            continue
        seconds = step_result.agent_execution_s
        checkpoint = builder.add(
            name=step_result.checkpoint,
            span_type="INTERNAL",
            status="OK" if step_result.checkpoint_state is CheckpointState.SOLVED else "ERROR",
            parent=agent,
            duration=round(seconds * 1000) if seconds is not None else None,
            input_value={
                "step": step_result.step,
                "instruction_sha256": step_result.instruction_sha256,
            },
            output_value={"usage": step_result.usage.model_dump() if step_result.usage else None},
        )
        if step_result.trace_dir is not None:
            project_trajectory(
                builder,
                trial.trial_dir / step_result.trace_dir,
                checkpoint,
                first_checkpoint=step_result.index == 0,
            )


def _observations(trial: TrialInput) -> dict[str, object]:
    return {
        ref: {
            "status": assessment.result.status.value,
            "reason": assessment.result.reason,
            "trajectory": {
                item.metric: item.value if item.value is not None else item.status.value
                for item in assessment.result.observations
                if item.subject == "trajectory"
            },
        }
        for ref, assessment in sorted(trial.assessments.found.items())
        if ref not in _POLICY_SCORERS
    }


def _support_spans(builder: SpanBuilder, trial: TrialInput, root: str, start: int) -> None:
    record = trial.record
    builder.add(
        name="execution validity",
        span_type="INTERNAL",
        status="OK" if record.valid else "ERROR",
        parent=root,
        start=start,
        output_value={
            "checks": [check.model_dump(mode="json") for check in record.validity.checks],
            "reached": list(record.reached),
            "reached_source": record.reached_source,
            "progression": record.progression.model_dump(mode="json"),
            "truncated_to": record.truncated_to,
            "trial_exception": record.trial_exception,
        },
    )
    builder.add(
        name="process metrics",
        span_type="INTERNAL",
        status="OK",
        parent=root,
        start=start,
        output_value={
            "phases": [sample.model_dump(mode="json") for sample in record.phases],
            "observations": _observations(trial),
        },
    )
    builder.add(
        name="artifacts & evidence",
        span_type="INTERNAL",
        status="OK",
        parent=root,
        start=start,
        output_value={
            "trial_dir": repo_path(trial.trial_dir),
            "snapshots": {
                a.step: a.snapshot.model_dump()
                for a in record.step_results
                if a.snapshot is not None
            },
            "verifier_records": {
                a.step: a.verifier_record for a in record.step_results if a.verifier_record
            },
            "trial_hashes": record.trial.hashes.model_dump(),
        },
    )


def _spans(builder: SpanBuilder, trial: TrialInput, start: int, duration: int) -> list[Finding]:
    labels, status = trial.labels, _status(trial)
    root = builder.add(
        name=f"{labels.problem}@{labels.arm}",
        span_type="TRACE",
        status=status,
        start=start,
        duration=duration,
    )
    agent = builder.add(
        name=f"{labels.harness} · {labels.model}",
        span_type="AGENT_ROOT",
        status=status,
        parent=root,
        start=start,
        duration=duration,
        model=labels.model,
    )
    _agent_spans(builder, trial, agent)
    findings = project_gates(GateContext(builder, root, start, trial.trial_dir, trial.record))
    findings += project_scoring(
        ScoringContext(builder, root, start, trial.record, trial.headline, trial.assessments)
    )
    _support_spans(builder, trial, root, start)
    return findings


def _metric_scores(record: TrialRecord) -> list[dict[str, object]]:
    return [
        {
            "metric_id": step_result.checkpoint,
            "score": step_result.strict_pass_rate,
            "passed": step_result.checkpoint_state is CheckpointState.SOLVED,
        }
        for step_result in record.step_results
        if step_result.strict_pass_rate is not None
    ]


def run_detail(trial: TrialInput) -> ReviewRunDetail:
    builder = SpanBuilder(trial.review_id)
    start, duration = _timing(trial)
    notes = to_annotations(_spans(builder, trial, start, duration), builder, start)
    counts, issues = finding_counts(notes)
    labels, headline = trial.labels, trial.headline
    uncached, output = _tokens(trial.record)
    unscored = headline.composite is None
    record = ReviewRunRecord(
        id=trial.review_id,
        name=f"{labels.problem}@{labels.arm} · {trial.review_id}",
        problem=labels.problem,
        arm=labels.arm,
        harness=labels.harness,
        model=labels.model,
        reasoning_effort=labels.reasoning_effort,
        agent_spec=labels.agent_spec,
        experiment_id=labels.experiment_id,
        started_at=start,
        duration_ms=duration,
        status=_status(trial),
        span_count=len(builder.spans),
        total_input_tokens=uncached,
        total_output_tokens=output,
        composite_score=headline.composite,
        quality_score=headline.quality,
        diagnostic_score=headline.diagnostic,
        unscored=unscored,
        unscored_reasons=_unscored_reasons(trial) if unscored else [],
        valid=trial.record.valid,
        synthetic=trial.synthetic,
        metric_scores=_metric_scores(trial.record),
        finding_counts=counts,
        issue_categories=issues,
        failed_gates=failed_gates(builder),
        artifact_paths={
            "run_json": repo_path(
                trial.job_dir / "trial-records" / f"{trial.record.trial.trial_id}.json"
            ),
            "findings_json": None,
            "trial_dir": repo_path(trial.trial_dir),
        },
    )
    return ReviewRunDetail(run=record, spans=builder.spans, annotations=notes)


def _exception(trial: TrialInput) -> list[str]:
    """Harbor's own account of a trial exception (type and message), when it recorded one."""
    info = _harbor_result(trial.trial_dir).get("exception_info")
    if isinstance(info, dict) and info.get("exception_type"):
        return [f"Trial exception: {info['exception_type']}: {info.get('exception_message', '')}"]
    record = trial.record
    return [f"Trial exception: {record.trial_exception}"] if record.trial_exception else []


def _unscored_reasons(trial: TrialInput) -> list[str]:
    """Why there is no headline score: withheld (invalid, truncated) first, then the policy's."""
    headline = trial.headline
    return [*headline.withheld, *_exception(trial), *headline.reasons]
