#!/usr/bin/env python3
"""Validate fresh full-scenario AgentSpec matrix artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.matrix import MatrixJob, load_matrix_config, resolve_matrix_jobs

JUDGE_OUTPUT_FIELDS = {
    "passed",
    "score",
    "verdict",
    "evidence",
    "findings",
    "rubric_coverage",
    "residual_risk",
}
INVALID_ANALYSIS_MARKERS = (
    "selected scorer did not emit metric",
    "llm judge failed",
    "judge execution failed",
)


@dataclass(frozen=True, slots=True)
class ValidatedJob:
    entry_id: str
    scenario: str
    revision: str
    model: str
    summary_path: Path
    run_path: Path
    metric_ids: tuple[str, ...]
    scorer_ids: tuple[str, ...]
    time_to_start_sec: float
    orchestration_overhead_sec: float


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _artifact_config_matches(config: object, job: MatrixJob) -> bool:
    if not isinstance(config, dict):
        return False
    expected_model = f"{job.agent.provider}/{job.agent.model}"
    return (
        config.get("scenario_name") == job.scenario.name
        and config.get("scenario_revision") == job.scenario.scenario_revision
        and config.get("harness") == job.agent.harness
        and config.get("model") == expected_model
        and config.get("reasoning_effort") == job.agent.reasoning_effort
    )


def _summary_matches(payload: dict[str, Any], job: MatrixJob) -> bool:
    return _artifact_config_matches(payload.get("config"), job)


def _validate_run_config(run_path: Path, run: dict[str, Any], job: MatrixJob) -> None:
    if _artifact_config_matches(run.get("config"), job):
        return
    raise ValueError(
        f"AgentSpec config mismatch in {run_path}: "
        f"expected={job.scenario.name}@{job.scenario.scenario_revision} "
        f"{job.agent.harness}/{job.agent.provider}/{job.agent.model} "
        f"[{job.agent.reasoning_effort}] actual={run.get('config')!r}"
    )


def _latest_summary(
    experiments_root: Path, job: MatrixJob, *, started_after: float
) -> tuple[Path, dict[str, Any]]:
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in experiments_root.glob("**/experiment-summary.json"):
        if path.stat().st_mtime < started_after:
            continue
        payload = _load_json(path)
        if _summary_matches(payload, job):
            matches.append((path, payload))
    if not matches:
        raise ValueError(
            "No fresh experiment summary for "
            f"{job.scenario.name}@{job.scenario.scenario_revision} "
            f"{job.agent.harness}/{job.agent.model} [{job.agent.reasoning_effort}]"
        )
    return max(matches, key=lambda item: item[0].stat().st_mtime)


def _single_run(
    summary_path: Path, summary: dict[str, Any]
) -> tuple[Path, dict[str, Any]]:
    run_paths = sorted(summary_path.parent.glob("runs/*/run.json"))
    if summary.get("run_count") != 1 or len(run_paths) != 1:
        raise ValueError(
            f"Expected exactly one completed run for {summary_path}; "
            f"summary={summary.get('run_count')} artifacts={len(run_paths)}"
        )
    return run_paths[0], _load_json(run_paths[0])


def _meaningful_scorer_analysis(
    run_path: Path, run: dict[str, Any], job: MatrixJob
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    scores = run.get("scores")
    if not isinstance(scores, dict):
        raise ValueError(f"Scores missing: {run_path}")
    if run.get("terminated_early") is not False:
        raise ValueError(f"Agent process terminated early: {run_path}")
    if scores.get("unscored") is not False:
        raise ValueError(f"Run is unscored: {run_path}")

    metric_rows = scores.get("metric_scores")
    scorer_rows = scores.get("scorer_results")
    if not isinstance(metric_rows, list) or not isinstance(scorer_rows, list):
        raise ValueError(f"Canonical scorer outputs missing: {run_path}")
    metrics = _rows_by_id(run_path, metric_rows, field="metric_id", label="metric")
    scorers = _rows_by_id(run_path, scorer_rows, field="scorer_id", label="scorer")
    expected_metrics = {metric.id: metric for metric in job.scenario.resolved_metrics()}
    if set(metrics) != set(expected_metrics):
        raise ValueError(
            f"Metric result mismatch in {run_path}: "
            f"expected={sorted(expected_metrics)} actual={sorted(metrics)}"
        )
    expected_scorers = {reference.id for reference in job.scenario.scorers}
    if set(scorers) != expected_scorers:
        raise ValueError(
            f"Scorer result mismatch in {run_path}: "
            f"expected={sorted(expected_scorers)} actual={sorted(scorers)}"
        )

    resolved_scorers = {scorer.id: scorer for scorer in job.scenario.resolved_scorers()}
    for scorer_id, scorer in scorers.items():
        contributions = scorer.get("metric_contributions")
        if not isinstance(contributions, list) or not contributions:
            raise ValueError(f"Scorer {scorer_id} has no metric analysis: {run_path}")
        contribution_rows = _rows_by_id(
            run_path,
            contributions,
            field="metric_id",
            label=f"{scorer_id} contribution",
        )
        expected_contributions = {metric.id for metric in resolved_scorers[scorer_id].metrics}
        if set(contribution_rows) != expected_contributions:
            raise ValueError(
                f"Scorer {scorer_id} contribution mismatch in {run_path}: "
                f"expected={sorted(expected_contributions)} "
                f"actual={sorted(contribution_rows)}"
            )
        for contribution in contributions:
            metric_id = (
                contribution.get("metric_id")
                if isinstance(contribution, dict)
                else None
            )
            metric = metrics.get(metric_id)
            if metric is None:
                raise ValueError(
                    f"Scorer {scorer_id} references missing metric {metric_id!r}: {run_path}"
                )
            evidence = metric.get("evidence")
            if not isinstance(evidence, str) or not evidence.strip():
                raise ValueError(
                    f"Metric {metric_id} has no meaningful deterministic/judge analysis: {run_path}"
                )
            normalized_evidence = evidence.casefold()
            if any(marker in normalized_evidence for marker in INVALID_ANALYSIS_MARKERS):
                raise ValueError(f"Metric {metric_id} contains failed analysis: {run_path}")
            if expected_metrics[metric_id].type == "llm-as-judge":
                _validate_judge_output(run_path, metric_id, metric.get("judge_output"))
    return tuple(sorted(metrics)), tuple(sorted(scorers))


def _rows_by_id(
    run_path: Path,
    rows: list[Any],
    *,
    field: str,
    label: str,
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        identifier = row.get(field) if isinstance(row, dict) else None
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"Invalid {label} row in {run_path}: {row!r}")
        if identifier in indexed:
            raise ValueError(f"Duplicate {label} {identifier!r} in {run_path}")
        indexed[identifier] = row
    return indexed


def _validate_judge_output(run_path: Path, metric_id: str, value: object) -> None:
    if not isinstance(value, dict) or set(value) != JUDGE_OUTPUT_FIELDS:
        raise ValueError(f"Judge metric {metric_id} has invalid structured output: {run_path}")
    score = value.get("score")
    valid = (
        isinstance(value.get("passed"), bool)
        and isinstance(score, int | float)
        and not isinstance(score, bool)
        and 0 <= score <= 1
        and isinstance(value.get("verdict"), str)
        and bool(value["verdict"].strip())
        and isinstance(value.get("evidence"), str)
        and bool(value["evidence"].strip())
        and isinstance(value.get("findings"), list)
        and isinstance(value.get("rubric_coverage"), dict)
        and isinstance(value.get("residual_risk"), list)
    )
    if not valid:
        raise ValueError(f"Judge metric {metric_id} has invalid structured output: {run_path}")


def _warm_timings(
    run_path: Path, run: dict[str, Any], *, require_warm: bool
) -> tuple[float, float]:
    scores = run["scores"]
    metadata = scores.get("metadata")
    harbor = metadata.get("harbor") if isinstance(metadata, dict) else None
    if not isinstance(harbor, dict):
        raise ValueError(f"Harbor metadata missing: {run_path}")
    time_to_start = harbor.get("time_to_experiment_start_sec")
    overhead = harbor.get("orchestration_overhead_excluding_agent_and_test_sec")
    if not isinstance(time_to_start, int | float) or not isinstance(
        overhead, int | float
    ):
        raise ValueError(f"Orchestration timings missing: {run_path}")
    if require_warm:
        cache = harbor.get("cache")
        if not isinstance(cache, dict):
            raise ValueError(f"Warm cache metadata missing: {run_path}")
        for layer in ("baseline", "preflight", "image"):
            entry = cache.get(layer)
            if not isinstance(entry, dict) or entry.get("hit") is not True:
                raise ValueError(f"Warm {layer} cache hit missing: {run_path}")
        if time_to_start >= 10.0 or overhead >= 10.0:
            raise ValueError(
                f"Warm orchestration budget exceeded: start={time_to_start}s "
                f"overhead={overhead}s path={run_path}"
            )
    return float(time_to_start), float(overhead)


def validate_job(
    experiments_root: Path, job: MatrixJob, *, started_after: float
) -> ValidatedJob:
    summary_path, summary = _latest_summary(
        experiments_root, job, started_after=started_after
    )
    run_path, run = _single_run(summary_path, summary)
    _validate_run_config(run_path, run, job)
    metric_ids, scorer_ids = _meaningful_scorer_analysis(run_path, run, job)
    time_to_start, overhead = _warm_timings(
        run_path,
        run,
        require_warm=job.agent.model == "gpt-5.6-terra",
    )
    return ValidatedJob(
        entry_id=job.entry_id,
        scenario=job.scenario.name,
        revision=job.scenario.scenario_revision,
        model=job.agent.model,
        summary_path=summary_path,
        run_path=run_path,
        metric_ids=metric_ids,
        scorer_ids=scorer_ids,
        time_to_start_sec=time_to_start,
        orchestration_overhead_sec=overhead,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", action="append", required=True, type=Path)
    parser.add_argument("--experiments-root", required=True, type=Path)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--started-after", required=True, type=float)
    args = parser.parse_args()

    validated: list[ValidatedJob] = []
    for config_path in args.config:
        config = load_matrix_config(config_path)
        for job in resolve_matrix_jobs(config, repo_root=args.repo_root):
            validated.append(
                validate_job(
                    args.experiments_root,
                    job,
                    started_after=args.started_after,
                )
            )

    for result in validated:
        print(
            "scenario-agent-matrix-ok "
            f"entry={result.entry_id} scenario={result.scenario}@{result.revision} "
            f"model={result.model} metrics={','.join(result.metric_ids)} "
            f"scorers={','.join(result.scorer_ids)} "
            f"time_to_start_sec={result.time_to_start_sec:.3f} "
            f"orchestration_overhead_sec={result.orchestration_overhead_sec:.3f} "
            f"run_json={result.run_path}"
        )
    print(f"scenario-agent-matrix-complete jobs={len(validated)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
