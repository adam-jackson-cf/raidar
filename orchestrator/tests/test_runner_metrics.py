"""Tests for execution-validity and resource-efficiency helpers."""

import json
import subprocess
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import raidar.runner as runner
from raidar.agents.config import AgentSpec, Harness, ModelTarget
from raidar.audit.workspace_diff import directory_fingerprint
from raidar.runner import (
    EvaluationOutputs,
    ExecutionPhaseResult,
    HarborExecutionResult,
    PersistedArtifacts,
    RunLayout,
    RunRequest,
    ScorecardBuildContext,
    WorkspaceContext,
    _build_verifier_scenario_spec,
    _classify_unscored_reasons,
    _ensure_baseline_workspace,
    _load_verifier_outputs,
    _normalized_shell_subcommands,
    _prune_workspace_artifacts,
    _resolve_homepage_screenshot_command,
    _workspace_changes_from_baseline,
    build_scorecard,
    collect_process_metrics,
    create_harbor_task_bundle,
    evaluate_coverage,
    evaluate_requirements,
    scenario_evaluation_profile,
)
from raidar.schemas.events import GateEvent
from raidar.schemas.scenario import DeterministicCheck, RequirementSpec, ScenarioDefinition
from raidar.schemas.scorecard import (
    AcceptanceScore,
    CoverageScore,
    ExecutionValidityScore,
    FunctionalScore,
    MetricResult,
    PerformanceGatesScore,
    VerificationStabilityScore,
)
from raidar.schemas.scorecard import (
    RequirementsCoverageScore as RequirementCoverageScore,
)
from raidar.starter.catalog import StarterSource


def _sample_scenario() -> ScenarioDefinition:
    return ScenarioDefinition.model_validate(
        {
            "name": "homepage-implementation",
            "scenario_revision": "v001",
            "description": "test task",
            "difficulty": "medium",
            "category": "greenfield-ui",
            "timeout_sec": 1800,
            "starter": {
                "root": "starter",
            },
            "verification": {
                "setup_actions": [
                    ["git", "init"],
                    ["git", "config", "core.hooksPath", ".githooks"],
                ],
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
                "workflow": {"atomic_commits_required": False},
            },
            "acceptance": {},
            "metrics": [
                {"type": "core", "id": "functional"},
                {"type": "core", "id": "acceptance"},
                {"type": "core", "id": "verification-stability"},
                {"type": "core", "id": "execution-validity"},
                {"type": "core", "id": "resource-efficiency"},
                {"type": "core", "id": "test-coverage"},
            ],
            "visual": {
                "reference_image": "./reference/homepage.png",
                "screenshot_command": ["bun", "run", "capture-screenshot"],
                "viewport": {"width": 1440, "height": 1024},
                "scoring": {
                    "weights": {
                        "global": 0.25,
                        "regional": 0.45,
                        "worst_region": 0.25,
                        "region_pass_rate": 0.05,
                    },
                    "bands": {
                        "global": {"lower": 0.85, "upper": 0.96},
                        "regional": {"lower": 0.8, "upper": 0.95},
                        "worst_region": {"lower": 0.75, "upper": 0.94},
                    },
                    "gamma": 2,
                    "region_pass_threshold": 0.9,
                },
                "pass_policy": {
                    "fail_if_global_below": 0.9,
                    "fail_if_worst_region_below": 0.85,
                    "minimum_score": 70,
                    "minimum_region_pass_rate": 0.75,
                    "minimum_worst_region": 0.88,
                    "high_fidelity_score": 85,
                    "high_fidelity_global": 0.95,
                    "high_fidelity_worst_region": 0.92,
                },
                "regions": [],
            },
            "prompt": {"entry": "prompt/task.md"},
        }
    )


def _sample_agent_config() -> AgentSpec:
    return AgentSpec(
        harness=Harness.CODEX_CLI,
        model=ModelTarget(provider="openai", name="gpt-5"),
        timeout_sec=1800,
    )


def _sample_evaluation_outputs() -> EvaluationOutputs:
    return EvaluationOutputs(
        functional=FunctionalScore(
            passed=True,
            tests_passed=2,
            tests_total=2,
            build_succeeded=True,
            gates_passed=2,
            gates_total=2,
        ),
        acceptance=AcceptanceScore(),
        visual=None,
        verification_stability=VerificationStabilityScore(
            total_gate_failures=0,
            unique_failure_categories=0,
            repeat_failures=0,
        ),
        test_coverage=CoverageScore(
            threshold=0.8,
            measured=0.9,
            source="coverage-summary",
            passed=True,
        ),
        requirements_coverage=RequirementCoverageScore(
            total_requirements=1,
            satisfied_requirements=1,
            mapped_requirements=1,
        ),
        execution_validity=ExecutionValidityScore(),
        performance_gates=PerformanceGatesScore(),
        metric_results=[],
        gate_history=[],
    )


def _sample_scorecard_context(
    tmp_path: Path,
    *,
    terminated_early: bool,
    termination_reason: str | None,
) -> ScorecardBuildContext:
    scenario_dir = tmp_path / "scenario"
    workspace_dir = tmp_path / "workspace"
    results_dir = tmp_path / "results"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "scenario.yaml").write_text("name: sample-task\nscenario_revision: v001\n")
    (scenario_dir / "prompt").mkdir(parents=True, exist_ok=True)
    (scenario_dir / "prompt" / "task.md").write_text("Build homepage\n")

    starter_source = StarterSource(
        scenario_name="homepage-implementation",
        scenario_revision="v001",
        path=workspace_dir,
        fingerprint=directory_fingerprint(workspace_dir),
    )

    request = RunRequest(
        scenario=_sample_scenario(),
        config=_sample_agent_config(),
        scenario_dir=scenario_dir,
        execution_dir=results_dir,
        repeat_index=1,
    )
    context = WorkspaceContext(
        starter_source=starter_source,
        baseline_workspace=workspace_dir,
        baseline_cache_key="baseline-cache-key",
        baseline_cache_status="hit",
        baseline_cache_hit=True,
        baseline_metadata_path=workspace_dir / "baseline-metadata.json",
        baseline_fingerprint="baseline-fingerprint",
        workspace=workspace_dir,
        injected_rules=None,
        metadata_path=workspace_dir / ".starter-meta.json",
    )
    layout = RunLayout(
        run_id="run-1234",
        start_time=datetime.now(UTC),
        run_label="run-01",
        root_dir=results_dir / "runs" / "run-1234",
        workspace_dir=results_dir / "runs" / "run-1234" / "workspace",
        verifier_dir=results_dir / "runs" / "run-1234" / "verifier",
        harness_dir=results_dir / "runs" / "run-1234" / "harness",
        harbor_dir=results_dir / "runs" / "run-1234" / "harbor",
        run_json_path=results_dir / "runs" / "run-1234" / "run.json",
        report_path=results_dir / "runs" / "run-1234" / "report.md",
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
        process_metrics=collect_process_metrics(_sample_scenario(), None, harness="codex-cli"),
        events=[],
        outputs=_sample_evaluation_outputs(),
        duration_sec=12.5,
        prep_phase_timings_sec={"prepare_run_context": 0.123},
        prep_total_sec=0.456,
        cache_metadata={
            "baseline": {
                "hit": True,
                "status": "hit",
                "cache_key": "baseline-cache-key",
                "workspace_dir": str(workspace_dir),
                "metadata_path": str(workspace_dir / "baseline-metadata.json"),
                "complete": True,
                "fingerprint": "baseline-fingerprint",
            },
            "preflight": {"hit": False},
            "image": {"hit": True},
            "image_key": "image-key",
            "image_tag": "task-env-codex-cli-image-key",
        },
    )
    artifacts = PersistedArtifacts(
        starter_meta={"scenario": "homepage-implementation", "scenario_revision": "v001"},
        scenario_revision_meta={"scenario_yaml_hash": "abc"},
        verifier_artifacts={"scorecard": "verifier/scorecard.json"},
        harness_artifacts={"log": "harness/codex.txt"},
        harbor_artifacts={"command": "harbor/command.txt"},
        evidence_artifacts={"homepage_post": None, "errors": []},
        workspace_prune={"removed": [], "reclaimed_bytes": 0},
        workspace_changes={
            "added": [],
            "removed": [],
            "modified": [],
            "changed_files": [],
            "changed_file_count": 0,
            "artifact": None,
            "error": None,
        },
    )
    return ScorecardBuildContext(
        request=request,
        layout=layout,
        context=context,
        artifacts=artifacts,
        execution=execution,
    )


def _seed_workspace_tree(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "package.json").write_text("{}")
    (workspace / "bun.lock").write_text("")
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "index.tsx").write_text("export const App = () => null;\n")


def _sample_workspace_context(workspace: Path, *, scenario_name: str) -> WorkspaceContext:
    starter_source = StarterSource(
        scenario_name=scenario_name,
        scenario_revision="v001",
        path=workspace,
        fingerprint=directory_fingerprint(workspace),
    )
    return WorkspaceContext(
        starter_source=starter_source,
        baseline_workspace=workspace,
        baseline_cache_key="baseline-cache-key",
        baseline_cache_status="hit",
        baseline_cache_hit=True,
        baseline_metadata_path=workspace / "baseline-metadata.json",
        baseline_fingerprint="baseline-fingerprint",
        workspace=workspace,
        injected_rules=None,
        metadata_path=workspace / ".starter-meta.json",
    )


def _make_bundle_request(
    *,
    scenario: ScenarioDefinition,
    scenario_dir: Path,
    results_dir: Path,
) -> RunRequest:
    return RunRequest(
        scenario=scenario,
        config=_sample_agent_config(),
        scenario_dir=scenario_dir,
        execution_dir=results_dir,
        repeat_index=1,
    )


def test_ensure_baseline_workspace_initializes_once_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario_dir = tmp_path / "scenario" / "v001"
    starter_dir = scenario_dir / "starter"
    starter_dir.mkdir(parents=True, exist_ok=True)
    baseline_workspace_dir = (
        tmp_path / ".cache" / "raidar" / "prep" / "baselines" / "cache-key" / "workspace"
    )
    call_count = 0
    call_lock = threading.Lock()
    start_barrier = threading.Barrier(3)

    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")

    def fake_prepare_workspace(
        starter_dir: Path, target_dir: Path, scenario_dir: Path, harness: str
    ) -> tuple[Path, Path | None]:
        del starter_dir, scenario_dir, harness
        nonlocal call_count
        with call_lock:
            call_count += 1
        time.sleep(0.05)
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir, None

    monkeypatch.setattr("raidar.runner.prepare_workspace", fake_prepare_workspace)

    failures: list[Exception] = []

    def _run() -> None:
        try:
            start_barrier.wait(timeout=1.0)
            _ensure_baseline_workspace(
                scenario=_sample_scenario(),
                starter_dir=starter_dir,
                baseline_workspace_dir=baseline_workspace_dir,
                baseline_cache_key="cache-key",
                scenario_dir=scenario_dir,
                harness="codex-cli",
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


def test_ensure_baseline_workspace_runs_setup_actions_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario_dir = tmp_path / "scenario" / "v001"
    starter_dir = scenario_dir / "starter"
    starter_dir.mkdir(parents=True, exist_ok=True)
    baseline_workspace_dir = (
        tmp_path / ".cache" / "raidar" / "prep" / "baselines" / "cache-key" / "workspace"
    )
    setup_calls: list[list[str]] = []

    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")

    def fake_prepare_workspace(
        starter_dir: Path, target_dir: Path, scenario_dir: Path, harness: str
    ) -> tuple[Path, Path | None]:
        del starter_dir, scenario_dir, harness
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir, None

    def fake_run_setup_actions(
        *, workspace: Path, env: dict[str, str], setup_actions: list[list[str]]
    ) -> None:
        assert workspace == baseline_workspace_dir
        assert env["TMPDIR"] == str(baseline_workspace_dir / ".tmp")
        assert env["TMP"] == str(baseline_workspace_dir / ".tmp")
        assert env["TEMP"] == str(baseline_workspace_dir / ".tmp")
        assert env["XDG_CACHE_HOME"] == str(baseline_workspace_dir / ".cache")
        assert env["UV_CACHE_DIR"] == str(baseline_workspace_dir / ".cache" / "uv")
        assert env["BUN_INSTALL_CACHE_DIR"] == str(baseline_workspace_dir / ".cache" / "bun")
        setup_calls.extend(setup_actions)

    monkeypatch.setattr("raidar.runner.prepare_workspace", fake_prepare_workspace)
    monkeypatch.setattr("raidar.runner._run_workspace_setup_actions", fake_run_setup_actions)

    _ensure_baseline_workspace(
        scenario=_sample_scenario(),
        starter_dir=starter_dir,
        baseline_workspace_dir=baseline_workspace_dir,
        baseline_cache_key="cache-key",
        scenario_dir=scenario_dir,
        harness="codex-cli",
    )

    assert setup_calls == [
        ["git", "init"],
        ["git", "config", "core.hooksPath", ".githooks"],
    ]
    assert (baseline_workspace_dir / ".tmp").is_dir()
    assert (baseline_workspace_dir / ".cache" / "uv").is_dir()
    assert (baseline_workspace_dir / ".cache" / "bun").is_dir()


def test_ensure_baseline_workspace_rebuilds_incomplete_cache_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario_dir = tmp_path / "scenario" / "v001"
    starter_dir = scenario_dir / "starter"
    starter_dir.mkdir(parents=True, exist_ok=True)
    baseline_workspace_dir = (
        tmp_path / ".cache" / "raidar" / "prep" / "baselines" / "cache-key" / "workspace"
    )
    baseline_workspace_dir.mkdir(parents=True, exist_ok=True)
    (baseline_workspace_dir / "partial.txt").write_text("stale\n", encoding="utf-8")

    prepare_calls = 0
    setup_calls = 0

    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")

    def fake_prepare_workspace(
        starter_dir: Path, target_dir: Path, scenario_dir: Path, harness: str
    ) -> tuple[Path, Path | None]:
        del starter_dir, scenario_dir, harness
        nonlocal prepare_calls
        prepare_calls += 1
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "fresh.txt").write_text("ready\n", encoding="utf-8")
        return target_dir, None

    def fake_run_setup_actions(
        *, workspace: Path, env: dict[str, str], setup_actions: list[list[str]]
    ) -> None:
        del env, setup_actions
        nonlocal setup_calls
        setup_calls += 1
        assert workspace == baseline_workspace_dir

    monkeypatch.setattr("raidar.runner.prepare_workspace", fake_prepare_workspace)
    monkeypatch.setattr("raidar.runner._run_workspace_setup_actions", fake_run_setup_actions)

    cache_result = _ensure_baseline_workspace(
        scenario=_sample_scenario(),
        starter_dir=starter_dir,
        baseline_workspace_dir=baseline_workspace_dir,
        baseline_cache_key="cache-key",
        scenario_dir=scenario_dir,
        harness="codex-cli",
    )

    assert cache_result.hit is False
    assert cache_result.status == "invalidated"
    assert prepare_calls == 1
    assert setup_calls == 1
    assert not (baseline_workspace_dir / "partial.txt").exists()
    assert (baseline_workspace_dir.parent / "metadata.json").exists()


def test_ensure_baseline_workspace_rebuilds_fingerprint_mismatch_cache_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario_dir = tmp_path / "scenario" / "v001"
    starter_dir = scenario_dir / "starter"
    starter_dir.mkdir(parents=True, exist_ok=True)
    baseline_workspace_dir = (
        tmp_path / ".cache" / "raidar" / "prep" / "baselines" / "cache-key" / "workspace"
    )
    baseline_workspace_dir.mkdir(parents=True, exist_ok=True)
    (baseline_workspace_dir / "partial.txt").write_text("tampered\n", encoding="utf-8")
    metadata_path = baseline_workspace_dir.parent / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "cache_key": "cache-key",
                "baseline_fingerprint": "sha256:not-a-match",
                "created_at": "2026-03-25T00:00:00+00:00",
                "harness": "codex-cli",
            }
        ),
        encoding="utf-8",
    )

    prepare_calls = 0

    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")

    def fake_prepare_workspace(
        starter_dir: Path, target_dir: Path, scenario_dir: Path, harness: str
    ) -> tuple[Path, Path | None]:
        del starter_dir, scenario_dir, harness
        nonlocal prepare_calls
        prepare_calls += 1
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "fresh.txt").write_text("ready\n", encoding="utf-8")
        return target_dir, None

    monkeypatch.setattr("raidar.runner.prepare_workspace", fake_prepare_workspace)
    monkeypatch.setattr("raidar.runner._run_workspace_setup_actions", lambda **_kwargs: None)

    cache_result = _ensure_baseline_workspace(
        scenario=_sample_scenario(),
        starter_dir=starter_dir,
        baseline_workspace_dir=baseline_workspace_dir,
        baseline_cache_key="cache-key",
        scenario_dir=scenario_dir,
        harness="codex-cli",
    )

    assert cache_result.hit is False
    assert cache_result.status == "invalidated"
    assert prepare_calls == 1
    assert not (baseline_workspace_dir / "partial.txt").exists()
    assert (baseline_workspace_dir / "fresh.txt").exists()


def test_collect_process_metrics_extracts_usage_and_failures(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    harness_dir = trial_dir / "agent"
    harness_dir.mkdir(parents=True, exist_ok=True)
    codex_log = harness_dir / "codex.txt"
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

    metrics = collect_process_metrics(_sample_scenario(), trial_dir, harness="codex-cli")

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
    harness_dir = trial_dir / "agent"
    harness_dir.mkdir(parents=True, exist_ok=True)
    codex_log = harness_dir / "codex.txt"
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

    scenario = ScenarioDefinition.model_validate(
        {
            "name": "homepage-implementation",
            "scenario_revision": "v001",
            "description": "test task",
            "difficulty": "medium",
            "category": "greenfield-ui",
            "timeout_sec": 1800,
            "starter": {
                "root": "starter",
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
            "acceptance": {},
            "metrics": [
                {"type": "core", "id": "functional"},
                {"type": "core", "id": "acceptance"},
                {"type": "core", "id": "verification-stability"},
                {"type": "core", "id": "execution-validity"},
                {"type": "core", "id": "resource-efficiency"},
            ],
            "prompt": {"entry": "prompt/task.md"},
        }
    )

    metrics = collect_process_metrics(scenario, trial_dir, harness="codex-cli")

    assert metrics.required_verification_commands == 2
    assert metrics.executed_required_verification_commands == 2


def test_collect_process_metrics_extracts_gemini_commands_from_agent_stdout(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    harness_dir = trial_dir / "agent"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (harness_dir / "gemini-cli.trajectory.json").write_text(
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

    metrics = collect_process_metrics(_sample_scenario(), trial_dir, harness="gemini")

    assert metrics.command_count == 3
    assert metrics.failed_command_count == 0
    assert metrics.required_verification_commands == 3
    assert metrics.executed_required_verification_commands == 3
    assert metrics.required_verification_first_pass["bun run typecheck"] == "pass"
    assert metrics.required_verification_first_pass["bun run lint"] == "pass"
    assert metrics.required_verification_first_pass["bun run build"] == "pass"


def test_collect_process_metrics_extracts_gemini_trajectory_shell_commands(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    harness_dir = trial_dir / "agent"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (harness_dir / "gemini-cli.trajectory.json").write_text(
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

    metrics = collect_process_metrics(_sample_scenario(), trial_dir, harness="gemini")

    assert metrics.command_count == 2
    assert metrics.required_verification_commands == 3
    assert metrics.executed_required_verification_commands == 2
    assert metrics.required_verification_first_pass["bun run typecheck"] == "pass"
    assert metrics.required_verification_first_pass["bun run lint"] == "pass"
    assert metrics.required_verification_first_pass["bun run build"] == "missing"


def test_normalized_shell_subcommands_splits_and_normalizes_aliases() -> None:
    commands = _normalized_shell_subcommands(
        "bash -lc 'bunx tsc --noEmit && npm run lint; bun run build'"
    )

    assert commands == ["bun run typecheck", "bun run lint", "bun run build"]


def test_normalized_shell_subcommands_handles_unparseable_command() -> None:
    commands = _normalized_shell_subcommands("bunx tsc --noEmit '")

    assert commands == ["bun run typecheck"]


def test_normalized_shell_subcommands_returns_empty_for_blank() -> None:
    assert _normalized_shell_subcommands("   ") == []


def test_normalized_shell_subcommands_handles_heredoc_followed_by_verification() -> None:
    commands = _normalized_shell_subcommands(
        "/bin/bash -lc \"cat > src/app/page.tsx <<'EOF'\n"
        "export default function Home() {\n"
        "\treturn <main>Harbor smoke test ready</main>;\n"
        "}\n"
        "EOF\n"
        "bun run typecheck\n"
        'bun run lint"'
    )

    assert commands[-2:] == ["bun run typecheck", "bun run lint"]


def test_collect_process_metrics_extracts_verify_with_phrasing(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    harness_dir = trial_dir / "agent"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (harness_dir / "gemini-cli.trajectory.json").write_text(
        json.dumps({"messages": [{"tokens": {"input": 10, "cached": 2, "output": 1}}]})
    )
    command_dir = trial_dir / "agent" / "command-0"
    command_dir.mkdir(parents=True, exist_ok=True)
    (command_dir / "stdout.txt").write_text(
        "I have updated `src/app/page.tsx` with the requested text and verified "
        "the implementation with a successful build and typecheck."
    )

    metrics = collect_process_metrics(_sample_scenario(), trial_dir, harness="gemini")

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

    metrics = collect_process_metrics(_sample_scenario(), trial_dir, harness="claude-code")

    assert metrics.command_count == 2
    assert metrics.failed_command_count == 0
    assert metrics.required_verification_commands == 3
    assert metrics.executed_required_verification_commands == 2
    assert metrics.required_verification_first_pass["bun run typecheck"] == "pass"
    assert metrics.required_verification_first_pass["bun run lint"] == "pass"
    assert metrics.required_verification_first_pass["bun run build"] == "missing"


def test_collect_process_metrics_extracts_claude_bash_from_top_level_log(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    harness_dir = trial_dir / "agent"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (harness_dir / "claude-code.txt").write_text(
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

    metrics = collect_process_metrics(_sample_scenario(), trial_dir, harness="claude-code")

    assert metrics.command_count == 2
    assert metrics.required_verification_commands == 3
    assert metrics.executed_required_verification_commands == 2
    assert metrics.required_verification_first_pass["bun run typecheck"] == "pass"
    assert metrics.required_verification_first_pass["bun run lint"] == "pass"
    assert metrics.required_verification_first_pass["bun run build"] == "missing"


def test_collect_process_metrics_extracts_claude_result_usage(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    harness_dir = trial_dir / "agent"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (harness_dir / "claude-code.txt").write_text(
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

    metrics = collect_process_metrics(_sample_scenario(), trial_dir, harness="claude-code")

    assert metrics.uncached_input_tokens == 600
    assert metrics.output_tokens == 111


def test_collect_process_metrics_extracts_gemini_usage_from_trajectory(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    harness_dir = trial_dir / "agent"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (harness_dir / "gemini-cli.trajectory.json").write_text(
        json.dumps(
            {
                "messages": [
                    {"tokens": {"input": 100, "cached": 20, "output": 10}},
                    {"tokens": {"input": 120, "cached": 30, "output": 12}},
                ]
            }
        )
    )
    (harness_dir / "gemini-cli.txt").write_text("$ bun run typecheck\n")

    metrics = collect_process_metrics(_sample_scenario(), trial_dir, harness="gemini")

    assert metrics.uncached_input_tokens == 170
    assert metrics.output_tokens == 22


def test_collect_process_metrics_raises_when_usage_missing(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    harness_dir = trial_dir / "agent"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (harness_dir / "gemini-cli.trajectory.json").write_text(json.dumps({"messages": []}))

    with pytest.raises(RuntimeError, match="Missing token usage metrics"):
        collect_process_metrics(_sample_scenario(), trial_dir, harness="gemini")


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
                "acceptance": {
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
                    "similarity": 0.91,
                    "contract_version": "oracle",
                    "global_similarity": 0.97,
                    "regional_similarity": 0.95,
                    "worst_region_similarity": 0.91,
                    "region_decent_pass_rate": 0.5,
                    "policy_score": 91.0,
                    "passed": True,
                    "fidelity_tier": "passed",
                    "expected_region_count": 2,
                    "available_region_count": 2,
                    "region_evidence_status": "present",
                    "actual_path": "/tmp/run/visual/actual.png",
                    "reference_path": "/tmp/run/visual/reference.png",
                    "diff_path": "/tmp/run/visual/diff.png",
                    "capture_succeeded": True,
                    "regional_scores": [
                        {
                            "name": "hero",
                            "weight": 0.5,
                            "normalized_weight": 0.5,
                            "similarity": 0.98,
                            "decent_pass": True,
                            "actual_path": "/tmp/run/visual/actual-region-hero.png",
                            "reference_path": "/tmp/run/visual/reference-region-hero.png",
                            "diff_path": "/tmp/run/visual/diff-region-hero.png",
                        },
                        {
                            "name": "footer",
                            "weight": 0.5,
                            "normalized_weight": 0.5,
                            "similarity": 0.91,
                            "decent_pass": True,
                            "actual_path": "/tmp/run/visual/actual-region-footer.png",
                            "reference_path": "/tmp/run/visual/reference-region-footer.png",
                            "diff_path": "/tmp/run/visual/diff-region-footer.png",
                        },
                    ],
                },
                "verification_stability": {
                    "total_gate_failures": 0,
                    "unique_failure_categories": 0,
                    "repeat_failures": 0,
                },
                "test_coverage": {
                    "threshold": 0.8,
                    "measured": 0.9,
                    "source": "coverage-summary",
                    "passed": True,
                },
                "requirements_coverage": {
                    "total_requirements": 1,
                    "satisfied_requirements": 1,
                    "mapped_requirements": 1,
                    "missing_requirement_ids": [],
                    "requirement_gap_ids": [],
                },
                "execution_validity": {
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
                "metric_results": [
                    {
                        "metric_id": "artifact-checks",
                        "passed": False,
                        "matched_count": 0,
                        "missing_patterns": ["src/components/**/*.tsx"],
                        "evidence": "artifact-checks matches (src/components/**/*.tsx:0)",
                    }
                ],
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
            }
        )
    )

    outputs, reason = _load_verifier_outputs(trial_dir)

    assert reason is None
    assert outputs is not None
    assert outputs.functional.passed is True
    assert outputs.visual is not None
    assert outputs.visual.passed is True
    assert outputs.visual.fidelity_tier == "passed"
    assert outputs.visual.policy_score == 91.0
    assert outputs.visual.region_evidence_status == "present"
    assert outputs.visual.worst_region_similarity == 0.91
    assert outputs.visual.regional_scores[1]["name"] == "footer"
    assert outputs.metric_results == [
        MetricResult(
            metric_id="artifact-checks",
            passed=False,
            matched_count=0,
            missing_patterns=["src/components/**/*.tsx"],
            evidence="artifact-checks matches (src/components/**/*.tsx:0)",
        )
    ]
    assert len(outputs.gate_history) == 1


def test_load_verifier_outputs_requires_modules_field(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    verifier_dir = trial_dir / "verifier"
    verifier_dir.mkdir(parents=True, exist_ok=True)
    (verifier_dir / "scorecard.json").write_text(
        json.dumps(
            {
                "functional": {
                    "passed": True,
                    "tests_passed": 1,
                    "tests_total": 1,
                    "build_succeeded": True,
                    "gates_passed": 1,
                    "gates_total": 1,
                },
                "acceptance": {"checks": []},
                "visual": None,
                "verification_stability": {
                    "total_gate_failures": 0,
                    "unique_failure_categories": 0,
                    "repeat_failures": 0,
                },
                "test_coverage": {
                    "threshold": None,
                    "measured": None,
                    "source": None,
                    "passed": True,
                },
                "requirements_coverage": {
                    "total_requirements": 0,
                    "satisfied_requirements": 0,
                    "mapped_requirements": 0,
                    "mapped_satisfied_requirements": 0,
                    "missing_requirement_ids": [],
                    "requirement_gap_ids": [],
                    "requirement_pattern_gaps": {},
                },
                "execution_validity": {"checks": []},
                "performance_gates": {"checks": []},
                "gate_history": [],
            }
        )
    )
    outputs, reason = _load_verifier_outputs(trial_dir)
    assert outputs is None
    assert reason is not None
    assert "scorecard.metric_results must be a list" in reason


def test_load_verifier_outputs_missing_scorecard(tmp_path: Path):
    outputs, reason = _load_verifier_outputs(tmp_path / "missing")
    assert outputs is None
    assert reason is not None


def test_scenario_evaluation_profile_uses_ordered_metrics():
    scenario = _sample_scenario()
    assert scenario_evaluation_profile(scenario) == (
        "functional+acceptance+verification-stability+"
        "execution-validity+resource-efficiency+test-coverage"
    )


def test_build_verifier_scenario_spec_includes_metrics(tmp_path: Path):
    score_context = _sample_scorecard_context(
        tmp_path=tmp_path,
        terminated_early=False,
        termination_reason=None,
    )
    scenario_spec = _build_verifier_scenario_spec(score_context.request, score_context.context)
    assert scenario_spec["metrics"] == [
        {"type": "core", "id": "functional"},
        {"type": "core", "id": "acceptance"},
        {"type": "core", "id": "verification-stability"},
        {"type": "core", "id": "execution-validity"},
        {"type": "core", "id": "resource-efficiency"},
        {"type": "core", "id": "test-coverage"},
    ]
    assert scenario_spec["visual"]["viewport"] == {"width": 1440, "height": 1024}
    assert scenario_spec["visual"]["scoring"]["weights"]["global"] == 0.25
    assert scenario_spec["visual"]["pass_policy"]["minimum_score"] == 70
    assert scenario_spec["verification"]["workflow"] == {"atomic_commits_required": False}


def test_build_scorecard_fails_execution_validity_without_required_atomic_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    score_context = _sample_scorecard_context(
        tmp_path=tmp_path,
        terminated_early=False,
        termination_reason=None,
    )
    score_context.request.scenario.verification.workflow.atomic_commits_required = True
    monkeypatch.setattr("raidar.runner._git_commit_count", lambda _: (0, "commit_count=0"))

    scorecard = build_scorecard(score_context)

    atomic_check = next(
        check
        for check in scorecard.execution_validity.checks
        if check.name == "atomic_commits_present"
    )
    assert atomic_check.passed is False
    assert scorecard.execution_validity.passed is False


def test_build_scorecard_records_prep_timings_and_cache_metadata(tmp_path: Path) -> None:
    score_context = _sample_scorecard_context(
        tmp_path=tmp_path,
        terminated_early=False,
        termination_reason=None,
    )

    scorecard = build_scorecard(score_context)
    harbor_meta = scorecard.metadata["harbor"]

    assert harbor_meta["prep_phase_timings_sec"] == {"prepare_run_context": 0.123}
    assert harbor_meta["prep_total_sec"] == 0.456
    assert harbor_meta["cache"]["baseline"]["hit"] is True
    assert harbor_meta["cache"]["baseline"]["status"] == "hit"
    assert harbor_meta["cache"]["baseline"]["cache_key"] == "baseline-cache-key"
    assert harbor_meta["cache"]["baseline"]["workspace_dir"] == str(score_context.context.workspace)
    assert harbor_meta["cache"]["baseline"]["metadata_path"] == str(
        score_context.context.baseline_metadata_path
    )
    assert harbor_meta["cache"]["baseline"]["complete"] is True
    assert harbor_meta["cache"]["baseline"]["fingerprint"] == "baseline-fingerprint"
    assert harbor_meta["cache"]["preflight"]["hit"] is False
    assert harbor_meta["cache"]["image"]["hit"] is True
    assert harbor_meta["cache"]["image_key"] == "image-key"
    assert harbor_meta["cache"]["image_tag"] == "task-env-codex-cli-image-key"


def test_verifier_file_exists_glob_matches_direct_and_nested_section_files(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    logs_dir = tmp_path / "logs"
    tests_dir = tmp_path / "tests"
    app_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "package.json").write_text("{}", encoding="utf-8")
    (app_dir / "bun.lock").write_text("", encoding="utf-8")
    (app_dir / "src" / "components" / "sections").mkdir(parents=True, exist_ok=True)
    (app_dir / "src" / "components" / "sections" / "Hero.tsx").write_text(
        "export function Hero() { return null; }\n",
        encoding="utf-8",
    )
    (app_dir / "src" / "components" / "sections" / "nested").mkdir(parents=True, exist_ok=True)
    (app_dir / "src" / "components" / "sections" / "nested" / "Footer.tsx").write_text(
        "export function Footer() { return null; }\n",
        encoding="utf-8",
    )

    scenario_spec_path = tests_dir / "scenario-spec.json"
    scenario_spec_path.write_text(
        json.dumps(
            {
                "metrics": [],
                "verification": {
                    "max_gate_failures": 3,
                    "coverage_threshold": None,
                    "min_quality_score": 0,
                    "gates": [],
                    "workflow": {"atomic_commits_required": False},
                },
                "acceptance": {
                    "deterministic_checks": [
                        {
                            "type": "file_exists",
                            "pattern": "src/components/sections/**/*.tsx",
                            "description": "direct or nested section component exists",
                        }
                    ],
                    "requirements": [],
                },
                "weights": {
                    "functional": 0.25,
                    "acceptance": 0.25,
                    "visual": 0.25,
                    "verification_stability": 0.25,
                },
                "baseline_scripts": {},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    score_script = tests_dir / "score-scenario.mjs"
    score_script.write_text(runner._verifier_scorer_script(), encoding="utf-8")

    completed = subprocess.run(
        ["bun", str(score_script), str(scenario_spec_path)],
        cwd=tests_dir,
        env={
            **runner.os.environ,
            "RAIDAR_APP_DIR": str(app_dir),
            "RAIDAR_LOG_DIR": str(logs_dir),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    scorecard = json.loads((logs_dir / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["acceptance"]["checks"][0]["passed"] is True


def test_classify_unscored_reasons_rate_limit():
    reasons = _classify_unscored_reasons(
        terminated_early=True,
        termination_reason="Codex turn failed: Rate limit reached for gpt-5.2-codex.",
    )
    assert "provider_rate_limit" in reasons


def test_classify_unscored_reasons_timeout():
    reasons = _classify_unscored_reasons(
        terminated_early=True,
        termination_reason="Timeout expired after 420s before trial result.json was written.",
    )
    assert reasons == ["harbor_timeout"]


def test_classify_unscored_reasons_compose_version_unsupported():
    reasons = _classify_unscored_reasons(
        terminated_early=True,
        termination_reason=(
            "Unsupported docker compose version 2.39.2. Require >= 2.40.1 for Harbor runs."
        ),
    )
    assert reasons == ["compose_version_unsupported"]


def test_classify_unscored_reasons_empty_when_not_terminated():
    reasons = _classify_unscored_reasons(
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

    assert scorecard.unscored is True
    assert "provider_rate_limit" in scorecard.unscored_reasons
    assert scorecard.metadata["run"]["rerun_required"] is True
    assert scorecard.metadata["run"]["unscored_reasons"] == scorecard.unscored_reasons


def test_persist_canonical_verifier_artifacts_overwrites_stale_trial_scorecard(tmp_path: Path):
    context = _sample_scorecard_context(
        tmp_path,
        terminated_early=True,
        termination_reason=(
            "Codex turn failed: Quota exceeded. Check your plan and billing details."
        ),
    )
    verifier_dir = context.layout.verifier_dir
    verifier_dir.mkdir(parents=True, exist_ok=True)
    (verifier_dir / "scorecard.json").write_text(
        json.dumps(
            {
                "execution_validity": {
                    "checks": [
                        {
                            "name": "run_completed",
                            "passed": True,
                            "evidence": "Run completed without early termination.",
                        }
                    ],
                    "passed": True,
                },
                "performance_gates": {"checks": [], "passed": True},
                "gate_history": [],
            }
        ),
        encoding="utf-8",
    )
    (verifier_dir / "execution-validity.json").write_text(
        json.dumps({"checks": [{"name": "run_completed", "passed": True}], "passed": True}),
        encoding="utf-8",
    )
    (verifier_dir / "performance-gates.json").write_text(
        json.dumps({"checks": [], "passed": True}),
        encoding="utf-8",
    )
    (verifier_dir / "gate-history.json").write_text(
        json.dumps([{"gate_name": "lint"}]), encoding="utf-8"
    )
    (verifier_dir / "reward.txt").write_text("1", encoding="utf-8")

    scorecard = build_scorecard(context)

    runner.persist_canonical_verifier_artifacts(
        context.layout, scorecard, context.execution.outputs
    )

    persisted_scorecard = json.loads((verifier_dir / "scorecard.json").read_text(encoding="utf-8"))
    persisted_execution = json.loads(
        (verifier_dir / "execution-validity.json").read_text(encoding="utf-8")
    )
    persisted_gate_history = json.loads(
        (verifier_dir / "gate-history.json").read_text(encoding="utf-8")
    )

    run_completed_check = next(
        check for check in persisted_execution["checks"] if check["name"] == "run_completed"
    )
    assert run_completed_check["passed"] is False
    assert persisted_execution["passed"] is False
    assert persisted_scorecard["execution_validity"]["passed"] is False
    assert persisted_scorecard["unscored"] is True
    assert persisted_gate_history == []
    assert float((verifier_dir / "reward.txt").read_text(encoding="utf-8")) == 0.0


def test_create_harbor_task_bundle_copies_relative_visual_reference(tmp_path: Path):
    workspace = tmp_path / "workspace"
    scenario_dir = tmp_path / "scenario"
    results_dir = tmp_path / "results"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    _seed_workspace_tree(workspace)

    reference_rel = Path("references/hero.png")
    source_reference = scenario_dir / reference_rel
    source_reference.parent.mkdir(parents=True, exist_ok=True)
    source_reference.write_bytes(b"png-binary")
    (scenario_dir / "references" / "hero-region-nav.png").write_bytes(b"region-binary")

    scenario = ScenarioDefinition.model_validate(
        {
            "name": "homepage-implementation",
            "scenario_revision": "v001",
            "description": "test task",
            "difficulty": "medium",
            "category": "greenfield-ui",
            "timeout_sec": 1800,
            "starter": {
                "root": "starter",
            },
            "verification": {"gates": [], "required_commands": []},
            "visual": {
                "reference_image": str(reference_rel),
                "screenshot_command": ["bun", "run", "capture-screenshot"],
                "scoring": {
                    "weights": {
                        "global": 0.25,
                        "regional": 0.45,
                        "worst_region": 0.25,
                        "region_pass_rate": 0.05,
                    },
                    "bands": {
                        "global": {"lower": 0.85, "upper": 0.96},
                        "regional": {"lower": 0.8, "upper": 0.95},
                        "worst_region": {"lower": 0.75, "upper": 0.94},
                    },
                },
                "pass_policy": {
                    "fail_if_global_below": 0.9,
                    "fail_if_worst_region_below": 0.85,
                    "minimum_score": 70,
                    "minimum_region_pass_rate": 0.75,
                    "minimum_worst_region": 0.88,
                    "high_fidelity_score": 85,
                    "high_fidelity_global": 0.95,
                    "high_fidelity_worst_region": 0.92,
                },
                "regions": [
                    {
                        "name": "nav",
                        "weight": 1.0,
                        "clip": {"x": 0, "y": 0, "width": 1200, "height": 120},
                    }
                ],
            },
            "acceptance": {},
            "metrics": [
                {"type": "core", "id": "functional"},
                {"type": "core", "id": "acceptance"},
                {"type": "core", "id": "verification-stability"},
                {"type": "core", "id": "execution-validity"},
                {"type": "core", "id": "resource-efficiency"},
                {"type": "core", "id": "visual-regression"},
            ],
            "prompt": {"entry": "prompt/task.md"},
        }
    )
    (scenario_dir / "prompt").mkdir(parents=True, exist_ok=True)
    (scenario_dir / "prompt" / "task.md").write_text("Build homepage\n")
    request = _make_bundle_request(
        scenario=scenario,
        scenario_dir=scenario_dir,
        results_dir=results_dir,
    )
    context = _sample_workspace_context(workspace, scenario_name="homepage-implementation")

    bundle = create_harbor_task_bundle(
        request,
        context,
        bundle_root=results_dir / "runs" / "run-01" / "harbor" / "bundle",
    )
    copied_reference = bundle / "environment" / "app" / reference_rel
    copied_region_reference = bundle / "environment" / "app" / "references" / "hero-region-nav.png"
    scenario_spec = json.loads(
        (bundle / "tests" / "scenario-spec.json").read_text(encoding="utf-8")
    )

    assert copied_reference.exists()
    assert copied_reference.read_bytes() == b"png-binary"
    assert copied_region_reference.exists()
    assert copied_region_reference.read_bytes() == b"region-binary"
    assert (bundle / "tests" / "scenario-spec.json").exists()
    assert scenario_spec["visual"]["regions"] == [
        {
            "name": "nav",
            "weight": 1.0,
            "clip": {"x": 0, "y": 0, "width": 1200, "height": 120},
        }
    ]
    assert (
        (bundle / "tests" / "score-scenario.mjs")
        .read_text(encoding="utf-8")
        .startswith("#!/usr/bin/env bun")
    )
    score_script = (bundle / "tests" / "score-scenario.mjs").read_text(encoding="utf-8")
    assert "scenarioSpec.acceptance?.deterministic_checks" in score_script
    assert "metric_results" in score_script
    assert "verification_stability" in score_script
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
    scenario_dir = tmp_path / "scenario"
    results_dir = tmp_path / "results"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    _seed_workspace_tree(workspace)
    (scenario_dir / "scenario.yaml").write_text(
        "name: hello-world-smoke\nscenario_revision: v001\n"
    )
    (scenario_dir / "prompt").mkdir(parents=True, exist_ok=True)
    (scenario_dir / "prompt" / "task.md").write_text("Print hello world\n")

    scenario = ScenarioDefinition.model_validate(
        {
            "name": "hello-world-smoke",
            "scenario_revision": "v001",
            "description": "test task",
            "difficulty": "easy",
            "category": "greenfield-ui",
            "timeout_sec": 1800,
            "starter": {
                "root": "starter",
            },
            "verification": {"gates": [], "required_commands": []},
            "acceptance": {},
            "metrics": [
                {"type": "core", "id": "functional"},
                {"type": "core", "id": "acceptance"},
                {"type": "core", "id": "verification-stability"},
                {"type": "core", "id": "execution-validity"},
                {"type": "core", "id": "resource-efficiency"},
            ],
            "prompt": {"entry": "prompt/task.md"},
        }
    )
    request = _make_bundle_request(
        scenario=scenario,
        scenario_dir=scenario_dir,
        results_dir=results_dir,
    )
    context = _sample_workspace_context(workspace, scenario_name="hello-world-smoke")

    bundle = create_harbor_task_bundle(
        request,
        context,
        bundle_root=results_dir / "runs" / "run-01" / "harbor" / "bundle",
    )
    task_toml = (bundle / "task.toml").read_text()
    dockerfile = (bundle / "environment" / "Dockerfile").read_text()

    assert 'docker_image = "ts-ui-eval-smoke-fast:task-env-codex-cli-' in task_toml
    assert "@openai/codex" in dockerfile
    assert "@anthropic-ai/claude-code" not in dockerfile
    assert "@google/gemini-cli" not in dockerfile


def test_create_harbor_task_bundle_uses_injected_rules_filename_in_instruction(tmp_path: Path):
    workspace = tmp_path / "workspace"
    scenario_dir = tmp_path / "scenario"
    results_dir = tmp_path / "results"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    _seed_workspace_tree(workspace)
    (workspace / "GEMINI.md").write_text("gemini rules\n", encoding="utf-8")
    (scenario_dir / "scenario.yaml").write_text(
        "name: hello-world-smoke\nscenario_revision: v001\n"
    )
    (scenario_dir / "prompt").mkdir(parents=True, exist_ok=True)
    (scenario_dir / "prompt" / "task.md").write_text("Print hello world\n")

    scenario = ScenarioDefinition.model_validate(
        {
            "name": "hello-world-smoke",
            "scenario_revision": "v001",
            "description": "test task",
            "difficulty": "easy",
            "category": "greenfield-ui",
            "timeout_sec": 1800,
            "starter": {
                "root": "starter",
            },
            "verification": {"gates": [], "required_commands": []},
            "acceptance": {},
            "metrics": [
                {"type": "core", "id": "functional"},
                {"type": "core", "id": "acceptance"},
                {"type": "core", "id": "verification-stability"},
                {"type": "core", "id": "execution-validity"},
                {"type": "core", "id": "resource-efficiency"},
            ],
            "prompt": {"entry": "prompt/task.md"},
        }
    )
    request = RunRequest(
        scenario=scenario,
        config=AgentSpec(
            harness=Harness.GEMINI,
            model=ModelTarget(provider="google", name="gemini-3-flash-preview"),
            timeout_sec=1800,
        ),
        scenario_dir=scenario_dir,
        execution_dir=results_dir,
        repeat_index=1,
    )
    context = replace(
        _sample_workspace_context(workspace, scenario_name="hello-world-smoke"),
        injected_rules=workspace / "GEMINI.md",
    )

    bundle = create_harbor_task_bundle(
        request,
        context,
        bundle_root=results_dir / "runs" / "run-01" / "harbor" / "bundle",
    )

    instruction = (bundle / "instruction.md").read_text(encoding="utf-8")

    assert "You are working in `/app`." in instruction
    assert "Follow rules in `/app/GEMINI.md`." in instruction
    assert "Follow rules in `/app/AGENTS.md`." not in instruction


def test_resolve_homepage_screenshot_command_uses_visual_override(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "package.json").write_text("{}\n")

    scenario = ScenarioDefinition.model_validate(
        {
            "name": "homepage-implementation",
            "scenario_revision": "v001",
            "description": "task",
            "difficulty": "medium",
            "category": "greenfield-ui",
            "timeout_sec": 1800,
            "starter": {"root": "starter"},
            "verification": {"gates": [], "required_commands": []},
            "visual": {
                "reference_image": "reference/homepage.png",
                "screenshot_command": ["bun", "run", "capture-screenshot"],
                "scoring": {
                    "weights": {
                        "global": 0.25,
                        "regional": 0.45,
                        "worst_region": 0.25,
                        "region_pass_rate": 0.05,
                    },
                    "bands": {
                        "global": {"lower": 0.85, "upper": 0.96},
                        "regional": {"lower": 0.8, "upper": 0.95},
                        "worst_region": {"lower": 0.75, "upper": 0.94},
                    },
                },
                "pass_policy": {
                    "fail_if_global_below": 0.9,
                    "fail_if_worst_region_below": 0.85,
                    "minimum_score": 70,
                    "minimum_region_pass_rate": 0.75,
                    "minimum_worst_region": 0.88,
                    "high_fidelity_score": 85,
                    "high_fidelity_global": 0.95,
                    "high_fidelity_worst_region": 0.92,
                },
            },
            "acceptance": {},
            "metrics": [
                {"type": "core", "id": "functional"},
                {"type": "core", "id": "acceptance"},
                {"type": "core", "id": "verification-stability"},
                {"type": "core", "id": "execution-validity"},
                {"type": "core", "id": "resource-efficiency"},
                {"type": "core", "id": "visual-regression"},
            ],
            "prompt": {"entry": "prompt/task.md"},
        }
    )

    command = _resolve_homepage_screenshot_command(scenario, workspace)
    assert command == ["bun", "run", "capture-screenshot"]


def test_resolve_homepage_screenshot_command_returns_none_when_visual_missing(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "package.json").write_text(
        json.dumps({"scripts": {"capture-screenshot": "bun run scripts/capture-screenshot.ts"}})
    )

    scenario = ScenarioDefinition.model_validate(
        {
            "name": "hello-world-smoke",
            "scenario_revision": "v001",
            "description": "task",
            "difficulty": "easy",
            "category": "agent-integration",
            "timeout_sec": 300,
            "starter": {"root": "starter"},
            "verification": {"gates": [], "required_commands": []},
            "acceptance": {},
            "metrics": [
                {"type": "core", "id": "functional"},
                {"type": "core", "id": "acceptance"},
                {"type": "core", "id": "verification-stability"},
                {"type": "core", "id": "execution-validity"},
                {"type": "core", "id": "resource-efficiency"},
            ],
            "prompt": {"entry": "prompt/task.md"},
        }
    )

    command = _resolve_homepage_screenshot_command(scenario, workspace)
    assert command is None


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


def test_workspace_changes_from_baseline_reports_added_modified_removed(tmp_path: Path):
    baseline = tmp_path / "baseline"
    run_workspace = tmp_path / "run"
    run_root = tmp_path / "run-root"
    (baseline / "src").mkdir(parents=True, exist_ok=True)
    (run_workspace / "src").mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)

    (baseline / "src" / "a.ts").write_text("export const a = 1;\n")
    (baseline / "src" / "b.ts").write_text("export const b = 1;\n")

    (run_workspace / "src" / "a.ts").write_text("export const a = 2;\n")
    (run_workspace / "src" / "c.ts").write_text("export const c = 1;\n")

    changes = _workspace_changes_from_baseline(
        baseline_workspace=baseline,
        run_workspace=run_workspace,
        run_root_dir=run_root,
    )

    assert changes["added"] == ["src/c.ts"]
    assert changes["removed"] == ["src/b.ts"]
    assert changes["modified"] == ["src/a.ts"]
    assert changes["changed_file_count"] == 3
    assert (run_root / "workspace-diff.json").exists()
