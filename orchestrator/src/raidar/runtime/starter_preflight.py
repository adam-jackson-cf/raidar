"""Starter setup and preflight cache services."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from raidar.config import settings
from raidar.runtime.environments import ResolvedEnvironment, resolve_scenario_environment
from raidar.runtime.models import RunRequest, WorkspaceContext
from raidar.runtime.profile import default_runtime_profile
from raidar.runtime.workspace_cache import (
    RAIDAR_CACHE_VERSION,
    _cache_key_lock,
    _hash_json_payload,
    _preflight_cache_file,
    _touch_cache_path,
)


@dataclass(frozen=True, slots=True)
class StarterPreflightCacheWrite:
    """Cache payload for starter preflight validation."""

    cache_file: Path
    harness: str
    starter_fingerprint: str
    baseline_cache_key: str
    setup_actions: list[list[str]]
    required_commands: list[list[str]]


class StarterPreflightError(RuntimeError):
    """Fatal starter setup error that unscored and aborts an entire experiment."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _resolved_environment(request: RunRequest) -> ResolvedEnvironment:
    return resolve_scenario_environment(
        scenario=request.scenario,
        scenario_path=request.scenario_dir / "scenario.yaml",
        repo_root=_repo_root(),
    )


def _preflight_command_timeout(request: RunRequest) -> int:
    return (
        request.scenario.verification.preflight_command_timeout_sec
        or settings.timeouts.command_default
    )


def _workspace_has_tests(workspace: Path, test_discovery_globs: list[str]) -> bool:
    ignored_dirs = set(default_runtime_profile().copy_excludes) | {
        ".git",
        ".turbo",
        "coverage",
        "dist",
        "build",
    }
    for pattern in test_discovery_globs:
        for path in workspace.glob(pattern):
            if not path.is_file():
                continue
            relative_parts = path.relative_to(workspace).parts[:-1]
            if any(part in ignored_dirs for part in relative_parts):
                continue
            return True
    return False


def _is_test_command(command: list[str]) -> bool:
    command_text = " ".join(command)
    if "test:coverage" in command_text or command_text.endswith(" test"):
        return True
    if len(command) >= 3 and command[:3] == ["python", "-m", "unittest"]:
        return True
    if len(command) >= 3 and command[:3] == ["python", "-m", "pytest"]:
        return True
    return bool(command and Path(command[0]).name == "pytest")


def _should_skip_preflight_command(
    command: list[str],
    *,
    has_tests: bool,
    skip_test_commands_when_no_tests: bool,
) -> bool:
    return skip_test_commands_when_no_tests and not has_tests and _is_test_command(command)


def _preflight_cache_key(request: RunRequest, context: WorkspaceContext) -> str:
    environment = _resolved_environment(request)
    payload = {
        "cache_version": RAIDAR_CACHE_VERSION,
        "baseline_cache_key": context.baseline_cache_key,
        "harness": request.config.harness.value,
        "starter_fingerprint": context.starter_source.fingerprint,
        "environment": environment.cache_payload(),
        "setup_actions": getattr(request.scenario.verification, "setup_actions", []),
        "required_commands": request.scenario.verification.required_commands,
        "preflight_command_timeout_sec": (
            request.scenario.verification.preflight_command_timeout_sec
        ),
        "test_discovery_globs": request.scenario.verification.test_discovery_globs,
        "skip_test_commands_when_no_tests": (
            request.scenario.verification.skip_test_commands_when_no_tests
        ),
    }
    return _hash_json_payload(payload)


def _preflight_scratch_workspace(cache_file: Path) -> Path:
    return cache_file.with_name(f"{cache_file.name}.workspace")


def _copy_preflight_workspace(source_workspace: Path, preflight_workspace: Path) -> None:
    if preflight_workspace.exists():
        shutil.rmtree(preflight_workspace)
    shutil.copytree(
        source_workspace,
        preflight_workspace,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*default_runtime_profile().copy_excludes),
    )


def _run_starter_preflight_command(
    workspace: Path,
    env: dict[str, str],
    command: list[str],
    *,
    timeout_sec: int,
) -> None:
    completed = subprocess.run(
        command,
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
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
    timeout_sec: int,
) -> None:
    for command in setup_actions:
        completed = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
        )
        if completed.returncode == 0:
            continue
        output = (completed.stdout + "\n" + completed.stderr).strip()[:8000]
        rendered = " ".join(shlex.quote(part) for part in command)
        raise StarterPreflightError(
            f"Starter setup action failed: `{rendered}` exited {completed.returncode}\n{output}"
        )


def _write_starter_preflight_cache(request: StarterPreflightCacheWrite) -> None:
    request.cache_file.parent.mkdir(parents=True, exist_ok=True)
    request.cache_file.write_text(
        json.dumps(
            {
                "cache_version": RAIDAR_CACHE_VERSION,
                "harness": request.harness,
                "starter_fingerprint": request.starter_fingerprint,
                "baseline_cache_key": request.baseline_cache_key,
                "validated_at": datetime.now(UTC).isoformat(),
                "setup_actions": request.setup_actions,
                "required_commands": request.required_commands,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def ensure_starter_preflight(request: RunRequest, context: WorkspaceContext) -> bool | None:
    """Validate starter baseline commands once per effective prep input set."""
    from raidar.runtime.workspace import (
        _cleanup_workspace_runtime_env,
        _workspace_runtime_env,
    )

    required_commands = request.scenario.verification.required_commands
    setup_actions = getattr(request.scenario.verification, "setup_actions", [])
    if not required_commands:
        return None

    source_workspace = getattr(context, "baseline_workspace", context.workspace)

    cache_key = _preflight_cache_key(request, context)
    cache_file = _preflight_cache_file(cache_key)
    with _cache_key_lock(f"preflight-{cache_key}"):
        if cache_file.exists():
            _touch_cache_path(cache_file)
            return True

        preflight_workspace = _preflight_scratch_workspace(cache_file)
        _copy_preflight_workspace(source_workspace, preflight_workspace)
        try:
            env = _workspace_runtime_env(preflight_workspace, os.environ.copy())
            timeout_sec = _preflight_command_timeout(request)
            _run_workspace_setup_actions(
                workspace=preflight_workspace,
                env=env,
                setup_actions=setup_actions,
                timeout_sec=timeout_sec,
            )

            has_tests = _workspace_has_tests(
                preflight_workspace,
                request.scenario.verification.test_discovery_globs,
            )
            for command in required_commands:
                if _should_skip_preflight_command(
                    command,
                    has_tests=has_tests,
                    skip_test_commands_when_no_tests=(
                        request.scenario.verification.skip_test_commands_when_no_tests
                    ),
                ):
                    continue
                _run_starter_preflight_command(
                    preflight_workspace,
                    env,
                    command,
                    timeout_sec=timeout_sec,
                )
        finally:
            shutil.rmtree(preflight_workspace, ignore_errors=True)
            _cleanup_workspace_runtime_env(preflight_workspace)

        _write_starter_preflight_cache(
            StarterPreflightCacheWrite(
                cache_file=cache_file,
                harness=request.config.harness.value,
                starter_fingerprint=context.starter_source.fingerprint,
                baseline_cache_key=context.baseline_cache_key,
                setup_actions=setup_actions,
                required_commands=required_commands,
            )
        )
        _touch_cache_path(cache_file)
        return False
