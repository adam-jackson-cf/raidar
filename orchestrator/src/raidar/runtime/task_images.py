"""Docker task image cache, build, and runtime preflight services."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from raidar.runtime.task_image_support import (
    TASK_IMAGE_PROBE_SCHEMA_VERSION,
    CapabilityRequirements,
    RuntimeProfile,
    TaskImageBuildResult,
    TaskImageRef,
    _cache_key_lock,
    _directory_size_bytes,
    _maintenance_marker_path,
    _prep_cache_root,
    _raidar_cache_root,
    _repo_cache_identity,
    _task_image_cache_metadata_path,
    capability_spec_satisfies,
    default_runtime_profile,
    installed_probe_value,
    merge_capability_requirements,
    normalize_probe_version,
    probe_command,
    tool_catalog_payload,
)


@dataclass(frozen=True, slots=True)
class RuntimePreflightRequest:
    """Docker runtime preflight command input."""

    image_name: str
    run_env: dict[str, str]
    command: list[str]
    log_path: Path
    docker_args: list[str] | None = None
    runtime_profile: RuntimeProfile = field(default_factory=default_runtime_profile)


@dataclass(frozen=True, slots=True)
class TaskImageEnsureRequest:
    """Input for validating or building a reusable Harbor task image."""

    task_bundle_path: Path
    image_ref: TaskImageRef
    harness: str
    run_env: dict[str, str]
    log_dir: Path
    task_timeout_sec: int
    provided_capabilities: CapabilityRequirements = field(default_factory=CapabilityRequirements)
    required_capabilities: CapabilityRequirements = field(default_factory=CapabilityRequirements)
    runtime_profile: RuntimeProfile = field(default_factory=default_runtime_profile)


def _cache_last_used_epoch(path: Path) -> float:
    return path.stat().st_mtime


def _prune_prep_cache_entries(runtime_profile: RuntimeProfile | None = None) -> None:
    runtime_profile = runtime_profile or default_runtime_profile()
    baselines_root = _prep_cache_root() / "baselines"
    preflight_root = _prep_cache_root() / "preflight"
    baselines_root.mkdir(parents=True, exist_ok=True)
    preflight_root.mkdir(parents=True, exist_ok=True)

    now = time.time()
    _prune_baseline_cache_entries(baselines_root, now, runtime_profile=runtime_profile)
    _prune_preflight_cache_entries(preflight_root, now, runtime_profile=runtime_profile)


def _prune_baseline_cache_entries(
    baselines_root: Path, now: float, *, runtime_profile: RuntimeProfile | None = None
) -> None:
    runtime_profile = runtime_profile or default_runtime_profile()
    baseline_entries = [path for path in baselines_root.iterdir() if path.is_dir()]
    total_bytes = 0
    retained: list[tuple[float, int, Path]] = []
    for entry in baseline_entries:
        last_used = _cache_last_used_epoch(entry)
        if now - last_used > runtime_profile.prep_cache_max_age_sec:
            shutil.rmtree(entry, ignore_errors=True)
            continue
        size_bytes = _directory_size_bytes(entry)
        total_bytes += size_bytes
        retained.append((last_used, size_bytes, entry))

    for _last_used, size_bytes, entry in sorted(retained, key=lambda item: item[0]):
        if total_bytes <= runtime_profile.prep_cache_max_bytes:
            break
        shutil.rmtree(entry, ignore_errors=True)
        total_bytes -= size_bytes


def _prune_preflight_cache_entries(
    preflight_root: Path, now: float, *, runtime_profile: RuntimeProfile | None = None
) -> None:
    runtime_profile = runtime_profile or default_runtime_profile()
    for cache_file in preflight_root.glob("*.ok.json"):
        try:
            last_used = _cache_last_used_epoch(cache_file)
        except FileNotFoundError:
            continue
        if now - last_used > runtime_profile.prep_cache_max_age_sec:
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
    metadata_path: Path,
    *,
    now: float,
    active_image_name: str | None,
    runtime_profile: RuntimeProfile | None = None,
) -> tuple[str, ...]:
    runtime_profile = runtime_profile or default_runtime_profile()
    payload = _load_task_image_cache_payload(metadata_path)
    image_name = _cached_task_image_name(payload)
    if payload is not None and image_name is None:
        metadata_path.unlink(missing_ok=True)
        return ()
    if image_name is None or image_name == active_image_name:
        return ()
    if not _cache_entry_expired(
        metadata_path,
        now=now,
        max_age_sec=runtime_profile.docker_cache_max_age_sec,
    ):
        return ()
    return _cached_task_image_names(payload, image_name)


def _cached_task_image_name(payload: dict[str, Any] | None) -> str | None:
    image_name = payload.get("image_name") if payload is not None else None
    return image_name if isinstance(image_name, str) else None


def _cache_entry_expired(metadata_path: Path, *, now: float, max_age_sec: int) -> bool:
    try:
        last_used = _cache_last_used_epoch(metadata_path)
    except FileNotFoundError:
        last_used = now
    return now - last_used > max_age_sec


def _cached_task_image_names(
    payload: dict[str, Any] | None,
    image_name: str,
) -> tuple[str, ...]:
    reserve_image_name = payload.get("reserve_image_name") if payload else None
    if isinstance(reserve_image_name, str):
        return (image_name, reserve_image_name)
    return (image_name,)


def _managed_task_image(
    image_name: str,
    run_env: dict[str, str],
    *,
    runtime_profile: RuntimeProfile | None = None,
) -> bool:
    runtime_profile = runtime_profile or default_runtime_profile()
    labels = _inspect_docker_image_labels(image_name, run_env)
    profile_labels = runtime_profile.docker_labels
    return labels is not None and (
        labels.get(profile_labels["managed"]) == "true"
        and labels.get(profile_labels["repo"]) == _repo_cache_identity()
    )


def _remove_task_image(
    image_name: str,
    run_env: dict[str, str],
    *,
    runtime_profile: RuntimeProfile | None = None,
) -> None:
    runtime_profile = runtime_profile or default_runtime_profile()
    subprocess.run(
        ["docker", "image", "rm", "-f", image_name],
        capture_output=True,
        text=True,
        timeout=runtime_profile.docker_command_timeout_sec,
        env=run_env,
        check=False,
    )


def _prune_stale_task_images(
    *,
    run_env: dict[str, str],
    active_image_name: str | None,
    runtime_profile: RuntimeProfile | None = None,
) -> None:
    runtime_profile = runtime_profile or default_runtime_profile()
    images_root = _raidar_cache_root() / "images"
    images_root.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for metadata_path in images_root.glob("*.json"):
        image_names = _stale_task_image_names(
            metadata_path,
            now=now,
            active_image_name=active_image_name,
            runtime_profile=runtime_profile,
        )
        if not image_names:
            continue
        try:
            for image_name in image_names:
                if _managed_task_image(
                    image_name,
                    run_env,
                    runtime_profile=runtime_profile,
                ):
                    _remove_task_image(image_name, run_env, runtime_profile=runtime_profile)
        except FileNotFoundError:
            return
        metadata_path.unlink(missing_ok=True)


def _maybe_run_cache_maintenance(
    *,
    run_env: dict[str, str],
    active_image_name: str | None,
    runtime_profile: RuntimeProfile | None = None,
) -> None:
    runtime_profile = runtime_profile or default_runtime_profile()
    marker_path = _maintenance_marker_path()
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if marker_path.exists():
            age_sec = time.time() - marker_path.stat().st_mtime
            if age_sec < runtime_profile.cache_prune_interval_sec:
                return
    except OSError:
        return

    try:
        with _cache_key_lock("maintenance", timeout_sec=30):
            if marker_path.exists():
                age_sec = time.time() - marker_path.stat().st_mtime
                if age_sec < runtime_profile.cache_prune_interval_sec:
                    return
            _prune_prep_cache_entries(runtime_profile=runtime_profile)
            _prune_stale_task_images(
                run_env=run_env,
                active_image_name=active_image_name,
                runtime_profile=runtime_profile,
            )
            marker_path.write_text(
                json.dumps({"last_pruned_at": datetime.now(UTC).isoformat()}, indent=2),
                encoding="utf-8",
            )
    except (OSError, RuntimeError, TimeoutError, ValueError):
        return


def _inspect_docker_image_labels(
    image_name: str,
    run_env: dict[str, str],
    *,
    runtime_profile: RuntimeProfile | None = None,
) -> dict[str, str] | None:
    runtime_profile = runtime_profile or default_runtime_profile()
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
            timeout=runtime_profile.docker_inspect_timeout_sec,
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


def _expected_task_image_labels(
    image_ref: TaskImageRef,
    harness: str,
    *,
    runtime_profile: RuntimeProfile | None = None,
) -> dict[str, str]:
    runtime_profile = runtime_profile or default_runtime_profile()
    profile_labels = runtime_profile.docker_labels
    return {
        profile_labels["managed"]: "true",
        profile_labels["key"]: image_ref.cache_key,
        profile_labels["harness"]: harness,
        profile_labels["repo"]: _repo_cache_identity(),
    }


def _reserve_task_image_name(image_ref: TaskImageRef) -> str:
    return f"{image_ref.image_name}-reserve"


def _task_image_labels_match(
    image_name: str,
    image_ref: TaskImageRef,
    *,
    harness: str,
    run_env: dict[str, str],
    runtime_profile: RuntimeProfile | None = None,
) -> bool:
    labels = _inspect_docker_image_labels(image_name, run_env, runtime_profile=runtime_profile)
    if labels is None:
        return False
    expected_labels = _expected_task_image_labels(
        image_ref,
        harness,
        runtime_profile=runtime_profile,
    )
    return all(labels.get(key) == value for key, value in expected_labels.items())


def _tag_task_image(
    source_image: str,
    target_image: str,
    run_env: dict[str, str],
    *,
    runtime_profile: RuntimeProfile | None = None,
) -> bool:
    runtime_profile = runtime_profile or default_runtime_profile()
    try:
        completed = subprocess.run(
            ["docker", "image", "tag", source_image, target_image],
            capture_output=True,
            text=True,
            timeout=runtime_profile.docker_command_timeout_sec,
            env=run_env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Docker CLI not found.") from exc
    return completed.returncode == 0


def _task_image_cache_hit(
    image_ref: TaskImageRef,
    *,
    harness: str,
    run_env: dict[str, str],
    runtime_profile: RuntimeProfile | None = None,
) -> bool:
    runtime_profile = runtime_profile or default_runtime_profile()
    if _task_image_labels_match(
        image_ref.image_name,
        image_ref,
        harness=harness,
        run_env=run_env,
        runtime_profile=runtime_profile,
    ):
        return True
    reserve_image = _reserve_task_image_name(image_ref)
    if not _task_image_labels_match(
        reserve_image,
        image_ref,
        harness=harness,
        run_env=run_env,
        runtime_profile=runtime_profile,
    ):
        return False
    if not _tag_task_image(
        reserve_image,
        image_ref.image_name,
        run_env,
        runtime_profile=runtime_profile,
    ):
        return False
    return _task_image_labels_match(
        image_ref.image_name,
        image_ref,
        harness=harness,
        run_env=run_env,
        runtime_profile=runtime_profile,
    )


def _task_image_build_command(
    image_ref: TaskImageRef,
    dockerfile: Path,
    context_dir: Path,
    *,
    harness: str,
    runtime_profile: RuntimeProfile | None = None,
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
    for key, value in _expected_task_image_labels(
        image_ref,
        harness,
        runtime_profile=runtime_profile,
    ).items():
        command.extend(["--label", f"{key}={value}"])
    command.append(str(context_dir))
    return command


def _run_task_image_build(
    build_cmd: list[str],
    run_env: dict[str, str],
    *,
    timeout_sec: int,
    runtime_profile: RuntimeProfile | None = None,
) -> TaskImageBuildResult:
    del runtime_profile
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


def _task_image_build_timeout(
    task_timeout_sec: int,
    *,
    runtime_profile: RuntimeProfile | None = None,
) -> int:
    """Bound pre-Harbor image builds to the scenario budget."""
    runtime_profile = runtime_profile or default_runtime_profile()
    return max(runtime_profile.task_image_build_min_timeout_sec, task_timeout_sec)


def _run_runtime_preflight_command(request: RuntimePreflightRequest) -> None:
    docker_args = request.docker_args or []
    docker_cmd = ["docker", "run", "--rm", *docker_args, request.image_name, *request.command]
    try:
        completed = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=request.runtime_profile.docker_command_timeout_sec,
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
    runtime_profile: RuntimeProfile | None = None,
) -> None:
    runtime_profile = runtime_profile or default_runtime_profile()
    _run_runtime_preflight_command(
        RuntimePreflightRequest(
            image_name=image_ref.image_name,
            run_env=run_env,
            command=["git", "--version"],
            log_path=log_dir / "runtime-git-preflight.log",
            runtime_profile=runtime_profile,
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
            runtime_profile=runtime_profile,
        )
    )


def _run_capability_probe(
    *,
    image_name: str,
    category: str,
    name: str,
    command: list[str],
    run_env: dict[str, str],
    log_dir: Path,
    runtime_profile: RuntimeProfile | None = None,
) -> str:
    runtime_profile = runtime_profile or default_runtime_profile()
    docker_cmd = ["docker", "run", "--rm", image_name, *command]
    try:
        completed = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=runtime_profile.docker_command_timeout_sec,
            env=run_env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Docker CLI not found.") from exc

    output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    log_path = log_dir / f"capability-{category}-{name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text((output + "\n") if output else "", encoding="utf-8")
    if completed.returncode != 0:
        rendered = " ".join(shlex.quote(part) for part in docker_cmd)
        excerpt = output[:8000]
        raise RuntimeError(
            f"Task image capability probe failed for {category}.{name}: "
            f"`{rendered}` exited {completed.returncode}" + (f"\n{excerpt}" if excerpt else "")
        )
    fixed_value = installed_probe_value(category, name)
    if fixed_value is not None:
        return fixed_value
    return normalize_probe_version(output)


def _ensure_task_image_capability_preflight(
    *,
    image_ref: TaskImageRef,
    run_env: dict[str, str],
    log_dir: Path,
    provided_capabilities: CapabilityRequirements,
    required_capabilities: CapabilityRequirements,
    runtime_profile: RuntimeProfile | None = None,
) -> dict[str, Any]:
    runtime_profile = runtime_profile or default_runtime_profile()
    expected = merge_capability_requirements(provided_capabilities, required_capabilities)
    probe_results: dict[str, dict[str, str]] = {
        "runtimes": {},
        "package_managers": {},
        "tools": {},
        "browsers": {},
    }
    for category in ("runtimes", "package_managers", "tools", "browsers"):
        expected_items = getattr(expected, category)
        for name in sorted(expected_items):
            command = probe_command(category, name)
            probe_results[category][name] = _run_capability_probe(
                image_name=image_ref.image_name,
                category=category,
                name=name,
                command=command,
                run_env=run_env,
                log_dir=log_dir,
                runtime_profile=runtime_profile,
            )
    _validate_probe_results(
        label="declared capabilities",
        expected=provided_capabilities,
        probe_results=probe_results,
    )
    _validate_probe_results(
        label="required capabilities",
        expected=required_capabilities,
        probe_results=probe_results,
    )
    return {
        "schema_version": TASK_IMAGE_PROBE_SCHEMA_VERSION,
        "results": probe_results,
    }


def _validate_probe_results(
    *,
    label: str,
    expected: CapabilityRequirements,
    probe_results: dict[str, dict[str, str]],
) -> None:
    errors: list[str] = []
    for category in ("runtimes", "package_managers", "tools", "browsers"):
        for name, expected_spec in sorted(getattr(expected, category).items()):
            probed_spec = probe_results.get(category, {}).get(name)
            if probed_spec is None:
                errors.append(f"{category}.{name} was not probed")
                continue
            if not capability_spec_satisfies(probed_spec, expected_spec):
                errors.append(f"{category}.{name} expected {expected_spec}, probed {probed_spec}")
    if errors:
        raise RuntimeError(f"Task image probe mismatch for {label}: " + "; ".join(errors))


def _cached_task_image_is_ready(
    *,
    image_ref: TaskImageRef,
    harness: str,
    run_env: dict[str, str],
    log_dir: Path,
    provided_capabilities: CapabilityRequirements,
    required_capabilities: CapabilityRequirements,
    runtime_profile: RuntimeProfile | None = None,
) -> dict[str, Any] | None:
    runtime_profile = runtime_profile or default_runtime_profile()
    if not _task_image_cache_hit(
        image_ref,
        harness=harness,
        run_env=run_env,
        runtime_profile=runtime_profile,
    ):
        return None
    cached_probe_results = _cached_task_image_probe_results(
        image_ref=image_ref,
        harness=harness,
        provided_capabilities=provided_capabilities,
        required_capabilities=required_capabilities,
    )
    if cached_probe_results is not None:
        return cached_probe_results
    try:
        _ensure_harbor_runtime_preflight(
            image_ref=image_ref,
            run_env=run_env,
            log_dir=log_dir,
            runtime_profile=runtime_profile,
        )
        probe_results = _ensure_task_image_capability_preflight(
            image_ref=image_ref,
            run_env=run_env,
            log_dir=log_dir,
            provided_capabilities=provided_capabilities,
            required_capabilities=required_capabilities,
            runtime_profile=runtime_profile,
        )
    except RuntimeError:
        return None
    return probe_results


def _cached_task_image_probe_results(
    *,
    image_ref: TaskImageRef,
    harness: str,
    provided_capabilities: CapabilityRequirements,
    required_capabilities: CapabilityRequirements,
) -> dict[str, Any] | None:
    payload = _load_task_image_cache_payload(_task_image_cache_metadata_path(image_ref.cache_key))
    if not _cached_task_image_metadata_matches(
        payload,
        image_ref=image_ref,
        harness=harness,
        provided_capabilities=provided_capabilities,
        required_capabilities=required_capabilities,
    ):
        return None

    probe_payload, probe_results = _cached_probe_payload(payload)
    if probe_payload is None or probe_results is None:
        return None
    _validate_probe_results(
        label="declared capabilities",
        expected=provided_capabilities,
        probe_results=probe_results,
    )
    _validate_probe_results(
        label="required capabilities",
        expected=required_capabilities,
        probe_results=probe_results,
    )
    return probe_payload


def _cached_task_image_metadata_matches(
    payload: dict[str, Any] | None,
    *,
    image_ref: TaskImageRef,
    harness: str,
    provided_capabilities: CapabilityRequirements,
    required_capabilities: CapabilityRequirements,
) -> bool:
    if payload is None:
        return False
    expected = {
        "cache_key": image_ref.cache_key,
        "image_name": image_ref.image_name,
        "harness": harness,
        "repo_id": _repo_cache_identity(),
        "probe_schema_version": TASK_IMAGE_PROBE_SCHEMA_VERSION,
        "tool_catalog": tool_catalog_payload(),
        "provided_capabilities": provided_capabilities.model_dump(mode="json"),
        "required_capabilities": required_capabilities.model_dump(mode="json"),
    }
    return all(payload.get(key) == value for key, value in expected.items())


def _cached_probe_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    probe_payload = payload.get("probe_results")
    if not isinstance(probe_payload, dict):
        return None, None
    if probe_payload.get("schema_version") != TASK_IMAGE_PROBE_SCHEMA_VERSION:
        return None, None
    probe_results = probe_payload.get("results")
    if not isinstance(probe_results, dict):
        return None, None
    return probe_payload, probe_results


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
    *,
    request: TaskImageEnsureRequest,
    outcome: str,
    probe_results: dict[str, Any] | None,
) -> None:
    metadata_path = _task_image_cache_metadata_path(request.image_ref.cache_key)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(
            {
                "cache_key": request.image_ref.cache_key,
                "image_name": request.image_ref.image_name,
                "reserve_image_name": _reserve_task_image_name(request.image_ref),
                "image_tag": request.image_ref.tag,
                "harness": request.harness,
                "repo_id": _repo_cache_identity(),
                "outcome": outcome,
                "probe_schema_version": TASK_IMAGE_PROBE_SCHEMA_VERSION,
                "tool_catalog": tool_catalog_payload(),
                "provided_capabilities": request.provided_capabilities.model_dump(mode="json"),
                "required_capabilities": request.required_capabilities.model_dump(mode="json"),
                "probe_results": probe_results or {},
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
        probe_results = _build_and_verify_task_image(request)
        _write_task_image_cache_metadata(
            outcome="miss",
            request=request,
            probe_results=probe_results,
        )
        return False


def _task_image_ready_for_reuse(request: TaskImageEnsureRequest) -> bool:
    probe_results = _cached_task_image_is_ready(
        image_ref=request.image_ref,
        harness=request.harness,
        run_env=request.run_env,
        log_dir=request.log_dir,
        provided_capabilities=request.provided_capabilities,
        required_capabilities=request.required_capabilities,
        runtime_profile=request.runtime_profile,
    )
    if probe_results is None or probe_results is False:
        return False
    if probe_results is True:
        probe_results = {}
    _write_task_image_cache_metadata(
        outcome="hit",
        request=request,
        probe_results=probe_results,
    )
    return True


def _build_and_verify_task_image(request: TaskImageEnsureRequest) -> dict[str, Any]:
    context_dir = request.task_bundle_path / "environment"
    dockerfile = context_dir / "Dockerfile"
    if not dockerfile.exists():
        raise FileNotFoundError(f"Task image build failed: missing Dockerfile {dockerfile}")

    build_cmd = _task_image_build_command(
        request.image_ref,
        dockerfile,
        context_dir,
        harness=request.harness,
        runtime_profile=request.runtime_profile,
    )
    build = _run_task_image_build(
        build_cmd,
        request.run_env,
        timeout_sec=_task_image_build_timeout(
            request.task_timeout_sec,
            runtime_profile=request.runtime_profile,
        ),
        runtime_profile=request.runtime_profile,
    )
    _write_task_image_build_log(request.log_dir, build)
    _raise_task_image_build_error(build_cmd, build)
    _ensure_harbor_runtime_preflight(
        image_ref=request.image_ref,
        run_env=request.run_env,
        log_dir=request.log_dir,
        runtime_profile=request.runtime_profile,
    )
    return _ensure_task_image_capability_preflight(
        image_ref=request.image_ref,
        run_env=request.run_env,
        log_dir=request.log_dir,
        provided_capabilities=request.provided_capabilities,
        required_capabilities=request.required_capabilities,
        runtime_profile=request.runtime_profile,
    )
