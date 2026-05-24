"""TypeScript implementation of the code-task scorer family."""

from __future__ import annotations

import re
from pathlib import Path

from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.code_task.base import CODE_TASK_METRICS, CodeTaskScorer

TYPESCRIPT_EXCLUDED_DIRS = {
    ".git",
    ".next",
    "coverage",
    "dist",
    "node_modules",
}


@register_scorer(id="typescript-code-task", version=1)
class TypeScriptCodeTask(CodeTaskScorer):
    """TypeScript-specific code-task scorer implementation contract."""

    status = "active"
    category = "quality"
    extends = "code-task"
    runtime = "typescript"
    description = (
        "Scores TypeScript code tasks using the code-task metric interface with "
        "TypeScript-specific measurement tools."
    )
    metrics = CODE_TASK_METRICS

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        """Collect deterministic TypeScript evidence for code-task metrics."""

        workspace = Path(context.workspace)
        files = _typescript_files(workspace)
        tests = [path for path in files if _is_test_file(path, workspace)]
        outputs = context.execution.outputs
        lint_gate = _latest_gate(outputs.gate_history, "lint")
        quality_findings = _static_quality_findings(files, workspace)

        required_artifacts = _required_artifact_patterns(context.scenario, self.id)
        return ScorerEvidence(
            metric_scores=(
                _typescript_functional_score(files, outputs.functional),
                _typescript_code_quality_score(lint_gate, quality_findings),
                _typescript_test_coverage_score(outputs.test_coverage),
                _typescript_artifact_score(files, tests, workspace, required_artifacts),
                _typescript_verification_stability_score(outputs.verification_stability),
            ),
            metadata={
                "source_files": len(files),
                "test_files": len(tests),
            },
        )


def _typescript_files(workspace: Path) -> list[Path]:
    return sorted(
        path
        for pattern in ("*.ts", "*.tsx")
        for path in workspace.rglob(pattern)
        if path.is_file() and not _has_excluded_part(path.relative_to(workspace))
    )


def _has_excluded_part(path: Path) -> bool:
    return any(part in TYPESCRIPT_EXCLUDED_DIRS for part in path.parts)


def _is_test_file(path: Path, workspace: Path) -> bool:
    relative = path.relative_to(workspace)
    return (
        "test" in relative.parts
        or "tests" in relative.parts
        or path.name.endswith(".test.ts")
        or path.name.endswith(".test.tsx")
        or path.name.endswith(".spec.ts")
        or path.name.endswith(".spec.tsx")
    )


def _static_quality_findings(files: list[Path], workspace: Path) -> list[str]:
    findings: list[str] = []
    forbidden_patterns = (
        (re.compile(r"\bany\b"), "explicit_any"),
        (re.compile(r"@ts-ignore"), "ts_ignore"),
        (re.compile(r"console\.(log|debug|warn|error)\s*\("), "console_output"),
    )
    for path in files:
        relative = path.relative_to(workspace)
        if relative.parts[0] != "src" or _is_test_file(path, workspace):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern, label in forbidden_patterns:
            if pattern.search(text):
                findings.append(f"{relative}: {label}")
    return findings


def _typescript_functional_score(
    files: list[Path],
    functional,
) -> MetricScore:
    source_present = bool(files)
    score = functional.score if source_present else 0.0
    return MetricScore(
        metric_id="functional",
        score=score,
        passed=source_present and functional.passed,
        missing_patterns=[] if source_present else ["*.ts", "*.tsx"],
        evidence=(
            f"typescript_files={len(files)}, build={functional.build_succeeded}, "
            f"tests={functional.tests_passed}/{functional.tests_total}, "
            f"gates={functional.gates_passed}/{functional.gates_total}"
        ),
    )


def _typescript_code_quality_score(
    lint,
    findings: list[str],
) -> MetricScore:
    lint_passed = lint is not None and lint.exit_code == 0
    static_passed = not findings
    score = (0.5 if lint_passed else 0.0) + (0.5 if static_passed else 0.0)
    return MetricScore(
        metric_id="code-quality",
        score=score,
        passed=score >= 1.0,
        missing_patterns=findings[:5],
        evidence=f"lint={_gate_evidence(lint)}, static_findings={len(findings)}",
    )


def _typescript_test_coverage_score(coverage) -> MetricScore:
    score = _coverage_score(coverage)
    return MetricScore(
        metric_id="test-coverage",
        score=score,
        passed=coverage.passed,
        evidence=(
            f"threshold={coverage.threshold}, measured={coverage.measured}, "
            f"source={coverage.source}"
        ),
    )


def _typescript_artifact_score(
    files: list[Path],
    tests: list[Path],
    workspace: Path,
    required_artifacts: tuple[str, ...],
) -> MetricScore:
    source_files = [path for path in files if not _is_test_file(path, workspace)]
    missing_required = _missing_required_artifacts(workspace, required_artifacts)
    missing = []
    if not source_files:
        missing.append("typescript source files")
    if not tests:
        missing.append("typescript test files")
    missing.extend(missing_required)
    structure_score = (0.5 if source_files else 0.0) + (0.5 if tests else 0.0)
    required_score = (
        1.0
        if not required_artifacts
        else (len(required_artifacts) - len(missing_required)) / len(required_artifacts)
    )
    score = round(structure_score * required_score, 3)
    return MetricScore(
        metric_id="artifact-checks",
        score=score,
        passed=score >= 1.0,
        matched_count=len(source_files) + len(tests),
        missing_patterns=missing,
        evidence=f"source_files={len(source_files)}, test_files={len(tests)}",
    )


def _required_artifact_patterns(scenario, scorer_id: str) -> tuple[str, ...]:
    patterns: list[str] = []
    for scorer_ref in getattr(scenario, "scorers", ()):
        if getattr(scorer_ref, "id", None) != scorer_id:
            continue
        artifact_config = getattr(scorer_ref, "config", {}).get("artifact-checks", {})
        required_paths = artifact_config.get("required_paths", [])
        if isinstance(required_paths, list):
            patterns.extend(path for path in required_paths if isinstance(path, str))
    return tuple(dict.fromkeys(patterns))


def _missing_required_artifacts(workspace: Path, patterns: tuple[str, ...]) -> list[str]:
    return [
        pattern
        for pattern in patterns
        if not any(path.is_file() for path in workspace.glob(pattern))
    ]


def _typescript_verification_stability_score(score) -> MetricScore:
    return MetricScore(
        metric_id="verification-stability",
        score=score.score,
        passed=score.score > 0,
        evidence=f"failures={score.total_gate_failures}",
    )


def _latest_gate(gate_history, gate_name: str):
    for event in reversed(gate_history):
        if event.gate_name == gate_name:
            return event
    return None


def _coverage_score(coverage) -> float:
    if coverage.threshold is None:
        return 1.0 if coverage.passed else 0.0
    if coverage.measured is None:
        return 0.0
    return min(1.0, coverage.measured / coverage.threshold)


def _gate_evidence(gate) -> str:
    if gate is None:
        return "not_run"
    output = re.sub(r"\s+", " ", f"{gate.stdout}\n{gate.stderr}").strip()
    if len(output) > 180:
        output = output[:177] + "..."
    return f"command={gate.command!r}, exit={gate.exit_code}, output={output!r}"
