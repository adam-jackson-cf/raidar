"""Tests for canonical Harbor routing."""

# ruff: noqa: F403, F405
import sys
from pathlib import Path

from raidar.agents.adapters.base import harbor_executable
from raidar.agents.adapters.gemini_cli import GeminiCliAdapter
from raidar.agents.config import AgentSpec, Harness, ModelTarget
from raidar.agents.harbor_routing import harness_src_path


def _gemini_adapter(monkeypatch) -> GeminiCliAdapter:
    monkeypatch.setenv("GEMINI_CLI_PATH", "/usr/local/bin/gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    config = AgentSpec(
        harness=Harness.GEMINI,
        model=ModelTarget(provider="google", name="gemini-3-flash-preview"),
    )
    return GeminiCliAdapter(config)


def test_harbor_routing_uses_harness_import_path(monkeypatch) -> None:
    adapter = _gemini_adapter(monkeypatch)

    command = adapter.build_harbor_command(
        task_path=Path("/tmp/scenario"),
        job_name="job",
        jobs_dir=Path("/tmp/jobs"),
    )

    # Harbor >=0.23 removed --agent-import-path and --no-export-traces.
    assert "--agent-import-path" not in command
    assert "--no-export-traces" not in command
    agent_index = command.index("--agent")
    assert command[agent_index + 1] == "raidar.agents.harbor_agents.cli_agents:GeminiCliHarborAgent"
    assert "--quiet" in command
    assert "--no-delete" in command


def test_harbor_command_uses_project_pinned_harbor(monkeypatch) -> None:
    adapter = _gemini_adapter(monkeypatch)

    command = adapter.build_harbor_command(task_path=Path("/tmp/scenario"))

    assert command[0] == harbor_executable()
    assert Path(command[0]).parent == Path(sys.executable).parent
    assert command[1] == "run"


def test_builtin_harness_uses_agent_name(monkeypatch) -> None:
    adapter = _gemini_adapter(monkeypatch)
    monkeypatch.setattr(adapter, "harbor_harness_import_path", lambda: None)

    command = adapter.build_harbor_command(task_path=Path("/tmp/scenario"))

    assert command[command.index("--agent") + 1] == "gemini-cli"


def test_harbor_routing_runtime_env_includes_pythonpath(monkeypatch) -> None:
    adapter = _gemini_adapter(monkeypatch)

    env = adapter.runtime_env()

    assert "PYTHONPATH" in env
    assert str(harness_src_path()) in env["PYTHONPATH"]


def test_codex_harbor_agent_disables_plugins() -> None:
    source = (harness_src_path() / "raidar/agents/harbor_agents/cli_agents.py").read_text(
        encoding="utf-8"
    )

    assert "codex exec --ignore-user-config --ephemeral " in source
    assert "--disable plugins " in source
    assert "--skip-git-repo-check --cd /app --json " in source
    assert "> /logs/agent/codex.txt 2>&1 </dev/null" in source
    assert "| tee /logs/agent/codex.txt" not in source
