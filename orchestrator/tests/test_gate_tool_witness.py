"""The gate payload's decisions, driven in memory: witnessing, check outcomes and the verdict.

Shell outcomes come from an injected supervisor (success, non-zero, crash, timeout, refusal,
malformed result) and an injected ``subprocess.run``; no process starts and nothing sleeps.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from raidar.subject.primitive import gate_tool
from raidar.subject.primitive.gate_tool import (
    CheckResult,
    Decision,
    session_findings,
    watches,
    witness_findings,
)


def seen(sha: str, ctime: int) -> dict[str, Any]:
    return {"files": {"state.json": {"sha256": sha, "ctime_ns": ctime}}}


def test_a_watched_state_file_written_and_restored_or_changed_during_a_gate_run_is_named() -> None:
    same = witness_findings("define", seen("a", 1), seen("a", 1), set(), "w")
    touched = witness_findings("define", seen("a", 1), seen("a", 2), set(), "w")
    changed = witness_findings("define", seen("a", 1), seen("b", 2), set(), "w")
    added = witness_findings("define", {"files": {}}, seen("a", 1), set(), "w")
    allowed = witness_findings("define", seen("a", 1), seen("b", 2), {"state.json"}, "w")

    assert same == []
    assert allowed == []
    assert [f["name"] for f in touched] == ["define_state_touched"]
    assert [f["name"] for f in changed] == ["define_state_rewritten"]
    assert [f["name"] for f in added] == ["define_state_rewritten"]


def test_a_removed_file_is_rewritten_state_and_allowance_is_by_basename() -> None:
    removed = witness_findings("define", seen("a", 1), {"files": {}}, set(), "w")
    nested = {"files": {"sub/note.md": {"sha256": "a", "ctime_ns": 1}}}
    moved = {"files": {"sub/note.md": {"sha256": "b", "ctime_ns": 2}}}

    assert [f["name"] for f in removed] == ["define_state_rewritten"]
    assert removed[0]["after"] == "w"
    assert witness_findings("define", nested, moved, {"note.md"}, "w") == []
    assert witness_findings("define", {}, {}, set(), "w") == []


def test_watched_directories_come_from_prerequisites_with_a_watch_only() -> None:
    found = watches(
        [
            {"name": "define.watch", "watch": "/app/.raidar/a", "writes": ["note.md"]},
            {"name": "plain", "argv": ["x"]},
            {"name": "gate-check.note", "watch": "/app/.raidar/b"},
        ]
    )
    assert found == [
        ("define", "/app/.raidar/a", {"note.md"}),
        ("gate-check", "/app/.raidar/b", set()),
    ]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _session(tmp_path: Path, data: bytes, ctime: int = 1) -> dict[str, Any]:
    (tmp_path / "s.jsonl").write_bytes(data)
    entry = {"sha256": _digest(data), "size": len(data), "ctime_ns": ctime}
    return {"dir": str(tmp_path), "files": {"s.jsonl": entry}}


def test_a_session_store_may_only_be_appended_to_during_a_gate_run(tmp_path: Path) -> None:
    before = _session(tmp_path, b"one\n")
    kept = {"files": {"s.jsonl": {"size": 4, "ctime_ns": 1}}}
    appended = {"files": {"s.jsonl": {"size": 8, "ctime_ns": 2}}}
    touched = {"files": {"s.jsonl": {"size": 4, "ctime_ns": 9}}}

    assert session_findings(before, kept, "w") == []
    assert session_findings(before, appended, "w") == []
    assert [f["name"] for f in session_findings(before, touched, "w")] == ["session_log_touched"]
    assert [f["name"] for f in session_findings(before, {}, "w")] == ["session_log_removed"]

    (tmp_path / "s.jsonl").write_bytes(b"two\n")
    rewritten = session_findings(before, appended, "w")
    assert [f["name"] for f in rewritten] == ["session_log_rewritten"]
    (tmp_path / "s.jsonl").unlink()  # unreadable now: it no longer begins with the bytes seen
    assert [f["name"] for f in session_findings(before, kept, "w")] == ["session_log_rewritten"]
    skipped = {"dir": str(tmp_path), "files": {"s.jsonl": {"error": "unreadable"}}}
    assert session_findings(skipped, {}, "w") == []


# the pytest kind and the verdict ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "tests", "expected"),
    [
        ("passed", {"collected": ["a"], "outcomes": {"a": "passed"}}, "passed"),
        ("passed", {"collected": ["a"], "outcomes": {"a": "skipped"}}, "passed"),
        ("passed", {"collected": ["a", "b"], "outcomes": {"a": "failed", "b": "passed"}}, "failed"),
        ("passed", {"collected": [], "outcomes": {}}, "failed"),
        ("passed", {"collected": ["a"]}, "passed"),
        ("passed", None, "failed"),
        ("error", None, "error"),
        ("failed", {"collected": ["a"], "outcomes": {"a": "passed"}}, "failed"),
    ],
)
def test_pytest_passes_only_with_a_collected_test_and_no_failure(
    status: str, tests: dict[str, Any] | None, expected: str
) -> None:
    assert gate_tool._pytest_status(status, tests) == expected


def result(name: str, status: str, *, blocking: bool = True, output: str = "") -> CheckResult:
    return CheckResult(name, "command", blocking, status, 0, 0.0, output)


def test_the_verdict_is_error_over_refused_over_passed() -> None:
    passed = gate_tool._verdict([result("a", "passed")], 1.0)
    refused = gate_tool._verdict([result("a", "passed"), result("b", "failed")], 1.0)
    erred = gate_tool._verdict(
        [result("a", "failed"), result("b", "error", output=" x" * 300)], 1.0
    )
    advisory = gate_tool._verdict([result("a", "failed", blocking=False)], 1.0)

    assert (passed.verdict, refused.verdict, erred.verdict, advisory.verdict) == (
        "passed", "refused", "error", "passed",
    )  # fmt: skip
    assert passed.error is None
    assert erred.error is not None
    assert erred.error.startswith("b: ")
    assert len(erred.error) == len("b: ") + 300  # only the tail of a long output is kept


def test_a_decision_reports_without_output_and_prints_what_did_not_pass() -> None:
    decision = Decision(
        "error", 1.0,
        [
            result("ok", "passed", output="quiet"),
            result("w", "failed", blocking=False, output="why\n"),
        ],
        error="boom",
    )  # fmt: skip
    assert all("output" not in row for row in decision.summary())
    text = decision.text()
    assert text.startswith("verify-gate: error\n")
    assert "verify-gate: ERROR, the gate could not run: boom" in text
    assert "- w (command, warning): failed, exit 0\nwhy\n" in text
    assert "quiet" not in text  # a passing check's output is not printed
    assert "ERROR" not in Decision("passed", 0.0).text()


# prerequisites run through an injected subprocess ----------------------------------------------


def completed(code: int, out: str = "", err: str = "") -> Callable[..., Any]:
    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, code, out, err)

    return run


@pytest.mark.parametrize(
    ("code", "status"),
    [(0, "passed"), (1, "failed"), (126, "error"), (127, "error"), (-9, "error")],
)
def test_a_prerequisite_exit_status_decides_its_outcome(
    monkeypatch: pytest.MonkeyPatch, code: int, status: str
) -> None:
    monkeypatch.setattr(subprocess, "run", completed(code, "out", "err"))
    (check,) = gate_tool.prerequisites({"prerequisites": [{"name": "p", "argv": ["x"]}]})
    assert (check.status, check.exit, check.category) == (status, code, "prerequisite")
    assert check.output == "outerr"
    assert check.input_sha256 == _digest(b"outerr")


@pytest.mark.parametrize("failure", [OSError("no such tool"), subprocess.TimeoutExpired("x", 1)])
def test_a_prerequisite_that_cannot_run_or_times_out_is_an_error(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    def broken(*_: Any, **__: Any) -> Any:
        raise failure

    monkeypatch.setattr(subprocess, "run", broken)
    (check,) = gate_tool.prerequisites({"prerequisites": [{"name": "p", "argv": ["x"]}]})
    assert (check.status, check.exit) == ("error", None)


def test_a_watched_directory_is_not_a_check_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_: Any, **__: Any) -> Any:
        raise AssertionError("a watch-only prerequisite runs nothing")

    monkeypatch.setattr(subprocess, "run", forbidden)
    config = {"prerequisites": [{"name": "p.watch", "argv": [], "watch": "/s", "watch_only": True}]}
    assert gate_tool.prerequisites(config) == []
    assert gate_tool.prerequisites({}) == []


# package checks through an injected supervisor -------------------------------------------------


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


class Supervisor:
    """The trusted call supervisor's two entry points, answering with a scripted result."""

    def __init__(self, reply: dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.reply = reply
        self.specs: list[dict[str, Any]] = []

    def build_spec(self, **spec: Any) -> dict[str, Any]:
        return spec

    def supervise(self, spec: dict[str, Any]) -> dict[str, Any]:
        self.specs.append(spec)
        return self.reply(spec) if callable(self.reply) else dict(self.reply)


@pytest.fixture
def tools(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("zorp", "python"):
        (bin_dir / name).write_text("#!/bin/sh\n")
        (bin_dir / name).chmod(0o755)
    (tmp_path / "scratch").mkdir()
    return bin_dir


def config_for(tmp_path: Path, *checks: dict[str, Any]) -> dict[str, Any]:
    return {
        "root": str(tmp_path), "scratch": str(tmp_path / "scratch"), "excludes": [],
        "trusted_root": str(tmp_path), "confine": False, "package_checks": list(checks),
    }  # fmt: skip


def entry(tools: Path, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "pkg.lint", "kind": "command", "argv": ["zorp", "--strict"], "before": False,
        "closed_flag": None, "timeout_s": 60, "path": f"{tools}:/nonexistent",
        "python": str(tools / "python"), "context": "/ctx.json", "verify": [], "state": None,
        "outputs": {}, "state_limits": {},
    }  # fmt: skip
    return {**base, **over}


def run_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, check: dict[str, Any], supervisor: Supervisor
) -> CheckResult:
    monkeypatch.setattr(gate_tool, "_supervisor", lambda _config: supervisor)
    config = config_for(tmp_path, check)
    return gate_tool._run_package_check(config, check, tmp_path / "scratch")


def test_a_successful_call_is_recorded_with_its_clean_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tools: Path
) -> None:
    monkeypatch.setenv("HOST_SECRET", "s3cret")
    supervisor = Supervisor({"exit_code": 0, "stdout_b64": b64("fine\n"), "stderr_b64": b64("e")})
    done = run_check(monkeypatch, tmp_path, entry(tools), supervisor)

    assert (done.status, done.exit, done.cause) == ("passed", 0, None)
    assert done.output == "fine\ne"
    (spec,) = supervisor.specs
    assert spec["argv"] == [str(tools / "zorp"), "--strict"]  # resolved on the package's PATH only
    assert spec["cwd"] == str(tmp_path)
    expected = (None, None, False, 60.0)
    assert (spec["uid"], spec["gid"], spec["seccomp"], spec["timeout_s"]) == expected
    env = spec["env"]
    assert set(env) == {
        "PATH",
        "LANG",
        "HOME",
        "TMPDIR",
        "RAIDAR_STAGE_CONTEXT",
        "PYTHONDONTWRITEBYTECODE",
    }
    assert env["RAIDAR_STAGE_CONTEXT"] == "/ctx.json"
    assert not Path(spec["call_dir"]).exists()  # the call's work directory is gone afterwards
    assert done.call is not None
    assert done.call["env_names"] == sorted(env)
    assert done.call["env_sha256"] == _digest(json.dumps(env, sort_keys=True).encode())
    assert done.call["argv"] == spec["argv"]


def test_a_non_zero_exit_is_the_check_failing_on_the_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tools: Path
) -> None:
    supervisor = Supervisor({"exit_code": 3, "stdout_b64": b64("bad")})
    done = run_check(monkeypatch, tmp_path, entry(tools), supervisor)
    assert (done.status, done.exit, done.cause, done.output) == ("failed", 3, None, "bad")


@pytest.mark.parametrize("code", [None, 126, 127])
def test_a_call_that_could_not_exit_normally_is_an_error_crashed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tools: Path, code: int | None
) -> None:
    supervisor = Supervisor({"exit_code": code, "stdout_b64": b64("trace")})
    done = run_check(monkeypatch, tmp_path, entry(tools), supervisor)
    assert (done.status, done.exit, done.cause) == ("error", code, "crashed")


def test_a_malformed_supervisor_result_is_a_crash_with_no_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tools: Path
) -> None:
    done = run_check(monkeypatch, tmp_path, entry(tools), Supervisor({}))
    assert (done.status, done.exit, done.cause, done.output) == ("error", None, "crashed", "")


def test_a_call_past_its_timeout_is_an_error_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tools: Path
) -> None:
    supervisor = Supervisor({"timed_out": True, "exit_code": None})
    done = run_check(monkeypatch, tmp_path, entry(tools, timeout_s=7), supervisor)
    assert (done.status, done.exit, done.cause) == ("error", None, "timeout")
    assert done.output == "killed after 7 s"


@pytest.mark.parametrize(
    ("reply", "cause"),
    [
        ({"bounds_violation": "a loop", "exit_code": 0}, "bounds"),
        ({"isolation_error": "no seccomp", "exit_code": 0}, "isolation"),
        ({"tool_before": [{"ok": False, "root": "/t"}], "exit_code": 0}, "tool_changed"),
        ({"tool_after": [{"ok": True, "root": "/a"}, {"ok": False, "root": "/t"}]}, "tool_changed"),
        ({"bounds_violation": "x", "isolation_error": "y", "timed_out": True}, "bounds"),
        ({"isolation_error": "y", "tool_before": [{"ok": False, "root": "/t"}]}, "isolation"),
    ],
)
def test_an_untrusted_call_is_refused_whatever_it_exited_with(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tools: Path, reply: dict[str, Any], cause: str
) -> None:
    done = run_check(monkeypatch, tmp_path, entry(tools), Supervisor(reply))
    assert (done.status, done.exit, done.cause) == ("error", None, cause)
    assert done.call is not None


def test_a_tool_that_is_not_installed_is_unrunnable_and_never_called(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tools: Path
) -> None:
    supervisor = Supervisor({"exit_code": 0})
    done = run_check(monkeypatch, tmp_path, entry(tools, argv=["gone"]), supervisor)
    assert (done.status, done.exit, done.cause) == ("error", None, "unrunnable")
    assert done.output == "tool not installed: gone"
    assert supervisor.specs == []
    assert done.call is None


def test_a_closed_flag_fails_only_a_before_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tools: Path
) -> None:
    flag = tmp_path / "closed"
    flag.write_text("closed")
    supervisor = Supervisor({"exit_code": 0})

    flagged = entry(tools, before=True, closed_flag=str(flag))
    closed = run_check(monkeypatch, tmp_path, flagged, supervisor)
    ordinary = run_check(monkeypatch, tmp_path, entry(tools, closed_flag=str(flag)), supervisor)
    flag.unlink()
    open_ = run_check(monkeypatch, tmp_path, flagged, supervisor)

    assert (closed.status, closed.cause, closed.exit) == ("failed", "closed", None)
    assert len(supervisor.specs) == 2  # the closed check never reached the supervisor
    assert (ordinary.status, open_.status) == ("passed", "passed")


def test_only_the_tail_of_a_long_output_is_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tools: Path
) -> None:
    long = "x" * gate_tool.OUTPUT_CHARS + "end"
    done = run_check(
        monkeypatch, tmp_path, entry(tools), Supervisor({"exit_code": 1, "stdout_b64": b64(long)})
    )
    assert len(done.output) == gate_tool.OUTPUT_CHARS
    assert done.output.endswith("end")


def pytest_reply(outcomes: dict[str, Any] | str | None, code: int = 0) -> Callable[..., Any]:
    """The supervisor answer of a ``pytest-run`` call, which leaves its outcomes file behind."""

    def reply(spec: dict[str, Any]) -> dict[str, Any]:
        argv = spec["argv"]
        target = Path(argv[argv.index("--outcomes") + 1])
        if outcomes is not None:
            target.write_text(outcomes if isinstance(outcomes, str) else json.dumps(outcomes))
        return {"exit_code": code}

    return reply


@pytest.mark.parametrize(
    ("outcomes", "code", "status"),
    [
        ({"collected": ["a"], "outcomes": {"a": "passed"}}, 0, "passed"),
        ({"collected": [], "outcomes": {}}, 0, "failed"),
        ({"collected": ["a"], "outcomes": {"a": "failed"}}, 0, "failed"),
        (None, 0, "failed"),  # no outcomes file: nothing was collected
        ("not json", 0, "failed"),
        ("[1]", 0, "failed"),  # an outcomes file that is not an object
        ({"collected": ["a"], "outcomes": {"a": "passed"}}, 126, "error"),
    ],
)
def test_the_pytest_kind_judges_the_recorded_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tools: Path,
    outcomes: dict[str, Any] | str | None,
    code: int,
    status: str,
) -> None:
    check = entry(tools, kind="pytest", argv=["one.py"], timeout_s=30)
    done = run_check(monkeypatch, tmp_path, check, Supervisor(pytest_reply(outcomes, code)))
    assert done.status == status


def test_the_pytest_kind_runs_the_payload_on_the_package_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tools: Path
) -> None:
    site = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    supervisor = Supervisor(pytest_reply({"collected": ["a"], "outcomes": {"a": "passed"}}))
    check = entry(tools, kind="pytest", argv=["one.py", "-k", "x"], timeout_s=30)
    run_check(monkeypatch, tmp_path, check, supervisor)
    explicit = entry(tools, kind="pytest", argv=[], timeout_s=1, test_timeout=9)
    run_check(monkeypatch, tmp_path, explicit, supervisor)

    first, second = (spec["argv"] for spec in supervisor.specs)
    assert first[0] == str(tools / "python")
    assert first[2] == "pytest-run"
    assert first[first.index("--timeout") + 1] == "15"  # half the check's timeout by default
    assert first[first.index("--site") + 1] == str(site)
    assert first[first.index("--") :] == ["--", "-q", "-p", "no:cacheprovider", "one.py", "-k", "x"]
    assert second[second.index("--timeout") + 1] == "9"
    assert supervisor.specs[0]["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def test_a_tiny_pytest_timeout_still_allows_a_second_per_test(tmp_path: Path, tools: Path) -> None:
    config = config_for(tmp_path)
    check = entry(tools, kind="pytest", argv=[], timeout_s=1)
    command = gate_tool._package_command(config, check, tmp_path / "o.json")
    assert command[command.index("--timeout") + 1] == "1"
    assert gate_tool._package_command(config, entry(tools), tmp_path / "o.json") == [
        "zorp", "--strict",
    ]  # fmt: skip


# the whole decision ----------------------------------------------------------------------------


def names_of(decision: Decision) -> list[str]:
    return [check.name for check in decision.checks]


def test_before_checks_run_first_wherever_listed_and_a_refusal_stops_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tools: Path
) -> None:
    exits = {"pkg.late": 0, "pkg.first": 0}
    supervisor = Supervisor(lambda spec: {"exit_code": exits[spec["argv"][1]]})
    monkeypatch.setattr(gate_tool, "_supervisor", lambda _config: supervisor)
    ordinary = entry(tools, name="pkg.late", argv=["zorp", "pkg.late"])
    before = entry(tools, name="pkg.first", argv=["zorp", "pkg.first"], before=True)
    config = config_for(tmp_path, ordinary, before)

    passed = gate_tool.decide(config, tmp_path / "one")
    exits["pkg.first"] = 1
    refused = gate_tool.decide(config, tmp_path / "two")
    exits["pkg.first"] = 126
    erred = gate_tool.decide(config, tmp_path / "three")

    assert (passed.verdict, names_of(passed)) == ("passed", ["pkg.first", "pkg.late"])
    assert (refused.verdict, names_of(refused)) == ("refused", ["pkg.first"])
    assert (erred.verdict, names_of(erred)) == ("error", ["pkg.first"])
    assert erred.error is not None
    assert erred.error.startswith("pkg.first:")


def test_a_failing_prerequisite_prevents_every_package_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tools: Path
) -> None:
    supervisor = Supervisor({"exit_code": 0})
    monkeypatch.setattr(gate_tool, "_supervisor", lambda _config: supervisor)
    monkeypatch.setattr(subprocess, "run", completed(1, "no"))
    config = config_for(tmp_path, entry(tools))
    config["prerequisites"] = [{"name": "wf.req", "argv": ["x"]}]

    decision = gate_tool.decide(config, tmp_path / "scratch")

    assert (decision.verdict, names_of(decision)) == ("refused", ["wf.req"])
    assert supervisor.specs == []


def test_python_files_are_listed_sorted_without_the_excluded_directories(tmp_path: Path) -> None:
    for name in ("b.py", "a.py", "skip.txt", "sub/c.py", "node_modules/d.py"):
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("")
    assert gate_tool.python_files(tmp_path, ["node_modules"]) == ["a.py", "b.py", "sub/c.py"]
