"""Deterministic review findings projected from retained run and experiment evidence.

Findings are non-authoritative review metadata: they never change scores and are
always derived from retained Raidar artifacts with evidence references.
"""

from __future__ import annotations

import statistics
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from raidar.sanitization import sanitize_evidence_text
from raidar.schemas.scorecard import EvalRun, MetricScore

FINDINGS_SCHEMA_VERSION = 1
DURATION_OUTLIER_Z = 2.0
REPEAT_VARIANCE_STDDEV_THRESHOLD = 0.1


class FindingEvidence(BaseModel):
    """One evidence reference supporting a finding."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(description="Evidence surface, for example gate_history or metric_scores")
    reference: str = Field(description="Stable reference inside the surface")
    detail: str = Field(default="", description="Short sanitized evidence excerpt")


class Finding(BaseModel):
    """One deterministic review finding."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable finding identifier inside its artifact")
    kind: Literal["issue", "good", "note"]
    category: str = Field(description="Finding category for grouping and filtering")
    title: str = Field(description="Short reviewer-facing headline")
    detail: str = Field(default="", description="Sanitized supporting detail")
    evidence: list[FindingEvidence] = Field(default_factory=list)
    source: Literal["deterministic"] = "deterministic"


class RunFindingsArtifact(BaseModel):
    """Persisted findings artifact for one evaluation run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = FINDINGS_SCHEMA_VERSION
    run_id: str
    scenario_name: str
    scenario_revision: str
    findings: list[Finding]


def run_findings(run: EvalRun) -> list[Finding]:
    """Project one run's retained evidence into review findings."""

    collected: list[Finding] = []
    collected.extend(_failed_gate_findings(run))
    collected.extend(_missing_required_command_findings(run))
    collected.extend(_requirements_gap_findings(run))
    collected.extend(_retained_evidence_findings(run))
    collected.extend(_metric_findings(run))
    collected.extend(_validity_findings(run))
    collected.extend(_process_findings(run))
    collected.extend(_strength_findings(run))
    return _with_ids(f"run-{run.id}", collected)


def run_findings_artifact(run: EvalRun) -> RunFindingsArtifact:
    return RunFindingsArtifact(
        run_id=run.id,
        scenario_name=run.scores.scenario_name,
        scenario_revision=run.scores.scenario_revision,
        findings=run_findings(run),
    )


def experiment_findings(runs: list[EvalRun], summary: dict[str, Any]) -> list[Finding]:
    """Project cross-run experiment evidence into review findings."""

    collected: list[Finding] = []
    collected.extend(_unscored_run_findings(runs))
    collected.extend(_repeat_variance_findings(summary))
    collected.extend(_duration_outlier_findings(runs))
    collected.extend(_sample_findings(summary))
    return _with_ids("experiment", collected)


def _with_ids(prefix: str, findings: list[Finding]) -> list[Finding]:
    return [
        finding.model_copy(update={"id": f"{prefix}-finding-{index:03d}"})
        for index, finding in enumerate(findings, start=1)
    ]


def _finding(
    kind: Literal["issue", "good", "note"],
    category: str,
    title: str,
    detail: str = "",
    evidence: list[FindingEvidence] | None = None,
) -> Finding:
    return Finding(
        id="pending",
        kind=kind,
        category=category,
        title=title,
        detail=sanitize_evidence_text(detail) if detail else "",
        evidence=evidence or [],
    )


def _failed_gate_findings(run: EvalRun) -> list[Finding]:
    failures: dict[str, list[Any]] = {}
    for event in run.gate_history:
        if event.exit_code != 0:
            failures.setdefault(event.gate_name, []).append(event)
    findings = []
    for gate_name, events in failures.items():
        last = events[-1]
        findings.append(
            _finding(
                "issue",
                "failed-gate",
                f"Gate '{gate_name}' failed {len(events)} time(s)",
                f"last exit_code={last.exit_code}, category={last.failure_category or 'unknown'}",
                [
                    FindingEvidence(
                        source="gate_history",
                        reference=gate_name,
                        detail=sanitize_evidence_text(last.stderr or last.stdout or ""),
                    )
                ],
            )
        )
    return findings


def _process_metadata(run: EvalRun) -> dict[str, Any]:
    process = run.scores.metadata.get("process", {})
    return process if isinstance(process, dict) else {}


def _missing_required_command_findings(run: EvalRun) -> list[Finding]:
    process = _process_metadata(run)
    first_pass = process.get("required_verification_first_pass", {})
    missing = [
        command
        for command, status in first_pass.items()
        if isinstance(status, str) and status == "missing"
    ]
    missing_count = process.get("missing_required_verification_commands", 0)
    if not missing and not missing_count:
        return []
    title = f"{missing_count or len(missing)} required verification command(s) never executed"
    evidence = [
        FindingEvidence(source="process_metadata", reference=command, detail="status=missing")
        for command in missing
    ]
    return [_finding("issue", "missing-required-command", title, "", evidence)]


def _requirements_gap_findings(run: EvalRun) -> list[Finding]:
    coverage = run.scores.requirements_coverage
    findings = []
    if coverage.missing_requirement_ids:
        findings.append(
            _finding(
                "issue",
                "requirements-gap",
                f"{len(coverage.missing_requirement_ids)} requirement(s) not satisfied",
                ", ".join(coverage.missing_requirement_ids),
                [
                    FindingEvidence(
                        source="requirements_coverage",
                        reference=requirement_id,
                        detail="deterministic check failed",
                    )
                    for requirement_id in coverage.missing_requirement_ids
                ],
            )
        )
    gaps = coverage.requirement_test_evidence_gaps
    if gaps:
        findings.append(
            _finding(
                "issue",
                "requirements-gap",
                f"{len(gaps)} requirement(s) missing required test evidence",
                ", ".join(sorted(gaps)),
                [
                    FindingEvidence(
                        source="requirements_coverage",
                        reference=requirement_id,
                        detail="; ".join(str(item) for item in items[:3]),
                    )
                    for requirement_id, items in sorted(gaps.items())
                ],
            )
        )
    return findings


def _retained_evidence_findings(run: EvalRun) -> list[Finding]:
    evidence_meta = run.scores.metadata.get("evidence", {})
    records = evidence_meta.get("retained_files") if isinstance(evidence_meta, dict) else None
    if not isinstance(records, list):
        return []
    findings = []
    for record in records:
        if not isinstance(record, dict):
            continue
        findings.append(_retained_file_finding(record))
    return findings


def _retained_file_finding(record: dict[str, Any]) -> Finding:
    path = str(record.get("path", "unknown"))
    status = str(record.get("status", "unknown"))
    reference = FindingEvidence(source="evidence_artifacts", reference=path, detail=status)
    if status == "ingested":
        keys = record.get("keys", [])
        return _finding(
            "good",
            "retained-evidence",
            f"Declared evidence file '{path}' retained",
            f"ingested keys: {', '.join(str(key) for key in keys)}",
            [reference],
        )
    return _finding(
        "issue",
        "missing-artifact",
        f"Declared evidence file '{path}' not usable ({status})",
        "",
        [reference],
    )


def _metric_findings(run: EvalRun) -> list[Finding]:
    findings = []
    for metric in run.scores.metric_scores:
        finding = _metric_finding(metric)
        if finding is not None:
            findings.append(finding)
    return findings


def _metric_finding(metric: MetricScore) -> Finding | None:
    evidence_text = metric.evidence or ""
    reference = FindingEvidence(
        source="metric_scores",
        reference=metric.metric_id,
        detail=sanitize_evidence_text(evidence_text),
    )
    if metric.judge_output is not None:
        kind = "note" if metric.passed else "issue"
        return _finding(
            kind,
            "judge-review",
            f"Judge-backed metric '{metric.metric_id}' scored {metric.score:.2f}",
            evidence_text,
            [reference],
        )
    if "capped" in evidence_text and not metric.passed:
        return _finding(
            "issue",
            "deterministic-cap",
            f"Metric '{metric.metric_id}' capped by deterministic prerequisites",
            evidence_text,
            [reference],
        )
    if metric.missing_patterns and not metric.passed:
        return _finding(
            "issue",
            "missing-artifact",
            f"Metric '{metric.metric_id}' missing expected evidence",
            ", ".join(metric.missing_patterns),
            [reference],
        )
    return None


def _validity_findings(run: EvalRun) -> list[Finding]:
    findings = []
    for check in run.scores.execution_validity.checks:
        if check.passed:
            continue
        findings.append(
            _finding(
                "issue",
                "completion-claim",
                f"Execution validity check '{check.name}' failed",
                check.evidence or "",
                [
                    FindingEvidence(
                        source="execution_validity",
                        reference=check.name,
                        detail=sanitize_evidence_text(check.evidence or ""),
                    )
                ],
            )
        )
    for check in run.scores.performance_gates.checks:
        if check.passed:
            continue
        findings.append(
            _finding(
                "issue",
                "performance-gate",
                f"Performance gate '{check.name}' failed",
                check.evidence or "",
                [
                    FindingEvidence(
                        source="performance_gates",
                        reference=check.name,
                        detail=sanitize_evidence_text(check.evidence or ""),
                    )
                ],
            )
        )
    return findings


def _process_findings(run: EvalRun) -> list[Finding]:
    process = _process_metadata(run)
    findings = []
    repeated = run.scores.resource_efficiency.repeated_verification_failures
    if repeated > 0:
        findings.append(
            _finding(
                "note",
                "workflow-anomaly",
                f"{repeated} repeated verification failure(s) in the same category",
                "",
                [
                    FindingEvidence(
                        source="resource_efficiency",
                        reference="repeated_verification_failures",
                        detail=str(repeated),
                    )
                ],
            )
        )
    bypass_commands = process.get("git_commit_verification_bypass_commands", [])
    if isinstance(bypass_commands, list) and bypass_commands:
        findings.append(
            _finding(
                "issue",
                "workflow-anomaly",
                "Verification-bypassing git commit command(s) detected",
                "",
                [
                    FindingEvidence(
                        source="process_metadata",
                        reference="git_commit_verification_bypass_commands",
                        detail=sanitize_evidence_text(str(command)),
                    )
                    for command in bypass_commands[:5]
                ],
            )
        )
    return findings


def _strength_findings(run: EvalRun) -> list[Finding]:
    findings = []
    scores = run.scores
    if scores.functional.passed and scores.verification_stability.total_gate_failures == 0:
        findings.append(
            _finding(
                "good",
                "clean-verification",
                "All verification gates passed without failures",
                "",
                [
                    FindingEvidence(
                        source="verification_stability",
                        reference="total_gate_failures",
                        detail="0",
                    )
                ],
            )
        )
    coverage = scores.requirements_coverage
    if coverage.total_requirements > 0 and coverage.satisfied_requirements == (
        coverage.total_requirements
    ):
        findings.append(
            _finding(
                "good",
                "requirements-satisfied",
                f"All {coverage.total_requirements} requirements satisfied",
                "",
                [
                    FindingEvidence(
                        source="requirements_coverage",
                        reference="satisfied_requirements",
                        detail=str(coverage.satisfied_requirements),
                    )
                ],
            )
        )
    return findings


def _unscored_run_findings(runs: list[EvalRun]) -> list[Finding]:
    findings = []
    for run in runs:
        if not run.scores.unscored:
            continue
        reasons = ", ".join(run.scores.unscored_reasons[:3])
        findings.append(
            _finding(
                "issue",
                "unscored-run",
                f"Run {run.id} is unscored",
                reasons,
                [
                    FindingEvidence(
                        source="run",
                        reference=run.id,
                        detail=sanitize_evidence_text(reasons),
                    )
                ],
            )
        )
    return findings


def _repeat_variance_findings(summary: dict[str, Any]) -> list[Finding]:
    aggregate = summary.get("aggregate", {})
    composite = aggregate.get("composite_score", {}) if isinstance(aggregate, dict) else {}
    stddev = composite.get("stddev") if isinstance(composite, dict) else None
    if not isinstance(stddev, int | float) or stddev < REPEAT_VARIANCE_STDDEV_THRESHOLD:
        return []
    return [
        _finding(
            "note",
            "repeat-variance",
            f"High composite-score variance across repeats (stddev={stddev:.3f})",
            "",
            [
                FindingEvidence(
                    source="experiment_aggregate",
                    reference="composite_score.stddev",
                    detail=f"{stddev:.3f}",
                )
            ],
        )
    ]


def _duration_outlier_findings(runs: list[EvalRun]) -> list[Finding]:
    scored = [run for run in runs if not run.scores.unscored]
    if len(scored) < 4:
        return []
    findings = []
    for run in scored:
        peers = [peer.duration_sec for peer in scored if peer is not run]
        if not _is_duration_outlier(run.duration_sec, peers):
            continue
        findings.append(
            _finding(
                "note",
                "resource-outlier",
                f"Run {run.id} duration {run.duration_sec:.0f}s is an outlier",
                f"peer mean {statistics.fmean(peers):.0f}s across {len(peers)} scored runs",
                [
                    FindingEvidence(
                        source="run",
                        reference=run.id,
                        detail=f"duration_sec={run.duration_sec:.1f}",
                    )
                ],
            )
        )
    return findings


def _is_duration_outlier(duration: float, peers: list[float]) -> bool:
    mean = statistics.fmean(peers)
    stddev = statistics.pstdev(peers)
    if stddev == 0:
        return mean > 0 and duration > mean * 1.5
    return duration > mean + DURATION_OUTLIER_Z * stddev


def _sample_findings(summary: dict[str, Any]) -> list[Finding]:
    sample = summary.get("sample", {})
    rerun = summary.get("rerun", {})
    findings = []
    if isinstance(sample, dict) and sample.get("minimum_met") is False:
        findings.append(
            _finding(
                "issue",
                "sample-adequacy",
                "Scored run count is below the scenario-family minimum",
                f"achieved={sample.get('achieved_scored_runs')}, "
                f"minimum={sample.get('minimum_scored_runs')}",
                [
                    FindingEvidence(
                        source="experiment_sample",
                        reference="minimum_met",
                        detail="false",
                    )
                ],
            )
        )
    if isinstance(rerun, dict) and rerun.get("target_met") is False:
        findings.append(
            _finding(
                "issue",
                "rerun-target",
                "Rerun budget exhausted before reaching the scored-run target",
                f"achieved={rerun.get('achieved_scored_runs')}, "
                f"target={rerun.get('target_scored_runs')}",
                [
                    FindingEvidence(
                        source="experiment_rerun",
                        reference="target_met",
                        detail="false",
                    )
                ],
            )
        )
    return findings
