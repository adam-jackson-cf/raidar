"""Task execution via Harbor."""

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .audit.scaffold_manifest import generate_manifest, save_manifest
from .config import settings
from .harness.config import HarnessConfig
from .harness.rules import inject_rules
from .scaffold import ScaffoldSource
from .schemas.events import GateEvent, SessionEvent
from .schemas.scorecard import (
    ComplianceCheck,
    ComplianceScore,
    CoverageScore,
    EfficiencyScore,
    EvalConfig,
    EvalRun,
    FunctionalScore,
    OptimizationScore,
    QualificationCheck,
    QualificationScore,
    RequirementCoverageScore,
    ScaffoldAudit,
    Scorecard,
    VisualScore,
)
from .schemas.task import RequirementSpec, TaskDefinition
from .scoring.compliance import run_deterministic_check

SCORING_SCHEMA_VERSION = "2.0.0"
HARBOR_TIMEOUT_BUFFER_SEC = 120
HARNESS_STALE_CONTAINER_PATTERN = re.compile(r"^harbor-task.*-main-1$")
HARBOR_GIT_MULTIBRANCH_PATTERN = re.compile(r"^git-multibranch__.+-main-1$")
HARNESS_STALE_BUILD_PATTERN = re.compile(r"harbor-task__.+docker-compose-build\.yaml build")
HARNESS_STALE_BUILDX_PATTERN = re.compile(r"docker-buildx bake .*harbor-task/environment")


def load_task(task_path: Path) -> TaskDefinition:
    """Load task definition from YAML file."""
    with open(task_path) as f:
        data = yaml.safe_load(f)
    return TaskDefinition.model_validate(data)


@dataclass(frozen=True, slots=True)
class RunRequest:
    """Input bundle for running a task."""

    task: TaskDefinition
    config: HarnessConfig
    scaffold_root: Path
    task_dir: Path
    workspace_dir: Path
    results_dir: Path


@dataclass(frozen=True, slots=True)
class ScaffoldContext:
    """Resolved scaffold context for a task run."""

    scaffold_source: ScaffoldSource
    workspace: Path
    injected_rules: Path | None
    manifest_path: Path
    baseline_manifest_path: Path
    metadata_path: Path


@dataclass(frozen=True, slots=True)
class ArtifactPaths:
    """Persisted artifact locations for a run."""

    baseline: Path
    manifest: Path
    metadata: Path
    rules: Path | None


@dataclass(frozen=True, slots=True)
class EvaluationOutputs:
    """Computed scoring outputs for a run."""

    functional: FunctionalScore
    compliance: ComplianceScore
    visual: VisualScore | None
    efficiency: EfficiencyScore
    coverage: CoverageScore
    requirements: RequirementCoverageScore
    qualification: QualificationScore
    scaffold_audit: ScaffoldAudit | None
    gate_history: list[GateEvent]


@dataclass(frozen=True, slots=True)
class HarborExecutionResult:
    """Outcome of the Harbor execution phase."""

    terminated_early: bool
    termination_reason: str | None
    job_dir: Path
    trial_dir: Path | None


@dataclass(frozen=True, slots=True)
class CommandRecord:
    """Normalized command execution record from Codex logs."""

    command: str
    failed: bool
    output: str


@dataclass(frozen=True, slots=True)
class ProcessMetrics:
    """Process metrics extracted from Harbor agent logs."""

    uncached_input_tokens: int
    output_tokens: int
    command_count: int
    failed_command_count: int
    verification_rounds: int
    repeated_verification_failures: int
    required_verification_commands: int
    executed_required_verification_commands: int
    failed_command_categories: dict[str, int] = field(default_factory=dict)
    required_verification_first_pass: dict[str, str] = field(default_factory=dict)
    first_pass_verification_successes: int = 0
    first_pass_verification_failures: int = 0
    missing_required_verification_commands: int = 0


@dataclass(frozen=True, slots=True)
class RunLayout:
    """Filesystem layout for a canonical evaluation run directory."""

    run_id: str
    start_time: datetime
    instance_name: str
    root_dir: Path
    summary_dir: Path
    scaffold_dir: Path
    verifier_dir: Path
    agent_dir: Path
    harbor_dir: Path
    result_json_path: Path
    summary_readme_path: Path
    jobs_root: Path


@dataclass(frozen=True, slots=True)
class HarborExecutionRequest:
    """Typed Harbor execution request."""

    adapter: Any
    workspace: Path
    task_bundle_path: Path
    jobs_dir: Path
    run_harbor_dir: Path
    run_id: str
    timeout_sec: int
    run_env: dict[str, str]


@dataclass(frozen=True, slots=True)
class WorkspacePreparationPhaseResult:
    """Workspace preparation phase output."""

    layout: RunLayout
    context: ScaffoldContext
    harbor_request: HarborExecutionRequest


@dataclass(frozen=True, slots=True)
class ExecutionPhaseResult:
    """Harbor execution + verifier loading phase output."""

    harbor_result: HarborExecutionResult
    terminated_early: bool
    termination_reason: str | None
    process_metrics: ProcessMetrics
    events: list[SessionEvent]
    outputs: EvaluationOutputs
    duration_sec: float


@dataclass(frozen=True, slots=True)
class PersistedArtifacts:
    """Persisted artifact metadata used for score synthesis."""

    scaffold_meta: dict
    task_version_meta: dict[str, str | None]
    verifier_artifacts: dict[str, str]
    agent_artifacts: dict[str, str]
    harbor_artifacts: dict[str, str]


@dataclass(frozen=True, slots=True)
class ScorecardBuildContext:
    """Input bundle for scorecard synthesis."""

    request: RunRequest
    layout: RunLayout
    context: ScaffoldContext
    artifacts: PersistedArtifacts
    execution: ExecutionPhaseResult


def _slug_fragment(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "unknown"


def _instance_name(run_id: str, start_time: datetime, request: RunRequest) -> str:
    timestamp = start_time.strftime("%Y%m%d-%H%M%SZ")
    task = _slug_fragment(request.task.name)
    agent = _slug_fragment(request.config.agent.value)
    model = _slug_fragment(request.config.model.qualified_name)
    return f"{timestamp}__{run_id}__{task}__{agent}__{model}"


def _command_timeout(command: list[str]) -> int:
    command_text = " ".join(command)
    if "typecheck" in command_text:
        return settings.timeouts.typecheck
    if "test:coverage" in command_text or " test" in command_text:
        return settings.timeouts.test
    if "build" in command_text:
        return settings.timeouts.build
    return settings.timeouts.command_default


def _workspace_has_tests(workspace: Path) -> bool:
    src_root = workspace / "src"
    if not src_root.exists():
        return False
    for pattern in ("**/*.test.ts", "**/*.test.tsx", "**/*.spec.ts", "**/*.spec.tsx"):
        if any(src_root.glob(pattern)):
            return True
    return False


def _preflight_cache_key(request: RunRequest, context: ScaffoldContext) -> str:
    payload = {
        "task_name": request.task.name,
        "task_yaml_hash": _hash_bytes((request.task_dir / "task.yaml").read_bytes()),
        "scaffold_fingerprint": context.scaffold_source.manifest.fingerprint,
        "required_commands": request.task.verification.required_commands,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def ensure_scaffold_preflight(request: RunRequest, context: ScaffoldContext) -> None:
    """Validate scaffold baseline commands once per task/scaffold version."""
    required_commands = request.task.verification.required_commands
    if not required_commands:
        return

    cache_dir = request.results_dir / ".preflight-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = _preflight_cache_key(request, context)
    cache_file = cache_dir / f"{cache_key}.ok.json"
    if cache_file.exists():
        return

    env = os.environ.copy()
    install = subprocess.run(
        ["bun", "install", "--frozen-lockfile"],
        cwd=context.workspace,
        capture_output=True,
        text=True,
        timeout=settings.timeouts.build,
        env=env,
    )
    if install.returncode != 0:
        output = (install.stdout + "\n" + install.stderr).strip()
        output = output[:8000]
        raise RuntimeError(
            f"Scaffold preflight failed: `bun install --frozen-lockfile` exited "
            f"{install.returncode}\n{output}"
        )

    has_tests = _workspace_has_tests(context.workspace)
    for command in required_commands:
        command_text = " ".join(command)
        if not has_tests and ("test:coverage" in command_text or command_text.endswith(" test")):
            continue
        completed = subprocess.run(
            command,
            cwd=context.workspace,
            capture_output=True,
            text=True,
            timeout=_command_timeout(command),
            env=env,
        )
        if completed.returncode != 0:
            output = (completed.stdout + "\n" + completed.stderr).strip()
            output = output[:8000]
            rendered = " ".join(shlex.quote(part) for part in command)
            raise RuntimeError(
                f"Scaffold preflight failed: `{rendered}` exited {completed.returncode}\n{output}"
            )

    cache_file.write_text(
        json.dumps(
            {
                "task_name": request.task.name,
                "scaffold_fingerprint": context.scaffold_source.manifest.fingerprint,
                "validated_at": datetime.now(UTC).isoformat(),
                "required_commands": required_commands,
            },
            indent=2,
        )
    )


def cleanup_stale_harbor_resources(
    *, include_containers: bool = True, include_build_processes: bool = False
) -> None:
    """Remove stale Harbor containers and/or orphaned build processes."""
    if include_containers:
        cleanup_stale_harbor_containers()
    if include_build_processes:
        cleanup_stale_harbor_build_processes()


def cleanup_stale_harbor_containers() -> None:
    """Remove stale Harbor task containers that can block/slow future runs."""
    try:
        listing = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return
    if listing.returncode != 0:
        return

    stale_ids: list[str] = []
    for line in listing.stdout.splitlines():
        parsed = _parse_container_listing_line(line)
        if not parsed:
            continue
        container_id, name, status = parsed
        if not _is_stale_harbor_container(name=name, status=status):
            continue
        stale_ids.append(container_id)
    for container_id in stale_ids:
        subprocess.run(
            ["docker", "rm", "-f", container_id],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )


def _parse_container_listing_line(line: str) -> tuple[str, str, str] | None:
    line = line.strip()
    if not line:
        return None
    parts = line.split("\t", maxsplit=2)
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


def _is_stale_harbor_container(*, name: str, status: str) -> bool:
    if not (
        HARNESS_STALE_CONTAINER_PATTERN.match(name) or HARBOR_GIT_MULTIBRANCH_PATTERN.match(name)
    ):
        return False
    # Do not kill active containers; parallel runs may be in-flight.
    return not status.startswith("Up ")


def cleanup_stale_harbor_build_processes() -> None:
    """Kill orphaned Harbor docker-compose/buildx build processes."""
    listing = subprocess.run(
        ["ps", "-ax", "-o", "pid=,command="],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if listing.returncode != 0:
        return
    stale_pids: list[int] = []
    for line in listing.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid_text, command = parts
        if not pid_text.isdigit():
            continue
        if HARNESS_STALE_BUILD_PATTERN.search(command) or HARNESS_STALE_BUILDX_PATTERN.search(
            command
        ):
            stale_pids.append(int(pid_text))

    for pid in stale_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue


def prepare_workspace(
    *,
    scaffold_dir: Path,
    target_dir: Path,
    task_dir: Path,
    agent: str,
    rules_variant: str,
) -> tuple[Path, Path | None]:
    """Prepare workspace by copying scaffold and injecting rules.

    Args:
        scaffold_dir: Path to resolved scaffold template/version
        target_dir: Path to create workspace
        task_dir: Path to task directory (contains rules/)
        agent: Agent name for rule file selection
        rules_variant: Rules variant (strict, minimal, none)

    Returns:
        Tuple of workspace path and injected rules file (if any)
    """
    # Copy scaffold to target
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(
        scaffold_dir,
        target_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("node_modules", ".next", "jobs"),
    )

    # Inject rules
    injected_rules: Path | None = None
    rules_dir = task_dir / "rules"
    if rules_dir.exists():
        injected_rules = inject_rules(rules_dir, target_dir, agent, rules_variant)

    # Generate initial manifest for baseline
    manifest = generate_manifest(target_dir)
    save_manifest(manifest, target_dir / "scaffold.manifest.json")

    return target_dir, injected_rules


def _load_baseline_scripts(scaffold_source: ScaffoldSource) -> dict[str, str]:
    package_path = scaffold_source.path / "package.json"
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


def _build_verifier_task_spec(request: RunRequest, context: ScaffoldContext) -> dict:
    return {
        "task_name": request.task.name,
        "verification": {
            "max_gate_failures": request.task.verification.max_gate_failures,
            "coverage_threshold": request.task.verification.coverage_threshold,
            "min_quality_score": request.task.verification.min_quality_score,
            "gates": [
                {
                    "name": gate.name,
                    "command": gate.command,
                    "on_failure": gate.on_failure,
                }
                for gate in request.task.verification.gates
            ],
        },
        "compliance": {
            "deterministic_checks": [
                {
                    "type": check.type,
                    "pattern": check.pattern,
                    "description": check.description,
                }
                for check in request.task.compliance.deterministic_checks
            ],
            "requirements": [
                {
                    "id": requirement.id,
                    "description": requirement.description,
                    "check": {
                        "type": requirement.check.type,
                        "pattern": requirement.check.pattern,
                        "description": requirement.check.description,
                    },
                    "required_test_patterns": requirement.required_test_patterns,
                }
                for requirement in request.task.compliance.requirements
            ],
        },
        "visual": (
            {
                "reference_image": request.task.visual.reference_image,
                "screenshot_command": request.task.visual.screenshot_command,
                "threshold": request.task.visual.threshold,
            }
            if request.task.visual
            else None
        ),
        "weights": {
            "functional": settings.weights.functional,
            "compliance": settings.weights.compliance,
            "visual": settings.weights.visual,
            "efficiency": settings.weights.efficiency,
        },
        "baseline_scripts": _load_baseline_scripts(context.scaffold_source),
    }


def _verifier_script_template_path() -> Path:
    return Path(__file__).parent / "assets" / "verifier-score-task.mjs"


def _verifier_scorer_script() -> str:
    return _verifier_script_template_path().read_text(encoding="utf-8")


def create_harbor_task_bundle(request: RunRequest, context: ScaffoldContext, run_id: str) -> Path:
    """Build a Harbor-compatible task directory from the scaffold workspace."""
    bundle_dir = context.workspace / f"harbor-task-{_slug_fragment(request.task.name)}-{run_id}"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)

    environment_dir = bundle_dir / "environment"
    app_dir = environment_dir / "app"
    tests_dir = bundle_dir / "tests"
    environment_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)

    shutil.copytree(
        context.workspace,
        app_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            "node_modules",
            ".next",
            "jobs",
            "harbor-task",
            "harbor-task-*",
            "actual.png",
            "diff.png",
        ),
    )
    if request.task.visual:
        reference_path = Path(request.task.visual.reference_image)
        if not reference_path.is_absolute():
            source_reference = (request.task_dir / reference_path).resolve()
            if source_reference.exists():
                target_reference = app_dir / reference_path
                target_reference.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_reference, target_reference)

    instruction = (
        request.task.prompt.strip()
        + "\n\nYou are working in `/app`.\nFollow rules in `/app/AGENTS.md`.\n"
    )
    (bundle_dir / "instruction.md").write_text(instruction)
    (bundle_dir / "task.toml").write_text(
        f"""version = "1.0"

[metadata]
name = "{request.task.name}"
source = "scaffold-spec"

[verifier]
timeout_sec = 300.0

[agent]
timeout_sec = {float(request.config.timeout_sec)}

[environment]
build_timeout_sec = 1800.0
cpus = 2
memory_mb = 4096
storage_mb = 10240
allow_internet = true
"""
    )
    dockerfile = """FROM oven/bun:1
WORKDIR /app
COPY app/package.json app/bun.lock /app/
RUN bun install --frozen-lockfile
COPY app/ /app/
"""
    if request.task.visual:
        dockerfile += """RUN apt-get update && apt-get install -y --no-install-recommends \\
  libx11-6 \\
  libxext6 \\
  libxcb1 \\
  libglib2.0-0 \\
  libnspr4 \\
  libnss3 \\
  libdbus-1-3 \\
  libatk1.0-0 \\
  libexpat1 \\
  libatspi2.0-0 \\
  libxcomposite1 \\
  libxdamage1 \\
  libxfixes3 \\
  libxrandr2 \\
  libgbm1 \\
  libxkbcommon0 \\
  libasound2 \\
  && rm -rf /var/lib/apt/lists/*
RUN bunx playwright install chromium
"""
    (environment_dir / "Dockerfile").write_text(dockerfile)
    (tests_dir / "task-spec.json").write_text(
        json.dumps(_build_verifier_task_spec(request, context), indent=2)
    )
    scorer_path = tests_dir / "score-task.mjs"
    scorer_path.write_text(_verifier_scorer_script())
    scorer_path.chmod(0o755)
    test_script = tests_dir / "test.sh"
    test_script.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p /logs/verifier /logs/agent
if [[ ! -d /app ]]; then
  echo "Missing /app workspace" >&2
  echo "0" > /logs/verifier/reward.txt
  exit 1
fi

if ! bun run "$SCRIPT_DIR/score-task.mjs" "$SCRIPT_DIR/task-spec.json"; then
  echo "0" > /logs/verifier/reward.txt
fi

tar \
  --exclude='./node_modules' \
  --exclude='./.next' \
  --exclude='./jobs' \
  --exclude='./actual.png' \
  --exclude='./diff.png' \
  -czf /logs/agent/final-app.tar.gz \
  -C /app .
"""
    )
    test_script.chmod(0o755)
    return bundle_dir


def initialize_run(request: RunRequest) -> RunLayout:
    """Create run ids and canonical output directories."""
    run_id = str(uuid.uuid4())[:8]
    start_time = datetime.now(UTC)
    runs_root = request.results_dir / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    instance_name = _instance_name(run_id, start_time, request)
    root_dir = runs_root / instance_name
    if root_dir.exists():
        shutil.rmtree(root_dir)
    summary_dir = root_dir / "summary"
    scaffold_dir = root_dir / "scaffold"
    verifier_dir = root_dir / "verifier"
    agent_dir = root_dir / "agent"
    harbor_dir = root_dir / "harbor"
    for path in (summary_dir, scaffold_dir, verifier_dir, agent_dir, harbor_dir):
        path.mkdir(parents=True, exist_ok=True)
    return RunLayout(
        run_id=run_id,
        start_time=start_time,
        instance_name=instance_name,
        root_dir=root_dir,
        summary_dir=summary_dir,
        scaffold_dir=scaffold_dir,
        verifier_dir=verifier_dir,
        agent_dir=agent_dir,
        harbor_dir=harbor_dir,
        result_json_path=summary_dir / "result.json",
        summary_readme_path=summary_dir / "README.md",
        jobs_root=request.results_dir.parent / "jobs",
    )


def prepare_run_context(request: RunRequest) -> ScaffoldContext:
    """Resolve scaffold source, workspace, and manifest metadata."""
    from .scaffold import record_scaffold_metadata, resolve_scaffold_source

    scaffold_source = resolve_scaffold_source(
        request.scaffold_root, request.task.scaffold.template, request.task.scaffold.version
    )

    workspace, injected_rules = prepare_workspace(
        scaffold_dir=scaffold_source.path,
        target_dir=request.workspace_dir,
        task_dir=request.task_dir,
        agent=request.config.agent.value,
        rules_variant=request.config.rules_variant,
    )
    manifest_path = workspace / "scaffold.manifest.json"
    if not manifest_path.exists():
        manifest = generate_manifest(workspace)
        save_manifest(manifest, manifest_path)

    baseline_manifest_path = workspace / ".baseline-scaffold.json"
    shutil.copy2(scaffold_source.manifest_path, baseline_manifest_path)

    metadata_path = record_scaffold_metadata(
        workspace,
        scaffold_source,
        manifest_path,
        baseline_manifest_path,
        request.config.rules_variant,
    )

    return ScaffoldContext(
        scaffold_source=scaffold_source,
        workspace=workspace,
        injected_rules=injected_rules,
        manifest_path=manifest_path,
        baseline_manifest_path=baseline_manifest_path,
        metadata_path=metadata_path,
    )


def execute_harbor(request: HarborExecutionRequest) -> HarborExecutionResult:
    """Execute Harbor against a local task bundle."""
    request.jobs_dir.mkdir(parents=True, exist_ok=True)
    job_name = f"orchestrator-{request.run_id}"
    job_dir = request.jobs_dir / job_name
    harbor_cmd = request.adapter.build_harbor_command(
        task_path=request.task_bundle_path,
        job_name=job_name,
        jobs_dir=request.jobs_dir,
    )

    execution_error = _run_harbor_process(
        harbor_cmd=harbor_cmd,
        workspace=request.workspace,
        timeout_sec=request.timeout_sec,
        run_env=request.run_env,
        run_harbor_dir=request.run_harbor_dir,
        job_dir=job_dir,
    )
    if execution_error:
        return _terminated_harbor_result(job_dir=job_dir, reason=execution_error, trial_dir=None)

    trial_dir = _select_trial_dir(job_dir)
    failure_reason = detect_trial_failure(trial_dir) if trial_dir else None
    if failure_reason:
        return _terminated_harbor_result(
            job_dir=job_dir,
            reason=failure_reason,
            trial_dir=trial_dir,
        )

    return HarborExecutionResult(
        terminated_early=False,
        termination_reason=None,
        job_dir=job_dir,
        trial_dir=trial_dir,
    )


def _harbor_process_timeout(task_timeout_sec: int) -> int:
    """Allow Harbor build + verifier overhead beyond agent task timeout."""
    return max(task_timeout_sec + HARBOR_TIMEOUT_BUFFER_SEC, int(task_timeout_sec * 1.25))


def _terminated_harbor_result(
    *,
    job_dir: Path,
    reason: str,
    trial_dir: Path | None,
) -> HarborExecutionResult:
    return HarborExecutionResult(
        terminated_early=True,
        termination_reason=reason,
        job_dir=job_dir,
        trial_dir=trial_dir,
    )


def _run_harbor_process(
    *,
    harbor_cmd: list[str],
    workspace: Path,
    timeout_sec: int,
    run_env: dict[str, str],
    run_harbor_dir: Path,
    job_dir: Path,
) -> str | None:
    run_harbor_dir.mkdir(parents=True, exist_ok=True)
    command_path = run_harbor_dir / "command.txt"
    stdout_path = run_harbor_dir / "harbor-stdout.log"
    stderr_path = run_harbor_dir / "harbor-stderr.log"
    command_path.write_text(" ".join(shlex.quote(part) for part in harbor_cmd) + "\n")

    try:
        process = subprocess.Popen(
            harbor_cmd,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=run_env,
            start_new_session=True,
        )
    except FileNotFoundError:
        return "Harbor not installed"

    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_group(process)
        stdout, stderr = process.communicate()

    stdout_path.write_text(stdout or "")
    stderr_path.write_text(stderr or "")

    if timed_out:
        return _timeout_reason(timeout_sec=timeout_sec, job_dir=job_dir)
    if process.returncode != 0:
        return f"Harbor exited with code {process.returncode}"
    return None


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _timeout_reason(*, timeout_sec: int, job_dir: Path) -> str:
    if not job_dir.exists():
        return f"Timeout expired after {timeout_sec}s before Harbor created a job directory."
    trial_dir = _select_trial_dir(job_dir)
    if not trial_dir:
        return f"Timeout expired after {timeout_sec}s before Harbor created a trial directory."
    result_json = trial_dir / "result.json"
    if not result_json.exists():
        return f"Timeout expired after {timeout_sec}s before trial result.json was written."
    return f"Timeout expired after {timeout_sec}s."


def _select_trial_dir(job_dir: Path) -> Path | None:
    if not job_dir.exists():
        return None
    trial_dirs = sorted([candidate for candidate in job_dir.iterdir() if candidate.is_dir()])
    with_agent = next(
        (candidate for candidate in trial_dirs if (candidate / "agent").exists()), None
    )
    return with_agent or (trial_dirs[0] if trial_dirs else None)


def detect_trial_failure(trial_dir: Path | None) -> str | None:
    """Extract a terminal failure reason from Harbor trial artifacts."""
    if not trial_dir:
        return None
    return _trial_exception_reason(trial_dir) or _codex_turn_failure_reason(trial_dir)


def _trial_exception_reason(trial_dir: Path) -> str | None:
    result_data = _load_json_dict(trial_dir / "result.json")
    exception_info = result_data.get("exception_info")
    if not isinstance(exception_info, dict):
        return None
    message = exception_info.get("exception_message")
    if not isinstance(message, str):
        return None
    message = message.strip()
    if not message:
        return None
    return f"Harbor trial exception: {message}"


def _codex_turn_failure_reason(trial_dir: Path) -> str | None:
    codex_log = trial_dir / "agent" / "codex.txt"
    if not codex_log.exists():
        return None
    for line in reversed(codex_log.read_text(errors="ignore").splitlines()):
        if '"type":"turn.failed"' not in line:
            continue
        message = _codex_turn_failure_message(line)
        return f"Codex turn failed: {message}" if message else "Codex turn failed."
    return None


def _codex_turn_failure_message(line: str) -> str | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        message: str | None = line
    else:
        raw_message = payload.get("error", {}).get("message")
        message = raw_message if isinstance(raw_message, str) else None
    if not message:
        return None
    message = message.strip()
    return message or None


def _load_json_dict(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_iso8601_timestamp(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _duration_seconds(start: str | None, end: str | None) -> float | None:
    start_ts = _parse_iso8601_timestamp(start)
    end_ts = _parse_iso8601_timestamp(end)
    if not start_ts or not end_ts:
        return None
    duration = (end_ts - start_ts).total_seconds()
    return round(max(0.0, duration), 3)


def _phase_duration(payload: dict, phase_key: str) -> float | None:
    phase_data = payload.get(phase_key)
    if not isinstance(phase_data, dict):
        return None
    return _duration_seconds(phase_data.get("started_at"), phase_data.get("finished_at"))


def _harbor_phase_timings(trial_dir: Path | None) -> dict[str, float]:
    if not trial_dir:
        return {}
    payload = _load_json_dict(trial_dir / "result.json")
    if not payload:
        return {}

    timings = {
        "trial_total_sec": _duration_seconds(payload.get("started_at"), payload.get("finished_at")),
        "environment_setup_sec": _phase_duration(payload, "environment_setup"),
        "agent_setup_sec": _phase_duration(payload, "agent_setup"),
        "agent_execution_sec": _phase_duration(payload, "agent_execution"),
        "verifier_sec": _phase_duration(payload, "verifier"),
    }
    return {key: value for key, value in timings.items() if value is not None}


def _verifier_scorecard_path(trial_dir: Path | None) -> Path | None:
    if not trial_dir:
        return None
    return trial_dir / "verifier" / "scorecard.json"


def _load_verifier_outputs(trial_dir: Path | None) -> tuple[EvaluationOutputs | None, str | None]:
    scorecard_path = _verifier_scorecard_path(trial_dir)
    if not scorecard_path:
        return None, "Harbor trial directory not found."
    if not scorecard_path.exists():
        return None, f"Verifier scorecard missing: {scorecard_path}"

    try:
        payload = json.loads(scorecard_path.read_text())
    except json.JSONDecodeError as exc:
        return None, f"Invalid verifier scorecard JSON: {exc.msg}"
    if not isinstance(payload, dict):
        return None, "Invalid verifier scorecard content: expected object root."

    try:
        gate_history_payload = payload.get("gate_history")
        if not isinstance(gate_history_payload, list):
            raise ValueError("scorecard.gate_history must be a list")
        gate_history = [GateEvent.model_validate(item) for item in gate_history_payload]

        scaffold_audit_payload = payload.get("scaffold_audit")
        scaffold_audit = (
            ScaffoldAudit.model_validate(scaffold_audit_payload)
            if scaffold_audit_payload is not None
            else None
        )

        outputs = EvaluationOutputs(
            functional=FunctionalScore.model_validate(payload.get("functional")),
            compliance=ComplianceScore.model_validate(payload.get("compliance")),
            visual=(
                VisualScore.model_validate(payload.get("visual"))
                if payload.get("visual") is not None
                else None
            ),
            efficiency=EfficiencyScore.model_validate(payload.get("efficiency")),
            coverage=CoverageScore.model_validate(payload.get("coverage")),
            requirements=RequirementCoverageScore.model_validate(payload.get("requirements")),
            qualification=QualificationScore.model_validate(payload.get("qualification")),
            scaffold_audit=scaffold_audit,
            gate_history=gate_history,
        )
    except (ValidationError, ValueError) as exc:
        return None, f"Invalid verifier scorecard content: {exc}"

    return outputs, None


def persist_scaffold_artifacts(context: ScaffoldContext, scaffold_dir: Path) -> ArtifactPaths:
    """Persist scaffold artifacts for later audits."""
    artifact_manifest = Path(
        shutil.copy2(context.manifest_path, scaffold_dir / "workspace.manifest.json")
    )
    artifact_baseline = Path(
        shutil.copy2(context.scaffold_source.manifest_path, scaffold_dir / "baseline.manifest.json")
    )
    artifact_meta = Path(shutil.copy2(context.metadata_path, scaffold_dir / "scaffold-meta.json"))
    artifact_rules = None
    if context.injected_rules and context.injected_rules.exists():
        artifact_rules = Path(
            shutil.copy2(
                context.injected_rules,
                scaffold_dir / context.injected_rules.name.replace(" ", "_"),
            )
        )

    return ArtifactPaths(
        baseline=artifact_baseline,
        manifest=artifact_manifest,
        metadata=artifact_meta,
        rules=artifact_rules,
    )


def build_scaffold_meta(context: ScaffoldContext, artifacts: ArtifactPaths) -> dict:
    """Build scaffold metadata for the scorecard."""
    return {
        "template": context.scaffold_source.template,
        "version": context.scaffold_source.version,
        "fingerprint": context.scaffold_source.manifest.fingerprint,
        "baseline_manifest": context.baseline_manifest_path.name,
        "workspace_manifest": context.manifest_path.name,
        "metadata_file": context.metadata_path.name,
        "rules_file": context.injected_rules.name if context.injected_rules else None,
        "artifacts": {
            "baseline_manifest": str(artifacts.baseline),
            "workspace_manifest": str(artifacts.manifest),
            "metadata": str(artifacts.metadata),
            **({"rules": str(artifacts.rules)} if artifacts.rules else {}),
        },
    }


def _hash_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def build_task_version_meta(request: RunRequest, context: ScaffoldContext) -> dict[str, str | None]:
    """Build deterministic task/scaffold fingerprint metadata."""
    task_path = request.task_dir / "task.yaml"
    task_yaml_hash = _hash_bytes(task_path.read_bytes()) if task_path.exists() else None

    seed_payload = {
        "scoring_schema_version": SCORING_SCHEMA_VERSION,
        "task_yaml_hash": task_yaml_hash,
        "task_model": request.task.model_dump(mode="json", exclude_none=True),
        "scaffold_template": context.scaffold_source.template,
        "scaffold_version": context.scaffold_source.version,
        "scaffold_fingerprint": context.scaffold_source.manifest.fingerprint,
        "rules_variant": request.config.rules_variant,
    }
    seed = json.dumps(seed_payload, sort_keys=True, separators=(",", ":")).encode()
    return {
        "scoring_schema_version": SCORING_SCHEMA_VERSION,
        "task_yaml_hash": task_yaml_hash,
        "task_fingerprint": _hash_bytes(seed),
    }


def persist_verifier_artifacts(
    harbor_result: HarborExecutionResult, verifier_dir: Path
) -> dict[str, str]:
    """Persist verifier outputs for run/task audits."""
    if not harbor_result.trial_dir:
        return {}
    source_dir = harbor_result.trial_dir / "verifier"
    if not source_dir.exists():
        return {}

    copied: dict[str, str] = {}
    for filename in (
        "scorecard.json",
        "gate-history.json",
        "qualification.json",
        "reward.txt",
        "test-stdout.txt",
    ):
        source = source_dir / filename
        if not source.exists():
            continue
        target = verifier_dir / filename
        copied[filename] = str(shutil.copy2(source, target))
    return copied


def persist_agent_artifacts(
    harbor_result: HarborExecutionResult, agent_dir: Path
) -> dict[str, str]:
    """Persist Harbor agent transcripts and command history."""
    if not harbor_result.trial_dir:
        return {}
    source = harbor_result.trial_dir / "agent"
    if not source.exists():
        return {}

    copied: dict[str, str] = {}
    for filename in ("trajectory.json", "codex.txt", "install.sh", "final-app.tar.gz"):
        src = source / filename
        if src.exists():
            copied[filename] = str(shutil.copy2(src, agent_dir / filename))

    setup_dir = source / "setup"
    if setup_dir.exists():
        target = agent_dir / "setup"
        shutil.copytree(setup_dir, target, dirs_exist_ok=True)
        copied["setup"] = str(target)

    commands_dir = agent_dir / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    for command_dir in sorted(source.glob("command-*")):
        if not command_dir.is_dir():
            continue
        target = commands_dir / command_dir.name
        shutil.copytree(command_dir, target, dirs_exist_ok=True)
        copied[f"commands/{command_dir.name}"] = str(target)

    return copied


def persist_harbor_artifacts(
    harbor_result: HarborExecutionResult, harbor_dir: Path
) -> dict[str, str]:
    """Persist Harbor job/trial metadata and logs for run review."""
    copied: dict[str, str] = {}
    job_files = ("config.json", "result.json", "job.log")
    job_target = harbor_dir / "job"
    job_target.mkdir(parents=True, exist_ok=True)
    for filename in job_files:
        source = harbor_result.job_dir / filename
        if source.exists():
            copied[f"job/{filename}"] = str(shutil.copy2(source, job_target / filename))

    if harbor_result.trial_dir:
        trial_target = harbor_dir / "trial"
        trial_target.mkdir(parents=True, exist_ok=True)
        for filename in ("config.json", "result.json", "trial.log"):
            source = harbor_result.trial_dir / filename
            if source.exists():
                copied[f"trial/{filename}"] = str(shutil.copy2(source, trial_target / filename))
    return copied


def write_summary_readme(
    layout: RunLayout,
    request: RunRequest,
    scorecard: Scorecard,
    harbor_result: HarborExecutionResult,
) -> None:
    """Write a human-readable run summary with canonical/raw pointers."""
    lines = [
        "# Run Summary",
        "",
        f"- run_id: `{layout.run_id}`",
        f"- started_at_utc: `{layout.start_time.isoformat()}`",
        f"- task: `{request.task.name}`",
        f"- agent: `{request.config.agent.value}`",
        f"- model: `{request.config.model.qualified_name}`",
        f"- qualified: `{scorecard.qualification.passed}`",
        f"- voided: `{scorecard.voided}`",
        f"- void_reasons: `{scorecard.void_reasons}`",
        f"- quality_score: `{scorecard.quality_score:.6f}`",
        f"- composite_score: `{scorecard.composite_score:.6f}`",
        "",
        "## Pointers",
        f"- canonical_run_dir: `{layout.root_dir}`",
        f"- raw_harbor_job_dir: `{harbor_result.job_dir}`",
        f"- raw_harbor_trial_dir: `{harbor_result.trial_dir}`",
        f"- summary_result_json: `{layout.result_json_path}`",
        "",
        "## Key Artifacts",
        f"- verifier_scorecard: `{layout.verifier_dir / 'scorecard.json'}`",
        f"- agent_trajectory: `{layout.agent_dir / 'trajectory.json'}`",
        f"- agent_event_stream: `{layout.agent_dir / 'codex.txt'}`",
    ]
    layout.summary_readme_path.write_text("\n".join(lines) + "\n")


def _read_jsonl_dicts(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries: list[dict] = []
    for line in path.read_text(errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def _extract_item_completed(entry: dict) -> dict | None:
    if entry.get("type") != "item.completed":
        return None
    item = entry.get("item")
    return item if isinstance(item, dict) else None


def _extract_usage(entry: dict) -> tuple[int, int, int] | None:
    if entry.get("type") != "turn.completed":
        return None
    usage = entry.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    cached_input_tokens = int(usage.get("cached_input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    return input_tokens, cached_input_tokens, output_tokens


def _normalize_command(command: str) -> str:
    command = command.strip()
    if not command:
        return command
    try:
        tokens = shlex.split(command)
    except ValueError:
        return command
    if "-lc" in tokens:
        idx = tokens.index("-lc")
        if idx + 1 < len(tokens):
            return tokens[idx + 1].strip()
    return command


def _command_failed(item: dict) -> bool:
    status = item.get("status")
    exit_code = int(item.get("exit_code", 0) or 0)
    return status == "failed" or exit_code != 0


def _verification_command_strings(task: TaskDefinition) -> list[str]:
    patterns: list[str] = []
    for gate in task.verification.gates:
        patterns.append(shlex.join(gate.command))
    for command in task.verification.required_commands:
        patterns.append(shlex.join(command))
    deduped = list(dict.fromkeys(patterns))
    return [pattern for pattern in deduped if pattern]


def _command_matches_pattern(command: str, patterns: list[str]) -> str | None:
    for pattern in sorted(patterns, key=len, reverse=True):
        if command == pattern or command.startswith(f"{pattern} "):
            return pattern
    return None


def _usage_from_entries(entries: list[dict]) -> tuple[int, int, int]:
    usage_tuple = next(
        (usage for usage in reversed([_extract_usage(entry) for entry in entries]) if usage),
        (0, 0, 0),
    )
    return usage_tuple


def _command_output(item: dict) -> str:
    aggregated = item.get("aggregated_output")
    if isinstance(aggregated, str) and aggregated:
        return aggregated
    stdout = str(item.get("stdout", "") or "")
    stderr = str(item.get("stderr", "") or "")
    return "\n".join(part for part in (stdout, stderr) if part)


def _command_records(entries: list[dict]) -> list[CommandRecord]:
    records: list[CommandRecord] = []
    for entry in entries:
        item = _extract_item_completed(entry)
        if not item or item.get("type") != "command_execution":
            continue
        command = _normalize_command(str(item.get("command", "")))
        records.append(
            CommandRecord(
                command=command,
                failed=_command_failed(item),
                output=_command_output(item),
            )
        )
    return records


def _verification_attempts(
    records: list[CommandRecord],
    verification_patterns: list[str],
) -> tuple[dict[str, int], dict[str, int]]:
    attempts_by_pattern: dict[str, int] = {pattern: 0 for pattern in verification_patterns}
    failures_by_pattern: dict[str, int] = {pattern: 0 for pattern in verification_patterns}
    for record in records:
        matched = _command_matches_pattern(record.command, verification_patterns)
        if not matched:
            continue
        attempts_by_pattern[matched] += 1
        if record.failed:
            failures_by_pattern[matched] += 1
    return attempts_by_pattern, failures_by_pattern


def _first_pass_status(
    records: list[CommandRecord], verification_patterns: list[str]
) -> dict[str, str]:
    status: dict[str, str] = {pattern: "missing" for pattern in verification_patterns}
    for record in records:
        matched = _command_matches_pattern(record.command, verification_patterns)
        if not matched or status[matched] != "missing":
            continue
        status[matched] = "fail" if record.failed else "pass"
    return status


def _matches_typecheck(command_text: str, combined: str) -> bool:
    if "typecheck" in command_text:
        return True
    return "tsc" in combined


def _matches_lint(command_text: str, combined: str) -> bool:
    if "lint" in command_text:
        return True
    if "ultracite" in combined:
        return True
    return "eslint" in combined


def _matches_coverage(command_text: str) -> bool:
    if "test:coverage" in command_text:
        return True
    return "--coverage" in command_text


def _matches_test(command_text: str) -> bool:
    if " test" in f" {command_text}":
        return True
    return command_text.endswith("test")


def _failure_category(record: CommandRecord) -> str:
    command_text = record.command.lower()
    output_text = record.output.lower()
    combined = f"{command_text}\n{output_text}"
    if "not found" in combined:
        return "missing_command"
    if _matches_typecheck(command_text, combined):
        return "typecheck"
    if _matches_lint(command_text, combined):
        return "lint"
    if _matches_coverage(command_text):
        return "coverage"
    if _matches_test(command_text):
        return "test"
    if "build" in command_text:
        return "build"
    return "other"


def _failure_category_counts(records: list[CommandRecord]) -> dict[str, int]:
    categories: dict[str, int] = {}
    for record in records:
        if not record.failed:
            continue
        category = _failure_category(record)
        categories[category] = categories.get(category, 0) + 1
    return categories


def _empty_process_metrics() -> ProcessMetrics:
    return ProcessMetrics(
        uncached_input_tokens=0,
        output_tokens=0,
        command_count=0,
        failed_command_count=0,
        verification_rounds=0,
        repeated_verification_failures=0,
        required_verification_commands=0,
        executed_required_verification_commands=0,
    )


def _count_failed_commands(records: list[CommandRecord]) -> int:
    return sum(1 for record in records if record.failed)


def _count_repeated_failures(failures_by_pattern: dict[str, int]) -> int:
    return sum(max(0, count - 1) for count in failures_by_pattern.values())


def _count_executed_required(attempts_by_pattern: dict[str, int]) -> int:
    return sum(1 for count in attempts_by_pattern.values() if count > 0)


def _first_pass_counts(first_pass_status: dict[str, str]) -> tuple[int, int, int]:
    passed = sum(1 for status in first_pass_status.values() if status == "pass")
    failed = sum(1 for status in first_pass_status.values() if status == "fail")
    missing = sum(1 for status in first_pass_status.values() if status == "missing")
    return passed, failed, missing


def collect_process_metrics(
    task: TaskDefinition,
    trial_dir: Path | None,
) -> ProcessMetrics:
    """Collect optimization metrics from Harbor Codex logs."""
    if not trial_dir:
        return _empty_process_metrics()

    entries = _read_jsonl_dicts(trial_dir / "agent" / "codex.txt")
    usage_tuple = _usage_from_entries(entries)
    input_tokens, cached_input_tokens, output_tokens = usage_tuple
    uncached_input_tokens = max(0, input_tokens - cached_input_tokens)

    records = _command_records(entries)
    verification_patterns = _verification_command_strings(task)
    attempts_by_pattern, failures_by_pattern = _verification_attempts(
        records, verification_patterns
    )
    first_pass_status = _first_pass_status(records, verification_patterns)
    failure_categories = _failure_category_counts(records)
    command_count = len(records)
    failed_command_count = _count_failed_commands(records)
    verification_rounds = max(attempts_by_pattern.values(), default=0)
    repeated_failures = _count_repeated_failures(failures_by_pattern)
    executed_required = _count_executed_required(attempts_by_pattern)
    first_pass_successes, first_pass_failures, missing_required = _first_pass_counts(
        first_pass_status
    )
    return ProcessMetrics(
        uncached_input_tokens=uncached_input_tokens,
        output_tokens=output_tokens,
        command_count=command_count,
        failed_command_count=failed_command_count,
        verification_rounds=verification_rounds,
        repeated_verification_failures=repeated_failures,
        required_verification_commands=len(verification_patterns),
        executed_required_verification_commands=executed_required,
        failed_command_categories=failure_categories,
        required_verification_first_pass=first_pass_status,
        first_pass_verification_successes=first_pass_successes,
        first_pass_verification_failures=first_pass_failures,
        missing_required_verification_commands=missing_required,
    )


def _events_from_command(timestamp: str, item: dict) -> list[SessionEvent]:
    command = _normalize_command(str(item.get("command", "")))
    return [
        SessionEvent(
            timestamp=timestamp,
            event_type="bash_command",
            data={"command": command},
        ),
        SessionEvent(
            timestamp=timestamp,
            event_type="gate_result",
            data={
                "status": item.get("status"),
                "exit_code": int(item.get("exit_code", 0) or 0),
            },
        ),
    ]


def _events_from_file_changes(timestamp: str, item: dict) -> list[SessionEvent]:
    file_events: list[SessionEvent] = []
    for change in item.get("changes", []) or []:
        path = change.get("path")
        if not path:
            continue
        file_events.append(
            SessionEvent(
                timestamp=timestamp,
                event_type="file_change",
                data={"file_path": str(path)},
            )
        )
    return file_events


def _events_from_item(timestamp: str, item: dict) -> list[SessionEvent]:
    item_type = item.get("type")
    if item_type == "command_execution":
        return _events_from_command(timestamp, item)
    if item_type == "file_change":
        return _events_from_file_changes(timestamp, item)
    if item_type != "agent_message":
        return []
    text = item.get("text")
    if not text:
        return []
    return [
        SessionEvent(
            timestamp=timestamp,
            event_type="assistant_message",
            data={"content": str(text)},
        )
    ]


def collect_session_events(trial_dir: Path | None) -> list[SessionEvent]:
    """Project Harbor/Codex log lines into normalized session events."""
    if not trial_dir:
        return []
    events: list[SessionEvent] = []
    for entry in _read_jsonl_dicts(trial_dir / "agent" / "codex.txt"):
        timestamp = str(entry.get("timestamp") or datetime.now(UTC).isoformat())
        item = _extract_item_completed(entry)
        if not item:
            continue
        events.extend(_events_from_item(timestamp, item))
    return events


def _coverage_from_summary_file(workspace: Path) -> tuple[float | None, str | None]:
    summary_path = workspace / "coverage" / "coverage-summary.json"
    if not summary_path.exists():
        return None, None
    try:
        payload = json.loads(summary_path.read_text())
    except json.JSONDecodeError:
        return None, None
    total = payload.get("total")
    if not isinstance(total, dict):
        return None, None
    values: list[float] = []
    for key in ("lines", "statements", "functions", "branches"):
        metric = total.get(key)
        if not isinstance(metric, dict):
            continue
        pct = metric.get("pct")
        if isinstance(pct, (int, float)):
            values.append(float(pct))
    if not values:
        return None, None
    return min(values) / 100.0, str(summary_path)


def _parse_coverage_percent(output: str) -> float | None:
    values: list[float] = []
    for pattern in (
        r"Lines\s*:\s*([0-9]+(?:\.[0-9]+)?)%",
        r"Statements\s*:\s*([0-9]+(?:\.[0-9]+)?)%",
        r"Functions\s*:\s*([0-9]+(?:\.[0-9]+)?)%",
        r"Branches\s*:\s*([0-9]+(?:\.[0-9]+)?)%",
    ):
        values.extend(float(match) for match in re.findall(pattern, output, re.IGNORECASE))
    table_match = re.search(
        (
            r"All files\s*\|\s*([0-9]+(?:\.[0-9]+)?)\s*\|\s*([0-9]+(?:\.[0-9]+)?)\s*\|\s*"
            r"([0-9]+(?:\.[0-9]+)?)\s*\|\s*([0-9]+(?:\.[0-9]+)?)"
        ),
        output,
    )
    if table_match:
        values.extend(float(value) for value in table_match.groups())
    if not values:
        return None
    return min(values) / 100.0


def _coverage_from_gate_history(gate_history: list[GateEvent]) -> tuple[float | None, str | None]:
    for event in reversed(gate_history):
        gate_hint = f"{event.gate_name} {event.command}".lower()
        if "coverage" not in gate_hint:
            continue
        parsed = _parse_coverage_percent(f"{event.stdout}\n{event.stderr}")
        if parsed is not None:
            return parsed, f"gate:{event.gate_name}"
    return None, None


def evaluate_coverage(
    workspace: Path,
    gate_history: list[GateEvent],
    threshold: float | None,
) -> CoverageScore:
    """Evaluate test coverage threshold compliance."""
    measured, source = _coverage_from_summary_file(workspace)
    if measured is None:
        measured, source = _coverage_from_gate_history(gate_history)
    passed = threshold is None or (measured is not None and measured >= threshold)
    return CoverageScore(
        threshold=threshold,
        measured=measured,
        source=source,
        passed=passed,
    )


def _test_file_paths(workspace: Path) -> list[Path]:
    patterns = (
        "**/*.test.ts",
        "**/*.test.tsx",
        "**/*.spec.ts",
        "**/*.spec.tsx",
    )
    test_paths: list[Path] = []
    for pattern in patterns:
        test_paths.extend((workspace / "src").glob(pattern))
    return test_paths


def _has_test_pattern(test_sources: list[str], pattern: str) -> bool:
    return any(re.search(pattern, source, re.MULTILINE) for source in test_sources)


def evaluate_requirements(
    workspace: Path,
    requirements: list[RequirementSpec],
) -> RequirementCoverageScore:
    """Evaluate requirement implementation and requirement-to-test mapping."""
    if not requirements:
        return RequirementCoverageScore()

    test_sources = [path.read_text(errors="ignore") for path in _test_file_paths(workspace)]
    missing_ids: list[str] = []
    gap_ids: list[str] = []
    pattern_gaps: dict[str, list[str]] = {}
    satisfied = 0
    mapped = 0

    for requirement in requirements:
        requirement_check = run_deterministic_check(requirement.check, workspace)
        if requirement_check.passed:
            satisfied += 1
        else:
            missing_ids.append(requirement.id)

        patterns = requirement.required_test_patterns
        missing_patterns = [
            pattern for pattern in patterns if not _has_test_pattern(test_sources, pattern)
        ]
        mapped_for_requirement = bool(patterns) and not missing_patterns
        if mapped_for_requirement:
            mapped += 1
        else:
            gap_ids.append(requirement.id)
            if missing_patterns:
                pattern_gaps[requirement.id] = missing_patterns

    return RequirementCoverageScore(
        total_requirements=len(requirements),
        satisfied_requirements=satisfied,
        mapped_requirements=mapped,
        missing_requirement_ids=missing_ids,
        requirement_gap_ids=gap_ids,
        requirement_pattern_gaps=pattern_gaps,
    )


def terminated_outputs(reason: str | None) -> EvaluationOutputs:
    """Create deterministic zeroed scores for terminated runs."""
    failure_reason = reason or "Run terminated before scoring."
    return EvaluationOutputs(
        functional=FunctionalScore(
            passed=False,
            tests_passed=0,
            tests_total=0,
            build_succeeded=False,
            gates_passed=0,
            gates_total=0,
        ),
        compliance=ComplianceScore(
            checks=[
                ComplianceCheck(
                    rule="Evaluation run completed",
                    type="deterministic",
                    passed=False,
                    evidence=failure_reason,
                )
            ]
        ),
        visual=None,
        efficiency=EfficiencyScore(
            total_gate_failures=settings.efficiency.max_gate_failures,
            unique_failure_categories=0,
            repeat_failures=0,
        ),
        coverage=CoverageScore(
            threshold=None,
            measured=None,
            source=None,
            passed=False,
        ),
        requirements=RequirementCoverageScore(
            total_requirements=0,
            satisfied_requirements=0,
            mapped_requirements=0,
            missing_requirement_ids=[],
            requirement_gap_ids=[],
        ),
        qualification=QualificationScore(
            checks=[
                QualificationCheck(
                    name="run_completed",
                    passed=False,
                    evidence=failure_reason,
                )
            ]
        ),
        scaffold_audit=None,
        gate_history=[],
    )


def _all_gates_passed(outputs: EvaluationOutputs) -> bool:
    return outputs.functional.gates_total == outputs.functional.gates_passed


def _completion_claim_consistent(
    events: list[SessionEvent], gates_passed: bool
) -> QualificationCheck:
    completion_keywords = ("complete", "completed", "done", "finished")
    completion_claimed = any(
        event.event_type == "assistant_message"
        and any(
            keyword in str(event.data.get("content", "")).lower() for keyword in completion_keywords
        )
        for event in events
    )
    if completion_claimed and not gates_passed:
        return QualificationCheck(
            name="completion_claim_integrity",
            passed=False,
            evidence="Agent claimed completion before all quality gates were passing.",
        )
    evidence = (
        "No completion claim detected."
        if not completion_claimed
        else "Completion claim matches gate state."
    )
    return QualificationCheck(
        name="completion_claim_integrity",
        passed=True,
        evidence=evidence,
    )


def _upsert_qualification_check(
    checks: list[QualificationCheck], candidate: QualificationCheck
) -> None:
    for idx, existing in enumerate(checks):
        if existing.name != candidate.name:
            continue
        checks[idx] = candidate
        return
    checks.append(candidate)


def build_qualification_score(
    *,
    outputs: EvaluationOutputs,
    terminated_early: bool,
    termination_reason: str | None,
    process_metrics: ProcessMetrics,
    events: list[SessionEvent],
) -> QualificationScore:
    """Build qualification gate checks for the run."""
    checks = [check.model_copy(deep=True) for check in outputs.qualification.checks]
    _upsert_qualification_check(
        checks,
        QualificationCheck(
            name="run_completed",
            passed=not terminated_early,
            evidence=termination_reason or "Run completed without early termination.",
        ),
    )

    required_count = process_metrics.required_verification_commands
    required_executed = process_metrics.executed_required_verification_commands
    required_commands_passed = required_count == 0 or required_executed == required_count
    _upsert_qualification_check(
        checks,
        QualificationCheck(
            name="required_verification_commands_executed",
            passed=required_commands_passed,
            evidence=f"executed={required_executed}/{required_count}",
        ),
    )

    completion_check = _completion_claim_consistent(events, _all_gates_passed(outputs))
    _upsert_qualification_check(checks, completion_check)
    return QualificationScore(checks=checks)


def build_optimization_score(metrics: ProcessMetrics) -> OptimizationScore:
    """Build optimization score model from process metrics."""
    return OptimizationScore(
        uncached_input_tokens=metrics.uncached_input_tokens,
        output_tokens=metrics.output_tokens,
        command_count=metrics.command_count,
        failed_command_count=metrics.failed_command_count,
        verification_rounds=metrics.verification_rounds,
        repeated_verification_failures=metrics.repeated_verification_failures,
    )


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def _classify_void_reasons(terminated_early: bool, termination_reason: str | None) -> list[str]:
    """Classify harness/provider issues that void a run and require repeat."""
    if not terminated_early and not termination_reason:
        return []

    reason = (termination_reason or "").lower()
    rules: list[tuple[str, tuple[str, ...]]] = [
        ("harbor_timeout", ("timeout expired",)),
        ("provider_rate_limit", ("rate limit",)),
        ("provider_stream_disconnect", ("stream disconnected before completion",)),
        ("harness_unavailable", ("harbor not installed",)),
        ("harbor_cli_failure", ("harbor exited with code",)),
        ("harbor_trial_exception", ("harbor trial exception",)),
    ]
    reasons: list[str] = []
    for code, patterns in rules:
        if _contains_any(reason, patterns):
            reasons.append(code)
    if "codex turn failed" in reason and not reasons:
        reasons.append("provider_or_harness_turn_failure")

    return list(dict.fromkeys(reasons))


def build_scorecard(context: ScorecardBuildContext) -> Scorecard:
    """Create scorecard with populated metrics and metadata."""

    request = context.request
    layout = context.layout
    scaffold_context = context.context
    artifacts = context.artifacts
    execution = context.execution
    outputs = execution.outputs

    scaffold_audit = outputs.scaffold_audit
    if scaffold_audit:
        scaffold_audit = scaffold_audit.model_copy(
            update={
                "template": scaffold_audit.template or scaffold_context.scaffold_source.template,
                "template_version": scaffold_audit.template_version
                or scaffold_context.scaffold_source.version,
                "manifest_fingerprint": scaffold_audit.manifest_fingerprint
                or scaffold_context.scaffold_source.manifest.fingerprint,
            }
        )

    qualification = build_qualification_score(
        outputs=outputs,
        terminated_early=execution.terminated_early,
        termination_reason=execution.termination_reason,
        process_metrics=execution.process_metrics,
        events=execution.events,
    )
    optimization = build_optimization_score(execution.process_metrics)
    void_reasons = _classify_void_reasons(execution.terminated_early, execution.termination_reason)
    voided = len(void_reasons) > 0
    harbor_timings = _harbor_phase_timings(execution.harbor_result.trial_dir)
    trial_total_sec = harbor_timings.get("trial_total_sec")
    harness_overhead_sec = (
        round(max(0.0, execution.duration_sec - trial_total_sec), 3)
        if trial_total_sec is not None
        else None
    )

    return Scorecard(
        run_id=layout.run_id,
        task_name=request.task.name,
        agent=request.config.agent.value,
        model=request.config.model.qualified_name,
        rules_variant=request.config.rules_variant,
        duration_sec=execution.duration_sec,
        terminated_early=execution.terminated_early,
        termination_reason=execution.termination_reason,
        voided=voided,
        void_reasons=void_reasons,
        functional=outputs.functional,
        compliance=outputs.compliance,
        visual=outputs.visual,
        efficiency=outputs.efficiency,
        coverage=outputs.coverage,
        requirements=outputs.requirements,
        qualification=qualification,
        optimization=optimization,
        metadata={
            "run": {
                "instance_name": layout.instance_name,
                "canonical_run_dir": str(layout.root_dir),
                "summary_result_json": str(layout.result_json_path),
                "summary_readme": str(layout.summary_readme_path),
                "repeat_required": voided,
                "repeat_required_reasons": void_reasons,
            },
            "scaffold": artifacts.scaffold_meta,
            "task": artifacts.task_version_meta,
            "harbor": {
                "raw_job_dir": str(execution.harbor_result.job_dir),
                "raw_trial_dir": (
                    str(execution.harbor_result.trial_dir)
                    if execution.harbor_result.trial_dir
                    else None
                ),
                "job_dir": str(execution.harbor_result.job_dir),
                "trial_dir": (
                    str(execution.harbor_result.trial_dir)
                    if execution.harbor_result.trial_dir
                    else None
                ),
                "phase_timings_sec": harbor_timings,
                "harness_overhead_sec": harness_overhead_sec,
                "artifacts": artifacts.harbor_artifacts,
            },
            "agent": {
                "artifacts": artifacts.agent_artifacts,
            },
            "verifier": {
                "scorecard": (
                    str(_verifier_scorecard_path(execution.harbor_result.trial_dir))
                    if _verifier_scorecard_path(execution.harbor_result.trial_dir)
                    else None
                ),
                "artifacts": artifacts.verifier_artifacts,
            },
            "process": {
                "uncached_input_tokens": execution.process_metrics.uncached_input_tokens,
                "output_tokens": execution.process_metrics.output_tokens,
                "command_count": execution.process_metrics.command_count,
                "failed_command_count": execution.process_metrics.failed_command_count,
                "verification_rounds": execution.process_metrics.verification_rounds,
                "repeated_verification_failures": (
                    execution.process_metrics.repeated_verification_failures
                ),
                "required_verification_commands": (
                    execution.process_metrics.required_verification_commands
                ),
                "executed_required_verification_commands": (
                    execution.process_metrics.executed_required_verification_commands
                ),
                "failed_command_categories": execution.process_metrics.failed_command_categories,
                "required_verification_first_pass": (
                    execution.process_metrics.required_verification_first_pass
                ),
                "first_pass_verification_successes": (
                    execution.process_metrics.first_pass_verification_successes
                ),
                "first_pass_verification_failures": (
                    execution.process_metrics.first_pass_verification_failures
                ),
                "missing_required_verification_commands": (
                    execution.process_metrics.missing_required_verification_commands
                ),
            },
        },
        scaffold_audit=scaffold_audit,
    )


def _prepare_workspace_phase(request: RunRequest) -> WorkspacePreparationPhaseResult:
    """Workspace prep phase: context, preflight, and Harbor bundle creation."""
    layout = initialize_run(request)
    adapter = request.config.adapter()
    adapter.validate()

    context = prepare_run_context(request)
    adapter.prepare_workspace(context.workspace)
    cleanup_stale_harbor_resources(include_containers=True, include_build_processes=False)
    ensure_scaffold_preflight(request, context)
    harbor_task_bundle = create_harbor_task_bundle(request, context, layout.run_id)

    run_env = os.environ.copy()
    run_env.update(adapter.runtime_env())
    harbor_request = HarborExecutionRequest(
        adapter=adapter,
        workspace=context.workspace,
        task_bundle_path=harbor_task_bundle,
        jobs_dir=layout.jobs_root,
        run_harbor_dir=layout.harbor_dir,
        run_id=layout.run_id,
        timeout_sec=_harbor_process_timeout(request.config.timeout_sec),
        run_env=run_env,
    )
    return WorkspacePreparationPhaseResult(
        layout=layout,
        context=context,
        harbor_request=harbor_request,
    )


def _execute_harbor_phase(
    request: RunRequest, phase: WorkspacePreparationPhaseResult
) -> ExecutionPhaseResult:
    """Harbor execution phase with verifier output loading."""
    harbor_result = execute_harbor(phase.harbor_request)
    terminated_early = harbor_result.terminated_early
    termination_reason = harbor_result.termination_reason
    process_metrics = collect_process_metrics(request.task, harbor_result.trial_dir)
    events = collect_session_events(harbor_result.trial_dir)

    verifier_outputs: EvaluationOutputs | None = None
    if not terminated_early:
        verifier_outputs, verifier_reason = _load_verifier_outputs(harbor_result.trial_dir)
        if verifier_outputs is None:
            terminated_early = True
            termination_reason = verifier_reason

    outputs = terminated_outputs(termination_reason) if terminated_early else verifier_outputs
    if outputs is None:
        outputs = terminated_outputs("Verifier outputs unavailable.")

    duration_sec = (datetime.now(UTC) - phase.layout.start_time).total_seconds()
    return ExecutionPhaseResult(
        harbor_result=harbor_result,
        terminated_early=terminated_early,
        termination_reason=termination_reason,
        process_metrics=process_metrics,
        events=events,
        outputs=outputs,
        duration_sec=duration_sec,
    )


def _persist_artifacts_phase(
    request: RunRequest,
    phase: WorkspacePreparationPhaseResult,
    execution: ExecutionPhaseResult,
) -> PersistedArtifacts:
    """Artifact persistence phase."""
    scaffold_artifacts = persist_scaffold_artifacts(phase.context, phase.layout.scaffold_dir)
    return PersistedArtifacts(
        scaffold_meta=build_scaffold_meta(phase.context, scaffold_artifacts),
        task_version_meta=build_task_version_meta(request, phase.context),
        verifier_artifacts=persist_verifier_artifacts(
            execution.harbor_result, phase.layout.verifier_dir
        ),
        agent_artifacts=persist_agent_artifacts(execution.harbor_result, phase.layout.agent_dir),
        harbor_artifacts=persist_harbor_artifacts(execution.harbor_result, phase.layout.harbor_dir),
    )


def _synthesize_scorecard_phase(
    request: RunRequest,
    phase: WorkspacePreparationPhaseResult,
    execution: ExecutionPhaseResult,
    artifacts: PersistedArtifacts,
) -> Scorecard:
    """Score synthesis phase from persisted artifacts and execution outputs."""
    scorecard = build_scorecard(
        ScorecardBuildContext(
            request=request,
            layout=phase.layout,
            context=phase.context,
            artifacts=artifacts,
            execution=execution,
        )
    )
    write_summary_readme(phase.layout, request, scorecard, execution.harbor_result)
    return scorecard


def run_task(request: RunRequest) -> EvalRun:
    """Execute a task and return evaluation results."""
    prepared = _prepare_workspace_phase(request)
    execution = _execute_harbor_phase(request, prepared)
    artifacts = _persist_artifacts_phase(request, prepared, execution)
    scorecard = _synthesize_scorecard_phase(request, prepared, execution, artifacts)

    return EvalRun(
        id=prepared.layout.run_id,
        timestamp=prepared.layout.start_time.isoformat(),
        config=EvalConfig(
            model=request.config.model.qualified_name,
            harness=request.config.agent.value,
            rules_variant=request.config.rules_variant,
            task_name=request.task.name,
            scaffold_template=prepared.context.scaffold_source.template,
            scaffold_version=prepared.context.scaffold_source.version,
        ),
        duration_sec=execution.duration_sec,
        terminated_early=execution.terminated_early,
        termination_reason=execution.termination_reason,
        scores=scorecard,
        events=execution.events,
        gate_history=execution.outputs.gate_history,
    )
