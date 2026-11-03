"""Shared test configuration. Reusable builders live in the *_support.py modules."""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.contract import paths

pytest_plugins = ("tests.workflow_support", "tests.process_guard")


@pytest.fixture
def stage_check_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: pytest.FixtureRequest
) -> Path:
    """Redirect only stage evidence; unrelated lab definitions retain their normal roots."""
    stage_checks_root = paths.stage_checks_root
    repo = tmp_path / "repo"

    def isolated_root(repo_root: Path = repo) -> Path:
        return stage_checks_root(repo_root)

    monkeypatch.setattr(paths, "stage_checks_root", isolated_root)
    monkeypatch.setattr("raidar.cli.stage.stage_checks_root", isolated_root)
    monkeypatch.setattr("raidar.cli.stage_fixtures.stage_checks_root", isolated_root)
    monkeypatch.setattr("raidar.subject.workflow.package_runtime.stage_checks_root", isolated_root)
    monkeypatch.setattr(request.module, "stage_checks_root", isolated_root, raising=False)
    return isolated_root()


@pytest.fixture
def docker_target_offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CLI commands that select a Docker target resolve the file's default, without Docker.

    The targets file defines one trusted target ``offline`` on context ``test-ctx`` as its
    default; reachability is not probed (the test fakes the engine).
    """
    targets = tmp_path / "targets.toml"
    targets.write_text(
        'default = "offline"\n[targets.offline]\ndocker_context = "test-ctx"\ntrusted = true\n'
    )
    monkeypatch.setenv("RAIDAR_TARGETS_FILE", str(targets))
    monkeypatch.delenv("RAIDAR_TARGET", raising=False)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr("raidar.cli.target_options.ensure_target", lambda target: None)
