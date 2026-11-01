"""The gate payload's decisions, driven in memory: witnessing, check outcomes and the verdict.

Shell outcomes come from an injected supervisor (success, non-zero, crash, timeout, refusal,
malformed result) and an injected ``subprocess.run``; no process starts and nothing sleeps.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import types
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
from raidar.subject.primitive.payload import walk as payload_walk


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


# git, the work tree and the environment, through an in-memory host ------------------------------

BLOCK = f"{gate_tool.RULES_BEGIN}\nrules\n{gate_tool.RULES_END}\n"


def _done(argv: list[str], code: int, out: bytes = b"", err: bytes = b"") -> Any:
    return subprocess.CompletedProcess(argv, code, out, err)


class Host:
    """``subprocess.run`` for git (a tiny in-memory repository) and for check commands."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.config: dict[str, str] = {}
        self.head: str | None = None
        self.log: list[str] = []
        self.ancestor = True
        self.write_tree: Callable[[dict[str, str]], str] = lambda env: "T"
        self.fail: dict[str, str] = {}
        self.hashed: dict[str, bytes] = {}
        self.index = ""
        self.calls: list[tuple[str, list[str], bytes | None]] = []
        self.ran: list[tuple[list[str], dict[str, Any]]] = []
        self.commands: Callable[[list[str], dict[str, Any]], tuple[int, str]] = lambda a, k: (
            0,
            "tool 1.0",
        )

    def __call__(self, argv: list[str], **kw: Any) -> Any:
        if argv[0] != "git" or argv == ["git", "--version"]:
            self.ran.append((argv, kw))
            code, out = self.commands(argv, kw)
            if kw.get("text"):
                return subprocess.CompletedProcess(argv, code, out, "")
            return _done(argv, code, out.encode())
        args = list(argv[1 + len(gate_tool.git_options()) :])
        if args[0] == "--git-dir":
            args = args[2:]
        name = args[0]
        self.calls.append((name, args, kw.get("input")))
        if name in self.fail:
            return _done(argv, 1, b"", self.fail[name].encode())
        out = getattr(self, "_" + name.replace("-", "_"))(args[1:], kw)
        if isinstance(out, int):
            return _done(argv, out)
        return _done(argv, 0, out.encode())

    def _store(self, content: bytes) -> str:
        sha = hashlib.sha1(content).hexdigest()  # noqa: S324 - a fake object id
        self.hashed[sha] = content
        return sha

    def _init(self, args: list[str], kw: dict[str, Any]) -> str:
        if "--bare" not in args:
            (self.root / ".git" / "objects").mkdir(parents=True, exist_ok=True)
        return ""

    def _config(self, args: list[str], kw: dict[str, Any]) -> str | int:
        if "--get" in args:
            value = self.config.get(args[-1])
            return 1 if value is None else value + "\n"
        self.config[args[-2]] = args[-1]
        return ""

    def _rev_parse(self, args: list[str], kw: dict[str, Any]) -> str | int:
        return 1 if self.head is None else self.head + "\n"

    def _merge_base(self, args: list[str], kw: dict[str, Any]) -> str | int:
        return "" if self.ancestor else 1

    def _log(self, args: list[str], kw: dict[str, Any]) -> str:
        return "\n".join(self.log) + "\n"

    def _write_tree(self, args: list[str], kw: dict[str, Any]) -> str:
        return self.write_tree(kw["env"]) + "\n"

    def _ls_files(self, args: list[str], kw: dict[str, Any]) -> str:
        excluded = [a.removeprefix("--exclude=") for a in args if a.startswith("--exclude=")]
        root, names = Path(kw["cwd"]), []
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d != ".git" and f"{d}/" not in excluded]
            for file in files:
                rel = (Path(current) / file).relative_to(root).as_posix()
                if f"/{rel}" not in excluded:
                    names.append(rel)
        return "".join(f"{name}\0" for name in sorted(names))

    def _hash_object(self, args: list[str], kw: dict[str, Any]) -> str:
        data: bytes = kw["input"]
        if "--stdin-paths" in args:
            names = [n for n in data.decode().split("\n") if n]
            return "".join(self._store((Path(kw["cwd"]) / n).read_bytes()) + "\n" for n in names)
        return self._store(data) + "\n"

    def _update_index(self, args: list[str], kw: dict[str, Any]) -> str:
        self.index = kw["input"].decode()
        return ""

    def _commit_tree(self, args: list[str], kw: dict[str, Any]) -> str:
        return "commit1\n"

    def _update_ref(self, args: list[str], kw: dict[str, Any]) -> str:
        self.head = args[1]
        return ""

    def git_names(self) -> list[str]:
        return [name for name, _, _ in self.calls]


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Host:
    fake = Host(tmp_path / "work")
    fake.root.mkdir()
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(gate_tool, "GATE_HOOKS", ["/dev/null"])
    monkeypatch.delenv("GIT_INDEX_FILE", raising=False)
    return fake


def gate_config(host: Host, tmp_path: Path, *, repo: bool = True, **over: Any) -> dict[str, Any]:
    if repo:
        (host.root / ".git" / "objects").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scratch").mkdir(exist_ok=True)
    (host.root / "a.py").write_text("x = 1\n", encoding="utf-8")
    return {
        "root": str(host.root), "scratch": str(tmp_path / "scratch"), "excludes": [".git"],
        "tools": str(tmp_path), "test_timeout": 5, **over,
    }  # fmt: skip


def lint(name: str = "lint", **over: Any) -> dict[str, Any]:
    return {"name": name, "category": "lint", "blocking": True, "argv": ["zorp"], **over}


def test_git_inherits_only_named_variables_and_a_commits_index_only_on_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_SECRET", "s3cret")
    monkeypatch.setenv("HOME", "/home/agent")
    monkeypatch.setenv("GIT_INDEX_FILE", "/commit/index")
    plain = gate_tool._git_env()
    kept = gate_tool._git_env(keep=gate_tool.GIT_KEPT, extra={"GIT_PAGER": "less"})
    assert "HOST_SECRET" not in plain
    assert "GIT_INDEX_FILE" not in plain
    assert (plain["HOME"], plain["GIT_CONFIG_GLOBAL"]) == ("/home/agent", "/dev/null")
    assert (kept["GIT_INDEX_FILE"], kept["GIT_PAGER"]) == ("/commit/index", "less")


def test_a_failed_git_call_is_a_gate_error_naming_gits_complaint(host: Host) -> None:
    host.fail["write-tree"] = "fatal: bad index"
    with pytest.raises(gate_tool.GateError, match="git write-tree: fatal: bad index"):
        gate_tool._git(host.root, "write-tree")
    assert gate_tool._repo_git_or_none(host.root, "write-tree") is None
    host.head = "abc"
    assert gate_tool._repo_git(host.root, "rev-parse", "HEAD") == "abc"


def test_a_manifest_names_each_entry_by_kind_and_content_and_its_digest_ignores_order(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.txt").write_text("one")
    script = tmp_path / "run.sh"
    script.write_text("#!")
    script.chmod(0o755)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b").write_text("two")
    (tmp_path / "skip").mkdir()
    (tmp_path / "skip" / "c").write_text("x")
    (tmp_path / "link").symlink_to("a.txt")
    (tmp_path / "dirlink").symlink_to("sub")

    manifest = gate_tool.worktree_manifest(tmp_path, ["skip"])

    assert sorted(manifest) == ["a.txt", "dirlink", "link", "run.sh", "sub/b"]
    assert manifest["a.txt"] == "file:" + _digest(b"one")
    assert manifest["run.sh"].startswith("exec:")
    assert manifest["link"] == "link:" + _digest(b"a.txt")
    assert manifest["dirlink"] == "link:" + _digest(b"sub")
    reordered = dict(reversed(list(manifest.items())))
    assert gate_tool.manifest_digest(reordered) == gate_tool.manifest_digest(manifest)
    (tmp_path / "a.txt").write_text("changed")
    changed = gate_tool.worktree_manifest(tmp_path, ["skip"])
    assert gate_tool.manifest_digest(changed) != gate_tool.manifest_digest(manifest)


def test_a_workspace_entry_that_is_neither_file_nor_link_stops_the_gate(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(gate_tool.GateError, match="not a regular file or link"):
        gate_tool.worktree_manifest(tmp_path, [])


def test_installed_entries_with_a_foreign_owner_or_loose_permissions_are_listed(
    tmp_path: Path,
) -> None:
    ok = tmp_path / "ok"
    ok.write_text("x")
    ok.chmod(0o644)
    loose = tmp_path / "loose"
    loose.write_text("x")
    loose.chmod(0o666)
    (tmp_path / "link").symlink_to("ok")  # a link's own mode is not judged
    uid = os.getuid()
    assert gate_tool._untrusted_entries(tmp_path, uid) == [str(loose)]
    for index in range(7):
        (tmp_path / f"f{index}").write_text("x")
    assert len(gate_tool._untrusted_entries(tmp_path, uid + 1)) == 5  # bounded


@pytest.mark.parametrize(
    ("category", "text", "expected"),
    [
        ("lint", '{"collected": []}', None),
        ("tests", None, None),
        ("tests", "not json", None),
        ("tests", "[1]", None),
        ("tests", '{"collected": ["t"]}', {"collected": ["t"]}),
    ],
)
def test_only_a_tests_check_with_a_readable_record_has_outcomes(
    tmp_path: Path, category: str, text: str | None, expected: dict[str, Any] | None
) -> None:
    target = tmp_path / "tests.json"
    if text is not None:
        target.write_text(text)
    assert gate_tool._read_outcomes(target, category) == expected


def raw(code: int, out: bytes = b"out") -> Callable[..., Any]:
    def run(argv: list[str], **_: Any) -> Any:
        return _done(argv, code, out)

    return run


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        (raw(0), ("passed", 0, "out", None)),
        (raw(3), ("failed", 3, "out", None)),
        (raw(127), ("error", 127, "out", "crashed")),
        (raw(-9), ("error", -9, "out", "crashed")),
    ],
)
def test_a_check_command_exit_decides_its_status(
    monkeypatch: pytest.MonkeyPatch, run: Callable[..., Any], expected: tuple[Any, ...]
) -> None:
    monkeypatch.setattr(subprocess, "run", run)
    assert gate_tool._spawn(["x"], ".", {}, 5) == expected


def test_a_check_that_times_out_or_cannot_start_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timed_out(*_: Any, **__: Any) -> Any:
        raise subprocess.TimeoutExpired("x", 5)

    def missing(*_: Any, **__: Any) -> Any:
        raise OSError("boom")

    monkeypatch.setattr(subprocess, "run", timed_out)
    assert gate_tool._spawn(["x"], ".", {}, 5) == ("error", None, "killed after 5 s", "timeout")
    monkeypatch.setattr(subprocess, "run", missing)
    assert gate_tool._spawn(["x"], ".", {}, 5) == (
        "error", None, "could not start: boom", "unrunnable",
    )  # fmt: skip


def test_a_command_check_without_python_files_passes_and_a_missing_tool_is_an_error(
    host: Host, tmp_path: Path, tools: Path
) -> None:
    config = gate_config(host, tmp_path)
    cache = tmp_path / "scratch" / "cache"
    idle = gate_tool._run_check(config, lint(), [], cache)
    assert (idle.status, idle.exit, idle.output) == ("passed", 0, "no Python files to check")
    missing = gate_tool._run_check(config, lint(argv=["absent"]), ["a.py"], cache)
    assert (missing.status, missing.cause) == ("error", "unrunnable")
    assert missing.output.startswith("tool not installed:")
    assert host.ran == []


def test_a_check_runs_in_the_work_tree_with_a_clean_environment_and_its_files(
    monkeypatch: pytest.MonkeyPatch, host: Host, tmp_path: Path, tools: Path
) -> None:
    monkeypatch.setenv("HOST_SECRET", "s3cret")
    config = gate_config(host, tmp_path)
    host.commands = lambda argv, kw: (1, "E501 " + "x" * gate_tool.OUTPUT_CHARS + "end")
    done = gate_tool._run_check(config, lint(), ["a.py", "b.py"], tmp_path / "scratch" / "cache")
    argv, kw = host.ran[0]
    assert argv[-2:] == ["a.py", "b.py"]
    assert kw["cwd"] == config["root"]
    assert "HOST_SECRET" not in kw["env"]
    assert kw["env"]["PATH"].startswith(f"{tmp_path}/bin:")
    assert (done.status, done.exit, done.blocking) == ("failed", 1, True)
    assert len(done.output) == gate_tool.OUTPUT_CHARS
    assert done.output.endswith("end")


def test_the_tests_check_reads_its_fresh_record_and_never_a_stale_one(
    host: Host, tmp_path: Path, tools: Path
) -> None:
    config = gate_config(host, tmp_path)
    check = {"name": "unit", "category": "tests", "blocking": True, "argv": []}
    cache = tmp_path / "scratch" / "cache"
    record = tmp_path / "scratch" / "tests.json"
    document = {"collected": ["t"], "outcomes": {"t": "passed"}}

    def write(argv: list[str], kw: dict[str, Any]) -> tuple[int, str]:
        Path(argv[argv.index("--outcomes") + 1]).write_text(json.dumps(document))
        return 0, "1 passed"

    host.commands = write
    fresh = gate_tool._run_check(config, check, [], cache)
    assert (fresh.status, fresh.tests) == ("passed", document)

    record.write_text('{"collected": ["stale"]}')
    host.commands = lambda argv, kw: (0, "")
    silent = gate_tool._run_check(config, check, [], cache)
    assert silent.tests is None


def test_a_blocking_check_refuses_but_a_failing_warning_does_not(
    host: Host, tmp_path: Path, tools: Path
) -> None:
    grumble = tools / "grumble"
    grumble.write_text("#!/bin/sh\n")
    grumble.chmod(0o755)
    (host.root / "vendor").mkdir()
    (host.root / "vendor" / "x.py").write_text("")
    config = gate_config(
        host, tmp_path, excludes=["vendor"],
        checks=[lint(), lint("style", blocking=False, argv=["grumble"])],
    )  # fmt: skip
    exits = {"zorp": 0}
    host.commands = lambda argv, kw: (1 if argv[0].endswith("grumble") else exits["zorp"], "E501")

    passed = gate_tool.decide(config, tmp_path / "scratch" / "one")
    exits["zorp"] = 1
    refused = gate_tool.decide(config, tmp_path / "scratch" / "two")

    assert (passed.verdict, [c.status for c in passed.checks]) == ("passed", ["passed", "failed"])
    assert refused.verdict == "refused"
    assert "E501" in refused.text()
    assert host.ran[0][0][1:] == ["a.py"]  # the excluded directory's files are not checked


# the hook ---------------------------------------------------------------------------------------


def hook_config(host: Host, tmp_path: Path, **over: Any) -> dict[str, Any]:
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    return gate_config(host, tmp_path, **{"hook_state": str(state), **over})


def last_record(tmp_path: Path) -> dict[str, Any]:
    lines = (tmp_path / "state" / gate_tool.HOOK_RUNS).read_text().splitlines()
    record: dict[str, Any] = json.loads(lines[-1])
    return record


def test_a_commit_of_exactly_the_checked_work_tree_is_recorded_as_gated(
    monkeypatch: pytest.MonkeyPatch, host: Host, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    config = hook_config(host, tmp_path)
    assert gate_tool.hook(config) == 0
    record = last_record(tmp_path)
    assert capsys.readouterr().out == "verify-gate: passed\n"
    assert (record["who"], record["verdict"], record["exit"]) == ("hook", "passed", 0)
    assert record["tree"] == record["index_tree"] == record["worktree_tree"] == "T"
    assert list((tmp_path / "scratch").glob("seal-*")) == []  # no private git directory left


def test_a_commit_of_a_different_index_is_not_recorded_as_the_gated_tree(
    monkeypatch: pytest.MonkeyPatch, host: Host, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_INDEX_FILE", "/commit/index")
    host.write_tree = lambda env: (
        "commit-tree" if env.get("GIT_INDEX_FILE") == "/commit/index" else "work-tree"
    )
    assert gate_tool.hook(hook_config(host, tmp_path)) == 0
    record = last_record(tmp_path)
    assert record["tree"] is None
    assert (record["index_tree"], record["worktree_tree"]) == ("commit-tree", "work-tree")


def test_a_refused_check_blocks_the_commit_and_prints_why(
    monkeypatch: pytest.MonkeyPatch, host: Host, tmp_path: Path, tools: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    monkeypatch.chdir(tmp_path)
    host.commands = lambda argv, kw: (1, "E501 too long")
    assert gate_tool.hook(hook_config(host, tmp_path, checks=[lint()])) == 1
    out = capsys.readouterr().out
    assert "verify-gate: refused" in out
    assert "- lint (lint, blocking): failed, exit 1" in out
    assert "E501 too long" in out


def test_a_hook_that_cannot_log_its_run_is_an_error(
    monkeypatch: pytest.MonkeyPatch, host: Host, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    config = hook_config(host, tmp_path, hook_state=str(tmp_path / "no-such-dir"))
    assert gate_tool.hook(config) == 126
    assert "the hook log could not be written" in capsys.readouterr().out


def test_a_git_failure_makes_the_hook_an_error_and_leaves_no_private_directory(
    monkeypatch: pytest.MonkeyPatch, host: Host, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    host.fail["ls-files"] = "fatal: not a repo"
    assert gate_tool.hook(hook_config(host, tmp_path)) == 126
    record = last_record(tmp_path)
    assert record["verdict"] == "error"
    assert record["error"] == "GateError: git ls-files: fatal: not a repo"
    assert record["tree"] is None
    assert list((tmp_path / "scratch").glob("seal-*")) == []


def test_a_gate_run_that_rewrites_session_or_watched_state_is_recorded_in_the_hook(
    monkeypatch: pytest.MonkeyPatch, host: Host, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    session_dir, watch = tmp_path / "sessions", tmp_path / "watch"
    session_dir.mkdir()
    watch.mkdir()
    session = session_dir / "s.jsonl"
    session.write_bytes(b"one\n")
    (watch / "keep.json").write_text("kept")

    def probe(argv: list[str], kw: dict[str, Any]) -> tuple[int, str]:
        session.write_bytes(b"xyz\n")
        (watch / "state.json").write_text("new")
        (watch / "ok.json").write_text("its own write")
        return 0, ""

    host.commands = probe
    config = hook_config(
        host, tmp_path, session_paths=[str(session_dir)],
        prerequisites=[
            {"name": "define.state", "argv": ["probe"], "watch": str(watch), "writes": ["ok.json"]}
        ],
    )  # fmt: skip
    assert gate_tool.hook(config) == 0
    found = last_record(tmp_path)["integrity"]
    assert [f["name"] for f in found] == ["session_log_rewritten", "define_state_rewritten"]
    assert {f["after"] for f in found} == {"the hook run"}


# the seal ---------------------------------------------------------------------------------------


def seal_args() -> argparse.Namespace:
    return argparse.Namespace(run=3, after="turn-2", base="B0")


def test_a_seal_records_head_history_and_what_the_checks_changed(
    host: Host, tmp_path: Path, tools: Path
) -> None:
    config = gate_config(host, tmp_path, checks=[lint()])
    host.head = "H1"
    host.config["core.hooksPath"] = "/gate/hooks"
    host.log = ["c1 t1 first commit", "c2 t2"]

    def mutate(argv: list[str], kw: dict[str, Any]) -> tuple[int, str]:
        (host.root / "a.py").write_text("x = 2\n")
        (host.root / "new.py").write_text("")
        return 0, ""

    host.commands = mutate
    record = gate_tool.seal(config, seal_args())

    assert (record["who"], record["run"], record["after"]) == ("seal", 3, "turn-2")
    assert (record["verdict"], record["exit"], record["error"]) == ("passed", 0, None)
    assert (record["head"], record["hooks_path"]) == ("H1", "/gate/hooks")
    assert record["commits"] == [
        {"commit": "c1", "tree": "t1", "subject": "first commit"},
        {"commit": "c2", "tree": "t2", "subject": ""},
    ]
    assert record["base_is_ancestor"] is True
    assert (record["tree"], record["final_tree"]) == ("T", "T")
    assert record["changed_during_gate"] == ["a.py", "new.py"]
    assert sorted(record["files"]) == ["a.py", "new.py"]
    assert record["worktree_sha256"] != record["final_worktree_sha256"]
    assert [c["name"] for c in record["checks"]] == ["lint"]
    assert record["output"].startswith("verify-gate: passed")


def test_a_quiet_seal_reports_no_change_and_rewritten_history_is_unreadable(
    host: Host, tmp_path: Path
) -> None:
    config = gate_config(host, tmp_path)
    host.head = "H1"
    quiet = gate_tool.seal(config, seal_args())
    assert quiet["changed_during_gate"] == []
    assert quiet["worktree_sha256"] == quiet["final_worktree_sha256"]
    assert quiet["hooks_path"] is None

    host.ancestor = False
    rewritten = gate_tool.seal(config, seal_args())
    assert (rewritten["commits"], rewritten["base_is_ancestor"]) == ([], False)


def test_a_seal_that_cannot_read_the_repository_records_an_error(
    host: Host, tmp_path: Path
) -> None:
    config = gate_config(host, tmp_path)
    host.fail["rev-parse"] = "fatal: no head"
    record = gate_tool.seal(config, seal_args())
    assert (record["verdict"], record["exit"], record["tree"]) == ("error", 126, None)
    assert record["error"].startswith("GateError:")
    assert "fatal: no head" in record["error"]
    assert "the gate could not run" in record["output"]
    assert record["checks"] == []


# the raw tree and the workspace setup ------------------------------------------------------------


def test_the_raw_tree_records_each_entry_with_its_mode_and_the_bytes_on_disk(
    host: Host, tmp_path: Path
) -> None:
    config = gate_config(host, tmp_path, excludes=[".git", "node_modules"])
    run = host.root / "run.sh"
    run.write_text("#!/bin/sh\n")
    run.chmod(0o755)
    (host.root / "ln").symlink_to("a.py")
    (host.root / "odd\nname").write_text("odd")
    (host.root / '"quoted"').write_text("q")
    (host.root / "node_modules").mkdir()
    (host.root / "node_modules" / "x.js").write_text("x")

    assert gate_tool._tree_of(config, tmp_path / "scratch") == "T"

    rows = [row.split("\t", 1) for row in host.index.split("\0")[:-1]]
    entries = {name: head.split(" ") for head, name in rows}
    assert {name: mode for name, (mode, _) in entries.items()} == {
        "a.py": "100644", "ln": "120000", "odd\nname": "100644", '"quoted"': "100644",
        "run.sh": "100755",
    }  # fmt: skip
    content = {name: host.hashed[sha] for name, (_, sha) in entries.items()}
    assert content["ln"] == b"a.py"  # a link is its target, not the file it points at
    assert (content["odd\nname"], content['"quoted"'], content["a.py"]) == (
        b"odd",
        b"q",
        b"x = 1\n",
    )
    batch = [i for n, a, i in host.calls if n == "hash-object" and "--stdin-paths" in a]
    assert batch == [b"a.py\nrun.sh\n"]  # only plain files go through one batch


def test_an_empty_work_tree_is_the_empty_tree_without_writing_one(
    host: Host, tmp_path: Path
) -> None:
    config = gate_config(host, tmp_path)
    (host.root / "a.py").unlink()
    assert gate_tool._tree_of(config, tmp_path / "scratch") == gate_tool.EMPTY_TREE
    assert "write-tree" not in host.git_names()


def test_the_private_git_directory_reads_the_agents_objects_and_nothing_else(
    host: Host, tmp_path: Path
) -> None:
    config = gate_config(host, tmp_path)
    scratch = tmp_path / "scratch"
    git_dir, env = gate_tool._private_git(config, scratch)
    alternates = git_dir / "objects" / "info" / "alternates"
    assert alternates.read_text() == f"{host.root / '.git' / 'objects'}\n"
    assert (env["GIT_DIR"], env["GIT_INDEX_FILE"]) == (str(git_dir), str(git_dir / "index"))


@pytest.mark.parametrize("layout", ["missing", "linked"])
def test_a_workspace_without_a_real_git_directory_has_no_private_view(
    host: Host, tmp_path: Path, layout: str
) -> None:
    config = gate_config(host, tmp_path, repo=False)
    if layout == "linked":
        target = tmp_path / "elsewhere"
        (target / "objects").mkdir(parents=True)
        (host.root / ".git").symlink_to(target)
    with pytest.raises(gate_tool.GateError, match="not a repository directory"):
        gate_tool._private_git(config, tmp_path / "scratch")


def test_setup_creates_the_repository_identity_exclusions_and_a_base_commit(
    host: Host, tmp_path: Path
) -> None:
    config = gate_config(host, tmp_path, repo=False, hooks="/gate/hooks", excludes=["node_modules"])
    assert gate_tool.workspace_setup(config) == {"base": "commit1", "repo_created": True}
    assert host.config["core.hooksPath"] == "/gate/hooks"
    assert host.config["user.name"] == "raidar-agent"
    assert (host.root / ".git" / "info" / "exclude").read_text() == "node_modules/\n"
    assert host.head == "commit1"


def test_setup_keeps_an_existing_repository_history_and_identity(
    host: Host, tmp_path: Path
) -> None:
    config = gate_config(host, tmp_path, hooks="/gate/hooks")
    host.head = "old"
    host.config["user.name"] = "alice"
    assert gate_tool.workspace_setup(config) == {"base": "old", "repo_created": False}
    assert host.config["user.name"] == "alice"
    assert host.config["user.email"] == "agent@raidar.invalid"
    assert "commit-tree" not in host.git_names()


def test_setup_refuses_a_git_entry_that_is_not_a_directory(host: Host, tmp_path: Path) -> None:
    config = gate_config(host, tmp_path, repo=False, hooks="/h")
    (host.root / ".git").write_text("gitdir: elsewhere")
    with pytest.raises(gate_tool.GateError, match="not a repository directory"):
        gate_tool.workspace_setup(config)


def rules_config(host: Host, tmp_path: Path, *, owned: bool) -> dict[str, Any]:
    record = tmp_path / "rules-block.md"
    record.write_text(BLOCK, encoding="utf-8")
    return gate_config(
        host, tmp_path, repo=False, hooks="/gate/hooks", excludes=["node_modules"],
        rules_file="RULES.md", rules_record=str(record), rules_owned=owned,
        strip_command="verify-gate strip-rules",
    )  # fmt: skip


def test_a_task_owned_rules_file_gets_the_block_once_and_is_based_without_it(
    host: Host, tmp_path: Path
) -> None:
    config = rules_config(host, tmp_path, owned=False)
    rules = host.root / "RULES.md"
    rules.write_text("task text\n")
    gate_tool.workspace_setup(config)
    gate_tool.workspace_setup(config)  # setting up again does not stack blocks
    assert rules.read_text() == "task text\n\n" + BLOCK
    git = host.root / ".git" / "info"
    assert (git / "attributes").read_text() == "/RULES.md filter=raidar-rules\n"
    assert host.config["filter.raidar-rules.clean"] == "verify-gate strip-rules"
    assert (git / "exclude").read_text() == "node_modules/\n"
    assert b"task text\n" in host.hashed.values()
    assert all(gate_tool.RULES_BEGIN.encode() not in data for data in host.hashed.values())


def test_a_rules_file_the_stage_owns_is_created_excluded_and_never_filtered(
    host: Host, tmp_path: Path
) -> None:
    config = rules_config(host, tmp_path, owned=True)
    gate_tool.workspace_setup(config)
    assert (host.root / "RULES.md").read_text() == "\n" + BLOCK
    git = host.root / ".git" / "info"
    assert (git / "exclude").read_text() == "node_modules/\n/RULES.md\n"
    assert not (git / "attributes").exists()
    assert "filter.raidar-rules.clean" not in host.config
    assert "RULES.md" not in host.index


def test_a_rules_file_that_is_a_link_is_refused(host: Host, tmp_path: Path) -> None:
    config = rules_config(host, tmp_path, owned=False)
    (host.root / "RULES.md").symlink_to("a.py")
    with pytest.raises(gate_tool.GateError, match="RULES.md is a symlink"):
        gate_tool.workspace_setup(config)


# setup facts ------------------------------------------------------------------------------------


@pytest.fixture
def installed(
    monkeypatch: pytest.MonkeyPatch, host: Host, tmp_path: Path, tools: Path
) -> types.SimpleNamespace:
    home = tmp_path / "home"
    hooks = home / "hooks"
    hooks.mkdir(parents=True)
    payload, script = home / "verify_gate.py", hooks / "pre-commit"
    payload.write_bytes(b"payload")
    script.write_bytes(b"#!/bin/sh\n")
    for directory in (home, hooks):
        directory.chmod(0o755)
    payload.chmod(0o644)
    script.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    log = state / gate_tool.HOOK_RUNS
    log.write_text("")
    (host.root / "RULES.md").write_text("own\n\n" + BLOCK)
    config = gate_config(
        host, tmp_path, home=str(home), trusted_uid=os.getuid(), hook_state=str(state),
        payload_sha256=_digest(b"payload"), hook_sha256=_digest(b"#!/bin/sh\n"),
        rules_file="RULES.md",
        checks=[lint(), {"name": "unit", "category": "tests", "blocking": True, "argv": []}],
        package_checks=[entry(tools, name="pkg.lint")],
    )  # fmt: skip
    host.config["core.hooksPath"] = str(hooks)
    return types.SimpleNamespace(
        config=config, host=host, tools=tools, payload=payload, hook=script, log=log,
        rules=host.root / "RULES.md", monkeypatch=monkeypatch,
    )  # fmt: skip


def test_a_sound_installation_passes_every_setup_fact(installed: types.SimpleNamespace) -> None:
    done = gate_tool.setup_check(installed.config)
    assert done["status"] == "passed"
    assert [c["name"] for c in done["checks"]] == [
        "agent_user_not_root", "interpreter", "tool:lint", "tool:unit", "tool:pkg.lint",
        "tool_ownership", "gate_payload", "hook_installed", "rules_block", "state_writable",
        "hook_log", "git",
    ]  # fmt: skip
    details = {c["name"]: c["detail"] for c in done["checks"]}
    assert details["rules_block"] == "RULES.md holds the rules block"
    assert details["tool:lint"] == "tool 1.0 " + _digest(b"#!/bin/sh\n")
    assert details["tool:unit"] == "pytest tool 1.0"
    assert done["process"]["uid"] == os.getuid()


def _chmod(path_name: str, mode: int) -> Callable[[types.SimpleNamespace], None]:
    return lambda w: getattr(w, path_name).chmod(mode)


def _git_broken(w: types.SimpleNamespace) -> None:
    w.host.commands = lambda argv, kw: (1, "boom") if argv[0] == "git" else (0, "tool 1.0")


SETUP_FAILURES = {
    "root": (
        lambda w: w.monkeypatch.setattr(os, "getuid", lambda: 0),
        {"agent_user_not_root"},
        "uid 0",
    ),
    "no interpreter": (
        lambda w: (w.tools / "python").unlink(),
        {"interpreter"},
        "no tools interpreter",
    ),
    "missing tool": (
        lambda w: (w.tools / "zorp").unlink(),
        {"tool:lint", "tool:pkg.lint"},
        "tool not installed",
    ),
    "version fails": (_git_broken, {"git"}, "exit 1: boom"),
    "writable payload": (_chmod("payload", 0o666), {"tool_ownership"}, "verify_gate.py"),
    "foreign owner": (
        lambda w: w.config.update(trusted_uid=w.config["trusted_uid"] + 1),
        {"tool_ownership"},
        "not owned by uid",
    ),
    "tampered payload": (
        lambda w: w.payload.write_bytes(b"tampered"),
        {"gate_payload"},
        "the installed gate is",
    ),
    "hook not executable": (_chmod("hook", 0o644), {"hook_installed"}, "not executable"),
    "hooks path moved": (
        lambda w: w.host.config.update({"core.hooksPath": "/other"}),
        {"hook_installed"},
        "core.hooksPath is '/other'",
    ),
    "rules block gone": (
        lambda w: w.rules.write_text("no block"),
        {"rules_block"},
        "holds no rules block",
    ),
    "hook log missing": (lambda w: w.log.unlink(), {"hook_log"}, "missing or not writable"),
}


@pytest.mark.parametrize("case", sorted(SETUP_FAILURES))
def test_each_broken_setup_fact_fails_only_its_own_step(
    installed: types.SimpleNamespace, case: str
) -> None:
    mutate, failed, detail = SETUP_FAILURES[case]
    mutate(installed)
    done = gate_tool.setup_check(installed.config)
    assert done["status"] == "failed"
    broken = {c["name"]: c["detail"] for c in done["checks"] if c["status"] == "failed"}
    assert set(broken) == failed
    assert all(detail in text for text in broken.values())


def test_a_gate_without_a_rules_file_needs_no_rules_block(installed: types.SimpleNamespace) -> None:
    installed.config.pop("rules_file")

    assert gate_tool.setup_check(installed.config)["status"] == "passed"


# rules stripping --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("own\n", "own\n"),
        ("own\n\n" + BLOCK, "own\n"),
        (BLOCK, ""),
        ("\n" + BLOCK, ""),
        ("a\n\n" + BLOCK + "tail\n", "a\ntail\n"),
        (gate_tool.RULES_BEGIN + " unterminated", gate_tool.RULES_BEGIN + " unterminated"),
        (gate_tool.RULES_END + "\nonly the end", gate_tool.RULES_END + "\nonly the end"),
    ],
)
def test_stripping_gives_back_the_files_own_bytes(text: str, expected: str) -> None:
    assert gate_tool.strip_rules(text) == expected


def test_stripping_a_file_removes_the_block_and_an_emptied_file_only_on_request(
    tmp_path: Path,
) -> None:
    absent = tmp_path / "absent"
    assert gate_tool.strip_file(absent, True) == {"path": str(absent), "present": False}
    target = tmp_path / "target"
    target.write_text("own\n\n" + BLOCK)
    link = tmp_path / "link"
    link.symlink_to(target)
    assert gate_tool.strip_file(link, True)["present"] is False
    assert target.read_text() == "own\n\n" + BLOCK  # a link is never followed

    kept = gate_tool.strip_file(target, True)
    assert (kept["present"], kept["removed"], target.read_text()) == (True, False, "own\n")

    target.write_text(BLOCK)
    assert gate_tool.strip_file(target, False)["removed"] is False
    assert target.read_text() == ""
    target.write_text(BLOCK)
    gone = gate_tool.strip_file(target, True)
    assert (gone["present"], gone["removed"], target.exists()) == (False, True, False)


# snapshots --------------------------------------------------------------------------------------


def test_a_snapshot_records_digests_for_regular_files_only_and_absence(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("abc")
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "b").write_text("b")
    (tmp_path / "ln").symlink_to("a")
    taken = gate_tool.snapshot(tmp_path)
    assert taken["present"] is True
    assert sorted(taken["files"]) == ["a", "d/b", "ln"]
    assert taken["files"]["a"]["sha256"] == _digest(b"abc")
    assert taken["files"]["a"]["size"] == 3
    assert "sha256" not in taken["files"]["ln"]
    gone = gate_tool.snapshot(tmp_path / "missing")
    assert (gone["present"], gone["files"]) == (False, {})
    assert gate_tool._prefix_sha256(tmp_path / "missing", 3) is None


# the test-outcome plugin and pytest-run ----------------------------------------------------------


def _report(nodeid: str, when: str, outcome: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        nodeid=nodeid, when=when, outcome=outcome, skipped=outcome == "skipped"
    )


def test_a_test_is_recorded_as_the_worst_of_its_phases(tmp_path: Path) -> None:
    target = tmp_path / "o.json"
    plugin = gate_tool.TestOutcomes(target)
    plugin.pytest_collection_modifyitems([types.SimpleNamespace(nodeid=n) for n in "abcde"])
    for report in (
        _report("a", "setup", "passed"), _report("a", "call", "passed"),
        _report("a", "teardown", "passed"),
        _report("b", "call", "failed"), _report("b", "teardown", "passed"),
        _report("c", "setup", "skipped"),
        _report("d", "call", "passed"), _report("d", "teardown", "failed"),
        _report("e", "call", "failed"), _report("e", "teardown", "failed"),
    ):  # fmt: skip
        plugin.pytest_runtest_logreport(report)
    plugin.pytest_sessionfinish()
    assert json.loads(target.read_text()) == {
        "collected": ["a", "b", "c", "d", "e"],
        "outcomes": {"a": "passed", "b": "failed", "c": "skipped", "d": "failed", "e": "failed"},
        "uid": os.getuid(),
    }
    gate_tool.TestOutcomes(None).pytest_sessionfinish()  # no target, nothing written


def test_pytest_run_refuses_to_run_the_agents_tests_as_root(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(os, "getuid", lambda: 0)
    assert gate_tool.pytest_run(5, [], [], agent_user=True) == 126
    assert "not root" in capsys.readouterr().err


def test_pytest_run_adds_sites_returns_pytests_status_and_ends_a_slow_test(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[Any] = []

    def main(args: list[str], plugins: list[Any]) -> int:
        seen.extend(plugins)
        plugins[1].pytest_sessionfinish()
        return 5

    monkeypatch.setattr(pytest, "main", main)
    monkeypatch.setattr(sys, "path", list(sys.path))
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    target = tmp_path / "o.json"

    assert gate_tool.pytest_run(2, [str(site_dir)], ["-q"], target) == 5

    assert sys.path[0] == os.getcwd()
    assert str(site_dir) in sys.path
    assert json.loads(target.read_text())["uid"] == os.getuid()
    stopper = next(p for p in seen if hasattr(p, "pytest_runtest_call"))
    previous = signal.getsignal(signal.SIGALRM)
    running = stopper.pytest_runtest_call()
    next(running)
    handler = signal.getsignal(signal.SIGALRM)
    with pytest.raises(pytest.fail.Exception, match="the test ran longer than 2 s"):
        handler(signal.SIGALRM, None)  # type: ignore[operator]
    with pytest.raises(StopIteration):
        running.send(None)
    assert signal.getsignal(signal.SIGALRM) == previous
    assert signal.alarm(0) == 0


# reaping ----------------------------------------------------------------------------------------


def fake_proc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    proc = tmp_path / "proc"
    proc.mkdir()
    real = gate_tool.Path

    def path(value: Any) -> Path:
        text = str(value)
        if text == "/proc":
            return proc
        return proc / text[len("/proc/") :] if text.startswith("/proc/") else real(text)

    monkeypatch.setattr(gate_tool, "Path", path)
    return proc


def test_reaping_kills_the_users_other_processes_until_none_remain_or_five_rounds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proc = fake_proc(monkeypatch, tmp_path)
    for pid in (70001, 70002):
        (proc / str(pid)).mkdir()
    sent: list[tuple[int, int]] = []

    def kill(pid: int, sig: int) -> None:
        sent.append((pid, sig))
        if pid == 70002:
            raise ProcessLookupError
        (proc / str(pid)).rmdir()

    monkeypatch.setattr(os, "kill", kill)
    monkeypatch.setattr(gate_tool.time, "sleep", lambda seconds: None)
    record = gate_tool.reap()
    assert record["killed"] == [70001]
    assert record["remaining"] == [70002]
    assert {sig for _, sig in sent} == {signal.SIGKILL}
    assert sent.count((70002, signal.SIGKILL)) == 5

    (proc / "70002").rmdir()
    clean = gate_tool.reap()
    assert (clean["killed"], clean["remaining"]) == ([], [])


def test_reaping_is_refused_for_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "getuid", lambda: 0)
    record = gate_tool.reap()
    assert record["error"].startswith("refused")
    assert record["killed"] == []


def test_the_ancestors_are_this_process_and_its_parents_up_to_init(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proc = fake_proc(monkeypatch, tmp_path)
    me = os.getpid()
    for pid, parent in ((me, 500), (500, 400), (400, 1)):
        (proc / str(pid)).mkdir()
        (proc / str(pid) / "stat").write_text(f"{pid} (my (odd) cmd) S {parent} 0 0")
    assert gate_tool._ancestors() == {me, 500, 400}
    (proc / "400" / "stat").write_text("garbage")
    assert gate_tool._ancestors() == {me, 500, 400}
    (proc / "400" / "stat").write_text(f"400 (x) S {me} 0")  # a cycle ends, never loops
    assert gate_tool._ancestors() == {me, 500, 400}


# baseline, canary and outputs -------------------------------------------------------------------


def test_the_baseline_runs_the_pytest_check_and_records_the_base_it_ran_over(
    monkeypatch: pytest.MonkeyPatch, host: Host, tmp_path: Path, tools: Path
) -> None:
    document = {"collected": ["t"], "outcomes": {"t": "failed"}}
    supervisor = Supervisor(pytest_reply(document))
    monkeypatch.setattr(gate_tool, "_supervisor", lambda _config: supervisor)
    check = entry(tools, kind="pytest", argv=["one.py"], timeout_s=30)
    config = gate_config(host, tmp_path, trusted_root=str(tmp_path), confine=False)
    config["package_checks"] = [check]

    record = gate_tool.baseline(config)

    assert (record["status"], record["tests"]) == ("failed", document)
    assert record["tree"] == "T"
    manifest = gate_tool.worktree_manifest(host.root, config["excludes"])
    assert record["worktree_sha256"] == gate_tool.manifest_digest(manifest)
    assert record["process"]["uid"] == os.getuid()


def test_a_baseline_without_a_pytest_check_is_an_error(host: Host, tmp_path: Path) -> None:
    config = gate_config(host, tmp_path, package_checks=[{"kind": "command"}])
    assert gate_tool.baseline(config) == {
        "status": "error", "exit": None, "tests": None, "error": "no pytest check to run",
    }  # fmt: skip


def test_a_declared_output_is_captured_and_a_refused_one_is_a_recorded_violation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tools: Path
) -> None:
    (tmp_path / "report").write_text("retained report\n")
    (tmp_path / "gone").symlink_to(tmp_path / "report")
    payload: Any = types.ModuleType("payload")
    payload.walk = payload_walk
    monkeypatch.setitem(sys.modules, "payload", payload)
    declared = {"report": str(tmp_path / "report"), "lost": str(tmp_path / "gone")}
    check = entry(tools, outputs=declared, state_limits={"max_files": 5})

    done = run_check(monkeypatch, tmp_path, check, Supervisor({"exit_code": 0}))

    assert done.outputs is not None
    report = done.outputs["report"]
    assert report["violation"] is None
    assert report["files"] == 1
    assert report["bytes"] == len(b"retained report\n")
    assert report["entries"][0]["sha256"] == _digest(b"retained report\n")
    assert done.outputs["lost"]["violation"] == "symlink: gone"


def test_an_output_archive_failure_does_not_misattribute_the_authored_checks_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tools: Path
) -> None:
    def no_archive(*args: Any) -> None:
        raise OSError("archive full")

    payload: Any = types.ModuleType("payload")
    payload.walk = types.SimpleNamespace(Limits=payload_walk.Limits, snapshot=no_archive)
    monkeypatch.setitem(sys.modules, "payload", payload)
    check = entry(tools, outputs={"report": "/out/report"})

    done = run_check(monkeypatch, tmp_path, check, Supervisor({"exit_code": 0}))

    assert done.status == "passed"
    assert done.outputs == {
        "report": {"path": "/out/report", "violation": "snapshot failed: archive full"}
    }


# the command line -------------------------------------------------------------------------------


class Libc:
    """The C library's ``prctl`` (the dumpable flag) and the umask the gate sets."""

    def __init__(self) -> None:
        self.result = 0
        self.calls: list[tuple[int, ...]] = []
        self.umasks: list[int] = []

    def prctl(self, *args: int) -> int:
        self.calls.append(args)
        return self.result


@pytest.fixture
def libc(monkeypatch: pytest.MonkeyPatch) -> Libc:
    fake = Libc()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(gate_tool.ctypes, "CDLL", lambda *a, **k: fake)
    monkeypatch.setattr(os, "umask", lambda mask: fake.umasks.append(mask) or 0)
    return fake


def test_the_record_channel_is_protected_on_linux_only_and_a_refusal_is_an_error(
    monkeypatch: pytest.MonkeyPatch, libc: Libc
) -> None:
    gate_tool._protect_record_channel()
    assert libc.calls == [(4, 0, 0, 0, 0)]  # the process becomes not dumpable
    libc.result = -1
    with pytest.raises(OSError, match="cannot protect the gate record channel"):
        gate_tool._protect_record_channel()
    monkeypatch.setattr(sys, "platform", "darwin")
    libc.calls.clear()
    gate_tool._protect_record_channel()
    assert libc.calls == []


def test_strip_rules_filters_standard_input_and_reports_a_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, libc: Libc, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("own\n\n" + BLOCK))
    assert gate_tool.main(["strip-rules"]) == 0
    assert capsys.readouterr().out == "own\n"
    assert libc.umasks == [0o022]  # nothing the gate writes is world-writable

    target = tmp_path / "rules.md"
    target.write_text(BLOCK)
    assert gate_tool.main(["strip-rules", "--file", str(target), "--remove-empty"]) == 0
    assert json.loads(capsys.readouterr().out)["removed"] is True
    assert not target.exists()


def test_snapshot_and_reap_are_standalone_subcommands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, libc: Libc, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "f").write_text("x")
    assert gate_tool.main(["snapshot", "--dir", str(tmp_path)]) == 0
    assert list(json.loads(capsys.readouterr().out)["files"]) == ["f"]
    monkeypatch.setattr(os, "getuid", lambda: 0)
    assert gate_tool.main(["reap"]) == 0
    assert json.loads(capsys.readouterr().out)["error"].startswith("refused")
    assert gate_tool.main(["pytest-run", "--timeout", "5", "--agent-user"]) == 126


def test_a_missing_subcommand_is_a_usage_error(libc: Libc) -> None:
    with pytest.raises(SystemExit) as stopped:
        gate_tool.main([])
    assert stopped.value.code == 2


@pytest.mark.parametrize(
    ("argv", "key", "expected"),
    [
        (["canary"], "verdict", "passed"),
        (["seal", "--run", "2", "--after", "turn", "--base", "B0"], "who", "seal"),
        (["baseline"], "error", "no pytest check to run"),
    ],
)
def test_configured_gate_subcommands_report_their_decision(
    host: Host, tmp_path: Path, libc: Libc, capsys: pytest.CaptureFixture[str],
    argv: list[str], key: str, expected: str,
) -> None:  # fmt: skip
    host.head = "H"
    config = gate_config(host, tmp_path, empty_hooks="/empty/hooks")
    cfg = tmp_path / "gate.json"
    cfg.write_text(json.dumps(config))
    assert gate_tool.main([argv[0], "--config", str(cfg), *argv[1:]]) == 0
    assert json.loads(capsys.readouterr().out)[key] == expected


def test_the_hook_and_workspace_setup_run_from_the_command_line(
    monkeypatch: pytest.MonkeyPatch, host: Host, tmp_path: Path, libc: Libc,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    monkeypatch.chdir(tmp_path)
    config = hook_config(host, tmp_path)
    cfg = tmp_path / "gate.json"
    cfg.write_text(json.dumps(config))
    assert gate_tool.main(["hook", "--config", str(cfg)]) == 0
    assert last_record(tmp_path)["verdict"] == "passed"
    capsys.readouterr()

    fresh = tmp_path / "fresh"
    host.root = fresh
    fresh.mkdir()
    setup = gate_config(host, tmp_path, repo=False, hooks="/gate/hooks")
    cfg.write_text(json.dumps(setup))
    assert gate_tool.main(["workspace-setup", "--config", str(cfg)]) == 0
    assert json.loads(capsys.readouterr().out) == {"base": "commit1", "repo_created": True}


def test_setup_check_runs_from_the_command_line(
    installed: types.SimpleNamespace, tmp_path: Path, libc: Libc,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    cfg = tmp_path / "gate.json"
    cfg.write_text(json.dumps(installed.config))
    assert gate_tool.main(["setup-check", "--config", str(cfg)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"
