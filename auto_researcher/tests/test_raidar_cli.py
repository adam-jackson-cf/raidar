"""Tests for the public Raidar CLI wrapper."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from auto_researcher.raidar_cli import RaidarCli
from auto_researcher.storage import WorkspaceLayout


def test_run_unsets_virtual_env(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _fake_run(*args, **kwargs):
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setenv("VIRTUAL_ENV", "/tmp/auto-researcher-venv")

    client = RaidarCli(layout=WorkspaceLayout(tmp_path))
    client._run("scenario", "list")  # noqa: SLF001

    env = captured["env"]
    assert isinstance(env, dict)
    assert "VIRTUAL_ENV" not in env
    assert os.environ["VIRTUAL_ENV"] == "/tmp/auto-researcher-venv"
