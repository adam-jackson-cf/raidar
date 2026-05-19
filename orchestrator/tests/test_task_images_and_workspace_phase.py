"""Focused Harbor runtime tests."""

# ruff: noqa: F403, F405
from tests.harbor_runtime_support import *  # noqa: F403


def test_ensure_task_image_writes_log_and_raises_on_build_failure(
    monkeypatch, tmp_path: Path
) -> None:
    bundle_path = tmp_path / "bundle"
    docker_context = bundle_path / "environment"
    docker_context.mkdir(parents=True, exist_ok=True)
    (docker_context / "Dockerfile").write_text("FROM oven/bun:1\n", encoding="utf-8")
    (docker_context / "app").mkdir(parents=True, exist_ok=True)
    (docker_context / "app" / "package.json").write_text("{}", encoding="utf-8")
    image_ref = runner.TaskImageRef(
        image_name="raidar-task-env:task-env-codex-cli-cachekey",
        cache_key="cachekey",
        tag="task-env-codex-cli-cachekey",
    )

    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")
    monkeypatch.setattr(
        runner,
        "_task_image_cache_metadata_path",
        lambda cache_key: tmp_path / "image-metadata" / f"{cache_key}.json",
    )
    monkeypatch.setattr(runner, "_task_image_cache_hit", lambda *_args, **_kwargs: False)

    def fake_run(command, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(command, 2, stdout="build-out", stderr="build-err")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="Task image build failed"):
        runner._ensure_task_image(
            runner.TaskImageEnsureRequest(
                task_bundle_path=bundle_path,
                image_ref=image_ref,
                harness="codex-cli",
                run_env={},
                log_dir=tmp_path / "logs",
                task_timeout_sec=300,
            )
        )

    build_log = tmp_path / "logs" / "task-image-build.log"
    assert build_log.exists()
    text = build_log.read_text(encoding="utf-8")
    assert "build-out" in text
    assert "build-err" in text


def test_task_image_reference_is_content_addressed_by_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIDAR_TASK_IMAGE_PREFIX", "custom-task-env")
    bundle_path = tmp_path / "bundle"
    app_dir = bundle_path / "environment" / "app"
    tests_dir = bundle_path / "tests"
    app_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    (bundle_path / "environment" / "Dockerfile").write_text("FROM oven/bun:1\n", encoding="utf-8")
    (app_dir / "package.json").write_text("{}", encoding="utf-8")
    (app_dir / "bun.lock").write_text("", encoding="utf-8")
    (tests_dir / "test.sh").write_text("echo first\n", encoding="utf-8")

    request = SimpleNamespace(
        config=SimpleNamespace(harness=SimpleNamespace(value="codex-cli")),
    )
    other_request = SimpleNamespace(
        config=SimpleNamespace(harness=SimpleNamespace(value="gemini")),
    )

    image_ref = runner._task_image_reference(request, bundle_path)
    other_ref = runner._task_image_reference(other_request, bundle_path)

    assert image_ref is not None
    assert image_ref.image_name.startswith("custom-task-env:task-env-codex-cli-")
    assert other_ref is not None
    assert other_ref.image_name.startswith("custom-task-env:task-env-gemini-")
    assert image_ref.cache_key != other_ref.cache_key

    (tests_dir / "test.sh").write_text("echo second\n", encoding="utf-8")
    changed_tests_ref = runner._task_image_reference(request, bundle_path)

    assert changed_tests_ref is not None
    assert changed_tests_ref.cache_key != image_ref.cache_key


def test_task_image_reference_respects_reuse_disable_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_path = tmp_path / "bundle"
    app_dir = bundle_path / "environment" / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (bundle_path / "environment" / "Dockerfile").write_text("FROM oven/bun:1\n", encoding="utf-8")
    (app_dir / "package.json").write_text("{}", encoding="utf-8")

    request = SimpleNamespace(
        config=SimpleNamespace(harness=SimpleNamespace(value="codex-cli")),
    )

    assert runner._task_image_reference(request, bundle_path) is not None

    monkeypatch.setenv("RAIDAR_TASK_IMAGE_REUSE", "0")

    assert runner._task_image_reference(request, bundle_path) is None


def test_task_image_build_command_uses_classic_docker_build(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM oven/bun:1\n", encoding="utf-8")
    image_ref = runner.TaskImageRef(
        image_name="raidar-task-env:task-env-codex-cli-cachekey",
        cache_key="cachekey",
        tag="task-env-codex-cli-cachekey",
    )

    command = runner._task_image_build_command(
        image_ref,
        dockerfile,
        tmp_path,
        harness="codex-cli",
    )

    assert command[:2] == ["docker", "build"]
    assert "--load" not in command


def test_render_environment_dockerfile_includes_visual_tooling_dependencies() -> None:
    request = SimpleNamespace(
        config=SimpleNamespace(harness=SimpleNamespace(value="codex-cli")),
        scenario=SimpleNamespace(visual=object()),
    )

    dockerfile = runner._render_environment_dockerfile(request)

    assert "git" in dockerfile
    assert "ripgrep" in dockerfile
    assert "file" in dockerfile
    assert "libatk-bridge2.0-0t64" in dockerfile
    assert "libcups2t64" in dockerfile
    assert "libcairo2" in dockerfile
    assert "libpango-1.0-0" in dockerfile
    assert dockerfile.index("RUN bunx playwright install chromium") < dockerfile.index(
        "COPY app/ /app/"
    )


def test_ensure_task_image_returns_immediately_when_image_exists(
    monkeypatch, tmp_path: Path
) -> None:
    bundle_path = tmp_path / "bundle"
    docker_context = bundle_path / "environment"
    docker_context.mkdir(parents=True, exist_ok=True)
    (docker_context / "Dockerfile").write_text("FROM oven/bun:1\n", encoding="utf-8")
    (docker_context / "app").mkdir(parents=True, exist_ok=True)
    (docker_context / "app" / "package.json").write_text("{}", encoding="utf-8")
    image_ref = runner.TaskImageRef(
        image_name="raidar-task-env:task-env-codex-cli-cachekey",
        cache_key="cachekey",
        tag="task-env-codex-cli-cachekey",
    )

    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")
    monkeypatch.setattr(
        runner,
        "_task_image_cache_metadata_path",
        lambda cache_key: tmp_path / "image-metadata" / f"{cache_key}.json",
    )
    monkeypatch.setattr(runner, "_task_image_cache_hit", lambda *_args, **_kwargs: True)
    preflight_calls: list[str] = []

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("subprocess.run should not be called when image already exists")

    monkeypatch.setattr(runner.subprocess, "run", fail_if_called)
    monkeypatch.setattr(
        runner,
        "_ensure_harbor_runtime_preflight",
        lambda *, image_ref, run_env, log_dir: preflight_calls.append(
            f"{image_ref.image_name}:{log_dir.name}"
        ),
    )

    cache_hit = runner._ensure_task_image(
        runner.TaskImageEnsureRequest(
            task_bundle_path=bundle_path,
            image_ref=image_ref,
            harness="codex-cli",
            run_env={},
            log_dir=tmp_path / "logs",
            task_timeout_sec=300,
        )
    )

    assert cache_hit is True
    assert preflight_calls == [f"{image_ref.image_name}:logs"]
    assert not (tmp_path / "logs" / "task-image-build.log").exists()


def test_ensure_task_image_rebuilds_when_cached_image_fails_runtime_preflight(
    monkeypatch, tmp_path: Path
) -> None:
    fixture = _task_image_fixture(tmp_path)
    _patch_task_image_cache(monkeypatch, tmp_path, cache_hit=True)

    build_calls: list[list[str]] = []

    def fake_build(build_cmd, run_env, *, timeout_sec):
        del run_env, timeout_sec
        build_calls.append(build_cmd)
        return runner.TaskImageBuildResult(
            completed_process=subprocess.CompletedProcess(
                build_cmd, 0, stdout="build-ok", stderr=""
            )
        )

    preflight_attempts: list[str] = []

    def fake_runtime_preflight(*, image_ref, run_env, log_dir):
        del run_env, log_dir
        preflight_attempts.append(image_ref.image_name)
        if len(preflight_attempts) < 3:
            raise RuntimeError("git missing")

    monkeypatch.setattr(runner, "_run_task_image_build", fake_build)
    monkeypatch.setattr(runner, "_ensure_harbor_runtime_preflight", fake_runtime_preflight)

    cache_hit = runner._ensure_task_image(_ensure_task_image_request(fixture, tmp_path))

    assert cache_hit is False
    assert len(build_calls) == 1
    assert len(preflight_attempts) == 3


def test_ensure_task_image_writes_log_and_raises_on_build_timeout(
    monkeypatch, tmp_path: Path
) -> None:
    fixture = _task_image_fixture(tmp_path, "timeout")
    _patch_task_image_cache(monkeypatch, tmp_path, cache_hit=False)

    def fake_build(build_cmd, run_env, *, timeout_sec):
        del run_env
        return runner.TaskImageBuildResult(
            completed_process=subprocess.CompletedProcess(
                build_cmd,
                returncode=124,
                stdout="partial-out",
                stderr="partial-err",
            ),
            timed_out=True,
            timeout_sec=timeout_sec,
        )

    monkeypatch.setattr(runner, "_run_task_image_build", fake_build)

    with pytest.raises(RuntimeError, match="Task image build timed out after 300s"):
        runner._ensure_task_image(_ensure_task_image_request(fixture, tmp_path))

    build_log = tmp_path / "logs" / "task-image-build.log"
    assert build_log.exists()
    text = build_log.read_text(encoding="utf-8")
    assert "partial-out" in text
    assert "partial-err" in text


def test_prepare_workspace_phase_reuses_prep_and_task_image_across_invocations(
    monkeypatch, tmp_path: Path
) -> None:
    scenario_dir = _prepare_workspace_scenario_dir(tmp_path)
    adapter = _ExecAdapterStub()
    request_one = _prepare_workspace_request(scenario_dir, tmp_path, "run-one")
    request_two = _prepare_workspace_request(scenario_dir, tmp_path, "run-two")

    patch_state = PrepareWorkspacePatchState(tmp_path)
    _patch_prepare_workspace_dependencies(monkeypatch, patch_state)
    monkeypatch.setattr(workspace_phase, "resolve_adapter", lambda _config: adapter)

    phase_one = workspace_phase.prepare_workspace_phase(request_one)
    phase_two = workspace_phase.prepare_workspace_phase(request_two)

    _assert_prepare_workspace_cache_reuse(phase_one, phase_two, patch_state)


def test_prepare_workspace_phase_validates_before_initializing_run(monkeypatch) -> None:
    class FailingAdapter:
        def validate(self) -> None:
            raise ValueError("invalid harness")

    def fail_initialize_run(_request):
        raise AssertionError("initialize_run should not run before adapter validation")

    monkeypatch.setattr(runner, "initialize_run", fail_initialize_run)
    monkeypatch.setattr(workspace_phase, "resolve_adapter", lambda _config: FailingAdapter())
    request = SimpleNamespace(config=SimpleNamespace())

    with pytest.raises(ValueError, match="invalid harness"):
        workspace_phase.prepare_workspace_phase(request)


def test_execute_harbor_phase_uses_empty_metrics_when_terminated_and_usage_missing(
    monkeypatch, tmp_path: Path
) -> None:
    request = SimpleNamespace(
        scenario=object(),
        config=SimpleNamespace(harness=SimpleNamespace(value="gemini")),
    )
    phase = SimpleNamespace(
        harbor_request=object(),
        layout=SimpleNamespace(start_time=datetime.now(UTC)),
    )
    harbor_result = runner.HarborExecutionResult(
        terminated_early=True,
        termination_reason="Harbor trial exception: Agent execution timed out after 300.0 seconds",
        job_dir=tmp_path / "jobs" / "orchestrator-run-01",
        trial_dir=tmp_path / "trial",
    )

    def fake_collect_process_metrics(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(
            "Missing token usage metrics for harness `gemini` in trial `/tmp/trial`."
        )

    monkeypatch.setattr(runner, "execute_harbor", lambda _request: harbor_result)
    monkeypatch.setattr(runner, "collect_process_metrics", fake_collect_process_metrics)
    monkeypatch.setattr(runner, "collect_trace_events", lambda *args, **kwargs: [])

    result = execution_phase.execute_harbor_phase(request, phase)

    assert result.terminated_early is True
    assert result.termination_reason == harbor_result.termination_reason
    assert result.process_metrics.uncached_input_tokens == 0
    assert result.process_metrics.output_tokens == 0
    assert result.process_metrics.command_count == 0


def test_execute_harbor_phase_recovers_timeout_when_verifier_outputs_exist(
    monkeypatch, tmp_path: Path
) -> None:
    request = SimpleNamespace(
        scenario=object(),
        config=SimpleNamespace(harness=SimpleNamespace(value="codex-cli")),
    )
    phase = SimpleNamespace(
        harbor_request=object(),
        layout=SimpleNamespace(start_time=datetime.now(UTC)),
    )
    trial_dir = tmp_path / "trial"
    trial_dir.mkdir(parents=True, exist_ok=True)
    harbor_result = runner.HarborExecutionResult(
        terminated_early=True,
        termination_reason="Timeout expired after 420s before trial result.json was written.",
        job_dir=tmp_path / "jobs" / "orchestrator-run-01",
        trial_dir=trial_dir,
    )

    monkeypatch.setattr(runner, "execute_harbor", lambda _request: harbor_result)
    monkeypatch.setattr(
        runner,
        "collect_process_metrics",
        lambda *args, **kwargs: runner._empty_process_metrics(),
    )
    monkeypatch.setattr(runner, "collect_trace_events", lambda *args, **kwargs: [])
    verifier_outputs = runner.terminated_outputs(None)
    monkeypatch.setattr(
        runner,
        "_load_verifier_outputs",
        lambda _trial_dir: (verifier_outputs, None),
    )

    result = execution_phase.execute_harbor_phase(request, phase)

    assert result.terminated_early is False
    assert result.termination_reason is None
    assert result.outputs == verifier_outputs


def test_execute_harbor_phase_raises_when_usage_missing_without_termination(
    monkeypatch, tmp_path: Path
) -> None:
    request = SimpleNamespace(
        scenario=object(),
        config=SimpleNamespace(harness=SimpleNamespace(value="gemini")),
    )
    phase = SimpleNamespace(
        harbor_request=object(),
        layout=SimpleNamespace(start_time=datetime.now(UTC)),
    )
    harbor_result = runner.HarborExecutionResult(
        terminated_early=False,
        termination_reason=None,
        job_dir=tmp_path / "jobs" / "orchestrator-run-01",
        trial_dir=tmp_path / "trial",
    )

    def fake_collect_process_metrics(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(
            "Missing token usage metrics for harness `gemini` in trial `/tmp/trial`."
        )

    monkeypatch.setattr(runner, "execute_harbor", lambda _request: harbor_result)
    monkeypatch.setattr(runner, "collect_process_metrics", fake_collect_process_metrics)
    monkeypatch.setattr(runner, "collect_trace_events", lambda *args, **kwargs: [])

    with pytest.raises(RuntimeError, match="Missing token usage metrics"):
        execution_phase.execute_harbor_phase(request, phase)


def test_execute_harbor_preserves_trial_dir_on_timeout(monkeypatch, tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "orchestrator-run-01"
    trial_dir = job_dir / "bundle__abc123"
    (trial_dir / "agent").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        runner,
        "_run_harbor_process",
        lambda _request: "Timeout expired after 420s before trial result.json was written.",
    )
    monkeypatch.setattr(runner, "cleanup_stale_harbor_resources", lambda: None)

    adapter = SimpleNamespace(
        build_harbor_command=lambda **kwargs: ["harbor", "run"],
    )
    request = runner.HarborExecutionRequest(
        adapter=adapter,
        workspace=tmp_path,
        task_bundle_path=tmp_path / "bundle",
        jobs_dir=jobs_dir,
        run_harbor_dir=tmp_path / "harbor",
        run_id="run-01",
        timeout_sec=420,
        run_env={},
    )

    result = runner.execute_harbor(request)

    assert result.terminated_early is True
    assert result.trial_dir == trial_dir
