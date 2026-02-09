"""Tests for runner qualification and optimization metric helpers."""

import json
from datetime import UTC, datetime
from pathlib import Path

from agentic_eval.audit.scaffold_manifest import generate_manifest
from agentic_eval.harness.config import Agent, HarnessConfig, ModelTarget
from agentic_eval.runner import (
    EvaluationOutputs,
    ExecutionPhaseResult,
    HarborExecutionResult,
    PersistedArtifacts,
    RunLayout,
    RunRequest,
    ScaffoldContext,
    ScorecardBuildContext,
    _classify_void_reasons,
    _load_verifier_outputs,
    build_scorecard,
    collect_process_metrics,
    create_harbor_task_bundle,
    evaluate_coverage,
    evaluate_requirements,
)
from agentic_eval.scaffold.catalog import ScaffoldSource
from agentic_eval.schemas.events import GateEvent
from agentic_eval.schemas.scorecard import (
    ComplianceScore,
    CoverageScore,
    EfficiencyScore,
    FunctionalScore,
    QualificationScore,
    RequirementCoverageScore,
    ScaffoldAudit,
)
from agentic_eval.schemas.task import DeterministicCheck, RequirementSpec, TaskDefinition


def _sample_task() -> TaskDefinition:
    return TaskDefinition.model_validate(
        {
            "name": "homepage-implementation",
            "description": "test task",
            "difficulty": "medium",
            "category": "greenfield-ui",
            "timeout_sec": 1800,
            "scaffold": {
                "template": "next-shadcn-starter",
                "version": "v2025.01",
                "rules_variant": "strict",
            },
            "verification": {
                "gates": [
                    {
                        "name": "typecheck",
                        "command": ["bun", "run", "typecheck"],
                        "on_failure": "continue",
                    },
                    {
                        "name": "lint",
                        "command": ["bun", "run", "lint"],
                        "on_failure": "continue",
                    },
                ],
                "required_commands": [
                    ["bun", "run", "build"],
                ],
                "coverage_threshold": 0.8,
                "min_quality_score": 0.9,
            },
            "compliance": {},
            "prompt": "Build homepage",
        }
    )


def _sample_harness_config() -> HarnessConfig:
    return HarnessConfig(
        agent=Agent.CODEX_CLI,
        model=ModelTarget(provider="openai", name="gpt-5"),
        rules_variant="strict",
        timeout_sec=1800,
    )


def _sample_evaluation_outputs(scaffold_audit: ScaffoldAudit | None = None) -> EvaluationOutputs:
    return EvaluationOutputs(
        functional=FunctionalScore(
            passed=True,
            tests_passed=2,
            tests_total=2,
            build_succeeded=True,
            gates_passed=2,
            gates_total=2,
        ),
        compliance=ComplianceScore(),
        visual=None,
        efficiency=EfficiencyScore(
            total_gate_failures=0,
            unique_failure_categories=0,
            repeat_failures=0,
        ),
        coverage=CoverageScore(
            threshold=0.8,
            measured=0.9,
            source="coverage-summary",
            passed=True,
        ),
        requirements=RequirementCoverageScore(
            total_requirements=1,
            satisfied_requirements=1,
            mapped_requirements=1,
        ),
        qualification=QualificationScore(),
        scaffold_audit=scaffold_audit,
        gate_history=[],
    )


def _sample_scorecard_context(
    tmp_path: Path,
    *,
    terminated_early: bool,
    termination_reason: str | None,
    scaffold_audit: ScaffoldAudit | None = None,
) -> ScorecardBuildContext:
    task_dir = tmp_path / "task"
    workspace_dir = tmp_path / "workspace"
    results_dir = tmp_path / "results"
    task_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "task.yaml").write_text("name: sample-task\n")

    scaffold_manifest = generate_manifest(
        workspace_dir,
        template_name="next-shadcn-starter",
        template_version="v2025.01",
    )
    scaffold_source = ScaffoldSource(
        template="next-shadcn-starter",
        version="v2025.01",
        path=workspace_dir,
        manifest=scaffold_manifest,
    )

    request = RunRequest(
        task=_sample_task(),
        config=_sample_harness_config(),
        scaffold_root=tmp_path / "scaffolds",
        task_dir=task_dir,
        workspace_dir=workspace_dir,
        results_dir=results_dir,
    )
    context = ScaffoldContext(
        scaffold_source=scaffold_source,
        workspace=workspace_dir,
        injected_rules=None,
        manifest_path=workspace_dir / "scaffold.manifest.json",
        baseline_manifest_path=workspace_dir / ".baseline-scaffold.json",
        metadata_path=workspace_dir / ".scaffold-meta.json",
    )
    layout = RunLayout(
        run_id="run-1234",
        start_time=datetime.now(UTC),
        instance_name="20260209-000000Z__run-1234__sample",
        root_dir=results_dir / "runs" / "run-1234",
        summary_dir=results_dir / "runs" / "run-1234" / "summary",
        scaffold_dir=results_dir / "runs" / "run-1234" / "scaffold",
        verifier_dir=results_dir / "runs" / "run-1234" / "verifier",
        agent_dir=results_dir / "runs" / "run-1234" / "agent",
        harbor_dir=results_dir / "runs" / "run-1234" / "harbor",
        result_json_path=results_dir / "runs" / "run-1234" / "summary" / "result.json",
        summary_readme_path=results_dir / "runs" / "run-1234" / "summary" / "README.md",
        jobs_root=tmp_path / "jobs",
    )
    execution = ExecutionPhaseResult(
        harbor_result=HarborExecutionResult(
            terminated_early=terminated_early,
            termination_reason=termination_reason,
            job_dir=tmp_path / "jobs" / "orchestrator-run-1234",
            trial_dir=None,
        ),
        terminated_early=terminated_early,
        termination_reason=termination_reason,
        process_metrics=collect_process_metrics(_sample_task(), None),
        events=[],
        outputs=_sample_evaluation_outputs(scaffold_audit=scaffold_audit),
        duration_sec=12.5,
    )
    artifacts = PersistedArtifacts(
        scaffold_meta={"template": "next-shadcn-starter"},
        task_version_meta={"task_yaml_sha256": "abc"},
        verifier_artifacts={"scorecard": "verifier/scorecard.json"},
        agent_artifacts={"log": "agent/codex.txt"},
        harbor_artifacts={"command": "harbor/command.txt"},
    )
    return ScorecardBuildContext(
        request=request,
        layout=layout,
        context=context,
        artifacts=artifacts,
        execution=execution,
    )


def test_collect_process_metrics_extracts_usage_and_failures(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    codex_log = agent_dir / "codex.txt"
    entries = [
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "/bin/bash -lc 'bun run typecheck'",
                "exit_code": 0,
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "/bin/bash -lc 'bun run build'",
                "exit_code": 1,
                "status": "failed",
            },
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 1000,
                "cached_input_tokens": 250,
                "output_tokens": 100,
            },
        },
    ]
    codex_log.write_text("\n".join(json.dumps(entry) for entry in entries))

    metrics = collect_process_metrics(_sample_task(), trial_dir)

    assert metrics.uncached_input_tokens == 750
    assert metrics.output_tokens == 100
    assert metrics.command_count == 2
    assert metrics.failed_command_count == 1
    assert metrics.required_verification_commands == 3
    assert metrics.executed_required_verification_commands == 2
    assert metrics.failed_command_categories == {"build": 1}
    assert metrics.required_verification_first_pass["bun run typecheck"] == "pass"
    assert metrics.required_verification_first_pass["bun run lint"] == "missing"
    assert metrics.required_verification_first_pass["bun run build"] == "fail"
    assert metrics.first_pass_verification_successes == 1
    assert metrics.first_pass_verification_failures == 1
    assert metrics.missing_required_verification_commands == 1


def test_collect_process_metrics_distinguishes_test_and_coverage(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    codex_log = agent_dir / "codex.txt"
    entries = [
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "/bin/bash -lc 'bun run test'",
                "exit_code": 0,
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "/bin/bash -lc 'bun run test:coverage'",
                "exit_code": 0,
                "status": "completed",
            },
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 10,
                "cached_input_tokens": 0,
                "output_tokens": 5,
            },
        },
    ]
    codex_log.write_text("\n".join(json.dumps(entry) for entry in entries))

    task = TaskDefinition.model_validate(
        {
            "name": "homepage-implementation",
            "description": "test task",
            "difficulty": "medium",
            "category": "greenfield-ui",
            "timeout_sec": 1800,
            "scaffold": {
                "template": "next-shadcn-starter",
                "version": "v2025.01",
                "rules_variant": "strict",
            },
            "verification": {
                "gates": [
                    {
                        "name": "test",
                        "command": ["bun", "run", "test"],
                        "on_failure": "continue",
                    },
                    {
                        "name": "coverage",
                        "command": ["bun", "run", "test:coverage"],
                        "on_failure": "continue",
                    },
                ],
                "required_commands": [],
            },
            "compliance": {},
            "prompt": "Build homepage",
        }
    )

    metrics = collect_process_metrics(task, trial_dir)

    assert metrics.required_verification_commands == 2
    assert metrics.executed_required_verification_commands == 2


def test_evaluate_coverage_reads_summary_file(tmp_path: Path):
    workspace = tmp_path / "workspace"
    coverage_dir = workspace / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)
    (coverage_dir / "coverage-summary.json").write_text(
        json.dumps(
            {
                "total": {
                    "lines": {"pct": 85},
                    "statements": {"pct": 90},
                    "functions": {"pct": 82},
                    "branches": {"pct": 80},
                }
            }
        )
    )
    score = evaluate_coverage(workspace, gate_history=[], threshold=0.8)
    assert score.measured == 0.8
    assert score.passed is True
    assert score.source is not None


def test_evaluate_coverage_parses_gate_output_when_summary_missing(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    gate_history = [
        GateEvent(
            timestamp="2026-01-01T00:00:00Z",
            gate_name="coverage",
            command="bun run test:coverage",
            exit_code=0,
            stdout="All files | 91.0 | 88.0 | 84.0 | 83.0 |",
            stderr="",
            failure_category=None,
            is_repeat=False,
        )
    ]
    score = evaluate_coverage(workspace, gate_history=gate_history, threshold=0.84)
    assert score.measured == 0.83
    assert score.passed is False
    assert score.source == "gate:coverage"


def test_evaluate_requirements_flags_requirement_gaps(tmp_path: Path):
    workspace = tmp_path / "workspace"
    src_app = workspace / "src" / "app"
    src_app.mkdir(parents=True, exist_ok=True)
    (src_app / "page.tsx").write_text(
        "export default function Home(){ return <h1>Get Started</h1>; }"
    )
    (src_app / "page.test.tsx").write_text("it('renders CTA', () => expect(true).toBe(true))")

    requirements = [
        RequirementSpec(
            id="req-cta",
            description="CTA exists",
            check=DeterministicCheck(
                type="import_present",
                pattern="Get Started",
                description="CTA string exists",
            ),
            required_test_patterns=["CTA", "Get Started"],
        )
    ]

    result = evaluate_requirements(workspace, requirements)
    assert result.total_requirements == 1
    assert result.satisfied_requirements == 1
    assert result.mapped_requirements == 0
    assert result.requirement_gap_ids == ["req-cta"]
    assert result.requirement_pattern_gaps == {"req-cta": ["Get Started"]}


def test_load_verifier_outputs_parses_scorecard(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    verifier_dir = trial_dir / "verifier"
    verifier_dir.mkdir(parents=True, exist_ok=True)
    scorecard_path = verifier_dir / "scorecard.json"
    scorecard_path.write_text(
        json.dumps(
            {
                "functional": {
                    "passed": True,
                    "tests_passed": 4,
                    "tests_total": 4,
                    "build_succeeded": True,
                    "gates_passed": 4,
                    "gates_total": 4,
                },
                "compliance": {
                    "checks": [
                        {
                            "rule": "Placeholder removed",
                            "type": "deterministic",
                            "passed": True,
                            "evidence": "ok",
                        }
                    ]
                },
                "visual": {
                    "similarity": 0.97,
                    "diff_path": None,
                    "capture_succeeded": True,
                    "threshold": 0.95,
                },
                "efficiency": {
                    "total_gate_failures": 0,
                    "unique_failure_categories": 0,
                    "repeat_failures": 0,
                },
                "coverage": {
                    "threshold": 0.8,
                    "measured": 0.9,
                    "source": "coverage-summary",
                    "passed": True,
                },
                "requirements": {
                    "total_requirements": 1,
                    "satisfied_requirements": 1,
                    "mapped_requirements": 1,
                    "missing_requirement_ids": [],
                    "requirement_gap_ids": [],
                },
                "qualification": {
                    "checks": [
                        {
                            "name": "run_completed",
                            "passed": True,
                            "evidence": "done",
                        }
                    ]
                },
                "gate_history": [
                    {
                        "timestamp": "2026-01-01T00:00:00Z",
                        "gate_name": "typecheck",
                        "command": "bun run typecheck",
                        "exit_code": 0,
                        "stdout": "",
                        "stderr": "",
                        "failure_category": None,
                        "is_repeat": False,
                    }
                ],
                "scaffold_audit": {
                    "manifest_version": "1.0.0",
                    "template": "next-shadcn-starter",
                    "template_version": "v2025.01",
                    "manifest_fingerprint": "abc",
                    "file_count": 10,
                    "dependency_count": 8,
                    "changes_from_baseline": ["Modified: src/app/page.tsx"],
                },
            }
        )
    )

    outputs, reason = _load_verifier_outputs(trial_dir)

    assert reason is None
    assert outputs is not None
    assert outputs.functional.passed is True
    assert outputs.visual is not None
    assert outputs.visual.threshold_met is True
    assert len(outputs.gate_history) == 1
    assert outputs.scaffold_audit is not None
    assert outputs.scaffold_audit.template == "next-shadcn-starter"


def test_load_verifier_outputs_missing_scorecard(tmp_path: Path):
    outputs, reason = _load_verifier_outputs(tmp_path / "missing")
    assert outputs is None
    assert reason is not None


def test_classify_void_reasons_rate_limit():
    reasons = _classify_void_reasons(
        terminated_early=True,
        termination_reason="Codex turn failed: Rate limit reached for gpt-5.2-codex.",
    )
    assert "provider_rate_limit" in reasons


def test_classify_void_reasons_timeout():
    reasons = _classify_void_reasons(
        terminated_early=True,
        termination_reason="Timeout expired after 420s before trial result.json was written.",
    )
    assert reasons == ["harbor_timeout"]


def test_classify_void_reasons_empty_when_not_terminated():
    reasons = _classify_void_reasons(
        terminated_early=False,
        termination_reason=None,
    )
    assert reasons == []


def test_build_scorecard_marks_rate_limited_run_void(tmp_path: Path):
    context = _sample_scorecard_context(
        tmp_path,
        terminated_early=True,
        termination_reason="Codex turn failed: Rate limit reached for provider/model",
    )

    scorecard = build_scorecard(context)

    assert scorecard.voided is True
    assert "provider_rate_limit" in scorecard.void_reasons
    assert scorecard.metadata["run"]["repeat_required"] is True
    assert scorecard.metadata["run"]["repeat_required_reasons"] == scorecard.void_reasons


def test_build_scorecard_populates_missing_scaffold_audit_fields(tmp_path: Path):
    context = _sample_scorecard_context(
        tmp_path,
        terminated_early=False,
        termination_reason=None,
        scaffold_audit=ScaffoldAudit(
            template=None,
            template_version=None,
            manifest_fingerprint=None,
            file_count=3,
            dependency_count=2,
            changes_from_baseline=[],
        ),
    )

    scorecard = build_scorecard(context)

    assert scorecard.scaffold_audit is not None
    assert scorecard.scaffold_audit.template == "next-shadcn-starter"
    assert scorecard.scaffold_audit.template_version == "v2025.01"
    assert scorecard.scaffold_audit.manifest_fingerprint == (
        context.context.scaffold_source.manifest.fingerprint
    )


def test_create_harbor_task_bundle_copies_relative_visual_reference(tmp_path: Path):
    workspace = tmp_path / "workspace"
    task_dir = tmp_path / "task"
    results_dir = tmp_path / "results"
    workspace.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    (workspace / "package.json").write_text("{}")
    (workspace / "bun.lock").write_text("")
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "index.tsx").write_text("export const App = () => null;\n")

    reference_rel = Path("references/hero.png")
    source_reference = task_dir / reference_rel
    source_reference.parent.mkdir(parents=True, exist_ok=True)
    source_reference.write_bytes(b"png-binary")

    task = TaskDefinition.model_validate(
        {
            "name": "homepage-implementation",
            "description": "test task",
            "difficulty": "medium",
            "category": "greenfield-ui",
            "timeout_sec": 1800,
            "scaffold": {
                "template": "next-shadcn-starter",
                "version": "v2025.01",
                "rules_variant": "strict",
            },
            "verification": {"gates": [], "required_commands": []},
            "visual": {
                "reference_image": str(reference_rel),
                "screenshot_command": ["bun", "run", "capture-screenshot"],
                "threshold": 0.95,
            },
            "compliance": {},
            "prompt": "Build homepage",
        }
    )
    request = RunRequest(
        task=task,
        config=_sample_harness_config(),
        scaffold_root=tmp_path / "scaffolds",
        task_dir=task_dir,
        workspace_dir=workspace,
        results_dir=results_dir,
    )
    scaffold_source = ScaffoldSource(
        template="next-shadcn-starter",
        version="v2025.01",
        path=workspace,
        manifest=generate_manifest(
            workspace, template_name="next-shadcn-starter", template_version="v2025.01"
        ),
    )
    context = ScaffoldContext(
        scaffold_source=scaffold_source,
        workspace=workspace,
        injected_rules=None,
        manifest_path=workspace / "scaffold.manifest.json",
        baseline_manifest_path=workspace / ".baseline-scaffold.json",
        metadata_path=workspace / ".scaffold-meta.json",
    )

    bundle = create_harbor_task_bundle(request, context, run_id="run1234")
    copied_reference = bundle / "environment" / "app" / reference_rel

    assert copied_reference.exists()
    assert copied_reference.read_bytes() == b"png-binary"
    assert (
        (bundle / "tests" / "score-task.mjs")
        .read_text(encoding="utf-8")
        .startswith("#!/usr/bin/env bun")
    )
