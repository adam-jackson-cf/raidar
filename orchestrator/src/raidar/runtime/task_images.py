"""Docker task image cache, build, and runtime preflight services."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from raidar.runtime.models import TaskImageBuildResult, TaskImageRef
from raidar.runtime.workspace_cache import (
    _cache_key_lock,
    _directory_size_bytes,
    _maintenance_marker_path,
    _prep_cache_root,
    _raidar_cache_root,
    _repo_cache_identity,
    _task_image_cache_metadata_path,
)

TASK_IMAGE_BUILD_MIN_TIMEOUT_SEC = 120

RAIDAR_CACHE_PRUNE_INTERVAL_SEC = 6 * 60 * 60

RAIDAR_PREP_CACHE_MAX_AGE_SEC = 7 * 24 * 60 * 60

RAIDAR_PREP_CACHE_MAX_BYTES = 2 * 1024 * 1024 * 1024

RAIDAR_DOCKER_CACHE_MAX_AGE_SEC = 14 * 24 * 60 * 60

RAIDAR_DOCKER_LABEL_MANAGED = "io.raidar.cache.managed"

RAIDAR_DOCKER_LABEL_KEY = "io.raidar.cache.key"

RAIDAR_DOCKER_LABEL_HARNESS = "io.raidar.cache.harness"

RAIDAR_DOCKER_LABEL_REPO = "io.raidar.cache.repo"


@dataclass(frozen=True, slots=True)
class RuntimePreflightRequest:
    """Docker runtime preflight command input."""

    image_name: str
    run_env: dict[str, str]
    command: list[str]
    log_path: Path
    docker_args: list[str] | None = None


@dataclass(frozen=True, slots=True)
class TaskImageEnsureRequest:
    """Input for validating or building a reusable Harbor task image."""

    task_bundle_path: Path
    image_ref: TaskImageRef
    harness: str
    run_env: dict[str, str]
    log_dir: Path
    task_timeout_sec: int


def _cache_last_used_epoch(path: Path) -> float:
    return path.stat().st_mtime


def _prune_prep_cache_entries() -> None:
    baselines_root = _prep_cache_root() / "baselines"
    preflight_root = _prep_cache_root() / "preflight"
    baselines_root.mkdir(parents=True, exist_ok=True)
    preflight_root.mkdir(parents=True, exist_ok=True)

    now = time.time()
    _prune_baseline_cache_entries(baselines_root, now)
    _prune_preflight_cache_entries(preflight_root, now)


def _prune_baseline_cache_entries(baselines_root: Path, now: float) -> None:
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


def _prune_preflight_cache_entries(preflight_root: Path, now: float) -> None:
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


def _stale_task_image_names(
    metadata_path: Path, *, now: float, active_image_name: str | None
) -> tuple[str, ...]:
    payload = _load_task_image_cache_payload(metadata_path)
    image_names: tuple[str, ...] = ()
    image_name = payload.get("image_name") if payload is not None else None
    if payload is not None and not isinstance(image_name, str):
        metadata_path.unlink(missing_ok=True)
    elif isinstance(image_name, str) and image_name != active_image_name:
        try:
            last_used = _cache_last_used_epoch(metadata_path)
        except FileNotFoundError:
            last_used = now
        if now - last_used > RAIDAR_DOCKER_CACHE_MAX_AGE_SEC:
            reserve_image_name = payload.get("reserve_image_name") if payload else None
            image_names = (
                (image_name, reserve_image_name)
                if isinstance(reserve_image_name, str)
                else (image_name,)
            )
    return image_names


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
        image_names = _stale_task_image_names(
            metadata_path,
            now=now,
            active_image_name=active_image_name,
        )
        if not image_names:
            continue
        try:
            for image_name in image_names:
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


def _reserve_task_image_name(image_ref: TaskImageRef) -> str:
    return f"{image_ref.image_name}-reserve"


def _task_image_labels_match(
    image_name: str, image_ref: TaskImageRef, *, harness: str, run_env: dict[str, str]
) -> bool:
    labels = _inspect_docker_image_labels(image_name, run_env)
    if labels is None:
        return False
    expected_labels = _expected_task_image_labels(image_ref, harness)
    return all(labels.get(key) == value for key, value in expected_labels.items())


def _tag_task_image(source_image: str, target_image: str, run_env: dict[str, str]) -> bool:
    try:
        completed = subprocess.run(
            ["docker", "image", "tag", source_image, target_image],
            capture_output=True,
            text=True,
            timeout=60,
            env=run_env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Docker CLI not found.") from exc
    return completed.returncode == 0


def _task_image_cache_hit(
    image_ref: TaskImageRef, *, harness: str, run_env: dict[str, str]
) -> bool:
    if _task_image_labels_match(image_ref.image_name, image_ref, harness=harness, run_env=run_env):
        return True
    reserve_image = _reserve_task_image_name(image_ref)
    if not _task_image_labels_match(reserve_image, image_ref, harness=harness, run_env=run_env):
        return False
    if not _tag_task_image(reserve_image, image_ref.image_name, run_env):
        return False
    return _task_image_labels_match(
        image_ref.image_name, image_ref, harness=harness, run_env=run_env
    )


def _task_image_build_command(
    image_ref: TaskImageRef, dockerfile: Path, context_dir: Path, *, harness: str
) -> list[str]:
    command = [
        "docker",
        "build",
        "--tag",
        image_ref.image_name,
        "--tag",
        _reserve_task_image_name(image_ref),
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


def _run_runtime_preflight_command(request: RuntimePreflightRequest) -> None:
    docker_args = request.docker_args or []
    docker_cmd = ["docker", "run", "--rm", *docker_args, request.image_name, *request.command]
    try:
        completed = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=60,
            env=request.run_env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Docker CLI not found.") from exc

    output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    request.log_path.parent.mkdir(parents=True, exist_ok=True)
    request.log_path.write_text((output + "\n") if output else "", encoding="utf-8")
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
        RuntimePreflightRequest(
            image_name=image_ref.image_name,
            run_env=run_env,
            command=["git", "--version"],
            log_path=log_dir / "runtime-git-preflight.log",
        )
    )
    _run_runtime_preflight_command(
        RuntimePreflightRequest(
            image_name=image_ref.image_name,
            run_env=run_env,
            command=[
                "sh",
                "-lc",
                (
                    "test ! -d /tmp/agentic-eval-secrets && "
                    "! touch /.raidar-root-write-probe 2>/dev/null && "
                    "touch /tmp/raidar-tmp-write-probe && "
                    "! grep -q '00000000' /proc/net/route && "
                    "! grep -E ' /app | /workspace ' /proc/self/mountinfo"
                ),
            ],
            log_path=log_dir / "runtime-isolation-preflight.log",
            docker_args=[
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=16m",
            ],
        )
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
                "reserve_image_name": _reserve_task_image_name(image_ref),
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


def _ensure_task_image(request: TaskImageEnsureRequest) -> bool:
    if _task_image_ready_for_reuse(request):
        return True

    with _cache_key_lock(f"image-{request.image_ref.cache_key}"):
        if _task_image_ready_for_reuse(request):
            return True
        _build_and_verify_task_image(request)
        _write_task_image_cache_metadata(
            image_ref=request.image_ref,
            harness=request.harness,
            outcome="miss",
        )
        return False


def _task_image_ready_for_reuse(request: TaskImageEnsureRequest) -> bool:
    if not _cached_task_image_is_ready(
        image_ref=request.image_ref,
        harness=request.harness,
        run_env=request.run_env,
        log_dir=request.log_dir,
    ):
        return False
    _write_task_image_cache_metadata(
        image_ref=request.image_ref,
        harness=request.harness,
        outcome="hit",
    )
    return True


def _build_and_verify_task_image(request: TaskImageEnsureRequest) -> None:
    context_dir = request.task_bundle_path / "environment"
    dockerfile = context_dir / "Dockerfile"
    if not dockerfile.exists():
        raise FileNotFoundError(f"Task image build failed: missing Dockerfile {dockerfile}")

    build_cmd = _task_image_build_command(
        request.image_ref,
        dockerfile,
        context_dir,
        harness=request.harness,
    )
    build = _run_task_image_build(
        build_cmd,
        request.run_env,
        timeout_sec=_task_image_build_timeout(request.task_timeout_sec),
    )
    _write_task_image_build_log(request.log_dir, build)
    _raise_task_image_build_error(build_cmd, build)
    _ensure_harbor_runtime_preflight(
        image_ref=request.image_ref,
        run_env=request.run_env,
        log_dir=request.log_dir,
    )
