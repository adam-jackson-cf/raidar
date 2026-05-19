"""Tests for Harbor runtime env and stale build cleanup behavior."""

import importlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

os = importlib.import_module("os")
signal = importlib.import_module("signal")
datetime_module = importlib.import_module("datetime")
UTC = datetime_module.UTC
datetime = datetime_module.datetime
agents_config = importlib.import_module("raidar.agents.config")
scenario_catalog = importlib.import_module("raidar.application.scenario_catalog")
artifacts_runtime = importlib.import_module("raidar.runtime.artifacts")
execution_phase = importlib.import_module("raidar.runtime.execution_phase")
harbor_cleanup = importlib.import_module("raidar.runtime.harbor_cleanup")
harbor_env = importlib.import_module("raidar.runtime.harbor_env")
harbor_execution = importlib.import_module("raidar.runtime.harbor_execution")
harbor_preflight = importlib.import_module("raidar.runtime.harbor_preflight")
harbor_results = importlib.import_module("raidar.runtime.harbor_results")
pipeline = importlib.import_module("raidar.runtime.pipeline")
starter_preflight = importlib.import_module("raidar.runtime.starter_preflight")
task_bundle = importlib.import_module("raidar.runtime.task_bundle")
task_images = importlib.import_module("raidar.runtime.task_images")
wait = importlib.import_module("raidar.runtime.wait")
workspace = importlib.import_module("raidar.runtime.workspace")
workspace_cache = importlib.import_module("raidar.runtime.workspace_cache")
workspace_phase = importlib.import_module("raidar.runtime.workspace_phase")
runtime_models = importlib.import_module("raidar.runtime.models")
process_metrics_runtime = importlib.import_module("raidar.runtime.process_metrics")
scorecard_runtime = importlib.import_module("raidar.runtime.scorecard")


class _RuntimeProxy:
    _modules = (
        harbor_execution,
        execution_phase,
        harbor_cleanup,
        harbor_env,
        harbor_preflight,
        harbor_results,
        starter_preflight,
        task_bundle,
        task_images,
        pipeline,
        workspace,
        workspace_cache,
        workspace_phase,
        artifacts_runtime,
        process_metrics_runtime,
        scorecard_runtime,
        runtime_models,
        wait,
        scenario_catalog,
        agents_config,
    )

    def __getattr__(self, name: str):
        for module in self._modules:
            if hasattr(module, name):
                return getattr(module, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value) -> None:
        patched = False
        for module in self._modules:
            if hasattr(module, name):
                setattr(module, name, value)
                patched = True
        if not patched:
            super().__setattr__(name, value)


runner = _RuntimeProxy()


class _AdapterStub:
    def __init__(
        self,
        *,
        import_path: str | None = None,
        excluded_keys: set[str] | None = None,
        local_secret_files: dict[str, Path] | None = None,
    ) -> None:
        self._import_path = import_path
        self._excluded_keys = excluded_keys or set()
        self._local_secret_files = local_secret_files or {}

    def runtime_env(self) -> dict[str, str]:
        return {"ADAPTER_FLAG": "1", "COMPOSE_BAKE": "1"}

    def harbor_harness_import_path(self) -> str | None:
        return self._import_path

    def excluded_run_env_keys(self) -> set[str]:
        return self._excluded_keys

    def local_secret_files(self) -> dict[str, Path]:
        return self._local_secret_files


@pytest.fixture
def repo_tmp_agentic_eval_home(monkeypatch, tmp_path: Path) -> Path:
    fake_home = tmp_path / "agentic-eval-home"
    fake_home.mkdir()
    monkeypatch.setattr(runner.Path, "home", classmethod(lambda cls: fake_home))
    return fake_home


def test_build_harbor_run_env_preserves_standard_harness_secrets(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    env = runner._build_harbor_run_env(_AdapterStub())

    assert env["ADAPTER_FLAG"] == "1"
    assert env["COMPOSE_BAKE"] == "false"
    assert env["OPENAI_API_KEY"] == "test-openai-key"
    assert "AGENTIC_EVAL_SECRET_FILE_OPENAI_API_KEY" not in env
    assert "DOCKER_CONFIG" not in env


def test_build_harbor_run_env_uses_secret_files_for_custom_harnesses(
    monkeypatch,
    repo_tmp_agentic_eval_home: Path,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    env = runner._build_harbor_run_env(
        _AdapterStub(import_path="raidar.agents.harbor_agents.cli_agents:CodexCliHarborAgent")
    )

    assert env["ADAPTER_FLAG"] == "1"
    assert env["COMPOSE_BAKE"] == "false"
    assert "OPENAI_API_KEY" not in env
    assert "AGENTIC_EVAL_SECRET_FILE_OPENAI_API_KEY" in env
    secret_file = runner.Path(env["AGENTIC_EVAL_SECRET_FILE_OPENAI_API_KEY"])
    assert secret_file.exists()
    assert secret_file.is_relative_to(repo_tmp_agentic_eval_home)
    assert secret_file.read_text(encoding="utf-8") == "test-openai-key"
    assert "DOCKER_CONFIG" not in env


def test_build_harbor_run_env_uses_local_secret_files_for_custom_harnesses(
    repo_tmp_agentic_eval_home: Path,
    tmp_path: Path,
) -> None:
    auth_json_path = tmp_path / "auth.json"
    auth_json_path.write_text('{"access_token":"token"}', encoding="utf-8")

    env = runner._build_harbor_run_env(
        _AdapterStub(
            import_path="raidar.agents.harbor_agents.cli_agents:CodexCliHarborAgent",
            local_secret_files={"CODEX_AUTH_JSON": auth_json_path},
        )
    )

    assert env["ADAPTER_FLAG"] == "1"
    secret_file = runner.Path(env["AGENTIC_EVAL_SECRET_FILE_CODEX_AUTH_JSON"])
    assert secret_file.exists()
    assert secret_file.is_relative_to(repo_tmp_agentic_eval_home)
    assert secret_file.read_text(encoding="utf-8") == '{"access_token":"token"}'


def test_build_harbor_run_env_excludes_adapter_blocked_env_keys(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    env = runner._build_harbor_run_env(
        _AdapterStub(
            import_path="raidar.agents.harbor_agents.cli_agents:CodexCliHarborAgent",
            excluded_keys={"OPENAI_API_KEY"},
        )
    )

    assert "OPENAI_API_KEY" not in env
    assert "AGENTIC_EVAL_SECRET_FILE_OPENAI_API_KEY" not in env


class _ExecAdapterStub:
    def build_harbor_command(self, *, task_path: Path, job_name: str, jobs_dir: Path) -> list[str]:
        del task_path, job_name, jobs_dir
        return ["harbor", "run"]

    def validate(self) -> None:
        return None

    def prepare_workspace(self, workspace: Path) -> None:
        del workspace

    def runtime_env(self) -> dict[str, str]:
        return {}

    def harbor_harness_import_path(self) -> str | None:
        return None

    def excluded_run_env_keys(self) -> set[str]:
        return set()

    def local_secret_files(self) -> dict[str, Path]:
        return {}

    def execution_metadata(self) -> dict[str, str]:
        return {}


@dataclass
class PrepareWorkspacePatchState:
    tmp_path: Path
    built_images: dict[str, dict[str, str]] = field(default_factory=dict)
    preflight_calls: list[str] = field(default_factory=list)
    runtime_preflight_calls: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TaskImageFixture:
    bundle_path: Path
    image_ref: runner.TaskImageRef


def _patch_prepare_workspace_dependencies(
    monkeypatch: pytest.MonkeyPatch, state: PrepareWorkspacePatchState
) -> None:
    monkeypatch.setattr(runner, "_raidar_cache_root", lambda: state.tmp_path / ".cache" / "raidar")
    monkeypatch.setattr(runner, "_maybe_run_cache_maintenance", lambda **_kwargs: None)
    monkeypatch.setattr(
        runner,
        "_run_starter_preflight_install",
        lambda workspace, env: state.preflight_calls.append(f"install:{workspace.name}"),
    )
    monkeypatch.setattr(
        runner,
        "_run_starter_preflight_command",
        lambda workspace, env, command: state.preflight_calls.append(
            f"{workspace.name}:{' '.join(command)}"
        ),
    )
    monkeypatch.setattr(
        runner,
        "_inspect_docker_image_labels",
        lambda image_name, run_env: state.built_images.get(image_name),
    )
    monkeypatch.setattr(
        runner,
        "_ensure_harbor_runtime_preflight",
        lambda *, image_ref, run_env, log_dir: state.runtime_preflight_calls.append(
            image_ref.image_name
        ),
    )

    def fake_run_task_image_build(
        build_cmd: list[str], run_env: dict[str, str], *, timeout_sec: int
    ):
        del run_env, timeout_sec
        image_name = build_cmd[build_cmd.index("--tag") + 1]
        labels: dict[str, str] = {}
        label_indices = [idx for idx, token in enumerate(build_cmd) if token == "--label"]
        for idx in label_indices:
            key, value = build_cmd[idx + 1].split("=", 1)
            labels[key] = value
        state.built_images[image_name] = labels
        return runner.TaskImageBuildResult(
            completed_process=subprocess.CompletedProcess(build_cmd, 0, stdout="built", stderr="")
        )

    monkeypatch.setattr(runner, "_run_task_image_build", fake_run_task_image_build)


def _starter_preflight_request(tmp_path: Path):
    task_dir = tmp_path / "scenario"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "scenario.yaml").write_text(
        "name: sample\nscenario_revision: v001\n", encoding="utf-8"
    )
    return SimpleNamespace(
        scenario=SimpleNamespace(
            verification=SimpleNamespace(required_commands=[["bun", "run", "lint"]])
        ),
        config=SimpleNamespace(harness=SimpleNamespace(value="codex-cli")),
        scenario_dir=task_dir,
    )


def _starter_preflight_context(tmp_path: Path, name: str):
    workspace = tmp_path / name
    workspace.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        workspace=workspace,
        baseline_cache_key="baseline-cache-key",
        starter_source=SimpleNamespace(fingerprint="abc123"),
    )


def _patch_starter_preflight_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        runner,
        "_preflight_cache_file",
        lambda cache_key: tmp_path / "preflight" / f"{cache_key}.ok.json",
    )
    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")
    monkeypatch.setattr(runner, "_workspace_has_tests", lambda _workspace: True)


def _task_image_fixture(tmp_path: Path, tag_suffix: str = "cachekey") -> TaskImageFixture:
    bundle_path = tmp_path / "bundle"
    docker_context = bundle_path / "environment"
    docker_context.mkdir(parents=True, exist_ok=True)
    (docker_context / "Dockerfile").write_text("FROM oven/bun:1\n", encoding="utf-8")
    (docker_context / "app").mkdir(parents=True, exist_ok=True)
    (docker_context / "app" / "package.json").write_text("{}", encoding="utf-8")
    return TaskImageFixture(
        bundle_path=bundle_path,
        image_ref=runner.TaskImageRef(
            image_name=f"raidar-task-env:task-env-codex-cli-{tag_suffix}",
            cache_key=f"{tag_suffix}-key" if tag_suffix == "timeout" else tag_suffix,
            tag=f"task-env-codex-cli-{tag_suffix}",
        ),
    )


def _patch_task_image_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, cache_hit: bool
) -> None:
    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")
    monkeypatch.setattr(
        runner,
        "_task_image_cache_metadata_path",
        lambda cache_key: tmp_path / "image-metadata" / f"{cache_key}.json",
    )
    monkeypatch.setattr(runner, "_task_image_cache_hit", lambda *_args, **_kwargs: cache_hit)


def _ensure_task_image_request(fixture: TaskImageFixture, tmp_path: Path):
    return runner.TaskImageEnsureRequest(
        task_bundle_path=fixture.bundle_path,
        image_ref=fixture.image_ref,
        harness="codex-cli",
        run_env={},
        log_dir=tmp_path / "logs",
        task_timeout_sec=300,
    )


def _prepare_workspace_scenario_dir(tmp_path: Path) -> Path:
    scenario_dir = tmp_path / "scenarios" / "hello-world-smoke" / "v001"
    starter_dir = scenario_dir / "starter"
    prompt_dir = scenario_dir / "prompt"
    (starter_dir / "src").mkdir(parents=True, exist_ok=True)
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (starter_dir / "package.json").write_text(
        json.dumps({"scripts": {"lint": "echo lint"}}), encoding="utf-8"
    )
    (starter_dir / "bun.lock").write_text("", encoding="utf-8")
    (starter_dir / "src" / "index.tsx").write_text(
        "export const App = () => null;\n", encoding="utf-8"
    )
    (prompt_dir / "task.md").write_text("Print hello world\n", encoding="utf-8")
    (scenario_dir / "scenario.yaml").write_text(_prepare_workspace_scenario_yaml())
    return scenario_dir


def _prepare_workspace_scenario_yaml() -> str:
    return "\n".join(
        [
            "name: hello-world-smoke",
            "scenario_revision: v001",
            "description: smoke task",
            "difficulty: easy",
            "category: greenfield-ui",
            "timeout_sec: 1800",
            "starter:",
            "  root: starter",
            "verification:",
            "  gates: []",
            "  required_commands:",
            "    - [bun, run, lint]",
            "acceptance: {}",
            "metrics:",
            "  - {type: core, id: functional}",
            "  - {type: core, id: acceptance}",
            "  - {type: core, id: verification-stability}",
            "  - {type: core, id: execution-validity}",
            "  - {type: core, id: resource-efficiency}",
            "prompt:",
            "  entry: prompt/task.md",
            "",
        ]
    )


def _prepare_workspace_request(scenario_dir: Path, tmp_path: Path, run_name: str):
    scenario = runner.load_scenario(scenario_dir / "scenario.yaml")
    return runner.RunRequest(
        scenario=scenario,
        config=SimpleNamespace(harness=runner.Harness.CODEX_CLI, timeout_sec=300),
        scenario_dir=scenario_dir,
        execution_dir=tmp_path / "experiments" / run_name,
        repeat_index=1,
    )


def _assert_prepare_workspace_cache_reuse(
    phase_one, phase_two, patch_state: PrepareWorkspacePatchState
) -> None:
    assert phase_one.cache_metadata["baseline"]["hit"] is False
    assert phase_one.cache_metadata["baseline"]["status"] == "miss"
    assert phase_two.cache_metadata["baseline"]["hit"] is True
    assert phase_two.cache_metadata["baseline"]["status"] == "hit"
    assert phase_one.cache_metadata["baseline"]["metadata_path"].endswith("metadata.json")
    assert phase_two.cache_metadata["baseline"]["complete"] is True
    assert phase_one.cache_metadata["preflight"]["hit"] is False
    assert phase_two.cache_metadata["preflight"]["hit"] is True
    assert phase_one.cache_metadata["image"]["hit"] is False
    assert phase_two.cache_metadata["image"]["hit"] is True
    assert (phase_one.layout.harbor_dir / "task-image-build.log").exists()
    assert not (phase_two.layout.harbor_dir / "task-image-build.log").exists()
    assert len(patch_state.preflight_calls) == 2
    install_name = patch_state.preflight_calls[0].removeprefix("install:")
    command_name, command_text = patch_state.preflight_calls[1].split(":", 1)
    assert install_name
    assert install_name == command_name
    assert command_text == "bun run lint"
    assert phase_one.cache_metadata["image_key"] == phase_two.cache_metadata["image_key"]
    assert phase_one.cache_metadata["image_tag"] == phase_two.cache_metadata["image_tag"]
    _assert_runtime_preflight_image(phase_one, patch_state)


def _assert_runtime_preflight_image(phase_one, patch_state: PrepareWorkspacePatchState) -> None:
    expected_image = f"{runner.task_image_prefix()}:{phase_one.cache_metadata['image_tag']}"
    assert patch_state.runtime_preflight_calls
    assert all(image_name == expected_image for image_name in patch_state.runtime_preflight_calls)


__all__ = [name for name in globals() if not name.startswith("__")]
