"""Tests for fast smoke-mode Harbor wiring."""

from pathlib import Path

from agentic_eval.harness.adapters.gemini_cli import GeminiCliAdapter
from agentic_eval.harness.config import Agent, HarnessConfig, ModelTarget
from agentic_eval.harness.fast_mode import harness_src_path


def _gemini_adapter(monkeypatch) -> GeminiCliAdapter:
    monkeypatch.setenv("GEMINI_CLI_PATH", "/usr/local/bin/gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    config = HarnessConfig(
        agent=Agent.GEMINI,
        model=ModelTarget(provider="google", name="gemini-3-flash-preview"),
    )
    return GeminiCliAdapter(config)


def test_fast_mode_uses_agent_import_path(monkeypatch) -> None:
    monkeypatch.setenv("HARBOR_SMOKE_FAST", "1")
    adapter = _gemini_adapter(monkeypatch)

    command = adapter.build_harbor_command(
        task_path=Path("/tmp/task"),
        job_name="job",
        jobs_dir=Path("/tmp/jobs"),
    )

    assert "--agent-import-path" in command
    assert "-a" not in command
    assert "agentic_eval.harness.harbor_agents.fast_cli_agents:FastGeminiCliAgent" in command


def test_fast_mode_runtime_env_includes_pythonpath(monkeypatch) -> None:
    monkeypatch.setenv("HARBOR_SMOKE_FAST", "1")
    adapter = _gemini_adapter(monkeypatch)

    env = adapter.runtime_env()

    assert "PYTHONPATH" in env
    assert str(harness_src_path()) in env["PYTHONPATH"]
