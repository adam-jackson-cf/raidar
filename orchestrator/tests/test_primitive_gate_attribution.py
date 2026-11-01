"""``gate/1`` host decisions, in memory: configuration, attribution, the canary and the seal.

Pure decisions run over records; the container is an injected scripted shell whose answers cover
success, non-zero, malformed and failed-transport outcomes. No check runs, nothing sleeps.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from raidar_contract.package.manifest import GateCheck, Output

from raidar.subject.primitive import calls, gate_tool
from raidar.subject.primitive.gate import (
    AgentFacts,
    Attribution,
    GateInvalid,
    GateOutcome,
    GatePackage,
    GatePaths,
    GateRun,
    GateSettings,
    HookLog,
    attribute,
    check_canary,
    gate_config,
    last_json,
    watched_state,
)
from raidar.subject.primitive.gate_outputs import CheckOutputs

FACTS = AgentFacts(owner="agent", trusted_uid=0)


def test_changed_tool_bytes_or_a_missing_record_are_integrity_failures() -> None:
    assert attribute({"verdict": "passed", "checks": []}, tools_intact=False)[0] is (
        Attribution.INTEGRITY
    )
    nothing = {"verdict": "error", "error": "no record", "checks": []}
    attribution, reason = attribute(nothing)
    assert attribution is Attribution.INTEGRITY
    assert "no record" in reason
    assert attribute({"checks": None})[1] == "the gate printed no record"


def test_passed_and_refused_records_keep_their_verdicts() -> None:
    assert attribute({"verdict": "passed"})[0] is Attribution.PASSED
    assert attribute({"verdict": "refused"})[0] is Attribution.REFUSED
    assert Attribution.REFUSED.value == "refused"


def errored(name: str, cause: str) -> dict[str, str]:
    return {"name": name, "status": "error", "cause": cause}


@pytest.mark.parametrize("cause", ["crashed", "timeout"])
def test_an_errored_check_of_the_agents_code_is_the_agents(cause: str) -> None:
    record = {"verdict": "error", "checks": [errored("p.specs", cause), {"status": "passed"}]}
    attribution, reason = attribute(record)
    assert attribution is Attribution.AGENT_ERROR
    assert reason == f"p.specs: {cause}"


@pytest.mark.parametrize("cause", ["unrunnable", "isolation", "tool_changed"])
def test_a_check_the_instrument_could_not_start_or_trust_is_an_integrity_failure(
    cause: str,
) -> None:
    record = {"verdict": "error", "checks": [errored("p.a", "crashed"), errored("p.b", cause)]}
    attribution, reason = attribute(record)
    assert attribution is Attribution.INTEGRITY
    assert reason == f"a check could not be trusted or started: p.b ({cause})"


@pytest.mark.parametrize(
    ("attribution", "passed", "resumable"),
    [
        (Attribution.PASSED, True, False),
        (Attribution.REFUSED, False, True),
        (Attribution.AGENT_ERROR, False, True),
        (Attribution.INTEGRITY, False, False),
    ],
)
def test_only_an_agent_outcome_that_did_not_pass_is_resumable(
    attribution: Attribution, passed: bool, resumable: bool
) -> None:
    outcome = GateOutcome(attribution, {})
    assert (outcome.passed, outcome.resumable) == (passed, resumable)


# the canary -------------------------------------------------------------------------------------


@pytest.mark.parametrize("verdict", ["passed", "refused"])
def test_a_canary_that_decided_is_a_working_instrument_even_when_it_refused(verdict: str) -> None:
    check_canary({"verdict": verdict, "checks": [errored("p.a", "crashed")]})


@pytest.mark.parametrize(
    ("record", "detail"),
    [
        ({"verdict": "error", "checks": [errored("p.a", "unrunnable")]}, "p.a: unrunnable"),
        (
            {"verdict": "error", "checks": [errored("p.a", "x"), errored("p.b", "y")]},
            "p.a: x; p.b: y",
        ),
        ({"verdict": "error", "error": "no record", "checks": []}, "no record"),
        ({}, "the canary printed no verdict"),
    ],
)
def test_a_canary_that_could_not_decide_invalidates_the_instrument(
    record: dict[str, Any], detail: str
) -> None:
    with pytest.raises(GateInvalid) as caught:
        check_canary(record)
    assert caught.value.category == "instrument"
    assert str(caught.value) == f"the gate's canary failed on the starting workspace: {detail}"


def test_the_last_json_object_line_is_the_payloads_record() -> None:
    text = 'noise\n{"a": 1}\n[2]\nnot json\n{"b": 2}\n3\n'
    assert last_json(text) == {"b": 2}
    assert last_json('{"a": 1}\nstray') == {"a": 1}
    assert last_json("[1]\n7\nplain") is None
    assert last_json("") is None


# the payload's configuration ---------------------------------------------------------------------


@dataclass
class FakeInstalled:
    """The installed package as the gate sees it (``InstalledPackage``'s shape)."""

    path: str = "/tools/bin:/usr/bin"
    root: str = "/tools"
    python: str = "/tools/bin/python"
    digest: str = "deadbeef"
    ok: bool = True

    async def verify(self) -> Any:
        return SimpleNamespace(ok=self.ok, digest="now", expected=self.digest)


PATHS = GatePaths(
    workspace="/app", home="/h", state="/s", system_python="/py", reap=False, trusted_root="/t",
    confine=False, tool_owner=7,
)  # fmt: skip


def package(*checks: GateCheck, **over: Any) -> GatePackage:
    return GatePackage("pkg", FakeInstalled(), checks, **over)  # type: ignore[arg-type]


def command(name: str, **extra: Any) -> GateCheck:
    return GateCheck(name=name, run=("tool", name), **extra)


def pytest_package(params: dict[str, Any], *, kind: str = "pytest") -> GatePackage:
    check = GateCheck(name="specs", kind=kind, run=("x",))  # type: ignore[arg-type]
    return package(check, context={"params": params})


def test_a_pytest_check_may_set_a_per_test_timeout_from_the_stage_params() -> None:
    def entry(pkg: GatePackage) -> dict[str, Any]:
        return gate_config(PATHS, [pkg], FACTS, hook_sha256="h")["package_checks"][0]

    assert entry(pytest_package({"test_timeout": 12}))["test_timeout"] == 12
    assert "test_timeout" not in entry(pytest_package({}))
    assert "test_timeout" not in entry(pytest_package({"test_timeout": 0}, kind="command"))


@pytest.mark.parametrize("bad", [0, -3, True, "5", 1.5])
def test_a_pytest_timeout_that_is_not_a_positive_integer_is_refused(bad: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        gate_config(PATHS, [pytest_package({"test_timeout": bad})], FACTS, hook_sha256="h")


def test_watched_state_is_one_check_free_prerequisite_per_package_and_directory() -> None:
    watch = "/app/.raidar/state"
    a = package(command("one", watch=watch), command("two", watch=watch), command("three"))
    installed = FakeInstalled()
    b = GatePackage("other", installed, (command("four", watch=watch),))  # type: ignore[arg-type]
    found = watched_state([a, b])
    pairs = [(w["name"], w["watch"]) for w in found]
    assert pairs == [("pkg.watch", watch), ("other.watch", watch)]
    assert all(w["watch_only"] and w["argv"] == [] for w in found)
    assert watched_state([package(command("plain"))]) == []
    assert gate_tool.watches(found) == [("pkg", watch, set()), ("other", watch, set())]


# the hook's log is the agent's: what it holds is a reading ---------------------------------------


def test_a_hook_log_may_only_grow() -> None:
    log = HookLog()
    assert log.observe("a\n", "1", "t1") == []
    assert log.observe("a\nb\n", "2", "t2") == []  # appended: the new ctime is no reading
    assert log.observe("a\nb\n", "2", "t3") == []
    assert log.observe("a\nb\n", None, "t4") == []  # a missing ctime cannot be compared
    assert log.observe("a\nb\n", "9", "t5") == []  # establish ctime after an unknown sample
    touched = log.observe("a\nb\n", "10", "t5")
    assert [f["name"] for f in touched] == ["hook_log_touched"]
    rewritten = log.observe("x\n", "9", "t6")
    assert [f["name"] for f in rewritten] == ["hook_log_rewritten"]
    assert rewritten[0]["after"] == "t6"
    assert [f["name"] for f in log.observe("a\n", "9", "t7")] == ["hook_log_rewritten"]  # truncated
    assert log.seen == "a\nb\n"  # a rewritten log never replaces what was first seen


def test_the_findings_a_hook_wrote_are_carried_once_and_tagged_with_the_turn() -> None:
    line = json.dumps({"integrity": [{"name": "n", "detail": "d", "after": "commit"}]})
    text = "\n".join([line, "not json", "[1]", json.dumps({"verdict": "passed"}), ""])
    log = HookLog()
    found = log.observe(text, None, "turn-1")
    assert found == [{"name": "n", "detail": "d", "after": "commit (turn-1)"}]
    assert log.observe(text, None, "turn-2") == []  # the same lines are not read twice
    more = text + json.dumps({"integrity": [{"name": "m"}]}) + "\n"
    assert [f["after"] for f in log.observe(more, None, "turn-3")] == [" (turn-3)"]


# the checkpoint's gate over a scripted container -------------------------------------------------


class Container:
    """The container's shell as a script: each payload command answers as told."""

    def __init__(self, **replies: Any) -> None:
        self.replies = replies
        self.commands: list[str] = []
        self.uploads: list[str] = []
        self.snapshots: list[str] = []
        self.broken: Exception | None = None

    def reply(self, command: str) -> Any:
        script = command.split("\n", 1)[-1]
        for suffix, key, default in (("id -un", "owner", "agent"), ("id -u", "uid", "0")):
            if script.endswith(suffix):
                return self.replies.get(key, default)
        if script.startswith("cat "):
            return self.replies.get("hooklog", "")
        if script.startswith("/py -I"):
            return self.replies.get("ctime", "")
        if script.startswith("/py -B "):
            name = script.split()[3]
            if name == "snapshot":
                return self.snapshots.pop(0) if self.snapshots else ""
            return self.replies.get(name, "")
        return ""

    async def exec(self, command: str, **_: Any) -> Any:
        self.commands.append(command)
        if self.broken is not None:
            raise self.broken
        reply = self.reply(command)
        code, out, err = reply if isinstance(reply, tuple) else (0, reply, "")
        return SimpleNamespace(return_code=code, stdout=out, stderr=err)

    async def upload_file(self, source: Path, target: str) -> None:
        self.uploads.append(target)


PASSING = {
    "workspace-setup": '{"base": "abc"}\n',
    "setup-check": '{"status": "passed", "checks": []}\n',
    "canary": '{"verdict": "passed", "checks": []}\n',
}


def gate(
    tmp_path: Path,
    pkg: GatePackage | None = None,
    settings: GateSettings | None = None,
    **replies: Any,
) -> tuple[GateRun, Container]:
    container = Container(**{**PASSING, **replies})
    run = GateRun(
        container,  # type: ignore[arg-type]
        tmp_path / "logs",
        [pkg or package(command("lint"), context={"task": "x", "base": "forged"})],
        settings,
        PATHS,
    )
    return run, container


async def test_prepare_publishes_the_start_commit_over_authored_context(tmp_path: Path) -> None:
    run, container = gate(tmp_path)
    await run.prepare()

    assert run.base == "abc"
    kept = tmp_path / "logs" / "gate" / "context" / "pkg.json"
    assert json.loads(kept.read_text()) == {"task": "x", "base": "abc"}
    assert "/h/context/pkg.json" in container.uploads
    assert {"/h/gate_tool.py", "/h/gate.json", "/h/hooks/pre-commit"} <= set(container.uploads)
    record = run.record()
    assert record["base"] == "abc"
    assert record["canary"]["verdict"] == "passed"
    assert record["setup_check"]["status"] == "passed"
    assert (tmp_path / "logs" / "gate" / "canary.json").is_file()
    assert "baseline" not in record


@pytest.mark.parametrize("answer", ["", "garbage\n", '{"other": 1}\n', "[1]\n"])
async def test_a_workspace_setup_without_a_base_commit_is_an_integrity_failure(
    tmp_path: Path, answer: str
) -> None:
    run, _ = gate(tmp_path, **{"workspace-setup": answer})
    with pytest.raises(GateInvalid) as caught:
        await run.prepare()
    assert caught.value.category == "integrity"
    assert run.base is None


async def test_a_root_or_user_step_that_exits_non_zero_is_an_integrity_failure(
    tmp_path: Path,
) -> None:
    run, _ = gate(tmp_path, uid=(1, "", "denied"))
    with pytest.raises(GateInvalid, match="exit 1.*denied") as caught:
        await run.prepare()
    assert caught.value.category == "integrity"


async def test_a_failed_container_call_is_a_transport_error_not_a_verdict(tmp_path: Path) -> None:
    run, container = gate(tmp_path)
    container.broken = ConnectionError("the container went away")
    with pytest.raises(calls.TransportError, match="went away"):
        await run.prepare()


@pytest.mark.parametrize(
    ("answer", "detail"),
    [
        (
            '{"status": "failed", "checks": [{"name": "git", "status": "failed", "detail": "no"},'
            ' {"name": "ok", "status": "passed"}]}\n',
            "git: no",
        ),
        ("", "exit 3: boom"),
        ("not json\n", "exit 3: boom"),
    ],
)
async def test_a_failed_or_unreadable_setup_check_invalidates_the_instrument(
    tmp_path: Path, answer: str, detail: str
) -> None:
    reply: Any = answer if answer.startswith("{") else (3, answer, "boom")
    run, _ = gate(tmp_path, **{"setup-check": reply})
    with pytest.raises(GateInvalid) as caught:
        await run.prepare()
    assert caught.value.category == "instrument"
    assert "the gate's setup check failed: " in str(caught.value)
    assert detail in str(caught.value)
    assert (tmp_path / "logs" / "gate" / "setup-check.json").is_file()


@pytest.mark.parametrize(
    ("answer", "detail"),
    [
        (
            '{"verdict": "error", "checks": [{"status": "error", "name": "p.a", "cause": "x"}]}',
            "p.a: x",
        ),
        ("", "the canary printed no record (exit 0)"),
        ("garbage", "the canary printed no record (exit 0)"),
    ],
)
async def test_a_canary_that_cannot_decide_stops_the_gate_before_the_agent(
    tmp_path: Path, answer: str, detail: str
) -> None:
    run, _ = gate(tmp_path, canary=answer)
    with pytest.raises(GateInvalid, match=re.escape(detail)) as caught:
        await run.prepare()
    assert caught.value.category == "instrument"
    assert run.record()["canary"] is not None  # the evidence is kept for the report


async def test_a_refusing_canary_is_still_a_working_instrument(tmp_path: Path) -> None:
    run, _ = gate(tmp_path, canary='{"verdict": "refused", "checks": []}')
    await run.prepare()
    assert run.record()["canary"]["verdict"] == "refused"


async def test_a_changed_tool_tree_before_the_agent_invalidates_the_trial(tmp_path: Path) -> None:
    pkg = package(command("lint"))
    pkg.installed.ok = False  # type: ignore[misc]
    run, _ = gate(tmp_path, pkg)
    with pytest.raises(GateInvalid) as caught:
        await run.prepare()
    assert caught.value.category == "integrity"
    assert run.record()["integrity"][0]["name"] == "tool_tree_changed"


async def test_the_baseline_is_evidence_whatever_it_found(tmp_path: Path) -> None:
    run, container = gate(
        tmp_path,
        settings=GateSettings(baseline=True),
        baseline='{"status": "passed", "exit": 0, "output": "x", "tests": {"collected": []}}',
    )
    await run.prepare()
    record = run.record()["baseline"]
    assert record == {"status": "passed", "exit": 0, "record": "gate/baseline.json"}
    assert any(" baseline " in command for command in container.commands)

    silent, _ = gate(tmp_path / "again", settings=GateSettings(baseline=True), baseline="")
    await silent.prepare()  # an unreadable baseline never invalidates the trial
    assert silent.record()["baseline"]["status"] == "error"
    assert "no record (exit 0)" in silent.record()["baseline"]["error"]


def seal_record(**fields: Any) -> str:
    return json.dumps({"verdict": "passed", "checks": [], "tree": "t", **fields}) + "\n"


async def test_each_seal_is_numbered_recorded_and_attributed(tmp_path: Path) -> None:
    run, container = gate(tmp_path, seal=seal_record(output="long", files=["f"]))
    await run.prepare()

    first = await run.seal("turn-1")
    container.replies["seal"] = seal_record(
        verdict="refused", checks=[{"name": "pkg.lint", "status": "failed", "tests": {"a": 1}}]
    )
    second = await run.seal("turn-2")
    container.replies["seal"] = seal_record(
        verdict="error", checks=[errored("pkg.lint", "crashed")]
    )
    third = await run.seal("turn-3")
    container.replies["seal"] = seal_record(
        verdict="error", checks=[errored("pkg.lint", "unrunnable")]
    )
    fourth = await run.seal("turn-4")

    assert [o.attribution for o in (first, second, third, fourth)] == [
        Attribution.PASSED, Attribution.REFUSED, Attribution.AGENT_ERROR, Attribution.INTEGRITY,
    ]  # fmt: skip
    assert [o.resumable for o in (first, second, third, fourth)] == [False, True, True, False]
    assert third.reason == "pkg.lint: crashed"
    assert first.record["record"] == "gate/gate-1.json"
    assert first.record["attribution"] == "passed"
    stored = json.loads((tmp_path / "logs" / "gate" / "gate-2.json").read_text())
    assert stored["attribution"] == "refused"
    recorded = run.record()["gates"]
    assert all("output" not in gate_ and "files" not in gate_ for gate_ in recorded)
    assert "tests" not in recorded[1]["checks"][0]  # per-test records stay out of the report
    seal_commands = [c for c in container.commands if " seal " in c]
    assert [c.split("--run ")[1].split()[0] for c in seal_commands] == ["1", "2", "3", "4"]
    assert all("--base abc" in c for c in seal_commands)


@pytest.mark.parametrize(
    ("reply", "fragment"),
    [("", "the seal printed no record (exit 4): out of memory"), ("not json\n", "no record")],
)
async def test_a_seal_that_printed_no_record_is_an_integrity_failure(
    tmp_path: Path, reply: str, fragment: str
) -> None:
    run, _ = gate(tmp_path)
    await run.prepare()
    run.environment.replies["seal"] = (4, reply, "out of memory")  # type: ignore[attr-defined]
    outcome = await run.seal("turn-1")
    assert outcome.attribution is Attribution.INTEGRITY
    assert fragment in outcome.reason
    assert outcome.record["verdict"] == "error"
    assert outcome.record["output"] == reply
    assert not outcome.resumable


async def test_a_tool_tree_changed_during_the_turn_makes_the_seal_an_integrity_failure(
    tmp_path: Path,
) -> None:
    pkg = package(command("lint"))
    run, _ = gate(tmp_path, pkg, seal=seal_record())
    await run.prepare()
    pkg.installed.ok = False  # type: ignore[misc]
    outcome = await run.seal("turn-1")
    assert outcome.attribution is Attribution.INTEGRITY
    assert outcome.reason == "the installed tool tree changed"
    names = [item["name"] for item in run.record()["integrity"]]
    assert names == ["tool_tree_changed", "tool_tree_changed"]  # before and after the seal


async def test_checks_that_changed_the_work_tree_are_an_integrity_reading(tmp_path: Path) -> None:
    moved = [f"f{n}.txt" for n in range(12)]
    run, _ = gate(tmp_path, seal=seal_record(changed_during_gate=moved))
    await run.prepare()
    outcome = await run.seal("turn-1")
    assert outcome.passed  # a reading, never a refusal
    (finding,) = run.record()["integrity"]
    assert finding["name"] == "workspace_changed_during_gate"
    assert "f9.txt" in finding["detail"]
    assert "f10.txt" not in finding["detail"]  # only the first ten are named


def snapshot(sha: str, ctime: int) -> str:
    entry = {"sha256": sha, "ctime_ns": ctime}
    return (
        json.dumps({"dir": "/app/.raidar/state", "present": True, "files": {"state.json": entry}})
        + "\n"
    )


async def test_watched_state_that_moved_during_a_seal_is_witnessed(tmp_path: Path) -> None:
    pkg = package(command("lint", watch="/app/.raidar/state"))
    run, container = gate(tmp_path, pkg, seal=seal_record())
    await run.prepare()
    container.snapshots = [snapshot("a", 1), snapshot("b", 2)]

    outcome = await run.seal("turn-1")

    assert outcome.passed
    (witness,) = outcome.record["witness"]
    assert witness["label"] == "pkg"
    assert [f["name"] for f in witness["findings"]] == ["pkg_state_rewritten"]
    assert [f["name"] for f in run.record()["integrity"]] == ["pkg_state_rewritten"]


async def test_watched_state_that_could_not_be_read_is_not_a_finding(tmp_path: Path) -> None:
    pkg = package(command("lint", watch="/app/.raidar/state"))
    run, container = gate(tmp_path, pkg, seal=seal_record())
    await run.prepare()
    container.snapshots = ["", "garbage"]  # no snapshot either side: nothing to compare
    outcome = await run.seal("turn-1")
    assert outcome.record["witness"] == [{"label": "pkg", "findings": []}]
    assert run.record()["integrity"] == []


async def test_a_rewritten_hook_log_is_a_reading_and_never_changes_attribution(
    tmp_path: Path,
) -> None:
    run, container = gate(tmp_path, seal=seal_record(), hooklog="one\n")
    await run.prepare()
    first = await run.seal("turn-1")
    container.replies["hooklog"] = "forged\n"
    second = await run.seal("turn-2")

    assert first.passed
    assert second.passed
    found = [item["name"] for item in run.record()["integrity"]]
    assert "hook_log_rewritten" in found
    assert "hook_log_chain_broken" not in found
    copied = tmp_path / "logs" / "turn-2" / "hook-runs.jsonl"
    assert copied.read_text() == "forged\n"  # the agent's log is kept as it was read


async def test_a_failed_output_copy_during_a_seal_is_an_integrity_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = gate(tmp_path, seal=seal_record())
    await run.prepare()

    async def unreachable(*_: Any) -> None:
        raise calls.TransportError("the container went away")

    monkeypatch.setattr(run.outputs, "keep", unreachable)
    with pytest.raises(GateInvalid, match="output copy failed") as caught:
        await run.seal("turn-1")
    assert caught.value.category == "integrity"


async def test_leftover_processes_are_reaped_before_a_seal_when_enabled(tmp_path: Path) -> None:
    container = Container(**PASSING, seal=seal_record(), reap='{"killed": [1]}')
    paths = GatePaths(**{**PATHS.__dict__, "reap": True})
    pkgs = [package(command("lint"))]
    run = GateRun(container, tmp_path / "logs", pkgs, paths=paths)  # type: ignore[arg-type]
    await run.prepare()
    await run.seal("turn-1")
    container.replies["reap"] = "garbage"
    container.replies["seal"] = seal_record()
    await run.seal("turn-2")
    assert run.record()["reaps"][0] == {"killed": [1]}
    assert "error" in run.record()["reaps"][1]
    cmds = container.commands
    reap_at = next(i for i, c in enumerate(cmds) if c.endswith(" reap"))
    seal_at = next(i for i, c in enumerate(cmds) if " seal " in c)
    assert reap_at < seal_at


async def test_no_prefix_tree_is_kept_without_fix_rounds(tmp_path: Path) -> None:
    run, container = gate(tmp_path, settings=GateSettings(max_fix_rounds=0))
    assert await run.retain_prefix(1) is None
    assert container.commands == []
    assert run.record()["pre_fix_snapshots"] == []


async def test_closing_records_what_failed_and_a_gate_never_opened(tmp_path: Path) -> None:
    unopened, _ = gate(tmp_path / "unopened")
    await unopened.close()
    assert unopened.record()["errors"] == ["the gate was not set up before the agent started"]

    run, container = gate(tmp_path / "opened")
    await run.prepare()
    await run.close()
    assert run.record()["errors"] == []
    assert (tmp_path / "opened" / "logs" / "gate" / "hook-runs.jsonl").is_file()

    container.broken = ConnectionError("gone")
    await run.close()
    (error,) = run.record()["errors"]
    assert error.startswith("TransportError: ")
    assert "gone" in error


# the host copies of what each check captured -----------------------------------------------------

NOTE = Output.model_construct(path="/app/note.md", contract=None)
DECLARED = {"pkg.lint": {"note": NOTE}}


def outputs(tmp_path: Path) -> CheckOutputs:
    return CheckOutputs(None, "/s/scratch", tmp_path / "out", DECLARED)


def kept(check: dict[str, Any]) -> dict[str, Any]:
    return check["outputs"]["note"]


@pytest.mark.parametrize(
    ("captured", "error"),
    [
        (None, "the check captured nothing"),
        ("a string", "the check captured nothing"),
        ({"violation": "outside bounds"}, "outside bounds"),
        ({"path": "/app/note.md"}, "malformed capture"),
        ({"entries": [], "tar": "/etc/passwd.tar"}, ""),
        ({"entries": [], "tar": "/s/scratch/outputs/../../x.tar"}, ""),
        ({"entries": []}, ""),
    ],
)
async def test_a_capture_that_is_absent_malformed_or_outside_scratch_is_that_outputs_error(
    tmp_path: Path, captured: object, error: str
) -> None:
    check: dict[str, Any] = {"name": "pkg.lint", "outputs": {"note": captured}}
    await outputs(tmp_path).keep([check], "seal-1")
    assert kept(check)["host_path"] is None
    assert error in kept(check)["error"]
    assert kept(check)["error"]  # evidence, never a failure of the gate


async def test_a_check_with_no_capture_or_no_declaration_is_left_as_it_is(tmp_path: Path) -> None:
    plain: dict[str, Any] = {"name": "pkg.lint"}
    undeclared: dict[str, Any] = {"name": "pkg.other", "outputs": {"note": None}}
    copies = outputs(tmp_path)
    await copies.keep("not a list", "seal-1")
    await copies.keep([plain, "junk", undeclared], "seal-1")
    assert plain == {"name": "pkg.lint"}
    assert undeclared["outputs"] == {}  # nothing declared: nothing kept


async def test_each_new_hook_run_is_copied_once_by_its_line_number(tmp_path: Path) -> None:
    copies = outputs(tmp_path)
    run = json.dumps({"checks": [{"name": "pkg.lint", "outputs": {"note": None}}], "seq": 99})
    plain = json.dumps({"checks": [{"name": "pkg.lint"}]})
    log = "\n".join(["not json", "[1]", json.dumps({"checks": "x"}), plain, "", run])

    await copies.keep_hook(log)
    assert [hook["seq"] for hook in copies.hook_runs] == [5]  # the agent's own "seq" is ignored
    (check,) = copies.hook_runs[0]["checks"]
    assert check["name"] == "pkg.lint"
    assert check["outputs"]["note"]["error"] == "the check captured nothing"

    await copies.keep_hook(log)  # the same log again adds nothing
    assert len(copies.hook_runs) == 1
    await copies.keep_hook(log + "\n" + run)
    assert [hook["seq"] for hook in copies.hook_runs] == [5, 6]


@pytest.mark.parametrize("tampered", [False, True])
async def test_captured_output_is_retained_only_when_archive_bytes_match_the_manifest(
    tmp_path: Path, tampered: bool
) -> None:
    data = b"the note\n"
    rows = [
        {
            "path": "note.md",
            "type": "file",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "mode": 0o644,
        }
    ]
    captured = {
        "path": NOTE.path,
        "exists": True,
        "entries": rows,
        "digest": hashlib.sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "files": 1,
        "bytes": len(data),
        "violation": None,
        "tar": "/s/scratch/outputs/note.tar",
    }
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        member = tarfile.TarInfo("note.md")
        member.size = len(data)
        archive.addfile(member, io.BytesIO(b"bad note\n" if tampered else data))
    bundle = stream.getvalue()

    class Snapshot:
        async def exec(self, *args: Any, **kw: Any) -> Any:
            return SimpleNamespace(return_code=0, stdout=str(len(bundle)), stderr="")

        async def download_file(self, source: str, target: Path) -> None:
            target.write_bytes(bundle)

    copies = CheckOutputs(Snapshot(), "/s/scratch", tmp_path / "out", DECLARED)
    check = {"name": "pkg.lint", "outputs": {"note": captured}}
    await copies.keep([check], "seal-1")

    record = kept(check)
    if tampered:
        assert "changed while it was copied" in record["error"]
        assert record["host_path"] is None
        assert not (tmp_path / "out" / "seal-1" / "pkg.lint" / "note").exists()
    else:
        assert record["error"] is None
        assert record["sha256"] == hashlib.sha256(data).hexdigest()
        assert record["bytes"] == len(data)
        assert Path(record["host_path"]).read_bytes() == data


async def test_a_prefix_snapshot_retains_the_fix_input_and_its_digest(tmp_path: Path) -> None:
    class Workspace(Container):
        async def download_dir_with_exclusions(
            self, *, source_dir: str, target_dir: Path, exclude: list[str]
        ) -> None:
            target_dir.mkdir(parents=True)
            (target_dir / "main.py").write_text("raise ValueError('not repaired yet')\n")

    run = GateRun(Workspace(), tmp_path, [], GateSettings(max_fix_rounds=2), PATHS)
    first = await run.retain_prefix(1)
    second = await run.retain_prefix(2)

    assert first is not None
    assert second is not None
    assert first["tree_digest"] == second["tree_digest"]
    assert first["path"] != second["path"]
    assert (tmp_path / first["path"] / "main.py").read_text().startswith("raise ValueError")
    assert run.record()["pre_fix_snapshots"] == [first, second]


async def test_an_unverifiable_trusted_payload_prevents_gate_preparation(tmp_path: Path) -> None:
    class UnverifiablePayload(Container):
        async def upload_dir(self, source: Path, target: str) -> None:
            pass

    run = GateRun(UnverifiablePayload(), tmp_path, [], paths=GatePaths())

    with pytest.raises(calls.TransportError, match="trusted payload does not match"):
        await run.prepare()

    assert run.record()["canary"] is None
    assert run.record()["gates"] == []


def test_a_requested_baseline_is_absent_until_the_instrument_has_run(tmp_path: Path) -> None:
    run, _ = gate(tmp_path, settings=GateSettings(baseline=True))

    assert run.record()["baseline"] is None
