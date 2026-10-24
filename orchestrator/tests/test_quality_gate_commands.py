import click
import pytest
from click.testing import CliRunner

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

    calls: list[list[str]] = []
    monkeypatch.setattr(quality, "PYTHON", "python")
    monkeypatch.setattr(quality, "run_or_raise", lambda cmd, *_args, **_kwargs: calls.append(cmd))
    quality.run_ruff_quality_gates(fix=True)
    quality.run_ruff_quality_gates(fix=False)

    assert calls[0] == ["python", "-m", "ruff", "format", "--force-exclude"]
    assert calls[1] == ["python", "-m", "ruff", "check", ".", "--fix", "--force-exclude"]
    assert calls[2] == ["python", "-m", "ruff", "format", "--check", "--force-exclude"]
    assert calls[3] == ["python", "-m", "ruff", "check", ".", "--no-fix", "--force-exclude"]

    monkeypatch.setattr(quality, "validate_quality_gate_options", lambda **_kwargs: None)
    monkeypatch.setattr(quality, "assert_quality_gate_requirements", lambda: None)
    monkeypatch.setattr(quality, "run_ruff_quality_gates", lambda **_kwargs: None)
    monkeypatch.setattr(quality, "run_orchestrator_quality_gates", lambda: None)
    calls.clear()
    runner = CliRunner()
    result = runner.invoke(quality.quality_gates, ["--fix", "--stage"])
    assert result.exit_code == 0
    assert calls == [["git", "-C", str(quality.REPO_ROOT), "add", "-u"]]
