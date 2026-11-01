"""Detached jobs (``make experiment-execute DETACH=1``, ``make experiment-status``): decisions.

Everything here is decided from in-memory inputs with injected process, signal-forwarding and
``which`` seams: no job directory, file or process is touched. The persisted launch record,
job-directory collision and acceptance, real supervisor session and signals are the slow
seams in ``test_target_loss_job_guard_slow.py``.
"""

from __future__ import annotations

import signal
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from raidar.engine.job.detach import (
    DetachError,
    JobFiles,
    SignalForwarder,
    _alive,
    detached_state,
    experiment_id_of,
    git_state,
    parse_object,
    parse_pid,
    plan_launch,
    status_of,
    supervisor_command,
    tail_lines,
)

JOB = Path("/jobs/python__toy__detach/main-02")


def _done(argv: list[str], out: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")


class FakeProcess:
    def __init__(self) -> None:
        self.sent: list[int] = []

    def send_signal(self, signum: int) -> None:
        self.sent.append(signum)


# ---- experiment id and parsing ---------------------------------------------------------


def test_the_experiment_id_names_the_job_dir() -> None:
    assert experiment_id_of({"id": "python__toy__detach"}, "e.yaml") == "python__toy__detach"


@pytest.mark.parametrize("document", [{"kind": "ab-test"}, {"id": ""}, {"id": 3}, ["id"], None])
def test_an_experiment_without_a_usable_id_is_refused(document: object) -> None:
    with pytest.raises(DetachError, match="e.yaml names no experiment id"):
        experiment_id_of(document, "e.yaml")


@pytest.mark.parametrize(
    ("text", "expected"),
    [('{"a": 1}', {"a": 1}), ("[1]", None), ("{broken", None), ("", None), ("3", None)],
)
def test_only_a_json_object_is_a_record(text: str, expected: dict[str, Any] | None) -> None:
    assert parse_object(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"), [("12\n", 12), (" 7 ", 7), ("", None), ("12 13", None), ("x", None)]
)
def test_a_pid_file_holds_one_integer(text: str, expected: int | None) -> None:
    assert parse_pid(text) == expected


@pytest.mark.parametrize(
    ("lines", "expected"),
    [(2, ["two", "three"]), (10, ["one", "two", "three"]), (0, []), (-1, [])],
)
def test_the_log_tail_is_the_last_lines_and_never_negative(lines: int, expected: list[str]) -> None:
    assert tail_lines("one\ntwo\nthree\n", lines) == expected


# ---- git provenance (injected which/run) -----------------------------------------------


def _git(porcelain: str = "", *, fail: Exception | None = None) -> tuple[Any, list[list[str]]]:
    seen: list[list[str]] = []

    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        if fail:
            raise fail
        return _done(argv, "abc123\n" if "rev-parse" in argv else porcelain)

    return run, seen


def test_the_launch_record_names_the_commit_and_whether_the_tree_was_dirty(tmp_path: Path) -> None:
    clean_run, clean_seen = _git()
    dirty_run, dirty_seen = _git(" M a.py")
    which = {"git": "/bin/git"}.get

    clean = git_state(tmp_path, which=which, run=clean_run)
    dirty = git_state(tmp_path, exclude=("docs", "notes"), which=which, run=dirty_run)

    assert clean == {"commit": "abc123", "dirty": False, "error": None}
    assert dirty == {"commit": "abc123", "dirty": True, "error": None}
    assert clean_seen[0][:2] == ["/bin/git", "rev-parse"]
    assert clean_seen[1][-1:] == [":(top)"]
    assert dirty_seen[1][-3:] == [":(top)", ":(top,exclude)docs", ":(top,exclude)notes"]


def test_a_launch_without_git_records_why_instead_of_failing(tmp_path: Path) -> None:
    found = git_state(tmp_path, which=lambda _name: None)

    assert found == {"commit": None, "dirty": None, "error": "git not found"}


@pytest.mark.parametrize(
    ("failure", "named"),
    [
        (FileNotFoundError("gone"), "FileNotFoundError"),
        (subprocess.CalledProcessError(128, ["git"]), "CalledProcessError"),
    ],
)
def test_a_git_that_fails_is_recorded_not_raised(
    tmp_path: Path, failure: Exception, named: str
) -> None:
    found = git_state(tmp_path, which={"git": "/bin/git"}.get, run=_git(fail=failure)[0])

    assert (found["commit"], found["dirty"]) == (None, None)
    assert named in found["error"]


# ---- launch plan -----------------------------------------------------------------------


def _plan(command: list[str], **changes: Any) -> Any:
    arguments: dict[str, Any] = {
        "experiment": Path("exp.yaml"),
        "job_dir": JOB,
        "job_id": "main-02",
        "workdir": Path("/work"),
        "launched_at": "2026-01-01T00:00:00+00:00",
        "git": {"commit": "abc", "dirty": False, "error": None},
        "python": "/usr/bin/python3",
    } | changes
    return plan_launch(command, **arguments)


def test_a_launch_plan_names_the_record_and_supervisor_for_an_absolute_job_dir() -> None:
    plan = _plan(["make", "run"])
    detach = JOB / "detach"

    assert plan.command == ["make", "run", "JOB_ID=main-02"]
    assert plan.record["command"] == plan.command
    assert plan.record["git"]["commit"] == "abc"
    assert plan.record["job_dir"] == str(JOB)
    assert plan.record["cwd"] == "/work"
    assert plan.record["log"] == str(detach / "job.log")
    assert plan.record["launched_at"] == "2026-01-01T00:00:00+00:00"
    assert plan.supervisor[:3] == ["/usr/bin/python3", "-m", "raidar.engine.job.detach"]
    assert plan.supervisor[3:5] == ["supervise", str(detach)]
    assert plan.supervisor[-3:] == plan.command


# ---- supervisor ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("awake", "expected"),
    [("/usr/bin/caffeinate", ["/usr/bin/caffeinate", "-i", "job", "X=1"]), (None, ["job", "X=1"])],
)
def test_the_job_is_kept_awake_only_where_caffeinate_exists(
    awake: str | None, expected: list[str]
) -> None:
    assert supervisor_command(("job", "X=1"), awake) == expected


def test_a_terminate_is_forwarded_once_the_job_runs_and_held_until_then() -> None:
    forwarder = SignalForwarder()
    running, early = FakeProcess(), FakeProcess()

    forwarder.started(cast(Any, running))
    forwarder(signal.SIGINT)

    assert running.sent == [signal.SIGINT]

    before = SignalForwarder()
    before(signal.SIGTERM, None)  # arrives before the job exists
    before(signal.SIGINT, None)
    assert early.sent == []
    before.started(cast(Any, early))
    assert early.sent == [signal.SIGTERM, signal.SIGINT]


# ---- is the recorded pid still the supervisor ------------------------------------------


def _probe(
    *,
    kill: Exception | None = None,
    ps: str | None = "/bin/ps",
    listed: str | Exception = "",
) -> bool:
    def fake_kill(pid: int, sig: int) -> None:
        if kill:
            raise kill

    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if isinstance(listed, Exception):
            raise listed
        return _done(argv, listed)

    return _alive(7, kill=fake_kill, which={"ps": ps}.get, run=run)


@pytest.mark.parametrize(
    ("setup", "alive"),
    [
        ({"kill": ProcessLookupError()}, False),
        ({"kill": PermissionError()}, False),
        ({"ps": None}, True),
        ({"listed": OSError("ps vanished")}, True),
        ({"listed": "python -m raidar.engine.job.detach supervise"}, True),
        ({"listed": "vim notes.txt"}, False),  # a recycled pid
        ({"listed": ""}, False),
    ],
)
def test_a_pid_is_the_supervisor_only_while_its_command_line_says_so(
    setup: dict[str, Any], alive: bool
) -> None:
    assert _probe(**setup) is alive


# ---- status ----------------------------------------------------------------------------


def _files(**changes: Any) -> JobFiles:
    arguments: dict[str, Any] = {"manifest": None, "trial_records": 0, "detached": True} | changes
    return JobFiles(**arguments)


def _status(files: JobFiles, *, alive: bool = True, log_lines: int = 20) -> Any:
    return status_of(JOB, files, alive=alive, log_lines=log_lines)


FINISHED = {
    "selected": ["t1", "t2"],
    "finished_at": "f",
    "summary": {"trials": 1, "valid": 1},
}


def test_a_detached_job_that_exited_zero_is_finished_only_when_job_json_says_so() -> None:
    assert _status(_files(exit_record={"exit_code": 0})).state == "failed"

    done = _status(
        _files(
            exit_record={"exit_code": 0},
            manifest=FINISHED,
            trial_records=1,
            log_text="one\ntwo\nthree\n",
        ),
        log_lines=2,
    )

    assert (done.state, done.trials_recorded, done.trials_selected) == ("finished", 1, 2)
    assert done.log_tail == ("two", "three")
    rendered = done.render()
    assert rendered.startswith("state: finished\n")
    assert f"job: {JOB}" in rendered
    assert "trials recorded: 1 of 2" in rendered
    assert "log (last lines):\n  two\n  three" in rendered


def test_a_failing_command_reads_failed_with_its_exit_code() -> None:
    found = _status(_files(exit_record={"exit_code": 3, "finished_at": "f"}))

    assert found.state == "failed"
    assert "exited 3 at f" in found.detail


@pytest.mark.parametrize(
    ("pid", "alive", "state"),
    [(123, True, "running"), (123, False, "died"), (None, True, "not-started")],
)
def test_without_an_exit_record_the_supervisor_decides_the_state(
    pid: int | None, alive: bool, state: str
) -> None:
    found = _status(_files(pid=pid, manifest={"started_at": "s"}), alive=alive)

    assert found.state == state
    if state == "running":
        assert found.detail == "supervisor pid 123; job started s"
        assert "log (last lines)" not in found.render()


def test_a_running_supervisor_without_a_manifest_names_only_its_pid() -> None:
    found = _status(_files(pid=9))

    assert (found.state, found.detail) == ("running", "supervisor pid 9")
    assert found.trials_selected is None
    assert "trials recorded: 0 of ?" in found.render()


def test_status_reads_jobs_that_were_not_detached() -> None:
    def plain(manifest: dict[str, Any] | None) -> Any:
        return _status(_files(detached=False, manifest=manifest, log_text="ignored\n"))

    assert plain({"selected": ["a"], "started_at": "s"}).state == "unknown"
    assert plain(None).state == "unknown"
    aborted = plain({"selected": ["a"], "finished_at": "f", "aborted": "CancelledError"})
    assert (aborted.state, "CancelledError" in aborted.detail) == ("failed", True)
    assert plain({"selected": ["a"], "finished_at": "f"}).state == "finished"
    assert plain({"selected": ["a"]}).log_tail == ()
    assert plain({"selected": "not a list"}).trials_selected is None


def test_an_exit_record_outranks_a_live_looking_supervisor() -> None:
    assert detached_state({"exit_code": 1}, 5, True, None)[0] == "failed"
    assert detached_state(None, 5, True, None)[0] == "running"
    assert detached_state(None, None, True, None)[0] == "not-started"
