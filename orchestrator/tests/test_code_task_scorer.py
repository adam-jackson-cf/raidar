"""Tests for code-task scorer implementations."""

from pathlib import Path
from types import SimpleNamespace

from raidar.schemas.scorecard import VerificationStabilityScore
from raidar.scorers.base import ScorerContext
from raidar.scorers.code_task import PythonCodeTask
from raidar.scorers.verification import VerificationEvidence


def _context(workspace: Path) -> ScorerContext:
    return ScorerContext(
        workspace=workspace,
        scenario_dir=workspace,
        scenario=SimpleNamespace(),
        execution=SimpleNamespace(
            outputs=SimpleNamespace(
                verification_stability=VerificationStabilityScore(),
            )
        ),
        resource_efficiency=SimpleNamespace(),
        execution_validity=SimpleNamespace(),
        verification_evidence=VerificationEvidence(
            actions={
                "python-code-task.pytest": _command_action(
                    ["python", "-m", "pytest", "-q"], 0, "1 passed"
                ),
                "python-code-task.coverage.run": _command_action(
                    ["python", "-m", "coverage", "run", "-m", "pytest", "-q"],
                    0,
                    "1 passed",
                ),
                "python-code-task.coverage.json": _command_action(
                    ["python", "-m", "coverage", "json", "-o", ".raidar/coverage.json"],
                    0,
                    "Wrote JSON report",
                ),
                "python-code-task.coverage.report": {
                    "result": {"content": {"totals": {"percent_covered": 100}}},
                    "status": "ok",
                },
                "python-code-task.ruff": _command_action(
                    ["python", "-m", "ruff", "check", "."], 0, "All checks passed"
                ),
                "python-code-task.lizard": _command_action(
                    ["python", "-m", "lizard", ".", "--CCN", "10", "--length", "100"],
                    0,
                    "",
                ),
            }
        ),
    )


def _command_action(command: list[str], exit_code: int, stdout: str) -> dict:
    return {
        "kind": "command",
        "status": "ok",
        "result": {
            "command": command,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": "",
        },
    }


def _scores_by_id(workspace: Path):
    evidence = PythonCodeTask().collect_evidence(_context(workspace))
    return {score.metric_id: score for score in evidence.metric_scores}


def test_python_code_task_collects_deterministic_metric_scores(tmp_path: Path) -> None:
    package = tmp_path / "src" / "demo"
    tests = tmp_path / "tests"
    package.mkdir(parents=True)
    tests.mkdir()
    (package / "__init__.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    (tests / "test_demo.py").write_text(
        "from src.demo import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )

    scores = _scores_by_id(tmp_path)

    assert scores["functional"].passed
    assert scores["artifact-checks"].passed
    assert scores["test-coverage"].score > 0
    assert "ruff=" in str(scores["code-quality"].evidence)
    assert "lizard=" in str(scores["code-quality"].evidence)


def test_python_code_task_reports_compile_failures(tmp_path: Path) -> None:
    package = tmp_path / "src" / "demo"
    package.mkdir(parents=True)
    (package / "broken.py").write_text("def broken(:\n", encoding="utf-8")

    scores = _scores_by_id(tmp_path)

    assert not scores["functional"].passed
    assert scores["functional"].score == 0.5
    assert not scores["code-quality"].passed
    assert scores["artifact-checks"].missing_patterns == ["python test files"]
