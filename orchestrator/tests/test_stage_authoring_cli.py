"""Authoring commands of ``stage/1``: tier-2 templates, locks and host isolation."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner
from raidar_contract.package.fixtures import required_cases, static_problems
from raidar_contract.package.registry import load_stage_package

from raidar.cli import main, stage, stage_fixtures, stage_lock, stage_package, workflow_new
from raidar.cli.stage_template import LOCK as TEMPLATE_LOCK
from raidar.cli.stage_template import render_template
from raidar.contract.wheels import uv_lock_refusals
from raidar.subject.workflow import stage_check

# Model uv's sdist metadata as well as the generated template's pinned wheel.
SDIST = (
    f'sdist = {{ url = "https://files.pythonhosted.org/x.tar.gz", hash = "sha256:{"0" * 64}" }}\n'
)
LOCK = TEMPLATE_LOCK.replace("wheels = [", SDIST + "wheels = [")


@pytest.fixture
def stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "stages"
    monkeypatch.setattr(stage_package, "stages_root", lambda: root)
    return root


def _write(root: Path, files: dict[str, bytes]) -> None:
    for relative, data in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def test_stage_new_writes_a_command_only_package_that_meets_the_fixture_contract(
    stages: Path,
) -> None:
    result = CliRunner().invoke(main, ["stage", "new", "acme/my-check"])

    assert result.exit_code == 0, result.output
    package = load_stage_package(stages / "acme" / "my-check")
    manifest = package.manifest
    assert static_problems(package) == []
    assert set(required_cases(package)) == {"pass", "refuse"}
    assert manifest.setup is None
    assert manifest.on_stop is None
    assert not manifest.outputs
    assert manifest.tool is not None
    assert manifest.tool.commands
    assert [item.path for item in package.files if item.path.startswith("tool/src")] == []
    assert not any(item.path.endswith(".py") for item in package.files)
    assert (stages / "acme" / "my-check" / "tool" / "uv.lock").is_file()


def test_the_template_pins_one_third_party_command_and_ships_adversarial_gate_fixtures(
    stages: Path,
) -> None:
    CliRunner().invoke(main, ["stage", "new", "acme/my-check"])
    package = load_stage_package(stages / "acme" / "my-check")

    pyproject = (stages / "acme" / "my-check" / "tool" / "pyproject.toml").read_text()
    statuses = {
        case: yaml.safe_load((package.fixture_dir(case) / "expect.yaml").read_text())["status"]
        for case in package.fixture_cases()
    }

    assert 'dependencies = ["codespell==2.4.1"]' in pyproject
    assert {"pass", "refuse", "nested-misspelling", "zero-byte-file", "binary-file"} <= set(
        statuses
    )
    assert statuses["pass"] == "pass"
    assert statuses["refuse"] == "refuse"
    assert set(statuses.values()) == {"pass", "refuse"}
    assert any(
        "fixtures/" in str(path) for path in (stages / "acme" / "my-check").rglob("expect.yaml")
    )
    assert all("fixtures/" not in item.path for item in package.files)


def test_every_new_package_carries_the_same_wheels_only_lock_unchanged(stages: Path) -> None:
    CliRunner().invoke(main, ["stage", "new", "acme/first-check"])
    CliRunner().invoke(main, ["stage", "new", "acme/second-check"])

    locks = [
        (stages / name / "tool" / "uv.lock").read_bytes()
        for name in ("acme/first-check", "acme/second-check")
    ]

    assert locks[0] == locks[1] == TEMPLATE_LOCK.encode()
    assert b"sdist" not in locks[0]
    assert uv_lock_refusals(locks[0].decode()) == []


@pytest.mark.parametrize("template", ["docs-check", None])
def test_stage_new_copies_the_docs_check_template(stages: Path, template: str | None) -> None:
    extra = [] if template is None else ["--from", template]

    result = CliRunner().invoke(main, ["stage", "new", "acme/my-check", *extra])

    assert result.exit_code == 0, result.output
    assert "from docs-check" in result.output


def test_stage_new_refuses_an_unknown_template_and_writes_nothing(stages: Path) -> None:
    result = CliRunner().invoke(main, ["stage", "new", "acme/my-check", "--from", "nope"])

    assert result.exit_code != 0
    assert "available: docs-check" in result.output
    assert not (stages / "acme" / "my-check").exists()


def test_stage_new_supports_a_namespace_and_refuses_to_overwrite(stages: Path) -> None:
    first = CliRunner().invoke(main, ["stage", "new", "acme/review-notes"])
    again = CliRunner().invoke(main, ["stage", "new", "acme/review-notes"])

    assert first.exit_code == 0, first.output
    load_stage_package(stages / "acme" / "review-notes")
    assert again.exit_code != 0
    assert "refusing to overwrite" in again.output


@pytest.mark.parametrize("name", ["Bad", "-x", "a b", "a/b/c", "", "my-check", "acme/"])
def test_stage_new_refuses_a_bad_name(stages: Path, name: str) -> None:
    result = CliRunner().invoke(main, ["stage", "new", name])

    assert result.exit_code != 0
    assert not stages.exists()


@pytest.mark.parametrize("command", ["lock", "info", "check"])
def test_every_package_command_refuses_a_name_without_a_namespace(
    stages: Path, command: str
) -> None:
    CliRunner().invoke(main, ["stage", "new", "acme/my-check"])

    result = CliRunner().invoke(main, ["stage", command, "my-check"])

    assert result.exit_code != 0
    assert "<ns>/<name>" in result.output


@pytest.mark.parametrize("case", ["pass", "refuse"])
def test_a_missing_mandatory_fixture_is_a_static_problem(stages: Path, case: str) -> None:
    CliRunner().invoke(main, ["stage", "new", "acme/my-check"])
    shutil.rmtree(stages / "acme" / "my-check" / "fixtures" / case)

    problems = static_problems(load_stage_package(stages / "acme" / "my-check"))

    assert any(f"missing mandatory fixture {case!r}" in problem for problem in problems)


def test_a_wrong_expected_status_for_a_mandatory_fixture_is_a_static_problem(
    stages: Path,
) -> None:
    CliRunner().invoke(main, ["stage", "new", "acme/my-check"])
    expect = stages / "acme" / "my-check" / "fixtures" / "refuse" / "expect.yaml"
    expect.write_text("status: pass\n")

    problems = static_problems(load_stage_package(stages / "acme" / "my-check"))

    assert any("'refuse' must expect status 'refuse'" in problem for problem in problems)


def test_a_stop_result_fixture_needs_a_stop(stages: Path) -> None:
    CliRunner().invoke(main, ["stage", "new", "acme/my-check"])
    expect = stages / "acme" / "my-check" / "fixtures" / "binary-file" / "expect.yaml"
    expect.write_text("status: done\n")

    problems = static_problems(load_stage_package(stages / "acme" / "my-check"))

    assert any("expects a stop result but the package has no stop" in item for item in problems)


def test_stage_list_and_info_show_the_package_and_its_next_register_line(
    stages: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stage, "lock_problems", lambda root: [])
    CliRunner().invoke(main, ["stage", "new", "acme/my-check"])
    package = load_stage_package(stages / "acme" / "my-check")

    listed = CliRunner().invoke(main, ["stage", "list"])
    info = CliRunner().invoke(main, ["stage", "info", "acme/my-check"])

    assert listed.exit_code == 0, listed.output
    assert "acme/my-check" in listed.output
    assert "unreleased" in listed.output
    document: dict[str, Any] = json.loads(info.output.replace("\r", ""))
    assert document["digest"] == package.digest
    assert document["register"] == f"register stages: acme/my-check@1: {package.digest}"
    assert document["fixtures"] == sorted(package.fixture_cases())


def test_workflow_new_defaults_to_one_shot_verify_and_refuses_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workflows"
    template = root / "one-shot-verify"
    template.mkdir(parents=True)
    (template / "workflow.yaml").write_text("schema: 1\nname: one-shot-verify\nstages: {}\n")
    monkeypatch.setattr(workflow_new, "workflows_root", lambda: root)

    first = CliRunner().invoke(main, ["workflow", "new", "mine"])
    again = CliRunner().invoke(main, ["workflow", "new", "mine"])

    assert first.exit_code == 0, first.output
    assert yaml.safe_load((root / "mine" / "workflow.yaml").read_text())["name"] == "mine"
    assert again.exit_code != 0
    assert "refusing to overwrite" in again.output


def test_workflow_new_refuses_an_unknown_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workflow_new, "workflows_root", lambda: tmp_path)

    result = CliRunner().invoke(main, ["workflow", "new", "mine", "--from", "missing"])

    assert result.exit_code != 0
    assert "no workflow template" in result.output


def test_a_lock_with_an_sdist_is_a_lock_problem(tmp_path: Path) -> None:
    root = tmp_path / "acme" / "pkg"
    _write(root, render_template("acme/pkg"))
    (root / "tool" / "uv.lock").write_text(LOCK)

    problems = stage_lock.lock_problems(root)

    assert any("sdist" in problem for problem in problems)


def test_stage_check_never_runs_package_code_on_the_host(
    stages: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V4: with no container, nothing at all executes."""
    CliRunner().invoke(main, ["stage", "new", "acme/my-check"])
    executed: list[Any] = []

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        executed.append(args)
        raise AssertionError(f"host execution: {args}")

    class NoDocker:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            raise RuntimeError("no docker in this test")

        async def __aexit__(self, *exc: Any) -> None:
            return None

    for name in ("run", "Popen", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(stage_fixtures, "lock_problems", lambda root: [])
    monkeypatch.setattr(stage_fixtures, "_pack", lambda target: ("image", "context", "pack"))
    monkeypatch.setattr(stage_check, "PackContainer", NoDocker)

    result = CliRunner().invoke(main, ["stage", "check", "acme/my-check"])

    assert result.exit_code != 0
    assert "stage-check could not complete" in result.output
    assert executed == []


def test_stage_check_with_a_static_problem_never_creates_a_container(
    stages: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    CliRunner().invoke(main, ["stage", "new", "acme/my-check"])
    expect = stages / "acme" / "my-check" / "fixtures" / "refuse" / "expect.yaml"
    expect.write_text("status: pass\n")
    created: list[str] = []

    class Recording:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            created.append("container")

    monkeypatch.setattr(stage_fixtures, "lock_problems", lambda root: [])
    monkeypatch.setattr(stage_fixtures, "_pack", lambda target: ("image", "context", "pack"))
    monkeypatch.setattr(stage_check, "PackContainer", Recording)

    result = CliRunner().invoke(main, ["stage", "check", "acme/my-check"])

    assert result.exit_code != 0
    assert "must expect status" in result.output
    assert created == []


def _tripwire(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every way a host process could start, recorded and refused."""
    import os
    import runpy

    attempts: list[Any] = []

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        attempts.append(args)
        raise AssertionError(f"host execution: {args}")

    for owner, names in (
        (subprocess, ("run", "Popen", "call", "check_call", "check_output")),
        (os, ("system", "execv", "execve", "execvp", "spawnv", "posix_spawn")),
        (asyncio, ("create_subprocess_exec", "create_subprocess_shell")),
        (runpy, ("run_path", "run_module")),
    ):
        for name in names:
            monkeypatch.setattr(owner, name, forbidden)
    return attempts


def test_no_authoring_path_runs_package_code_on_the_host(
    stages: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V4: new, list, info, lock (resolution only) and static checks start no host process."""
    CliRunner().invoke(main, ["stage", "new", "acme/my-check"])
    attempts = _tripwire(monkeypatch)
    resolved: list[Any] = []

    def uv(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
        resolved.append(args)
        (project / "uv.lock").write_text(LOCK)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(stage_lock, "_uv", uv)

    locked = CliRunner().invoke(main, ["stage", "lock", "acme/my-check"])
    listed = CliRunner().invoke(main, ["stage", "list"])
    info = CliRunner().invoke(main, ["stage", "info", "acme/my-check"])
    package = load_stage_package(stages / "acme" / "my-check")

    assert locked.exit_code == 0, locked.output
    assert listed.exit_code == 0, listed.output
    assert info.exit_code == 0, info.output
    assert static_problems(package) == []
    assert resolved  # uv resolved; nothing else ran
    assert attempts == []
    assert not any(name.startswith("my_check") for name in sys.modules)


def test_a_replay_snapshot_without_a_recorded_context_is_refused(tmp_path: Path) -> None:
    key = "acme_my-check"
    folder = tmp_path / "trial" / "stages" / key
    (folder / "stop-0-before").mkdir(parents=True)
    (folder / "stop-1-before").mkdir()
    (folder / "stop-1-context.json").write_text('{"round": 1, "carry": {"words": 2}}')
    captured = stage_check.captured_states(tmp_path / "trial", key)

    assert [item.context is not None for item in captured] == [False, True]
    stop_package = load_stage_package(
        Path(__file__).parent / "fixtures" / "stages" / "acme" / "note-state"
    )
    refused = asyncio.run(
        stage_check.replay_trial(
            stop_package,
            key,
            captured[:1],
            image="unused",
            docker_context="unused",
            logs_dir=tmp_path / "logs",
        )
    )
    assert len(refused) == 1
    assert refused[0].status is None
    assert refused[0].problem is not None
    assert "refused" in refused[0].problem


def test_the_record_floors_are_the_measured_minimum_around_the_done_run() -> None:
    from raidar.subject.primitive.calls import ResourceFloors

    before = ResourceFloors({"/app": 900, "/tmp": 500}, 4_000)
    after = ResourceFloors({"/app": 800, "/tmp": None}, None)

    assert stage_check._floors(before, after) == (500, 4_000)


def test_gate_runs_that_differ_in_an_output_digest_are_not_stable() -> None:
    base = {"outputs": {"note": "a" * 64}, "output_errors": {}, "reason": ""}
    moved = {"outputs": {"note": "b" * 64}, "output_errors": {}, "reason": "x"}

    assert stage_check._stable(base, {**base, "reason": "different words"})
    assert not stage_check._stable(base, moved)


def test_a_missing_or_invalid_expected_output_refuses_a_gate_fixture() -> None:
    expected = stage_check.Expectation("pass", {"note": "a" * 64, "report": "c" * 64})
    observed = {
        "outputs": {"note": "b" * 64, "report": None},
        "output_errors": {"report": "missing"},
    }

    problems = stage_check._output_problems(expected, observed)

    assert any("note: expected" in item for item in problems)
    assert any("report is invalid: missing" in item for item in problems)
    assert (
        stage_check._output_problems(stage_check.Expectation("pass", {"note": "b" * 64}), observed)
        == []
    )
