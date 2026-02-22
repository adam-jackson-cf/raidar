"""Tests for runner validity and optimization metric helpers."""

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

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
    _ensure_suite_baseline_workspace,
    _load_verifier_outputs,
    _prune_workspace_artifacts,
    _resolve_homepage_screenshot_command,
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
    PerformanceGatesScore,
    RequirementCoverageScore,
    RunValidityScore,
    ScaffoldAudit,
)
from agentic_eval.schemas.task import DeterministicCheck, RequirementSpec, TaskDefinition


def _sample_task() -> TaskDefinition:
    return TaskDefinition.model_validate(
        {
            "name": "homepage-implementation",
            "version": "v001",
            "description": "test task",
            "difficulty": "medium",
            "category": "greenfield-ui",
            "timeout_sec": 1800,
            "scaffold": {
                "root": "scaffold",
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
            "prompt": {"entry": "prompt/task.md"},
        }
    )


def _sample_harness_config() -> HarnessConfig:
    return HarnessConfig(
        agent=Agent.CODEX_CLI,
        model=ModelTarget(provider="openai", name="gpt-5"),
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
        run_validity=RunValidityScore(),
        performance_gates=PerformanceGatesScore(),
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
    (task_dir / "task.yaml").write_text("name: sample-task\nversion: v001\n")
    (task_dir / "prompt").mkdir(parents=True, exist_ok=True)
    (task_dir / "prompt" / "task.md").write_text("Build homepage\n")

    scaffold_manifest = generate_manifest(
        workspace_dir,
        template_name="homepage-implementation",
        template_version="v001",
    )
    scaffold_source = ScaffoldSource(
        task_name="homepage-implementation",
        task_version="v001",
        path=workspace_dir,
        manifest=scaffold_manifest,
    )

    request = RunRequest(
        task=_sample_task(),
        config=_sample_harness_config(),
        task_dir=task_dir,
        execution_dir=results_dir,
        repeat_index=1,
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
        run_label="run-01",
        root_dir=results_dir / "runs" / "run-1234",
        workspace_dir=results_dir / "runs" / "run-1234" / "workspace",
        verifier_dir=results_dir / "runs" / "run-1234" / "verifier",
        agent_dir=results_dir / "runs" / "run-1234" / "agent",
        harbor_dir=results_dir / "runs" / "run-1234" / "harbor",
        run_json_path=results_dir / "runs" / "run-1234" / "run.json",
        analysis_path=results_dir / "runs" / "run-1234" / "summary.md",
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
        process_metrics=collect_process_metrics(_sample_task(), None, harness="codex-cli"),
        events=[],
        outputs=_sample_evaluation_outputs(scaffold_audit=scaffold_audit),
        duration_sec=12.5,
    )
    artifacts = PersistedArtifacts(
        scaffold_meta={"task": "homepage-implementation", "task_version": "v001"},
        task_version_meta={"task_yaml_sha256": "abc"},
        verifier_artifacts={"scorecard": "verifier/scorecard.json"},
        agent_artifacts={"log": "agent/codex.txt"},
        harbor_artifacts={"command": "harbor/command.txt"},
        evidence_artifacts={"homepage_pre": None, "homepage_post": None, "errors": []},
        workspace_prune={"removed": [], "reclaimed_bytes": 0},
    )
    return ScorecardBuildContext(
        request=request,
        layout=layout,
        context=context,
        artifacts=artifacts,
        execution=execution,
    )


def test_ensure_suite_baseline_workspace_initializes_once_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_dir = tmp_path / "task" / "v001"
    scaffold_dir = task_dir / "scaffold"
    scaffold_dir.mkdir(parents=True, exist_ok=True)
    suite_baseline_dir = tmp_path / "executions" / "suite-01" / "workspace" / "baseline"
    call_count = 0
    call_lock = threading.Lock()
    start_barrier = threading.Barrier(3)

    def fake_prepare_workspace(
        scaffold_dir: Path, target_dir: Path, task_dir: Path, agent: str
    ) -> tuple[Path, Path | None]:
        del scaffold_dir, task_dir, agent
        nonlocal call_count
        with call_lock:
            call_count += 1
        time.sleep(0.05)
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "scaffold.manifest.json").write_text("{}\n", encoding="utf-8")
        return target_dir, None

    monkeypatch.setattr("agentic_eval.runner.prepare_workspace", fake_prepare_workspace)

    failures: list[Exception] = []

    def _run() -> None:
        try:
            start_barrier.wait(timeout=1.0)
            _ensure_suite_baseline_workspace(
                scaffold_dir=scaffold_dir,
                suite_baseline_dir=suite_baseline_dir,
                task_dir=task_dir,
                agent="codex-cli",
            )
        except Exception as exc:  # pragma: no cover - assertion below surfaces failure
            failures.append(exc)

    threads = [threading.Thread(target=_run), threading.Thread(target=_run)]
    for thread in threads:
        thread.start()
    start_barrier.wait(timeout=1.0)
    for thread in threads:
        thread.join()

    assert not failures
    assert call_count == 1


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

    metrics = collect_process_metrics(_sample_task(), trial_dir, harness="codex-cli")

    assert metrics.uncached_input_tokens == 750
    assert metrics.output_tokens == 100
    assert metrics.command_count == 2
    assert metrics.failed_command_count == 1
    assert metrics.process_failed_command_count == 0
    assert metrics.required_verification_commands == 3
    assert metrics.executed_required_verification_commands == 2
    assert metrics.failed_command_categories == {}
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
            "version": "v001",
            "description": "test task",
            "difficulty": "medium",
            "category": "greenfield-ui",
            "timeout_sec": 1800,
            "scaffold": {
                "root": "scaffold",
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
            "prompt": {"entry": "prompt/task.md"},
        }
    )

    metrics = collect_process_metrics(task, trial_dir, harness="codex-cli")

    assert metrics.required_verification_commands == 2
    assert metrics.executed_required_verification_commands == 2


def test_collect_process_metrics_extracts_gemini_commands_from_agent_stdout(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "gemini-cli.trajectory.json").write_text(
        json.dumps({"messages": [{"tokens": {"input": 20, "cached": 5, "output": 3}}]})
    )
    command_dir = trial_dir / "agent" / "command-0"
    command_dir.mkdir(parents=True, exist_ok=True)
    (command_dir / "stdout.txt").write_text(
        "\n".join(
            [
                "I will run the type-checking command to ensure there are no TypeScript errors.",
                "I have completed the smoke-task implementation. I updated `src/app/page.tsx` "
                "with the text `Harbor smoke test ready`, and verified by running the project's "
                "type-checking, linting, and build commands, all of which passed.",
            ]
        )
    )

    metrics = collect_process_metrics(_sample_task(), trial_dir, harness="gemini")

    assert metrics.command_count == 3
    assert metrics.failed_command_count == 0
    assert metrics.required_verification_commands == 3
    assert metrics.executed_required_verification_commands == 3
    assert metrics.required_verification_first_pass["bun run typecheck"] == "pass"
    assert metrics.required_verification_first_pass["bun run lint"] == "pass"
    assert metrics.required_verification_first_pass["bun run build"] == "pass"


def test_collect_process_metrics_extracts_gemini_trajectory_shell_commands(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "gemini-cli.trajectory.json").write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "tokens": {"input": 30, "cached": 10, "output": 4},
                        "toolCalls": [
                            {
                                "name": "run_shell_command",
                                "status": "success",
                                "args": {"command": "bun run typecheck && bun run lint"},
                            }
                        ],
                    }
                ]
            }
        )
    )

    metrics = collect_process_metrics(_sample_task(), trial_dir, harness="gemini")

    assert metrics.command_count == 2
    assert metrics.required_verification_commands == 3
    assert metrics.executed_required_verification_commands == 2
    assert metrics.required_verification_first_pass["bun run typecheck"] == "pass"
    assert metrics.required_verification_first_pass["bun run lint"] == "pass"
    assert metrics.required_verification_first_pass["bun run build"] == "missing"


def test_collect_process_metrics_extracts_verify_with_phrasing(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "gemini-cli.trajectory.json").write_text(
        json.dumps({"messages": [{"tokens": {"input": 10, "cached": 2, "output": 1}}]})
    )
    command_dir = trial_dir / "agent" / "command-0"
    command_dir.mkdir(parents=True, exist_ok=True)
    (command_dir / "stdout.txt").write_text(
        "I have updated `src/app/page.tsx` with the requested text and verified "
        "the implementation with a successful build and typecheck."
    )

    metrics = collect_process_metrics(_sample_task(), trial_dir, harness="gemini")

    assert metrics.command_count == 2
    assert metrics.required_verification_commands == 3
    assert metrics.executed_required_verification_commands == 2
    assert metrics.required_verification_first_pass["bun run typecheck"] == "pass"
    assert metrics.required_verification_first_pass["bun run lint"] == "missing"
    assert metrics.required_verification_first_pass["bun run build"] == "pass"


def test_collect_process_metrics_extracts_claude_structured_bash_commands(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    command_dir = trial_dir / "agent" / "command-1"
    command_dir.mkdir(parents=True, exist_ok=True)
    (command_dir / "stdout.txt").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "id": "msg_1",
                            "usage": {
                                "input_tokens": 70,
                                "cache_read_input_tokens": 20,
                                "output_tokens": 9,
                            },
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_typecheck",
                                    "name": "Bash",
                                    "input": {"command": "bunx tsc --noEmit"},
                                },
                                {
                                    "type": "tool_use",
                                    "id": "toolu_lint",
                                    "name": "Bash",
                                    "input": {"command": "npm run lint"},
                                },
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_typecheck",
                                    "is_error": False,
                                },
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_lint",
                                    "is_error": False,
                                },
                            ]
                        },
                    }
                ),
            ]
        )
    )

    metrics = collect_process_metrics(_sample_task(), trial_dir, harness="claude-code")

    assert metrics.command_count == 2
    assert metrics.failed_command_count == 0
    assert metrics.required_verification_commands == 3
    assert metrics.executed_required_verification_commands == 2
    assert metrics.required_verification_first_pass["bun run typecheck"] == "pass"
    assert metrics.required_verification_first_pass["bun run lint"] == "pass"
    assert metrics.required_verification_first_pass["bun run build"] == "missing"


def test_collect_process_metrics_extracts_claude_bash_from_top_level_log(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "claude-code.txt").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "id": "msg_1",
                            "usage": {
                                "input_tokens": 50,
                                "cache_read_input_tokens": 0,
                                "output_tokens": 7,
                            },
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_typecheck",
                                    "name": "Bash",
                                    "input": {"command": "bun run typecheck"},
                                },
                                {
                                    "type": "tool_use",
                                    "id": "toolu_lint",
                                    "name": "Bash",
                                    "input": {"command": "bun run lint"},
                                },
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_typecheck",
                                    "is_error": False,
                                },
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_lint",
                                    "is_error": False,
                                },
                            ]
                        },
                    }
                ),
            ]
        )
    )

    metrics = collect_process_metrics(_sample_task(), trial_dir, harness="claude-code")

    assert metrics.command_count == 2
    assert metrics.required_verification_commands == 3
    assert metrics.executed_required_verification_commands == 2
    assert metrics.required_verification_first_pass["bun run typecheck"] == "pass"
    assert metrics.required_verification_first_pass["bun run lint"] == "pass"
    assert metrics.required_verification_first_pass["bun run build"] == "missing"


def test_collect_process_metrics_extracts_claude_result_usage(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "claude-code.txt").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "result",
                        "usage": {
                            "input_tokens": 900,
                            "cache_read_input_tokens": 300,
                            "output_tokens": 111,
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "id": "msg_1",
                            "usage": {
                                "input_tokens": 9,
                                "cache_read_input_tokens": 3,
                                "output_tokens": 1,
                            },
                            "content": [],
                        },
                    }
                ),
            ]
        )
    )

    metrics = collect_process_metrics(_sample_task(), trial_dir, harness="claude-code")

    assert metrics.uncached_input_tokens == 600
    assert metrics.output_tokens == 111


def test_collect_process_metrics_extracts_gemini_usage_from_trajectory(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "gemini-cli.trajectory.json").write_text(
        json.dumps(
            {
                "messages": [
                    {"tokens": {"input": 100, "cached": 20, "output": 10}},
                    {"tokens": {"input": 120, "cached": 30, "output": 12}},
                ]
            }
        )
    )
    (agent_dir / "gemini-cli.txt").write_text("$ bun run typecheck\n")

    metrics = collect_process_metrics(_sample_task(), trial_dir, harness="gemini")

    assert metrics.uncached_input_tokens == 170
    assert metrics.output_tokens == 22


def test_collect_process_metrics_raises_when_usage_missing(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "gemini-cli.trajectory.json").write_text(json.dumps({"messages": []}))

    with pytest.raises(RuntimeError, match="Missing token usage metrics"):
        collect_process_metrics(_sample_task(), trial_dir, harness="gemini")


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
    assert result.mapped_satisfied_requirements == 0
    assert result.requirement_gap_ids == ["req-cta"]
    assert result.requirement_pattern_gaps == {"req-cta": ["Get Started"]}


def test_evaluate_requirements_matches_patterns_case_insensitively(tmp_path: Path):
    workspace = tmp_path / "workspace"
    src_app = workspace / "src" / "app"
    src_app.mkdir(parents=True, exist_ok=True)
    (src_app / "page.tsx").write_text(
        "export default function Home(){ return <h1>Get Started</h1>; }"
    )
    (src_app / "page.test.tsx").write_text(
        "it('renders nav', () => { expect('nav-link-about').toBeTruthy();"
        " expect('nav-link-contact').toBeTruthy(); })"
    )

    requirements = [
        RequirementSpec(
            id="req-header-nav",
            description="Header nav links exist",
            check=DeterministicCheck(
                type="import_present",
                pattern="Get Started",
                description="Placeholder deterministic check",
            ),
            required_test_patterns=["About", "Contact"],
        )
    ]

    result = evaluate_requirements(workspace, requirements)
    assert result.total_requirements == 1
    assert result.satisfied_requirements == 1
    assert result.mapped_requirements == 1
    assert result.mapped_satisfied_requirements == 1
    assert result.requirement_gap_ids == []
    assert result.requirement_pattern_gaps == {}


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
                "run_validity": {
                    "checks": [
                        {
                            "name": "run_completed",
                            "passed": True,
                            "evidence": "done",
                        }
                    ]
                },
                "performance_gates": {
                    "checks": [
                        {
                            "name": "quality_gates_passed",
                            "passed": True,
                            "evidence": "2/2 gates passed",
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
                    "template": "homepage-implementation",
                    "template_version": "v001",
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
    assert outputs.scaffold_audit.template == "homepage-implementation"


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


def test_classify_void_reasons_compose_version_unsupported():
    reasons = _classify_void_reasons(
        terminated_early=True,
        termination_reason=(
            "Unsupported docker compose version 2.39.2. Require >= 2.40.1 for Harbor runs."
        ),
    )
    assert reasons == ["compose_version_unsupported"]


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
    assert scorecard.scaffold_audit.template == "homepage-implementation"
    assert scorecard.scaffold_audit.template_version == "v001"
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
            "version": "v001",
            "description": "test task",
            "difficulty": "medium",
            "category": "greenfield-ui",
            "timeout_sec": 1800,
            "scaffold": {
                "root": "scaffold",
            },
            "verification": {"gates": [], "required_commands": []},
            "visual": {
                "reference_image": str(reference_rel),
                "screenshot_command": ["bun", "run", "capture-screenshot"],
                "threshold": 0.95,
            },
            "compliance": {},
            "prompt": {"entry": "prompt/task.md"},
        }
    )
    (task_dir / "prompt").mkdir(parents=True, exist_ok=True)
    (task_dir / "prompt" / "task.md").write_text("Build homepage\n")
    request = RunRequest(
        task=task,
        config=_sample_harness_config(),
        task_dir=task_dir,
        execution_dir=results_dir,
        repeat_index=1,
    )
    scaffold_source = ScaffoldSource(
        task_name="homepage-implementation",
        task_version="v001",
        path=workspace,
        manifest=generate_manifest(
            workspace,
            template_name="homepage-implementation",
            template_version="v001",
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

    bundle = create_harbor_task_bundle(
        request,
        context,
        bundle_root=results_dir / "runs" / "run-01" / "harbor" / "bundle",
    )
    copied_reference = bundle / "environment" / "app" / reference_rel

    assert copied_reference.exists()
    assert copied_reference.read_bytes() == b"png-binary"
    assert (
        (bundle / "tests" / "score-task.mjs")
        .read_text(encoding="utf-8")
        .startswith("#!/usr/bin/env bun")
    )
    score_script = (bundle / "tests" / "score-task.mjs").read_text(encoding="utf-8")
    assert r"const testPattern = /\.(test|spec)\.tsx?$/" in score_script
    assert r"/(\d+)\s+passed/gi" in score_script
    assert r"/(\d+)\s+failed/gi" in score_script
    assert r"/([0-9]+(?:\.[0-9]+)?)\s*%/" in score_script
    assert 'new RegExp(pattern, "mi").test(content)' in score_script


def test_create_harbor_task_bundle_fast_mode_sets_image_and_cli_install(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("HARBOR_SMOKE_FAST", "1")
    monkeypatch.setenv("HARBOR_SMOKE_FAST_REUSE_IMAGE", "1")

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
    (task_dir / "task.yaml").write_text("name: hello-world-smoke\nversion: v001\n")
    (task_dir / "prompt").mkdir(parents=True, exist_ok=True)
    (task_dir / "prompt" / "task.md").write_text("Print hello world\n")

    task = TaskDefinition.model_validate(
        {
            "name": "hello-world-smoke",
            "version": "v001",
            "description": "test task",
            "difficulty": "easy",
            "category": "greenfield-ui",
            "timeout_sec": 1800,
            "scaffold": {
                "root": "scaffold",
            },
            "verification": {"gates": [], "required_commands": []},
            "compliance": {},
            "prompt": {"entry": "prompt/task.md"},
        }
    )
    request = RunRequest(
        task=task,
        config=_sample_harness_config(),
        task_dir=task_dir,
        execution_dir=results_dir,
        repeat_index=1,
    )
    scaffold_source = ScaffoldSource(
        task_name="hello-world-smoke",
        task_version="v001",
        path=workspace,
        manifest=generate_manifest(
            workspace,
            template_name="hello-world-smoke",
            template_version="v001",
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

    bundle = create_harbor_task_bundle(
        request,
        context,
        bundle_root=results_dir / "runs" / "run-01" / "harbor" / "bundle",
    )
    task_toml = (bundle / "task.toml").read_text()
    dockerfile = (bundle / "environment" / "Dockerfile").read_text()

    assert 'docker_image = "ts-ui-eval-smoke-fast:hello-world-smoke-' in task_toml
    assert "@openai/codex" in dockerfile
    assert "@anthropic-ai/claude-code" not in dockerfile
    assert "@google/gemini-cli" not in dockerfile


def test_resolve_homepage_screenshot_command_uses_visual_override(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "package.json").write_text("{}\n")

    task = TaskDefinition.model_validate(
        {
            "name": "homepage-implementation",
            "version": "v001",
            "description": "task",
            "difficulty": "medium",
            "category": "greenfield-ui",
            "timeout_sec": 1800,
            "scaffold": {"root": "scaffold"},
            "verification": {"gates": [], "required_commands": []},
            "visual": {
                "reference_image": "reference/homepage.png",
                "screenshot_command": ["bun", "run", "capture-screenshot"],
                "threshold": 0.95,
            },
            "compliance": {},
            "prompt": {"entry": "prompt/task.md"},
        }
    )

    command = _resolve_homepage_screenshot_command(task, workspace)
    assert command == ["bun", "run", "capture-screenshot"]


def test_resolve_homepage_screenshot_command_uses_package_script_when_visual_missing(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "package.json").write_text(
        json.dumps({"scripts": {"capture-screenshot": "bun run scripts/capture-screenshot.ts"}})
    )

    task = TaskDefinition.model_validate(
        {
            "name": "hello-world-smoke",
            "version": "v001",
            "description": "task",
            "difficulty": "easy",
            "category": "harness-integration",
            "timeout_sec": 300,
            "scaffold": {"root": "scaffold"},
            "verification": {"gates": [], "required_commands": []},
            "compliance": {},
            "prompt": {"entry": "prompt/task.md"},
        }
    )

    command = _resolve_homepage_screenshot_command(task, workspace)
    assert command == ["bun", "run", "capture-screenshot"]


def test_prune_workspace_artifacts_removes_transient_directories(tmp_path: Path):
    workspace = tmp_path / "workspace"
    (workspace / "node_modules" / "pkg").mkdir(parents=True, exist_ok=True)
    (workspace / "node_modules" / "pkg" / "index.js").write_text("console.log('x')\n")
    (workspace / ".next").mkdir(parents=True, exist_ok=True)
    (workspace / ".next" / "trace").write_text("trace\n")
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "app.tsx").write_text("export const App = () => null;\n")

    prune = _prune_workspace_artifacts(workspace)

    assert "node_modules" in prune["removed"]
    assert ".next" in prune["removed"]
    assert prune["reclaimed_bytes"] > 0
    assert not (workspace / "node_modules").exists()
    assert not (workspace / ".next").exists()
    assert (workspace / "src" / "app.tsx").exists()
