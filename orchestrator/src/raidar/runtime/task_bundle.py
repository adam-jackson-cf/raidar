"""Harbor task bundle and verifier scenario rendering services."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from raidar.agents.harbor_routing import is_task_image_reuse_enabled, task_image_prefix
from raidar.audit.workspace_diff import directory_fingerprint
from raidar.runtime.environments import ResolvedEnvironment
from raidar.runtime.execution_contract import EffectiveRunContract
from raidar.runtime.harbor import validate_public_base_images as _validate_public_base_images
from raidar.runtime.identifiers import slug_fragment
from raidar.runtime.models import (
    BaselineWorkspaceCacheResult,
    RunRequest,
    TaskImageRef,
    WorkspaceContext,
)
from raidar.runtime.workspace_artifacts import (
    _runtime_copy_excludes,
    _visual_artifact_manifest,
    _visual_reference_assets,
)
from raidar.runtime.workspace_cache import (
    RAIDAR_CACHE_VERSION,
    BaselineWorkspaceRequest,
    _baseline_cache_key,
    _baseline_cache_workspace_dir,
    _ensure_baseline_workspace,
    _hash_json_payload,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


def _load_baseline_scripts(starter_source: Any) -> dict[str, str]:
    package_path = starter_source.path / "package.json"
    if not package_path.exists():
        return {}
    try:
        payload = json.loads(package_path.read_text())
    except json.JSONDecodeError:
        return {}
    scripts = payload.get("scripts")
    if not isinstance(scripts, dict):
        return {}
    return {str(key): str(value) for key, value in scripts.items()}


def _scenario_spec_metrics_block(request: RunRequest) -> list[dict[str, Any]]:
    return [
        metric.model_dump(mode="json", exclude_none=True)
        for metric in request.scenario.resolved_metrics()
    ]


def _scenario_spec_scorers_block(request: RunRequest) -> list[dict[str, Any]]:
    return [
        {
            "id": scorer.id,
            "version": scorer.version,
            "status": scorer.status,
            "category": scorer.category,
            "description": scorer.description,
            "weight": scorer.weight,
            "extends": scorer.extends,
            "requirements": scorer.requirements.model_dump(mode="json"),
            "metrics": [metric.model_dump(mode="json") for metric in scorer.metrics],
        }
        for scorer in request.scenario.resolved_scorers()
    ]


def _scenario_spec_verification_block(request: RunRequest) -> dict[str, Any]:
    return {
        "max_gate_failures": request.scenario.verification.max_gate_failures,
        "coverage_threshold": request.scenario.verification.coverage_threshold,
        "min_quality_score": request.scenario.verification.min_quality_score,
        "workflow": request.scenario.verification.workflow.model_dump(mode="json"),
        "gates": [
            {
                "name": gate.name,
                "command": gate.command,
                "on_failure": gate.on_failure,
            }
            for gate in request.scenario.verification.gates
        ],
    }


def _scenario_spec_requirements_block(request: RunRequest) -> dict[str, Any]:
    return {
        "items": [
            {
                "id": requirement.id,
                "description": requirement.description,
                "check": {
                    "type": requirement.check.type,
                    "pattern": requirement.check.pattern,
                    "description": requirement.check.description,
                },
                "required_test_evidence": [
                    evidence.model_dump(mode="json")
                    for evidence in requirement.required_test_evidence
                ],
            }
            for requirement in request.scenario.requirements.items
        ],
    }


def _scenario_spec_visual_block(request: RunRequest) -> dict[str, Any] | None:
    if request.scenario.visual is None:
        return None
    return {
        "reference_image": request.scenario.visual.reference_image,
        "screenshot_command": request.scenario.visual.screenshot_command,
        "artifacts": _visual_artifact_manifest(request.scenario),
        "viewport": (
            request.scenario.visual.viewport.model_dump(mode="json")
            if request.scenario.visual.viewport is not None
            else None
        ),
        "scoring": request.scenario.visual.scoring.model_dump(mode="json", by_alias=True),
        "pass_policy": request.scenario.visual.pass_policy.model_dump(mode="json"),
        "regions": [region.model_dump(mode="json") for region in request.scenario.visual.regions],
    }


def _scenario_spec_environment_block(environment: ResolvedEnvironment) -> dict[str, Any]:
    return environment.report_payload()


def _build_verifier_scenario_spec(
    request: RunRequest,
    context: WorkspaceContext,
    environment: ResolvedEnvironment,
) -> dict:
    return {
        "scenario_name": request.scenario.name,
        "environment": _scenario_spec_environment_block(environment),
        "metrics": _scenario_spec_metrics_block(request),
        "scorers": _scenario_spec_scorers_block(request),
        "verification": _scenario_spec_verification_block(request),
        "requirements": _scenario_spec_requirements_block(request),
        "visual": _scenario_spec_visual_block(request),
        "baseline_scripts": _load_baseline_scripts(context.starter_source),
    }


def _resolve_contract(request: RunRequest):
    from raidar.runtime.execution_contract import resolve_effective_run_contract

    return resolve_effective_run_contract(
        scenario=request.scenario,
        scenario_path=request.scenario_dir / "scenario.yaml",
        repo_root=REPO_ROOT,
        harness_id=request.config.harness.value,
    )


def _task_image_reference(
    request: RunRequest,
    task_bundle_path: Path,
    contract: EffectiveRunContract | None = None,
) -> TaskImageRef | None:
    if not is_task_image_reuse_enabled():
        return None
    environment_dir = task_bundle_path / "environment"
    dockerfile_path = environment_dir / "Dockerfile"
    app_dir = environment_dir / "app"
    tests_dir = task_bundle_path / "tests"
    if not dockerfile_path.exists() or not app_dir.exists():
        return None

    contract = contract or _resolve_contract(request)
    payload = {
        "cache_version": RAIDAR_CACHE_VERSION,
        "effective_run_contract": contract.cache_payload,
        "effective_run_contract_hash": contract.contract_hash,
        "dockerfile": dockerfile_path.read_text(encoding="utf-8"),
        "app_fingerprint": directory_fingerprint(app_dir),
        "tests_fingerprint": directory_fingerprint(tests_dir) if tests_dir.exists() else None,
    }
    cache_key = _hash_json_payload(payload)
    digest = cache_key[:16]
    harness_fragment = slug_fragment(request.config.harness.value)
    image_tag = f"task-env-{harness_fragment}-{digest}"
    return TaskImageRef(
        image_name=f"{task_image_prefix()}:{image_tag}",
        cache_key=cache_key,
        tag=image_tag,
    )


def _task_image_capability_inputs(
    request: RunRequest,
    contract: EffectiveRunContract | None = None,
):
    contract = contract or _resolve_contract(request)
    return (
        contract.provided_capabilities,
        contract.required_capabilities,
    )


def _task_environment_toml(image_name: str | None, environment: ResolvedEnvironment) -> str:
    lines = ["build_timeout_sec = 1800.0"]
    if image_name:
        lines.append(f'docker_image = "{image_name}"')
    resources = environment.resources
    lines.extend(
        [
            f"cpus = {resources.cpus}",
            f"memory_mb = {resources.memory_mb}",
            f"storage_mb = {resources.storage_mb}",
            f"allow_internet = {str(environment.config.allow_internet).lower()}",
        ]
    )
    return "\n".join(lines)


def _copy_baseline_workspace(baseline_workspace_dir: Path, workspace_dir: Path) -> None:
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    shutil.copytree(
        baseline_workspace_dir,
        workspace_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*_runtime_copy_excludes()),
    )


def _baseline_workspace_for_request(
    request: RunRequest, starter_source
) -> tuple[str, Path, BaselineWorkspaceCacheResult]:
    baseline_cache_key = _baseline_cache_key(request, starter_source.fingerprint)
    baseline_workspace_dir = _baseline_cache_workspace_dir(baseline_cache_key)
    baseline_cache = _ensure_baseline_workspace(
        BaselineWorkspaceRequest(
            scenario=request.scenario,
            starter_dir=starter_source.path,
            baseline_workspace_dir=baseline_workspace_dir,
            baseline_cache_key=baseline_cache_key,
            scenario_dir=request.scenario_dir,
            harness=request.config.harness,
        )
    )
    return baseline_cache_key, baseline_workspace_dir, baseline_cache


def _initialize_harbor_bundle_paths(
    bundle_root: Path,
) -> tuple[Path, Path, Path, Path]:
    bundle_dir = bundle_root
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    environment_dir = bundle_dir / "environment"
    app_dir = environment_dir / "app"
    tests_dir = bundle_dir / "tests"
    environment_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    return bundle_dir, environment_dir, app_dir, tests_dir


def _copy_workspace_into_bundle(
    request: RunRequest, context: WorkspaceContext, app_dir: Path
) -> None:
    shutil.copytree(
        context.workspace,
        app_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*_runtime_copy_excludes()),
    )
    visual_assets = [] if not request.scenario.visual else _visual_reference_assets(request)
    for source_reference, relative_target in visual_assets:
        target_reference = app_dir / relative_target
        target_reference.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_reference, target_reference)


def _load_scenario_prompt(task: Any, scenario_dir: Path) -> str:
    prompt_paths = [task.prompt.entry, *task.prompt.includes]
    prompt_chunks: list[str] = []
    for rel_path in prompt_paths:
        prompt_path = (scenario_dir / rel_path).resolve()
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt artifact not found: {prompt_path}")
        prompt_chunks.append(prompt_path.read_text(encoding="utf-8").strip())
    return "\n\n".join(chunk for chunk in prompt_chunks if chunk)


def _bundle_instruction_text(
    prompt: str, rules_filename: str = "AGENTS.md", *, include_rules_reference: bool = True
) -> str:
    rules_reference = (
        f"Follow rules in `/app/{rules_filename}`.\n" if include_rules_reference else ""
    )
    return (
        prompt.strip()
        + f"\n\nYou are working in `/app`.\n{rules_reference}"
        + "The `/app` workspace is not a git repository; do not run git commands unless "
        "the task explicitly requires Git.\n"
        + "Avoid broad dependency-directory inspection such as `node_modules` unless "
        "the task explicitly requires dependency internals.\n"
        + "Inspect only the files needed for the task; do not list the workspace just "
        "to confirm common project files exist.\n"
        + "Do not emit progress updates; make the requested changes, run required "
        "verification, then provide a concise final result.\n"
    )


def _render_task_toml(
    request: RunRequest,
    task_image: str | None,
    environment: ResolvedEnvironment,
) -> str:
    return f"""version = "1.0"

[metadata]
name = "{request.scenario.name}"
source = "starter-spec"

[verifier]
timeout_sec = 300.0

[agent]
timeout_sec = {float(request.config.timeout_sec)}

[environment]
{_task_environment_toml(task_image, environment)}
"""


def _harness_install_dockerfile_fragment(harness: str) -> str:
    from raidar.harness import harness_definition

    return harness_definition(harness).dockerfile_install_fragment()


def _render_environment_dockerfile(
    environment: ResolvedEnvironment, *, harness: str | None = None
) -> str:
    dockerfile = environment.dockerfile_path.read_text(encoding="utf-8")
    if harness is None:
        return dockerfile

    install_fragment = _harness_install_dockerfile_fragment(harness)
    if not install_fragment:
        return dockerfile

    copy_marker = "COPY app/ /app/"
    if copy_marker not in dockerfile:
        return dockerfile.rstrip() + "\n" + install_fragment
    return dockerfile.replace(copy_marker, install_fragment + "\n" + copy_marker, 1)


def _write_verifier_artifacts(
    request: RunRequest,
    context: WorkspaceContext,
    tests_dir: Path,
    environment: ResolvedEnvironment,
    contract: EffectiveRunContract,
) -> None:
    (tests_dir / "scenario-spec.json").write_text(
        json.dumps(_build_verifier_scenario_spec(request, context, environment), indent=2)
    )
    runner = contract.verifier_runner
    scorer_path = tests_dir / runner.bundle_filename
    scorer_path.write_text(runner.asset_path.read_text(encoding="utf-8"))
    verifier_command = runner.render_command()
    final_workspace_archive = (
        contract.harness.final_workspace_archive if contract.harness else "final-app.tar.gz"
    )
    scorer_path.chmod(0o755)
    test_script = tests_dir / "test.sh"
    test_script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
mkdir -p /logs/verifier /logs/agent
if [[ ! -d /app ]]; then
  echo "Missing /app workspace" >&2
  echo "0" > /logs/verifier/reward.txt
  exit 1
fi

if ! {verifier_command}; then
  echo "0" > /logs/verifier/reward.txt
fi

tar \
  --exclude='./node_modules' \
  --exclude='./.next' \
  --exclude='./jobs' \
  -czf /logs/agent/{final_workspace_archive} \
  -C /app .
"""
    )
    test_script.chmod(0o755)


def create_harbor_task_bundle(
    request: RunRequest,
    context: WorkspaceContext,
    bundle_root: Path,
    contract: EffectiveRunContract | None = None,
) -> Path:
    """Build a Harbor-compatible bundle from the starter workspace."""
    contract = contract or _resolve_contract(request)
    bundle_dir, environment_dir, app_dir, tests_dir = _initialize_harbor_bundle_paths(bundle_root)
    environment = contract.environment
    _copy_workspace_into_bundle(request, context, app_dir)
    prompt_text = _load_scenario_prompt(request.scenario, request.scenario_dir)
    rules_filename = context.injected_rules.name if context.injected_rules else "AGENTS.md"
    (bundle_dir / "instruction.md").write_text(
        _bundle_instruction_text(
            prompt_text,
            rules_filename,
            include_rules_reference=request.config.harness.value != "codex-cli",
        )
    )

    dockerfile = _render_environment_dockerfile(environment, harness=request.config.harness.value)
    _validate_public_base_images(dockerfile)
    dockerfile_path = environment_dir / "Dockerfile"
    dockerfile_path.write_text(dockerfile)
    _write_verifier_artifacts(request, context, tests_dir, environment, contract)
    image_ref = _task_image_reference(request, bundle_dir, contract)
    (bundle_dir / "task.toml").write_text(
        _render_task_toml(request, image_ref.image_name if image_ref else None, environment)
    )
    return bundle_dir
