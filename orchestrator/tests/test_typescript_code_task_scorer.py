"""Tests for the TypeScript code-task scorer."""

from pathlib import Path
from types import SimpleNamespace

from raidar.schemas.events import GateEvent
from raidar.schemas.scorecard import CoverageScore, FunctionalScore, VerificationStabilityScore
from raidar.scorers.base import ScorerContext
from raidar.scorers.code_task.typescript import TypeScriptCodeTask


def _context(
    workspace: Path,
    *,
    scenario: object | None = None,
    functional: FunctionalScore | None = None,
    coverage: CoverageScore | None = None,
    gate_history: list[GateEvent] | None = None,
) -> ScorerContext:
    return ScorerContext(
        workspace=workspace,
        scenario_dir=workspace,
        scenario=scenario or SimpleNamespace(),
        execution=SimpleNamespace(
            outputs=SimpleNamespace(
                functional=functional or FunctionalScore(),
                test_coverage=coverage or CoverageScore(passed=False),
                verification_stability=VerificationStabilityScore(),
                gate_history=gate_history or [],
            )
        ),
        resource_efficiency=SimpleNamespace(),
        execution_validity=SimpleNamespace(),
    )


def test_typescript_code_task_collects_metric_scores(
    tmp_path: Path,
) -> None:
    src = tmp_path / "src" / "lib"
    tests = tmp_path / "src" / "test"
    src.mkdir(parents=True)
    tests.mkdir(parents=True)
    (src / "math.ts").write_text(
        "export function sumEven(nums: number[]): number {\n"
        "  return nums.filter((value) => Number.isInteger(value) && value % 2 === 0)"
        ".reduce((total, value) => total + value, 0);\n"
        "}\n",
        encoding="utf-8",
    )
    (tests / "math.test.ts").write_text(
        "import { describe, expect, it } from 'vitest';\n"
        "import { sumEven } from '../lib/math';\n"
        "describe('sumEven', () => { it('sums even integers', () => {"
        " expect(sumEven([1, 2, 3, 4])).toBe(6); }); });\n",
        encoding="utf-8",
    )
    evidence = TypeScriptCodeTask().collect_evidence(
        _context(
            tmp_path,
            functional=FunctionalScore(
                passed=True,
                tests_passed=6,
                tests_total=6,
                build_succeeded=True,
                gates_passed=4,
                gates_total=4,
            ),
            coverage=CoverageScore(threshold=0.8, measured=0.8, source="gate:coverage"),
            gate_history=[
                GateEvent(
                    timestamp="2026-01-01T00:00:00Z",
                    gate_name="lint",
                    command="bun run lint",
                    exit_code=0,
                    stdout="Checked files.",
                    stderr="",
                )
            ],
        )
    )
    scores = {score.metric_id: score for score in evidence.metric_scores}

    assert scores["functional"].passed
    assert scores["code-quality"].passed
    assert scores["test-coverage"].score == 1.0
    assert scores["artifact-checks"].passed


def test_typescript_code_task_reports_missing_verifier_evidence(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (tmp_path / "package.json").write_text('{"scripts":{}}', encoding="utf-8")
    (src / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")

    evidence = TypeScriptCodeTask().collect_evidence(_context(tmp_path))
    scores = {score.metric_id: score for score in evidence.metric_scores}

    assert not scores["functional"].passed
    assert "gates=0/0" in str(scores["functional"].evidence)
    assert not scores["code-quality"].passed
    assert scores["artifact-checks"].missing_patterns == ["typescript test files"]


def test_typescript_code_task_redacts_secret_shaped_gate_output(tmp_path: Path) -> None:
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")
    (tests / "index.test.ts").write_text("expect(value).toBe(1);\n", encoding="utf-8")

    evidence = TypeScriptCodeTask().collect_evidence(
        _context(
            tmp_path,
            gate_history=[
                GateEvent(
                    timestamp="2026-01-01T00:00:00Z",
                    gate_name="lint",
                    command="bun run lint",
                    exit_code=1,
                    stdout="token=super-secret-value-1234567890",
                    stderr="Bearer abcdefghijklmnopqrstuvwxyz1234567890",
                )
            ],
        )
    )
    scores = {score.metric_id: score for score in evidence.metric_scores}

    assert "super-secret-value" not in str(scores["code-quality"].evidence)
    assert "abcdefghijklmnopqrstuvwxyz" not in str(scores["code-quality"].evidence)


def test_typescript_code_task_enforces_configured_artifact_paths(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")
    (src / "index.test.ts").write_text("import { value } from './index';\n", encoding="utf-8")
    scenario = SimpleNamespace(
        scorers=[
            SimpleNamespace(
                id="typescript-code-task",
                config={
                    "artifact-checks": {
                        "required_paths": ["src/lib/math.ts", "src/test/math.test.ts"]
                    }
                },
            )
        ]
    )

    evidence = TypeScriptCodeTask().collect_evidence(_context(tmp_path, scenario=scenario))
    scores = {score.metric_id: score for score in evidence.metric_scores}

    assert not scores["artifact-checks"].passed
    assert scores["artifact-checks"].score == 0.0
    assert scores["artifact-checks"].missing_patterns == [
        "src/lib/math.ts",
        "src/test/math.test.ts",
    ]
