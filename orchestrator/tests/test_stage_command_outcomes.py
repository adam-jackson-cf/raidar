"""Outcomes of the stage authoring commands: what is published, refused or left untouched.

The Docker-backed pieces are replaced by in-process fakes that only isolate the dependency; every
assertion is on files the commands leave behind, exit codes and refusals.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click
import pytest
import yaml
from click.testing import CliRunner

from raidar.cli import main, stage_fixtures, stage_lock, stage_package, workflow_new
from raidar.cli.stage_template import LOCK as TEMPLATE_LOCK
from raidar.cli.stage_template import render_template
from raidar.contract.paths import stage_checks_root
from raidar.contract.wheels import uv_lock_refusals
from raidar.subject.package.registry import load_stage_package
from raidar.subject.primitive.canary import StageCheckRecord
from raidar.subject.workflow.stage_check import CaseResult, CheckReport, ReplayRun

SDIST = (
    f'sdist = {{ url = "https://files.pythonhosted.org/x.tar.gz", hash = "sha256:{"0" * 64}" }}\n'
)
UV_LOCK_WITH_SDIST = TEMPLATE_LOCK.replace("wheels = [", SDIST + "wheels = [")


@pytest.fixture
def stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "stages"
    monkeypatch.setattr(stage_package, "stages_root", lambda: root)
    monkeypatch.setenv("RAIDAR_VAR", str(tmp_path / "var"))
    return root


def _new(name: str = "my-check") -> None:
    result = CliRunner().invoke(main, ["stage", "new", name])
    assert result.exit_code == 0, result.output


def _write(root: Path, files: dict[str, bytes]) -> None:
    for relative, data in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


# ---- stage_package: names, loading, release state --------------------------------------------


@pytest.mark.parametrize("name", ["A", "-a", "a_b", "a/", "/a", "a/b/c", "a b", ""])
def test_invalid_package_names_are_refused(name: str) -> None:
    with pytest.raises(click.BadParameter):
        stage_package.valid_name(name)


def test_load_named_refuses_a_missing_package_and_names_the_broken_one(stages: Path) -> None:
    with pytest.raises(click.ClickException, match="no stage package at"):
        stage_package.load_named("absent")
    (stages / "broken").mkdir(parents=True)
    (stages / "broken" / "stage.yaml").write_text("this: [is not a manifest\n")

    with pytest.raises(click.ClickException) as raised:
        stage_package.load_named("broken")

    assert raised.value.message.startswith("broken: ")


def test_package_folders_finds_manifests_at_most_one_namespace_deep_in_sorted_order(
    stages: Path,
) -> None:
    for relative in ["zeta", "acme/inner", "acme/deep/too-deep", "alpha"]:
        (stages / relative).mkdir(parents=True)
        (stages / relative / "stage.yaml").write_text("x: 1\n")
    (stages / "no-manifest").mkdir()

    found = [path.relative_to(stages).as_posix() for path in stage_package.package_folders()]

    assert found == ["acme/inner", "alpha", "zeta"]


def test_package_folders_is_empty_without_a_stages_root(stages: Path) -> None:
    assert not stages.exists()
    assert stage_package.package_folders() == []


def _register(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stages_: dict[str, str]) -> None:
    path = tmp_path / "releases.yaml"
    path.write_text(yaml.safe_dump({"stages": stages_}))
    monkeypatch.setattr(stage_package, "releases_file", lambda: path)


def test_release_state_distinguishes_unreleased_released_and_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = "a" * 64, "b" * 64
    _register(tmp_path, monkeypatch, {"seen@1": old, "moved@1": old, "moved@2": new})
    register = stage_package.register()

    assert stage_package.release_state("fresh", new, register) == (
        "unreleased",
        f"register stages: fresh@1: {new}",
    )
    assert stage_package.release_state("seen", old, register) == ("released as seen@1", None)
    assert stage_package.release_state("seen", new, register) == (
        "changed since seen@1",
        f"register stages: seen@2: {new}",
    )
    # only the highest registered version is "the one the tree must hold"
    assert stage_package.release_state("moved", old, register) == (
        "changed since moved@2",
        f"register stages: moved@3: {old}",
    )
    assert stage_package.release_state("moved", new, register)[1] is None


# ---- stage_lock ------------------------------------------------------------------------------


def _package_with_tool(tmp_path: Path) -> Path:
    root = tmp_path / "pkg"
    _write(root, render_template("pkg"))
    return root


def test_a_package_without_a_tool_project_has_nothing_to_lock(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    root.mkdir()

    assert stage_lock.lock_problems(root) == []
    with pytest.raises(stage_lock.StageLockError, match="no tool/pyproject.toml"):
        stage_lock.generate_lock(root)


def test_a_tool_project_without_a_lock_is_refused_before_uv_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _package_with_tool(tmp_path)
    (root / "tool" / "uv.lock").unlink()
    monkeypatch.setattr(
        stage_lock, "_uv", lambda *a: pytest.fail("uv must not run without a lock to check")
    )

    assert stage_lock.lock_problems(root) == ["tool/uv.lock is missing: run make stage-lock"]


def _done(code: int, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["uv"], code, stdout="", stderr=stderr)


def _fake_uv(
    seen: list[list[str]], *, resolved: str = UV_LOCK_WITH_SDIST, check_code: int = 0
) -> Callable[..., subprocess.CompletedProcess[str]]:
    def uv(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
        seen.append(sorted(item.name for item in project.iterdir()))
        if "--check" in args:
            return _done(check_code, "lock is out of date" if check_code else "")
        (project / "uv.lock").write_text(resolved)
        return _done(0)

    return uv


def test_a_stale_lock_is_a_problem_and_a_current_wheels_only_lock_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _package_with_tool(tmp_path)
    seen: list[list[str]] = []
    monkeypatch.setattr(stage_lock, "_uv", _fake_uv(seen, check_code=1))

    stale = stage_lock.lock_problems(root)
    monkeypatch.setattr(stage_lock, "_uv", _fake_uv(seen, check_code=0))
    current = stage_lock.lock_problems(root)

    assert len(stale) == 1
    assert stale[0].startswith("tool/uv.lock is stale")
    assert "out of date" in stale[0]
    assert current == []
    # uv only ever sees the dependency declaration and the lock, never authored source
    assert seen == [["pyproject.toml", "uv.lock"]] * 2


def test_generate_lock_writes_a_wheels_only_lock_resolved_from_the_declaration_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _package_with_tool(tmp_path)
    (root / "tool" / "uv.lock").write_text("# old\n")
    (root / "tool" / "tool_source.py").write_text("raise SystemExit('authored code ran')\n")
    seen: list[list[str]] = []
    monkeypatch.setattr(stage_lock, "_uv", _fake_uv(seen))

    text = stage_lock.generate_lock(root)

    assert "sdist" not in text
    assert uv_lock_refusals(text) == []
    assert (root / "tool" / "uv.lock").read_text() == text
    # first resolution: no lock yet; the check sees the lock just generated
    assert seen == [["pyproject.toml"], ["pyproject.toml", "uv.lock"]]


def test_a_lock_uv_cannot_resolve_leaves_the_existing_lock_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _package_with_tool(tmp_path)
    (root / "tool" / "uv.lock").write_text("# keep me\n")
    monkeypatch.setattr(
        stage_lock, "_uv", lambda *a: _done(1, "No solution found when resolving dependencies")
    )

    with pytest.raises(stage_lock.StageLockError, match="uv lock failed: No solution found"):
        stage_lock.generate_lock(root)

    assert (root / "tool" / "uv.lock").read_text() == "# keep me\n"


def test_a_generated_lock_uv_rejects_offline_is_never_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _package_with_tool(tmp_path)
    (root / "tool" / "uv.lock").write_text("# keep me\n")
    monkeypatch.setattr(stage_lock, "_uv", _fake_uv([], check_code=1))

    with pytest.raises(stage_lock.StageLockError, match="rejects the generated lock offline"):
        stage_lock.generate_lock(root)

    assert (root / "tool" / "uv.lock").read_text() == "# keep me\n"


def test_a_resolution_that_is_not_wheels_only_is_refused_and_not_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _package_with_tool(tmp_path)
    (root / "tool" / "uv.lock").unlink()
    monkeypatch.setattr(stage_lock, "_uv", _fake_uv([], resolved="sdist = 1\n" + TEMPLATE_LOCK))

    with pytest.raises(stage_lock.StageLockError, match="not wheels-only"):
        stage_lock.generate_lock(root)

    assert not (root / "tool" / "uv.lock").exists()


@pytest.mark.parametrize("failure", [FileNotFoundError("uv"), subprocess.TimeoutExpired("uv", 1)])
def test_uv_that_cannot_run_or_times_out_is_a_lock_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    root = _package_with_tool(tmp_path)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise failure

    monkeypatch.setattr(subprocess, "run", broken)

    with pytest.raises(stage_lock.StageLockError, match="uv could not run"):
        stage_lock.generate_lock(root)
    with pytest.raises(stage_lock.StageLockError, match="uv could not run"):
        stage_lock.lock_problems(root)


def test_stage_lock_command_reports_a_refusal_as_a_failed_command(
    stages: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _new()
    before = (stages / "my-check" / "tool" / "uv.lock").read_text()
    monkeypatch.setattr(stage_lock, "_uv", lambda *a: _done(1, "boom"))

    result = CliRunner().invoke(main, ["stage", "lock", "my-check"])

    assert result.exit_code != 0
    assert "uv lock failed: boom" in result.output
    assert (stages / "my-check" / "tool" / "uv.lock").read_text() == before


# ---- stage check: refusals before any container ----------------------------------------------


def _record(package: str = "my-check") -> StageCheckRecord:
    return StageCheckRecord(
        package=package,
        pack="pack",
        primitives={"frame/1": "f" * 64},
        stdout_sha256="1" * 64,
        outputs={"note": "2" * 64},
        disk_free=10,
        memory_available=None,
    )


def _installed(arch: str, tree: str) -> dict[str, Any]:
    return {"arch": arch, "tree_digest": tree, "wheels": [{"name": "w", "sha256": tree[:3]}]}


def _passing(**overrides: Any) -> CheckReport:
    base: dict[str, Any] = {
        "reference": "my-check",
        "digest": "d",
        "pack": "pack",
        "primitives": {},
        "cases": [CaseResult("done", "fixture", True)],
        "record": _record(),
        "installed": _installed("arm64", "a" * 64),
    }
    return CheckReport(**{**base, **overrides})


@pytest.fixture
def rig(stages: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A new package, the pack and lock gates open, and ``check_package`` returning ``report``."""
    _new()
    package = load_stage_package(stages / "my-check")
    state = SimpleNamespace(report=_passing(), calls=[], digest=package.digest)

    async def fake_check(*args: Any, **kwargs: Any) -> CheckReport:
        state.calls.append(kwargs["logs_dir"])
        if isinstance(state.report, Exception):
            raise state.report
        return state.report

    monkeypatch.setattr(stage_fixtures, "lock_problems", lambda root: [])
    monkeypatch.setattr(stage_fixtures, "_pack", lambda target: ("image", "context", "pack"))
    monkeypatch.setattr(stage_fixtures, "check_package", fake_check)
    return state


def _check(*extra: str) -> Any:
    return CliRunner().invoke(main, ["stage", "check", "my-check", *extra])


def test_check_refuses_a_stale_lock_before_touching_the_pack(
    stages: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _new()
    monkeypatch.setattr(stage_fixtures, "lock_problems", lambda root: ["one", "two"])
    monkeypatch.setattr(
        stage_fixtures, "_pack", lambda target: pytest.fail("no pack use with a bad lock")
    )

    result = _check()

    assert result.exit_code != 0
    assert "one; two" in result.output
    assert not stage_checks_root().exists()


def test_check_refuses_an_unknown_package_and_a_missing_pack_image(
    stages: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unknown = CliRunner().invoke(main, ["stage", "check", "nope"])
    assert unknown.exit_code != 0
    assert "no stage package at" in unknown.output

    _new()
    monkeypatch.setattr(stage_fixtures, "lock_problems", lambda root: [])
    monkeypatch.setattr(
        stage_fixtures,
        "docker_selection",
        lambda target: SimpleNamespace(trial=SimpleNamespace(docker_context="ctx")),
    )
    monkeypatch.setattr(stage_fixtures, "inspect_image", lambda tag: None)

    absent = _check()

    assert absent.exit_code != 0
    assert "is absent: run make pack-build" in absent.output
    assert not stage_checks_root().exists()


# ---- stage check: what is published ----------------------------------------------------------


def test_a_complete_pass_publishes_the_canary_record_and_this_architectures_proof(
    rig: SimpleNamespace,
) -> None:
    result = _check()

    assert result.exit_code == 0, result.output
    root = stage_checks_root()
    assert json.loads((root / f"{rig.digest}.json").read_text()) == _record().as_json()
    installed = json.loads((root / f"{rig.digest}.installed.json").read_text())
    wheels = rig.report.installed["wheels"]
    assert installed == {"arm64": {"tree_digest": "a" * 64, "wheels": wheels}}
    assert f"canary record {root / (rig.digest + '.json')}" in result.output


def test_publishing_one_architecture_keeps_the_others_and_replaces_only_its_own(
    rig: SimpleNamespace,
) -> None:
    root = stage_checks_root()
    root.mkdir(parents=True)
    proof = root / f"{rig.digest}.installed.json"
    proof.write_text(
        json.dumps(
            {
                "amd64": {"tree_digest": "c" * 64, "wheels": []},
                "arm64": {"tree_digest": "stale", "wheels": []},
            }
        )
    )

    assert _check().exit_code == 0

    published = json.loads(proof.read_text())
    assert published["amd64"] == {"tree_digest": "c" * 64, "wheels": []}
    assert published["arm64"]["tree_digest"] == "a" * 64

    rig.report = _passing(installed=_installed("riscv64", "e" * 64))
    assert _check().exit_code == 0
    assert set(json.loads(proof.read_text())) == {"amd64", "arm64", "riscv64"}


def test_a_package_without_a_stop_publishes_proof_but_no_canary_record(
    rig: SimpleNamespace,
) -> None:
    rig.report = _passing(record=None)

    result = _check()

    assert result.exit_code == 0, result.output
    assert "no canary record" in result.output
    names = sorted(path.name for path in stage_checks_root().iterdir())
    assert names == [f"{rig.digest}.installed.json"]


def test_a_failed_case_publishes_nothing_and_leaves_earlier_proof_alone(
    rig: SimpleNamespace,
) -> None:
    root = stage_checks_root()
    root.mkdir(parents=True)
    proof = root / f"{rig.digest}.installed.json"
    proof.write_text('{"amd64": {"tree_digest": "c", "wheels": []}}')
    rig.report = _passing(
        cases=[
            CaseResult("done", "fixture", True),
            CaseResult("refuse", "fixture", False, "pass", ("expected refuse",)),
        ]
    )

    result = _check()

    assert result.exit_code != 0
    assert "failed its checks" in result.output
    assert "FAIL" in result.output
    assert "expected refuse" in result.output
    assert proof.read_text() == '{"amd64": {"tree_digest": "c", "wheels": []}}'
    assert [path.name for path in root.iterdir()] == [proof.name]


@pytest.mark.parametrize(
    "report",
    [
        _passing(static=["missing mandatory fixture 'pass'"]),
        _passing(cases=[]),
    ],
    ids=["static-problem", "no-cases-ran"],
)
def test_a_report_that_cannot_vouch_publishes_nothing(
    rig: SimpleNamespace, report: CheckReport
) -> None:
    rig.report = report

    result = _check()

    assert result.exit_code != 0
    assert not stage_checks_root().exists()
    if report.static:
        assert "FAIL static: missing mandatory fixture 'pass'" in result.output


def test_a_check_that_cannot_complete_is_reported_and_publishes_nothing(
    rig: SimpleNamespace,
) -> None:
    rig.report = RuntimeError("container died")

    result = _check()

    assert result.exit_code != 0
    assert "stage-check could not complete: container died" in result.output
    assert not stage_checks_root().exists()


def test_check_logs_go_under_a_per_package_directory_in_the_generated_tree(
    rig: SimpleNamespace, tmp_path: Path
) -> None:
    _check()

    (logs,) = rig.calls
    assert logs.parent == tmp_path / "var" / "tmp" / "stage-check" / "my-check"
    assert logs.name.startswith("check-")


# ---- stage check --replay --------------------------------------------------------------------


def _trial(tmp_path: Path, *, with_context: bool, key: str = "my-check") -> Path:
    folder = tmp_path / "trial" / "stages" / key
    (folder / "stop-0-before").mkdir(parents=True)
    (folder / "stop-0-before" / "note.md").write_text("hello")
    if with_context:
        (folder / "stop-0-context.json").write_text('{"round": 0, "carry": {}}')
    return tmp_path / "trial"


def test_replay_of_a_trial_holding_no_captured_state_is_refused(
    rig: SimpleNamespace, tmp_path: Path
) -> None:
    empty = tmp_path / "empty-trial"
    empty.mkdir()

    result = _check("--replay", str(empty))

    assert result.exit_code != 0
    assert "holds no captured state for my-check" in result.output


def test_replay_of_a_package_that_declares_no_stop_cannot_complete(
    rig: SimpleNamespace, tmp_path: Path
) -> None:
    result = _check("--replay", str(_trial(tmp_path, with_context=True)))

    assert result.exit_code != 0
    assert "stage-check replay could not complete" in result.output
    assert "declares no stop to replay" in result.output
    assert rig.calls == []


def _replaying(monkeypatch: pytest.MonkeyPatch, runs: list[ReplayRun]) -> None:
    async def fake_replay(*args: Any, **kwargs: Any) -> list[ReplayRun]:
        return runs

    monkeypatch.setattr(stage_fixtures, "replay_trial", fake_replay)


def test_replay_with_an_unusable_state_prints_every_outcome_and_fails(
    rig: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _replaying(
        monkeypatch,
        [
            ReplayRun("s0", "done", None, "ab" * 32),
            ReplayRun("s1", None, "refused: no recorded call context for this state", ""),
        ],
    )

    result = _check("--replay", str(_trial(tmp_path, with_context=True)))

    assert result.exit_code != 0
    assert "s0: done stdout=" + "ab" * 6 in result.output
    assert "s1: unusable (refused: no recorded call context" in result.output
    assert "replay disposition" in result.output


def test_a_fully_usable_replay_succeeds_and_publishes_no_record(
    rig: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _replaying(monkeypatch, [ReplayRun("s0", "done", None, "cd" * 32)])

    result = _check("--replay", str(_trial(tmp_path, with_context=True)))

    assert result.exit_code == 0, result.output
    assert "s0: done" in result.output
    assert not stage_checks_root().exists()


def test_a_replay_that_raises_is_reported_not_traced(
    rig: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing(*args: Any, **kwargs: Any) -> list[ReplayRun]:
        raise RuntimeError("docker run failed (125)")

    monkeypatch.setattr(stage_fixtures, "replay_trial", failing)

    result = _check("--replay", str(_trial(tmp_path, with_context=True)))

    assert result.exit_code != 0
    assert "could not complete: docker run failed (125)" in result.output


# ---- workflow new ----------------------------------------------------------------------------


@pytest.fixture
def workflows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "workflows"
    monkeypatch.setattr(workflow_new, "workflows_root", lambda: root)
    return root


def _new_workflow(*args: str) -> Any:
    return CliRunner().invoke(main, ["workflow", "new", *args])


def test_workflow_new_copies_the_template_folder_and_renames_only_the_declared_name(
    workflows: Path,
) -> None:
    template = workflows / "base"
    (template / "prompts").mkdir(parents=True)
    (template / "prompts" / "build.md").write_text("build it")
    (template / "workflow.yaml").write_text(
        "# name: not-this\nname: base\nstages: []\nnotes:\n  name: nested-stays\n"
    )

    result = _new_workflow("mine", "--from", "base")

    assert result.exit_code == 0, result.output
    text = (workflows / "mine" / "workflow.yaml").read_text()
    assert text == "# name: not-this\nname: mine\nstages: []\nnotes:\n  name: nested-stays\n"
    assert (workflows / "mine" / "prompts" / "build.md").read_text() == "build it"
    assert (template / "workflow.yaml").read_text().count("name: base") == 1


@pytest.mark.parametrize("content", ["- just\n- a list\n", "stages: []\n", ""])
def test_a_template_that_is_not_a_workflow_definition_is_refused_and_leaves_no_copy(
    workflows: Path, content: str
) -> None:
    (workflows / "bad").mkdir(parents=True)
    (workflows / "bad" / "workflow.yaml").write_text(content)

    result = _new_workflow("mine", "--from", "bad")

    assert result.exit_code != 0
    assert "is not a workflow definition" in result.output
    assert not (workflows / "mine").exists()
    assert (workflows / "bad" / "workflow.yaml").read_text() == content


@pytest.mark.parametrize(
    "args",
    [["Bad"], ["-x"], ["a/b"], ["ok", "--from", "../x"], ["ok", "--from", "Up"]],
)
def test_workflow_new_refuses_names_that_could_escape_or_are_malformed(
    workflows: Path, args: list[str]
) -> None:
    result = _new_workflow(*args)

    assert result.exit_code != 0
    assert not workflows.exists()
