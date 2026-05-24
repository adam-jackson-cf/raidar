"""Tests for code-task scorer implementations."""

from pathlib import Path
from types import SimpleNamespace

from raidar.schemas.scorecard import VerificationStabilityScore
from raidar.scorers.base import ScorerContext
from raidar.scorers.code_task import PythonCodeTask


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
    )


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
