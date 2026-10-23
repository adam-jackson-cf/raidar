"""Shared test configuration. Reusable builders live in the *_support.py modules."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def docker_target_offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CLI commands that select a Docker target resolve the implicit one, without Docker.

    No targets file, nothing selected: the implicit target names the context in
    ``DOCKER_CONTEXT``; reachability is not probed (the test fakes the engine).
    """
    monkeypatch.setenv("RAIDAR_TARGETS_FILE", str(tmp_path / "no-targets.toml"))
    monkeypatch.delenv("RAIDAR_TARGET", raising=False)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setenv("DOCKER_CONTEXT", "test-ctx")
    monkeypatch.setattr("raidar.commands.target_options.ensure_target", lambda target: None)
