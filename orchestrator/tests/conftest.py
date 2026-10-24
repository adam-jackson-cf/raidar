"""Shared test configuration. Reusable builders live in the *_support.py modules."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def docker_target_offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CLI commands that select a Docker target resolve the file's default, without Docker.

    The targets file defines one trusted target ``offline`` on context ``test-ctx`` as its
    default; reachability is not probed (the test fakes the engine).
    """
    targets = tmp_path / "targets.toml"
    targets.write_text('default = "offline"\n[targets.offline]\ndocker_context = "test-ctx"\n')
    monkeypatch.setenv("RAIDAR_TARGETS_FILE", str(targets))
    monkeypatch.delenv("RAIDAR_TARGET", raising=False)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr("raidar.cli.target_options.ensure_target", lambda target: None)
