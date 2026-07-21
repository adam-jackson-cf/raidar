"""Tests for execution-validity and resource-efficiency helpers."""

from types import SimpleNamespace

import pytest

from raidar.runtime import scoring_outputs as scoring_outputs_runtime
from raidar.runtime.profile import RuntimeProfile
from raidar.schemas.scorecard import Scorecard
from raidar.scorers.code_task.typescript_evidence import (
    evaluate_typescript_coverage,
    evaluate_typescript_requirements,
)
from raidar.scorers.evidence_contract import CommandEvidenceResult
from tests import runtime_process_metrics_support as process_support
from tests import runtime_scorecard_workspace_support as runtime_support

RUNTIME_PROFILE = RuntimeProfile()

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
HarborResultManifest = process_support.HarborResultManifest
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
task_bundle_images_runtime = runtime_support.task_bundle_images_runtime
workspace_runtime = runtime_support.workspace_runtime
workspace_artifacts_runtime = runtime_support.workspace_artifacts_runtime
workspace_cache_runtime = runtime_support.workspace_cache_runtime
runtime_environments = runtime_support.runtime_environments
_load_evidence_outputs = runtime_support._load_evidence_outputs
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
task_image_reference = runtime_support.task_image_reference
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
        task_bundle_images_runtime,
        process_metrics_runtime,
        runtime_environments,
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
        "display_name": "Homepage Implementation",
        "scenario_revision": "v001",
        "description": "test task",
        "difficulty": "medium",
        "task_kind": "design_implementation",
        "delivery_mode": "direct_implementation",
        "workspace_state": "new",
        "starting_conditions": [],
        "languages": ["TypeScript"],
        "evaluation_role": "benchmark",
        "catalog_status": "active",
        "variant_axes": {},
        "timeout_sec": 1800,
        "environment": _web_environment_doc(),
        "starter": {"root": "starter"},
        "verification": _sample_verification_doc(),
        "requirements": {"items": [_sample_requirement_doc()]},
        "scorers": _sample_scorer_docs(),
        "visual": _sample_visual_doc(),
        "prompt": {"entry": "prompt/task.md"},
    }


def _web_environment_doc() -> dict[str, object]:
    return {
        "kind": "stack_preset",
        "id": "node:20",
        "workdir": "/app",
        "requirements": {
            "runtimes": {"node": ">=20"},
            "package_managers": {"bun": ">=1"},
            "tools": {
                "git": ">=2",
                "typescript": ">=5",
                "playwright": ">=1",
                "odiff": ">=0",
            },
            "browsers": {"chromium": "installed"},
        },
        "resources": {
            "cpus": 2,
            "memory_mb": 4096,
            "storage_mb": 10240,
        },
        "allow_internet": True,
    }


def _typescript_environment_doc() -> dict[str, object]:
    return {
        "kind": "stack_preset",
        "id": "node:20",
        "workdir": "/app",
        "requirements": {
            "runtimes": {"node": ">=20"},
            "package_managers": {"bun": ">=1"},
            "tools": {"typescript": ">=5"},
            "browsers": {},
        },
        "resources": {
            "cpus": 2,
            "memory_mb": 4096,
            "storage_mb": 10240,
        },
        "allow_internet": True,
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
        "artifact_manifest": {
            "actual_image": ".raidar/visual/homepage-actual.png",
            "diff_image": ".raidar/visual/homepage-diff.png",
            "post_capture_image": "post-run-homepage.png",
        },
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
        execution_validity=ExecutionValidityScore(),
        performance_gates=PerformanceGatesScore(),
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
        runner_evidence_dir=results_dir / "runs" / "run-1234" / "runner-evidence",
        scoring_dir=results_dir / "runs" / "run-1234" / "scoring",
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
    failure_code: str | None = None,
) -> ExecutionPhaseResult:
    return ExecutionPhaseResult(
        harbor_result=HarborExecutionResult(
            terminated_early=terminated_early,
            termination_reason=termination_reason,
            job_dir=tmp_path / "jobs" / "orchestrator-run-1234",
            trial_dir=None,
            failure_code=failure_code,
        ),
        terminated_early=terminated_early,
        termination_reason=termination_reason,
        process_metrics=collect_process_metrics(_sample_scenario(), None),
        events=[],
        outputs=_sample_evaluation_outputs(),
        duration_sec=12.5,
        prep_phase_timings_sec={"prepare_run_context": 0.123},
        prep_total_sec=0.456,
        time_to_experiment_start_sec=0.789,
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
        failure_code=failure_code,
    )


def _sample_persisted_artifacts() -> PersistedArtifacts:
    return PersistedArtifacts(
        starter_meta={"scenario": "homepage-implementation", "scenario_revision": "v001"},
        scenario_revision_meta={"scenario_yaml_hash": "abc"},
        runner_evidence_artifacts={},
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
    failure_code: str | None = None,
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
            failure_code=failure_code,
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
            "display_name": "Homepage Implementation",
            "scenario_revision": "v001",
            "description": "test task",
            "difficulty": "medium",
            "task_kind": "design_implementation",
            "delivery_mode": "direct_implementation",
            "workspace_state": "new",
            "starting_conditions": [],
            "languages": ["TypeScript"],
            "evaluation_role": "benchmark",
            "catalog_status": "active",
            "variant_axes": {},
            "timeout_sec": 1800,
            "environment": _web_environment_doc(),
            "starter": {"root": "starter"},
            "verification": {
                "gates": [],
                "required_commands": [],
                "coverage_threshold": 0.8,
                "min_quality_score": 0.0,
            },
            "visual": {
                "reference_image": str(reference_image),
                "screenshot_command": ["bun", "run", "capture-screenshot"],
                "artifact_manifest": {
                    "actual_image": ".raidar/visual/homepage-actual.png",
                    "diff_image": ".raidar/visual/homepage-diff.png",
                    "post_capture_image": "post-run-homepage.png",
                },
                "scoring": _visual_bundle_scoring(),
                "pass_policy": _visual_bundle_pass_policy(),
                "regions": [_visual_bundle_region()],
            },
            "requirements": {"items": []},
            "scorers": [
                {"id": "design-to-code", "version": 1, "weight": 0.9},
                {"id": "resource-efficiency", "version": 1, "weight": 0.1},
            ],
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
        "reference_image": "references/hero-region-nav.png",
        "actual_image": ".raidar/visual/homepage-nav-actual.png",
        "diff_image": ".raidar/visual/homepage-nav-diff.png",
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


def _stale_canonical_scorecard_payload() -> dict[str, object]:
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
        runtime_profile=RUNTIME_PROFILE,
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


def _score_context_with_evidence_metadata(score_context, trial_dir: Path):
    evidence_path = trial_dir / "verifier" / "evidence.json"
    return replace(
        score_context,
        artifacts=replace(
            score_context.artifacts,
            runner_evidence_artifacts={"evidence.json": str(evidence_path)},
        ),
        execution=replace(
            score_context.execution,
            harbor_result=replace(score_context.execution.harbor_result, trial_dir=trial_dir),
        ),
    )


def _write_evidence_metadata_artifacts(trial_dir: Path, action_count: int) -> None:
    verifier_dir = trial_dir / "verifier"
    verifier_dir.mkdir(parents=True, exist_ok=True)
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "started_at": "2026-05-13T00:00:00",
                "finished_at": "2026-05-13T00:00:10",
                "environment_setup": {
                    "started_at": "2026-05-13T00:00:00",
                    "finished_at": "2026-05-13T00:00:01",
                },
                "agent_setup": {
                    "started_at": "2026-05-13T00:00:01",
                    "finished_at": "2026-05-13T00:00:02",
                },
                "agent_execution": {
                    "started_at": "2026-05-13T00:00:02",
                    "finished_at": "2026-05-13T00:00:08",
                },
                "verifier": {
                    "started_at": "2026-05-13T00:00:08",
                    "finished_at": "2026-05-13T00:00:09",
                },
            }
        ),
        encoding="utf-8",
    )
    actions = [
        {
            "id": f"test.action-{index}",
            "kind": "command",
            "required": True,
            "status": "ok",
            "duration_sec": 0.1,
            "result": {
                "command": ["true"],
                "cwd": ".",
                "duration_sec": 0.1,
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
            },
        }
        for index in range(action_count)
    ]
    (verifier_dir / "evidence.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "runner_contract": "python-evidence-runner@2",
                "aliases": {
                    f"test.action-{index}": f"test.action-{index}" for index in range(action_count)
                },
                "actions": actions,
                "artifacts": [],
                "metadata": {
                    "action_count": action_count,
                    "error_count": 0,
                    "required_error_count": 0,
                    "workspace": "/app",
                },
            }
        ),
        encoding="utf-8",
    )


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
        contract=task_bundle_runtime._resolve_contract(request),
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
        starter_dir: Path,
        target_dir: Path,
        scenario_dir: Path,
        harness: str,
        *,
        runtime_profile: RuntimeProfile,
    ) -> tuple[Path, Path | None]:
        del starter_dir, scenario_dir, harness, runtime_profile
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
        starter_dir: Path,
        target_dir: Path,
        scenario_dir: Path,
        harness: str,
        *,
        runtime_profile: RuntimeProfile,
    ) -> tuple[Path, Path | None]:
        del starter_dir, scenario_dir, harness, runtime_profile
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir, None

    def fake_run_setup_actions(
        *,
        workspace: Path,
        env: dict[str, str],
        setup_actions: list[list[str]],
        timeout_sec: int,
    ) -> None:
        runtime_dir = baseline_workspace_dir.parent / f"{baseline_workspace_dir.name}.runtime"
        assert timeout_sec == 60
        assert workspace == baseline_workspace_dir
        assert env["TMPDIR"] == str(runtime_dir / "tmp")
        assert env["TMP"] == str(runtime_dir / "tmp")
        assert env["TEMP"] == str(runtime_dir / "tmp")
        assert env["XDG_CACHE_HOME"] == str(runtime_dir / "cache")
        assert env["UV_CACHE_DIR"] == str(runtime_dir / "cache" / "uv")
        assert env["BUN_INSTALL_CACHE_DIR"] == str(runtime_dir / "cache" / "bun")
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
    runtime_dir = baseline_workspace_dir.parent / f"{baseline_workspace_dir.name}.runtime"
    assert (runtime_dir / "tmp").is_dir()
    assert (runtime_dir / "cache" / "uv").is_dir()
    assert (runtime_dir / "cache" / "bun").is_dir()


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
        starter_dir: Path,
        target_dir: Path,
        scenario_dir: Path,
        harness: str,
        *,
        runtime_profile: RuntimeProfile,
    ) -> tuple[Path, Path | None]:
        del starter_dir, scenario_dir, harness, runtime_profile
        nonlocal prepare_calls
        prepare_calls += 1
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "fresh.txt").write_text("ready\n", encoding="utf-8")
        return target_dir, None

    def fake_run_setup_actions(
        *,
        workspace: Path,
        env: dict[str, str],
        setup_actions: list[list[str]],
        timeout_sec: int,
    ) -> None:
        del env, setup_actions
        assert timeout_sec == 60
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
        starter_dir: Path,
        target_dir: Path,
        scenario_dir: Path,
        harness: str,
        *,
        runtime_profile: RuntimeProfile,
    ) -> tuple[Path, Path | None]:
        del starter_dir, scenario_dir, harness, runtime_profile
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

    metrics = collect_process_metrics(
        _sample_scenario(), HarborResultManifest.for_trial(trial_dir, "codex-cli")
    )

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

    metrics = collect_process_metrics(
        _test_and_coverage_scenario(),
        HarborResultManifest.for_trial(trial_dir, "codex-cli"),
    )

    assert metrics.required_verification_commands == 2
    assert metrics.executed_required_verification_commands == 2


def test_collect_process_metrics_counts_unmatched_actual_commands(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    _write_codex_log(
        trial_dir,
        [
            _codex_command_entry("node scripts/format.js"),
            _codex_usage_entry(10, 0, 5),
        ],
    )

    metrics = collect_process_metrics(
        _sample_scenario(), HarborResultManifest.for_trial(trial_dir, "codex-cli")
    )

    assert metrics.command_count == 1
    assert metrics.executed_required_verification_commands == 0
    assert metrics.missing_required_verification_commands == 3


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
                "$ bun run typecheck",
                "$ bun run lint",
                "$ bun run build",
            ]
        )
    )

    metrics = collect_process_metrics(
        _sample_scenario(), HarborResultManifest.for_trial(trial_dir, "gemini")
    )

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

    metrics = collect_process_metrics(
        _sample_scenario(), HarborResultManifest.for_trial(trial_dir, "gemini")
    )

    assert metrics.command_count == 2
    assert metrics.required_verification_commands == 3
    assert metrics.executed_required_verification_commands == 2
    assert metrics.required_verification_first_pass["bun run typecheck"] == "pass"
    assert metrics.required_verification_first_pass["bun run lint"] == "pass"
    assert metrics.required_verification_first_pass["bun run build"] == "missing"


def test_normalized_shell_subcommands_splits_and_preserves_commands() -> None:
    commands = _normalized_shell_subcommands(
        "bash -lc 'bunx tsc --noEmit && npm run lint; bun run build'"
    )

    assert commands == ["bunx tsc --noEmit", "npm run lint", "bun run build"]


def test_normalized_shell_subcommands_handles_unparseable_command() -> None:
    commands = _normalized_shell_subcommands("bunx tsc --noEmit '")

    assert commands == ["bunx tsc --noEmit '"]


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
        "the implementation with `bun run build` and `bun run typecheck`."
    )

    metrics = collect_process_metrics(
        _sample_scenario(), HarborResultManifest.for_trial(trial_dir, "gemini")
    )

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
        ["bun run typecheck", "bun run lint"],
        {"input_tokens": 70, "cache_read_input_tokens": 20, "output_tokens": 9},
    )

    metrics = collect_process_metrics(
        _sample_scenario(), HarborResultManifest.for_trial(trial_dir, "claude-code")
    )

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

    metrics = collect_process_metrics(
        _sample_scenario(), HarborResultManifest.for_trial(trial_dir, "claude-code")
    )

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

    metrics = collect_process_metrics(
        _sample_scenario(), HarborResultManifest.for_trial(trial_dir, "claude-code")
    )

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

    metrics = collect_process_metrics(
        _sample_scenario(), HarborResultManifest.for_trial(trial_dir, "gemini")
    )

    assert metrics.uncached_input_tokens == 170
    assert metrics.output_tokens == 22


def test_collect_process_metrics_raises_when_usage_missing(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    harness_dir = trial_dir / "agent"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (harness_dir / "gemini-cli.trajectory.json").write_text(json.dumps({"messages": []}))

    with pytest.raises(RuntimeError, match="Missing token usage metrics"):
        collect_process_metrics(
            _sample_scenario(), HarborResultManifest.for_trial(trial_dir, "gemini")
        )


def test_collect_process_metrics_defaults_optional_usage_to_zero(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    (trial_dir / "agent").mkdir(parents=True)

    metrics = collect_process_metrics(
        _sample_scenario(), HarborResultManifest.for_trial(trial_dir, "cursor")
    )

    assert metrics.uncached_input_tokens == 0
    assert metrics.output_tokens == 0


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

    metrics = collect_process_metrics(
        _sample_scenario(), HarborResultManifest.for_trial(trial_dir, "codex-cli")
    )

    assert metrics.git_commit_verification_bypass_commands == [
        "git commit --no-verify -m 'feat: bypass hooks'"
    ]


def test_evaluate_typescript_coverage_reads_summary_file(tmp_path: Path):
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
    score = evaluate_typescript_coverage(workspace, gate_history=[], threshold=0.8)
    assert score.measured == 0.8
    assert score.passed is True
    assert score.source is not None


def test_evaluate_typescript_coverage_parses_gate_output_when_summary_missing(tmp_path: Path):
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
    score = evaluate_typescript_coverage(workspace, gate_history=gate_history, threshold=0.84)
    assert score.measured == 0.83
    assert score.passed is False
    assert score.source == "gate:coverage"


def test_evaluate_typescript_coverage_zero_threshold_does_not_require_measurement(tmp_path: Path):
    score = evaluate_typescript_coverage(tmp_path, gate_history=[], threshold=0)

    assert score.threshold == 0
    assert score.measured is None
    assert score.passed is True


def test_coverage_profile_score_treats_zero_threshold_as_no_minimum():
    scorecard = Scorecard(
        metric_scores=[MetricScore(metric_id="test-coverage", score=1.0, passed=True)],
    )

    assert scorecard.metric_score("test-coverage") == 1.0


def test_evaluate_typescript_requirements_flags_requirement_gaps(tmp_path: Path):
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

    result = evaluate_typescript_requirements(workspace, requirements)
    assert result.total_requirements == 1
    assert result.satisfied_requirements == 1
    assert result.mapped_requirements == 0
    assert result.mapped_satisfied_requirements == 0
    assert result.requirement_gap_ids == ["req-cta"]
    assert result.requirement_test_evidence_gaps == {
        "req-cta": ["query_role:button x1", "query_role:heading x1"]
    }


def test_evaluate_typescript_requirements_matches_role_queries_and_counts(tmp_path: Path):
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

    result = evaluate_typescript_requirements(workspace, requirements)
    assert result.total_requirements == 1
    assert result.satisfied_requirements == 1
    assert result.mapped_requirements == 1
    assert result.mapped_satisfied_requirements == 1
    assert result.requirement_gap_ids == []
    assert result.requirement_test_evidence_gaps == {}


def test_load_evidence_outputs_builds_gate_history(tmp_path: Path):
    trial_dir = tmp_path / "trial"
    verifier_dir = trial_dir / "verifier"
    verifier_dir.mkdir(parents=True, exist_ok=True)
    (verifier_dir / "evidence.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "runner_contract": "python-evidence-runner@2",
                "aliases": {"design-to-code.build": "design-to-code.build"},
                "actions": [
                    {
                        "id": "design-to-code.build",
                        "kind": "command",
                        "required": True,
                        "status": "ok",
                        "duration_sec": 0.1,
                        "result": {
                            "command": ["bun", "run", "build"],
                            "cwd": ".",
                            "duration_sec": 0.1,
                            "exit_code": 0,
                            "stdout": "ok",
                            "stderr": "",
                            "timed_out": False,
                        },
                    }
                ],
                "artifacts": [],
                "metadata": {
                    "action_count": 1,
                    "error_count": 0,
                    "required_error_count": 0,
                    "workspace": "/app",
                },
            }
        ),
        encoding="utf-8",
    )

    outputs, reason = _load_evidence_outputs(HarborResultManifest.for_trial(trial_dir, "codex-cli"))

    assert reason is None
    assert outputs is not None
    assert len(outputs.gate_history) == 1
    assert outputs.gate_history[0].command == "bun run build"


def test_load_evidence_outputs_missing_evidence(tmp_path: Path):
    outputs, reason = _load_evidence_outputs(
        HarborResultManifest.for_trial(tmp_path / "missing", "codex-cli")
    )
    assert outputs is None
    assert reason is not None


def test_scenario_evaluation_profile_uses_ordered_metrics():
    scenario = _sample_scenario()
    assert scenario_evaluation_profile(scenario) == (
        "scorers:typescript-code-task@1:0.9+resource-efficiency@1:0.1"
    )


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

    with pytest.raises(ValueError, match="Scorers did not emit all resolved metrics"):
        build_scorecard(score_context)


def test_build_metric_scores_passes_verification_evidence_to_scorers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    score_context = _sample_scorecard_context(
        tmp_path=tmp_path,
        terminated_early=False,
        termination_reason=None,
    )
    evidence_path = tmp_path / "verifier" / "evidence.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "runner_contract": "python-evidence-runner@2",
                "aliases": {
                    "design-to-code.build": "design-to-code.build",
                    "design-to-code.coverage": "design-to-code.coverage",
                },
                "actions": [
                    {
                        "id": "design-to-code.build",
                        "kind": "command",
                        "required": True,
                        "status": "ok",
                        "duration_sec": 0.1,
                        "result": {
                            "command": ["bun", "run", "build"],
                            "cwd": ".",
                            "duration_sec": 0.1,
                            "exit_code": 0,
                            "stdout": "ok",
                            "stderr": "",
                            "timed_out": False,
                        },
                    },
                    {
                        "id": "design-to-code.coverage",
                        "kind": "command",
                        "required": False,
                        "status": "error",
                        "duration_sec": 0.1,
                        "error": {"type": "RuntimeError", "message": "coverage unavailable"},
                    },
                ],
                "artifacts": [],
                "metadata": {
                    "action_count": 2,
                    "error_count": 1,
                    "required_error_count": 0,
                    "workspace": "/app",
                },
            }
        ),
        encoding="utf-8",
    )
    score_context = replace(
        score_context,
        artifacts=replace(
            score_context.artifacts,
            runner_evidence_artifacts={"evidence.json": str(evidence_path)},
        ),
    )
    observed_contexts = []

    resolved_metrics_by_scorer = {
        (scorer_ref.id, scorer_ref.version): tuple(
            MetricScore(metric_id=metric.id, score=1.0, passed=True) for metric in resolved.metrics
        )
        for scorer_ref, resolved in zip(
            score_context.request.scenario.scorers,
            score_context.request.scenario.resolved_scorers(),
            strict=True,
        )
    }

    def observing_scorer_class(scorer_id, version):
        class ObservingScorer:
            def collect_evidence(self, scorer_context):
                observed_contexts.append(scorer_context)
                return SimpleNamespace(
                    metric_scores=resolved_metrics_by_scorer[(scorer_id, version)]
                )

        return ObservingScorer

    monkeypatch.setattr(
        scoring_outputs_runtime,
        "scorer_class",
        observing_scorer_class,
    )

    scoring_outputs_runtime.build_metric_scores(
        score_context,
        execution_validity=ExecutionValidityScore(),
        resource_efficiency=scorecard_runtime.build_resource_efficiency_score(
            score_context.execution.process_metrics
        ),
    )

    evidence = observed_contexts[0].verification_evidence
    build = evidence.for_owner("design-to-code").action("build")
    assert build is not None
    assert isinstance(build.result, CommandEvidenceResult)
    assert build.result.exit_code == 0
    assert evidence.artifacts == ()
    coverage = evidence.for_owner("design-to-code").action("coverage")
    assert coverage is not None
    assert coverage.error is not None
    assert coverage.error.type == "RuntimeError"


def test_build_scorecard_recomputes_minimum_quality_gate_from_scorers(tmp_path: Path):
    score_context = _sample_scorecard_context(
        tmp_path=tmp_path,
        terminated_early=False,
        termination_reason=None,
    )
    coverage_dir = score_context.context.workspace / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)
    (coverage_dir / "coverage-summary.json").write_text(
        json.dumps(
            {
                "total": {
                    "lines": {"pct": 90},
                    "statements": {"pct": 90},
                    "functions": {"pct": 90},
                    "branches": {"pct": 90},
                }
            }
        ),
        encoding="utf-8",
    )
    score_context.request.scenario.verification.min_quality_score = 0.95

    scorecard = build_scorecard(score_context)

    quality_gate = next(
        check
        for check in scorecard.performance_gates.checks
        if check.name == "minimum_quality_score"
    )
    assert quality_gate.passed is False
    assert quality_gate.evidence == "quality=0.125, min=0.950"


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
    _write_evidence_metadata_artifacts(trial_dir, 2)
    score_context = _score_context_with_evidence_metadata(score_context, trial_dir)

    scorecard = build_scorecard(score_context)
    harbor_meta = scorecard.metadata["harbor"]

    assert harbor_meta["prep_phase_timings_sec"] == {"prepare_run_context": 0.123}
    assert harbor_meta["prep_total_sec"] == 0.456
    assert harbor_meta["time_to_experiment_start_sec"] == 0.789
    assert harbor_meta["orchestration_overhead_excluding_agent_and_test_sec"] == 5.5
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
    assert scorecard.metadata["runner_evidence"]["action_count"] == 2


def test_classify_unscored_reasons_rate_limit():
    reasons = _classify_unscored_reasons(
        terminated_early=True,
        termination_reason="Codex turn failed: Rate limit reached for gpt-5.2-codex.",
        failure_code="provider_rate_limit",
    )
    assert "provider_rate_limit" in reasons


def test_classify_unscored_reasons_timeout():
    reasons = _classify_unscored_reasons(
        terminated_early=True,
        termination_reason="Timeout expired after 420s before trial result.json was written.",
        failure_code="harbor_timeout",
    )
    assert reasons == ["harbor_timeout"]


def test_classify_unscored_reasons_compose_version_unsupported():
    reasons = _classify_unscored_reasons(
        terminated_early=True,
        termination_reason=(
            "Unsupported docker compose version 2.39.2. Require >= 2.40.1 for Harbor runs."
        ),
        failure_code="compose_version_unsupported",
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
        failure_code="provider_rate_limit",
    )

    scorecard = build_scorecard(context)

    assert scorecard.unscored is True
    assert "provider_rate_limit" in scorecard.unscored_reasons
    assert scorecard.metadata["run"]["rerun_required"] is True
    assert scorecard.metadata["run"]["unscored_reasons"] == scorecard.unscored_reasons


def test_persist_scorecard_artifacts_overwrites_stale_outputs(tmp_path: Path):
    context = _sample_scorecard_context(
        tmp_path,
        terminated_early=True,
        termination_reason=(
            "Codex turn failed: Quota exceeded. Check your plan and billing details."
        ),
    )
    scoring_dir = context.layout.scoring_dir
    scoring_dir.mkdir(parents=True, exist_ok=True)
    (scoring_dir / "scorecard.json").write_text(
        json.dumps(_stale_canonical_scorecard_payload()),
        encoding="utf-8",
    )
    (scoring_dir / "execution-validity.json").write_text(
        json.dumps({"checks": [{"name": "run_completed", "passed": True}], "passed": True}),
        encoding="utf-8",
    )
    (scoring_dir / "performance-gates.json").write_text(
        json.dumps({"checks": [], "passed": True}),
        encoding="utf-8",
    )
    (scoring_dir / "gate-history.json").write_text(
        json.dumps([{"gate_name": "lint"}]), encoding="utf-8"
    )
    (scoring_dir / "reward.txt").write_text("1", encoding="utf-8")

    scorecard = build_scorecard(context)

    runner.persist_scorecard_artifacts(context.layout, scorecard, context.execution.outputs)

    persisted_scorecard = json.loads((scoring_dir / "scorecard.json").read_text(encoding="utf-8"))
    persisted_execution = json.loads(
        (scoring_dir / "execution-validity.json").read_text(encoding="utf-8")
    )
    persisted_gate_history = json.loads(
        (scoring_dir / "gate-history.json").read_text(encoding="utf-8")
    )

    run_completed_check = next(
        check for check in persisted_execution["checks"] if check["name"] == "run_completed"
    )
    assert run_completed_check["passed"] is False
    assert persisted_execution["passed"] is False
    assert persisted_scorecard["execution_validity"]["passed"] is False
    assert persisted_scorecard["unscored"] is True
    assert persisted_gate_history == []
    assert float((scoring_dir / "reward.txt").read_text(encoding="utf-8")) == 0.0


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
        contract=task_bundle_runtime._resolve_contract(request),
    )
    copied_reference = bundle / "environment" / "app" / reference_rel
    copied_region_reference = bundle / "environment" / "app" / "references" / "hero-region-nav.png"
    verification_plan = json.loads(
        (bundle / "tests" / "verification-plan.json").read_text(encoding="utf-8")
    )
    visual_actions = [
        action for action in verification_plan["actions"] if action["kind"] == "visual_diff"
    ]

    assert copied_reference.exists()
    assert copied_reference.read_bytes() == b"png-binary"
    assert copied_region_reference.exists()
    assert copied_region_reference.read_bytes() == b"region-binary"
    assert (bundle / "tests" / "evidence-runner.py").exists()
    test_script = (bundle / "tests" / "test.sh").read_text(encoding="utf-8")
    assert "printf '0\\n' > /logs/verifier/reward.txt" in test_script
    expected_region = _visual_bundle_region()
    expected_region.pop("clip")
    assert visual_actions[0]["config"]["regions"] == [expected_region]
    assert visual_actions[0]["config"]["reference"] == reference_rel.as_posix()


def test_create_harbor_task_bundle_sets_task_image_and_cli_install(tmp_path: Path):
    fixture = _harbor_bundle_fixture(tmp_path)
    bundle = _create_bundle(fixture)
    task_toml = (bundle / "task.toml").read_text()
    dockerfile = (bundle / "environment" / "Dockerfile").read_text()
    compose = (bundle / "environment" / "docker-compose.yaml").read_text()

    assert 'docker_image = "raidar-task-env:task-env-codex-cli-' in task_toml
    assert "git \\" in dockerfile
    assert "@openai/codex" in dockerfile
    assert "@anthropic-ai/claude-code" not in dockerfile
    assert "@google/gemini-cli" not in dockerfile
    assert 'command: ["sleep", "infinity"]' in compose
    assert "stop_grace_period: 0s" in compose


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
        contract=task_bundle_runtime._resolve_contract(request),
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
        contract=task_bundle_runtime._resolve_contract(request),
    )

    instruction = (bundle / "instruction.md").read_text(encoding="utf-8")
    image_ref = runner.task_image_reference(
        request,
        bundle,
        task_bundle_runtime._resolve_contract(request),
    )
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
            "display_name": "Hello World Smoke",
            "scenario_revision": "v001",
            "description": "task",
            "difficulty": "easy",
            "task_kind": "feature_implementation",
            "delivery_mode": "direct_implementation",
            "workspace_state": "existing",
            "starting_conditions": [],
            "languages": ["TypeScript"],
            "evaluation_role": "operational",
            "catalog_status": "active",
            "variant_axes": {},
            "timeout_sec": 300,
            "environment": _typescript_environment_doc(),
            "starter": {"root": "starter"},
            "verification": {"gates": [], "required_commands": [], "min_quality_score": 0.0},
            "requirements": {"items": []},
            "scorers": [{"id": "resource-efficiency", "version": 1, "weight": 1.0}],
            "prompt": {"entry": "prompt/task.md"},
        }
    )

    command = _resolve_homepage_screenshot_command(scenario, workspace)
    assert command is None


def test_ensure_workspace_capture_dependencies_runs_visual_setup_actions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    calls: list[tuple[list[str], Path]] = []

    def fake_run(command, **kwargs):
        calls.append((command, Path(kwargs["cwd"])))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    task = SimpleNamespace(
        visual=SimpleNamespace(capture_setup_actions=[["bun", "install", "--frozen-lockfile"]])
    )
    error = runner._ensure_workspace_capture_dependencies(
        task,
        workspace,
        runtime_profile=RUNTIME_PROFILE,
    )

    assert error is None
    assert calls == [(["bun", "install", "--frozen-lockfile"], workspace)]


def test_ensure_workspace_capture_dependencies_skips_without_visual_setup_actions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    def fail_run(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr(subprocess, "run", fail_run)

    task = SimpleNamespace(visual=SimpleNamespace(capture_setup_actions=[]))
    error = runner._ensure_workspace_capture_dependencies(
        task,
        workspace,
        runtime_profile=RUNTIME_PROFILE,
    )

    assert error is None


def test_prune_workspace_artifacts_removes_transient_directories(tmp_path: Path):
    workspace = tmp_path / "workspace"
    (workspace / "node_modules" / "pkg").mkdir(parents=True, exist_ok=True)
    (workspace / "node_modules" / "pkg" / "index.js").write_text("console.log('x')\n")
    (workspace / ".next").mkdir(parents=True, exist_ok=True)
    (workspace / ".next" / "trace").write_text("trace\n")
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "app.tsx").write_text("export const App = () => null;\n")

    prune = _prune_workspace_artifacts(workspace, RUNTIME_PROFILE)

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

    prune = _prune_workspace_artifacts(workspace, RUNTIME_PROFILE)

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
