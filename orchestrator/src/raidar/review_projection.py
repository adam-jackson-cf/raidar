"""Typed Python ownership for review-surface projection artifacts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from raidar.findings import run_findings_artifact
from raidar.schemas.review import (
    ExperimentReviewProjection,
    ReviewAnnotation,
    ReviewExperimentRecord,
    ReviewProjectionArtifact,
    ReviewRunDetail,
    ReviewRunRecord,
    ReviewScenarioMetadata,
    ReviewSpan,
    ReviewSpanType,
    ReviewStatus,
    ScenarioReviewContract,
)
from raidar.schemas.scorecard import EvalRun

REPO_ROOT = Path(__file__).resolve().parents[4]
EXPERIMENT_PROJECTION_FILENAME = "review-projection.json"
PAYLOAD_CAP = 32_000


def _json_text(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _payload(value: object) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else _json_text(value)
    if len(text) <= PAYLOAD_CAP:
        return text
    return f"{text[:PAYLOAD_CAP]}\n...[truncated]"


def _to_ms(value: str | int | float | None) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


class _SpanBuilder:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.spans: list[ReviewSpan] = []

    def add(
        self,
        *,
        name: str,
        span_type: ReviewSpanType,
        status: ReviewStatus,
        parent: str | None = None,
        start: int | None = None,
        duration: int | None = None,
        model: str | None = None,
        input_value: object = None,
        output_value: object = None,
        attributes: object = None,
    ) -> str:
        span_id = f"{self.run_id}:s{len(self.spans) + 1}"
        self.spans.append(
            ReviewSpan(
                id=span_id,
                run_id=self.run_id,
                parent_span_id=parent,
                name=name,
                span_type=span_type,
                status=status,
                start_time_ms=start,
                end_time_ms=(
                    start + duration if start is not None and duration is not None else None
                ),
                duration_ms=duration,
                model=model,
                input_tokens=None,
                output_tokens=None,
                input_payload=_payload(input_value),
                output_payload=_payload(output_value),
                attributes=_payload(attributes),
            )
        )
        return span_id


def _run_status(run: EvalRun) -> ReviewStatus:
    if run.scores.unscored or not run.scores.execution_validity.passed:
        return "ERROR"
    return "OK"


def _trace_name(event_type: str, data: dict[str, Any]) -> str:
    if event_type == "bash_command":
        return str(data.get("command", "command"))[:60]
    if event_type == "file_change":
        return f"edit {data.get('file_path', 'file')}"
    if event_type == "assistant_message":
        return "assistant message"
    if event_type == "user_prompt":
        return "user prompt"
    if event_type == "tool_call":
        return f"tool {data.get('tool', data.get('name', 'call'))}"
    return event_type


def _project_spans(run: EvalRun) -> list[ReviewSpan]:
    builder = _SpanBuilder(run.id)
    started = _to_ms(run.timestamp) or 0
    root = builder.add(
        name=f"{run.scores.scenario_name}@{run.scores.scenario_revision}",
        span_type="TRACE",
        status=_run_status(run),
        start=started,
        duration=round(run.duration_sec * 1000),
    )
    agent = builder.add(
        name=f"{run.config.harness} · {run.config.model}",
        span_type="AGENT_ROOT",
        status=_run_status(run),
        parent=root,
        start=started,
        duration=round(run.duration_sec * 1000),
        model=run.config.model,
    )
    _project_trace_spans(builder, run, agent)
    _project_gate_spans(builder, run, root, started)
    _project_scoring_spans(builder, run, root, started)
    _project_support_spans(builder, run, root, started)
    return builder.spans


def _project_trace_spans(builder: _SpanBuilder, run: EvalRun, agent: str) -> None:
    for event in run.traces:
        event_type = event.event_type
        span_type: ReviewSpanType = "INTERNAL"
        if event_type == "assistant_message":
            span_type = "LLM_GENERATION"
        elif event_type in {"bash_command", "tool_call"}:
            span_type = "TOOL_CALL"
        builder.add(
            name=_trace_name(event_type, event.data),
            span_type=span_type,
            status="OK",
            parent=agent,
            start=_to_ms(event.timestamp),
            duration=1_000,
            model=run.config.model if event_type == "assistant_message" else None,
            input_value=None if event_type == "assistant_message" else event.data,
            output_value=event.data.get("content") if event_type == "assistant_message" else None,
        )


def _project_gate_spans(
    builder: _SpanBuilder,
    run: EvalRun,
    root: str,
    started: int,
) -> None:
    if not run.gate_history:
        return
    gates = builder.add(
        name="verification gates",
        span_type="INTERNAL",
        status="ERROR" if any(gate.exit_code != 0 for gate in run.gate_history) else "OK",
        parent=root,
        start=started,
        duration=max(1_500, len(run.gate_history) * 1_500),
    )
    for gate in run.gate_history:
        builder.add(
            name=f"gate:{gate.gate_name}",
            span_type="TOOL_CALL",
            status="OK" if gate.exit_code == 0 else "ERROR",
            parent=gates,
            start=_to_ms(gate.timestamp),
            duration=1_200,
            input_value=gate.command,
            output_value=gate.model_dump(mode="json"),
        )


def _project_scoring_spans(
    builder: _SpanBuilder,
    run: EvalRun,
    root: str,
    started: int,
) -> None:
    scoring = builder.add(
        name="scoring",
        span_type="INTERNAL",
        status="ERROR" if run.scores.unscored else "OK",
        parent=root,
        start=started,
        duration=2_000,
        output_value={
            "composite_score": run.scores.composite_score,
            "quality_score": run.scores.quality_score,
            "diagnostic_score": run.scores.diagnostic_score,
            "unscored": run.scores.unscored,
            "unscored_reasons": run.scores.unscored_reasons,
        },
    )
    scorer_spans: dict[str, str] = {}
    for scorer in run.scores.scorer_results:
        scorer_span = builder.add(
            name=f"scorer:{scorer.scorer_id}@{scorer.version}",
            span_type="INTERNAL",
            status="OK",
            parent=scoring,
            start=started,
            duration=1_000,
            output_value=scorer.model_dump(mode="json"),
        )
        for contribution in scorer.metric_contributions:
            scorer_spans[contribution.metric_id] = scorer_span
    for metric in run.scores.metric_scores:
        builder.add(
            name=f"metric:{metric.metric_id}",
            span_type="INTERNAL",
            status="OK" if metric.passed else "ERROR",
            parent=scorer_spans.get(metric.metric_id, scoring),
            start=started,
            duration=800,
            output_value=metric.model_dump(mode="json"),
        )


def _project_support_spans(
    builder: _SpanBuilder,
    run: EvalRun,
    root: str,
    started: int,
) -> None:
    builder.add(
        name="execution validity",
        span_type="INTERNAL",
        status="OK" if run.scores.execution_validity.passed else "ERROR",
        parent=root,
        start=started,
        duration=600,
        output_value={
            "execution_validity": run.scores.execution_validity.model_dump(mode="json"),
            "performance_gates": run.scores.performance_gates.model_dump(mode="json"),
        },
    )
    builder.add(
        name="process metrics",
        span_type="INTERNAL",
        status="OK",
        parent=root,
        start=started,
        duration=600,
        output_value={
            "process": run.scores.metadata.get("process", {}),
            "resource_efficiency": run.scores.resource_efficiency.model_dump(mode="json"),
        },
    )
    requirements = run.scores.metric_result("requirements-coverage")
    if requirements is not None:
        builder.add(
            name="requirements",
            span_type="INTERNAL",
            status="OK" if requirements.passed else "ERROR",
            parent=root,
            start=started,
            duration=600,
            output_value=requirements.model_dump(mode="json"),
        )
    builder.add(
        name="artifacts & evidence",
        span_type="INTERNAL",
        status="OK",
        parent=root,
        start=started,
        duration=600,
        output_value={
            "evidence": run.scores.metadata.get("evidence", {}),
            "workspace": run.scores.metadata.get("workspace", {}),
        },
    )


_FINDING_SECTION_NAMES = {
    "missing-required-command": "process metrics",
    "workflow-anomaly": "process metrics",
    "resource-outlier": "process metrics",
    "requirements-gap": "requirements",
    "requirements-satisfied": "requirements",
    "retained-evidence": "artifacts & evidence",
    "completion-claim": "execution validity",
    "performance-gate": "execution validity",
    "clean-verification": "verification gates",
}
_FINDING_REFERENCE_PREFIXES = {
    "failed-gate": "gate:",
    "judge-review": "metric:",
    "deterministic-cap": "metric:",
}


def _span_id(spans: list[ReviewSpan], name: str) -> str | None:
    return next((span.id for span in spans if span.name == name), None)


def _missing_artifact_span_id(
    spans: list[ReviewSpan],
    *,
    source: str | None,
    reference: str | None,
) -> str | None:
    if source != "metric_scores":
        return _span_id(spans, "artifacts & evidence")
    return _span_id(spans, f"metric:{reference}") or _span_id(spans, "scoring")


def _referenced_finding_span_id(
    spans: list[ReviewSpan],
    *,
    category: str,
    prefix: str,
    reference: str,
) -> str | None:
    direct = _span_id(spans, f"{prefix}{reference}")
    fallback = "verification gates" if category == "failed-gate" else "scoring"
    return direct or _span_id(spans, fallback)


def _finding_span_id(finding: Any, spans: list[ReviewSpan]) -> str | None:
    evidence = finding.evidence[0] if finding.evidence else None
    reference = evidence.reference if evidence else None
    source = evidence.source if evidence else None
    if finding.category == "missing-artifact":
        return _missing_artifact_span_id(spans, source=source, reference=reference)
    prefix = _FINDING_REFERENCE_PREFIXES.get(finding.category)
    if prefix and reference:
        return _referenced_finding_span_id(
            spans,
            category=finding.category,
            prefix=prefix,
            reference=reference,
        )
    section = _FINDING_SECTION_NAMES.get(finding.category)
    return _span_id(spans, section) if section else None


def _annotations(
    run: EvalRun,
    started_at: int,
    spans: list[ReviewSpan],
) -> list[ReviewAnnotation]:
    artifact = run_findings_artifact(run)
    return [
        ReviewAnnotation(
            id=f"{run.id}:{finding.id}",
            run_id=run.id,
            span_id=_finding_span_id(finding, spans),
            kind=finding.kind,
            note=f"{finding.title} - {finding.detail}" if finding.detail else finding.title,
            created_at=started_at,
            category=finding.category,
            evidence=[item.model_dump(mode="json") for item in finding.evidence],
        )
        for finding in artifact.findings
    ]


def _run_detail(
    run: EvalRun,
    *,
    experiment_id: str,
    synthetic: bool,
    reasoning_effort: str | None,
    experiment_dir: Path,
) -> ReviewRunDetail:
    started_at = _to_ms(run.timestamp) or 0
    spans = _project_spans(run)
    annotations = _annotations(run, started_at, spans)
    record = _run_record(
        run,
        experiment_id=experiment_id,
        synthetic=synthetic,
        reasoning_effort=reasoning_effort,
        experiment_dir=experiment_dir,
        started_at=started_at,
        spans=spans,
        annotations=annotations,
    )
    return ReviewRunDetail(run=record, spans=spans, annotations=annotations)


def _agent_spec(harness: object, model: object, reasoning_effort: str | None) -> str:
    value = f"{harness} · {model}"
    return f"{value} [{reasoning_effort}]" if reasoning_effort else value


def _process_metadata(run: EvalRun) -> dict[str, Any]:
    process = run.scores.metadata.get("process", {})
    return process if isinstance(process, dict) else {}


def _run_token_counts(run: EvalRun, process: dict[str, Any]) -> tuple[int, int]:
    input_tokens = process.get(
        "uncached_input_tokens",
        run.scores.resource_efficiency.uncached_input_tokens,
    )
    output_tokens = process.get(
        "output_tokens",
        run.scores.resource_efficiency.output_tokens,
    )
    return int(input_tokens or 0), int(output_tokens or 0)


def _finding_counts(
    annotations: list[ReviewAnnotation],
) -> tuple[dict[str, int], dict[str, int]]:
    kind_counts = Counter(annotation.kind for annotation in annotations)
    issue_categories = Counter(
        annotation.category for annotation in annotations if annotation.kind == "issue"
    )
    return (
        {
            "issue": kind_counts.get("issue", 0),
            "good": kind_counts.get("good", 0),
            "note": kind_counts.get("note", 0),
        },
        dict(issue_categories),
    )


def _artifact_paths(experiment_dir: Path, run_id: str) -> dict[str, str | None]:
    run_dir = experiment_dir / "runs" / run_id
    findings_path = run_dir / "findings.json"
    return {
        "run_json": os.path.relpath(run_dir / "run.json", REPO_ROOT),
        "findings_json": (
            os.path.relpath(findings_path, REPO_ROOT) if findings_path.exists() else None
        ),
    }


def _run_record(
    run: EvalRun,
    *,
    experiment_id: str,
    synthetic: bool,
    reasoning_effort: str | None,
    experiment_dir: Path,
    started_at: int,
    spans: list[ReviewSpan],
    annotations: list[ReviewAnnotation],
) -> ReviewRunRecord:
    process = _process_metadata(run)
    input_tokens, output_tokens = _run_token_counts(run, process)
    finding_counts, issue_categories = _finding_counts(annotations)
    unscored = run.scores.unscored
    return ReviewRunRecord(
        id=run.id,
        name=f"{run.scores.scenario_name}@{run.scores.scenario_revision} · {run.id}",
        scenario=run.scores.scenario_name,
        revision=run.scores.scenario_revision,
        harness=run.config.harness,
        model=run.config.model,
        reasoning_effort=reasoning_effort,
        agent_spec=_agent_spec(run.config.harness, run.config.model, reasoning_effort),
        experiment_id=experiment_id,
        started_at=started_at,
        duration_ms=round(run.duration_sec * 1_000),
        status=_run_status(run),
        span_count=len(spans),
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        composite_score=None if unscored else run.scores.composite_score,
        quality_score=None if unscored else run.scores.quality_score,
        diagnostic_score=None if unscored else run.scores.diagnostic_score,
        unscored=unscored,
        unscored_reasons=run.scores.unscored_reasons,
        valid=run.scores.execution_validity.passed,
        synthetic=synthetic,
        metric_scores=[metric.model_dump(mode="json") for metric in run.scores.metric_scores],
        finding_counts=finding_counts,
        issue_categories=issue_categories,
        failed_gates=[gate.gate_name for gate in run.gate_history if gate.exit_code != 0],
        artifact_paths=_artifact_paths(experiment_dir, run.id),
    )


def _summary_objects(
    summary: dict[str, object],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    values = [summary.get(key) for key in ("aggregate", "sample", "rerun")]
    labels = ("aggregate", "sample", "rerun")
    for label, value in zip(labels, values, strict=True):
        if not isinstance(value, dict):
            raise ValueError(f"experiment summary {label} must be an object")
    findings = summary.get("findings")
    if not isinstance(findings, list) or not all(isinstance(item, dict) for item in findings):
        raise ValueError("experiment summary findings must be an array of objects")
    aggregate, sample, rerun = values
    assert isinstance(aggregate, dict)
    assert isinstance(sample, dict)
    assert isinstance(rerun, dict)
    return aggregate, sample, rerun, findings


def _experiment_record(
    experiment_dir: Path,
    summary: dict[str, object],
    config: dict[str, Any],
    review: ScenarioReviewContract,
    details: list[ReviewRunDetail],
    *,
    experiment_id: str,
    reasoning_effort: str | None,
    synthetic: bool,
) -> ReviewExperimentRecord:
    aggregate, sample, rerun, findings = _summary_objects(summary)
    comparable = review.comparable_config
    scenario_meta = ReviewScenarioMetadata(
        display_name=comparable.display_name,
        description=comparable.description,
        difficulty=comparable.difficulty,
        task_kind=comparable.task_kind,
        delivery_mode=comparable.delivery_mode,
        workspace_state=comparable.workspace_state,
        starting_conditions=comparable.starting_conditions,
        languages=comparable.languages,
        evaluation_role=comparable.evaluation_role,
        catalog_status=comparable.catalog_status,
        family_id=comparable.family_id,
        variant_axes=comparable.variant_axes,
        timeout_sec=comparable.timeout_sec,
    )
    return ReviewExperimentRecord(
        experiment_id=experiment_id,
        dir=os.path.relpath(experiment_dir, REPO_ROOT),
        scenario=str(config["scenario_name"]),
        revision=str(config["scenario_revision"]),
        harness=str(config["harness"]),
        model=str(config["model"]),
        reasoning_effort=reasoning_effort,
        agent_spec=_agent_spec(config["harness"], config["model"], reasoning_effort),
        synthetic=synthetic,
        repeats=int(config["repeats"]),
        scenario_meta=scenario_meta,
        comparable_config=comparable,
        change_categories=review.change_categories,
        aggregate=aggregate,
        sample=sample,
        rerun=rerun,
        findings=findings,
        created_at_utc=(
            str(summary["created_at_utc"]) if summary.get("created_at_utc") is not None else None
        ),
        run_ids=[detail.run.id for detail in details],
    )


def build_experiment_review_projection(
    experiment_dir: Path,
    summary: dict[str, object],
    runs: list[EvalRun],
) -> ExperimentReviewProjection:
    """Build one experiment projection from typed run and scenario-review artifacts."""

    config = summary.get("config")
    if not isinstance(config, dict):
        raise ValueError("experiment summary config must be an object")
    review = ScenarioReviewContract.model_validate(config.get("review"))
    experiment_id = str(summary["experiment_id"])
    reasoning_effort = config.get("reasoning_effort")
    if reasoning_effort is not None and not isinstance(reasoning_effort, str):
        raise ValueError("experiment reasoning_effort must be a string or null")
    synthetic = bool(summary.get("synthetic"))
    details = [
        _run_detail(
            run,
            experiment_id=experiment_id,
            synthetic=synthetic,
            reasoning_effort=reasoning_effort,
            experiment_dir=experiment_dir,
        )
        for run in runs
    ]
    record = _experiment_record(
        experiment_dir,
        summary,
        config,
        review,
        details,
        experiment_id=experiment_id,
        reasoning_effort=reasoning_effort,
        synthetic=synthetic,
    )
    return ExperimentReviewProjection(
        experiment=record,
        runs=details,
        revision_diff=review.revision_diff,
    )


def persist_experiment_review_projection(
    experiment_dir: Path,
    summary: dict[str, object],
    runs: list[EvalRun],
) -> Path:
    """Persist the versioned semantic review artifact beside an experiment."""

    artifact = build_experiment_review_projection(experiment_dir, summary, runs)
    output = experiment_dir / EXPERIMENT_PROJECTION_FILENAME
    output.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return output


def _load_experiment_projection(path: Path) -> ExperimentReviewProjection:
    try:
        return ExperimentReviewProjection.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise ValueError(f"invalid review projection {path}: {exc}") from exc


def _load_experiment_projections(experiments_root: Path) -> list[ExperimentReviewProjection]:
    projections: list[ExperimentReviewProjection] = []
    experiment_dirs = sorted(path for path in experiments_root.iterdir() if path.is_dir())
    for experiment_dir in experiment_dirs:
        projection_path = experiment_dir / EXPERIMENT_PROJECTION_FILENAME
        if projection_path.is_file():
            projections.append(_load_experiment_projection(projection_path))
        else:
            print(
                f"Skipping legacy experiment without {EXPERIMENT_PROJECTION_FILENAME}: "
                f"{experiment_dir.name}",
                file=sys.stderr,
            )
    return projections


def _merged_run_details(
    projections: list[ExperimentReviewProjection],
) -> list[ReviewRunDetail]:
    details = [detail for projection in projections for detail in projection.runs]
    run_ids = [detail.run.id for detail in details]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("review projection contains duplicate run ids")
    return details


def merge_review_projections(experiments_root: Path) -> ReviewProjectionArtifact:
    """Validate and merge persisted experiment review artifacts."""

    projections = _load_experiment_projections(experiments_root)
    if not projections:
        raise ValueError(f"no current-version review projections found under {experiments_root}")
    details = _merged_run_details(projections)
    revision_diffs = {
        projection.revision_diff.key: projection.revision_diff
        for projection in projections
        if projection.revision_diff is not None
    }
    return ReviewProjectionArtifact(
        generated_from=os.path.relpath(experiments_root, REPO_ROOT),
        experiments=[projection.experiment for projection in projections],
        runs=sorted(
            (detail.run for detail in details),
            key=lambda run: (-run.started_at, run.id),
        ),
        run_details={detail.run.id: detail for detail in details},
        revision_diffs=sorted(revision_diffs.values(), key=lambda diff: diff.key),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiments-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    artifact = merge_review_projections(args.experiments_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(
        f"Projected {len(artifact.runs)} run(s) across {len(artifact.experiments)} "
        f"experiment(s) into {args.output}"
    )


if __name__ == "__main__":
    main()
