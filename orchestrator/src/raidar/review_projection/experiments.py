"""Experiment records: one per (experiment lock, problem, arm), comparing agent specs.

The surface groups experiment records by ``problem`` and ``arm`` (the arm's
condition label) and ranks agent specs inside each group, so one record holds
the trials (attempts) of one arm on one problem. Scored runs are those
with a headline score, repeatability flags a composite stddev of 0.1 or more,
and the sample is adequate at 3 scored runs and preferred at 5.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean, median, pstdev
from typing import Any

from raidar.review_projection.headline import (
    CORRECTNESS_SCORER,
    QUALITY_SCORERS,
    UNDECLARED_MARKER,
    policy_shares,
)
from raidar.review_projection.notes import EvidenceRef, Finding
from raidar.review_projection.spans import repo_path
from raidar.review_projection.trials import TrialInput
from raidar.schemas.review import (
    ArmChangeCategory,
    ArmComparableConfig,
    ResolvedScorerReviewConfig,
    ReviewExperimentRecord,
    ReviewProblemMetadata,
    ReviewRunDetail,
    ReviewRunRecord,
)
from raidar.spec.canonical import digest_json
from raidar.spec.metrics import DEFAULT_METRIC_REGISTRY
from raidar.spec.references import RegistryReferenceError
from raidar.spec.schema import ExperimentSpec

MINIMUM_SCORED_RUNS = 3
PREFERRED_SCORED_RUNS = 5
REPEAT_VARIANCE_STDDEV = 0.1
_POLICY_CATEGORIES = {
    CORRECTNESS_SCORER: "correctness",
    **dict.fromkeys(QUALITY_SCORERS, "quality"),
}


@dataclass(frozen=True)
class ArmGroup:
    """The trials of one arm on one problem under one experiment lock."""

    experiment_id: str
    spec: ExperimentSpec
    arm: str
    condition_manifest: object
    rules_artifacts: Mapping[str, str]
    parent_arm: str | None
    change_categories: Sequence[ArmChangeCategory]
    metric: str
    trials: Sequence[TrialInput]
    details: Sequence[ReviewRunDetail]


def stat_block(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {}
    return {
        "mean": round(fmean(values), 6),
        "median": round(median(values), 6),
        "stddev": round(pstdev(values), 6) if len(values) > 1 else 0.0,
        "min": round(min(values), 6),
        "max": round(max(values), 6),
    }


def _checkpoint_outcomes(runs: Sequence[ReviewRunRecord]) -> dict[str, dict[str, Any]]:
    """Per checkpoint: how many runs solved it (runs without a verifier verdict excluded)."""
    by_metric: dict[str, list[tuple[bool, float]]] = {}
    for run in runs:
        for metric in run.metric_scores:
            by_metric.setdefault(str(metric["metric_id"]), []).append(
                (bool(metric["passed"]), float(metric["score"]))
            )
    outcomes: dict[str, dict[str, Any]] = {}
    for metric_id, samples in by_metric.items():
        passed = sum(1 for ok, _ in samples if ok)
        outcomes[metric_id] = {
            "pass_count": passed,
            "fail_count": len(samples) - passed,
            "sample_size": len(samples),
            "pass_rate": round(passed / len(samples), 6),
            "mean_score": round(fmean(score for _, score in samples), 6),
        }
    return outcomes


def _measured(values: Sequence[float | None]) -> list[float]:
    return [value for value in values if value is not None]


def _scorer_outcomes(areas: Mapping[str, Sequence[float]]) -> dict[str, dict[str, Any]]:
    """Mean score area (C for correctness@1, I for code-quality@1) over scored runs."""
    return {
        ref: {"sample_size": len(values), "mean_score": round(fmean(values), 6)}
        for ref, values in areas.items()
        if values
    }


def _aggregate(runs: Sequence[ReviewRunRecord], policy: str) -> dict[str, Any]:
    scored = [run for run in runs if not run.unscored]
    composites = _measured([run.composite_score for run in scored])
    diagnostics = _measured([run.diagnostic_score for run in scored])
    qualities = _measured([run.quality_score for run in scored])
    return {
        "headline_policy": policy,
        "run_count_total": len(runs),
        "run_count_scored": len(scored),
        "unscored_count": len(runs) - len(scored),
        "validity_rate": round(fmean(float(run.valid) for run in runs), 6),
        "composite_score": stat_block(composites),
        "quality_score": stat_block(qualities),
        "diagnostic_score": stat_block(diagnostics),
        "duration_sec": stat_block([run.duration_ms / 1000 for run in runs]),
        "uncached_input_tokens": stat_block([float(run.total_input_tokens) for run in runs]),
        "metric_outcomes": _checkpoint_outcomes(runs),
        "scorer_outcomes": _scorer_outcomes(
            {"correctness@1": diagnostics, "code-quality@1": qualities}
        ),
    }


def _sample(scored: int, attempts: int) -> tuple[dict[str, Any], dict[str, Any]]:
    sample = {
        "minimum_scored_runs": MINIMUM_SCORED_RUNS,
        "preferred_scored_runs": PREFERRED_SCORED_RUNS,
        "sample_class": "review" if attempts >= PREFERRED_SCORED_RUNS else "smoke",
        "achieved_scored_runs": scored,
        "minimum_met": scored >= MINIMUM_SCORED_RUNS,
        "preferred_met": scored >= PREFERRED_SCORED_RUNS,
        "sample_adequacy": round(min(scored / PREFERRED_SCORED_RUNS, 1.0), 6),
    }
    rerun = {
        "target_scored_runs": attempts,
        "achieved_scored_runs": scored,
        "target_met": scored >= attempts,
    }
    return sample, rerun


def _undeclared_scorer_findings(
    runs: Sequence[ReviewRunRecord],
) -> tuple[list[Finding], list[str]]:
    """Runs no rescore or rerun can score: the headline needs an undeclared scorer."""
    undeclared = [
        run.id
        for run in runs
        if run.unscored and any(UNDECLARED_MARKER in reason for reason in run.unscored_reasons)
    ]
    if not undeclared:
        return [], []
    finding = Finding(
        id="policy-scorer-mismatch",
        kind="note",
        category="policy-scorer-mismatch",
        title="The headline metric needs a scorer the experiment does not declare",
        detail="no rescore or rerun produces a headline score; declare the scorer "
        "in assessment.scorers and relock",
        span=None,
        evidence=tuple(EvidenceRef("run", run_id) for run_id in undeclared),
    )
    return [finding], undeclared


def _findings(aggregate: Mapping[str, Any], runs: Sequence[ReviewRunRecord]) -> list[Finding]:
    findings, undeclared = _undeclared_scorer_findings(runs)
    unscored = [run.id for run in runs if run.unscored and run.id not in undeclared]
    if unscored:
        findings.append(
            Finding(
                id="rerun-target",
                kind="issue",
                category="rerun-target",
                title=f"{len(unscored)} trial(ies) without a headline score",
                detail="assess or rerun these trials before trusting the arm's mean",
                span=None,
                evidence=tuple(EvidenceRef("run", run_id) for run_id in unscored),
            )
        )
    if aggregate["run_count_scored"] < MINIMUM_SCORED_RUNS:
        findings.append(
            Finding(
                id="sample-adequacy",
                kind="note",
                category="sample-adequacy",
                title="Sample below the minimum",
                detail=f"{aggregate['run_count_scored']} scored of {MINIMUM_SCORED_RUNS} required",
                span=None,
            )
        )
    stddev = aggregate["composite_score"].get("stddev")
    if stddev is not None and stddev >= REPEAT_VARIANCE_STDDEV:
        findings.append(
            Finding(
                id="repeat-variance",
                kind="issue",
                category="repeat-variance",
                title="Attempts disagree",
                detail=f"composite stddev {stddev:.3f} across attempts",
                span=None,
            )
        )
    return findings


def _task_document(trial: TrialInput) -> Mapping[str, Any]:
    path = trial.job_dir / "tasks" / trial.record.trial.trial_id / "task.toml"
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _comparable(group: ArmGroup, task: Mapping[str, Any]) -> ArmComparableConfig:
    spec, first = group.spec, group.trials[0]
    labels, record = first.labels, first.record
    info, metadata = task.get("task", {}), task.get("metadata", {})
    body: dict[str, Any] = {
        "problem": labels.problem,
        "display_name": str(info.get("name") or labels.problem),
        "arm": labels.arm,
        "parent_arm": group.parent_arm,
        "description": str(info.get("description") or spec.description or labels.problem),
        "difficulty": str(metadata.get("difficulty") or "unrated"),
        "languages": [spec.runtime.capitalize()],
        "checkpoints": len(record.step_results),
        "timeout_sec": spec.protocol.agent_timeout_sec,
        "environment": {"runtime": spec.runtime, **spec.environment.model_dump(mode="json")},
        "verification": {
            "oracle": "raidar verifier, strict all-cases verdict per checkpoint",
            "progression": spec.protocol.model_dump(mode="json"),
            "problem_digest": record.trial.hashes.problem,
            "checkpoints": [step_result.checkpoint for step_result in record.step_results],
        },
        "scorers": [_scorer(ref, _shares(group.metric)) for ref in spec.assessment.scorers],
        "condition": {
            "label": labels.arm,
            "arm": group.arm,
            "components": group.condition_manifest,
        },
        "rules_artifacts": dict(group.rules_artifacts),
    }
    config = ArmComparableConfig(**body, fingerprint="")
    identity = config.model_dump(mode="json", exclude={"fingerprint"})
    return config.model_copy(update={"fingerprint": digest_json(identity)})


def _shares(policy_ref: str) -> dict[str, float]:
    """Each policy scorer's share of S under the headline metric; none for an unknown policy."""
    try:
        base, inertia = policy_shares(DEFAULT_METRIC_REGISTRY.get(policy_ref))
    except RegistryReferenceError:
        return {}
    return {CORRECTNESS_SCORER: base, **dict.fromkeys(QUALITY_SCORERS, inertia)}


def _scorer(ref: str, shares: Mapping[str, float]) -> ResolvedScorerReviewConfig:
    name, _, version = ref.partition("@")
    return ResolvedScorerReviewConfig(
        id=name,
        version=int(version) if version.isdigit() else 0,
        category=_POLICY_CATEGORIES.get(ref, "observation"),
        weight=shares.get(ref, 0.0),
        metrics=[],
    )


def _metadata(comparable: ArmComparableConfig) -> ReviewProblemMetadata:
    fields = ReviewProblemMetadata.model_fields
    return ReviewProblemMetadata(**{name: getattr(comparable, name) for name in fields})


def experiment_record(group: ArmGroup) -> ReviewExperimentRecord:
    first = group.trials[0]
    labels = first.labels
    runs = [detail.run for detail in group.details]
    aggregate = _aggregate(runs, group.metric)
    sample, rerun = _sample(aggregate["run_count_scored"], group.spec.analysis.attempts)
    rerun["unresolved_unscored_count"] = aggregate["unscored_count"]
    comparable = _comparable(group, _task_document(first))
    return ReviewExperimentRecord(
        experiment_id=group.experiment_id,
        dir=repo_path(first.job_dir.parent),
        problem=labels.problem,
        arm=labels.arm,
        harness=labels.harness,
        model=labels.model,
        reasoning_effort=labels.reasoning_effort,
        agent_spec=labels.agent_spec,
        synthetic=any(trial.synthetic for trial in group.trials),
        repeats=group.spec.analysis.attempts,
        problem_meta=_metadata(comparable),
        comparable_config=comparable,
        change_categories=list(group.change_categories),
        aggregate=aggregate,
        sample=sample,
        rerun=rerun,
        findings=[finding.as_json() for finding in _findings(aggregate, runs)],
        created_at_utc=first.run_started,
        run_ids=[run.id for run in sorted(runs, key=lambda run: (run.started_at, run.id))],
    )
