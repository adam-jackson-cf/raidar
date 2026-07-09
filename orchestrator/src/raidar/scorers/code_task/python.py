"""Python implementation of the code-task scorer family."""

from __future__ import annotations

import ast
import json
import os
import py_compile
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from raidar.config import settings
from raidar.sanitization import sanitize_evidence_text
from raidar.schemas.scenario import CapabilityRequirements
from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import (
    ScorerContext,
    ScorerEvidence,
    register_scorer,
)
from raidar.scorers.code_task.base import CodeTaskScorer
from raidar.scorers.common import (
    code_task_artifact_metric_score,
    required_artifact_patterns,
    verification_stability_score,
)

PYTHON_EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "site-packages",
    "venv",
}

MAX_FUNCTION_BRANCH_NODES = 10
MAX_FUNCTION_STATEMENTS = 80


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """Result of a scorer-owned command execution."""

    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return f"{self.stdout}\n{self.stderr}".strip()

    @property
    def passed(self) -> bool:
        return self.returncode == 0


@register_scorer(scorer_id="python-code-task", version=1)
class PythonCodeTask(CodeTaskScorer):
    """Python-specific code-task scorer implementation contract."""

    status = "active"
    category = "quality"
    extends = "code-task"
    description = (
        "Scores Python code tasks using the code-task metric interface with "
        "Python-specific measurement tools."
    )
    metrics = CodeTaskScorer.default_metrics()
    requirements = CapabilityRequirements(
        runtimes={"python": ">=3.12"},
        tools={
            "ruff": ">=0.14",
            "pytest": ">=9",
            "coverage": ">=7",
            "lizard": ">=1.17",
        },
    )

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        """Collect deterministic Python evidence for code-task metrics."""

        workspace = Path(context.workspace)
        files = _python_files(workspace)
        tests = [path for path in files if _is_test_file(path, workspace)]
        compile_failures = _compile_failures(files, workspace)
        pytest = _run_pytest(workspace, tests)
        coverage = _run_coverage(workspace, tests)
        ruff = _run_python_module(workspace, "ruff", "check", ".")
        lizard = _run_python_module(workspace, "lizard", ".", "--CCN", "10", "--length", "100")
        quality_findings = _static_quality_findings(files, workspace)
        required_artifacts = required_artifact_patterns(context.scenario, self.id)

        return ScorerEvidence(
            metric_scores=(
                _python_functional_score(files, tests, compile_failures, pytest),
                _python_code_quality_score(compile_failures, ruff, lizard, quality_findings),
                _python_test_coverage_score(coverage),
                _python_artifact_score(files, tests, workspace, required_artifacts),
                _python_verification_stability_score(
                    context.execution.outputs.verification_stability
                ),
            ),
            metadata={
                "source_files": len(files),
                "test_files": len(tests),
            },
        )


def _python_files(workspace: Path) -> list[Path]:
    return sorted(
        path
        for path in workspace.rglob("*.py")
        if path.is_file() and not _has_excluded_part(path.relative_to(workspace))
    )


def _has_excluded_part(path: Path) -> bool:
    return any(part in PYTHON_EXCLUDED_DIRS for part in path.parts)


def _is_test_file(path: Path, workspace: Path) -> bool:
    relative = path.relative_to(workspace)
    return (
        "tests" in relative.parts or path.name.startswith("test_") or path.name.endswith("_test.py")
    )


def _compile_failures(files: list[Path], workspace: Path) -> list[str]:
    failures: list[str] = []
    for path in files:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append(f"{path.relative_to(workspace)}: {exc.msg}")
    return failures


def _run_pytest(workspace: Path, tests: list[Path]) -> CommandOutcome | None:
    if not tests:
        return None
    return _run_command(
        workspace,
        (sys.executable, "-m", "pytest", "-q"),
        timeout=settings.timeouts.test,
    )


def _run_coverage(
    workspace: Path,
    tests: list[Path],
) -> tuple[CommandOutcome, CommandOutcome | None, float | None] | None:
    if not tests:
        return None
    with tempfile.TemporaryDirectory(prefix="raidar-python-coverage-") as temp_dir:
        coverage_json = Path(temp_dir) / "coverage.json"
        env = {"COVERAGE_FILE": str(Path(temp_dir) / ".coverage")}
        run = _run_command(
            workspace,
            (sys.executable, "-m", "coverage", "run", "-m", "pytest", "-q"),
            timeout=settings.timeouts.test,
            extra_env=env,
        )
        if not run.passed:
            return run, None, None
        report = _run_command(
            workspace,
            (sys.executable, "-m", "coverage", "json", "-o", str(coverage_json)),
            timeout=settings.timeouts.command_default,
            extra_env=env,
        )
        if not report.passed:
            return run, report, None
        return run, report, _coverage_percent(coverage_json)


def _coverage_percent(path: Path) -> float | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    totals = payload.get("totals")
    if not isinstance(totals, dict):
        return None
    value = totals.get("percent_covered")
    if not isinstance(value, (int, float)):
        return None
    return max(0.0, min(1.0, float(value) / 100.0))


def _run_python_module(workspace: Path, module: str, *args: str) -> CommandOutcome:
    return _run_command(
        workspace,
        (sys.executable, "-m", module, *args),
        timeout=settings.timeouts.command_default,
    )


def _run_command(
    workspace: Path,
    command: tuple[str, ...],
    *,
    timeout: int,
    extra_env: dict[str, str] | None = None,
) -> CommandOutcome:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        result = subprocess.run(
            list(command),
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandOutcome(
            command=command,
            returncode=-1,
            stdout=exc.stdout or "",
            stderr="Command timed out",
        )
    return CommandOutcome(
        command=command,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def _static_quality_findings(files: list[Path], workspace: Path) -> list[str]:
    findings: list[str] = []
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            findings.append(f"{path.relative_to(workspace)}: parse_error={type(exc).__name__}")
            continue
        findings.extend(_wildcard_import_findings(tree, path, workspace))
        findings.extend(_function_shape_findings(tree, path, workspace))
    return findings


def _wildcard_import_findings(tree: ast.AST, path: Path, workspace: Path) -> list[str]:
    return [
        f"{path.relative_to(workspace)}:{node.lineno}: wildcard_import"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
    ]


def _function_shape_findings(tree: ast.AST, path: Path, workspace: Path) -> list[str]:
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        branch_nodes = sum(
            isinstance(
                child,
                ast.If
                | ast.For
                | ast.AsyncFor
                | ast.While
                | ast.Try
                | ast.ExceptHandler
                | ast.BoolOp,
            )
            for child in ast.walk(node)
        )
        statement_count = sum(isinstance(child, ast.stmt) for child in ast.walk(node))
        if branch_nodes > MAX_FUNCTION_BRANCH_NODES:
            findings.append(
                f"{path.relative_to(workspace)}:{node.lineno}: "
                f"{node.name} branch_nodes={branch_nodes}"
            )
        if statement_count > MAX_FUNCTION_STATEMENTS:
            findings.append(
                f"{path.relative_to(workspace)}:{node.lineno}: "
                f"{node.name} statements={statement_count}"
            )
    return findings


def _python_functional_score(
    files: list[Path],
    tests: list[Path],
    compile_failures: list[str],
    pytest: CommandOutcome | None,
) -> MetricScore:
    compile_passed = bool(files) and not compile_failures
    tests_passed = pytest is None or pytest.passed
    score = (0.5 if compile_passed else 0.0) + (0.5 if tests_passed else 0.0)
    return MetricScore(
        metric_id="functional",
        score=score,
        passed=compile_passed and tests_passed,
        missing_patterns=[] if files else ["*.py"],
        evidence=(
            f"python_files={len(files)}, test_files={len(tests)}, "
            f"compile_failures={len(compile_failures)}, "
            f"pytest={_command_evidence(pytest)}"
        ),
    )


def _python_code_quality_score(
    compile_failures: list[str],
    ruff: CommandOutcome,
    lizard: CommandOutcome,
    findings: list[str],
) -> MetricScore:
    checks = [
        not compile_failures,
        ruff.passed,
        lizard.passed,
        not findings,
    ]
    score = sum(1 for check in checks if check) / len(checks)
    missing = []
    if compile_failures:
        missing.extend(compile_failures[:5])
    if findings:
        missing.extend(findings[:5])
    return MetricScore(
        metric_id="code-quality",
        score=score,
        passed=score >= 1.0,
        missing_patterns=missing,
        evidence=(
            f"proxy: static analysis checks compile, lint, complexity, and bounded AST heuristics; "
            f"compile={not compile_failures}, ruff={_command_evidence(ruff)}, "
            f"lizard={_command_evidence(lizard)}, static_findings={len(findings)}"
        ),
    )


def _python_test_coverage_score(
    coverage: tuple[CommandOutcome, CommandOutcome | None, float | None] | None,
) -> MetricScore:
    if coverage is None:
        return MetricScore(
            metric_id="test-coverage",
            score=0.0,
            passed=False,
            missing_patterns=["python tests"],
            evidence="coverage=not_run, reason=no_tests",
        )
    run, report, measured = coverage
    score = measured or 0.0
    return MetricScore(
        metric_id="test-coverage",
        score=score,
        passed=run.passed and report is not None and report.passed and measured is not None,
        evidence=(
            f"proxy: coverage is a test adequacy proxy; coverage_run={_command_evidence(run)}, "
            f"coverage_report={_command_evidence(report)}, measured={measured}"
        ),
    )


def _python_artifact_score(
    files: list[Path],
    tests: list[Path],
    workspace: Path,
    required_artifacts: tuple[str, ...],
) -> MetricScore:
    return code_task_artifact_metric_score(
        language_label="python",
        files=files,
        tests=tests,
        workspace=workspace,
        required_artifacts=required_artifacts,
        is_test_file=_is_test_file,
    )


def _python_verification_stability_score(score) -> MetricScore:
    return verification_stability_score(score)


def _command_evidence(outcome: CommandOutcome | None) -> str:
    if outcome is None:
        return "not_run"
    command = " ".join(outcome.command)
    output = sanitize_evidence_text(outcome.output)
    return f"command={command!r}, exit={outcome.returncode}, output={output!r}"
