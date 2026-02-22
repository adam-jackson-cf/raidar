"""Task execution via Harbor."""

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tarfile
import threading
import time
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
from .harness.fast_mode import (
    fast_image_prefix,
    is_fast_image_reuse_enabled,
)
from .harness.rules import SYSTEM_RULES, inject_rules
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
    GateCheck,
    OptimizationScore,
    PerformanceGatesScore,
    RequirementCoverageScore,
    RunValidityScore,
    ScaffoldAudit,
    Scorecard,
    VisualScore,
)
from .schemas.task import RequirementSpec, TaskDefinition
from .scoring.compliance import run_deterministic_check

SCORING_SCHEMA_VERSION = "2.0.0"
HARBOR_TIMEOUT_BUFFER_SEC = 120
MIN_DOCKER_COMPOSE_VERSION = (2, 40, 1)
HARBOR_RATE_LIMIT_RETRY_DELAY_SEC = 20
HARBOR_RATE_LIMIT_MAX_ATTEMPTS = 2
HARNESS_STALE_CONTAINER_PATTERN = re.compile(r"^harbor-task.*-main-1$")
HARBOR_GIT_MULTIBRANCH_PATTERN = re.compile(r"^git-multibranch__.+-main-1$")
HARNESS_STALE_BUILD_PATTERN = re.compile(
    r"(?:docker compose|docker-compose compose).+docker-compose-build\.yaml build"
)
HARNESS_STALE_BUILDX_PATTERN = re.compile(
    r"docker-buildx bake .*--allow fs\.read=.*harbor-task-[^/]+/environment"
)
HARNESS_STALE_RUN_PATTERN = re.compile(r"\bharbor run --path .*harbor-task-")
DOCKER_COMPOSE_VERSION_PATTERN = re.compile(r"(?:^|[^0-9])v?(\d+)\.(\d+)\.(\d+)(?:[^0-9]|$)")
DOCKERFILE_FROM_PATTERN = re.compile(
    r"^\s*FROM(?:\s+--platform=[^\s]+)?\s+([^\s]+)",
    re.IGNORECASE | re.MULTILINE,
)
BACKTICK_COMMAND_PATTERN = re.compile(r"`([^`\n]+)`")
SHELL_COMMAND_PREFIX_PATTERN = re.compile(r"^(?:bun|npm|npx|pnpm|yarn|biome|tsc|next|vitest)\b")
COMMAND_INTENT_PATTERN = re.compile(r"\b(i will|i'll|i am going to|i'm going to|i plan to)\b")
COMMAND_FAILURE_PATTERN = re.compile(r"\b(failed|failure|error|unable|did not|non-zero)\b")
COMMAND_EXECUTION_HINTS = (
    "verified with",
    "verified the changes with",
    "verifying the changes with",
    "by running",
    "ran `",
    "running `",
    "executed `",
    "all of which passed",
    "verification steps passed",
    "passed successfully",
)
VERIFIED_WITH_PATTERN = re.compile(r"\bverif(?:y|ied|ying)\b.*\bwith\b")
INLINE_SECRET_PATTERN = re.compile(
    r"\b("
    r"OPENAI_API_KEY|ANTHROPIC_API_KEY|CLAUDE_CODE_API_KEY|CLAUDE_CODE_OAUTH_TOKEN|"
    r"GEMINI_API_KEY|GOOGLE_API_KEY|COPILOT_API_KEY|CURSOR_API_KEY|PI_API_KEY|"
    r"GOOGLE_APPLICATION_CREDENTIALS"
    r")=([^\s\"']+)"
)
JSON_SECRET_PATTERN = re.compile(
    r'"('
    r"OPENAI_API_KEY|ANTHROPIC_API_KEY|CLAUDE_CODE_API_KEY|CLAUDE_CODE_OAUTH_TOKEN|"
    r"GEMINI_API_KEY|GOOGLE_API_KEY|COPILOT_API_KEY|CURSOR_API_KEY|PI_API_KEY|"
    r"GOOGLE_APPLICATION_CREDENTIALS"
    r')"\s*:\s*"([^"]+)"'
)
KEY_LIKE_TOKEN_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
SECRET_ENV_KEYS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)
SECRET_FILE_ENV_PREFIX = "AGENTIC_EVAL_SECRET_FILE_"
PUBLIC_REGISTRY_HOSTS: set[str] = {
    "docker.io",
    "index.docker.io",
    "registry-1.docker.io",
    "ghcr.io",
    "quay.io",
    "mcr.microsoft.com",
    "public.ecr.aws",
    "gcr.io",
    "us.gcr.io",
    "eu.gcr.io",
    "asia.gcr.io",
    "registry.k8s.io",
}
REGISTRY_RATE_LIMIT_PATTERN = re.compile(
    r"(?:toomanyrequests|too many requests|pull rate limit|rate limit exceeded|429)",
    re.IGNORECASE,
)
KEYWORD_COMMAND_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bun run typecheck", ("type-check", "typecheck", "type checking", "tsc")),
    ("bun run lint", ("lint", "linting")),
    ("bun run test:coverage", ("test:coverage", "coverage")),
    ("bun run test", ("run test", "test command", "testing", "tests")),
    ("bun run build", ("build", "compil", "next build")),
)
AGENT_NPM_PACKAGES: dict[str, str] = {
    "codex-cli": "@openai/codex",
    "claude-code": "@anthropic-ai/claude-code",
    "gemini": "@google/gemini-cli",
}
PROCESS_FAILURE_MISSING_COMMAND_SNIPPETS: tuple[str, ...] = (
    "command not found",
    "not found",
    "no such file or directory",
    "enoent",
)
PROCESS_FAILURE_PERMISSION_SNIPPETS: tuple[str, ...] = (
    "permission denied",
    "operation not permitted",
    "eacces",
)
PROCESS_FAILURE_TIMEOUT_SNIPPETS: tuple[str, ...] = (
    "timed out",
    "timeout",
    "time limit exceeded",
)
PROCESS_FAILURE_RESOURCE_SNIPPETS: tuple[str, ...] = (
    "out of memory",
    "cannot allocate memory",
    "no space left on device",
    "enospc",
    "killed",
)
PROCESS_FAILURE_INVOCATION_SNIPPETS: tuple[str, ...] = (
    "exec format error",
    "bad substitution",
    "syntax error near unexpected token",
    "invalid option",
)
WORKSPACE_PRUNE_DIRS: tuple[str, ...] = (
    "node_modules",
    ".next",
    ".turbo",
    ".cache",
    "coverage",
    "dist",
    "build",
    "tmp",
)
_SUITE_BASELINE_LOCKS_GUARD = threading.Lock()
_SUITE_BASELINE_LOCKS: dict[Path, threading.Lock] = {}


class ScaffoldPreflightError(RuntimeError):
    """Fatal scaffold setup error that voids and aborts an entire suite."""


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
    task_dir: Path
    execution_dir: Path
    repeat_index: int = 1


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
class EvaluationOutputs:
    """Computed scoring outputs for a run."""

    functional: FunctionalScore
    compliance: ComplianceScore
    visual: VisualScore | None
    efficiency: EfficiencyScore
    coverage: CoverageScore
    requirements: RequirementCoverageScore
    run_validity: RunValidityScore
    performance_gates: PerformanceGatesScore
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
    exit_code: int | None = None


@dataclass(frozen=True, slots=True)
class ProcessMetrics:
    """Process metrics extracted from Harbor agent logs."""

    uncached_input_tokens: int
    output_tokens: int
    command_count: int
    failed_command_count: int
    process_failed_command_count: int
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
    run_label: str
    root_dir: Path
    workspace_dir: Path
    verifier_dir: Path
    agent_dir: Path
    harbor_dir: Path
    run_json_path: Path
    analysis_path: Path


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
    screenshot_command: tuple[str, ...] | None
    pre_screenshot_path: Path | None
    evidence_errors: tuple[str, ...]


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
    evidence_artifacts: dict[str, Any]
    workspace_prune: dict[str, Any]


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


def _run_label(repeat_index: int) -> str:
    return f"run-{repeat_index:02d}"


def _repeat_workspace_dir(request: RunRequest) -> Path:
    return request.execution_dir / "runs" / _run_label(request.repeat_index) / "workspace"


def _suite_baseline_lock(suite_baseline_dir: Path) -> threading.Lock:
    key = suite_baseline_dir.resolve()
    with _SUITE_BASELINE_LOCKS_GUARD:
        lock = _SUITE_BASELINE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SUITE_BASELINE_LOCKS[key] = lock
        return lock


def _ensure_suite_baseline_workspace(
    *,
    scaffold_dir: Path,
    suite_baseline_dir: Path,
    task_dir: Path,
    agent: str,
) -> None:
    with _suite_baseline_lock(suite_baseline_dir):
        if suite_baseline_dir.exists():
            return
        prepare_workspace(
            scaffold_dir=scaffold_dir,
            target_dir=suite_baseline_dir,
            task_dir=task_dir,
            agent=agent,
        )


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


def _resolve_homepage_screenshot_command(task: TaskDefinition, workspace: Path) -> list[str] | None:
    if task.visual and task.visual.screenshot_command:
        return list(task.visual.screenshot_command)

    package_json = workspace / "package.json"
    if not package_json.exists():
        return None
    try:
        payload = json.loads(package_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    scripts = payload.get("scripts")
    if not isinstance(scripts, dict):
        return None
    capture_script = scripts.get("capture-screenshot")
    if not isinstance(capture_script, str) or not capture_script.strip():
        return None
    return ["bun", "run", "capture-screenshot"]


def _run_homepage_capture_command(
    command: list[str], workspace: Path, output_path: Path
) -> tuple[Path | None, str | None]:
    actual_path = workspace / "actual.png"
    actual_path.unlink(missing_ok=True)

    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.screenshot,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)

    if completed.returncode != 0:
        output = (completed.stdout + "\n" + completed.stderr).strip()[:4000]
        rendered = " ".join(shlex.quote(part) for part in command)
        return None, f"`{rendered}` exited {completed.returncode}: {output}"

    if not actual_path.exists():
        rendered = " ".join(shlex.quote(part) for part in command)
        return None, f"`{rendered}` completed without producing {actual_path}"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(actual_path, output_path)
    actual_path.unlink(missing_ok=True)
    return output_path, None


def _safe_extract_tarball(archive_path: Path, target_dir: Path) -> None:
    target_root = target_dir.resolve()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            member_target = (target_root / member.name).resolve()
            if member_target != target_root and not str(member_target).startswith(
                f"{target_root}{os.sep}"
            ):
                raise RuntimeError(f"Unsafe tar member path: {member.name}")
        archive.extractall(path=target_root, filter="data")


def _hydrate_workspace_from_final_app(
    harbor_result: HarborExecutionResult, workspace: Path
) -> tuple[Path | None, str | None]:
    if not harbor_result.trial_dir:
        return None, "Harbor trial directory missing; cannot hydrate post-run workspace."
    archive_path = harbor_result.trial_dir / "agent" / "final-app.tar.gz"
    if not archive_path.exists():
        return None, f"Missing final app archive: {archive_path}"
    try:
        _safe_extract_tarball(archive_path, workspace)
    except (OSError, tarfile.TarError, RuntimeError) as exc:
        return None, f"Failed to hydrate workspace from {archive_path}: {exc}"
    return archive_path, None


def _directory_size_bytes(path: Path) -> int:
    total = 0
    for candidate in path.rglob("*"):
        if candidate.is_file():
            total += candidate.stat().st_size
    return total


def _prune_workspace_artifacts(workspace: Path) -> dict[str, Any]:
    removed: list[str] = []
    reclaimed_bytes = 0
    for dirname in WORKSPACE_PRUNE_DIRS:
        candidate = workspace / dirname
        if not candidate.exists():
            continue
        reclaimed_bytes += _directory_size_bytes(candidate)
        shutil.rmtree(candidate)
        removed.append(dirname)
    return {
        "removed": removed,
        "reclaimed_bytes": reclaimed_bytes,
    }


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

    cache_dir = request.execution_dir / ".preflight-cache"
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
        raise ScaffoldPreflightError(
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
            raise ScaffoldPreflightError(
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
    parsed = _collect_harbor_process_candidates()
    if parsed is None:
        return

    process_table, candidate_pids, orphan_harbor_run_pids = parsed
    orphan_harbor_run_set = set(orphan_harbor_run_pids)
    stale_build_pids = _stale_harbor_build_pids(
        process_table=process_table,
        candidate_pids=candidate_pids,
        orphan_harbor_run_set=orphan_harbor_run_set,
    )
    stale_pids = sorted(set(orphan_harbor_run_pids).union(stale_build_pids))
    for pid in stale_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue


def _collect_harbor_process_candidates() -> tuple[dict[int, int], list[int], list[int]] | None:
    listing = subprocess.run(
        ["ps", "-ax", "-o", "pid=,ppid=,command="],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if listing.returncode != 0:
        return None

    process_table: dict[int, int] = {}
    candidate_pids: list[int] = []
    orphan_harbor_run_pids: list[int] = []
    for line in listing.stdout.splitlines():
        parsed = _parse_process_listing_line(line)
        if parsed is None:
            continue
        pid, ppid, command = parsed
        process_table[pid] = ppid
        if _is_orphan_harbor_run_command(command=command, ppid=ppid):
            orphan_harbor_run_pids.append(pid)
        if _is_harbor_build_command(command):
            candidate_pids.append(pid)

    return process_table, candidate_pids, orphan_harbor_run_pids


def _stale_harbor_build_pids(
    *,
    process_table: dict[int, int],
    candidate_pids: list[int],
    orphan_harbor_run_set: set[int],
) -> list[int]:
    return [
        pid
        for pid in candidate_pids
        if process_table.get(pid, 0) <= 1
        or _has_ancestor_in_set(
            pid=pid,
            process_table=process_table,
            ancestor_set=orphan_harbor_run_set,
        )
    ]


def _parse_process_listing_line(line: str) -> tuple[int, int, str] | None:
    line = line.strip()
    if not line:
        return None
    parts = line.split(maxsplit=2)
    if len(parts) != 3:
        return None
    pid_text, ppid_text, command = parts
    if not pid_text.isdigit() or not ppid_text.isdigit():
        return None
    return int(pid_text), int(ppid_text), command


def _is_harbor_build_command(command: str) -> bool:
    return bool(
        HARNESS_STALE_BUILD_PATTERN.search(command) or HARNESS_STALE_BUILDX_PATTERN.search(command)
    )


def _is_orphan_harbor_run_command(*, command: str, ppid: int) -> bool:
    return ppid <= 1 and bool(HARNESS_STALE_RUN_PATTERN.search(command))


def _has_ancestor_in_set(
    *,
    pid: int,
    process_table: dict[int, int],
    ancestor_set: set[int],
) -> bool:
    current = process_table.get(pid, 0)
    seen: set[int] = set()
    while current > 1 and current not in seen:
        if current in ancestor_set:
            return True
        seen.add(current)
        current = process_table.get(current, 0)
    return current in ancestor_set


def _build_harbor_run_env(adapter: Any) -> dict[str, str]:
    run_env = os.environ.copy()
    run_env.update(adapter.runtime_env())
    _inject_secret_file_env(run_env)
    # Workaround for docker compose v2.39.x bake hang in non-interactive runs.
    run_env["COMPOSE_BAKE"] = "false"
    return run_env


def _redact_sensitive_text(value: str) -> str:
    redacted = INLINE_SECRET_PATTERN.sub(r"\1=[REDACTED]", value)
    redacted = JSON_SECRET_PATTERN.sub(r'"\1":"[REDACTED]"', redacted)
    return KEY_LIKE_TOKEN_PATTERN.sub("[REDACTED]", redacted)


def _inject_secret_file_env(run_env: dict[str, str]) -> None:
    for key in SECRET_ENV_KEYS:
        secret_value = run_env.pop(key, "")
        if not secret_value:
            continue
        run_env[f"{SECRET_FILE_ENV_PREFIX}{key}"] = str(
            _write_harbor_secret_file(secret_name=key, secret_value=secret_value)
        )


def _write_harbor_secret_file(*, secret_name: str, secret_value: str) -> Path:
    secret_dir = Path.home() / ".agentic-eval" / "secrets"
    secret_dir.mkdir(parents=True, exist_ok=True)
    secret_path = secret_dir / f"{secret_name.lower()}-{uuid.uuid4().hex}"
    secret_path.write_text(secret_value, encoding="utf-8")
    secret_path.chmod(0o600)
    return secret_path


def _parse_docker_compose_version(raw: str) -> tuple[int, int, int] | None:
    match = DOCKER_COMPOSE_VERSION_PATTERN.search(raw.strip())
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _read_docker_compose_version(run_env: dict[str, str]) -> tuple[int, int, int] | None:
    for cmd in (["docker", "compose", "version", "--short"], ["docker", "compose", "version"]):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                env=run_env,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            continue
        version = _parse_docker_compose_version(result.stdout or "")
        if version:
            return version
    return None


def _format_version(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def _docker_compose_preflight_reason(run_env: dict[str, str]) -> str | None:
    version = _read_docker_compose_version(run_env)
    if version is None:
        return None
    if version < MIN_DOCKER_COMPOSE_VERSION:
        required = _format_version(MIN_DOCKER_COMPOSE_VERSION)
        detected = _format_version(version)
        return (
            f"Unsupported docker compose version {detected}. Require >= {required} for Harbor runs."
        )
    return None


def _dockerfile_from_images(dockerfile_content: str) -> list[str]:
    return [match.group(1) for match in DOCKERFILE_FROM_PATTERN.finditer(dockerfile_content)]


def _image_registry_host(image: str) -> str | None:
    first_segment = image.split("/", 1)[0].strip().lower()
    if not first_segment or first_segment == "scratch":
        return None
    if "." in first_segment or ":" in first_segment or first_segment == "localhost":
        return first_segment
    return None


def _validate_public_base_images(dockerfile_content: str) -> None:
    for image in _dockerfile_from_images(dockerfile_content):
        if image.startswith("$"):
            raise ValueError(
                f"Dockerfile FROM image must be explicit, found unresolved variable: {image}."
            )
        host = _image_registry_host(image)
        if host and host not in PUBLIC_REGISTRY_HOSTS:
            raise ValueError(
                f"Dockerfile uses private or unsupported registry host '{host}' in FROM '{image}'. "
                "Only public registries are allowed."
            )


def _is_registry_rate_limited(run_harbor_dir: Path) -> bool:
    for name in ("harbor-stdout.log", "harbor-stderr.log"):
        log_path = run_harbor_dir / name
        if not log_path.exists():
            continue
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        if REGISTRY_RATE_LIMIT_PATTERN.search(log_text):
            return True
    return False


def prepare_workspace(
    *,
    scaffold_dir: Path,
    target_dir: Path,
    task_dir: Path,
    agent: str,
) -> tuple[Path, Path | None]:
    """Prepare workspace by copying scaffold and injecting rules.

    Args:
        scaffold_dir: Path to resolved scaffold template/version
        target_dir: Path to create workspace
        task_dir: Path to task directory (contains rules/)
        agent: Agent name for rule file selection
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
        injected_rules = inject_rules(rules_dir, target_dir, agent)

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


def _fast_task_docker_image(request: RunRequest, context: ScaffoldContext) -> str | None:
    if not is_fast_image_reuse_enabled():
        return None

    task_path = request.task_dir / "task.yaml"
    task_yaml_hash = _hash_bytes(task_path.read_bytes()) if task_path.exists() else "missing"
    payload = {
        "fast_mode_version": "1",
        "task_name": request.task.name,
        "task_version": request.task.version,
        "task_yaml_hash": task_yaml_hash,
        "scaffold_fingerprint": context.scaffold_source.manifest.fingerprint,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    image_tag = f"{_slug_fragment(request.task.name)}-{digest}"
    return f"{fast_image_prefix()}:{image_tag}"


def _task_environment_toml(image_name: str | None) -> str:
    lines = ["build_timeout_sec = 1800.0"]
    if image_name:
        lines.append(f'docker_image = "{image_name}"')
    lines.extend(
        [
            "cpus = 2",
            "memory_mb = 4096",
            "storage_mb = 10240",
            "allow_internet = true",
        ]
    )
    return "\n".join(lines)


def _agent_npm_package(agent: str) -> str | None:
    return AGENT_NPM_PACKAGES.get(agent)


def _docker_image_exists(image_name: str, run_env: dict[str, str]) -> bool:
    try:
        probe = subprocess.run(
            ["docker", "image", "inspect", image_name],
            capture_output=True,
            text=True,
            timeout=30,
            env=run_env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Docker CLI not found.") from exc
    return probe.returncode == 0


def _ensure_fast_task_image(
    *,
    task_bundle_path: Path,
    image_name: str,
    run_env: dict[str, str],
    log_dir: Path,
) -> None:
    if _docker_image_exists(image_name, run_env):
        return

    context_dir = task_bundle_path / "environment"
    dockerfile = context_dir / "Dockerfile"
    if not dockerfile.exists():
        raise FileNotFoundError(f"Fast image build failed: missing Dockerfile {dockerfile}")

    build_cmd = [
        "docker",
        "build",
        "--tag",
        image_name,
        "--file",
        str(dockerfile),
        str(context_dir),
    ]
    try:
        build = subprocess.run(
            build_cmd,
            capture_output=True,
            text=True,
            timeout=1800,
            env=run_env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Docker CLI not found.") from exc

    log_dir.mkdir(parents=True, exist_ok=True)
    build_log = log_dir / "fast-image-build.log"
    build_log.write_text((build.stdout or "") + "\n" + (build.stderr or ""))

    if build.returncode != 0:
        output = ((build.stdout or "") + "\n" + (build.stderr or "")).strip()[:8000]
        rendered = " ".join(shlex.quote(part) for part in build_cmd)
        raise RuntimeError(
            f"Fast image build failed: `{rendered}` exited {build.returncode}\n{output}"
        )


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
    request: RunRequest, context: ScaffoldContext, app_dir: Path
) -> None:
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
    if not request.task.visual:
        return
    reference_path = Path(request.task.visual.reference_image)
    if reference_path.is_absolute():
        return
    source_reference = (request.task_dir / reference_path).resolve()
    if not source_reference.exists():
        return
    target_reference = app_dir / reference_path
    target_reference.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_reference, target_reference)


def _load_task_prompt(task: TaskDefinition, task_dir: Path) -> str:
    prompt_paths = [task.prompt.entry, *task.prompt.includes]
    prompt_chunks: list[str] = []
    for rel_path in prompt_paths:
        prompt_path = (task_dir / rel_path).resolve()
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt artifact not found: {prompt_path}")
        prompt_chunks.append(prompt_path.read_text(encoding="utf-8").strip())
    return "\n\n".join(chunk for chunk in prompt_chunks if chunk)


def _bundle_instruction_text(prompt: str) -> str:
    return prompt.strip() + "\n\nYou are working in `/app`.\nFollow rules in `/app/AGENTS.md`.\n"


def _render_task_toml(request: RunRequest, task_image: str | None) -> str:
    return f"""version = "1.0"

[metadata]
name = "{request.task.name}"
source = "scaffold-spec"

[verifier]
timeout_sec = 300.0

[agent]
timeout_sec = {float(request.config.timeout_sec)}

[environment]
{_task_environment_toml(task_image)}
"""


def _render_environment_dockerfile(request: RunRequest) -> str:
    dockerfile = """FROM oven/bun:1
WORKDIR /app
"""
    cli_package = _agent_npm_package(request.config.agent.value)
    if cli_package:
        dockerfile += """RUN apt-get update && apt-get install -y --no-install-recommends \\
  npm \\
  && rm -rf /var/lib/apt/lists/*
"""
        dockerfile += f"RUN npm install -g {cli_package}\n"
    dockerfile += """COPY app/package.json app/bun.lock /app/
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
    return dockerfile


def _write_verifier_artifacts(
    request: RunRequest, context: ScaffoldContext, tests_dir: Path
) -> None:
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


def create_harbor_task_bundle(
    request: RunRequest,
    context: ScaffoldContext,
    bundle_root: Path,
) -> Path:
    """Build a Harbor-compatible task directory from the scaffold workspace."""
    bundle_dir, environment_dir, app_dir, tests_dir = _initialize_harbor_bundle_paths(bundle_root)
    _copy_workspace_into_bundle(request, context, app_dir)
    prompt_text = _load_task_prompt(request.task, request.task_dir)
    (bundle_dir / "instruction.md").write_text(_bundle_instruction_text(prompt_text))

    task_image = _fast_task_docker_image(request, context)
    (bundle_dir / "task.toml").write_text(_render_task_toml(request, task_image))

    dockerfile = _render_environment_dockerfile(request)
    _validate_public_base_images(dockerfile)
    (environment_dir / "Dockerfile").write_text(dockerfile)
    _write_verifier_artifacts(request, context, tests_dir)
    return bundle_dir


def initialize_run(request: RunRequest) -> RunLayout:
    """Create run ids and canonical output directories."""
    run_id = str(uuid.uuid4())[:8]
    start_time = datetime.now(UTC)
    runs_root = request.execution_dir / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    run_label = _run_label(request.repeat_index)
    root_dir = runs_root / run_label
    if root_dir.exists():
        shutil.rmtree(root_dir)
    workspace_dir = root_dir / "workspace"
    verifier_dir = root_dir / "verifier"
    agent_dir = root_dir / "agent"
    harbor_dir = root_dir / "harbor"
    for path in (workspace_dir, verifier_dir, agent_dir, harbor_dir):
        path.mkdir(parents=True, exist_ok=True)
    return RunLayout(
        run_id=run_id,
        start_time=start_time,
        run_label=run_label,
        root_dir=root_dir,
        workspace_dir=workspace_dir,
        verifier_dir=verifier_dir,
        agent_dir=agent_dir,
        harbor_dir=harbor_dir,
        run_json_path=root_dir / "run.json",
        analysis_path=root_dir / "summary.md",
    )


def prepare_run_context(request: RunRequest) -> ScaffoldContext:
    """Resolve scaffold source, workspace, and manifest metadata."""
    from .scaffold import record_scaffold_metadata, resolve_scaffold_source

    scaffold_source = resolve_scaffold_source(
        request.task_dir,
        request.task.scaffold.root,
        task_name=request.task.name,
        task_version=request.task.version,
    )

    suite_baseline_dir = request.execution_dir / "workspace" / "baseline"
    _ensure_suite_baseline_workspace(
        scaffold_dir=scaffold_source.path,
        suite_baseline_dir=suite_baseline_dir,
        task_dir=request.task_dir,
        agent=request.config.agent.value,
    )

    workspace_dir = _repeat_workspace_dir(request)
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    shutil.copytree(
        suite_baseline_dir,
        workspace_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("node_modules", ".next", "jobs"),
    )

    injected_rules: Path | None = None
    injected_rule_name = SYSTEM_RULES.get(request.config.agent.value)
    if injected_rule_name:
        candidate = workspace_dir / injected_rule_name
        if candidate.exists():
            injected_rules = candidate

    workspace = workspace_dir
    manifest_path = workspace / "scaffold.manifest.json"
    if not manifest_path.exists():
        manifest = generate_manifest(workspace)
        save_manifest(manifest, manifest_path)

    baseline_manifest_path = workspace / ".baseline-scaffold.json"
    shutil.copy2(suite_baseline_dir / "scaffold.manifest.json", baseline_manifest_path)

    metadata_path = record_scaffold_metadata(
        workspace,
        scaffold_source,
        manifest_path,
        baseline_manifest_path,
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

    execution_error: str | None = None
    for attempt in range(1, HARBOR_RATE_LIMIT_MAX_ATTEMPTS + 1):
        execution_error = _run_harbor_process(
            harbor_cmd=harbor_cmd,
            workspace=request.workspace,
            timeout_sec=request.timeout_sec,
            run_env=request.run_env,
            run_harbor_dir=request.run_harbor_dir,
            job_dir=job_dir,
        )
        if execution_error is None:
            break
        should_retry = (
            attempt < HARBOR_RATE_LIMIT_MAX_ATTEMPTS
            and execution_error.startswith("Harbor exited with code")
            and _is_registry_rate_limited(request.run_harbor_dir)
        )
        if not should_retry:
            return _terminated_harbor_result(
                job_dir=job_dir,
                reason=execution_error,
                trial_dir=None,
            )
        cleanup_stale_harbor_resources()
        time.sleep(HARBOR_RATE_LIMIT_RETRY_DELAY_SEC)

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

    preflight_reason = _docker_compose_preflight_reason(run_env)
    if preflight_reason:
        stdout_path.write_text("")
        stderr_path.write_text(preflight_reason + "\n")
        return preflight_reason

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

    stdout_path.write_text(_redact_sensitive_text(stdout or ""))
    stderr_path.write_text(_redact_sensitive_text(stderr or ""))

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
    return f"Harbor trial exception: {_redact_sensitive_text(message)}"


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
    return _redact_sensitive_text(message) if message else None


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
            run_validity=RunValidityScore.model_validate(payload.get("run_validity")),
            performance_gates=PerformanceGatesScore.model_validate(
                payload.get("performance_gates")
            ),
            scaffold_audit=scaffold_audit,
            gate_history=gate_history,
        )
    except (ValidationError, ValueError) as exc:
        return None, f"Invalid verifier scorecard content: {exc}"

    return outputs, None


def build_scaffold_meta(request: RunRequest, context: ScaffoldContext) -> dict:
    """Build scaffold metadata for the scorecard."""
    suite_baseline_dir = request.execution_dir / "workspace" / "baseline"
    return {
        "task": context.scaffold_source.task_name,
        "task_version": context.scaffold_source.task_version,
        "root": str(context.scaffold_source.path),
        "suite_baseline_dir": str(suite_baseline_dir),
        "run_workspace_dir": str(context.workspace),
        "fingerprint": context.scaffold_source.manifest.fingerprint,
        "baseline_manifest": context.baseline_manifest_path.name,
        "workspace_manifest": context.manifest_path.name,
        "metadata_file": context.metadata_path.name,
        "rules_file": context.injected_rules.name if context.injected_rules else None,
        "artifacts": {
            "baseline_manifest": str(context.baseline_manifest_path),
            "workspace_manifest": str(context.manifest_path),
            "metadata": str(context.metadata_path),
            **({"rules": str(context.injected_rules)} if context.injected_rules else {}),
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
        "task_name": request.task.name,
        "task_version": request.task.version,
        "scaffold_root": request.task.scaffold.root,
        "scaffold_fingerprint": context.scaffold_source.manifest.fingerprint,
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
        "run-validity.json",
        "performance-gates.json",
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
    for filename in (
        "trajectory.json",
        "codex.txt",
        "claude-code.txt",
        "gemini-cli.txt",
        "gemini-cli.trajectory.json",
        "install.sh",
        "final-app.tar.gz",
    ):
        src = source / filename
        if src.exists():
            copied[filename] = str(shutil.copy2(src, agent_dir / filename))
    final_app = agent_dir / "final-app.tar.gz"
    if final_app.exists():
        copied["project.final.tar.gz"] = str(
            shutil.copy2(final_app, agent_dir / "project.final.tar.gz")
        )

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
    """Record Harbor artifact pointers for run review."""
    copied: dict[str, str] = {}
    for name in ("command.txt", "harbor-stdout.log", "harbor-stderr.log"):
        candidate = harbor_dir / name
        if candidate.exists():
            copied[name] = str(candidate)
    copied["raw_job_dir"] = str(harbor_result.job_dir)
    if harbor_result.trial_dir:
        copied["raw_trial_dir"] = str(harbor_result.trial_dir)
    return copied


def write_run_analysis(
    layout: RunLayout,
    request: RunRequest,
    scorecard: Scorecard,
    harbor_result: HarborExecutionResult,
) -> None:
    """Write a human-readable run summary with canonical/raw pointers."""
    evidence_meta = scorecard.metadata.get("evidence", {})
    workspace_meta = scorecard.metadata.get("workspace", {})
    prune_meta = workspace_meta.get("prune", {}) if isinstance(workspace_meta, dict) else {}
    lines = [
        "# Run Summary",
        "",
        f"- run_id: `{layout.run_id}`",
        f"- started_at_utc: `{layout.start_time.isoformat()}`",
        f"- task: `{request.task.name}`",
        f"- agent: `{request.config.agent.value}`",
        f"- model: `{request.config.model.qualified_name}`",
        f"- run_label: `{layout.run_label}`",
        f"- run_valid: `{scorecard.run_validity.passed}`",
        f"- performance_gates_passed: `{scorecard.performance_gates.passed}`",
        f"- voided: `{scorecard.voided}`",
        f"- void_reasons: `{scorecard.void_reasons}`",
        f"- quality_score: `{scorecard.quality_score:.6f}`",
        f"- composite_score: `{scorecard.composite_score:.6f}`",
        "",
        "## Pointers",
        f"- canonical_run_dir: `{layout.root_dir}`",
        f"- workspace_dir: `{layout.workspace_dir}`",
        f"- raw_harbor_job_dir: `{harbor_result.job_dir}`",
        f"- raw_harbor_trial_dir: `{harbor_result.trial_dir}`",
        f"- run_json_path: `{layout.run_json_path}`",
        "",
        "## Key Artifacts",
        f"- verifier_scorecard: `{layout.verifier_dir / 'scorecard.json'}`",
        f"- agent_trajectory: `{layout.agent_dir / 'trajectory.json'}`",
    ]
    event_stream = _agent_event_stream_pointer(layout.agent_dir, request.config.agent.value)
    lines.append(f"- agent_event_stream: `{event_stream}`")
    lines.append(f"- homepage_pre_screenshot: `{evidence_meta.get('homepage_pre')}`")
    lines.append(f"- homepage_post_screenshot: `{evidence_meta.get('homepage_post')}`")
    lines.append(f"- final_workspace_archive: `{evidence_meta.get('final_workspace_archive')}`")
    lines.append(f"- evidence_errors: `{evidence_meta.get('errors')}`")
    lines.append(f"- workspace_pruned_dirs: `{prune_meta.get('removed')}`")
    lines.append(f"- workspace_pruned_bytes: `{prune_meta.get('reclaimed_bytes')}`")
    layout.analysis_path.write_text("\n".join(lines) + "\n")


def _agent_event_stream_pointer(agent_dir: Path, harness: str) -> Path:
    if harness == "codex-cli":
        return agent_dir / "codex.txt"
    if harness == "claude-code":
        return agent_dir / "commands"
    if harness == "gemini":
        return agent_dir / "commands"
    if harness == "cursor":
        return agent_dir / "commands"
    if harness == "copilot":
        return agent_dir / "commands"
    if harness == "pi":
        return agent_dir / "commands"
    raise ValueError(f"Unsupported harness for artifact summary: {harness}")


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


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _usage_tuple_from_payload(
    usage: dict | None,
    *,
    input_key: str = "input_tokens",
    cached_keys: tuple[str, ...] = ("cached_input_tokens",),
    output_key: str = "output_tokens",
) -> tuple[int, int, int] | None:
    if not isinstance(usage, dict):
        return None
    input_tokens = _as_int(usage.get(input_key))
    output_tokens = _as_int(usage.get(output_key))
    if input_tokens is None or output_tokens is None:
        return None
    cached_input_tokens = 0
    for key in cached_keys:
        candidate = _as_int(usage.get(key))
        if candidate is not None:
            cached_input_tokens = candidate
            break
    return input_tokens, cached_input_tokens, output_tokens


def _extract_codex_usage(entry: dict) -> tuple[int, int, int] | None:
    if entry.get("type") != "turn.completed":
        return None
    return _usage_tuple_from_payload(entry.get("usage"))


def _normalize_command(command: str) -> str:
    commands = _normalized_shell_subcommands(command)
    if commands:
        return commands[0]
    return command.strip()


def _normalized_shell_subcommands(command: str) -> list[str]:
    command_text = _unwrap_shell_wrapper(command)
    if not command_text:
        return []
    try:
        tokens = shlex.split(command_text)
    except ValueError:
        return [_normalize_verification_alias(command_text)]
    if not tokens:
        return []

    subcommands: list[str] = []
    current: list[str] = []
    for token in tokens:
        if token in {"&&", "||", ";"}:
            if current:
                subcommands.append(_normalize_verification_alias(shlex.join(current).strip()))
                current = []
            continue
        current.append(token)
    if current:
        subcommands.append(_normalize_verification_alias(shlex.join(current).strip()))
    return [entry for entry in subcommands if entry]


def _unwrap_shell_wrapper(command: str) -> str:
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


def _normalize_verification_alias(command: str) -> str:
    lowered = command.lower().strip()
    if lowered in {"bun run typecheck", "npm run typecheck", "pnpm typecheck", "yarn typecheck"}:
        return "bun run typecheck"
    if lowered in {"bun run lint", "npm run lint", "pnpm lint", "yarn lint"}:
        return "bun run lint"
    if lowered in {"bun run build", "npm run build", "pnpm build", "yarn build"}:
        return "bun run build"
    if "tsc --noemit" in lowered:
        return "bun run typecheck"
    if "ultracite lint" in lowered or lowered.startswith("eslint "):
        return "bun run lint"
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


def _usage_from_codex_log(trial_dir: Path) -> tuple[int, int, int] | None:
    entries = _read_jsonl_dicts(trial_dir / "agent" / "codex.txt")
    usages = [_extract_codex_usage(entry) for entry in entries]
    return next((usage for usage in reversed(usages) if usage), None)


def _usage_from_trial_result(trial_dir: Path) -> tuple[int, int, int] | None:
    payload = _load_json_dict(trial_dir / "result.json")
    agent_result = payload.get("agent_result")
    if not isinstance(agent_result, dict):
        return None
    input_tokens = _as_int(agent_result.get("n_input_tokens"))
    output_tokens = _as_int(agent_result.get("n_output_tokens"))
    cached_tokens = _as_int(agent_result.get("n_cache_tokens")) or 0
    if input_tokens is None or output_tokens is None:
        return None
    return input_tokens, cached_tokens, output_tokens


def _record_claude_usage(
    entry: dict,
    *,
    message_usage_by_id: dict[str, tuple[int, int, int]],
    result_usage: tuple[int, int, int] | None,
) -> tuple[int, int, int] | None:
    if entry.get("type") == "result":
        usage_tuple = _usage_tuple_from_payload(
            entry.get("usage"),
            cached_keys=("cached_input_tokens", "cache_read_input_tokens"),
        )
        if usage_tuple:
            result_usage = usage_tuple
    message = entry.get("message")
    if not isinstance(message, dict):
        return result_usage
    message_id = str(message.get("id", "")).strip()
    usage_tuple = _usage_tuple_from_payload(
        message.get("usage"),
        cached_keys=("cached_input_tokens", "cache_read_input_tokens"),
    )
    if message_id and usage_tuple:
        message_usage_by_id[message_id] = usage_tuple
    return result_usage


def _usage_from_claude_log(trial_dir: Path) -> tuple[int, int, int] | None:
    result_usage: tuple[int, int, int] | None = None
    message_usage_by_id: dict[str, tuple[int, int, int]] = {}
    agent_dir = trial_dir / "agent"
    candidate_paths = sorted(agent_dir.glob("command-*/stdout.txt"))
    candidate_paths.append(agent_dir / "claude-code.txt")
    for path in candidate_paths:
        for entry in _read_jsonl_dicts(path):
            result_usage = _record_claude_usage(
                entry,
                message_usage_by_id=message_usage_by_id,
                result_usage=result_usage,
            )

    if result_usage:
        return result_usage
    if not message_usage_by_id:
        return None
    input_tokens = sum(usage[0] for usage in message_usage_by_id.values())
    cached_tokens = sum(usage[1] for usage in message_usage_by_id.values())
    output_tokens = sum(usage[2] for usage in message_usage_by_id.values())
    return input_tokens, cached_tokens, output_tokens


def _usage_from_gemini_trajectory(trial_dir: Path) -> tuple[int, int, int] | None:
    payload = _load_json_dict(trial_dir / "agent" / "gemini-cli.trajectory.json")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return None
    input_tokens = 0
    cached_tokens = 0
    output_tokens = 0
    found = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        token_block = message.get("tokens")
        if not isinstance(token_block, dict):
            continue
        msg_input = _as_int(token_block.get("input"))
        msg_cached = _as_int(token_block.get("cached")) or 0
        msg_output = _as_int(token_block.get("output"))
        if msg_input is None or msg_output is None:
            continue
        input_tokens += msg_input
        cached_tokens += msg_cached
        output_tokens += msg_output
        found = True
    if not found:
        return None
    return input_tokens, cached_tokens, output_tokens


def _usage_tuple_for_harness(trial_dir: Path, harness: str) -> tuple[int, int, int] | None:
    trial_usage = _usage_from_trial_result(trial_dir)
    if trial_usage:
        return trial_usage
    if harness == "codex-cli":
        return _usage_from_codex_log(trial_dir)
    if harness == "claude-code":
        return _usage_from_claude_log(trial_dir)
    if harness == "gemini":
        return _usage_from_gemini_trajectory(trial_dir)
    if harness in {"cursor", "copilot", "pi"}:
        return None
    raise ValueError(f"Unsupported harness for usage extraction: {harness}")


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
        failed = _command_failed(item)
        exit_code = _as_int(item.get("exit_code"))
        output = _command_output(item)
        commands = _normalized_shell_subcommands(str(item.get("command", "")))
        for command in commands:
            if not _looks_like_shell_command(command):
                continue
            records.append(
                CommandRecord(
                    command=command,
                    failed=failed,
                    output=output,
                    exit_code=exit_code,
                )
            )
    return records


def _command_records_for_harness(trial_dir: Path, harness: str) -> list[CommandRecord]:
    if harness == "codex-cli":
        return _command_records(_read_jsonl_dicts(trial_dir / "agent" / "codex.txt"))
    if harness == "claude-code":
        return _command_records_from_claude_stdout(trial_dir)
    if harness == "gemini":
        stdout_records = _command_records_from_agent_stdout(
            trial_dir,
            additional_stdout_files=("gemini-cli.txt",),
        )
        if stdout_records:
            return stdout_records
        return _command_records_from_gemini_trajectory(trial_dir)
    if harness == "cursor":
        return _command_records_from_agent_stdout(trial_dir)
    if harness == "copilot":
        return _command_records_from_agent_stdout(trial_dir)
    if harness == "pi":
        return _command_records_from_agent_stdout(trial_dir)
    raise ValueError(f"Unsupported harness for command extraction: {harness}")


def _harness_emits_structured_session_events(harness: str) -> bool:
    if harness == "codex-cli":
        return True
    if harness in {"claude-code", "gemini", "cursor", "copilot", "pi"}:
        return False
    raise ValueError(f"Unsupported harness for session event extraction: {harness}")


def _command_records_from_agent_stdout(
    trial_dir: Path,
    *,
    additional_stdout_files: tuple[str, ...] = (),
) -> list[CommandRecord]:
    agent_dir = trial_dir / "agent"
    if not agent_dir.exists():
        return []
    records: list[CommandRecord] = []
    stdout_paths: list[Path] = sorted(agent_dir.glob("command-*/stdout.txt"))
    stdout_paths.extend(agent_dir / name for name in additional_stdout_files)
    for stdout_path in stdout_paths:
        if not stdout_path.exists():
            continue
        records.extend(_command_records_from_stdout(stdout_path))
    return records


def _command_records_from_claude_stdout(trial_dir: Path) -> list[CommandRecord]:
    agent_dir = trial_dir / "agent"
    if not agent_dir.exists():
        return []
    records: list[CommandRecord] = []
    stdout_paths: list[Path] = sorted(agent_dir.glob("command-*/stdout.txt"))
    stdout_paths.append(agent_dir / "claude-code.txt")
    for stdout_path in stdout_paths:
        if not stdout_path.exists():
            continue
        records.extend(_command_records_from_claude_stdout_file(stdout_path))
    return records


def _command_records_from_claude_stdout_file(stdout_path: Path) -> list[CommandRecord]:
    try:
        lines = stdout_path.read_text(errors="ignore").splitlines()
    except OSError:
        return []

    records: list[CommandRecord] = []
    record_idx_by_tool_use_id: dict[str, int] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        payload = _line_as_json_dict(stripped)
        if payload is None:
            records.extend(_command_records_from_line(stripped))
            continue
        _append_claude_tool_use_records(
            payload=payload,
            output=stripped,
            records=records,
            record_idx_by_tool_use_id=record_idx_by_tool_use_id,
        )
        _mark_claude_failed_tool_records(
            payload=payload,
            records=records,
            record_idx_by_tool_use_id=record_idx_by_tool_use_id,
        )
    return records


def _append_claude_tool_use_records(
    *,
    payload: dict,
    output: str,
    records: list[CommandRecord],
    record_idx_by_tool_use_id: dict[str, int],
) -> None:
    for tool_use_id, command in _claude_bash_tool_use_commands(payload):
        matched_indexes: list[int] = []
        for normalized in _normalized_shell_subcommands(command):
            if not _looks_like_shell_command(normalized):
                continue
            matched_indexes.append(len(records))
            records.append(
                CommandRecord(
                    command=normalized,
                    failed=False,
                    output=output,
                )
            )
        if matched_indexes:
            record_idx_by_tool_use_id[tool_use_id] = matched_indexes[0]


def _mark_claude_failed_tool_records(
    *,
    payload: dict,
    records: list[CommandRecord],
    record_idx_by_tool_use_id: dict[str, int],
) -> None:
    for tool_use_id in _claude_failed_tool_result_ids(payload):
        idx = record_idx_by_tool_use_id.get(tool_use_id)
        if idx is None:
            continue
        original = records[idx]
        records[idx] = CommandRecord(
            command=original.command,
            failed=True,
            output=original.output,
        )


def _line_as_json_dict(line: str) -> dict | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _claude_bash_tool_use_commands(payload: dict) -> list[tuple[str, str]]:
    message = payload.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    commands: list[tuple[str, str]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") != "tool_use" or part.get("name") != "Bash":
            continue
        tool_use_id = str(part.get("id", "")).strip()
        tool_input = part.get("input")
        if not isinstance(tool_input, dict):
            continue
        command = str(tool_input.get("command", "")).strip()
        if not tool_use_id or not command:
            continue
        commands.append((tool_use_id, command))
    return commands


def _claude_failed_tool_result_ids(payload: dict) -> list[str]:
    message = payload.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    failed_tool_ids: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") != "tool_result":
            continue
        if not bool(part.get("is_error", False)):
            continue
        tool_use_id = str(part.get("tool_use_id", "")).strip()
        if tool_use_id:
            failed_tool_ids.append(tool_use_id)
    return failed_tool_ids


def _command_records_from_stdout(stdout_path: Path) -> list[CommandRecord]:
    try:
        lines = stdout_path.read_text(errors="ignore").splitlines()
    except OSError:
        return []
    records: list[CommandRecord] = []
    for line in lines:
        records.extend(_command_records_from_line(line))
    return records


def _command_records_from_line(line: str) -> list[CommandRecord]:
    stripped = line.strip()
    if not stripped:
        return []
    if stripped.startswith("$ "):
        return _prompt_command_record(stripped[2:], output=stripped)
    if _line_is_command_intent(stripped):
        return []
    if not _line_reports_command_execution(stripped):
        return []
    quoted_records = _quoted_command_records(stripped)
    if quoted_records:
        return quoted_records
    return _keyword_command_records(stripped)


def _prompt_command_record(command_text: str, *, output: str) -> list[CommandRecord]:
    commands = _normalized_shell_subcommands(command_text)
    return [
        CommandRecord(command=command, failed=False, output=output)
        for command in commands
        if _looks_like_shell_command(command)
    ]


def _quoted_command_records(line: str) -> list[CommandRecord]:
    commands: list[str] = []
    for match in BACKTICK_COMMAND_PATTERN.findall(line):
        commands.extend(_normalized_shell_subcommands(match))
    commands = [command for command in commands if _looks_like_shell_command(command)]
    if not commands:
        return []
    failed = _line_reports_command_failure(line)
    return [CommandRecord(command=command, failed=failed, output=line) for command in commands]


def _command_records_from_gemini_trajectory(trial_dir: Path) -> list[CommandRecord]:
    payload = _load_json_dict(trial_dir / "agent" / "gemini-cli.trajectory.json")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return []
    records: list[CommandRecord] = []
    for message in messages:
        records.extend(_command_records_from_gemini_message(message))
    return records


def _command_records_from_gemini_message(message: dict) -> list[CommandRecord]:
    if not isinstance(message, dict):
        return []
    tool_calls = message.get("toolCalls")
    if not isinstance(tool_calls, list):
        return []
    records: list[CommandRecord] = []
    for tool_call in tool_calls:
        records.extend(_command_records_from_gemini_tool_call(tool_call))
    return records


def _command_records_from_gemini_tool_call(tool_call: dict) -> list[CommandRecord]:
    if not isinstance(tool_call, dict):
        return []
    if tool_call.get("name") != "run_shell_command":
        return []
    args = tool_call.get("args")
    if not isinstance(args, dict):
        return []
    command_text = str(args.get("command", "")).strip()
    if not command_text:
        return []
    failed = str(tool_call.get("status", "")).strip().lower() == "error"
    commands = _normalized_shell_subcommands(command_text)
    return [
        CommandRecord(
            command=command,
            failed=failed,
            output=command_text,
        )
        for command in commands
        if _looks_like_shell_command(command)
    ]


def _keyword_command_records(line: str) -> list[CommandRecord]:
    lowered = f" {line.lower()} "
    commands: list[str] = []
    for command, keywords in KEYWORD_COMMAND_PATTERNS:
        if any(keyword in lowered for keyword in keywords):
            commands.append(command)
    deduped = list(dict.fromkeys(commands))
    if not deduped:
        return []
    failed = _line_reports_command_failure(line)
    return [CommandRecord(command=command, failed=failed, output=line) for command in deduped]


def _looks_like_shell_command(command: str) -> bool:
    return bool(command and SHELL_COMMAND_PREFIX_PATTERN.match(command))


def _line_is_command_intent(line: str) -> bool:
    return bool(COMMAND_INTENT_PATTERN.search(line.lower()))


def _line_reports_command_execution(line: str) -> bool:
    lowered = line.lower()
    if any(hint in lowered for hint in COMMAND_EXECUTION_HINTS):
        return True
    return bool(VERIFIED_WITH_PATTERN.search(lowered))


def _line_reports_command_failure(line: str) -> bool:
    return bool(COMMAND_FAILURE_PATTERN.search(line.lower()))


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


def _contains_snippet(text: str, snippets: tuple[str, ...]) -> bool:
    return any(snippet in text for snippet in snippets)


def _failure_category(record: CommandRecord) -> str | None:
    combined = f"{record.command}\n{record.output}".lower()
    if record.exit_code in {126, 127} or _contains_snippet(
        combined, PROCESS_FAILURE_MISSING_COMMAND_SNIPPETS
    ):
        return "missing_command"
    if _contains_snippet(combined, PROCESS_FAILURE_PERMISSION_SNIPPETS):
        return "permission_denied"
    if _contains_snippet(combined, PROCESS_FAILURE_TIMEOUT_SNIPPETS):
        return "command_timeout"
    if _contains_snippet(combined, PROCESS_FAILURE_RESOURCE_SNIPPETS):
        return "resource_exhausted"
    if _contains_snippet(combined, PROCESS_FAILURE_INVOCATION_SNIPPETS):
        return "command_invocation_error"
    return None


def _failure_category_counts(records: list[CommandRecord]) -> dict[str, int]:
    categories: dict[str, int] = {}
    for record in records:
        if not record.failed:
            continue
        category = _failure_category(record)
        if category is None:
            continue
        categories[category] = categories.get(category, 0) + 1
    return categories


def _empty_process_metrics() -> ProcessMetrics:
    return ProcessMetrics(
        uncached_input_tokens=0,
        output_tokens=0,
        command_count=0,
        failed_command_count=0,
        process_failed_command_count=0,
        verification_rounds=0,
        repeated_verification_failures=0,
        required_verification_commands=0,
        executed_required_verification_commands=0,
    )


def _count_failed_commands(records: list[CommandRecord]) -> int:
    return sum(1 for record in records if record.failed)


def _count_process_failed_commands(failure_categories: dict[str, int]) -> int:
    return sum(failure_categories.values())


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
    *,
    harness: str,
) -> ProcessMetrics:
    """Collect optimization metrics from harness agent logs."""
    if not trial_dir:
        return _empty_process_metrics()

    usage_tuple = _usage_tuple_for_harness(trial_dir, harness)
    if usage_tuple is None:
        raise RuntimeError(
            f"Missing token usage metrics for harness `{harness}` in trial `{trial_dir}`."
        )
    input_tokens, cached_input_tokens, output_tokens = usage_tuple
    uncached_input_tokens = max(0, input_tokens - cached_input_tokens)

    records = _command_records_for_harness(trial_dir, harness)
    verification_patterns = _verification_command_strings(task)
    attempts_by_pattern, failures_by_pattern = _verification_attempts(
        records, verification_patterns
    )
    first_pass_status = _first_pass_status(records, verification_patterns)
    failure_categories = _failure_category_counts(records)
    command_count = len(records)
    failed_command_count = _count_failed_commands(records)
    process_failed_command_count = _count_process_failed_commands(failure_categories)
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
        process_failed_command_count=process_failed_command_count,
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


def collect_session_events(
    trial_dir: Path | None,
    *,
    harness: str,
) -> list[SessionEvent]:
    """Project harness logs into normalized session events."""
    if not trial_dir:
        return []
    if not _harness_emits_structured_session_events(harness):
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
    return any(re.search(pattern, source, re.MULTILINE | re.IGNORECASE) for source in test_sources)


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
    mapped_satisfied = 0

    for requirement in requirements:
        requirement_check, missing_patterns = _requirement_status(
            workspace, requirement, test_sources
        )
        if requirement_check.passed:
            satisfied += 1
        else:
            missing_ids.append(requirement.id)

        mapped_for_requirement = bool(requirement.required_test_patterns) and not missing_patterns
        mapped, mapped_satisfied = _apply_requirement_mapping_counts(
            mapped=mapped,
            mapped_satisfied=mapped_satisfied,
            mapped_for_requirement=mapped_for_requirement,
            requirement_passed=requirement_check.passed,
        )
        if not mapped_for_requirement:
            gap_ids.append(requirement.id)
            if missing_patterns:
                pattern_gaps[requirement.id] = missing_patterns

    return RequirementCoverageScore(
        total_requirements=len(requirements),
        satisfied_requirements=satisfied,
        mapped_requirements=mapped,
        mapped_satisfied_requirements=mapped_satisfied,
        missing_requirement_ids=missing_ids,
        requirement_gap_ids=gap_ids,
        requirement_pattern_gaps=pattern_gaps,
    )


def _requirement_status(
    workspace: Path,
    requirement: RequirementSpec,
    test_sources: list[str],
) -> tuple[ComplianceCheck, list[str]]:
    requirement_check = run_deterministic_check(requirement.check, workspace)
    missing_patterns = [
        pattern
        for pattern in requirement.required_test_patterns
        if not _has_test_pattern(test_sources, pattern)
    ]
    return requirement_check, missing_patterns


def _apply_requirement_mapping_counts(
    *,
    mapped: int,
    mapped_satisfied: int,
    mapped_for_requirement: bool,
    requirement_passed: bool,
) -> tuple[int, int]:
    if not mapped_for_requirement:
        return mapped, mapped_satisfied
    mapped += 1
    if requirement_passed:
        mapped_satisfied += 1
    return mapped, mapped_satisfied


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
        run_validity=RunValidityScore(
            checks=[
                GateCheck(
                    name="run_completed",
                    passed=False,
                    evidence=failure_reason,
                )
            ]
        ),
        performance_gates=PerformanceGatesScore(checks=[]),
        scaffold_audit=None,
        gate_history=[],
    )


def _all_gates_passed(outputs: EvaluationOutputs) -> bool:
    return outputs.functional.gates_total == outputs.functional.gates_passed


def _completion_claim_consistent(events: list[SessionEvent], gates_passed: bool) -> GateCheck:
    completion_keywords = ("complete", "completed", "done", "finished")
    completion_claimed = any(
        event.event_type == "assistant_message"
        and any(
            keyword in str(event.data.get("content", "")).lower() for keyword in completion_keywords
        )
        for event in events
    )
    if completion_claimed and not gates_passed:
        return GateCheck(
            name="completion_claim_integrity",
            passed=False,
            evidence="Agent claimed completion before all quality gates were passing.",
        )
    evidence = (
        "No completion claim detected."
        if not completion_claimed
        else "Completion claim matches gate state."
    )
    return GateCheck(
        name="completion_claim_integrity",
        passed=True,
        evidence=evidence,
    )


def _upsert_gate_check(checks: list[GateCheck], candidate: GateCheck) -> None:
    for idx, existing in enumerate(checks):
        if existing.name != candidate.name:
            continue
        checks[idx] = candidate
        return
    checks.append(candidate)


def build_run_validity_score(
    *,
    outputs: EvaluationOutputs,
    terminated_early: bool,
    termination_reason: str | None,
    process_metrics: ProcessMetrics,
    events: list[SessionEvent],
) -> RunValidityScore:
    """Build run-validity checks for the run."""
    checks = [check.model_copy(deep=True) for check in outputs.run_validity.checks]
    _upsert_gate_check(
        checks,
        GateCheck(
            name="run_completed",
            passed=not terminated_early,
            evidence=termination_reason or "Run completed without early termination.",
        ),
    )

    required_count = process_metrics.required_verification_commands
    required_executed = process_metrics.executed_required_verification_commands
    required_commands_passed = required_count == 0 or required_executed == required_count
    _upsert_gate_check(
        checks,
        GateCheck(
            name="required_verification_commands_executed",
            passed=required_commands_passed,
            evidence=f"executed={required_executed}/{required_count}",
        ),
    )

    completion_check = _completion_claim_consistent(events, _all_gates_passed(outputs))
    _upsert_gate_check(checks, completion_check)
    return RunValidityScore(checks=checks)


def build_performance_gates_score(*, outputs: EvaluationOutputs) -> PerformanceGatesScore:
    """Build performance-gate checks for scored task outcomes."""
    checks = [check.model_copy(deep=True) for check in outputs.performance_gates.checks]
    return PerformanceGatesScore(checks=checks)


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
        ("compose_version_unsupported", ("unsupported docker compose version",)),
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


def _normalized_scaffold_audit(
    scaffold_audit: ScaffoldAudit | None, scaffold_context: ScaffoldContext
) -> ScaffoldAudit | None:
    if not scaffold_audit:
        return None
    return scaffold_audit.model_copy(
        update={
            "template": scaffold_audit.template or scaffold_context.scaffold_source.task_name,
            "template_version": scaffold_audit.template_version
            or scaffold_context.scaffold_source.task_version,
            "manifest_fingerprint": (
                scaffold_audit.manifest_fingerprint
                or scaffold_context.scaffold_source.manifest.fingerprint
            ),
        }
    )


def _scorecard_run_metadata(
    layout: RunLayout, *, voided: bool, void_reasons: list[str]
) -> dict[str, Any]:
    return {
        "run_label": layout.run_label,
        "canonical_run_dir": str(layout.root_dir),
        "run_json_path": str(layout.run_json_path),
        "run_analysis_path": str(layout.analysis_path),
        "repeat_required": voided,
        "repeat_required_reasons": void_reasons,
    }


def _scorecard_harbor_metadata(
    execution: ExecutionPhaseResult, artifacts: PersistedArtifacts
) -> dict[str, Any]:
    harbor_timings = _harbor_phase_timings(execution.harbor_result.trial_dir)
    trial_total_sec = harbor_timings.get("trial_total_sec")
    harness_overhead_sec = (
        round(max(0.0, execution.duration_sec - trial_total_sec), 3)
        if trial_total_sec is not None
        else None
    )
    trial_dir = (
        str(execution.harbor_result.trial_dir) if execution.harbor_result.trial_dir else None
    )
    return {
        "raw_job_dir": str(execution.harbor_result.job_dir),
        "raw_trial_dir": trial_dir,
        "job_dir": str(execution.harbor_result.job_dir),
        "trial_dir": trial_dir,
        "phase_timings_sec": harbor_timings,
        "harness_overhead_sec": harness_overhead_sec,
        "artifacts": artifacts.harbor_artifacts,
    }


def _scorecard_verifier_metadata(
    execution: ExecutionPhaseResult, artifacts: PersistedArtifacts
) -> dict[str, Any]:
    verifier_scorecard_path = _verifier_scorecard_path(execution.harbor_result.trial_dir)
    return {
        "scorecard": str(verifier_scorecard_path) if verifier_scorecard_path else None,
        "artifacts": artifacts.verifier_artifacts,
    }


def _scorecard_process_metadata(process_metrics: ProcessMetrics) -> dict[str, Any]:
    return {
        "uncached_input_tokens": process_metrics.uncached_input_tokens,
        "output_tokens": process_metrics.output_tokens,
        "command_count": process_metrics.command_count,
        "failed_command_count": process_metrics.failed_command_count,
        "process_failed_command_count": process_metrics.process_failed_command_count,
        "verification_rounds": process_metrics.verification_rounds,
        "repeated_verification_failures": process_metrics.repeated_verification_failures,
        "required_verification_commands": process_metrics.required_verification_commands,
        "executed_required_verification_commands": (
            process_metrics.executed_required_verification_commands
        ),
        "failed_command_categories": process_metrics.failed_command_categories,
        "required_verification_first_pass": process_metrics.required_verification_first_pass,
        "first_pass_verification_successes": process_metrics.first_pass_verification_successes,
        "first_pass_verification_failures": process_metrics.first_pass_verification_failures,
        "missing_required_verification_commands": (
            process_metrics.missing_required_verification_commands
        ),
    }


def _scorecard_metadata(
    *,
    layout: RunLayout,
    execution: ExecutionPhaseResult,
    artifacts: PersistedArtifacts,
    voided: bool,
    void_reasons: list[str],
) -> dict[str, Any]:
    return {
        "run": _scorecard_run_metadata(layout, voided=voided, void_reasons=void_reasons),
        "scaffold": artifacts.scaffold_meta,
        "task": artifacts.task_version_meta,
        "harbor": _scorecard_harbor_metadata(execution, artifacts),
        "agent": {"artifacts": artifacts.agent_artifacts},
        "verifier": _scorecard_verifier_metadata(execution, artifacts),
        "process": _scorecard_process_metadata(execution.process_metrics),
        "evidence": artifacts.evidence_artifacts,
        "workspace": {"prune": artifacts.workspace_prune},
    }


def build_scorecard(context: ScorecardBuildContext) -> Scorecard:
    """Create scorecard with populated metrics and metadata."""

    request = context.request
    layout = context.layout
    scaffold_context = context.context
    artifacts = context.artifacts
    execution = context.execution
    outputs = execution.outputs

    scaffold_audit = _normalized_scaffold_audit(outputs.scaffold_audit, scaffold_context)

    run_validity = build_run_validity_score(
        outputs=outputs,
        terminated_early=execution.terminated_early,
        termination_reason=execution.termination_reason,
        process_metrics=execution.process_metrics,
        events=execution.events,
    )
    performance_gates = build_performance_gates_score(outputs=outputs)
    optimization = build_optimization_score(execution.process_metrics)
    void_reasons = _classify_void_reasons(execution.terminated_early, execution.termination_reason)
    voided = len(void_reasons) > 0
    metadata = _scorecard_metadata(
        layout=layout,
        execution=execution,
        artifacts=artifacts,
        voided=voided,
        void_reasons=void_reasons,
    )

    return Scorecard(
        run_id=layout.run_id,
        task_name=request.task.name,
        task_version=request.task.version,
        agent=request.config.agent.value,
        model=request.config.model.qualified_name,
        scaffold_root=request.task.scaffold.root,
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
        run_validity=run_validity,
        performance_gates=performance_gates,
        optimization=optimization,
        metadata=metadata,
        scaffold_audit=scaffold_audit,
    )


def _prepare_workspace_phase(request: RunRequest) -> WorkspacePreparationPhaseResult:
    """Workspace prep phase: context, preflight, and Harbor bundle creation."""
    layout = initialize_run(request)
    adapter = request.config.adapter()
    adapter.validate()

    context = prepare_run_context(request)
    adapter.prepare_workspace(context.workspace)
    cleanup_stale_harbor_resources(include_containers=True, include_build_processes=True)
    ensure_scaffold_preflight(request, context)
    screenshot_command = _resolve_homepage_screenshot_command(request.task, context.workspace)
    pre_screenshot_path: Path | None = None
    evidence_errors: list[str] = []
    if screenshot_command:
        pre_screenshot_path, pre_error = _run_homepage_capture_command(
            screenshot_command,
            context.workspace,
            layout.root_dir / "homepage-pre.png",
        )
        if pre_error:
            evidence_errors.append(f"homepage-pre capture failed: {pre_error}")
    harbor_task_bundle = create_harbor_task_bundle(
        request,
        context,
        bundle_root=layout.harbor_dir / "bundle",
    )

    run_env = _build_harbor_run_env(adapter)
    fast_task_image = _fast_task_docker_image(request, context)
    if fast_task_image:
        _ensure_fast_task_image(
            task_bundle_path=harbor_task_bundle,
            image_name=fast_task_image,
            run_env=run_env,
            log_dir=layout.harbor_dir,
        )

    harbor_request = HarborExecutionRequest(
        adapter=adapter,
        workspace=context.workspace,
        task_bundle_path=harbor_task_bundle,
        jobs_dir=layout.harbor_dir / "raw",
        run_harbor_dir=layout.harbor_dir,
        run_id=layout.run_id,
        timeout_sec=_harbor_process_timeout(request.config.timeout_sec),
        run_env=run_env,
    )
    return WorkspacePreparationPhaseResult(
        layout=layout,
        context=context,
        harbor_request=harbor_request,
        screenshot_command=tuple(screenshot_command) if screenshot_command else None,
        pre_screenshot_path=pre_screenshot_path,
        evidence_errors=tuple(evidence_errors),
    )


def _execute_harbor_phase(
    request: RunRequest, phase: WorkspacePreparationPhaseResult
) -> ExecutionPhaseResult:
    """Harbor execution phase with verifier output loading."""
    harbor_result = execute_harbor(phase.harbor_request)
    terminated_early = harbor_result.terminated_early
    termination_reason = harbor_result.termination_reason
    process_metrics = collect_process_metrics(
        request.task,
        harbor_result.trial_dir,
        harness=request.config.agent.value,
    )
    events = collect_session_events(
        harbor_result.trial_dir,
        harness=request.config.agent.value,
    )

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
    evidence_artifacts: dict[str, Any] = {
        "screenshot_command": list(phase.screenshot_command) if phase.screenshot_command else None,
        "homepage_pre": str(phase.pre_screenshot_path) if phase.pre_screenshot_path else None,
        "homepage_post": None,
        "final_workspace_archive": None,
        "errors": list(phase.evidence_errors),
    }
    if phase.screenshot_command and not execution.terminated_early:
        archive_path, hydrate_error = _hydrate_workspace_from_final_app(
            execution.harbor_result,
            phase.context.workspace,
        )
        if archive_path:
            evidence_artifacts["final_workspace_archive"] = str(archive_path)
            post_path, post_error = _run_homepage_capture_command(
                list(phase.screenshot_command),
                phase.context.workspace,
                phase.layout.root_dir / "homepage-post.png",
            )
            if post_path:
                evidence_artifacts["homepage_post"] = str(post_path)
            if post_error:
                evidence_artifacts["errors"].append(f"homepage-post capture failed: {post_error}")
        if hydrate_error:
            evidence_artifacts["errors"].append(hydrate_error)

    workspace_prune = _prune_workspace_artifacts(phase.layout.workspace_dir)
    return PersistedArtifacts(
        scaffold_meta=build_scaffold_meta(request, phase.context),
        task_version_meta=build_task_version_meta(request, phase.context),
        verifier_artifacts=persist_verifier_artifacts(
            execution.harbor_result, phase.layout.verifier_dir
        ),
        agent_artifacts=persist_agent_artifacts(execution.harbor_result, phase.layout.agent_dir),
        harbor_artifacts=persist_harbor_artifacts(execution.harbor_result, phase.layout.harbor_dir),
        evidence_artifacts=evidence_artifacts,
        workspace_prune=workspace_prune,
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
    write_run_analysis(phase.layout, request, scorecard, execution.harbor_result)
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
            task_name=request.task.name,
            task_version=request.task.version,
            scaffold_root=request.task.scaffold.root,
        ),
        duration_sec=execution.duration_sec,
        terminated_early=execution.terminated_early,
        termination_reason=execution.termination_reason,
        scores=scorecard,
        events=execution.events,
        gate_history=execution.outputs.gate_history,
    )
