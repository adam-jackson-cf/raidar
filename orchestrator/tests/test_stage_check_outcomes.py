"""``check_package`` and ``replay_trial`` end to end over a container double.

The service decides which cases run and what each must show; the container, the confined tool and
the gate engine are doubles. Authored fixtures are data; nothing here executes package code.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from raidar.subject.package.registry import load_stage_package
from raidar.subject.primitive.calls import ResourceFloors, TransportError
from raidar.subject.primitive.canary import StageCheckRecord
from raidar.subject.primitive.gate import Attribution, GateInvalid, GateOutcome
from raidar.subject.primitive.site import TrialInvalid
from raidar.subject.workflow.identity import package_primitives
from raidar.subject.workflow.stage_cases import required_cases, static_problems
from raidar.subject.workflow.stage_check import captured_states, record_json, replay_trial
from tests.lifecycle_support import FakeContainer, ToolOutcome, note_digest, stop_json
from tests.package_support import write_package
from tests.stage_check_support import (
    CheckRig,
    copy_package,
    gate_outcome,
    honest_gate,
    install_rig,
    lax_tool,
    stop_only_note_state,
    strict_note_tool,
)

DATA_MODES = ("empty-state", "zero-byte", "invalid-json", "non-utf8", "deep-json")
BOUNDS_MODES = ("fifo", "symlink-loop", "oversized", "unreadable")
HOSTILE_MODES = ("fifo", "symlink-loop", "oversized")
FLAKY = "two runs differ in stdout or output digests"


@pytest.fixture(autouse=True)
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The only temp folder the service may use, so leaks are observable."""
    folder = tmp_path / "scratch"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


def _by_case(report: Any) -> dict[str, Any]:
    return {item.case: item for item in report.cases}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stop_states(container: FakeContainer) -> list[str]:
    return [context["state"] for context in container.tool_calls("stop/1")]


# ---------------------------------------------------------------- a stop package that behaves


async def test_a_well_behaved_stop_passes_every_case_and_records_its_done_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scratch: Path
) -> None:
    rig = install_rig(tmp_path / "rig", monkeypatch)
    before = rig.package.fixture_cases()

    report = await rig.run_check("ns/note-state")

    cases = _by_case(report)
    assert report.ok
    assert {name: item.status for name, item in cases.items() if item.kind == "fixture"} == {
        "closed": "closed",
        "done": "done",
        "resume": "resume",
    }
    assert {item.case for item in report.cases if item.kind == "adversarial"} == {
        f"raidar-{mode}" for mode in DATA_MODES
    }
    assert all(item.status == "resume" for item in report.cases if item.kind == "adversarial")
    assert {item.case: item.status for item in report.cases if item.kind == "bounds"} == {
        "raidar-fifo": "bounds",
        "raidar-symlink-loop": "bounds",
        "raidar-oversized": "bounds",
        "raidar-unreadable": "resume",
    }
    done_stdout = stop_json("done", carry={"words": 5}).stdout
    assert report.record == StageCheckRecord(
        package=rig.package.digest,
        pack="pack-recipe-1",
        primitives=dict(package_primitives([rig.package])),
        stdout_sha256=_sha(done_stdout),
        outputs=note_digest(rig.container),
        disk_free=10**9,
        memory_available=10**9,
    )
    assert StageCheckRecord.from_json(record_json(report.record)) == report.record
    # The package on disk is untouched by the generated data cases, the container is released, and
    # the generated copy is gone.
    assert rig.package.fixture_cases() == before
    assert rig.exited == 1
    assert list(scratch.iterdir()) == []


async def test_every_stop_call_gets_its_own_fresh_state_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = install_rig(tmp_path / "rig", monkeypatch)

    report = await rig.run_check()

    assert report.ok
    states = _stop_states(rig.container)
    assert len(states) == len(set(states))


async def test_a_verifier_that_calls_damaged_state_done_is_attributed_case_by_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = install_rig(tmp_path / "rig", monkeypatch, tool=lax_tool)

    report = await rig.run_check()

    cases = _by_case(report)
    assert not report.ok
    assert report.record is None
    assert cases["done"].ok
    assert cases["closed"].problems == ("expected status closed, got done",)
    assert cases["resume"].problems == ("expected status resume, got done",)
    for mode in DATA_MODES:
        assert cases[f"raidar-{mode}"].problems == ("a damaged state must not report done",)
    assert cases["raidar-unreadable"].problems == ("an unreadable state must not report done",)
    assert all(cases[f"raidar-{mode}"].ok for mode in HOSTILE_MODES)


async def test_generated_data_cases_damage_every_state_file_and_leave_the_done_state_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = stop_only_note_state(tmp_path / "pkgs")
    extra = root / "fixtures" / "done" / "state" / "sub" / "extra.txt"
    extra.parent.mkdir()
    extra.write_text("The note has extra words.\n", encoding="utf-8")
    seen: dict[str, dict[str, bytes]] = {}

    def recording(
        container: FakeContainer, argv: Sequence[str], context: dict[str, Any]
    ) -> ToolOutcome:
        state = container.host(context["state"])
        seen[context["state"]] = {
            str(item.relative_to(state)): item.read_bytes()
            for item in sorted(state.rglob("*"))
            if item.is_file()
        }
        return strict_note_tool(container, argv, context)

    rig = install_rig(tmp_path / "rig", monkeypatch, load_stage_package(root), tool=recording)

    report = await rig.run_check()

    assert report.ok
    run = {
        key.split("/")[-2].removesuffix("-0"): files
        for key, files in seen.items()
        if key.endswith("-0/state")
    }
    both = {"note.md", "sub/extra.txt"}
    assert run["done"]["sub/extra.txt"] == extra.read_bytes()
    assert run["raidar-empty-state"] == {}
    assert run["raidar-zero-byte"] == dict.fromkeys(both, b"")
    for data in run["raidar-invalid-json"].values():
        with pytest.raises(json.JSONDecodeError):
            json.loads(data)
    assert set(run["raidar-invalid-json"]) == both
    for data in run["raidar-non-utf8"].values():
        with pytest.raises(UnicodeDecodeError):
            data.decode("utf-8")
    for data in run["raidar-deep-json"].values():
        assert data.count(b"[") == data.count(b"]") >= 500


# ---------------------------------------------------------------- a verifier that misbehaves


async def test_a_verifier_whose_output_changes_between_identical_runs_fails_those_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = [0]

    def flaky(
        container: FakeContainer, argv: Sequence[str], context: dict[str, Any]
    ) -> ToolOutcome:
        calls[0] += 1
        document = json.loads(strict_note_tool(container, argv, context).stdout)
        document["carry"]["call"] = calls[0]
        return ToolOutcome(json.dumps(document).encode())

    rig = install_rig(tmp_path / "rig", monkeypatch, tool=flaky)

    report = await rig.run_check()

    cases = _by_case(report)
    assert not report.ok
    assert report.record is None
    for name in ("closed", "done", "resume", *(f"raidar-{mode}" for mode in DATA_MODES)):
        assert FLAKY in cases[name].problems, name
    # the bounds cases never compare two runs
    assert all(cases[f"raidar-{mode}"].ok for mode in BOUNDS_MODES)


async def test_a_slow_verifier_is_flagged_on_data_cases_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = install_rig(tmp_path / "rig", monkeypatch, duration_s=20.0)

    report = await rig.run_check()

    cases = _by_case(report)
    slow = "took 20.0s, over a quarter of the 60s timeout"
    assert all(cases[name].ok for name in ("closed", "done", "resume"))
    for mode in DATA_MODES:
        assert cases[f"raidar-{mode}"].problems == (slow,)
    assert report.record is None


@pytest.mark.parametrize(
    ("outcome", "problem"),
    [
        (ToolOutcome(b"{}", exit_code=1), "call unusable (exit 1)"),
        (ToolOutcome(stop_json("done").stdout, timed_out=True), "call unusable (exit 0)"),
        (ToolOutcome(b"oops"), "stop output is not JSON"),
        (ToolOutcome(b'{"status": "done"}'), "exactly status, message, tokens and carry"),
        (stop_json("done", tokens={"UNDECLARED": "x"}), "undeclared tokens ['UNDECLARED']"),
        (stop_json("resume", "  "), "a resume needs a nonempty message"),
        (stop_json("finished"), "'finished' is not done, resume or closed"),
    ],
)
async def test_an_unusable_stop_result_is_the_case_problem_and_never_a_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: ToolOutcome, problem: str
) -> None:
    rig = install_rig(tmp_path / "rig", monkeypatch, tool=lambda container, argv, context: outcome)

    report = await rig.run_check()

    cases = _by_case(report)
    assert not report.ok
    assert report.record is None
    assert cases["closed"].status is None
    assert len(cases["closed"].problems) == 1
    assert problem in cases["closed"].problems[0]
    assert all(not cases[f"raidar-{mode}"].ok for mode in DATA_MODES)


async def test_a_done_output_that_is_not_the_expected_digest_names_both_digests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = stop_only_note_state(tmp_path / "pkgs")
    wrong = "0" * 64
    (root / "fixtures" / "done" / "expect.yaml").write_text(
        f"status: done\noutputs:\n  note: '{wrong}'\n", encoding="utf-8"
    )
    rig = install_rig(tmp_path / "rig", monkeypatch, load_stage_package(root))

    report = await rig.run_check()

    real = note_digest(rig.container)["note"]
    assert _by_case(report)["done"].problems == (f"output note: expected {wrong}, got {real}",)
    assert report.record is None


async def test_an_expected_output_the_verifier_deleted_reads_as_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deleting(
        container: FakeContainer, argv: Sequence[str], context: dict[str, Any]
    ) -> ToolOutcome:
        outcome = strict_note_tool(container, argv, context)
        (container.host(context["state"]) / "note.md").unlink(missing_ok=True)
        return outcome

    rig = install_rig(tmp_path / "rig", monkeypatch, tool=deleting)

    report = await rig.run_check()

    expected = note_digest(rig.container)["note"]
    assert _by_case(report)["done"].problems == (f"output note: expected {expected}, got None",)


# ---------------------------------------------------------------- the done canary record


async def test_the_record_floors_are_the_least_free_disk_and_memory_around_the_done_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = install_rig(
        tmp_path / "rig",
        monkeypatch,
        floors=[
            ResourceFloors({"/app": 900, "/tmp": 500}, 4_000),
            ResourceFloors({"/app": 800, "/tmp": None}, 3_000),
        ],
    )

    report = await rig.run_check()

    assert report.record is not None
    assert (report.record.disk_free, report.record.memory_available) == (500, 3_000)


async def test_unmeasurable_floors_record_no_memory_floor_and_zero_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = install_rig(
        tmp_path / "rig",
        monkeypatch,
        floors=[ResourceFloors({}, None), ResourceFloors({"/app": None}, None)],
    )

    report = await rig.run_check()

    assert report.record is not None
    assert (report.record.disk_free, report.record.memory_available) == (0, None)
    stored = record_json(report.record)
    assert stored["floors"] == {"disk_free": 0, "memory_available": None}
    assert StageCheckRecord.from_json(stored) == report.record


async def test_a_done_run_that_loses_its_output_at_record_time_invalidates_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scratch: Path
) -> None:
    def vanishing(
        container: FakeContainer, argv: Sequence[str], context: dict[str, Any]
    ) -> ToolOutcome:
        outcome = strict_note_tool(container, argv, context)
        if context["state"].endswith("done-record/state"):
            (container.host(context["state"]) / "note.md").unlink()
        return outcome

    rig = install_rig(tmp_path / "rig", monkeypatch, tool=vanishing)

    with pytest.raises(TrialInvalid) as error:
        await rig.run_check()

    assert error.value.kind == "instrument"
    assert "the done fixture left no output ['note']" in error.value.detail
    assert rig.exited == 1
    assert list(scratch.iterdir()) == []


async def test_changed_tool_bytes_fail_the_check_even_when_every_fixture_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = install_rig(tmp_path / "rig", monkeypatch, tree_digest="x" * 64)

    report = await rig.run_check()

    *fixtures, tree = report.cases
    assert all(item.ok for item in fixtures)
    assert (tree.case, tree.kind, tree.ok, tree.problems) == (
        "installed-tree",
        "integrity",
        False,
        ("tool bytes changed",),
    )
    assert not report.ok
    assert report.record is None


# ---------------------------------------------------------------- bounds and transport


async def test_state_outside_the_bounds_must_be_rejected_before_authored_code_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = install_rig(tmp_path / "rig", monkeypatch, ignore_bounds=True)

    report = await rig.run_check()

    cases = _by_case(report)
    assert not report.ok
    for mode in HOSTILE_MODES:
        assert cases[f"raidar-{mode}"].problems == (
            "watched state outside Raidar's bounds was not rejected",
            "authored code ran against state outside Raidar's bounds",
        )
    assert cases["raidar-unreadable"].ok
    assert report.record is None


async def test_a_fifo_symlink_loop_and_oversized_file_never_reach_the_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered: list[str] = []

    def tool(container: FakeContainer, argv: Sequence[str], context: dict[str, Any]) -> ToolOutcome:
        entered.append(context["state"])
        return strict_note_tool(container, argv, context)

    rig = install_rig(tmp_path / "rig", monkeypatch, tool=tool)

    await rig.run_check()

    ran_on = "\n".join(entered)
    for mode in HOSTILE_MODES:
        assert f"raidar-{mode}/state" not in ran_on
    # a merely unreadable file is the verifier's to handle, so it is called
    assert "raidar-unreadable/state" in ran_on


async def test_a_container_that_cannot_build_hostile_state_is_a_transport_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scratch: Path
) -> None:
    rig = install_rig(tmp_path / "rig", monkeypatch, failing_commands=("mkfifo",))

    with pytest.raises(TransportError, match="could not build fifo state: no space left"):
        await rig.run_check()

    assert rig.exited == 1
    assert list(scratch.iterdir()) == []


async def test_an_unreachable_container_invalidates_the_check_as_integrity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scratch: Path
) -> None:
    rig = install_rig(tmp_path / "rig", monkeypatch)
    rig.container.transport_down = True

    with pytest.raises(TrialInvalid) as error:
        await rig.run_check()

    assert error.value.kind == "integrity"
    assert error.value.detail == "canary transport failed: exec failed"
    assert list(scratch.iterdir()) == []


async def test_a_call_that_changed_the_installed_tree_invalidates_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = install_rig(tmp_path / "rig", monkeypatch)
    rig.container.tampered = True

    with pytest.raises(TrialInvalid) as error:
        await rig.run_check()

    assert error.value.kind == "integrity"
    assert "installed tree changed during the call" in error.value.detail


# ---------------------------------------------------------------- fixture contract, statically


def _write_expect(case: str, text: str) -> Callable[[Path], None]:
    def write(root: Path) -> None:
        (root / "fixtures" / case / "expect.yaml").write_text(text, encoding="utf-8")

    return write


def _write_context(case: str, text: str | None) -> Callable[[Path], None]:
    def write(root: Path) -> None:
        target = root / "fixtures" / case / "context.json"
        if text is None:
            target.unlink()
        else:
            target.write_text(text, encoding="utf-8")

    return write


def _remove(case: str) -> Callable[[Path], None]:
    return lambda root: shutil.rmtree(root / "fixtures" / case)


GHOST = "a" * 64
STATIC: dict[str, tuple[Callable[[Path], None], str]] = {
    "missing-mandatory": (_remove("closed"), "missing mandatory fixture 'closed'"),
    "mandatory-status": (
        _write_expect("done", f"status: resume\noutputs:\n  note: {GHOST}\n"),
        "fixture 'done' must expect status 'done'",
    ),
    "extra-key": (
        _write_expect("closed", "status: closed\nowner: me\n"),
        "expect.yaml is a mapping of exactly status",
    ),
    "unknown-status": (_write_expect("closed", "status: bogus\n"), "is not one of"),
    "not-a-mapping": (_write_expect("closed", "- closed\n"), "exactly status"),
    "bad-digest": (
        _write_expect("done", "status: done\noutputs:\n  note: not-hex\n"),
        "map output names to sha256 hex digests",
    ),
    "done-without-outputs": (
        _write_expect("done", "status: done\n"),
        "fixture 'done' must declare a digest for every output",
    ),
    "undeclared-output": (
        _write_expect("resume", f"status: resume\noutputs:\n  ghost: {GHOST}\n"),
        "fixture 'resume' expects undeclared outputs ['ghost']",
    ),
    "gate-result-without-gate": (
        _write_expect("resume", "status: pass\n"),
        "fixture 'resume' expects a gate result but the package has no gate",
    ),
    "context-not-an-object": (
        _write_context("done", "[1, 2]"),
        "fixture 'done': context.json is not a JSON object",
    ),
    "context-not-json": (_write_context("done", "{"), "fixture 'done': Expecting"),
    "context-missing": (_write_context("done", None), "fixture 'done'"),
}


@pytest.mark.parametrize("name", sorted(STATIC))
async def test_a_fixture_that_breaks_the_contract_stops_the_check_before_any_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    mutate, problem = STATIC[name]
    root = stop_only_note_state(tmp_path / "pkgs")
    mutate(root)
    rig = install_rig(tmp_path / "rig", monkeypatch, load_stage_package(root))

    report = await rig.run_check()

    assert any(problem in item for item in report.static), report.static
    assert report.cases == []
    assert report.record is None
    assert not report.ok
    assert rig.entered == 0
    assert rig.containers[0].calls == []


async def test_a_stop_with_no_resume_rounds_does_not_need_or_check_a_resume_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = stop_only_note_state(tmp_path / "pkgs")
    manifest = root / "stage.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("max_rounds: 2", "max_rounds: 0"),
        encoding="utf-8",
    )
    shutil.rmtree(root / "fixtures" / "resume")
    package = load_stage_package(root)
    rig = install_rig(tmp_path / "rig", monkeypatch, package)

    report = await rig.run_check()

    assert required_cases(package) == ("done", "closed")
    assert static_problems(package) == []
    assert report.ok
    assert "resume" not in _by_case(report)


async def test_a_package_with_neither_a_stop_nor_a_gate_has_nothing_to_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = load_stage_package(write_package(tmp_path / "pkgs"))
    rig = install_rig(tmp_path / "rig", monkeypatch, package)

    report = await rig.run_check()

    assert report.static == ["a package with neither on_stop nor a gate has nothing to check"]
    assert not report.ok
    assert rig.entered == 0


# ---------------------------------------------------------------- gate fixtures


def _gate_rig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, behaviour: Callable[[Path, int], GateOutcome]
) -> CheckRig:
    package = load_stage_package(copy_package("gate-check", tmp_path / "pkgs"))
    return install_rig(tmp_path / "rig", monkeypatch, package, gate=behaviour, shared=False)


def _passing_workspace(workspace: Path) -> bool:
    return (workspace / "spec_ok.py").exists()


async def test_each_gate_fixture_runs_twice_as_the_workspace_of_a_fresh_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = _gate_rig(tmp_path, monkeypatch, honest_gate)

    report = await rig.run_check()

    cases = _by_case(report)
    assert report.ok
    assert (cases["pass"].status, cases["refuse"].status) == ("pass", "refuse")
    assert report.record is None  # only a stop produces a canary record
    main, *gates = rig.containers
    assert len(gates) == 4
    assert len(rig.closed) == 4
    assert rig.entered == rig.exited == 5
    workspaces = [
        sorted(item.name for item in gate.host("/app").iterdir() if item.is_file())
        for gate in gates
    ]
    assert workspaces.count(["calc_ok.py", "pytest.ini", "spec_ok.py"]) == 2
    assert workspaces.count(["calc_bad.py", "pytest.ini", "spec_bad.py"]) == 2
    assert not (main.root / "app").exists()


async def test_a_gate_that_refuses_a_fixture_expected_to_pass_fails_that_case_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def always_refuses(workspace: Path, run: int) -> GateOutcome:
        return gate_outcome(
            Attribution.REFUSED, {"no-marker": "passed", "unit-specs": "failed"}, exit_code=1
        )

    rig = _gate_rig(tmp_path, monkeypatch, always_refuses)

    report = await rig.run_check()

    cases = _by_case(report)
    assert cases["pass"].problems == ("expected pass, got refuse",)
    assert cases["pass"].status == "refuse"
    assert cases["refuse"].ok
    assert not report.ok


async def test_a_gate_that_passes_a_fixture_expected_to_refuse_fails_that_case_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def always_passes(workspace: Path, run: int) -> GateOutcome:
        return gate_outcome(Attribution.PASSED, {"no-marker": "passed", "unit-specs": "passed"})

    rig = _gate_rig(tmp_path, monkeypatch, always_passes)

    report = await rig.run_check()

    cases = _by_case(report)
    assert cases["refuse"].problems == ("expected refuse, got pass",)
    assert cases["pass"].ok


@pytest.mark.parametrize(
    ("statuses", "problem"),
    [
        (
            {"no-marker": "passed", "unit-specs": "skipped"},
            "check gate-check.unit-specs did not pass (status skipped)",
        ),
        ({"no-marker": "passed"}, "check gate-check.unit-specs did not pass (status None)"),
    ],
)
async def test_a_pass_must_show_every_declared_check_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, statuses: dict[str, str], problem: str
) -> None:
    def thin(workspace: Path, run: int) -> GateOutcome:
        if _passing_workspace(workspace):
            return gate_outcome(Attribution.PASSED, statuses)
        return honest_gate(workspace, run)

    rig = _gate_rig(tmp_path, monkeypatch, thin)

    report = await rig.run_check()

    cases = _by_case(report)
    assert cases["pass"].problems == (problem,)
    assert cases["refuse"].ok


async def test_a_check_that_errors_is_a_problem_even_when_the_decision_was_right(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def erroring(workspace: Path, run: int) -> GateOutcome:
        if _passing_workspace(workspace):
            return honest_gate(workspace, run)
        return gate_outcome(
            Attribution.REFUSED, {"no-marker": "error", "unit-specs": "failed"}, exit_code=1
        )

    rig = _gate_rig(tmp_path, monkeypatch, erroring)

    report = await rig.run_check()

    cases = _by_case(report)
    assert cases["refuse"].problems == ("a check errored instead of deciding",)
    assert cases["refuse"].status == "refuse"
    assert cases["pass"].ok


async def test_a_gate_that_cannot_decide_names_the_attribution_and_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crashing(workspace: Path, run: int) -> GateOutcome:
        if _passing_workspace(workspace):
            return gate_outcome(Attribution.AGENT_ERROR, {}, "the tool crashed", exit_code=127)
        return honest_gate(workspace, run)

    rig = _gate_rig(tmp_path, monkeypatch, crashing)

    report = await rig.run_check()

    case = _by_case(report)["pass"]
    assert case.status is None
    assert case.problems == ("the gate could not decide: agent_error: the tool crashed",)


async def test_gate_runs_that_disagree_on_a_check_result_are_unstable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def moody(workspace: Path, run: int) -> GateOutcome:
        outcome = honest_gate(workspace, run)
        if run % 2 == 0:
            return outcome
        checks = [{**check, "exit": 3} for check in outcome.record["checks"]]
        return dataclasses.replace(outcome, record={**outcome.record, "checks": checks})

    rig = _gate_rig(tmp_path, monkeypatch, moody)

    report = await rig.run_check()

    unstable = "two runs differ in attribution, verdict, check results or outputs"
    assert [item.problems for item in report.cases] == [(unstable,), (unstable,)]


async def test_gate_runs_that_differ_only_in_their_reason_text_are_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def chatty(workspace: Path, run: int) -> GateOutcome:
        return dataclasses.replace(honest_gate(workspace, run), reason=f"run number {run}")

    rig = _gate_rig(tmp_path, monkeypatch, chatty)

    report = await rig.run_check()

    assert report.ok


async def test_an_invalid_gate_run_invalidates_the_check_and_still_closes_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scratch: Path
) -> None:
    def tampered(workspace: Path, run: int) -> GateOutcome:
        raise GateInvalid("integrity", "the gate log was rewritten")

    rig = _gate_rig(tmp_path, monkeypatch, tampered)

    with pytest.raises(TrialInvalid) as error:
        await rig.run_check()

    assert error.value.kind == "integrity"
    assert error.value.detail == "the gate log was rewritten"
    assert len(rig.closed) == 1
    assert rig.entered == rig.exited
    assert list(scratch.iterdir()) == []


# ---------------------------------------------------------------- replay of captured trial state


def _snapshot(
    trial: Path, key: str, index: int, note: str | None, context: dict[str, Any] | str | None
) -> Path:
    folder = trial / "attempt-1" / "stages" / key
    before = folder / f"stop-{index}-before"
    before.mkdir(parents=True)
    if note is not None:
        (before / "note.md").write_text(note, encoding="utf-8")
    if context is not None:
        text = context if isinstance(context, str) else json.dumps(context)
        (folder / f"stop-{index}-context.json").write_text(text, encoding="utf-8")
    return before


async def _replay(rig: CheckRig, trial: Path, reference: str = "ns/note-state") -> list[Any]:
    return await replay_trial(
        rig.package,
        reference,
        captured_states(trial, reference),
        image="image",
        docker_context="context",
        logs_dir=rig.root / "logs",
    )


async def test_replay_runs_each_captured_state_with_its_own_recorded_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scratch: Path
) -> None:
    rig = install_rig(tmp_path / "rig", monkeypatch)
    trial = tmp_path / "trial"
    five = "The note has five words.\n"
    first = {"task": "ship it", "params": {"min_words": 3}, "round": 0, "carry": {}}
    later = {"task": "ship it", "params": {"min_words": 9}, "round": 1, "carry": {"words": 2}}
    one = _snapshot(trial, "ns_note-state", 0, five, first)
    two = _snapshot(trial, "ns_note-state", 1, "two words", later)
    lost = _snapshot(trial, "ns_note-state", 2, five, None)
    (trial / "attempt-1" / "stages" / "ns_note-state" / "stop-3-before").write_text("a file")
    before = rig.package.fixture_cases()

    runs = await _replay(rig, trial)

    assert [(run.snapshot, run.status) for run in runs] == [
        (str(lost), None),
        (str(one), "done"),
        (str(two), "resume"),
    ]
    assert runs[0].problem == "refused: no recorded call context for this state"
    assert runs[0].stdout_sha256 == ""
    assert runs[1].problem is None
    assert runs[2].problem is None
    assert runs[1].stdout_sha256 == _sha(stop_json("done", carry={"words": 5}).stdout)
    assert runs[2].stdout_sha256 == _sha(
        stop_json("resume", "Write the note.", {"words": 2}).stdout
    )
    contexts = rig.container.tool_calls("stop/1")
    assert [(c["task"], c["params"], c["round"], c["carry"]) for c in contexts] == [
        ("ship it", {"min_words": 3}, 0, {}),
        ("ship it", {"min_words": 9}, 1, {"words": 2}),
    ]
    assert [Path(c["state"]).parent.name for c in contexts] == ["replay-0", "replay-1"]
    # nothing of the trial or the package was disturbed or leaked
    assert (one / "note.md").read_text(encoding="utf-8") == five
    assert rig.package.fixture_cases() == before
    assert rig.exited == 1
    assert list(scratch.iterdir()) == []


async def test_replay_of_only_context_less_states_refuses_without_starting_a_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = install_rig(tmp_path / "rig", monkeypatch)
    trial = tmp_path / "trial"
    for index in range(2):
        _snapshot(trial, "note-state", index, "words", None)

    runs = await _replay(rig, trial, "note-state")

    assert [run.status for run in runs] == [None, None]
    assert all(run.problem and run.problem.startswith("refused") for run in runs)
    assert rig.entered == 0


async def test_replay_reports_a_captured_state_the_verifier_cannot_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = install_rig(
        tmp_path / "rig",
        monkeypatch,
        tool=lambda container, argv, context: ToolOutcome(b"not json at all"),
    )
    trial = tmp_path / "trial"
    snapshot = _snapshot(trial, "note-state", 0, "words", {"round": 0, "carry": {}})

    (run,) = await _replay(rig, trial, "note-state")

    assert run.snapshot == str(snapshot)
    assert run.status is None
    assert run.problem is not None
    assert "stop output is not JSON" in run.problem
    assert run.stdout_sha256 == _sha(b"not json at all")


async def test_replay_refuses_a_recorded_context_that_is_not_an_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scratch: Path
) -> None:
    rig = install_rig(tmp_path / "rig", monkeypatch)
    trial = tmp_path / "trial"
    _snapshot(trial, "note-state", 0, "words", "[1]")

    with pytest.raises(TrialInvalid) as error:
        await _replay(rig, trial, "note-state")

    assert error.value.kind == "instrument"
    assert "is not a JSON object" in error.value.detail
    assert list(scratch.iterdir()) == []


async def test_replay_needs_a_package_with_a_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = load_stage_package(copy_package("gate-check", tmp_path / "pkgs"))
    rig = install_rig(tmp_path / "rig", monkeypatch, package)

    with pytest.raises(ValueError, match="gate-check declares no stop to replay"):
        await replay_trial(
            package,
            "gate-check",
            [],
            image="image",
            docker_context="context",
            logs_dir=tmp_path / "logs",
        )

    assert rig.entered == 0


def test_captured_states_belong_to_one_reference_and_pair_only_their_own_context(
    tmp_path: Path,
) -> None:
    trial = tmp_path / "trial"
    mine = _snapshot(trial, "ns_note-state", 0, "x", {"round": 0})
    bare = _snapshot(trial, "ns_note-state", 1, "x", None)
    _snapshot(trial, "other", 0, "x", {"round": 5})
    (trial / "attempt-1" / "stages" / "ns_note-state" / "stop-2-before").write_text("a file")

    found = captured_states(trial, "ns/note-state")

    assert [item.snapshot for item in found] == [mine, bare]
    assert found[0].context == mine.with_name("stop-0-context.json")
    assert found[1].context is None


@pytest.mark.parametrize("text", ["status: [", "status: done\noutputs: {note: [", "? [a]\n: b\n"])
async def test_an_expect_file_that_is_not_yaml_is_a_static_refusal_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    root = stop_only_note_state(tmp_path / "pkgs")
    (root / "fixtures" / "closed" / "expect.yaml").write_text(text, encoding="utf-8")
    rig = install_rig(tmp_path / "rig", monkeypatch, load_stage_package(root))

    report = await rig.run_check()

    assert any(
        item.startswith("fixture 'closed': expect.yaml is not valid YAML") for item in report.static
    ), report.static
    assert report.cases == []
    assert rig.entered == 0


def test_the_canonical_note_state_fixtures_meet_the_contract_for_its_stop_and_gate() -> None:
    package = load_stage_package(
        Path(__file__).parent / "fixtures" / "stages" / "acme" / "note-state"
    )

    assert set(required_cases(package)) == {"done", "closed", "resume", "pass", "refuse"}
    assert static_problems(package) == []
