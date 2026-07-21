"""Tests for the TypeScript code-task scorer."""

from pathlib import Path
from types import SimpleNamespace

from raidar.schemas.events import GateEvent
from raidar.schemas.scorecard import CoverageScore, FunctionalScore
from raidar.scorers.base import ScorerContext
from raidar.scorers.code_task.typescript import TypeScriptCodeTask
from raidar.scorers.evidence_contract import (
    CommandEvidenceResult,
    EvidenceActionResult,
    JsonEvidenceResult,
)
from raidar.scorers.verification import (
    VerificationEvidence,
)


def _context(
    workspace: Path,
    *,
    scenario: object | None = None,
    functional: FunctionalScore | None = None,
    coverage: CoverageScore | None = None,
    gate_history: list[GateEvent] | None = None,
) -> ScorerContext:
    resolved_scenario = scenario or SimpleNamespace(scorers=[])
    if not hasattr(resolved_scenario, "verification"):
        resolved_scenario.verification = SimpleNamespace(
            coverage_threshold=(coverage.threshold if coverage else None)
        )
    actions: dict[str, EvidenceActionResult] = {}
    if functional is not None:
        for local_id in ("typecheck", "lint", "build"):
            actions[f"typescript-code-task.{local_id}"] = _command_action(
                f"typescript-code-task.{local_id}", 0
            )
    for event in gate_history or []:
        if event.gate_name == "lint":
            actions["typescript-code-task.lint"] = _command_action(
                "typescript-code-task.lint",
                event.exit_code,
                stdout=event.stdout,
                stderr=event.stderr,
            )
    if coverage is not None and coverage.measured is not None:
        actions["typescript-code-task.coverage.summary"] = EvidenceActionResult(
            id="typescript-code-task.coverage.summary",
            kind="read_json",
            required=False,
            status="ok",
            duration_sec=0.1,
            result=JsonEvidenceResult(
                bytes=10,
                content={"total": {"lines": {"pct": coverage.measured * 100}}},
                path="coverage/coverage-summary.json",
                sha256="0" * 64,
                truncated=False,
            ),
        )
    return ScorerContext(
        workspace=workspace,
        scenario_dir=workspace,
        scenario=resolved_scenario,
        execution=SimpleNamespace(),
        resource_efficiency=SimpleNamespace(),
        execution_validity=SimpleNamespace(),
        verification_evidence=VerificationEvidence(
            actions=actions,
            aliases={action_id: action_id for action_id in actions},
        ),
    )


def _command_action(
    action_id: str,
    exit_code: int,
    *,
    stdout: str = "ok",
    stderr: str = "",
) -> EvidenceActionResult:
    return EvidenceActionResult(
        id=action_id,
        kind="command",
        required=action_id.endswith(("typecheck", "lint", "build")),
        status="ok",
        duration_sec=0.1,
        result=CommandEvidenceResult(
            command=["bun", "run", action_id.rsplit(".", 1)[-1]],
            cwd=".",
            duration_sec=0.1,
            exit_code=exit_code,
            stderr=stderr,
            stdout=stdout,
            timed_out=False,
        ),
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
                ),
                GateEvent(
                    timestamp="2026-01-01T00:00:01Z",
                    gate_name="coverage",
                    command="bun run test:coverage",
                    exit_code=0,
                    stdout="All files | 90 | 88 | 85 | 84",
                    stderr="",
                ),
            ],
        )
    )
    scores = {score.metric_id: score for score in evidence.metric_scores}

    assert scores["functional"].passed
    assert scores["code-quality"].passed
    assert scores["test-coverage"].score == 1.0
    assert scores["artifact-checks"].passed


def test_typescript_code_task_reports_missing_runner_evidence(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (tmp_path / "package.json").write_text('{"scripts":{}}', encoding="utf-8")
    (src / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")

    evidence = TypeScriptCodeTask().collect_evidence(_context(tmp_path))
    scores = {score.metric_id: score for score in evidence.metric_scores}

    assert not scores["functional"].passed
    assert "required_commands=0/0" in str(scores["functional"].evidence)
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
