import json
import subprocess
import time
from pathlib import Path

import pytest

from raidar.runtime import task_images
from raidar.runtime.models import TaskImageRef


def test_load_task_image_cache_payload_removes_invalid_files(tmp_path):
    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("{", encoding="utf-8")
    assert task_images._load_task_image_cache_payload(invalid_json) is None
    assert not invalid_json.exists()

    non_object = tmp_path / "list.json"
    non_object.write_text("[]", encoding="utf-8")
    assert task_images._load_task_image_cache_payload(non_object) is None
    assert not non_object.exists()

    valid = tmp_path / "valid.json"
    valid.write_text('{"image_name":"img"}', encoding="utf-8")
    assert task_images._load_task_image_cache_payload(valid) == {"image_name": "img"}


def test_stale_task_image_names_returns_only_expired_inactive_images(monkeypatch, tmp_path):
    metadata = tmp_path / "image.json"
    metadata.write_text('{"image_name":"old"}', encoding="utf-8")
    old = time.time() - task_images.RAIDAR_DOCKER_CACHE_MAX_AGE_SEC - 10
    fresh = time.time()
    monkeypatch.setattr(task_images, "_cache_last_used_epoch", lambda _path: old)

    assert task_images._stale_task_image_names(
        metadata, now=time.time(), active_image_name=None
    ) == ("old",)
    assert (
        task_images._stale_task_image_names(metadata, now=time.time(), active_image_name="old")
        == ()
    )

    monkeypatch.setattr(task_images, "_cache_last_used_epoch", lambda _path: fresh)
    assert (
        task_images._stale_task_image_names(metadata, now=time.time(), active_image_name=None) == ()
    )

    metadata.write_text('{"image_name":42}', encoding="utf-8")
    assert (
        task_images._stale_task_image_names(metadata, now=time.time(), active_image_name=None) == ()
    )
    assert not metadata.exists()


def test_prune_prep_cache_removes_expired_and_oversized_entries(monkeypatch, tmp_path):
    baselines_root = tmp_path / "prep" / "baselines"
    preflight_root = tmp_path / "prep" / "preflight"
    old_entry = baselines_root / "old"
    large_old = baselines_root / "large-old"
    large_new = baselines_root / "large-new"
    old_preflight = preflight_root / "old.ok.json"
    fresh_preflight = preflight_root / "fresh.ok.json"
    for path in (old_entry, large_old, large_new):
        path.mkdir(parents=True)
        (path / "file").write_text(path.name, encoding="utf-8")
    preflight_root.mkdir(parents=True)
    old_preflight.write_text("{}", encoding="utf-8")
    fresh_preflight.write_text("{}", encoding="utf-8")

    now = 10_000.0
    mtimes = {
        old_entry: now - task_images.RAIDAR_PREP_CACHE_MAX_AGE_SEC - 1,
        large_old: now - 20,
        large_new: now - 10,
        old_preflight: now - task_images.RAIDAR_PREP_CACHE_MAX_AGE_SEC - 1,
        fresh_preflight: now,
    }
    monkeypatch.setattr(task_images, "_prep_cache_root", lambda: tmp_path / "prep")
    monkeypatch.setattr(task_images, "_cache_last_used_epoch", lambda path: mtimes[path])
    monkeypatch.setattr(task_images, "_directory_size_bytes", lambda _path: 10)
    monkeypatch.setattr(task_images, "RAIDAR_PREP_CACHE_MAX_BYTES", 10)
    monkeypatch.setattr(task_images.time, "time", lambda: now)

    task_images._prune_prep_cache_entries()

    assert not old_entry.exists()
    assert not large_old.exists()
    assert large_new.exists()
    assert not old_preflight.exists()
    assert fresh_preflight.exists()


def test_prune_stale_task_images_removes_only_managed_expired_images(monkeypatch, tmp_path):
    images_root = tmp_path / "images"
    images_root.mkdir()
    (images_root / "old.json").write_text('{"image_name":"old"}', encoding="utf-8")
    (images_root / "active.json").write_text('{"image_name":"active"}', encoding="utf-8")
    removed: list[str] = []
    monkeypatch.setattr(task_images, "_raidar_cache_root", lambda: tmp_path)
    monkeypatch.setattr(task_images, "_cache_last_used_epoch", lambda _path: 0)
    monkeypatch.setattr(
        task_images.time, "time", lambda: task_images.RAIDAR_DOCKER_CACHE_MAX_AGE_SEC + 1
    )
    monkeypatch.setattr(task_images, "_managed_task_image", lambda image, _env: image == "old")
    monkeypatch.setattr(
        task_images, "_remove_task_image", lambda image, _env: removed.append(image)
    )

    task_images._prune_stale_task_images(run_env={}, active_image_name="active")

    assert removed == ["old"]
    assert not (images_root / "old.json").exists()
    assert (images_root / "active.json").exists()


def test_cache_maintenance_respects_marker_and_swallows_lock_errors(monkeypatch, tmp_path):
    marker = tmp_path / "maintenance.json"
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(task_images, "_maintenance_marker_path", lambda: marker)
    monkeypatch.setattr(task_images.time, "time", lambda: marker.stat().st_mtime + 1)
    prune_calls: list[str] = []
    monkeypatch.setattr(
        task_images, "_prune_prep_cache_entries", lambda: prune_calls.append("prep")
    )

    task_images._maybe_run_cache_maintenance(run_env={}, active_image_name=None)
    assert prune_calls == []

    marker.unlink()

    class _FailingLock:
        def __enter__(self):
            raise TimeoutError("locked")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(task_images, "_cache_key_lock", lambda *_args, **_kwargs: _FailingLock())
    task_images._maybe_run_cache_maintenance(run_env={}, active_image_name=None)
    assert prune_calls == []


def test_docker_label_inspection_and_cache_hit(monkeypatch):
    image_ref = TaskImageRef("image:tag", "cache", "tag")
    expected = task_images._expected_task_image_labels(image_ref, "codex-cli")
    monkeypatch.setattr(task_images, "_repo_cache_identity", lambda: "repo")
    expected[task_images.RAIDAR_DOCKER_LABEL_REPO] = "repo"

    monkeypatch.setattr(
        task_images.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["docker"], 0, stdout=json.dumps(expected), stderr=""
        ),
    )
    assert task_images._inspect_docker_image_labels("image:tag", {}) == expected
    assert task_images._task_image_cache_hit(image_ref, harness="codex-cli", run_env={}) is True
    assert task_images._managed_task_image("image:tag", {}) is True

    monkeypatch.setattr(
        task_images.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(["docker"], 1, stdout="", stderr=""),
    )
    assert task_images._inspect_docker_image_labels("missing", {}) is None

    def missing_docker(*_args, **_kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(task_images.subprocess, "run", missing_docker)
    with pytest.raises(RuntimeError, match="Docker CLI not found"):
        task_images._inspect_docker_image_labels("image:tag", {})


def test_run_task_image_build_handles_timeout_and_missing_docker(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["docker"], timeout=5, output=b"out", stderr=b"err")

    monkeypatch.setattr(task_images.subprocess, "run", timeout)
    build = task_images._run_task_image_build(["docker", "build"], {}, timeout_sec=5)
    assert build.timed_out is True
    assert build.completed_process.returncode == 124
    assert build.completed_process.stdout == "out"
    assert build.completed_process.stderr == "err"

    def missing(*_args, **_kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(task_images.subprocess, "run", missing)
    with pytest.raises(RuntimeError, match="Docker CLI not found"):
        task_images._run_task_image_build(["docker"], {}, timeout_sec=5)


def test_task_image_build_command_tags_primary_and_reserve_images():
    image_ref = TaskImageRef("image:tag", "cache", "tag")

    command = task_images._task_image_build_command(
        image_ref, Path("Dockerfile"), Path("context"), harness="codex-cli"
    )

    tag_values = [command[index + 1] for index, value in enumerate(command) if value == "--tag"]
    assert tag_values == ["image:tag", "image:tag-reserve"]


def test_task_image_cache_hit_restores_primary_tag_from_validated_reserve(
    monkeypatch,
):
    image_ref = TaskImageRef("image:tag", "cache", "tag")
    monkeypatch.setattr(task_images, "_repo_cache_identity", lambda: "repo")
    expected = task_images._expected_task_image_labels(image_ref, "codex-cli")
    labels_by_image = {"image:tag-reserve": expected}

    monkeypatch.setattr(
        task_images,
        "_inspect_docker_image_labels",
        lambda image_name, _env: labels_by_image.get(image_name),
    )
    tag_calls: list[tuple[str, str]] = []

    def fake_tag(source_image: str, target_image: str, _env: dict[str, str]) -> bool:
        tag_calls.append((source_image, target_image))
        labels_by_image[target_image] = labels_by_image[source_image]
        return True

    monkeypatch.setattr(task_images, "_tag_task_image", fake_tag)

    assert task_images._task_image_cache_hit(image_ref, harness="codex-cli", run_env={})
    assert tag_calls == [("image:tag-reserve", "image:tag")]


def test_run_task_image_build_does_not_force_legacy_docker_builder(monkeypatch):
    captured_env: dict[str, str] = {}

    def fake_run(*_args, **kwargs):
        captured_env.update(kwargs["env"])
        return subprocess.CompletedProcess(["docker"], 0, stdout="", stderr="")

    monkeypatch.setattr(task_images.subprocess, "run", fake_run)

    task_images._run_task_image_build(["docker", "build"], {}, timeout_sec=5)

    assert "DOCKER_BUILDKIT" not in captured_env


def test_runtime_preflight_writes_logs_and_reports_failures(monkeypatch, tmp_path):
    request = task_images.RuntimePreflightRequest(
        image_name="image:tag",
        run_env={"A": "1"},
        command=["git", "--version"],
        log_path=tmp_path / "logs" / "preflight.log",
        docker_args=["--network", "none"],
    )
    monkeypatch.setattr(
        task_images.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["docker"], 0, stdout="git version", stderr=""
        ),
    )
    task_images._run_runtime_preflight_command(request)
    assert request.log_path.read_text(encoding="utf-8") == "git version\n"

    monkeypatch.setattr(
        task_images.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["docker"], 2, stdout="", stderr="bad preflight"
        ),
    )
    with pytest.raises(RuntimeError, match="bad preflight"):
        task_images._run_runtime_preflight_command(request)

    monkeypatch.setattr(
        task_images.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(["docker"], 2, stdout="", stderr=""),
    )
    with pytest.raises(RuntimeError, match="exited 2"):
        task_images._run_runtime_preflight_command(request)


def test_task_image_ready_paths_write_hit_metadata_and_build_on_miss(monkeypatch, tmp_path):
    fixture = TaskImageRef("image:tag", "cache", "tag")
    request = task_images.TaskImageEnsureRequest(
        task_bundle_path=tmp_path / "bundle",
        image_ref=fixture,
        harness="codex-cli",
        run_env={},
        log_dir=tmp_path / "logs",
        task_timeout_sec=10,
    )
    metadata_path = tmp_path / "metadata" / "cache.json"
    monkeypatch.setattr(task_images, "_task_image_cache_metadata_path", lambda _key: metadata_path)
    monkeypatch.setattr(task_images, "_cached_task_image_is_ready", lambda **_kwargs: True)

    assert task_images._task_image_ready_for_reuse(request) is True
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["outcome"] == "hit"

    monkeypatch.setattr(task_images, "_cached_task_image_is_ready", lambda **_kwargs: False)
    assert task_images._task_image_ready_for_reuse(request) is False


def test_build_and_verify_task_image_requires_dockerfile(monkeypatch, tmp_path):
    request = task_images.TaskImageEnsureRequest(
        task_bundle_path=tmp_path / "bundle",
        image_ref=TaskImageRef("image:tag", "cache", "tag"),
        harness="codex-cli",
        run_env={},
        log_dir=tmp_path / "logs",
        task_timeout_sec=10,
    )

    with pytest.raises(FileNotFoundError, match="missing Dockerfile"):
        task_images._build_and_verify_task_image(request)

    context = request.task_bundle_path / "environment"
    context.mkdir(parents=True)
    (context / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    build_calls: list[list[str]] = []
    monkeypatch.setattr(
        task_images,
        "_run_task_image_build",
        lambda cmd, _env, timeout_sec: build_calls.append(cmd)
        or task_images.TaskImageBuildResult(
            completed_process=subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
        ),
    )
    preflight: list[str] = []
    monkeypatch.setattr(
        task_images,
        "_ensure_harbor_runtime_preflight",
        lambda **kwargs: preflight.append(kwargs["image_ref"].image_name),
    )

    task_images._build_and_verify_task_image(request)

    assert build_calls
    assert preflight == ["image:tag"]
