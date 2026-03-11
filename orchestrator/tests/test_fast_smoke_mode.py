"""Tests for fast smoke-mode Harbor wiring."""

from pathlib import Path

from raidar.agents.adapters.gemini_cli import GeminiCliAdapter
from raidar.agents.config import AgentSpec, Harness, ModelTarget
from raidar.agents.fast_mode import harness_src_path


def _gemini_adapter(monkeypatch) -> GeminiCliAdapter:
    monkeypatch.setenv("GEMINI_CLI_PATH", "/usr/local/bin/gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    config = AgentSpec(
        harness=Harness.GEMINI,
        model=ModelTarget(provider="google", name="gemini-3-flash-preview"),
    )
    return GeminiCliAdapter(config)


def test_fast_mode_uses_harness_import_path(monkeypatch) -> None:
    monkeypatch.setenv("HARBOR_SMOKE_FAST", "1")
    adapter = _gemini_adapter(monkeypatch)

    command = adapter.build_harbor_command(
        task_path=Path("/tmp/scenario"),
        job_name="job",
        jobs_dir=Path("/tmp/jobs"),
    )

    assert "--agent-import-path" in command
    assert "-a" not in command
    assert "raidar.agents.harbor_agents.fast_cli_agents:FastGeminiCliAgent" in command


def test_fast_mode_runtime_env_includes_pythonpath(monkeypatch) -> None:
    monkeypatch.setenv("HARBOR_SMOKE_FAST", "1")
    adapter = _gemini_adapter(monkeypatch)

    env = adapter.runtime_env()

    assert "PYTHONPATH" in env
    assert str(harness_src_path()) in env["PYTHONPATH"]
