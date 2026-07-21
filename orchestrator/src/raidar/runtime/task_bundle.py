"""Harbor task bundle and runner-evidence rendering services."""

from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path
from typing import Any

from raidar.runtime.environments import ResolvedEnvironment
from raidar.runtime.execution_contract import EffectiveRunContract
from raidar.runtime.harbor import validate_public_base_images as _validate_public_base_images
from raidar.runtime.models import (
    BaselineWorkspaceCacheResult,
    RunRequest,
    WorkspaceContext,
)
from raidar.runtime.profile import RuntimeProfile
from raidar.runtime.task_bundle_images import task_image_reference
from raidar.runtime.workspace_artifacts import _visual_reference_assets
from raidar.runtime.workspace_cache import (
    BaselineWorkspaceRequest,
    _baseline_cache_key,
    _baseline_cache_workspace_dir,
    _ensure_baseline_workspace,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_RUNNER_ASSET = Path(__file__).parent.parent / "assets" / "evidence_runner.py"


def _resolve_contract(request: RunRequest):
    from raidar.runtime.execution_contract import resolve_effective_run_contract

    return resolve_effective_run_contract(
        scenario=request.scenario,
        scenario_path=request.scenario_dir / "scenario.yaml",
        repo_root=REPO_ROOT,
        harness_id=request.config.harness.value,
    )


def _task_image_capability_inputs(
    request: RunRequest,
    contract: EffectiveRunContract,
):
    del request
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


def _copy_baseline_workspace(
    baseline_workspace_dir: Path,
    workspace_dir: Path,
    *,
    runtime_profile: RuntimeProfile,
) -> None:
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    shutil.copytree(
        baseline_workspace_dir,
        workspace_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*runtime_profile.copy_excludes),
    )


def _baseline_workspace_for_request(
    request: RunRequest,
    starter_source,
    *,
    runtime_profile: RuntimeProfile,
) -> tuple[str, Path, BaselineWorkspaceCacheResult]:
    baseline_cache_key = _baseline_cache_key(
        request,
        starter_source.fingerprint,
        runtime_profile=runtime_profile,
    )
    baseline_workspace_dir = _baseline_cache_workspace_dir(baseline_cache_key)
    baseline_cache = _ensure_baseline_workspace(
        BaselineWorkspaceRequest(
            scenario=request.scenario,
            starter_dir=starter_source.path,
            baseline_workspace_dir=baseline_workspace_dir,
            baseline_cache_key=baseline_cache_key,
            scenario_dir=request.scenario_dir,
            harness=request.config.harness,
            runtime_profile=runtime_profile,
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
    request: RunRequest,
    source_workspace: Path,
    workspace_dir: Path,
    *,
    runtime_profile: RuntimeProfile,
) -> None:
    shutil.copytree(
        source_workspace,
        workspace_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*runtime_profile.copy_excludes),
    )
    visual_assets = [] if not request.scenario.visual else _visual_reference_assets(request)
    for source_reference, relative_target in visual_assets:
        target_reference = workspace_dir / relative_target
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


def _write_prebuilt_compose_file(environment_dir: Path) -> None:
    """Use a directly signalled idle process so warm teardown does not wait 10 seconds."""

    (environment_dir / "docker-compose.yaml").write_text(
        """services:
  main:
    image: ${PREBUILT_IMAGE_NAME}
    command: ["sleep", "infinity"]
    stop_grace_period: 0s
    network_mode: ${NETWORK_MODE:-bridge}
    environment:
      - TEST_DIR=${TEST_DIR}
    volumes:
      - ${HOST_VERIFIER_LOGS_PATH}:${ENV_VERIFIER_LOGS_PATH}
      - ${HOST_AGENT_LOGS_PATH}:${ENV_AGENT_LOGS_PATH}
    deploy:
      resources:
        limits:
          cpus: ${CPUS}
          memory: ${MEMORY}
""",
        encoding="utf-8",
    )


def _write_runner_evidence_artifacts(
    request: RunRequest,
    tests_dir: Path,
    contract: EffectiveRunContract,
) -> None:
    del request
    from raidar.harness import HarborArtifactRole, HarborResultManifest

    runner_evidence_dir = HarborResultManifest.container_path(
        HarborArtifactRole.RUNNER_EVIDENCE_ROOT
    )
    baseline_runner_evidence_dir = HarborResultManifest.container_path(
        HarborArtifactRole.BASELINE_RUNNER_EVIDENCE
    ).parent
    harness_output_dir = HarborResultManifest.container_path(HarborArtifactRole.HARNESS_OUTPUT_ROOT)
    baseline_verification_plan = contract.evidence_runner_payload(
        workspace="/tests/baseline-workspace",
        output_dir=str(baseline_runner_evidence_dir),
    )
    verification_plan = contract.evidence_runner_payload(
        workspace="/app",
        output_dir=str(runner_evidence_dir),
    )
    (tests_dir / "baseline-verification-plan.json").write_text(
        json.dumps(baseline_verification_plan, indent=2) + "\n",
        encoding="utf-8",
    )
    (tests_dir / "verification-plan.json").write_text(
        json.dumps(verification_plan, indent=2) + "\n",
        encoding="utf-8",
    )
    evidence_runner_path = tests_dir / "evidence-runner.py"
    evidence_runner_path.write_text(EVIDENCE_RUNNER_ASSET.read_text(encoding="utf-8"))
    evidence_runner_path.chmod(0o755)
    final_workspace_archive = (
        contract.harness.final_workspace_archive if contract.harness else "final-app.tar.gz"
    )
    archive_excludes = "\n".join(
        f"  --exclude={shlex.quote(f'./{pattern}')} \\"
        for pattern in contract.runtime_profile.archive_excludes
    )
    test_script = tests_dir / "test.sh"
    test_script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
mkdir -p {runner_evidence_dir} {baseline_runner_evidence_dir} {harness_output_dir}
if [[ ! -d /tests/baseline-workspace ]]; then
  echo "Missing baseline workspace" >&2
  exit 1
fi
if [[ ! -d /app ]]; then
  echo "Missing /app workspace" >&2
  exit 1
fi

python "$SCRIPT_DIR/evidence-runner.py" \
  "$SCRIPT_DIR/baseline-verification-plan.json" \
  --output-dir {baseline_runner_evidence_dir}

python "$SCRIPT_DIR/evidence-runner.py" \
  "$SCRIPT_DIR/verification-plan.json" \
  --output-dir {runner_evidence_dir}

# Harbor requires a verifier reward sentinel; host-side canonical scoring replaces it.
printf '0\\n' > {runner_evidence_dir}/reward.txt

tar \
{archive_excludes}
  -czf {harness_output_dir}/{final_workspace_archive} \
  -C /app .
"""
    )
    test_script.chmod(0o755)


def create_harbor_task_bundle(
    request: RunRequest,
    context: WorkspaceContext,
    bundle_root: Path,
    contract: EffectiveRunContract,
) -> Path:
    """Build a Harbor-compatible bundle from the starter workspace."""
    bundle_dir, environment_dir, app_dir, tests_dir = _initialize_harbor_bundle_paths(bundle_root)
    environment = contract.environment
    _copy_workspace_into_bundle(
        request,
        context.baseline_workspace,
        tests_dir / "baseline-workspace",
        runtime_profile=contract.runtime_profile,
    )
    _copy_workspace_into_bundle(
        request,
        context.workspace,
        app_dir,
        runtime_profile=contract.runtime_profile,
    )
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
    _validate_public_base_images(
        dockerfile,
        allowed_registry_hosts=contract.runtime_profile.docker_registry_allowlist,
    )
    dockerfile_path = environment_dir / "Dockerfile"
    dockerfile_path.write_text(dockerfile)
    _write_runner_evidence_artifacts(request, tests_dir, contract)
    image_ref = task_image_reference(request, bundle_dir, contract)
    if image_ref is not None:
        _write_prebuilt_compose_file(environment_dir)
    (bundle_dir / "task.toml").write_text(
        _render_task_toml(request, image_ref.image_name if image_ref else None, environment)
    )
    return bundle_dir
