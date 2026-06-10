"""Tests for execution-validity and resource-efficiency helpers."""

from types import SimpleNamespace

import pytest

from raidar.runtime import scoring_outputs as scoring_outputs_runtime
from raidar.schemas.scorecard import Scorecard
from tests import runtime_process_metrics_support as process_support
from tests import runtime_scorecard_workspace_support as runtime_support

errno = process_support.errno
json = process_support.json
subprocess = process_support.subprocess
threading = process_support.threading
time = process_support.time
dataclass = process_support.dataclass
replace = process_support.replace
UTC = process_support.UTC
datetime = process_support.datetime
Path = process_support.Path
AgentSpec = process_support.AgentSpec
Harness = process_support.Harness
ModelTarget = process_support.ModelTarget
process_metrics_runtime = process_support.process_metrics_runtime
_normalized_shell_subcommands = process_support._normalized_shell_subcommands
collect_process_metrics = process_support.collect_process_metrics
GateEvent = process_support.GateEvent
DeterministicCheck = process_support.DeterministicCheck
RequirementSpec = process_support.RequirementSpec
ScenarioDefinition = process_support.ScenarioDefinition
CoverageScore = process_support.CoverageScore
ExecutionValidityScore = process_support.ExecutionValidityScore
FunctionalScore = process_support.FunctionalScore
MetricScore = process_support.MetricScore
PerformanceGatesScore = process_support.PerformanceGatesScore
VerificationStabilityScore = process_support.VerificationStabilityScore
RequirementCoverageScore = process_support.RequirementCoverageScore
directory_fingerprint = runtime_support.directory_fingerprint
artifacts_runtime = runtime_support.artifacts_runtime
scorecard_runtime = runtime_support.scorecard_runtime
task_bundle_runtime = runtime_support.task_bundle_runtime
workspace_runtime = runtime_support.workspace_runtime
workspace_artifacts_runtime = runtime_support.workspace_artifacts_runtime
workspace_cache_runtime = runtime_support.workspace_cache_runtime
_load_verifier_outputs = runtime_support._load_verifier_outputs
EvaluationOutputs = runtime_support.EvaluationOutputs
ExecutionPhaseResult = runtime_support.ExecutionPhaseResult
HarborExecutionResult = runtime_support.HarborExecutionResult
PersistedArtifacts = runtime_support.PersistedArtifacts
RunLayout = runtime_support.RunLayout
RunRequest = runtime_support.RunRequest
ScorecardBuildContext = runtime_support.ScorecardBuildContext
WorkspaceContext = runtime_support.WorkspaceContext
BaselineWorkspaceRequest = runtime_support.BaselineWorkspaceRequest
_classify_unscored_reasons = runtime_support._classify_unscored_reasons
build_scorecard = runtime_support.build_scorecard
evaluate_coverage = runtime_support.evaluate_coverage
evaluate_requirements = runtime_support.evaluate_requirements
_build_verifier_scenario_spec = runtime_support._build_verifier_scenario_spec
_task_image_reference = runtime_support._task_image_reference
_verifier_scorer_script = runtime_support._verifier_scorer_script
_ensure_baseline_workspace = runtime_support._ensure_baseline_workspace
_prune_workspace_artifacts = runtime_support._prune_workspace_artifacts
_resolve_homepage_screenshot_command = runtime_support._resolve_homepage_screenshot_command
_workspace_changes_from_baseline = runtime_support._workspace_changes_from_baseline
create_harbor_task_bundle = runtime_support.create_harbor_task_bundle
scenario_evaluation_profile = runtime_support.scenario_evaluation_profile
StarterSource = runtime_support.StarterSource


class _RuntimeProxy:
    _modules = (
        workspace_runtime,
        workspace_artifacts_runtime,
        workspace_cache_runtime,
        artifacts_runtime,
        scorecard_runtime,
        task_bundle_runtime,
        process_metrics_runtime,
    )

    def __getattr__(self, name: str):
        for module in self._modules:
            if hasattr(module, name):
                return getattr(module, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value) -> None:
        patched = False
        for module in self._modules:
            if hasattr(module, name):
                setattr(module, name, value)
                patched = True
        if not patched:
            super().__setattr__(name, value)


runner = _RuntimeProxy()


@dataclass(frozen=True)
class BaselineWorkspaceFixture:
    scenario_dir: Path
    starter_dir: Path
    baseline_workspace_dir: Path


@dataclass(frozen=True)
class VerifierRunFixture:
    app_dir: Path
    logs_dir: Path
    tests_dir: Path


@dataclass(frozen=True)
class HarborBundleFixture:
    workspace: Path
    scenario_dir: Path
    results_dir: Path
    scenario: ScenarioDefinition


def _sample_scenario() -> ScenarioDefinition:
    return ScenarioDefinition.model_validate(_sample_scenario_doc())


def _sample_scenario_doc() -> dict[str, object]:
    return {
        "name": "homepage-implementation",
        "scenario_revision": "v001",
        "description": "test task",
        "difficulty": "medium",
        "category": "greenfield-ui",
        "timeout_sec": 1800,
        "starter": {"root": "starter"},
        "verification": _sample_verification_doc(),
        "requirements": {"items": [_sample_requirement_doc()]},
        "scorers": _sample_scorer_docs(),
        "visual": _sample_visual_doc(),
        "prompt": {"entry": "prompt/task.md"},
    }


def _sample_verification_doc() -> dict[str, object]:
    return {
        "setup_actions": [
            ["git", "init"],
            ["git", "config", "core.hooksPath", ".githooks"],
        ],
        "gates": [
            _verification_gate_doc("typecheck"),
            _verification_gate_doc("lint"),
        ],
        "required_commands": [["bun", "run", "build"]],
        "coverage_threshold": 0.8,
        "min_quality_score": 0.9,
        "workflow": {"atomic_commits_required": False},
    }


def _verification_gate_doc(name: str) -> dict[str, object]:
    return {
        "name": name,
        "command": ["bun", "run", name],
        "on_failure": "continue",
    }


def _sample_scorer_docs() -> list[dict[str, object]]:
    return [
        {
            "id": "typescript-code-task",
            "version": 1,
            "weight": 0.9,
            "config": {
                "artifact-checks": {
                    "required_paths": ["src/app/page.tsx"],
                    "path_match": "glob",
                }
            },
        },
        {"id": "resource-efficiency", "version": 1, "weight": 0.1},
    ]


def _sample_requirement_doc() -> dict[str, object]:
    return {
        "id": "req-marker",
        "description": "Marker text exists.",
        "check": {
            "type": "import_present",
            "pattern": "Ready",
            "description": "Marker text exists",
        },
        "required_test_evidence": [],
    }


def _sample_visual_doc() -> dict[str, object]:
    return {
        "reference_image": "./reference/homepage.png",
        "screenshot_command": ["bun", "run", "capture-screenshot"],
        "viewport": {"width": 1440, "height": 1024},
        "scoring": _sample_visual_scoring_doc(),
        "pass_policy": _sample_visual_pass_policy_doc(),
        "regions": [],
    }


def _sample_visual_scoring_doc() -> dict[str, object]:
    return {
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
    }


def _sample_visual_pass_policy_doc() -> dict[str, float | int]:
    return {
        "fail_if_global_below": 0.9,
        "fail_if_worst_region_below": 0.85,
        "minimum_score": 70,
        "minimum_region_pass_rate": 0.75,
        "minimum_worst_region": 0.88,
        "high_fidelity_score": 85,
        "high_fidelity_global": 0.95,
        "high_fidelity_worst_region": 0.92,
    }


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
        metric_scores=[],
        gate_history=[],
    )


def _sample_run_request(scenario_dir: Path, results_dir: Path) -> RunRequest:
    return RunRequest(
        scenario=_sample_scenario(),
        config=_sample_agent_config(),
        scenario_dir=scenario_dir,
        execution_dir=results_dir,
        repeat_index=1,
    )


def _sample_run_layout(results_dir: Path) -> RunLayout:
    return RunLayout(
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


def _sample_execution_phase(
    tmp_path: Path,
    workspace_dir: Path,
    *,
    terminated_early: bool,
    termination_reason: str | None,
) -> ExecutionPhaseResult:
    return ExecutionPhaseResult(
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
        auth_metadata={"auth_mode": "chatgpt", "auth_mode_requested": "auto"},
    )


def _sample_persisted_artifacts() -> PersistedArtifacts:
    return PersistedArtifacts(
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

    return ScorecardBuildContext(
        request=_sample_run_request(scenario_dir, results_dir),
        layout=_sample_run_layout(results_dir),
        context=_sample_workspace_context(
            workspace_dir,
            scenario_name="homepage-implementation",
        ),
        artifacts=_sample_persisted_artifacts(),
        execution=_sample_execution_phase(
            tmp_path,
            workspace_dir,
            terminated_early=terminated_early,
            termination_reason=termination_reason,
        ),
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


def _visual_bundle_scenario(reference_image: Path | str) -> ScenarioDefinition:
    return ScenarioDefinition.model_validate(
        {
            "name": "homepage-implementation",
            "scenario_revision": "v001",
            "description": "test task",
            "difficulty": "medium",
            "category": "greenfield-ui",
            "timeout_sec": 1800,
            "starter": {"root": "starter"},
            "verification": {"gates": [], "required_commands": [], "min_quality_score": 0.0},
            "visual": {
                "reference_image": str(reference_image),
                "screenshot_command": ["bun", "run", "capture-screenshot"],
                "scoring": _visual_bundle_scoring(),
                "pass_policy": _visual_bundle_pass_policy(),
                "regions": [_visual_bundle_region()],
            },
            "requirements": {"items": []},
            "scorers": [{"id": "resource-efficiency", "version": 1, "weight": 1.0}],
            "prompt": {"entry": "prompt/task.md"},
        }
    )


def _visual_bundle_scoring() -> dict[str, object]:
    return {
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
    }


def _visual_bundle_pass_policy() -> dict[str, object]:
    return {
        "fail_if_global_below": 0.9,
        "fail_if_worst_region_below": 0.85,
        "minimum_score": 70,
        "minimum_region_pass_rate": 0.75,
        "minimum_worst_region": 0.88,
        "high_fidelity_score": 85,
        "high_fidelity_global": 0.95,
        "high_fidelity_worst_region": 0.92,
    }


def _visual_bundle_region() -> dict[str, object]:
    return {
        "name": "nav",
        "weight": 1.0,
        "clip": {"x": 0, "y": 0, "width": 1200, "height": 120},
    }


def _visual_bundle_metrics() -> list[dict[str, str]]:
    return [
        {"type": "core", "id": "functional"},
        {"type": "core", "id": "requirements-coverage"},
        {"type": "core", "id": "verification-stability"},
        {"type": "core", "id": "execution-validity"},
        {"type": "core", "id": "resource-efficiency"},
        {"type": "core", "id": "visual-regression"},
    ]


def _assert_verifier_script_contains_contracts(score_script: str) -> None:
    expected_snippets = [
        "scenarioSpec.requirements?.items",
        "metric_scores",
        "verification_stability",
        r"const testPattern = /\.(test|spec)\.tsx?$/",
        "NEXT_TELEMETRY_DISABLED",
        "command_timings_sec",
        "hasWorkspaceTestFiles()",
        "No test files found, exiting with code 1",
        r"/(\d+)\s+passed/gi",
        r"/(\d+)\s+failed/gi",
        r"/([0-9]+(?:\.[0-9]+)?)\s*%/",
        "required_test_evidence",
        "countRoleQueryMatches",
        "missingTestEvidence",
    ]
    for snippet in expected_snippets:
        assert snippet in score_script


def _verifier_functional_payload() -> dict[str, object]:
    return {
        "passed": True,
        "tests_passed": 4,
        "tests_total": 4,
        "build_succeeded": True,
        "gates_passed": 4,
        "gates_total": 4,
    }


def _verifier_visual_region(name: str, similarity: float) -> dict[str, object]:
    return {
        "name": name,
        "weight": 0.5,
        "normalized_weight": 0.5,
        "similarity": similarity,
        "decent_pass": True,
        "actual_path": f"/tmp/run/visual/actual-region-{name}.png",
        "reference_path": f"/tmp/run/visual/reference-region-{name}.png",
        "diff_path": f"/tmp/run/visual/diff-region-{name}.png",
    }


def _verifier_visual_payload() -> dict[str, object]:
    return {
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
            _verifier_visual_region("hero", 0.98),
            _verifier_visual_region("footer", 0.91),
        ],
    }


def _verifier_stability_payload() -> dict[str, object]:
    return {
        "total_gate_failures": 0,
        "unique_failure_categories": 0,
        "repeat_failures": 0,
    }


def _verifier_coverage_payload() -> dict[str, object]:
    return {
        "threshold": 0.8,
        "measured": 0.9,
        "source": "coverage-summary",
        "passed": True,
    }


def _verifier_requirements_payload() -> dict[str, object]:
    return {
        "total_requirements": 1,
        "satisfied_requirements": 1,
        "mapped_requirements": 1,
        "missing_requirement_ids": [],
        "requirement_gap_ids": [],
    }


def _verifier_execution_validity_payload() -> dict[str, object]:
    return {"checks": [{"name": "run_completed", "passed": True, "evidence": "done"}]}


def _verifier_performance_payload() -> dict[str, object]:
    return {
        "checks": [
            {
                "name": "quality_gates_passed",
                "passed": True,
                "evidence": "2/2 gates passed",
            }
        ]
    }


def _verifier_gate_history_payload() -> list[dict[str, object]]:
    return [
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
    ]


def _verifier_metric_scores_payload() -> list[dict[str, object]]:
    return [
        {
            "metric_id": "artifact-checks",
            "score": 0.0,
            "passed": False,
            "matched_count": 0,
            "missing_patterns": ["src/components/**/*.tsx"],
            "evidence": "artifact-checks matches (src/components/**/*.tsx:0)",
        }
    ]


def _verifier_scorecard_payload(*, include_metric_scores: bool = True) -> dict[str, object]:
    payload: dict[str, object] = {
        "functional": _verifier_functional_payload(),
        "visual": _verifier_visual_payload(),
        "verification_stability": _verifier_stability_payload(),
        "test_coverage": _verifier_coverage_payload(),
        "requirements_coverage": _verifier_requirements_payload(),
        "execution_validity": _verifier_execution_validity_payload(),
        "performance_gates": _verifier_performance_payload(),
        "gate_history": _verifier_gate_history_payload(),
    }
    if include_metric_scores:
        payload["metric_scores"] = _verifier_metric_scores_payload()
    return payload


def _stale_verifier_scorecard_payload() -> dict[str, object]:
    return {
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


def _baseline_workspace_fixture(tmp_path: Path) -> BaselineWorkspaceFixture:
    scenario_dir = tmp_path / "scenario" / "v001"
    starter_dir = scenario_dir / "starter"
    starter_dir.mkdir(parents=True, exist_ok=True)
    return BaselineWorkspaceFixture(
        scenario_dir=scenario_dir,
        starter_dir=starter_dir,
        baseline_workspace_dir=(
            tmp_path / ".cache" / "raidar" / "prep" / "baselines" / "cache-key" / "workspace"
        ),
    )


def _baseline_workspace_request(fixture: BaselineWorkspaceFixture):
    return runner.BaselineWorkspaceRequest(
        scenario=_sample_scenario(),
        starter_dir=fixture.starter_dir,
        baseline_workspace_dir=fixture.baseline_workspace_dir,
        baseline_cache_key="cache-key",
        scenario_dir=fixture.scenario_dir,
        harness="codex-cli",
    )


def _patch_baseline_cache_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")


def _write_mismatched_baseline_metadata(fixture: BaselineWorkspaceFixture) -> None:
    metadata_path = fixture.baseline_workspace_dir.parent / "metadata.json"
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


def _write_codex_log(trial_dir: Path, entries: list[dict[str, object]]) -> None:
    harness_dir = trial_dir / "agent"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (harness_dir / "codex.txt").write_text("\n".join(json.dumps(entry) for entry in entries))


def _codex_command_entry(command: str, exit_code: int = 0) -> dict[str, object]:
    status = "completed" if exit_code == 0 else "failed"
    return {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "command": f"/bin/bash -lc '{command}'",
            "exit_code": exit_code,
            "status": status,
        },
    }


def _codex_usage_entry(
    input_tokens: int, cached_input_tokens: int, output_tokens: int
) -> dict[str, object]:
    return {
        "type": "turn.completed",
        "usage": {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
        },
    }


def _test_and_coverage_scenario() -> ScenarioDefinition:
    scenario_doc = _sample_scenario_doc()
    scenario_doc["verification"] = {
        "gates": [
            _verification_gate_doc("test"),
            _verification_gate_doc("test:coverage"),
        ],
        "required_commands": [],
        "min_quality_score": 0.0,
    }
    scenario_doc["scorers"] = [{"id": "resource-efficiency", "version": 1, "weight": 1.0}]
    scenario_doc.pop("visual")
    return ScenarioDefinition.model_validate(scenario_doc)


def _assert_codex_usage_and_failure_metrics(metrics) -> None:
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


def _claude_assistant_entry(commands: list[str], usage: dict[str, int]) -> dict[str, object]:
    return {
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "usage": usage,
            "content": [
                {
                    "type": "tool_use",
                    "id": f"toolu_{index}",
                    "name": "Bash",
                    "input": {"command": command},
                }
                for index, command in enumerate(commands)
            ],
        },
    }


def _claude_tool_results(count: int) -> dict[str, object]:
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": f"toolu_{index}",
                    "is_error": False,
                }
                for index in range(count)
            ]
        },
    }


def _write_claude_jsonl(path: Path, commands: list[str], usage: dict[str, int]) -> None:
    entries = [_claude_assistant_entry(commands, usage), _claude_tool_results(len(commands))]
    path.write_text("\n".join(json.dumps(entry) for entry in entries))


def _assert_claude_required_verification(metrics) -> None:
    assert metrics.command_count == 2
    assert metrics.required_verification_commands == 3
    assert metrics.executed_required_verification_commands == 2
    assert metrics.required_verification_first_pass["bun run typecheck"] == "pass"
    assert metrics.required_verification_first_pass["bun run lint"] == "pass"
    assert metrics.required_verification_first_pass["bun run build"] == "missing"


def _gate_event(index: int, gate_name: str, command: str) -> GateEvent:
    return GateEvent(
        timestamp=f"2026-01-01T00:00:0{index}Z",
        gate_name=gate_name,
        command=command,
        exit_code=0,
        stdout="",
        stderr="",
        failure_category=None,
        is_repeat=False,
    )


def _successful_required_gate_history() -> list[GateEvent]:
    return [
        _gate_event(0, "typecheck", "bun run typecheck"),
        _gate_event(1, "lint", "bun run lint"),
        _gate_event(2, "coverage", "bun run test:coverage"),
        _gate_event(3, "build", "bun run build"),
    ]


def _execution_validity_check(scorecard, name: str):
    return next(check for check in scorecard.execution_validity.checks if check.name == name)


def _score_context_with_gate_history(score_context, gate_history: list[GateEvent]):
    return replace(
        score_context,
        execution=replace(
            score_context.execution,
            process_metrics=replace(
                score_context.execution.process_metrics,
                required_verification_commands=3,
                executed_required_verification_commands=3,
            ),
            outputs=replace(score_context.execution.outputs, gate_history=gate_history),
        ),
    )


def _score_context_with_verifier_timings(score_context, trial_dir: Path):
    return replace(
        score_context,
        execution=replace(
            score_context.execution,
            harbor_result=replace(score_context.execution.harbor_result, trial_dir=trial_dir),
        ),
    )


def _write_verifier_timing_artifacts(trial_dir: Path, raw_timings) -> None:
    verifier_dir = trial_dir / "verifier"
    verifier_dir.mkdir(parents=True, exist_ok=True)
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "started_at": "2026-05-13T00:00:00",
                "finished_at": "2026-05-13T00:00:10",
            }
        ),
        encoding="utf-8",
    )
    (verifier_dir / "scorecard.json").write_text(
        json.dumps({"metadata": {"command_timings_sec": raw_timings}}),
        encoding="utf-8",
    )


def _verifier_fixture(tmp_path: Path) -> VerifierRunFixture:
    fixture = VerifierRunFixture(
        app_dir=tmp_path / "app",
        logs_dir=tmp_path / "logs",
        tests_dir=tmp_path / "tests",
    )
    fixture.logs_dir.mkdir(parents=True, exist_ok=True)
    fixture.tests_dir.mkdir(parents=True, exist_ok=True)
    fixture.app_dir.mkdir(parents=True, exist_ok=True)
    (fixture.app_dir / "package.json").write_text("{}", encoding="utf-8")
    (fixture.app_dir / "bun.lock").write_text("", encoding="utf-8")
    return fixture


def _write_verifier_spec(
    fixture: VerifierRunFixture, requirement_checks: list[dict[str, str]]
) -> Path:
    scenario_spec_path = fixture.tests_dir / "scenario-spec.json"
    scenario_spec_path.write_text(
        json.dumps(_verifier_spec_doc(requirement_checks), indent=2),
        encoding="utf-8",
    )
    return scenario_spec_path


def _verifier_spec_doc(requirement_checks: list[dict[str, str]]) -> dict[str, object]:
    return {
        "metrics": [],
        "verification": {
            "max_gate_failures": 3,
            "coverage_threshold": None,
            "min_quality_score": 0,
            "gates": [],
            "workflow": {"atomic_commits_required": False},
        },
        "requirements": {
            "items": [
                {
                    "id": f"req-{index}",
                    "description": check["description"],
                    "check": check,
                    "required_test_evidence": [],
                }
                for index, check in enumerate(requirement_checks, start=1)
            ],
        },
        "weights": {
            "functional": 0.25,
            "visual": 0.25,
            "verification_stability": 0.25,
        },
        "baseline_scripts": {},
    }


def _run_verifier_script(fixture: VerifierRunFixture, scenario_spec_path: Path):
    score_script = fixture.tests_dir / "score-scenario.mjs"
    score_script.write_text(runner._verifier_scorer_script(), encoding="utf-8")
    return subprocess.run(
        ["bun", str(score_script), str(scenario_spec_path)],
        cwd=fixture.tests_dir,
        env={
            **runner.os.environ,
            "RAIDAR_APP_DIR": str(fixture.app_dir),
            "RAIDAR_LOG_DIR": str(fixture.logs_dir),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def _verifier_scorecard(fixture: VerifierRunFixture) -> dict[str, object]:
    return json.loads((fixture.logs_dir / "scorecard.json").read_text(encoding="utf-8"))


def _simple_bundle_scenario() -> ScenarioDefinition:
    scenario_doc = _sample_scenario_doc()
    scenario_doc.update(
        {
            "name": "hello-world-smoke",
            "description": "test task",
            "difficulty": "easy",
            "verification": {
                **scenario_doc["verification"],
                "min_quality_score": 0.0,
            },
            "scorers": [{"id": "resource-efficiency", "version": 1, "weight": 1.0}],
        }
    )
    scenario_doc.pop("visual")
    return ScenarioDefinition.model_validate(scenario_doc)


def _standard_core_metric_docs() -> list[dict[str, str]]:
    return [
        {"type": "core", "id": "functional"},
        {"type": "core", "id": "requirements-coverage"},
        {"type": "core", "id": "verification-stability"},
        {"type": "core", "id": "execution-validity"},
        {"type": "core", "id": "resource-efficiency"},
    ]


def _harbor_bundle_fixture(tmp_path: Path, *, prompt: str = "Print hello world\n"):
    fixture = HarborBundleFixture(
        workspace=tmp_path / "workspace",
        scenario_dir=tmp_path / "scenario",
        results_dir=tmp_path / "results",
        scenario=_simple_bundle_scenario(),
    )
    fixture.scenario_dir.mkdir(parents=True, exist_ok=True)
    fixture.results_dir.mkdir(parents=True, exist_ok=True)
    _seed_workspace_tree(fixture.workspace)
    (fixture.scenario_dir / "scenario.yaml").write_text(
        "name: hello-world-smoke\nscenario_revision: v001\n"
    )
    (fixture.scenario_dir / "prompt").mkdir(parents=True, exist_ok=True)
    (fixture.scenario_dir / "prompt" / "task.md").write_text(prompt)
    return fixture


def _create_bundle(fixture: HarborBundleFixture, harness: Harness = Harness.CODEX_CLI):
    request = _bundle_run_request(fixture, harness)
    context = _sample_workspace_context(fixture.workspace, scenario_name="hello-world-smoke")
    return create_harbor_task_bundle(
        request,
        context,
        bundle_root=fixture.results_dir / "runs" / "run-01" / "harbor" / "bundle",
    )


def _bundle_run_request(
    fixture: HarborBundleFixture, harness: Harness = Harness.CODEX_CLI
) -> RunRequest:
    request = RunRequest(
        scenario=fixture.scenario,
        config=AgentSpec(
            harness=harness,
            model=_bundle_model_target(harness),
            timeout_sec=1800,
        ),
        scenario_dir=fixture.scenario_dir,
        execution_dir=fixture.results_dir,
        repeat_index=1,
    )
    return request


def _bundle_model_target(harness: Harness) -> ModelTarget:
    if harness == Harness.GEMINI:
        return ModelTarget(provider="google", name="gemini-3-flash-preview")
    return ModelTarget(provider="openai", name="gpt-5.5", reasoning_effort="low")


def test_ensure_baseline_workspace_initializes_once_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _baseline_workspace_fixture(tmp_path)
    call_count = 0
    call_lock = threading.Lock()
    start_barrier = threading.Barrier(3)

    _patch_baseline_cache_lock(monkeypatch, tmp_path)

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

    monkeypatch.setattr("raidar.runtime.workspace.prepare_workspace", fake_prepare_workspace)

    failures: list[Exception] = []

    def _run() -> None:
        try:
            start_barrier.wait(timeout=1.0)
            _ensure_baseline_workspace(_baseline_workspace_request(fixture))
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
    fixture = _baseline_workspace_fixture(tmp_path)
    baseline_workspace_dir = fixture.baseline_workspace_dir
    setup_calls: list[list[str]] = []

    _patch_baseline_cache_lock(monkeypatch, tmp_path)

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

    monkeypatch.setattr("raidar.runtime.workspace.prepare_workspace", fake_prepare_workspace)
    monkeypatch.setattr(
        "raidar.runtime.starter_preflight._run_workspace_setup_actions",
        fake_run_setup_actions,
    )

    _ensure_baseline_workspace(_baseline_workspace_request(fixture))

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
    fixture = _baseline_workspace_fixture(tmp_path)
    baseline_workspace_dir = fixture.baseline_workspace_dir
    baseline_workspace_dir.mkdir(parents=True, exist_ok=True)
    (baseline_workspace_dir / "partial.txt").write_text("stale\n", encoding="utf-8")

    prepare_calls = 0
    setup_calls = 0

    _patch_baseline_cache_lock(monkeypatch, tmp_path)

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

    monkeypatch.setattr("raidar.runtime.workspace.prepare_workspace", fake_prepare_workspace)
    monkeypatch.setattr(
        "raidar.runtime.starter_preflight._run_workspace_setup_actions",
        fake_run_setup_actions,
    )

    cache_result = _ensure_baseline_workspace(_baseline_workspace_request(fixture))

    assert cache_result.hit is False
    assert cache_result.status == "invalidated"
    assert prepare_calls == 1
    assert setup_calls == 1
    assert not (baseline_workspace_dir / "partial.txt").exists()
    assert (baseline_workspace_dir.parent / "metadata.json").exists()


def test_ensure_baseline_workspace_rebuilds_fingerprint_mismatch_cache_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _baseline_workspace_fixture(tmp_path)
    baseline_workspace_dir = fixture.baseline_workspace_dir
    baseline_workspace_dir.mkdir(parents=True, exist_ok=True)
    (baseline_workspace_dir / "partial.txt").write_text("tampered\n", encoding="utf-8")
    _write_mismatched_baseline_metadata(fixture)

    prepare_calls = 0

    _patch_baseline_cache_lock(monkeypatch, tmp_path)

    def fake_prepare_workspace(
        starter_dir: Path, target_dir: Path, scenario_dir: Path, harness: str
    ) -> tuple[Path, Path | None]:
        del starter_dir, scenario_dir, harness
        nonlocal prepare_calls
        prepare_calls += 1
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "fresh.txt").write_text("ready\n", encoding="utf-8")
        return target_dir, None

    monkeypatch.setattr("raidar.runtime.workspace.prepare_workspace", fake_prepare_workspace)
    monkeypatch.setattr(
        "raidar.runtime.starter_preflight._run_workspace_setup_actions",
        lambda **_kwargs: None,
    )

    cache_result = _ensure_baseline_workspace(_baseline_workspace_request(fixture))

    assert cache_result.hit is False
    assert cache_result.status == "invalidated"
    assert prepare_calls == 1
    assert not (baseline_workspace_dir / "partial.txt").exists()
    assert (baseline_workspace_dir / "fresh.txt").exists()


def test_collect_process_metrics_extracts_usage_and_failures(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    _write_codex_log(
        trial_dir,
        [
            _codex_command_entry("bun run typecheck"),
            _codex_command_entry("bun run build", exit_code=1),
            _codex_usage_entry(1000, 250, 100),
        ],
    )

    metrics = collect_process_metrics(_sample_scenario(), trial_dir, harness="codex-cli")

    _assert_codex_usage_and_failure_metrics(metrics)


def test_collect_process_metrics_distinguishes_test_and_coverage(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    _write_codex_log(
        trial_dir,
        [
            _codex_command_entry("bun run test"),
            _codex_command_entry("bun run test:coverage"),
            _codex_usage_entry(10, 0, 5),
        ],
    )

    metrics = collect_process_metrics(_test_and_coverage_scenario(), trial_dir, harness="codex-cli")

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
    _write_claude_jsonl(
        command_dir / "stdout.txt",
        ["bunx tsc --noEmit", "npm run lint"],
        {"input_tokens": 70, "cache_read_input_tokens": 20, "output_tokens": 9},
    )

    metrics = collect_process_metrics(_sample_scenario(), trial_dir, harness="claude-code")

    assert metrics.failed_command_count == 0
    _assert_claude_required_verification(metrics)


def test_collect_process_metrics_extracts_claude_bash_from_top_level_log(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    harness_dir = trial_dir / "agent"
    harness_dir.mkdir(parents=True, exist_ok=True)
    _write_claude_jsonl(
        harness_dir / "claude-code.txt",
        ["bun run typecheck", "bun run lint"],
        {"input_tokens": 50, "cache_read_input_tokens": 0, "output_tokens": 7},
    )

    metrics = collect_process_metrics(_sample_scenario(), trial_dir, harness="claude-code")

    _assert_claude_required_verification(metrics)


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


def test_collect_process_metrics_detects_git_commit_bypass_commands(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    codex_log = agent_dir / "codex.txt"
    entries = [
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": (
                    "/bin/bash -lc 'git add src/app/page.tsx && "
                    'git commit --no-verify -m "feat: bypass hooks"\''
                ),
                "exit_code": 0,
                "status": "completed",
            },
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 12,
                "cached_input_tokens": 0,
                "output_tokens": 6,
            },
        },
    ]
    codex_log.write_text("\n".join(json.dumps(entry) for entry in entries), encoding="utf-8")

    metrics = collect_process_metrics(_sample_scenario(), trial_dir, harness="codex-cli")

    assert metrics.git_commit_verification_bypass_commands == [
        "git commit --no-verify -m 'feat: bypass hooks'"
    ]


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


def test_evaluate_coverage_zero_threshold_does_not_require_measurement(tmp_path: Path):
    score = evaluate_coverage(tmp_path, gate_history=[], threshold=0)

    assert score.threshold == 0
    assert score.measured is None
    assert score.passed is True


def test_coverage_profile_score_treats_zero_threshold_as_no_minimum():
    scorecard = Scorecard(
        test_coverage=CoverageScore(threshold=0, measured=None, passed=True),
    )

    assert scorecard.metric_score("test-coverage") == 1.0


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
            required_test_evidence=[
                {"type": "query_role", "role": "button"},
                {"type": "query_role", "role": "heading"},
            ],
        )
    ]

    result = evaluate_requirements(workspace, requirements)
    assert result.total_requirements == 1
    assert result.satisfied_requirements == 1
    assert result.mapped_requirements == 0
    assert result.mapped_satisfied_requirements == 0
    assert result.requirement_gap_ids == ["req-cta"]
    assert result.requirement_test_evidence_gaps == {
        "req-cta": ["query_role:button x1", "query_role:heading x1"]
    }


def test_evaluate_requirements_matches_role_queries_and_counts(tmp_path: Path):
    workspace = tmp_path / "workspace"
    src_app = workspace / "src" / "app"
    src_app.mkdir(parents=True, exist_ok=True)
    (src_app / "page.tsx").write_text(
        "export default function Home(){ return <h1>Get Started</h1>; }"
    )
    (src_app / "page.test.tsx").write_text(
        "it('renders nav', () => {"
        " screen.getByRole('navigation');"
        " screen.getAllByRole('link');"
        " screen.getAllByRole('link');"
        " screen.getByRole('button');"
        "})"
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
            required_test_evidence=[
                {"type": "query_role", "role": "navigation"},
                {"type": "query_role", "role": "link", "min_count": 2},
                {"type": "query_role", "role": "button"},
            ],
        )
    ]

    result = evaluate_requirements(workspace, requirements)
    assert result.total_requirements == 1
    assert result.satisfied_requirements == 1
    assert result.mapped_requirements == 1
    assert result.mapped_satisfied_requirements == 1
    assert result.requirement_gap_ids == []
    assert result.requirement_test_evidence_gaps == {}


def test_load_verifier_outputs_parses_scorecard(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    verifier_dir = trial_dir / "verifier"
    verifier_dir.mkdir(parents=True, exist_ok=True)
    scorecard_path = verifier_dir / "scorecard.json"
    scorecard_path.write_text(json.dumps(_verifier_scorecard_payload()))

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
    assert outputs.metric_scores == [
        MetricScore(
            metric_id="artifact-checks",
            score=0.0,
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
        json.dumps(_verifier_scorecard_payload(include_metric_scores=False))
    )
    outputs, reason = _load_verifier_outputs(trial_dir)
    assert outputs is None
    assert reason is not None
    assert "scorecard.metric_scores must be a list" in reason


def test_load_verifier_outputs_missing_scorecard(tmp_path: Path):
    outputs, reason = _load_verifier_outputs(tmp_path / "missing")
    assert outputs is None
    assert reason is not None


def test_scenario_evaluation_profile_uses_ordered_metrics():
    scenario = _sample_scenario()
    assert scenario_evaluation_profile(scenario) == (
        "scorers:typescript-code-task@1:0.9+resource-efficiency@1:0.1"
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
        {"type": "core", "id": "code-quality"},
        {"type": "core", "id": "test-coverage"},
        {
            "type": "artifact-checks",
            "id": "artifact-checks",
            "config": {"required_paths": ["src/app/page.tsx"], "path_match": "glob"},
        },
        {"type": "core", "id": "verification-stability"},
        {"type": "core", "id": "resource-efficiency"},
    ]
    assert scenario_spec["scorers"][0]["id"] == "typescript-code-task"
    assert scenario_spec["visual"]["viewport"] == {"width": 1440, "height": 1024}
    assert scenario_spec["visual"]["scoring"]["weights"]["global"] == 0.25
    assert scenario_spec["visual"]["pass_policy"]["minimum_score"] == 70
    assert scenario_spec["verification"]["workflow"] == {"atomic_commits_required": False}


def test_build_scorecard_carries_scorer_results(tmp_path: Path):
    score_context = _sample_scorecard_context(
        tmp_path=tmp_path,
        terminated_early=False,
        termination_reason=None,
    )

    scorecard = build_scorecard(score_context)

    assert [result.scorer_id for result in scorecard.scorer_results] == [
        "typescript-code-task",
        "resource-efficiency",
    ]
    assert scorecard.metric_score("resource-efficiency") == scorecard.resource_efficiency.score


def test_build_metric_scores_does_not_use_global_metric_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    score_context = _sample_scorecard_context(
        tmp_path=tmp_path,
        terminated_early=False,
        termination_reason=None,
    )

    class EmptyScorer:
        def collect_evidence(self, _context):
            return SimpleNamespace(metric_scores=())

    monkeypatch.setattr(
        scoring_outputs_runtime,
        "scorer_class",
        lambda _scorer_id, _version: EmptyScorer,
    )

    scorecard = build_scorecard(score_context)

    assert scorecard.metric_scores
    assert all(
        score.evidence == f"Selected scorer did not emit metric: {score.metric_id}"
        for score in scorecard.metric_scores
    )


def test_build_scorecard_recomputes_minimum_quality_gate_from_scorers(tmp_path: Path):
    score_context = _sample_scorecard_context(
        tmp_path=tmp_path,
        terminated_early=False,
        termination_reason=None,
    )
    score_context.request.scenario.verification.min_quality_score = 0.95

    scorecard = build_scorecard(score_context)

    quality_gate = next(
        check
        for check in scorecard.performance_gates.checks
        if check.name == "minimum_quality_score"
    )
    assert quality_gate.passed is False
    assert quality_gate.evidence == "quality=0.425, min=0.950"


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
    monkeypatch.setattr(
        "raidar.runtime.scorecard._git_commit_count", lambda _: (0, "commit_count=0")
    )

    scorecard = build_scorecard(score_context)

    atomic_check = next(
        check
        for check in scorecard.execution_validity.checks
        if check.name == "atomic_commits_present"
    )
    assert atomic_check.passed is False
    assert scorecard.execution_validity.passed is False


def test_build_scorecard_accepts_observed_required_verification_from_gate_history(
    tmp_path: Path,
) -> None:
    score_context = _sample_scorecard_context(
        tmp_path=tmp_path,
        terminated_early=False,
        termination_reason=None,
    )
    score_context = _score_context_with_gate_history(
        score_context, _successful_required_gate_history()
    )

    scorecard = build_scorecard(score_context)

    verification_check = _execution_validity_check(
        scorecard, "required_verification_commands_executed"
    )
    assert verification_check.passed is True
    assert verification_check.evidence == "observed=3/3, explicit=3/3"


def test_build_scorecard_fails_when_git_commit_bypasses_verification_hooks(
    tmp_path: Path,
) -> None:
    score_context = _sample_scorecard_context(
        tmp_path=tmp_path,
        terminated_early=False,
        termination_reason=None,
    )
    score_context = replace(
        score_context,
        execution=replace(
            score_context.execution,
            process_metrics=replace(
                score_context.execution.process_metrics,
                git_commit_verification_bypass_commands=[
                    "git commit --no-verify -m 'feat: bypass'"
                ],
            ),
        ),
    )

    scorecard = build_scorecard(score_context)

    bypass_check = next(
        check
        for check in scorecard.execution_validity.checks
        if check.name == "commit_verification_hooks_not_bypassed"
    )
    assert bypass_check.passed is False
    assert "git commit --no-verify" in str(bypass_check.evidence)
    assert scorecard.execution_validity.passed is False


def test_build_scorecard_records_prep_timings_and_cache_metadata(tmp_path: Path) -> None:
    score_context = _sample_scorecard_context(
        tmp_path=tmp_path,
        terminated_early=False,
        termination_reason=None,
    )
    trial_dir = tmp_path / "jobs" / "orchestrator-run-1234" / "trial-01"
    raw_timings = {
        "functional_build": 11.423,
        "functional_test": 0.357,
        "gates": [{"gate_name": "lint", "command": "bun run lint", "duration_sec": 1.234}],
    }
    _write_verifier_timing_artifacts(trial_dir, raw_timings)
    score_context = _score_context_with_verifier_timings(score_context, trial_dir)

    scorecard = build_scorecard(score_context)
    harbor_meta = scorecard.metadata["harbor"]

    assert harbor_meta["prep_phase_timings_sec"] == {"prepare_run_context": 0.123}
    assert harbor_meta["prep_total_sec"] == 0.456
    assert harbor_meta["orchestration_overhead_excluding_test_sec"] == 2.5
    assert harbor_meta["harness_overhead_sec"] == 2.5
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
    assert harbor_meta["auth"]["auth_mode"] == "chatgpt"
    assert scorecard.metadata["verifier"]["command_timings_sec"] == raw_timings


def test_verifier_file_exists_glob_matches_direct_and_nested_section_files(
    tmp_path: Path,
) -> None:
    fixture = _verifier_fixture(tmp_path)
    (fixture.app_dir / "src" / "components" / "sections").mkdir(parents=True, exist_ok=True)
    (fixture.app_dir / "src" / "components" / "sections" / "Hero.tsx").write_text(
        "export function Hero() { return null; }\n",
        encoding="utf-8",
    )
    (fixture.app_dir / "src" / "components" / "sections" / "nested").mkdir(
        parents=True, exist_ok=True
    )
    (fixture.app_dir / "src" / "components" / "sections" / "nested" / "Footer.tsx").write_text(
        "export function Footer() { return null; }\n",
        encoding="utf-8",
    )
    scenario_spec_path = _write_verifier_spec(
        fixture,
        [
            {
                "type": "file_exists",
                "pattern": "src/components/sections/**/*.tsx",
                "description": "direct or nested section component exists",
            }
        ],
    )

    completed = _run_verifier_script(fixture, scenario_spec_path)

    assert completed.returncode == 0, completed.stderr
    scorecard = _verifier_scorecard(fixture)
    assert scorecard["requirements_coverage"]["missing_requirement_ids"] == []


def test_verifier_rejects_unsafe_no_pattern_regex(tmp_path: Path) -> None:
    fixture = _verifier_fixture(tmp_path)
    (fixture.app_dir / "src").mkdir(parents=True, exist_ok=True)
    (fixture.app_dir / "src" / "App.tsx").write_text(
        "export const value = 'aaaa';\n", encoding="utf-8"
    )
    scenario_spec_path = _write_verifier_spec(
        fixture,
        [
            {
                "type": "no_pattern",
                "pattern": "(a+)+$",
                "description": "unsafe regex is rejected",
            }
        ],
    )

    completed = _run_verifier_script(fixture, scenario_spec_path)

    assert completed.returncode == 0, completed.stderr
    scorecard = _verifier_scorecard(fixture)
    assert scorecard["requirements_coverage"]["missing_requirement_ids"] == ["req-1"]


def test_verifier_no_pattern_uses_bounded_literal_matching(tmp_path: Path) -> None:
    fixture = _verifier_fixture(tmp_path)
    (fixture.app_dir / "src").mkdir(parents=True, exist_ok=True)
    (fixture.app_dir / "src" / "App.tsx").write_text(
        "export const value = 'ok';\n", encoding="utf-8"
    )
    scenario_spec_path = _write_verifier_spec(
        fixture,
        [
            {
                "type": "no_pattern",
                "pattern": r"console\.log\(",
                "description": "literal console logging is absent",
            },
            {
                "type": "no_pattern",
                "pattern": r"console\.(log|warn)\(",
                "description": "regex alternation is rejected",
            },
        ],
    )

    completed = _run_verifier_script(fixture, scenario_spec_path)

    assert completed.returncode == 0, completed.stderr
    scorecard = _verifier_scorecard(fixture)
    assert scorecard["requirements_coverage"]["missing_requirement_ids"] == ["req-2"]


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
        json.dumps(_stale_verifier_scorecard_payload()),
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

    scenario = _visual_bundle_scenario(reference_rel)
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
    assert scenario_spec["visual"]["regions"] == [_visual_bundle_region()]
    assert (
        (bundle / "tests" / "score-scenario.mjs")
        .read_text(encoding="utf-8")
        .startswith("#!/usr/bin/env bun")
    )
    score_script = (bundle / "tests" / "score-scenario.mjs").read_text(encoding="utf-8")
    _assert_verifier_script_contains_contracts(score_script)


def test_create_harbor_task_bundle_sets_task_image_and_cli_install(tmp_path: Path):
    fixture = _harbor_bundle_fixture(tmp_path)
    bundle = _create_bundle(fixture)
    task_toml = (bundle / "task.toml").read_text()
    dockerfile = (bundle / "environment" / "Dockerfile").read_text()

    assert 'docker_image = "raidar-task-env:task-env-codex-cli-' in task_toml
    assert "git \\" in dockerfile
    assert "@openai/codex" in dockerfile
    assert "@anthropic-ai/claude-code" not in dockerfile
    assert "@google/gemini-cli" not in dockerfile


def test_create_harbor_task_bundle_uses_injected_rules_filename_in_instruction(tmp_path: Path):
    fixture = _harbor_bundle_fixture(tmp_path)
    (fixture.workspace / "GEMINI.md").write_text("gemini rules\n", encoding="utf-8")
    request = _bundle_run_request(fixture, Harness.GEMINI)
    context = replace(
        _sample_workspace_context(fixture.workspace, scenario_name="hello-world-smoke"),
        injected_rules=fixture.workspace / "GEMINI.md",
    )

    bundle = create_harbor_task_bundle(
        request,
        context,
        bundle_root=fixture.results_dir / "runs" / "run-01" / "harbor" / "bundle",
    )

    instruction = (bundle / "instruction.md").read_text(encoding="utf-8")

    assert "You are working in `/app`." in instruction
    assert "Follow rules in `/app/GEMINI.md`." in instruction
    assert "Avoid broad dependency-directory inspection such as `node_modules`" in instruction
    assert "Do not emit progress updates" in instruction
    assert "Follow rules in `/app/AGENTS.md`." not in instruction


def test_create_harbor_task_bundle_omits_redundant_codex_rules_reference(tmp_path: Path):
    fixture = _harbor_bundle_fixture(tmp_path, prompt="Change the page\n")
    (fixture.workspace / "AGENTS.md").write_text("Run verification commands.\n")
    request = _bundle_run_request(fixture)
    context = _sample_workspace_context(fixture.workspace, scenario_name="hello-world-smoke")

    bundle = create_harbor_task_bundle(
        request,
        context,
        bundle_root=fixture.results_dir / "runs" / "run-01" / "harbor" / "bundle",
    )

    instruction = (bundle / "instruction.md").read_text(encoding="utf-8")
    image_ref = runner._task_image_reference(request, bundle)
    task_toml = (bundle / "task.toml").read_text(encoding="utf-8")

    assert "You are working in `/app`." in instruction
    assert "Follow rules in `/app/AGENTS.md`." not in instruction
    assert "Avoid broad dependency-directory inspection such as `node_modules`" in instruction
    assert image_ref is not None
    assert image_ref.image_name in task_toml


def test_resolve_homepage_screenshot_command_uses_visual_override(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "package.json").write_text("{}\n")

    scenario = _sample_scenario()

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
            "verification": {"gates": [], "required_commands": [], "min_quality_score": 0.0},
            "requirements": {"items": []},
            "scorers": [{"id": "resource-efficiency", "version": 1, "weight": 1.0}],
            "prompt": {"entry": "prompt/task.md"},
        }
    )

    command = _resolve_homepage_screenshot_command(scenario, workspace)
    assert command is None


def test_ensure_workspace_capture_dependencies_installs_when_next_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "package.json").write_text('{"name":"app"}\n')
    (workspace / "bun.lock").write_text("lockfileVersion = 1\n")

    calls: list[tuple[list[str], Path]] = []

    def fake_run(command, **kwargs):
        calls.append((command, Path(kwargs["cwd"])))
        (workspace / "node_modules" / "next").mkdir(parents=True, exist_ok=True)
        (workspace / "node_modules" / "next" / "package.json").write_text("{}\n")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    error = runner._ensure_workspace_capture_dependencies(workspace)

    assert error is None
    assert calls == [(["bun", "install", "--frozen-lockfile"], workspace)]


def test_ensure_workspace_capture_dependencies_skips_when_next_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "node_modules" / "next").mkdir(parents=True, exist_ok=True)
    (workspace / "node_modules" / "next" / "package.json").write_text("{}\n")
    (workspace / "package.json").write_text('{"name":"app"}\n')
    (workspace / "bun.lock").write_text("lockfileVersion = 1\n")

    def fail_run(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr(subprocess, "run", fail_run)

    error = runner._ensure_workspace_capture_dependencies(workspace)

    assert error is None


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


def test_prune_workspace_artifacts_retries_transient_enotempty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "node_modules"
    (target / "next" / "dist").mkdir(parents=True, exist_ok=True)
    (target / "next" / "dist" / "server.js").write_text("console.log('x')\n")

    original_rmtree = runner.shutil.rmtree
    calls: list[Path] = []

    def flaky_rmtree(path: Path) -> None:
        calls.append(Path(path))
        if len(calls) == 1:
            raise OSError(errno.ENOTEMPTY, "Directory not empty", str(path))
        original_rmtree(path)

    monkeypatch.setattr(runner.shutil, "rmtree", flaky_rmtree)

    prune = _prune_workspace_artifacts(workspace)

    assert prune["removed"] == ["node_modules"]
    assert len(calls) == 2
    assert not target.exists()


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
