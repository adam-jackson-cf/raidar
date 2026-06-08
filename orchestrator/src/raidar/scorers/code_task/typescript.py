"""TypeScript implementation of the code-task scorer family."""

from __future__ import annotations

import re
from pathlib import Path

from raidar.sanitization import sanitize_evidence_text
from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.code_task.base import CodeTaskScorer
from raidar.scorers.common import (
    code_task_artifact_metric_score,
    required_artifact_patterns,
    verification_stability_score,
)

TYPESCRIPT_EXCLUDED_DIRS = {
    ".git",
    ".next",
    "coverage",
    "dist",
    "node_modules",
}


@register_scorer(scorer_id="typescript-code-task", version=1)
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
    metrics = CodeTaskScorer.default_metrics()

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        """Collect deterministic TypeScript evidence for code-task metrics."""

        workspace = Path(context.workspace)
        files = _typescript_files(workspace)
        tests = [path for path in files if _is_test_file(path, workspace)]
        outputs = context.execution.outputs
        lint_gate = _latest_gate(outputs.gate_history, "lint")
        quality_findings = _static_quality_findings(files, workspace)

        required_artifacts = required_artifact_patterns(context.scenario, self.id)
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
        evidence=(
            "proxy: static analysis checks lint gate and bounded source heuristics; "
            f"lint={_gate_evidence(lint)}, static_findings={len(findings)}"
        ),
    )


def _typescript_test_coverage_score(coverage) -> MetricScore:
    score = _coverage_score(coverage)
    return MetricScore(
        metric_id="test-coverage",
        score=score,
        passed=coverage.passed,
        evidence=(
            "proxy: coverage is a test adequacy proxy; "
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
    return code_task_artifact_metric_score(
        language_label="typescript",
        files=files,
        tests=tests,
        workspace=workspace,
        required_artifacts=required_artifacts,
        is_test_file=_is_test_file,
    )


def _typescript_verification_stability_score(score) -> MetricScore:
    return verification_stability_score(score)


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
    output = sanitize_evidence_text(f"{gate.stdout}\n{gate.stderr}")
    return f"command={gate.command!r}, exit={gate.exit_code}, output={output!r}"
