"""Deterministic inputs that exercise registered scorers for synthetic fixtures."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from raidar.runtime.scorecard import CanonicalScorecardInput, assemble_scorecard
from raidar.runtime.scoring_outputs import collect_metric_scores_from_scorers
from raidar.schemas.scenario import ScenarioDefinition
from raidar.schemas.scorecard import (
    ExecutionValidityScore,
    GateCheck,
    PerformanceGatesScore,
    ResourceEfficiencyScore,
    Scorecard,
)
from raidar.scorers.base import JudgeExecutor, ScorerContext
from raidar.scorers.evidence_contract import (
    CommandEvidenceResult,
    EvidenceActionResult,
    JsonEvidenceResult,
)
from raidar.scorers.verification import VerificationEvidence


class SyntheticAgentSpec(Protocol):
    """AgentSpec fields required by synthetic scorecard construction."""

    harness: str
    model: str


@dataclass(frozen=True)
class SyntheticEvidence:
    """Typed synthetic scorer evidence before canonical scorecard assembly."""

    default_score: float
    metric_overrides: dict[str, float] | None = None
    matched_counts: dict[str, int] | None = None
    missing_patterns: dict[str, list[str]] | None = None
    evidence: dict[str, str] | None = None
    judge_outputs: dict[str, dict[str, object]] | None = None


def build_synthetic_scorecard(
    run_id: str,
    spec: SyntheticAgentSpec,
    scenario: ScenarioDefinition,
    *,
    duration: float,
    evidence: SyntheticEvidence,
    process: dict[str, Any],
    unscored: bool = False,
    unscored_reasons: list[str] | None = None,
) -> Scorecard:
    """Collect synthetic inputs through real scorers and assemble a scorecard."""

    resource_efficiency = ResourceEfficiencyScore(
        uncached_input_tokens=process.get("uncached_input_tokens", 0),
        output_tokens=process.get("output_tokens", 0),
        command_count=process.get("command_count", 4),
        failed_command_count=process.get("failed_command_count", 0),
        verification_rounds=process.get("verification_rounds", 1),
        repeated_verification_failures=process.get("repeated_verification_failures", 0),
    )
    execution_validity = ExecutionValidityScore(
        checks=[
            GateCheck(
                name="run_completed",
                passed=not unscored,
                evidence="typed synthetic process input",
            )
        ]
    )
    with _synthetic_scorer_context(
        scenario=scenario,
        evidence=evidence,
        process=process,
        resource_efficiency=resource_efficiency,
        execution_validity=execution_validity,
    ) as scorer_context:
        metric_scores = collect_metric_scores_from_scorers(scenario, scorer_context)
    return assemble_scorecard(
        CanonicalScorecardInput(
            scenario=scenario,
            run_id=run_id,
            harness=spec.harness,
            model=spec.model,
            duration_sec=duration,
            terminated_early=unscored,
            termination_reason="synthetic runtime termination" if unscored else None,
            unscored=unscored,
            unscored_reasons=unscored_reasons or [],
            execution_validity=execution_validity,
            performance_gates=PerformanceGatesScore(),
            resource_efficiency=resource_efficiency,
            metric_scores=metric_scores,
            metadata={
                "synthetic": True,
                "run": {"canonical_run_dir": None, "run_json_path": None},
                "process": process,
            },
        )
    )


@contextmanager
def _synthetic_scorer_context(
    *,
    scenario: ScenarioDefinition,
    evidence: SyntheticEvidence,
    process: dict[str, Any],
    resource_efficiency: ResourceEfficiencyScore,
    execution_validity: ExecutionValidityScore,
) -> Iterator[ScorerContext]:
    """Provide deterministic raw inputs to registered scorer implementations."""

    with TemporaryDirectory(prefix="raidar-synthetic-") as temporary_directory:
        workspace = Path(temporary_directory)
        _write_synthetic_workspace(workspace, scenario, evidence)
        yield ScorerContext(
            workspace=workspace,
            scenario_dir=workspace,
            scenario=scenario,
            execution=process,
            resource_efficiency=resource_efficiency,
            execution_validity=execution_validity,
            verification_evidence=_synthetic_verification_evidence(evidence),
            workspace_changes={
                "changed_files": ["src/lib/math.ts", "src/test/regression.test.ts"],
                "changed_file_count": 2,
                "error": None,
            },
            retained_evidence=_synthetic_retained_evidence(evidence),
            judge_executor=_synthetic_judge_executor(evidence),
        )


def _write_synthetic_workspace(
    workspace: Path,
    scenario: ScenarioDefinition,
    evidence: SyntheticEvidence,
) -> None:
    requirement_count = len(scenario.requirements.items)
    desired_requirements = (evidence.matched_counts or {}).get(
        "requirements-coverage",
        round(_synthetic_metric_score(evidence, "requirements-coverage") * requirement_count),
    )
    for index in range(1, min(requirement_count, desired_requirements) + 1):
        path = workspace / "synthetic" / f"{index}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"synthetic requirement {index}\n", encoding="utf-8")

    source = workspace / "src" / "lib" / "math.ts"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "export const sumEven = (values: number[]) => "
        "values.filter((value) => value % 2 === 0).reduce((sum, value) => sum + value, 0);\n",
        encoding="utf-8",
    )
    test_name = (
        "math.test.ts"
        if "regression-protection" in (evidence.missing_patterns or {})
        else "regression.test.ts"
    )
    test = workspace / "src" / "test" / test_name
    test.parent.mkdir(parents=True, exist_ok=True)
    test.write_text(
        "import { sumEven } from '../lib/math';\n"
        "test('regression: sums even values', () => expect(sumEven([1, 2, 3, 4])).toBe(6));\n",
        encoding="utf-8",
    )


def _synthetic_verification_evidence(evidence: SyntheticEvidence) -> VerificationEvidence:
    command_exit = 0 if evidence.default_score >= 0.8 else 1
    actions = {
        "bugfix.required.0": _synthetic_command_action(
            "bugfix.required.0", command_exit, required=True
        ),
        "typescript-code-task.typecheck": _synthetic_command_action(
            "typescript-code-task.typecheck", command_exit, required=False
        ),
        "typescript-code-task.lint": _synthetic_command_action(
            "typescript-code-task.lint", command_exit, required=False
        ),
        "typescript-code-task.coverage": _synthetic_command_action(
            "typescript-code-task.coverage", command_exit, required=False
        ),
        "typescript-code-task.build": _synthetic_command_action(
            "typescript-code-task.build", command_exit, required=True
        ),
        "typescript-code-task.coverage.summary": _synthetic_coverage_action(evidence),
    }
    return VerificationEvidence(
        actions=actions,
        aliases={action_id: action_id for action_id in actions},
    )


def _synthetic_command_action(
    action_id: str,
    exit_code: int,
    *,
    required: bool,
) -> EvidenceActionResult:
    return EvidenceActionResult(
        id=action_id,
        kind="command",
        required=required,
        status="ok",
        duration_sec=0.1,
        result=CommandEvidenceResult(
            command=["synthetic", action_id.rsplit(".", 1)[-1]],
            cwd=".",
            duration_sec=0.1,
            exit_code=exit_code,
            stderr="" if exit_code == 0 else "synthetic verification failure",
            stdout="ok" if exit_code == 0 else "",
            timed_out=False,
        ),
    )


def _synthetic_coverage_action(evidence: SyntheticEvidence) -> EvidenceActionResult:
    measured = _synthetic_metric_score(evidence, "test-coverage") * 100
    return EvidenceActionResult(
        id="typescript-code-task.coverage.summary",
        kind="read_json",
        required=False,
        status="ok",
        duration_sec=0.1,
        result=JsonEvidenceResult(
            bytes=1,
            content={
                "total": {
                    key: {"pct": measured}
                    for key in ("lines", "statements", "functions", "branches")
                }
            },
            path="coverage/coverage-summary.json",
            sha256="0" * 64,
            truncated=False,
        ),
    )


def _synthetic_retained_evidence(evidence: SyntheticEvidence) -> dict[str, Any]:
    completeness = _synthetic_metric_score(evidence, "defect-evidence-completeness")
    if completeness >= 0.8:
        return {
            "reproduction_note": "Synthetic defect reproduced before the fix.",
            "regression_tests": ["src/test/regression.test.ts"],
            "verification_evidence": "Synthetic verification passed.",
        }
    return {"reproduction_note": "Synthetic defect reproduction was incomplete."}


def _synthetic_judge_executor(evidence: SyntheticEvidence) -> JudgeExecutor:
    score = _synthetic_metric_score(evidence, "requirements-adherence")
    passed = score >= 0.8
    output = {
        "passed": passed,
        "score": score,
        "verdict": "satisfied" if passed else "unsatisfied",
        "evidence": (evidence.evidence or {}).get(
            "requirements-adherence",
            "Deterministic synthetic judge evidence.",
        ),
        "findings": [],
        "rubric_coverage": {"synthetic": True},
        "residual_risk": [] if passed else ["Synthetic requirements gap."],
    }

    def execute(_judge_role: str, _prompt: str) -> str:
        return json.dumps(output, sort_keys=True)

    return execute


def _synthetic_metric_score(evidence: SyntheticEvidence, metric_id: str) -> float:
    return (evidence.metric_overrides or {}).get(metric_id, evidence.default_score)
