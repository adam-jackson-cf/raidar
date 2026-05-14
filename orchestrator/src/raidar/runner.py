"""Task execution via Harbor."""

import errno
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
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .agents.config import AgentSpec, Harness
from .agents.harbor_routing import (
    is_task_image_reuse_enabled,
    task_image_prefix,
)
from .agents.rules import SYSTEM_RULES, inject_rules
from .audit.workspace_diff import diff_directories, directory_fingerprint
from .config import settings
from .schemas.events import GateEvent, TraceEvent
from .schemas.scenario import RequirementSpec, ScenarioDefinition
from .schemas.scorecard import (
    AcceptanceCheck,
    AcceptanceScore,
    CoverageScore,
    EvalRun,
    ExecutionValidityScore,
    FunctionalScore,
    GateCheck,
    MetricResult,
    PerformanceGatesScore,
    RequirementsCoverageScore,
    ResourceEfficiencyScore,
    Scorecard,
    VerificationStabilityScore,
    VisualScore,
)
from .scoring.acceptance import run_deterministic_check
from .starter import StarterSource

SCORING_SCHEMA_VERSION = "2.0.0"
HARBOR_TIMEOUT_BUFFER_SEC = 120
TASK_IMAGE_BUILD_MIN_TIMEOUT_SEC = 120
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
    r"GEMINI_API_KEY|COPILOT_API_KEY|CURSOR_API_KEY|PI_API_KEY|"
    r"GOOGLE_APPLICATION_CREDENTIALS"
    r")=([^\s\"']+)"
)
JSON_SECRET_PATTERN = re.compile(
    r'"('
    r"OPENAI_API_KEY|ANTHROPIC_API_KEY|CLAUDE_CODE_API_KEY|CLAUDE_CODE_OAUTH_TOKEN|"
    r"GEMINI_API_KEY|COPILOT_API_KEY|CURSOR_API_KEY|PI_API_KEY|"
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
HARNESS_NPM_PACKAGES: dict[str, str] = {
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
RAIDAR_CACHE_VERSION = "1"
RAIDAR_CACHE_PRUNE_INTERVAL_SEC = 6 * 60 * 60
RAIDAR_PREP_CACHE_MAX_AGE_SEC = 7 * 24 * 60 * 60
RAIDAR_PREP_CACHE_MAX_BYTES = 2 * 1024 * 1024 * 1024
RAIDAR_DOCKER_CACHE_MAX_AGE_SEC = 14 * 24 * 60 * 60
RAIDAR_CACHE_LOCK_TIMEOUT_SEC = 10 * 60
RAIDAR_CACHE_LOCK_STALE_SEC = 60 * 60
RAIDAR_DOCKER_LABEL_MANAGED = "io.raidar.cache.managed"
RAIDAR_DOCKER_LABEL_KEY = "io.raidar.cache.key"
RAIDAR_DOCKER_LABEL_HARNESS = "io.raidar.cache.harness"
RAIDAR_DOCKER_LABEL_REPO = "io.raidar.cache.repo"


class StarterPreflightError(RuntimeError):
    """Fatal starter setup error that unscored and aborts an entire experiment."""


def load_scenario(scenario_path: Path) -> ScenarioDefinition:
    """Load scenario definition from YAML file."""
    with open(scenario_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return ScenarioDefinition.model_validate(data)


@dataclass(frozen=True, slots=True)
class RunRequest:
    """Input bundle for running a scenario."""

    scenario: ScenarioDefinition
    config: AgentSpec
    scenario_dir: Path
    execution_dir: Path
    repeat_index: int = 1


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    """Resolved starter context for a scenario run."""

    starter_source: StarterSource
    baseline_workspace: Path
    baseline_cache_key: str
    baseline_cache_status: str
    baseline_cache_hit: bool
    baseline_metadata_path: Path
    baseline_fingerprint: str
    workspace: Path
    injected_rules: Path | None
    metadata_path: Path


@dataclass(frozen=True, slots=True)
class EvaluationOutputs:
    """Computed scoring outputs for a run."""

    functional: FunctionalScore
    acceptance: AcceptanceScore
    visual: VisualScore | None
    verification_stability: VerificationStabilityScore
    test_coverage: CoverageScore
    requirements_coverage: RequirementsCoverageScore
    execution_validity: ExecutionValidityScore
    performance_gates: PerformanceGatesScore
    metric_results: list[MetricResult]
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
    """Process metrics extracted from Harbor harness logs."""

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
    git_commit_verification_bypass_commands: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RunLayout:
    """Filesystem layout for a canonical evaluation run directory."""

    run_id: str
    start_time: datetime
    run_label: str
    root_dir: Path
    workspace_dir: Path
    verifier_dir: Path
    harness_dir: Path
    harbor_dir: Path
    run_json_path: Path
    report_path: Path


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
class TaskImageRef:
    """Content-addressed Docker image reference for Harbor execution."""

    image_name: str
    cache_key: str
    tag: str


@dataclass(frozen=True, slots=True)
class TaskImageBuildResult:
    """Result of a Harbor task image build."""

    completed_process: subprocess.CompletedProcess[str]
    timed_out: bool = False
    timeout_sec: int | None = None


@dataclass(frozen=True, slots=True)
class BaselineWorkspaceCacheResult:
    """Cache result for the shared prepared baseline workspace."""

    metadata_path: Path
    baseline_fingerprint: str
    hit: bool
    status: str


@dataclass(frozen=True, slots=True)
class WorkspacePreparationPhaseResult:
    """Workspace preparation phase output."""

    layout: RunLayout
    context: WorkspaceContext
    harbor_request: HarborExecutionRequest
    prep_phase_timings_sec: dict[str, float]
    prep_total_sec: float
    cache_metadata: dict[str, Any]
    auth_metadata: dict[str, Any]
    screenshot_command: tuple[str, ...] | None
    evidence_errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExecutionPhaseResult:
    """Harbor execution + verifier loading phase output."""

    harbor_result: HarborExecutionResult
    terminated_early: bool
    termination_reason: str | None
    process_metrics: ProcessMetrics
    events: list[TraceEvent]
    outputs: EvaluationOutputs
    duration_sec: float
    prep_phase_timings_sec: dict[str, float]
    prep_total_sec: float
    cache_metadata: dict[str, Any]
    auth_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PersistedArtifacts:
    """Persisted artifact metadata used for score synthesis."""

    starter_meta: dict
    scenario_revision_meta: dict[str, str | None]
    verifier_artifacts: dict[str, str]
    harness_artifacts: dict[str, str]
    harbor_artifacts: dict[str, str]
    evidence_artifacts: dict[str, Any]
    workspace_prune: dict[str, Any]
    workspace_changes: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ScorecardBuildContext:
    """Input bundle for scorecard synthesis."""

    request: RunRequest
    layout: RunLayout
    context: WorkspaceContext
    artifacts: PersistedArtifacts
    execution: ExecutionPhaseResult


def _slug_fragment(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "unknown"


def _harness_value(harness: Harness | Any) -> str:
    return str(getattr(harness, "value", harness))


def _run_label(repeat_index: int) -> str:
    return f"run-{repeat_index:02d}"


def _repeat_workspace_dir(request: RunRequest) -> Path:
    return request.execution_dir / "runs" / _run_label(request.repeat_index) / "workspace"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _raidar_cache_root() -> Path:
    return _repo_root() / ".cache" / "raidar"


def _prep_cache_root() -> Path:
    return _raidar_cache_root() / "prep"


def _baseline_cache_entry_dir(cache_key: str) -> Path:
    return _prep_cache_root() / "baselines" / cache_key


def _baseline_cache_workspace_dir(cache_key: str) -> Path:
    return _baseline_cache_entry_dir(cache_key) / "workspace"


def _preflight_cache_file(cache_key: str) -> Path:
    return _prep_cache_root() / "preflight" / f"{cache_key}.ok.json"


def _workspace_runtime_env(
    workspace: Path, base_env: dict[str, str] | None = None
) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    tmp_dir = workspace / ".tmp"
    cache_dir = workspace / ".cache"
    uv_cache_dir = cache_dir / "uv"
    bun_cache_dir = cache_dir / "bun"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    uv_cache_dir.mkdir(parents=True, exist_ok=True)
    bun_cache_dir.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "TMPDIR": str(tmp_dir),
            "TMP": str(tmp_dir),
            "TEMP": str(tmp_dir),
            "XDG_CACHE_HOME": str(cache_dir),
            "UV_CACHE_DIR": str(uv_cache_dir),
            "BUN_INSTALL_CACHE_DIR": str(bun_cache_dir),
        }
    )
    return env


def _cache_lock_root() -> Path:
    return _raidar_cache_root() / "locks"


def _task_image_cache_metadata_path(cache_key: str) -> Path:
    return _raidar_cache_root() / "images" / f"{cache_key}.json"


def _maintenance_marker_path() -> Path:
    return _raidar_cache_root() / "maintenance" / "last-prune.json"


def _repo_cache_identity() -> str:
    digest = hashlib.sha256(str(_repo_root().resolve()).encode("utf-8")).hexdigest()
    return digest[:16]


def _hash_json_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _effective_rule_source(request: RunRequest) -> Path | None:
    injected_rule_name = SYSTEM_RULES.get(request.config.harness)
    if not injected_rule_name:
        return None
    candidate = request.scenario_dir / "rules" / injected_rule_name
    return candidate if candidate.exists() else None


def _injected_rules_hash(request: RunRequest) -> str | None:
    rule_source = _effective_rule_source(request)
    if rule_source is None:
        return None
    return _hash_bytes(rule_source.read_bytes())


def _baseline_cache_key(request: RunRequest, starter_fingerprint: str) -> str:
    payload = {
        "cache_version": RAIDAR_CACHE_VERSION,
        "starter_fingerprint": starter_fingerprint,
        "harness": request.config.harness.value,
        "injected_rules_hash": _injected_rules_hash(request),
        "setup_actions": getattr(request.scenario.verification, "setup_actions", []),
    }
    return _hash_json_payload(payload)


def _touch_cache_path(path: Path) -> None:
    now = time.time()
    try:
        os.utime(path, (now, now))
    except FileNotFoundError:
        return


def _cache_lock_owner_pid(lock_dir: Path) -> int | None:
    owner_path = lock_dir / "owner.json"
    try:
        payload = json.loads(owner_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    pid = payload.get("pid")
    return pid if isinstance(pid, int) and pid > 0 else None


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def _cache_key_lock(lock_key: str, *, timeout_sec: int = RAIDAR_CACHE_LOCK_TIMEOUT_SEC):
    lock_root = _cache_lock_root()
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_dir = lock_root / f"{lock_key}.lock"
    deadline = time.monotonic() + timeout_sec

    while True:
        try:
            lock_dir.mkdir(parents=False, exist_ok=False)
            (lock_dir / "owner.json").write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "created_at": datetime.now(UTC).isoformat(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            break
        except FileExistsError as err:
            try:
                age_sec = time.time() - lock_dir.stat().st_mtime
            except FileNotFoundError:
                continue
            owner_pid = _cache_lock_owner_pid(lock_dir)
            if owner_pid is not None and not _process_exists(owner_pid):
                shutil.rmtree(lock_dir, ignore_errors=True)
                continue
            if age_sec > RAIDAR_CACHE_LOCK_STALE_SEC:
                shutil.rmtree(lock_dir, ignore_errors=True)
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for cache lock `{lock_key}`.") from err
            time.sleep(0.1)

    try:
        yield
    finally:
        shutil.rmtree(lock_dir, ignore_errors=True)


def _baseline_workspace_lock(baseline_workspace_dir: Path) -> threading.Lock:
    key = baseline_workspace_dir.resolve()
    with _SUITE_BASELINE_LOCKS_GUARD:
        lock = _SUITE_BASELINE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SUITE_BASELINE_LOCKS[key] = lock
        return lock


def _baseline_cache_entry_metadata(
    *,
    baseline_workspace_dir: Path,
    metadata_path: Path,
    baseline_cache_key: str,
    harness: Harness,
) -> dict[str, str] | None:
    if not baseline_workspace_dir.exists() or not metadata_path.exists():
        return None
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("cache_key") != baseline_cache_key:
        return None
    if payload.get("harness") != _harness_value(harness):
        return None
    baseline_fingerprint = payload.get("baseline_fingerprint")
    if not isinstance(baseline_fingerprint, str) or not baseline_fingerprint:
        return None
    if baseline_fingerprint != directory_fingerprint(baseline_workspace_dir):
        return None
    return {"baseline_fingerprint": baseline_fingerprint}


def _ensure_baseline_workspace(
    *,
    scenario: ScenarioDefinition,
    starter_dir: Path,
    baseline_workspace_dir: Path,
    baseline_cache_key: str,
    scenario_dir: Path,
    harness: Harness,
) -> BaselineWorkspaceCacheResult:
    entry_dir = baseline_workspace_dir.parent
    metadata_path = entry_dir / "metadata.json"
    lock_key = f"baseline-{baseline_cache_key}"
    with _baseline_workspace_lock(baseline_workspace_dir), _cache_key_lock(lock_key):
        entry_metadata = _baseline_cache_entry_metadata(
            baseline_workspace_dir=baseline_workspace_dir,
            metadata_path=metadata_path,
            baseline_cache_key=baseline_cache_key,
            harness=harness,
        )
        if entry_metadata is not None:
            _touch_cache_path(entry_dir)
            return BaselineWorkspaceCacheResult(
                metadata_path=metadata_path,
                baseline_fingerprint=entry_metadata["baseline_fingerprint"],
                hit=True,
                status="hit",
            )
        invalidated = entry_dir.exists()
        if entry_dir.exists():
            shutil.rmtree(entry_dir, ignore_errors=True)
        entry_dir.mkdir(parents=True, exist_ok=True)
        try:
            prepare_workspace(
                starter_dir=starter_dir,
                target_dir=baseline_workspace_dir,
                scenario_dir=scenario_dir,
                harness=harness,
            )
            _run_workspace_setup_actions(
                workspace=baseline_workspace_dir,
                env=_workspace_runtime_env(baseline_workspace_dir, os.environ.copy()),
                setup_actions=scenario.verification.setup_actions,
            )
            baseline_fingerprint = directory_fingerprint(baseline_workspace_dir)
            metadata_path.write_text(
                json.dumps(
                    {
                        "cache_key": baseline_cache_key,
                        "baseline_fingerprint": baseline_fingerprint,
                        "created_at": datetime.now(UTC).isoformat(),
                        "harness": _harness_value(harness),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            _touch_cache_path(entry_dir)
            return BaselineWorkspaceCacheResult(
                metadata_path=metadata_path,
                baseline_fingerprint=baseline_fingerprint,
                hit=False,
                status="invalidated" if invalidated else "miss",
            )
        except Exception:
            shutil.rmtree(entry_dir, ignore_errors=True)
            raise


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


def _resolve_homepage_screenshot_command(
    task: ScenarioDefinition, workspace: Path
) -> list[str] | None:
    del workspace
    if task.visual and task.visual.screenshot_command:
        return list(task.visual.screenshot_command)
    return None


def _visual_reference_assets(request: RunRequest) -> list[tuple[Path, Path]]:
    """Return scenario-local visual reference assets and their relative targets."""
    if request.scenario.visual is None:
        return []
    reference_path = Path(request.scenario.visual.reference_image)
    if reference_path.is_absolute():
        return []
    source_reference = (request.scenario_dir / reference_path).resolve()
    if not source_reference.exists():
        return []

    assets = [(source_reference, reference_path)]
    for sibling in sorted(
        source_reference.parent.glob(f"{source_reference.stem}-region-*{source_reference.suffix}")
    ):
        assets.append((sibling, reference_path.parent / sibling.name))
    return assets


def _visual_region_names(request: RunRequest) -> list[str]:
    """Return authored or inferred visual region names for one scenario."""
    if request.scenario.visual is None:
        return []
    configured = [region.name for region in request.scenario.visual.regions]
    if configured:
        return configured

    prefix = f"{Path(request.scenario.visual.reference_image).stem}-region-"
    suffix = Path(request.scenario.visual.reference_image).suffix
    inferred: list[str] = []
    for _, relative_target in _visual_reference_assets(request):
        filename = relative_target.name
        if not filename.startswith(prefix) or not filename.endswith(suffix):
            continue
        inferred.append(filename[len(prefix) : len(filename) - len(suffix)])
    return inferred


def scenario_evaluation_profile(scenario: ScenarioDefinition) -> str:
    """Derive deterministic evaluation-profile identifier for a scenario."""
    return "+".join(scenario_metrics(scenario))


def scenario_metrics(scenario: ScenarioDefinition) -> list[str]:
    """Return deterministic ordered metric ids for a scenario."""
    return scenario.metric_ids()


def _run_homepage_capture_command(
    command: list[str], workspace: Path, output_path: Path
) -> tuple[Path | None, str | None]:
    actual_path = workspace / "actual.png"
    actual_path.unlink(missing_ok=True)

    install_error = _ensure_workspace_capture_dependencies(workspace)
    if install_error:
        return None, install_error

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


def _ensure_workspace_capture_dependencies(workspace: Path) -> str | None:
    package_json = workspace / "package.json"
    lockfile = workspace / "bun.lock"
    node_modules = workspace / "node_modules"
    next_package = node_modules / "next" / "package.json"
    if not package_json.exists() or not lockfile.exists() or next_package.exists():
        return None

    try:
        completed = subprocess.run(
            ["bun", "install", "--frozen-lockfile"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.screenshot,
            check=False,
            env=_workspace_runtime_env(workspace),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return f"Failed to install workspace dependencies before capture: {exc}"

    if completed.returncode != 0:
        output = (completed.stdout + "\n" + completed.stderr).strip()[:4000]
        return (
            "Failed to install workspace dependencies before capture: "
            f"`bun install --frozen-lockfile` exited {completed.returncode}: {output}"
        )
    return None


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


def _remove_tree_with_retries(path: Path, *, attempts: int = 3, delay_sec: float = 0.2) -> None:
    last_error: OSError | None = None
    transient_errnos = {errno.ENOTEMPTY, errno.EBUSY, errno.EPERM}
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            last_error = exc
            if exc.errno not in transient_errnos or attempt == attempts - 1:
                raise
            time.sleep(delay_sec)
    if last_error is not None:
        raise last_error


def _prune_workspace_artifacts(workspace: Path) -> dict[str, Any]:
    removed: list[str] = []
    reclaimed_bytes = 0
    for dirname in WORKSPACE_PRUNE_DIRS:
        candidate = workspace / dirname
        if not candidate.exists():
            continue
        reclaimed_bytes += _directory_size_bytes(candidate)
        _remove_tree_with_retries(candidate)
        removed.append(dirname)
    return {
        "removed": removed,
        "reclaimed_bytes": reclaimed_bytes,
    }


def _workspace_changes_from_baseline(
    *,
    baseline_workspace: Path,
    run_workspace: Path,
    run_root_dir: Path,
) -> dict[str, Any]:
    if not baseline_workspace.exists():
        return {
            "added": [],
            "removed": [],
            "modified": [],
            "changed_files": [],
            "changed_file_count": 0,
            "artifact": None,
            "error": f"Missing baseline workspace: {baseline_workspace}",
        }

    diff = diff_directories(baseline_workspace, run_workspace)
    artifact_path = run_root_dir / "workspace-diff.json"
    artifact_path.write_text(
        json.dumps(
            {
                "baseline_workspace": str(baseline_workspace),
                "run_workspace": str(run_workspace),
                "added": diff.added,
                "removed": diff.removed,
                "modified": diff.modified,
                "changed_files": diff.changed_files,
                "changed_file_count": diff.count,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "added": diff.added,
        "removed": diff.removed,
        "modified": diff.modified,
        "changed_files": diff.changed_files,
        "changed_file_count": diff.count,
        "artifact": str(artifact_path),
        "error": None,
    }


def _preflight_cache_key(request: RunRequest, context: WorkspaceContext) -> str:
    payload = {
        "cache_version": RAIDAR_CACHE_VERSION,
        "baseline_cache_key": context.baseline_cache_key,
        "harness": request.config.harness.value,
        "starter_fingerprint": context.starter_source.fingerprint,
        "required_commands": request.scenario.verification.required_commands,
    }
    return _hash_json_payload(payload)


def _run_starter_preflight_install(workspace: Path, env: dict[str, str]) -> None:
    install = subprocess.run(
        ["bun", "install", "--frozen-lockfile"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=settings.timeouts.build,
        env=env,
    )
    if install.returncode == 0:
        return
    output = (install.stdout + "\n" + install.stderr).strip()[:8000]
    raise StarterPreflightError(
        "Starter preflight failed: `bun install --frozen-lockfile` exited "
        f"{install.returncode}\n{output}"
    )


def _should_skip_preflight_command(command: list[str], has_tests: bool) -> bool:
    if has_tests:
        return False
    command_text = " ".join(command)
    return "test:coverage" in command_text or command_text.endswith(" test")


def _run_starter_preflight_command(
    workspace: Path, env: dict[str, str], command: list[str]
) -> None:
    completed = subprocess.run(
        command,
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=_command_timeout(command),
        env=env,
    )
    if completed.returncode == 0:
        return
    output = (completed.stdout + "\n" + completed.stderr).strip()[:8000]
    rendered = " ".join(shlex.quote(part) for part in command)
    raise StarterPreflightError(
        f"Starter preflight failed: `{rendered}` exited {completed.returncode}\n{output}"
    )


def _run_workspace_setup_actions(
    *,
    workspace: Path,
    env: dict[str, str],
    setup_actions: list[list[str]],
) -> None:
    for command in setup_actions:
        completed = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=_command_timeout(command),
            env=env,
        )
        if completed.returncode == 0:
            continue
        output = (completed.stdout + "\n" + completed.stderr).strip()[:8000]
        rendered = " ".join(shlex.quote(part) for part in command)
        raise StarterPreflightError(
            f"Starter setup action failed: `{rendered}` exited {completed.returncode}\n{output}"
        )


def _write_starter_preflight_cache(
    *,
    cache_file: Path,
    harness: str,
    starter_fingerprint: str,
    baseline_cache_key: str,
    setup_actions: list[list[str]],
    required_commands: list[list[str]],
) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps(
            {
                "cache_version": RAIDAR_CACHE_VERSION,
                "harness": harness,
                "starter_fingerprint": starter_fingerprint,
                "baseline_cache_key": baseline_cache_key,
                "validated_at": datetime.now(UTC).isoformat(),
                "setup_actions": setup_actions,
                "required_commands": required_commands,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def ensure_starter_preflight(request: RunRequest, context: WorkspaceContext) -> bool | None:
    """Validate starter baseline commands once per effective prep input set."""
    required_commands = request.scenario.verification.required_commands
    setup_actions = getattr(request.scenario.verification, "setup_actions", [])
    if not required_commands:
        return None

    preflight_workspace = getattr(context, "baseline_workspace", context.workspace)

    cache_key = _preflight_cache_key(request, context)
    cache_file = _preflight_cache_file(cache_key)
    with _cache_key_lock(f"preflight-{cache_key}"):
        if cache_file.exists():
            _touch_cache_path(cache_file)
            return True

        env = _workspace_runtime_env(preflight_workspace, os.environ.copy())
        _run_starter_preflight_install(preflight_workspace, env)

        has_tests = _workspace_has_tests(preflight_workspace)
        for command in required_commands:
            if _should_skip_preflight_command(command, has_tests):
                continue
            _run_starter_preflight_command(preflight_workspace, env, command)

        _write_starter_preflight_cache(
            cache_file=cache_file,
            harness=request.config.harness.value,
            starter_fingerprint=context.starter_source.fingerprint,
            baseline_cache_key=context.baseline_cache_key,
            setup_actions=setup_actions,
            required_commands=required_commands,
        )
        _touch_cache_path(cache_file)
        return False


def cleanup_stale_harbor_resources(
    *, include_containers: bool = True, include_build_processes: bool = False
) -> None:
    """Remove stale Harbor containers and/or orphaned build processes."""
    if include_containers:
        cleanup_stale_harbor_containers()
    if include_build_processes:
        cleanup_stale_harbor_build_processes()


def _cache_last_used_epoch(path: Path) -> float:
    return path.stat().st_mtime


def _prune_prep_cache_entries() -> None:
    baselines_root = _prep_cache_root() / "baselines"
    preflight_root = _prep_cache_root() / "preflight"
    baselines_root.mkdir(parents=True, exist_ok=True)
    preflight_root.mkdir(parents=True, exist_ok=True)

    now = time.time()
    baseline_entries = [path for path in baselines_root.iterdir() if path.is_dir()]
    total_bytes = 0
    retained: list[tuple[float, int, Path]] = []
    for entry in baseline_entries:
        last_used = _cache_last_used_epoch(entry)
        if now - last_used > RAIDAR_PREP_CACHE_MAX_AGE_SEC:
            shutil.rmtree(entry, ignore_errors=True)
            continue
        size_bytes = _directory_size_bytes(entry)
        total_bytes += size_bytes
        retained.append((last_used, size_bytes, entry))

    for _last_used, size_bytes, entry in sorted(retained, key=lambda item: item[0]):
        if total_bytes <= RAIDAR_PREP_CACHE_MAX_BYTES:
            break
        shutil.rmtree(entry, ignore_errors=True)
        total_bytes -= size_bytes

    for cache_file in preflight_root.glob("*.ok.json"):
        try:
            last_used = _cache_last_used_epoch(cache_file)
        except FileNotFoundError:
            continue
        if now - last_used > RAIDAR_PREP_CACHE_MAX_AGE_SEC:
            cache_file.unlink(missing_ok=True)


def _load_task_image_cache_payload(metadata_path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        metadata_path.unlink(missing_ok=True)
        return None
    if not isinstance(payload, dict):
        metadata_path.unlink(missing_ok=True)
        return None
    return payload


def _stale_task_image_name(
    metadata_path: Path, *, now: float, active_image_name: str | None
) -> str | None:
    payload = _load_task_image_cache_payload(metadata_path)
    if payload is None:
        return None
    image_name = payload.get("image_name")
    if not isinstance(image_name, str):
        metadata_path.unlink(missing_ok=True)
        return None
    if image_name == active_image_name:
        return None
    try:
        last_used = _cache_last_used_epoch(metadata_path)
    except FileNotFoundError:
        return None
    if now - last_used <= RAIDAR_DOCKER_CACHE_MAX_AGE_SEC:
        return None
    return image_name


def _managed_task_image(image_name: str, run_env: dict[str, str]) -> bool:
    labels = _inspect_docker_image_labels(image_name, run_env)
    return labels is not None and (
        labels.get(RAIDAR_DOCKER_LABEL_MANAGED) == "true"
        and labels.get(RAIDAR_DOCKER_LABEL_REPO) == _repo_cache_identity()
    )


def _remove_task_image(image_name: str, run_env: dict[str, str]) -> None:
    subprocess.run(
        ["docker", "image", "rm", "-f", image_name],
        capture_output=True,
        text=True,
        timeout=60,
        env=run_env,
        check=False,
    )


def _prune_stale_task_images(*, run_env: dict[str, str], active_image_name: str | None) -> None:
    images_root = _raidar_cache_root() / "images"
    images_root.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for metadata_path in images_root.glob("*.json"):
        image_name = _stale_task_image_name(
            metadata_path,
            now=now,
            active_image_name=active_image_name,
        )
        if image_name is None:
            continue
        try:
            if _managed_task_image(image_name, run_env):
                _remove_task_image(image_name, run_env)
        except FileNotFoundError:
            return
        metadata_path.unlink(missing_ok=True)


def _maybe_run_cache_maintenance(*, run_env: dict[str, str], active_image_name: str | None) -> None:
    marker_path = _maintenance_marker_path()
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if marker_path.exists():
            age_sec = time.time() - marker_path.stat().st_mtime
            if age_sec < RAIDAR_CACHE_PRUNE_INTERVAL_SEC:
                return
    except OSError:
        return

    try:
        with _cache_key_lock("maintenance", timeout_sec=30):
            if marker_path.exists():
                age_sec = time.time() - marker_path.stat().st_mtime
                if age_sec < RAIDAR_CACHE_PRUNE_INTERVAL_SEC:
                    return
            _prune_prep_cache_entries()
            _prune_stale_task_images(run_env=run_env, active_image_name=active_image_name)
            marker_path.write_text(
                json.dumps({"last_pruned_at": datetime.now(UTC).isoformat()}, indent=2),
                encoding="utf-8",
            )
    except (OSError, RuntimeError, TimeoutError, ValueError):
        return


def cleanup_stale_harbor_containers() -> None:
    """Remove stale Harbor scenario-run containers that can block future runs."""
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
    try:
        listing = subprocess.run(
            ["ps", "-ax", "-o", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
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
    for key in adapter.excluded_run_env_keys():
        run_env.pop(key, None)
    if adapter.harbor_harness_import_path():
        _inject_secret_file_env(run_env)
        _inject_local_secret_file_env(run_env, adapter.local_secret_files())
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


def _inject_local_secret_file_env(run_env: dict[str, str], secret_files: dict[str, Path]) -> None:
    for key, source_path in secret_files.items():
        run_env[f"{SECRET_FILE_ENV_PREFIX}{key}"] = str(
            _write_harbor_secret_file_from_path(secret_name=key, source_path=source_path)
        )


def _write_harbor_secret_file(*, secret_name: str, secret_value: str) -> Path:
    secret_dir = Path.home() / ".agentic-eval" / "secrets"
    secret_dir.mkdir(parents=True, exist_ok=True)
    secret_path = secret_dir / f"{secret_name.lower()}-{uuid.uuid4().hex}"
    secret_path.write_text(secret_value, encoding="utf-8")
    secret_path.chmod(0o600)
    return secret_path


def _write_harbor_secret_file_from_path(*, secret_name: str, source_path: Path) -> Path:
    secret_dir = Path.home() / ".agentic-eval" / "secrets"
    secret_dir.mkdir(parents=True, exist_ok=True)
    secret_path = secret_dir / f"{secret_name.lower()}-{uuid.uuid4().hex}"
    secret_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
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
    starter_dir: Path,
    target_dir: Path,
    scenario_dir: Path,
    harness: Harness,
) -> tuple[Path, Path | None]:
    """Prepare workspace by copying the starter and injecting rules.

    Args:
        starter_dir: Path to resolved starter template/version
        target_dir: Path to create workspace
        scenario_dir: Path to scenario directory (contains rules/)
        harness: Harness id for rule file selection
    Returns:
        Tuple of workspace path and injected rules file (if any)
    """
    # Copy the starter into the run workspace.
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(
        starter_dir,
        target_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("node_modules", ".next", "jobs"),
    )

    # Inject rules
    injected_rules: Path | None = None
    rules_dir = scenario_dir / "rules"
    if rules_dir.exists():
        injected_rules = inject_rules(rules_dir, target_dir, harness)

    return target_dir, injected_rules


def _load_baseline_scripts(starter_source: StarterSource) -> dict[str, str]:
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
        metric.model_dump(mode="json", exclude_none=True) for metric in request.scenario.metrics
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


def _scenario_spec_acceptance_block(request: RunRequest) -> dict[str, Any]:
    return {
        "deterministic_checks": [
            {
                "type": check.type,
                "pattern": check.pattern,
                "description": check.description,
            }
            for check in request.scenario.acceptance.deterministic_checks
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
                "required_test_evidence": [
                    evidence.model_dump(mode="json")
                    for evidence in requirement.required_test_evidence
                ],
            }
            for requirement in request.scenario.acceptance.requirements
        ],
    }


def _scenario_spec_visual_block(request: RunRequest) -> dict[str, Any] | None:
    if request.scenario.visual is None:
        return None
    return {
        "reference_image": request.scenario.visual.reference_image,
        "screenshot_command": request.scenario.visual.screenshot_command,
        "viewport": (
            request.scenario.visual.viewport.model_dump(mode="json")
            if request.scenario.visual.viewport is not None
            else None
        ),
        "scoring": request.scenario.visual.scoring.model_dump(mode="json", by_alias=True),
        "pass_policy": request.scenario.visual.pass_policy.model_dump(mode="json"),
        "regions": [region.model_dump(mode="json") for region in request.scenario.visual.regions],
    }


def _scenario_spec_weights_block() -> dict[str, float]:
    return {
        "functional": settings.weights.functional,
        "acceptance": settings.weights.acceptance,
        "visual": settings.weights.visual,
        "verification_stability": settings.weights.verification_stability,
    }


def _scenario_score_profile_block(request: RunRequest) -> dict[str, Any]:
    if request.scenario.score_profile is None:
        return {
            "id": "legacy-resource-efficiency-v1",
            "baseline_lineage": None,
            "weights": {"resource-efficiency": 1.0},
        }
    return request.scenario.score_profile.model_dump(mode="json")


def _build_verifier_scenario_spec(request: RunRequest, context: WorkspaceContext) -> dict:
    return {
        "scenario_name": request.scenario.name,
        "metrics": _scenario_spec_metrics_block(request),
        "score_profile": _scenario_score_profile_block(request),
        "verification": _scenario_spec_verification_block(request),
        "acceptance": _scenario_spec_acceptance_block(request),
        "visual": _scenario_spec_visual_block(request),
        "weights": _scenario_spec_weights_block(),
        "baseline_scripts": _load_baseline_scripts(context.starter_source),
    }


def _verifier_script_template_path() -> Path:
    return Path(__file__).parent / "assets" / "verifier-score-scenario.mjs"


def _verifier_scorer_script() -> str:
    return _verifier_script_template_path().read_text(encoding="utf-8")


def _task_image_reference(request: RunRequest, task_bundle_path: Path) -> TaskImageRef | None:
    if not is_task_image_reuse_enabled():
        return None
    environment_dir = task_bundle_path / "environment"
    dockerfile_path = environment_dir / "Dockerfile"
    app_dir = environment_dir / "app"
    tests_dir = task_bundle_path / "tests"
    if not dockerfile_path.exists() or not app_dir.exists():
        return None

    payload = {
        "cache_version": RAIDAR_CACHE_VERSION,
        "task_image_version": "3",
        "harness": request.config.harness.value,
        "harness_package": _harness_npm_package(request.config.harness.value),
        "harness_cli_version": _harness_cli_version(request.config.harness.value),
        "dockerfile": dockerfile_path.read_text(encoding="utf-8"),
        "app_fingerprint": directory_fingerprint(app_dir),
        "tests_fingerprint": directory_fingerprint(tests_dir) if tests_dir.exists() else None,
    }
    cache_key = _hash_json_payload(payload)
    digest = cache_key[:16]
    harness_fragment = _slug_fragment(request.config.harness.value)
    image_tag = f"task-env-{harness_fragment}-{digest}"
    return TaskImageRef(
        image_name=f"{task_image_prefix()}:{image_tag}",
        cache_key=cache_key,
        tag=image_tag,
    )


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


def _harness_npm_package(harness: str) -> str | None:
    return HARNESS_NPM_PACKAGES.get(harness)


def _codex_cli_npm_version() -> str | None:
    version_output = _harness_cli_version("codex-cli")
    if not version_output:
        return None
    match = re.search(r"(\d+\.\d+\.\d+)", version_output)
    return match.group(1) if match else None


def _harness_npm_install_spec(harness: str) -> str | None:
    package = _harness_npm_package(harness)
    if package is None:
        return None
    if harness != "codex-cli":
        return package
    version = _codex_cli_npm_version()
    return f"{package}@{version}" if version else package


def _harness_cli_version(harness: str) -> str | None:
    """Return the locally resolved CLI version that should invalidate task images."""
    if harness != "codex-cli":
        return None
    executable = os.environ.get("CODEX_CLI_PATH") or shutil.which("codex")
    if not executable:
        return None
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return (completed.stdout or "").strip() or None


def _inspect_docker_image_labels(image_name: str, run_env: dict[str, str]) -> dict[str, str] | None:
    try:
        probe = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                image_name,
                "--format",
                "{{json .Config.Labels}}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=run_env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Docker CLI not found.") from exc
    if probe.returncode != 0:
        return None
    labels = json.loads((probe.stdout or "null").strip() or "null")
    if not isinstance(labels, dict):
        return {}
    return {str(key): str(value) for key, value in labels.items()}


def _expected_task_image_labels(image_ref: TaskImageRef, harness: str) -> dict[str, str]:
    return {
        RAIDAR_DOCKER_LABEL_MANAGED: "true",
        RAIDAR_DOCKER_LABEL_KEY: image_ref.cache_key,
        RAIDAR_DOCKER_LABEL_HARNESS: harness,
        RAIDAR_DOCKER_LABEL_REPO: _repo_cache_identity(),
    }


def _task_image_cache_hit(
    image_ref: TaskImageRef, *, harness: str, run_env: dict[str, str]
) -> bool:
    labels = _inspect_docker_image_labels(image_ref.image_name, run_env)
    if labels is None:
        return False
    expected_labels = _expected_task_image_labels(image_ref, harness)
    return all(labels.get(key) == value for key, value in expected_labels.items())


def _task_image_build_command(
    image_ref: TaskImageRef, dockerfile: Path, context_dir: Path, *, harness: str
) -> list[str]:
    command = [
        "docker",
        "build",
        "--tag",
        image_ref.image_name,
        "--file",
        str(dockerfile),
    ]
    for key, value in _expected_task_image_labels(image_ref, harness).items():
        command.extend(["--label", f"{key}={value}"])
    command.append(str(context_dir))
    return command


def _run_task_image_build(
    build_cmd: list[str], run_env: dict[str, str], *, timeout_sec: int
) -> TaskImageBuildResult:
    build_env = dict(run_env)
    # Use the classic builder for task images. It has been more reliable than buildx
    # on local OrbStack storage and still produces a locally-available image.
    build_env["DOCKER_BUILDKIT"] = "0"
    try:
        completed = subprocess.run(
            build_cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=build_env,
            check=False,
        )
        return TaskImageBuildResult(completed_process=completed)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return TaskImageBuildResult(
            completed_process=subprocess.CompletedProcess(
                build_cmd,
                returncode=124,
                stdout=stdout,
                stderr=stderr,
            ),
            timed_out=True,
            timeout_sec=timeout_sec,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Docker CLI not found.") from exc


def _task_image_build_timeout(task_timeout_sec: int) -> int:
    """Bound pre-Harbor image builds to the scenario budget."""
    return max(TASK_IMAGE_BUILD_MIN_TIMEOUT_SEC, task_timeout_sec)


def _run_runtime_preflight_command(
    *,
    image_name: str,
    run_env: dict[str, str],
    command: list[str],
    log_path: Path,
) -> None:
    docker_cmd = ["docker", "run", "--rm", image_name, *command]
    try:
        completed = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=60,
            env=run_env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Docker CLI not found.") from exc

    output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text((output + "\n") if output else "", encoding="utf-8")
    if completed.returncode == 0:
        return

    rendered = " ".join(shlex.quote(part) for part in docker_cmd)
    excerpt = output[:8000]
    if excerpt:
        raise RuntimeError(
            f"Harbor runtime preflight failed: `{rendered}` exited {completed.returncode}\n"
            f"{excerpt}"
        )
    raise RuntimeError(
        f"Harbor runtime preflight failed: `{rendered}` exited {completed.returncode}"
    )


def _ensure_harbor_runtime_preflight(
    *,
    image_ref: TaskImageRef,
    run_env: dict[str, str],
    log_dir: Path,
) -> None:
    _run_runtime_preflight_command(
        image_name=image_ref.image_name,
        run_env=run_env,
        command=["git", "--version"],
        log_path=log_dir / "runtime-git-preflight.log",
    )


def _cached_task_image_is_ready(
    *,
    image_ref: TaskImageRef,
    harness: str,
    run_env: dict[str, str],
    log_dir: Path,
) -> bool:
    if not _task_image_cache_hit(image_ref, harness=harness, run_env=run_env):
        return False
    try:
        _ensure_harbor_runtime_preflight(image_ref=image_ref, run_env=run_env, log_dir=log_dir)
    except RuntimeError:
        return False
    return True


def _write_task_image_build_log(log_dir: Path, build: TaskImageBuildResult) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    build_log = log_dir / "task-image-build.log"
    completed = build.completed_process
    build_log.write_text((completed.stdout or "") + "\n" + (completed.stderr or ""))


def _raise_task_image_build_error(build_cmd: list[str], build: TaskImageBuildResult) -> None:
    completed = build.completed_process
    if completed.returncode == 0:
        return
    output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()[:8000]
    rendered = " ".join(shlex.quote(part) for part in build_cmd)
    if build.timed_out:
        suffix = f"\n{output}" if output else ""
        raise RuntimeError(
            f"Task image build timed out after {build.timeout_sec}s: `{rendered}`{suffix}"
        )
    raise RuntimeError(
        f"Task image build failed: `{rendered}` exited {completed.returncode}\n{output}"
    )


def _write_task_image_cache_metadata(
    *, image_ref: TaskImageRef, harness: str, outcome: str
) -> None:
    metadata_path = _task_image_cache_metadata_path(image_ref.cache_key)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(
            {
                "cache_key": image_ref.cache_key,
                "image_name": image_ref.image_name,
                "image_tag": image_ref.tag,
                "harness": harness,
                "repo_id": _repo_cache_identity(),
                "outcome": outcome,
                "last_used_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _ensure_task_image(
    *,
    task_bundle_path: Path,
    image_ref: TaskImageRef,
    harness: str,
    run_env: dict[str, str],
    log_dir: Path,
    task_timeout_sec: int,
) -> bool:
    if _cached_task_image_is_ready(
        image_ref=image_ref,
        harness=harness,
        run_env=run_env,
        log_dir=log_dir,
    ):
        _write_task_image_cache_metadata(image_ref=image_ref, harness=harness, outcome="hit")
        return True

    with _cache_key_lock(f"image-{image_ref.cache_key}"):
        if _cached_task_image_is_ready(
            image_ref=image_ref,
            harness=harness,
            run_env=run_env,
            log_dir=log_dir,
        ):
            _write_task_image_cache_metadata(image_ref=image_ref, harness=harness, outcome="hit")
            return True

        context_dir = task_bundle_path / "environment"
        dockerfile = context_dir / "Dockerfile"
        if not dockerfile.exists():
            raise FileNotFoundError(f"Task image build failed: missing Dockerfile {dockerfile}")

        build_cmd = _task_image_build_command(
            image_ref,
            dockerfile,
            context_dir,
            harness=harness,
        )
        build = _run_task_image_build(
            build_cmd,
            run_env,
            timeout_sec=_task_image_build_timeout(task_timeout_sec),
        )
        _write_task_image_build_log(log_dir, build)
        _raise_task_image_build_error(build_cmd, build)
        _ensure_harbor_runtime_preflight(image_ref=image_ref, run_env=run_env, log_dir=log_dir)
        _write_task_image_cache_metadata(image_ref=image_ref, harness=harness, outcome="miss")
        return False


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
    if not request.scenario.visual:
        return
    for source_reference, relative_target in _visual_reference_assets(request):
        target_reference = app_dir / relative_target
        target_reference.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_reference, target_reference)


def _load_scenario_prompt(task: ScenarioDefinition, scenario_dir: Path) -> str:
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


def _render_task_toml(request: RunRequest, task_image: str | None) -> str:
    return f"""version = "1.0"

[metadata]
name = "{request.scenario.name}"
source = "starter-spec"

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
RUN apt-get update && apt-get install -y --no-install-recommends \\
  git \\
  && rm -rf /var/lib/apt/lists/*
"""
    cli_package = _harness_npm_package(request.config.harness.value)
    if cli_package:
        install_spec = _harness_npm_install_spec(request.config.harness.value)
        dockerfile += """RUN apt-get update && apt-get install -y --no-install-recommends \\
  npm \\
  && rm -rf /var/lib/apt/lists/*
"""
        dockerfile += f"RUN npm install -g {install_spec}\n"
    dockerfile += """COPY app/package.json app/bun.lock /app/
RUN bun install --frozen-lockfile
"""
    if request.scenario.visual:
        dockerfile += """RUN apt-get update && apt-get install -y --no-install-recommends \\
  file \\
  ripgrep \\
  libx11-6 \\
  libxext6 \\
  libxcb1 \\
  libglib2.0-0 \\
  libnspr4 \\
  libnss3 \\
  libdbus-1-3 \\
  libatk1.0-0 \\
  libatk-bridge2.0-0t64 \\
  libcairo2 \\
  libcups2t64 \\
  libexpat1 \\
  libatspi2.0-0 \\
  libpango-1.0-0 \\
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
    dockerfile += """COPY app/ /app/
"""
    return dockerfile


def _write_verifier_artifacts(
    request: RunRequest, context: WorkspaceContext, tests_dir: Path
) -> None:
    (tests_dir / "scenario-spec.json").write_text(
        json.dumps(_build_verifier_scenario_spec(request, context), indent=2)
    )
    scorer_path = tests_dir / "score-scenario.mjs"
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

if ! bun run "$SCRIPT_DIR/score-scenario.mjs" "$SCRIPT_DIR/scenario-spec.json"; then
  echo "0" > /logs/verifier/reward.txt
fi

tar \
  --exclude='./node_modules' \
  --exclude='./.next' \
  --exclude='./jobs' \
  -czf /logs/agent/final-app.tar.gz \
  -C /app .
"""
    )
    test_script.chmod(0o755)


def create_harbor_task_bundle(
    request: RunRequest,
    context: WorkspaceContext,
    bundle_root: Path,
) -> Path:
    """Build a Harbor-compatible bundle from the starter workspace."""
    bundle_dir, environment_dir, app_dir, tests_dir = _initialize_harbor_bundle_paths(bundle_root)
    _copy_workspace_into_bundle(request, context, app_dir)
    prompt_text = _load_scenario_prompt(request.scenario, request.scenario_dir)
    rules_filename = context.injected_rules.name if context.injected_rules else "AGENTS.md"
    (bundle_dir / "instruction.md").write_text(
        _bundle_instruction_text(
            prompt_text,
            rules_filename,
            include_rules_reference=request.config.harness != Harness.CODEX_CLI,
        )
    )

    dockerfile = _render_environment_dockerfile(request)
    _validate_public_base_images(dockerfile)
    dockerfile_path = environment_dir / "Dockerfile"
    dockerfile_path.write_text(dockerfile)
    _write_verifier_artifacts(request, context, tests_dir)
    image_ref = _task_image_reference(request, bundle_dir)
    (bundle_dir / "task.toml").write_text(
        _render_task_toml(request, image_ref.image_name if image_ref else None)
    )
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
    harness_dir = root_dir / "harness"
    harbor_dir = root_dir / "harbor"
    for path in (workspace_dir, verifier_dir, harness_dir, harbor_dir):
        path.mkdir(parents=True, exist_ok=True)
    return RunLayout(
        run_id=run_id,
        start_time=start_time,
        run_label=run_label,
        root_dir=root_dir,
        workspace_dir=workspace_dir,
        verifier_dir=verifier_dir,
        harness_dir=harness_dir,
        harbor_dir=harbor_dir,
        run_json_path=root_dir / "run.json",
        report_path=root_dir / "report.md",
    )


def prepare_run_context(request: RunRequest) -> WorkspaceContext:
    """Resolve starter source, workspace, and metadata."""
    from .starter import record_starter_metadata, resolve_starter_source

    starter_source = resolve_starter_source(
        request.scenario_dir,
        request.scenario.starter.root,
        scenario_name=request.scenario.name,
        scenario_revision=request.scenario.scenario_revision,
    )

    baseline_cache_key = _baseline_cache_key(request, starter_source.fingerprint)
    baseline_workspace_dir = _baseline_cache_workspace_dir(baseline_cache_key)
    baseline_cache = _ensure_baseline_workspace(
        scenario=request.scenario,
        starter_dir=starter_source.path,
        baseline_workspace_dir=baseline_workspace_dir,
        baseline_cache_key=baseline_cache_key,
        scenario_dir=request.scenario_dir,
        harness=request.config.harness,
    )

    workspace_dir = _repeat_workspace_dir(request)
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    shutil.copytree(
        baseline_workspace_dir,
        workspace_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("node_modules", ".next", "jobs"),
    )

    injected_rules: Path | None = None
    injected_rule_name = SYSTEM_RULES.get(request.config.harness)
    if injected_rule_name:
        candidate = workspace_dir / injected_rule_name
        if candidate.exists():
            injected_rules = candidate

    workspace = workspace_dir
    metadata_path = record_starter_metadata(workspace, starter_source)

    return WorkspaceContext(
        starter_source=starter_source,
        baseline_workspace=baseline_workspace_dir,
        baseline_cache_key=baseline_cache_key,
        baseline_cache_status=baseline_cache.status,
        baseline_cache_hit=baseline_cache.hit,
        baseline_metadata_path=baseline_cache.metadata_path,
        baseline_fingerprint=baseline_cache.baseline_fingerprint,
        workspace=workspace,
        injected_rules=injected_rules,
        metadata_path=metadata_path,
    )


def execute_harbor(request: HarborExecutionRequest) -> HarborExecutionResult:
    """Execute Harbor against a local scenario bundle."""
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
        trial_dir = _select_trial_dir(job_dir)
        if not _should_retry_harbor_rate_limit(
            attempt=attempt,
            execution_error=execution_error,
            run_harbor_dir=request.run_harbor_dir,
        ):
            return _terminated_harbor_result(
                job_dir=job_dir,
                reason=execution_error,
                trial_dir=trial_dir,
            )
        cleanup_stale_harbor_resources()
        time.sleep(HARBOR_RATE_LIMIT_RETRY_DELAY_SEC)

    if execution_error:
        return _terminated_harbor_result(
            job_dir=job_dir,
            reason=execution_error,
            trial_dir=_select_trial_dir(job_dir),
        )

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


def _should_retry_harbor_rate_limit(
    *, attempt: int, execution_error: str, run_harbor_dir: Path
) -> bool:
    return (
        attempt < HARBOR_RATE_LIMIT_MAX_ATTEMPTS
        and execution_error.startswith("Harbor exited with code")
        and _is_registry_rate_limited(run_harbor_dir)
    )


def _harbor_process_timeout(task_timeout_sec: int) -> int:
    """Allow Harbor build + verifier overhead beyond harness task timeout."""
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
        "harness_setup_sec": _phase_duration(payload, "agent_setup"),
        "harness_execution_sec": _phase_duration(payload, "agent_execution"),
        "verifier_sec": _phase_duration(payload, "verifier"),
    }
    return {key: value for key, value in timings.items() if value is not None}


def _verifier_scorecard_path(trial_dir: Path | None) -> Path | None:
    if not trial_dir:
        return None
    return trial_dir / "verifier" / "scorecard.json"


def _parse_gate_history(payload: dict[str, Any]) -> list[GateEvent]:
    gate_history_payload = payload.get("gate_history")
    if not isinstance(gate_history_payload, list):
        raise ValueError("scorecard.gate_history must be a list")
    return [GateEvent.model_validate(item) for item in gate_history_payload]


def _parse_module_results(payload: dict[str, Any]) -> list[MetricResult]:
    metric_results_payload = payload.get("metric_results")
    if not isinstance(metric_results_payload, list):
        raise ValueError("scorecard.metric_results must be a list")
    return [MetricResult.model_validate(item) for item in metric_results_payload]


def _parse_verifier_scorecard(payload: dict[str, Any]) -> EvaluationOutputs:
    gate_history = _parse_gate_history(payload)
    metric_results = _parse_module_results(payload)
    return EvaluationOutputs(
        functional=FunctionalScore.model_validate(payload.get("functional")),
        acceptance=AcceptanceScore.model_validate(payload.get("acceptance")),
        visual=(
            VisualScore.model_validate(payload.get("visual"))
            if payload.get("visual") is not None
            else None
        ),
        verification_stability=VerificationStabilityScore.model_validate(
            payload.get("verification_stability")
        ),
        test_coverage=CoverageScore.model_validate(payload.get("test_coverage")),
        requirements_coverage=RequirementsCoverageScore.model_validate(
            payload.get("requirements_coverage")
        ),
        execution_validity=ExecutionValidityScore.model_validate(payload.get("execution_validity")),
        performance_gates=PerformanceGatesScore.model_validate(payload.get("performance_gates")),
        metric_results=metric_results,
        gate_history=gate_history,
    )


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
        outputs = _parse_verifier_scorecard(payload)
    except (ValidationError, ValueError) as exc:
        return None, f"Invalid verifier scorecard content: {exc}"

    return outputs, None


def build_starter_meta(request: RunRequest, context: WorkspaceContext) -> dict:
    """Build starter metadata for the scorecard."""
    del request
    return {
        "scenario": context.starter_source.scenario_name,
        "scenario_revision": context.starter_source.scenario_revision,
        "root": str(context.starter_source.path),
        "baseline_workspace_dir": str(context.baseline_workspace),
        "baseline_cache_key": context.baseline_cache_key,
        "baseline_cache_status": context.baseline_cache_status,
        "baseline_metadata_path": str(context.baseline_metadata_path),
        "baseline_fingerprint": context.baseline_fingerprint,
        "run_workspace_dir": str(context.workspace),
        "fingerprint": context.starter_source.fingerprint,
        "metadata_file": context.metadata_path.name,
        "rules_file": context.injected_rules.name if context.injected_rules else None,
        "artifacts": {
            "metadata": str(context.metadata_path),
            "baseline_metadata": str(context.baseline_metadata_path),
            **({"rules": str(context.injected_rules)} if context.injected_rules else {}),
        },
    }


def _hash_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def build_scenario_revision_meta(
    request: RunRequest, context: WorkspaceContext
) -> dict[str, str | None]:
    """Build deterministic scenario/starter fingerprint metadata."""
    scenario_path = request.scenario_dir / "scenario.yaml"
    scenario_yaml_hash = _hash_bytes(scenario_path.read_bytes()) if scenario_path.exists() else None

    seed_payload = {
        "scoring_schema_version": SCORING_SCHEMA_VERSION,
        "scenario_yaml_hash": scenario_yaml_hash,
        "scenario_model": request.scenario.model_dump(mode="json", exclude_none=True),
        "scenario_name": request.scenario.name,
        "scenario_revision": request.scenario.scenario_revision,
        "starter_root": request.scenario.starter.root,
        "starter_fingerprint": context.starter_source.fingerprint,
    }
    seed = json.dumps(seed_payload, sort_keys=True, separators=(",", ":")).encode()
    return {
        "scoring_schema_version": SCORING_SCHEMA_VERSION,
        "scenario_yaml_hash": scenario_yaml_hash,
        "scenario_fingerprint": _hash_bytes(seed),
        "evaluation_profile": scenario_evaluation_profile(request.scenario),
        "metrics": scenario_metrics(request.scenario),
        "score_profile": _scenario_score_profile_block(request),
    }


def persist_verifier_artifacts(
    harbor_result: HarborExecutionResult, verifier_dir: Path
) -> dict[str, str]:
    """Persist verifier outputs for run and scenario audits."""
    if not harbor_result.trial_dir:
        return {}
    source_dir = harbor_result.trial_dir / "verifier"
    if not source_dir.exists():
        return {}

    copied: dict[str, str] = {}
    for filename in (
        "scorecard.json",
        "gate-history.json",
        "execution-validity.json",
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


def persist_canonical_verifier_artifacts(
    layout: RunLayout, scorecard: Scorecard, outputs: EvaluationOutputs
) -> None:
    """Rewrite canonical verifier artifacts from the synthesized canonical scorecard."""
    layout.verifier_dir.mkdir(parents=True, exist_ok=True)
    gate_history_payload = [event.model_dump(mode="json") for event in outputs.gate_history]
    scorecard_payload = scorecard.model_dump(mode="json")
    scorecard_payload["gate_history"] = gate_history_payload

    (layout.verifier_dir / "scorecard.json").write_text(
        json.dumps(scorecard_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    (layout.verifier_dir / "gate-history.json").write_text(
        json.dumps(gate_history_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    (layout.verifier_dir / "execution-validity.json").write_text(
        scorecard.execution_validity.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (layout.verifier_dir / "performance-gates.json").write_text(
        scorecard.performance_gates.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    reward_value = scorecard.quality_score if scorecard.execution_validity.passed else 0
    (layout.verifier_dir / "reward.txt").write_text(f"{reward_value}", encoding="utf-8")


def _copy_optional_visual_asset(source: Path, target: Path) -> str | None:
    if not source.exists():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    return str(shutil.copy2(source, target))


def _persist_visual_evidence_artifacts(
    *,
    request: RunRequest,
    workspace: Path,
    run_root_dir: Path,
) -> dict[str, Any]:
    """Persist visual evidence assets into the canonical run directory."""
    if request.scenario.visual is None:
        return {
            "actual": None,
            "reference": None,
            "diff": None,
            "regions": [],
        }

    visual_dir = run_root_dir / "visual"
    main_reference_name = Path(request.scenario.visual.reference_image).name
    main_artifacts = {
        "actual": _copy_optional_visual_asset(workspace / "actual.png", visual_dir / "actual.png"),
        "reference": None,
        "diff": _copy_optional_visual_asset(workspace / "diff.png", visual_dir / "diff.png"),
    }
    region_artifacts: list[dict[str, str | None]] = []

    for source_reference, relative_target in _visual_reference_assets(request):
        copied = _copy_optional_visual_asset(source_reference, visual_dir / relative_target.name)
        if relative_target.name == main_reference_name:
            main_artifacts["reference"] = copied

    reference_stem = Path(request.scenario.visual.reference_image).stem
    reference_suffix = Path(request.scenario.visual.reference_image).suffix
    for region_name in _visual_region_names(request):
        region_artifacts.append(
            {
                "name": region_name,
                "actual": _copy_optional_visual_asset(
                    workspace / f"actual-region-{region_name}.png",
                    visual_dir / f"actual-region-{region_name}.png",
                ),
                "reference": _copy_optional_visual_asset(
                    request.scenario_dir
                    / Path(request.scenario.visual.reference_image).parent
                    / f"{reference_stem}-region-{region_name}{reference_suffix}",
                    visual_dir / f"{reference_stem}-region-{region_name}{reference_suffix}",
                ),
                "diff": _copy_optional_visual_asset(
                    workspace / f"diff-region-{region_name}.png",
                    visual_dir / f"diff-region-{region_name}.png",
                ),
            }
        )

    return {
        "actual": main_artifacts["actual"],
        "reference": main_artifacts["reference"],
        "diff": main_artifacts["diff"],
        "regions": region_artifacts,
    }


def _rebind_visual_evidence_paths(
    scorecard_visual: VisualScore | None, evidence: dict[str, Any]
) -> None:
    """Replace transient /app visual paths with canonical run artifact paths."""
    if scorecard_visual is None:
        return

    scorecard_visual.actual_path = evidence.get("actual")
    scorecard_visual.reference_path = evidence.get("reference")
    scorecard_visual.diff_path = evidence.get("diff")

    regional_paths = {
        entry.get("name"): entry for entry in evidence.get("regions", []) if isinstance(entry, dict)
    }
    for region in scorecard_visual.regional_scores:
        if not isinstance(region, dict):
            continue
        region_paths = regional_paths.get(region.get("name"))
        if region_paths is None:
            continue
        region["actual_path"] = region_paths.get("actual")
        region["reference_path"] = region_paths.get("reference")
        region["diff_path"] = region_paths.get("diff")


def persist_harness_artifacts(
    harbor_result: HarborExecutionResult, harness_dir: Path
) -> dict[str, str]:
    """Persist Harbor harness transcripts and command history."""
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
            copied[filename] = str(shutil.copy2(src, harness_dir / filename))
    final_app = harness_dir / "final-app.tar.gz"
    if final_app.exists():
        copied["project.final.tar.gz"] = str(
            shutil.copy2(final_app, harness_dir / "project.final.tar.gz")
        )

    setup_dir = source / "setup"
    if setup_dir.exists():
        target = harness_dir / "setup"
        shutil.copytree(setup_dir, target, dirs_exist_ok=True)
        copied["setup"] = str(target)

    commands_dir = harness_dir / "commands"
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
    change_meta = workspace_meta.get("changes", {}) if isinstance(workspace_meta, dict) else {}
    lines = [
        "# Run Summary",
        "",
        f"- run_id: `{layout.run_id}`",
        f"- started_at_utc: `{layout.start_time.isoformat()}`",
        f"- scenario: `{request.scenario.name}`",
        f"- harness: `{request.config.harness.value}`",
        f"- model: `{request.config.model.qualified_name}`",
        f"- run_label: `{layout.run_label}`",
        f"- execution_valid: `{scorecard.execution_validity.passed}`",
        f"- performance_gates_passed: `{scorecard.performance_gates.passed}`",
        f"- unscored: `{scorecard.unscored}`",
        f"- unscored_reasons: `{scorecard.unscored_reasons}`",
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
        f"- harness_trajectory: `{layout.harness_dir / 'trajectory.json'}`",
    ]
    event_stream = _harness_event_stream_pointer(layout.harness_dir, request.config.harness.value)
    lines.append(f"- harness_event_stream: `{event_stream}`")
    lines.append(f"- homepage_post_screenshot: `{evidence_meta.get('homepage_post')}`")
    lines.append(f"- final_workspace_archive: `{evidence_meta.get('final_workspace_archive')}`")
    lines.append(f"- evidence_errors: `{evidence_meta.get('errors')}`")
    lines.append(f"- workspace_pruned_dirs: `{prune_meta.get('removed')}`")
    lines.append(f"- workspace_pruned_bytes: `{prune_meta.get('reclaimed_bytes')}`")
    lines.append(f"- workspace_changed_file_count: `{change_meta.get('changed_file_count')}`")
    lines.append(f"- workspace_changed_files: `{change_meta.get('changed_files')}`")
    lines.append(f"- workspace_diff_artifact: `{change_meta.get('artifact')}`")
    lines.append(f"- workspace_diff_error: `{change_meta.get('error')}`")
    layout.report_path.write_text("\n".join(lines) + "\n")


def _harness_event_stream_pointer(harness_dir: Path, harness: str) -> Path:
    if harness == "codex-cli":
        return harness_dir / "codex.txt"
    if harness == "claude-code":
        return harness_dir / "commands"
    if harness == "gemini":
        return harness_dir / "commands"
    if harness == "cursor":
        return harness_dir / "commands"
    if harness == "copilot":
        return harness_dir / "commands"
    if harness == "pi":
        return harness_dir / "commands"
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


def _strip_shell_env_prefix(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
    env_assignments: dict[str, str] = {}
    idx = 0
    if idx < len(tokens) and tokens[idx] == "env":
        idx += 1
    while idx < len(tokens):
        token = tokens[idx]
        if "=" not in token or token.startswith("-"):
            break
        key, value = token.split("=", 1)
        if not key or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            break
        env_assignments[key] = value
        idx += 1
    return env_assignments, tokens[idx:]


def _git_command_tokens(command: str) -> tuple[dict[str, str], list[str]]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        lowered = command.lower().strip()
        if "git" not in lowered:
            return {}, []
        return {}, lowered.split()
    env_assignments, tokens = _strip_shell_env_prefix(tokens)
    if not tokens or tokens[0] != "git":
        return env_assignments, []
    idx = _git_subcommand_index(tokens)
    return env_assignments, tokens[idx:]


def _git_option_consumes_value(token: str) -> bool:
    return token in {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix"}


def _git_subcommand_index(tokens: list[str]) -> int:
    idx = 1
    while idx < len(tokens):
        token = tokens[idx]
        if _git_option_consumes_value(token):
            idx += 2
            continue
        if token.startswith("-c") and token != "-c":
            idx += 1
            continue
        if token == "--":
            return idx + 1
        if token.startswith("-"):
            idx += 1
            continue
        break
    return idx


def _is_git_commit_command(command: str) -> bool:
    _env_assignments, tokens = _git_command_tokens(command)
    return bool(tokens) and tokens[0] == "commit"


def _git_commit_uses_verification_bypass(command: str) -> bool:
    env_assignments, tokens = _git_command_tokens(command)
    if not tokens or tokens[0] != "commit":
        return False

    bypass_env_values = {
        "HUSKY": {"0"},
        "HUSKY_SKIP_HOOKS": {"1", "true", "yes"},
        "NO_VERIFY": {"1", "true", "yes"},
    }
    for key, truthy_values in bypass_env_values.items():
        value = env_assignments.get(key)
        if value is None:
            continue
        if value.lower() in truthy_values:
            return True

    lowered = command.lower()
    if "core.hookspath=/dev/null" in lowered:
        return True

    return "--no-verify" in tokens or "-n" in tokens


def _should_record_command(command: str, *, include_git_commit: bool) -> bool:
    if _looks_like_shell_command(command):
        return True
    return include_git_commit and _is_git_commit_command(command)


def _is_shell_separator(token: str) -> bool:
    return token in {"&&", "||", ";"}


def _normalized_joined_command(tokens: list[str]) -> str | None:
    if not tokens:
        return None
    return _normalize_verification_alias(shlex.join(tokens).strip())


def _split_token_by_shell_separators(token: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    idx = 0
    while idx < len(token):
        pair = token[idx : idx + 2]
        if pair in {"&&", "||"}:
            if current:
                parts.append("".join(current))
                current = []
            parts.append(pair)
            idx += 2
            continue
        if token[idx] == ";":
            if current:
                parts.append("".join(current))
                current = []
            parts.append(";")
            idx += 1
            continue
        current.append(token[idx])
        idx += 1
    if current:
        parts.append("".join(current))
    return [part for part in parts if part]


def _expand_shell_separator_tokens(tokens: list[str]) -> list[str]:
    expanded: list[str] = []
    for token in tokens:
        expanded.extend(_split_token_by_shell_separators(token))
    return expanded


def _split_normalized_subcommands(tokens: list[str]) -> list[str]:
    expanded_tokens = _expand_shell_separator_tokens(tokens)
    subcommands: list[str] = []
    current: list[str] = []
    for token in expanded_tokens:
        if _is_shell_separator(token):
            normalized = _normalized_joined_command(current)
            if normalized:
                subcommands.append(normalized)
            current = []
            continue
        current.append(token)

    normalized = _normalized_joined_command(current)
    if normalized:
        subcommands.append(normalized)
    return subcommands


_HEREDOC_PATTERN = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")


def _shell_command_segments(command_text: str) -> list[str]:
    segments: list[str] = []
    lines = command_text.splitlines()
    idx = 0

    while idx < len(lines):
        line = lines[idx].strip()
        idx += 1
        if not line:
            continue

        heredoc_match = _HEREDOC_PATTERN.search(line)
        if not heredoc_match:
            segments.append(line)
            continue

        terminator = heredoc_match.group(1)
        heredoc_lines = [line]
        while idx < len(lines):
            heredoc_line = lines[idx]
            heredoc_lines.append(heredoc_line)
            idx += 1
            if heredoc_line.strip() == terminator:
                break
        segments.append("\n".join(heredoc_lines).strip())

    return segments


def _normalized_shell_subcommands(command: str) -> list[str]:
    command_text = _unwrap_shell_wrapper(command)
    if not command_text:
        return []
    subcommands: list[str] = []
    for segment in _shell_command_segments(command_text):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            subcommands.append(_normalize_verification_alias(segment))
            continue
        if not tokens:
            continue
        subcommands.extend(_split_normalized_subcommands(tokens))
    return subcommands


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


def _verification_command_strings(task: ScenarioDefinition) -> list[str]:
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


def _command_records(
    entries: list[dict], *, include_git_commit: bool = False
) -> list[CommandRecord]:
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
            if not _should_record_command(command, include_git_commit=include_git_commit):
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


def _command_records_for_harness(
    trial_dir: Path, harness: str, *, include_git_commit: bool = False
) -> list[CommandRecord]:
    if harness == "codex-cli":
        return _command_records(
            _read_jsonl_dicts(trial_dir / "agent" / "codex.txt"),
            include_git_commit=include_git_commit,
        )
    if harness == "claude-code":
        return _command_records_from_claude_stdout(trial_dir, include_git_commit=include_git_commit)
    if harness == "gemini":
        stdout_records = _command_records_from_harness_stdout(
            trial_dir,
            additional_stdout_files=("gemini-cli.txt",),
            include_git_commit=include_git_commit,
        )
        if stdout_records:
            return stdout_records
        return _command_records_from_gemini_trajectory(
            trial_dir, include_git_commit=include_git_commit
        )
    if harness == "cursor":
        return _command_records_from_harness_stdout(
            trial_dir, include_git_commit=include_git_commit
        )
    if harness == "copilot":
        return _command_records_from_harness_stdout(
            trial_dir, include_git_commit=include_git_commit
        )
    if harness == "pi":
        return _command_records_from_harness_stdout(
            trial_dir, include_git_commit=include_git_commit
        )
    raise ValueError(f"Unsupported harness for command extraction: {harness}")


def _harness_emits_structured_trace_events(harness: str) -> bool:
    if harness == "codex-cli":
        return True
    if harness in {"claude-code", "gemini", "cursor", "copilot", "pi"}:
        return False
    raise ValueError(f"Unsupported harness for trace event extraction: {harness}")


def _command_records_from_harness_stdout(
    trial_dir: Path,
    *,
    additional_stdout_files: tuple[str, ...] = (),
    include_git_commit: bool = False,
) -> list[CommandRecord]:
    harness_dir = trial_dir / "agent"
    if not harness_dir.exists():
        return []
    records: list[CommandRecord] = []
    stdout_paths: list[Path] = sorted(harness_dir.glob("command-*/stdout.txt"))
    stdout_paths.extend(harness_dir / name for name in additional_stdout_files)
    for stdout_path in stdout_paths:
        if not stdout_path.exists():
            continue
        records.extend(
            _command_records_from_stdout(stdout_path, include_git_commit=include_git_commit)
        )
    return records


def _command_records_from_claude_stdout(
    trial_dir: Path, *, include_git_commit: bool = False
) -> list[CommandRecord]:
    agent_dir = trial_dir / "agent"
    if not agent_dir.exists():
        return []
    records: list[CommandRecord] = []
    stdout_paths: list[Path] = sorted(agent_dir.glob("command-*/stdout.txt"))
    stdout_paths.append(agent_dir / "claude-code.txt")
    for stdout_path in stdout_paths:
        if not stdout_path.exists():
            continue
        records.extend(
            _command_records_from_claude_stdout_file(
                stdout_path, include_git_commit=include_git_commit
            )
        )
    return records


def _command_records_from_claude_stdout_file(
    stdout_path: Path, *, include_git_commit: bool = False
) -> list[CommandRecord]:
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
            records.extend(
                _command_records_from_line(stripped, include_git_commit=include_git_commit)
            )
            continue
        _append_claude_tool_use_records(
            payload=payload,
            output=stripped,
            records=records,
            record_idx_by_tool_use_id=record_idx_by_tool_use_id,
            include_git_commit=include_git_commit,
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
    include_git_commit: bool = False,
) -> None:
    for tool_use_id, command in _claude_bash_tool_use_commands(payload):
        matched_indexes: list[int] = []
        for normalized in _normalized_shell_subcommands(command):
            if not _should_record_command(normalized, include_git_commit=include_git_commit):
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


def _command_records_from_stdout(
    stdout_path: Path, *, include_git_commit: bool = False
) -> list[CommandRecord]:
    try:
        lines = stdout_path.read_text(errors="ignore").splitlines()
    except OSError:
        return []
    records: list[CommandRecord] = []
    for line in lines:
        records.extend(_command_records_from_line(line, include_git_commit=include_git_commit))
    return records


def _command_records_from_line(
    line: str, *, include_git_commit: bool = False
) -> list[CommandRecord]:
    stripped = line.strip()
    if not stripped:
        return []
    if stripped.startswith("$ "):
        return _prompt_command_record(
            stripped[2:],
            output=stripped,
            include_git_commit=include_git_commit,
        )
    if _line_is_command_intent(stripped):
        return []
    if not _line_reports_command_execution(stripped):
        return []
    quoted_records = _quoted_command_records(stripped, include_git_commit=include_git_commit)
    if quoted_records:
        return quoted_records
    return _keyword_command_records(stripped, include_git_commit=include_git_commit)


def _prompt_command_record(
    command_text: str, *, output: str, include_git_commit: bool = False
) -> list[CommandRecord]:
    commands = _normalized_shell_subcommands(command_text)
    return [
        CommandRecord(command=command, failed=False, output=output)
        for command in commands
        if _should_record_command(command, include_git_commit=include_git_commit)
    ]


def _quoted_command_records(line: str, *, include_git_commit: bool = False) -> list[CommandRecord]:
    commands: list[str] = []
    for match in BACKTICK_COMMAND_PATTERN.findall(line):
        commands.extend(_normalized_shell_subcommands(match))
    commands = [
        command
        for command in commands
        if _should_record_command(command, include_git_commit=include_git_commit)
    ]
    if not commands:
        return []
    failed = _line_reports_command_failure(line)
    return [CommandRecord(command=command, failed=failed, output=line) for command in commands]


def _command_records_from_gemini_trajectory(
    trial_dir: Path, *, include_git_commit: bool = False
) -> list[CommandRecord]:
    payload = _load_json_dict(trial_dir / "agent" / "gemini-cli.trajectory.json")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return []
    records: list[CommandRecord] = []
    for message in messages:
        records.extend(
            _command_records_from_gemini_message(message, include_git_commit=include_git_commit)
        )
    return records


def _command_records_from_gemini_message(
    message: dict, *, include_git_commit: bool = False
) -> list[CommandRecord]:
    if not isinstance(message, dict):
        return []
    tool_calls = message.get("toolCalls")
    if not isinstance(tool_calls, list):
        return []
    records: list[CommandRecord] = []
    for tool_call in tool_calls:
        records.extend(
            _command_records_from_gemini_tool_call(tool_call, include_git_commit=include_git_commit)
        )
    return records


def _command_records_from_gemini_tool_call(
    tool_call: dict, *, include_git_commit: bool = False
) -> list[CommandRecord]:
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
        if _should_record_command(command, include_git_commit=include_git_commit)
    ]


def _keyword_command_records(line: str, *, include_git_commit: bool = False) -> list[CommandRecord]:
    lowered = f" {line.lower()} "
    commands: list[str] = []
    for command, keywords in KEYWORD_COMMAND_PATTERNS:
        if any(keyword in lowered for keyword in keywords):
            commands.append(command)
    if include_git_commit and " git " in lowered and " commit " in lowered:
        commands.append("git commit")
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


def _observed_verification_attempts(
    gate_history: list[GateEvent], verification_patterns: list[str]
) -> dict[str, int]:
    attempts_by_pattern: dict[str, int] = {pattern: 0 for pattern in verification_patterns}
    for event in gate_history:
        matched = _command_matches_pattern(event.command, verification_patterns)
        if not matched:
            continue
        attempts_by_pattern[matched] += 1
    return attempts_by_pattern


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


def _git_commit_bypass_commands(records: list[CommandRecord]) -> list[str]:
    commands: list[str] = []
    for record in records:
        if not _is_git_commit_command(record.command):
            continue
        if not _git_commit_uses_verification_bypass(record.command):
            continue
        commands.append(record.command)
    return list(dict.fromkeys(commands))


def _first_pass_counts(first_pass_status: dict[str, str]) -> tuple[int, int, int]:
    passed = sum(1 for status in first_pass_status.values() if status == "pass")
    failed = sum(1 for status in first_pass_status.values() if status == "fail")
    missing = sum(1 for status in first_pass_status.values() if status == "missing")
    return passed, failed, missing


def collect_process_metrics(
    scenario: ScenarioDefinition,
    trial_dir: Path | None,
    *,
    harness: str,
) -> ProcessMetrics:
    """Collect resource-efficiency metrics from harness execution logs."""
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
    git_commit_records = _command_records_for_harness(trial_dir, harness, include_git_commit=True)
    verification_patterns = _verification_command_strings(scenario)
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
        git_commit_verification_bypass_commands=_git_commit_bypass_commands(git_commit_records),
    )


def _events_from_command(timestamp: str, item: dict) -> list[TraceEvent]:
    command = _normalize_command(str(item.get("command", "")))
    return [
        TraceEvent(
            timestamp=timestamp,
            event_type="bash_command",
            data={"command": command},
        ),
        TraceEvent(
            timestamp=timestamp,
            event_type="gate_result",
            data={
                "status": item.get("status"),
                "exit_code": int(item.get("exit_code", 0) or 0),
            },
        ),
    ]


def _events_from_file_changes(timestamp: str, item: dict) -> list[TraceEvent]:
    file_events: list[TraceEvent] = []
    for change in item.get("changes", []) or []:
        path = change.get("path")
        if not path:
            continue
        file_events.append(
            TraceEvent(
                timestamp=timestamp,
                event_type="file_change",
                data={"file_path": str(path)},
            )
        )
    return file_events


def _events_from_item(timestamp: str, item: dict) -> list[TraceEvent]:
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
        TraceEvent(
            timestamp=timestamp,
            event_type="assistant_message",
            data={"content": str(text)},
        )
    ]


def collect_trace_events(
    trial_dir: Path | None,
    *,
    harness: str,
) -> list[TraceEvent]:
    """Project harness logs into normalized trace events."""
    if not trial_dir:
        return []
    if not _harness_emits_structured_trace_events(harness):
        return []

    events: list[TraceEvent] = []
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
    """Evaluate whether measured test coverage meets the configured threshold."""
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


def _test_evidence_label(evidence: dict[str, Any]) -> str:
    evidence_type = str(evidence.get("type", "unknown"))
    if evidence_type == "query_role":
        role = str(evidence.get("role", "unknown"))
        min_count = int(evidence.get("min_count", 1) or 1)
        parts = [role]
        if evidence.get("level") is not None:
            parts.append(f"level={evidence['level']}")
        if evidence.get("name"):
            parts.append(f"name={evidence['name']}")
        return f"query_role:{','.join(parts)} x{min_count}"
    if evidence_type == "query_text":
        pattern = str(evidence.get("pattern", "unknown"))
        min_count = int(evidence.get("min_count", 1) or 1)
        return f"query_text:{pattern} x{min_count}"
    return evidence_type


def _count_role_query_matches(test_sources: list[str], evidence: dict[str, Any]) -> int:
    role = re.escape(str(evidence.get("role", "")))
    if not role:
        return 0
    query_pattern = re.compile(
        r"(?:screen\.)?(?:get|find|query)(?:All)?ByRole\s*\(\s*(['\"])"
        + role
        + r"\1(?P<options>\s*,\s*\{[\s\S]*?\})?",
        re.MULTILINE,
    )
    level = evidence.get("level")
    name = evidence.get("name")
    count = 0
    for source in test_sources:
        for match in query_pattern.finditer(source):
            options = match.group("options") or ""
            if level is not None and not re.search(rf"level\s*:\s*{int(level)}\b", options):
                continue
            if name is not None and not re.search(re.escape(str(name)), options, re.IGNORECASE):
                continue
            count += 1
    return count


def _count_text_query_matches(test_sources: list[str], evidence: dict[str, Any]) -> int:
    pattern = str(evidence.get("pattern", ""))
    if not pattern:
        return 0
    count = 0
    query_pattern = re.compile(r"(?:screen\.)?(?:get|find|query)(?:All)?ByText\s*\(", re.MULTILINE)
    for source in test_sources:
        if not query_pattern.search(source):
            continue
        count += len(re.findall(pattern, source, re.MULTILINE | re.IGNORECASE))
    return count


def _missing_test_evidence(
    test_sources: list[str],
    required_test_evidence: list[Any],
) -> list[str]:
    missing: list[str] = []
    for evidence in required_test_evidence:
        payload = evidence.model_dump(mode="json") if hasattr(evidence, "model_dump") else evidence
        evidence_type = payload.get("type")
        min_count = int(payload.get("min_count", 1) or 1)
        if evidence_type == "query_role":
            matched = _count_role_query_matches(test_sources, payload)
        elif evidence_type == "query_text":
            matched = _count_text_query_matches(test_sources, payload)
        else:
            matched = 0
        if matched < min_count:
            missing.append(_test_evidence_label(payload))
    return missing


def evaluate_requirements(
    workspace: Path,
    requirements: list[RequirementSpec],
) -> RequirementsCoverageScore:
    """Evaluate requirement implementation and optional requirement-to-test mapping."""
    if not requirements:
        return RequirementsCoverageScore()

    test_sources = [path.read_text(errors="ignore") for path in _test_file_paths(workspace)]
    missing_ids: list[str] = []
    gap_ids: list[str] = []
    evidence_gaps: dict[str, list[str]] = {}
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

        mapped_for_requirement = not missing_patterns
        mapped, mapped_satisfied = _apply_requirement_mapping_counts(
            mapped=mapped,
            mapped_satisfied=mapped_satisfied,
            mapped_for_requirement=mapped_for_requirement,
            requirement_passed=requirement_check.passed,
        )
        if missing_patterns:
            gap_ids.append(requirement.id)
            evidence_gaps[requirement.id] = missing_patterns

    return RequirementsCoverageScore(
        total_requirements=len(requirements),
        satisfied_requirements=satisfied,
        mapped_requirements=mapped,
        mapped_satisfied_requirements=mapped_satisfied,
        missing_requirement_ids=missing_ids,
        requirement_gap_ids=gap_ids,
        requirement_test_evidence_gaps=evidence_gaps,
    )


def _requirement_status(
    workspace: Path,
    requirement: RequirementSpec,
    test_sources: list[str],
) -> tuple[AcceptanceCheck, list[str]]:
    requirement_check = run_deterministic_check(requirement.check, workspace)
    missing_evidence = _missing_test_evidence(test_sources, requirement.required_test_evidence)
    return requirement_check, missing_evidence


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
        acceptance=AcceptanceScore(
            checks=[
                AcceptanceCheck(
                    rule="Evaluation run completed",
                    type="deterministic",
                    passed=False,
                    evidence=failure_reason,
                )
            ]
        ),
        visual=None,
        verification_stability=VerificationStabilityScore(
            total_gate_failures=settings.verification_stability.max_gate_failures,
            unique_failure_categories=0,
            repeat_failures=0,
        ),
        test_coverage=CoverageScore(
            threshold=None,
            measured=None,
            source=None,
            passed=False,
        ),
        requirements_coverage=RequirementsCoverageScore(
            total_requirements=0,
            satisfied_requirements=0,
            mapped_requirements=0,
            missing_requirement_ids=[],
            requirement_gap_ids=[],
        ),
        execution_validity=ExecutionValidityScore(
            checks=[
                GateCheck(
                    name="run_completed",
                    passed=False,
                    evidence=failure_reason,
                )
            ]
        ),
        performance_gates=PerformanceGatesScore(checks=[]),
        metric_results=[],
        gate_history=[],
    )


def _all_gates_passed(outputs: EvaluationOutputs) -> bool:
    return outputs.functional.gates_total == outputs.functional.gates_passed


def _completion_claim_consistent(
    events: list[TraceEvent],
    gates_passed: bool,
    *,
    atomic_commits_required: bool,
    atomic_commits_present: bool,
) -> GateCheck:
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
            evidence="Harness run claimed completion before all quality gates were passing.",
        )
    if completion_claimed and atomic_commits_required and not atomic_commits_present:
        return GateCheck(
            name="completion_claim_integrity",
            passed=False,
            evidence="Harness run claimed completion without making the required atomic commit.",
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


def _git_commit_count(workspace_path: Path) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return 0, "git not available in run environment."
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "No git commits found.").strip()
        return 0, message
    try:
        count = int((result.stdout or "0").strip() or "0")
    except ValueError:
        return 0, f"Unable to parse git commit count: {(result.stdout or '').strip()}"
    return count, f"commit_count={count}"


def build_execution_validity_score(
    *,
    outputs: EvaluationOutputs,
    terminated_early: bool,
    termination_reason: str | None,
    process_metrics: ProcessMetrics,
    events: list[TraceEvent],
    workspace_path: Path,
    atomic_commits_required: bool,
    verification_patterns: list[str],
) -> ExecutionValidityScore:
    """Build execution-validity checks for the run."""
    checks = [check.model_copy(deep=True) for check in outputs.execution_validity.checks]
    _upsert_gate_check(
        checks,
        GateCheck(
            name="run_completed",
            passed=not terminated_early,
            evidence=termination_reason or "Run completed without early termination.",
        ),
    )

    configured_required_count = len(verification_patterns)
    explicit_required_executed = process_metrics.executed_required_verification_commands
    observed_attempts = _observed_verification_attempts(outputs.gate_history, verification_patterns)
    observed_required_executed = _count_executed_required(observed_attempts)
    required_count = configured_required_count
    if not outputs.gate_history and process_metrics.required_verification_commands > 0:
        required_count = process_metrics.required_verification_commands
    if required_count == 0:
        required_commands_passed = True
        required_commands_evidence = "required=0"
    elif outputs.gate_history:
        required_commands_passed = observed_required_executed == required_count
        required_commands_evidence = (
            f"observed={observed_required_executed}/{required_count}, "
            f"explicit={explicit_required_executed}/{required_count}"
        )
    else:
        required_commands_passed = explicit_required_executed == required_count
        required_commands_evidence = (
            f"explicit={explicit_required_executed}/{required_count} (gate history unavailable)"
        )
    _upsert_gate_check(
        checks,
        GateCheck(
            name="required_verification_commands_executed",
            passed=required_commands_passed,
            evidence=required_commands_evidence,
        ),
    )

    bypass_commands = process_metrics.git_commit_verification_bypass_commands
    _upsert_gate_check(
        checks,
        GateCheck(
            name="commit_verification_hooks_not_bypassed",
            passed=not bypass_commands,
            evidence=(
                "No git commit verification bypass detected."
                if not bypass_commands
                else f"bypass_commands={bypass_commands}"
            ),
        ),
    )

    commit_count, commit_evidence = _git_commit_count(workspace_path)
    atomic_commits_present = commit_count > 0
    if atomic_commits_required:
        _upsert_gate_check(
            checks,
            GateCheck(
                name="atomic_commits_present",
                passed=atomic_commits_present,
                evidence=commit_evidence,
            ),
        )

    completion_check = _completion_claim_consistent(
        events,
        _all_gates_passed(outputs),
        atomic_commits_required=atomic_commits_required,
        atomic_commits_present=atomic_commits_present,
    )
    _upsert_gate_check(checks, completion_check)
    return ExecutionValidityScore(checks=checks)


def build_performance_gates_score(*, outputs: EvaluationOutputs) -> PerformanceGatesScore:
    """Build performance-gate checks for scored scenario outcomes."""
    checks = [check.model_copy(deep=True) for check in outputs.performance_gates.checks]
    return PerformanceGatesScore(checks=checks)


def build_resource_efficiency_score(metrics: ProcessMetrics) -> ResourceEfficiencyScore:
    """Build resource-efficiency score model from process metrics."""
    return ResourceEfficiencyScore(
        uncached_input_tokens=metrics.uncached_input_tokens,
        output_tokens=metrics.output_tokens,
        command_count=metrics.command_count,
        failed_command_count=metrics.failed_command_count,
        verification_rounds=metrics.verification_rounds,
        repeated_verification_failures=metrics.repeated_verification_failures,
    )


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def _classify_unscored_reasons(terminated_early: bool, termination_reason: str | None) -> list[str]:
    """Classify harness/provider issues that unscore a run and require a rerun."""
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


def _scorecard_run_metadata(
    layout: RunLayout, *, unscored: bool, unscored_reasons: list[str]
) -> dict[str, Any]:
    return {
        "run_label": layout.run_label,
        "canonical_run_dir": str(layout.root_dir),
        "run_json_path": str(layout.run_json_path),
        "run_report_path": str(layout.report_path),
        "rerun_required": unscored,
        "unscored_reasons": unscored_reasons,
    }


def _scorecard_harbor_metadata(
    execution: ExecutionPhaseResult, artifacts: PersistedArtifacts
) -> dict[str, Any]:
    harbor_timings = _harbor_phase_timings(execution.harbor_result.trial_dir)
    trial_total_sec = harbor_timings.get("trial_total_sec")
    orchestration_overhead_excluding_test_sec = (
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
        "prep_phase_timings_sec": execution.prep_phase_timings_sec,
        "prep_total_sec": execution.prep_total_sec,
        "phase_timings_sec": harbor_timings,
        "harness_overhead_sec": orchestration_overhead_excluding_test_sec,
        "orchestration_overhead_excluding_test_sec": orchestration_overhead_excluding_test_sec,
        "cache": execution.cache_metadata,
        "auth": execution.auth_metadata,
        "artifacts": artifacts.harbor_artifacts,
    }


def _scorecard_verifier_metadata(
    execution: ExecutionPhaseResult, artifacts: PersistedArtifacts
) -> dict[str, Any]:
    verifier_scorecard_path = _verifier_scorecard_path(execution.harbor_result.trial_dir)
    verifier_payload = _load_json_dict(verifier_scorecard_path) if verifier_scorecard_path else {}
    verifier_metadata = verifier_payload.get("metadata")
    command_timings = (
        verifier_metadata.get("command_timings_sec")
        if isinstance(verifier_metadata, dict)
        else None
    )
    return {
        "scorecard": str(verifier_scorecard_path) if verifier_scorecard_path else None,
        "artifacts": artifacts.verifier_artifacts,
        "command_timings_sec": command_timings,
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
        "git_commit_verification_bypass_commands": (
            process_metrics.git_commit_verification_bypass_commands
        ),
    }


def _scorecard_metadata(
    *,
    layout: RunLayout,
    execution: ExecutionPhaseResult,
    artifacts: PersistedArtifacts,
    unscored: bool,
    unscored_reasons: list[str],
) -> dict[str, Any]:
    return {
        "run": _scorecard_run_metadata(
            layout,
            unscored=unscored,
            unscored_reasons=unscored_reasons,
        ),
        "starter": artifacts.starter_meta,
        "scenario": artifacts.scenario_revision_meta,
        "harbor": _scorecard_harbor_metadata(execution, artifacts),
        "harness": {"artifacts": artifacts.harness_artifacts},
        "verifier": _scorecard_verifier_metadata(execution, artifacts),
        "process": _scorecard_process_metadata(execution.process_metrics),
        "evidence": artifacts.evidence_artifacts,
        "workspace": {
            "prune": artifacts.workspace_prune,
            "changes": artifacts.workspace_changes,
        },
    }


def build_scorecard(context: ScorecardBuildContext) -> Scorecard:
    """Create scorecard with populated metrics and metadata."""

    request = context.request
    layout = context.layout
    artifacts = context.artifacts
    execution = context.execution
    outputs = execution.outputs

    execution_validity = build_execution_validity_score(
        outputs=outputs,
        terminated_early=execution.terminated_early,
        termination_reason=execution.termination_reason,
        process_metrics=execution.process_metrics,
        events=execution.events,
        workspace_path=context.context.workspace,
        atomic_commits_required=request.scenario.verification.workflow.atomic_commits_required,
        verification_patterns=_verification_command_strings(request.scenario),
    )
    performance_gates = build_performance_gates_score(outputs=outputs)
    resource_efficiency = build_resource_efficiency_score(execution.process_metrics)
    unscored_reasons = _classify_unscored_reasons(
        execution.terminated_early,
        execution.termination_reason,
    )
    unscored = len(unscored_reasons) > 0
    metadata = _scorecard_metadata(
        layout=layout,
        execution=execution,
        artifacts=artifacts,
        unscored=unscored,
        unscored_reasons=unscored_reasons,
    )

    return Scorecard(
        run_id=layout.run_id,
        scenario_name=request.scenario.name,
        scenario_revision=request.scenario.scenario_revision,
        harness=request.config.harness.value,
        model=request.config.model.qualified_name,
        starter_root=request.scenario.starter.root,
        duration_sec=execution.duration_sec,
        terminated_early=execution.terminated_early,
        termination_reason=execution.termination_reason,
        unscored=unscored,
        unscored_reasons=unscored_reasons,
        score_profile=_scenario_score_profile_block(request),
        functional=outputs.functional,
        acceptance=outputs.acceptance,
        visual=outputs.visual,
        verification_stability=outputs.verification_stability,
        test_coverage=outputs.test_coverage,
        requirements_coverage=outputs.requirements_coverage,
        execution_validity=execution_validity,
        performance_gates=performance_gates,
        resource_efficiency=resource_efficiency,
        metric_results=outputs.metric_results,
        metadata=metadata,
    )


def _prepare_workspace_phase(request: RunRequest) -> WorkspacePreparationPhaseResult:
    from .runner_pipeline import prepare_workspace_phase

    return prepare_workspace_phase(request)


def _execute_harbor_phase(
    request: RunRequest, phase: WorkspacePreparationPhaseResult
) -> ExecutionPhaseResult:
    from .runner_pipeline import execute_harbor_phase

    return execute_harbor_phase(request, phase)


def _persist_artifacts_phase(
    request: RunRequest,
    phase: WorkspacePreparationPhaseResult,
    execution: ExecutionPhaseResult,
) -> PersistedArtifacts:
    from .runner_pipeline import persist_artifacts_phase

    return persist_artifacts_phase(request, phase, execution)


def _synthesize_scorecard_phase(
    request: RunRequest,
    phase: WorkspacePreparationPhaseResult,
    execution: ExecutionPhaseResult,
    artifacts: PersistedArtifacts,
) -> Scorecard:
    from .runner_pipeline import synthesize_scorecard_phase

    return synthesize_scorecard_phase(request, phase, execution, artifacts)


def run_task(request: RunRequest) -> EvalRun:
    from .runner_pipeline import run_task as run_task_pipeline

    return run_task_pipeline(request)
