import click
import pytest

from raidar.cli import process, quality


def test_commands_run_through_one_runner_that_raises_on_failure(monkeypatch, tmp_path) -> None:
    calls: list[tuple[list[str], object]] = []

    class Result:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

    monkeypatch.setattr(
        process.subprocess,
        "run",
        lambda cmd, cwd, env, check: calls.append((cmd, env)) or Result(0),
    )
    process.run_or_raise(["tool", "ok"], tmp_path, env={"A": "1"})
    assert calls == [(["tool", "ok"], {"A": "1"})]

    monkeypatch.setattr(
        process.subprocess,
        "run",
        lambda cmd, cwd, env, check: Result(7),
    )
    with pytest.raises(click.ClickException, match="Command failed \\(7\\): tool fail"):
        process.run_or_raise(["tool", "fail"], tmp_path)


def test_quality_gate_helpers_validate_requirements_and_commands(monkeypatch, tmp_path) -> None:
    with pytest.raises(click.ClickException, match="--stage is only supported"):
        quality.validate_quality_gate_options(fix=False, stage=True)

    monkeypatch.setattr(quality.repo_state, "has_unstaged_changes", lambda _root: True)
    with pytest.raises(click.ClickException, match="Unstaged changes detected"):
        quality.validate_quality_gate_options(fix=True, stage=False)

    monkeypatch.setattr(
        quality.repo_state, "assert_no_generated_artifact_changes", lambda _root: None
    )
    monkeypatch.setattr(
        quality.shutil, "which", lambda command: None if command == "lizard" else "/bin/tool"
    )
    with pytest.raises(click.ClickException, match="Missing required command: lizard"):
        quality.assert_quality_gate_requirements()

    monkeypatch.setattr(
        quality.shutil, "which", lambda command: None if command == "lint-imports" else "/bin/tool"
    )
    with pytest.raises(click.ClickException, match="Missing required command: lint-imports"):
        quality.assert_quality_gate_requirements()
