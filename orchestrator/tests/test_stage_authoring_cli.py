"""Authoring commands of ``stage/1``: tier-2 templates, locks and host isolation."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner
from raidar_contract.package.fixtures import static_problems

from raidar.cli import main, stage, stage_fixtures, stage_lock, stage_package, workflow_new
from raidar.subject.package.registry import load_stage_package
from raidar.subject.workflow import stage_check
from tests.release_support import REAL_PACKAGE, UV_LOCK_WITH_SDIST


@pytest.fixture
def stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stages root holding only a copy of the real ``raidar/docs-check`` source package."""
    root = tmp_path / "stages"
    shutil.copytree(REAL_PACKAGE, root / "raidar" / "docs-check")
    monkeypatch.setattr(stage_package, "stages_root", lambda: root)
    return root


def _files(root: Path, folder: str) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted((root / folder).rglob("*"))
        if path.is_file()
    }


def test_stage_new_defaults_to_the_real_docs_check_package_and_copies_it_verbatim(
    stages: Path,
) -> None:
    original = _files(stages / "raidar" / "docs-check", ".")
    result = CliRunner().invoke(main, ["stage", "new", "acme/my-check"])

    assert result.exit_code == 0, result.output
    copy = stages / "acme" / "my-check"
    real = stages / "raidar" / "docs-check"
    copied, source = _files(copy, "."), original.copy()
    assert load_stage_package(copy).manifest.name == "my-check"
    manifest = copied.pop("stage.yaml")
    assert manifest == source.pop("stage.yaml").replace(b"name: docs-check", b"name: my-check", 1)
    assert copied == source
    assert _files(real, ".") == original


def test_stage_new_copies_any_in_tree_package_fixtures_and_modes_included(stages: Path) -> None:
    first = CliRunner().invoke(main, ["stage", "new", "acme/first"])
    (stages / "acme" / "first" / "fixtures" / "pass" / "state" / "extra.txt").write_text("x")
    script = stages / "acme" / "first" / "tool" / "run.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)

    second = CliRunner().invoke(main, ["stage", "new", "beta/second", "--from", "acme/first"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    copy = stages / "beta" / "second"
    assert (copy / "fixtures" / "pass" / "state" / "extra.txt").read_text() == "x"
    assert (copy / "tool" / "run.sh").stat().st_mode & 0o111
    assert load_stage_package(copy).manifest.name == "second"


def test_stage_new_preserves_flow_yaml_comments_and_crlf(stages: Path) -> None:
    manifest = stages / "raidar" / "docs-check" / "stage.yaml"
    document = yaml.safe_load(manifest.read_text())
    source = (
        b"# preserve this comment\r\n"
        + yaml.safe_dump(document, default_flow_style=True).replace("\n", "\r\n").encode()
    )
    manifest.write_bytes(source)

    result = CliRunner().invoke(main, ["stage", "new", "acme/renamed"])

    assert result.exit_code == 0, result.output
    target = stages / "acme" / "renamed"
    assert load_stage_package(target).manifest.name == "renamed"
    assert (target / "stage.yaml").read_bytes() == source.replace(
        b"name: docs-check", b"name: renamed", 1
    )


@pytest.mark.parametrize(
    ("fields", "old", "new", "mode"),
    [
        (
            "name: &identity 'docs-check' # keep\r\nsummary: *identity\r\n",
            b"'docs-check'",
            b"'renamed'",
            0o644,
        ),
        (
            "name: |- # keep\r\n  docs-check\r\nsummary: prose\r\n",
            b"  docs-check\r\n",
            b"  renamed\r\n",
            0o644,
        ),
        (
            "summary: &identity docs-check\r\nname: *identity # keep\r\n",
            b"name: *identity",
            b"name: renamed",
            0o644,
        ),
        (
            'name: !!str "docs\\u002dcheck" # keep\r\nsummary: prose\r\n',
            b'"docs\\u002dcheck"',
            b'"renamed"',
            0o644,
        ),
        ("name: docs-check\r\nsummary: prose\r\n", b"name: docs-check", b"name: renamed", 0o444),
    ],
    ids=["anchor", "block", "alias", "tag", "read-only"],
)
def test_stage_new_renames_scalar_without_damaging_yaml_or_permissions(
    stages: Path, fields: str, old: bytes, new: bytes, mode: int
) -> None:
    manifest = stages / "raidar" / "docs-check" / "stage.yaml"
    document = yaml.safe_load(manifest.read_text())
    for field in ("name", "summary"):
        document.pop(field)
    source = fields.encode() + yaml.safe_dump(document).replace("\n", "\r\n").encode()
    manifest.write_bytes(source)
    manifest.chmod(mode)

    result = CliRunner().invoke(main, ["stage", "new", "acme/renamed"])

    assert result.exit_code == 0, result.output
    target = stages / "acme" / "renamed"
    assert load_stage_package(target).manifest.name == "renamed"
    assert (target / "stage.yaml").read_bytes() == source.replace(old, new, 1)
    assert (target / "stage.yaml").stat().st_mode & 0o777 == mode
    assert manifest.read_bytes() == source


def test_stage_new_refuses_an_unknown_source_and_writes_nothing(stages: Path) -> None:
    result = CliRunner().invoke(main, ["stage", "new", "acme/my-check", "--from", "acme/nope"])

    assert result.exit_code != 0
    assert "no stage package" in result.output
    assert not (stages / "acme").exists()


def test_stage_new_supports_a_namespace_and_refuses_to_overwrite(stages: Path) -> None:
    first = CliRunner().invoke(main, ["stage", "new", "acme/review-notes"])
    again = CliRunner().invoke(main, ["stage", "new", "acme/review-notes"])

    assert first.exit_code == 0, first.output
    load_stage_package(stages / "acme" / "review-notes")
    assert again.exit_code != 0
    assert "refusing to overwrite" in again.output


def test_stage_new_refuses_to_replace_a_symlink_at_the_target(stages: Path, tmp_path: Path) -> None:
    (stages / "acme").mkdir()
    (stages / "acme" / "my-check").symlink_to(tmp_path)

    result = CliRunner().invoke(main, ["stage", "new", "acme/my-check"])

    assert result.exit_code != 0
    assert "refusing to overwrite" in result.output
    assert (stages / "acme" / "my-check").is_symlink()


@pytest.mark.parametrize(
    "manifest",
    ["- a\n- list\n", "name:\n  - x\n", "summary: x\n", "name:\n", "name: [unclosed\n"],
    ids=["not-a-mapping", "name-not-scalar", "no-name", "empty-name", "invalid-yaml"],
)
def test_stage_new_from_a_manifest_it_cannot_rename_writes_nothing(
    stages: Path, manifest: str
) -> None:
    (stages / "acme" / "odd").mkdir(parents=True)
    (stages / "acme" / "odd" / "stage.yaml").write_text(manifest)

    result = CliRunner().invoke(main, ["stage", "new", "acme/copy", "--from", "acme/odd"])

    assert result.exit_code != 0
    assert not (stages / "acme" / "copy").exists()


@pytest.mark.parametrize("kind", ["file", "directory", "fixture", "fifo"])
def test_stage_new_refuses_a_source_with_a_symlink_or_special_file_and_writes_nothing(
    stages: Path, tmp_path: Path, kind: str
) -> None:
    real = stages / "raidar" / "docs-check"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    if kind == "file":
        (real / "tool" / "leak").symlink_to(outside / "secret.txt")
    elif kind == "directory":
        (real / "tool" / "leak").symlink_to(outside, target_is_directory=True)
    elif kind == "fixture":
        (real / "fixtures" / "pass" / "state" / "leak").symlink_to(outside / "secret.txt")
    else:
        os.mkfifo(real / "tool" / "pipe")

    result = CliRunner().invoke(main, ["stage", "new", "acme/my-check"])

    assert result.exit_code != 0
    assert "refused" in result.output
    assert not (stages / "acme").exists()


def test_stage_new_refuses_a_symlinked_namespace_folder(stages: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (stages / "acme").symlink_to(elsewhere, target_is_directory=True)

    result = CliRunner().invoke(main, ["stage", "new", "acme/my-check"])

    assert result.exit_code != 0
    assert "symlinked folders are refused" in result.output
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("args", [["../x"], ["acme/ok", "--from", "../etc"]])
def test_stage_new_refuses_names_that_could_traverse(stages: Path, args: list[str]) -> None:
    result = CliRunner().invoke(main, ["stage", "new", *args])

    assert result.exit_code != 0
    assert not (stages / "acme").exists()


@pytest.mark.parametrize("command", ["lock", "info", "check"])
def test_every_package_command_refuses_a_name_without_a_namespace(
    stages: Path, command: str
) -> None:
    result = CliRunner().invoke(main, ["stage", command, "my-check"])

    assert result.exit_code != 0
    assert "<ns>/<name>" in result.output


_STATIC_FAULTS: dict[str, tuple[Callable[[Path], object], str]] = {
    "missing pass fixture": (
        lambda root: shutil.rmtree(root / "fixtures" / "pass"),
        "missing mandatory fixture 'pass'",
    ),
    "missing refuse fixture": (
        lambda root: shutil.rmtree(root / "fixtures" / "refuse"),
        "missing mandatory fixture 'refuse'",
    ),
    "wrong expected status": (
        lambda root: (root / "fixtures" / "refuse" / "expect.yaml").write_text("status: pass\n"),
        "'refuse' must expect status 'refuse'",
    ),
    "stop result without a stop": (
        lambda root: (root / "fixtures" / "binary-file" / "expect.yaml").write_text(
            "status: done\n"
        ),
        "expects a stop result but the package has no stop",
    ),
}


@pytest.mark.parametrize("fault", sorted(_STATIC_FAULTS))
def test_a_fixture_set_that_breaks_the_authoring_contract_is_a_static_problem(
    stages: Path, fault: str
) -> None:
    CliRunner().invoke(main, ["stage", "new", "acme/my-check"])
    mutate, expected = _STATIC_FAULTS[fault]
    mutate(stages / "acme" / "my-check")

    problems = static_problems(load_stage_package(stages / "acme" / "my-check"))

    assert any(expected in problem for problem in problems)


def test_stage_list_reports_an_empty_tree_and_an_invalid_package(
    stages: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stage_package, "stages_root", lambda: tmp_path / "none")
    empty = CliRunner().invoke(main, ["stage", "list"])
    monkeypatch.setattr(stage_package, "stages_root", lambda: stages)
    (stages / "acme" / "broken").mkdir(parents=True)
    (stages / "acme" / "broken" / "stage.yaml").write_text("this: [is not a manifest\n")

    listed = CliRunner().invoke(main, ["stage", "list"])

    assert empty.exit_code == 0, empty.output
    assert "no stage packages" in empty.output
    assert listed.exit_code == 0, listed.output
    assert "acme/broken\tINVALID: " in listed.output


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
    template = root / "raidar" / "one-shot-verify"
    template.mkdir(parents=True)
    (template / "workflow.yaml").write_text(
        "schema: 2\nname: one-shot-verify\nbuild:\n  prompt: do %%%TASK%%%\n"
    )
    monkeypatch.setattr(workflow_new, "workflows_root", lambda: root)

    first = CliRunner().invoke(main, ["workflow", "new", "acme/mine"])
    again = CliRunner().invoke(main, ["workflow", "new", "acme/mine"])

    assert first.exit_code == 0, first.output
    assert yaml.safe_load((root / "acme" / "mine" / "workflow.yaml").read_text())["name"] == "mine"
    assert again.exit_code != 0
    assert "refusing to overwrite" in again.output


def test_workflow_new_refuses_an_unknown_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workflow_new, "workflows_root", lambda: tmp_path)

    result = CliRunner().invoke(main, ["workflow", "new", "acme/mine", "--from", "acme/missing"])

    assert result.exit_code != 0
    assert "no workflow template" in result.output


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
    """V4: new, list, info, lock (resolution only) and check start no host process."""
    CliRunner().invoke(main, ["stage", "new", "acme/my-check"])
    attempts = _tripwire(monkeypatch)
    resolved: list[Any] = []

    class NoDocker:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            raise RuntimeError("no docker in this test")

        async def __aexit__(self, *exc: Any) -> None:
            return None

    def uv(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
        resolved.append(args)
        (project / "uv.lock").write_text(UV_LOCK_WITH_SDIST)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(stage_lock, "_uv", uv)

    locked = CliRunner().invoke(main, ["stage", "lock", "acme/my-check"])
    listed = CliRunner().invoke(main, ["stage", "list"])
    info = CliRunner().invoke(main, ["stage", "info", "acme/my-check"])
    monkeypatch.setattr(stage_fixtures, "lock_problems", lambda root: [])
    monkeypatch.setattr(stage_fixtures, "_pack", lambda target: ("image", "context", "pack"))
    monkeypatch.setattr(stage_check, "PackContainer", NoDocker)
    checked = CliRunner().invoke(main, ["stage", "check", "acme/my-check"])
    package = load_stage_package(stages / "acme" / "my-check")

    assert locked.exit_code == 0, locked.output
    assert listed.exit_code == 0, listed.output
    assert info.exit_code == 0, info.output
    assert checked.exit_code != 0
    assert "stage-check could not complete" in checked.output
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
