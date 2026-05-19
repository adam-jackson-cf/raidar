"""Harbor process execution and retry services."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from raidar.runtime.harbor_cleanup import cleanup_stale_harbor_resources
from raidar.runtime.harbor_env import _redact_sensitive_text
from raidar.runtime.harbor_preflight import _docker_compose_preflight_reason
from raidar.runtime.harbor_results import _is_registry_rate_limited, detect_trial_failure
from raidar.runtime.models import HarborExecutionRequest, HarborExecutionResult
from raidar.runtime.wait import wait_for_harbor_rate_limit_retry

HARBOR_TIMEOUT_BUFFER_SEC = 120

HARBOR_RATE_LIMIT_MAX_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class HarborProcessRequest:
    """Input for one Harbor process attempt."""

    harbor_cmd: list[str]
    workspace: Path
    timeout_sec: int
    run_env: dict[str, str]
    run_harbor_dir: Path
    job_dir: Path


def execute_harbor(request: HarborExecutionRequest) -> HarborExecutionResult:
    """Execute Harbor against a local scenario bundle."""
    request.jobs_dir.mkdir(parents=True, exist_ok=True)
    job_name = f"orchestrator-{request.run_id}"
    job_dir = request.jobs_dir / job_name
    process_request = _harbor_process_request(request, job_name, job_dir)

    execution_error = _run_harbor_with_retries(process_request)
    if execution_error is not None:
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


def _harbor_process_request(
    request: HarborExecutionRequest, job_name: str, job_dir: Path
) -> HarborProcessRequest:
    return HarborProcessRequest(
        harbor_cmd=request.adapter.build_harbor_command(
            task_path=request.task_bundle_path,
            job_name=job_name,
            jobs_dir=request.jobs_dir,
        ),
        workspace=request.workspace,
        timeout_sec=request.timeout_sec,
        run_env=request.run_env,
        run_harbor_dir=request.run_harbor_dir,
        job_dir=job_dir,
    )


def _run_harbor_with_retries(request: HarborProcessRequest) -> str | None:
    for attempt in range(1, HARBOR_RATE_LIMIT_MAX_ATTEMPTS + 1):
        execution_error = _run_harbor_process(request)
        if execution_error is None:
            return None
        if not _should_retry_harbor_rate_limit(
            attempt=attempt,
            execution_error=execution_error,
            run_harbor_dir=request.run_harbor_dir,
        ):
            return execution_error
        cleanup_stale_harbor_resources()
        wait_for_harbor_rate_limit_retry()
    return execution_error


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


def _run_harbor_process(request: HarborProcessRequest) -> str | None:
    request.run_harbor_dir.mkdir(parents=True, exist_ok=True)
    command_path = request.run_harbor_dir / "command.txt"
    stdout_path = request.run_harbor_dir / "harbor-stdout.log"
    stderr_path = request.run_harbor_dir / "harbor-stderr.log"
    command_path.write_text(" ".join(shlex.quote(part) for part in request.harbor_cmd) + "\n")

    preflight_reason = _docker_compose_preflight_reason(request.run_env)
    if preflight_reason:
        stdout_path.write_text("")
        stderr_path.write_text(preflight_reason + "\n")
        return preflight_reason

    try:
        process = subprocess.Popen(
            request.harbor_cmd,
            cwd=request.workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=request.run_env,
            start_new_session=True,
        )
    except FileNotFoundError:
        return "Harbor not installed"

    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=request.timeout_sec)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_group(process)
        stdout, stderr = process.communicate()

    stdout_path.write_text(_redact_sensitive_text(stdout or ""))
    stderr_path.write_text(_redact_sensitive_text(stderr or ""))

    if timed_out:
        return _timeout_reason(timeout_sec=request.timeout_sec, job_dir=request.job_dir)
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
