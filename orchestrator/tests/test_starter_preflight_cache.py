"""Focused Harbor runtime tests."""

# ruff: noqa: F403, F405
from tests.harbor_runtime_support import *  # noqa: F403


def test_ensure_starter_preflight_skips_test_command_without_tests(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace"
    task_dir = tmp_path / "scenario"
    workspace.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "scenario.yaml").write_text(
        "name: sample\nscenario_revision: v001\n", encoding="utf-8"
    )

    request = SimpleNamespace(
        scenario=SimpleNamespace(
            name="sample-task",
            verification=SimpleNamespace(
                required_commands=[["bun", "run", "test"], ["bun", "run", "lint"]]
            ),
        ),
        config=SimpleNamespace(harness=SimpleNamespace(value="codex-cli")),
        scenario_dir=task_dir,
    )
    context = SimpleNamespace(
        workspace=workspace,
        baseline_cache_key="baseline-cache-key",
        starter_source=SimpleNamespace(fingerprint="abc123"),
    )

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        del kwargs
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(
        runner,
        "_preflight_cache_file",
        lambda cache_key: tmp_path / "preflight" / f"{cache_key}.ok.json",
    )
    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")
    monkeypatch.setattr(runner, "_workspace_has_tests", lambda _workspace: False)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    runner.ensure_starter_preflight(request, context)

    assert calls == [["bun", "install", "--frozen-lockfile"], ["bun", "run", "lint"]]


def test_ensure_starter_preflight_reuses_repo_local_cache_across_invocations(
    monkeypatch, tmp_path: Path
) -> None:
    request = _starter_preflight_request(tmp_path)
    context_one = _starter_preflight_context(tmp_path, "workspace-one")
    context_two = _starter_preflight_context(tmp_path, "workspace-two")

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        del kwargs
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    _patch_starter_preflight_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    first_hit = runner.ensure_starter_preflight(request, context_one)
    second_hit = runner.ensure_starter_preflight(request, context_two)

    assert first_hit is False
    assert second_hit is True
    assert calls == [["bun", "install", "--frozen-lockfile"], ["bun", "run", "lint"]]


def test_ensure_starter_preflight_uses_workspace_local_runtime_env(
    monkeypatch, tmp_path: Path
) -> None:
    request = _starter_preflight_request(tmp_path)
    context = _starter_preflight_context(tmp_path, "workspace")

    envs: list[dict[str, str]] = []

    def fake_run(command, **kwargs):
        envs.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    _patch_starter_preflight_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    runner.ensure_starter_preflight(request, context)

    assert envs
    preflight_workspace = Path(envs[0]["TMPDIR"]).parent
    assert preflight_workspace != context.workspace
    assert preflight_workspace.parent == tmp_path / "preflight"

    expected_tmp = preflight_workspace / ".tmp"
    expected_cache = preflight_workspace / ".cache"
    expected_uv_cache = expected_cache / "uv"
    expected_bun_cache = expected_cache / "bun"

    for env in envs:
        assert env["TMPDIR"] == str(expected_tmp)
        assert env["TMP"] == str(expected_tmp)
        assert env["TEMP"] == str(expected_tmp)
        assert env["XDG_CACHE_HOME"] == str(expected_cache)
        assert env["UV_CACHE_DIR"] == str(expected_uv_cache)
        assert env["BUN_INSTALL_CACHE_DIR"] == str(expected_bun_cache)

    assert not preflight_workspace.exists()


def test_ensure_starter_preflight_runs_against_baseline_workspace(
    monkeypatch, tmp_path: Path
) -> None:
    task_dir = tmp_path / "scenario"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "scenario.yaml").write_text(
        "name: sample\nscenario_revision: v001\n", encoding="utf-8"
    )

    request = SimpleNamespace(
        scenario=SimpleNamespace(
            verification=SimpleNamespace(required_commands=[["bun", "run", "lint"]])
        ),
        config=SimpleNamespace(harness=SimpleNamespace(value="codex-cli")),
        scenario_dir=task_dir,
    )
    baseline_workspace = tmp_path / "baseline-workspace"
    run_workspace = tmp_path / "run-workspace"
    baseline_workspace.mkdir(parents=True, exist_ok=True)
    run_workspace.mkdir(parents=True, exist_ok=True)
    (baseline_workspace / "package.json").write_text("{}", encoding="utf-8")
    context = SimpleNamespace(
        workspace=run_workspace,
        baseline_workspace=baseline_workspace,
        baseline_cache_key="baseline-cache-key",
        starter_source=SimpleNamespace(fingerprint="abc123"),
    )

    called_workspaces: list[Path] = []

    def fake_install(workspace: Path, env: dict[str, str]) -> None:
        del env
        called_workspaces.append(workspace)
        (workspace / "node_modules").mkdir()
        (workspace / "node_modules" / "installed.txt").write_text("ok", encoding="utf-8")

    def fake_command(workspace: Path, env: dict[str, str], command: list[str]) -> None:
        del env, command
        called_workspaces.append(workspace)
        (workspace / "command-ran.txt").write_text("ok", encoding="utf-8")

    monkeypatch.setattr(
        runner,
        "_preflight_cache_file",
        lambda cache_key: tmp_path / "preflight" / f"{cache_key}.ok.json",
    )
    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")
    monkeypatch.setattr(runner, "_workspace_has_tests", lambda _workspace: True)
    monkeypatch.setattr(runner, "_run_starter_preflight_install", fake_install)
    monkeypatch.setattr(runner, "_run_starter_preflight_command", fake_command)

    runner.ensure_starter_preflight(request, context)

    assert len(called_workspaces) == 2
    assert called_workspaces[0] == called_workspaces[1]
    preflight_workspace = called_workspaces[0]
    assert preflight_workspace != baseline_workspace
    assert preflight_workspace != run_workspace
    assert preflight_workspace.parent == tmp_path / "preflight"
    assert not preflight_workspace.exists()
    assert not (baseline_workspace / "node_modules").exists()
    assert not (baseline_workspace / "command-ran.txt").exists()


def test_cache_key_lock_reclaims_dead_owner_immediately(tmp_path: Path, monkeypatch) -> None:
    lock_root = tmp_path / "locks"
    lock_dir = lock_root / "image-dead.lock"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "owner.json").write_text(
        json.dumps({"pid": 999999, "created_at": "2026-04-10T00:00:00+00:00"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(runner, "_cache_lock_root", lambda: lock_root)

    with runner._cache_key_lock("image-dead", timeout_sec=1):
        assert lock_dir.exists()
        payload = json.loads((lock_dir / "owner.json").read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()

    assert not lock_dir.exists()
