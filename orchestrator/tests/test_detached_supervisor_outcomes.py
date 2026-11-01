"""Detached supervisor, launch and status outcomes (``raidar.engine.job.detach``).

The job process, signal handlers, ``which``, clock and the job directory are all in memory:
``MemPath`` stands in for ``Path`` (no directory or file is created), and the supervisor's
process and signal modules are fakes. No real process, signal or file is touched.
"""

from __future__ import annotations

import json
import re
import signal
from types import SimpleNamespace
from typing import Any

import pytest

from raidar.contract.job_layout import DETACH_DIR, JOB_MANIFEST, TRIAL_RECORDS_DIR
from raidar.engine.job import detach
from raidar.engine.job.detach import DetachError

NOW = "2026-01-01T00:00:00+00:00"


class MemFs:
    """Files and directories by path string."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.dirs: set[str] = set()


class MemPath:
    """The slice of ``pathlib.Path`` the detach module uses, over ``MemFs``."""

    def __init__(self, fs: MemFs, path: str) -> None:
        self.fs = fs
        self.path = path

    def __str__(self) -> str:
        return self.path

    def __eq__(self, other: object) -> bool:
        return isinstance(other, MemPath) and other.path == self.path

    def __hash__(self) -> int:
        return hash(self.path)

    def __truediv__(self, name: str) -> MemPath:
        return MemPath(self.fs, f"{self.path}/{name}")

    def resolve(self) -> MemPath:
        return self

    def is_file(self) -> bool:
        return self.path in self.fs.files

    def is_dir(self) -> bool:
        return self.path in self.fs.dirs

    def exists(self) -> bool:
        return self.is_file() or self.is_dir()

    def mkdir(self, parents: bool = False) -> None:
        assert parents
        parts = self.path.split("/")
        for end in range(2, len(parts) + 1):
            self.fs.dirs.add("/".join(parts[:end]))

    def write_text(self, text: str, encoding: str = "utf-8") -> None:
        self.fs.files[self.path] = text

    def read_text(self, encoding: str = "utf-8", errors: str = "strict") -> str:
        if self.path not in self.fs.files:
            raise FileNotFoundError(self.path)
        return self.fs.files[self.path]

    def open(self, mode: str) -> Any:
        self.fs.files.setdefault(self.path, "")
        return _Handle()

    def glob(self, pattern: str) -> list[MemPath]:
        suffix = pattern.removeprefix("*")
        return [
            MemPath(self.fs, name)
            for name in sorted(self.fs.files)
            if name.rsplit("/", 1)[0] == self.path and name.endswith(suffix)
        ]


class _Handle:
    def __enter__(self) -> _Handle:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class FakeJob:
    """A job process that ends with ``code``, delivering any signals while it runs."""

    def __init__(self, world: World, code: int = 0, deliver: tuple[int, ...] = ()) -> None:
        self.world = world
        self.code = code
        self.deliver = deliver
        self.sent: list[int] = []
        self.pid = 777

    def wait(self) -> int:
        for signum in self.deliver:
            self.world.handlers[signum](signum, None)
        return self.code

    def send_signal(self, signum: int) -> None:
        self.sent.append(signum)


class World:
    """Everything the supervisor and launcher reach outside the module, recorded."""

    def __init__(self) -> None:
        self.fs = MemFs()
        self.handlers: dict[int, Any] = {}
        self.started: list[tuple[list[str], dict[str, Any]]] = []
        self.job = FakeJob(self)
        self.start_error: OSError | None = None
        self.early: tuple[int, ...] = ()
        self.which_result: str | None = None
        self.ignored: list[int] = []

    def path(self, path: str) -> MemPath:
        return MemPath(self.fs, path)

    def popen(self, argv: list[str], **kwargs: Any) -> FakeJob:
        self.started.append((argv, kwargs))
        if self.start_error is not None:
            raise self.start_error
        for signum in self.early:
            self.handlers[signum](signum, None)  # arrives before the job has a process
        return self.job

    def install(self, signum: int, handler: Any) -> None:
        if handler is signal.SIG_IGN:
            self.ignored.append(signum)
        self.handlers[signum] = handler

    def exit_record(self, detach_dir: MemPath) -> dict[str, Any]:
        return json.loads((detach_dir / detach.EXIT_FILE).read_text())


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    fake = World()
    monkeypatch.setattr(
        detach, "subprocess", SimpleNamespace(Popen=fake.popen, DEVNULL=-3, STDOUT=-2)
    )
    monkeypatch.setattr(
        detach,
        "signal",
        SimpleNamespace(
            signal=fake.install,
            SIGHUP=signal.SIGHUP,
            SIGTERM=signal.SIGTERM,
            SIGINT=signal.SIGINT,
            SIG_IGN=signal.SIG_IGN,
        ),
    )
    monkeypatch.setattr(detach, "shutil", SimpleNamespace(which=lambda _name: fake.which_result))
    monkeypatch.setattr(detach, "open", lambda *_args: _Handle(), raising=False)
    monkeypatch.setattr(detach, "_now", lambda: NOW)
    return fake


# ---- supervise -------------------------------------------------------------------------


def test_a_job_that_finishes_is_recorded_with_its_exit_code_and_kept_awake(world: World) -> None:
    world.which_result = "/usr/bin/caffeinate"
    detach_dir = world.path("/j/detach")

    code = detach.supervise(detach_dir, ["make", "run", "JOB_ID=a"])  # type: ignore[arg-type]

    assert code == 0
    assert world.started[0][0] == ["/usr/bin/caffeinate", "-i", "make", "run", "JOB_ID=a"]
    assert world.exit_record(detach_dir) == {
        "exit_code": 0,
        "finished_at": NOW,
        "error": None,
        "kept_awake": True,
    }
    assert (detach_dir / detach.PID_FILE).read_text().strip().isdigit()
    assert world.ignored == [signal.SIGHUP]  # a hang-up is not the job's concern


def test_without_caffeinate_the_job_runs_unwrapped_and_the_record_says_so(world: World) -> None:
    world.job = FakeJob(world, code=3)
    detach_dir = world.path("/j/detach")

    code = detach.supervise(detach_dir, ["make", "run"])  # type: ignore[arg-type]

    assert code == 3
    assert world.started[0][0] == ["make", "run"]
    assert world.exit_record(detach_dir)["exit_code"] == 3
    assert world.exit_record(detach_dir)["kept_awake"] is False


def test_a_job_that_cannot_start_is_recorded_as_127_with_the_reason(world: World) -> None:
    world.start_error = FileNotFoundError("no such make")
    detach_dir = world.path("/j/detach")

    code = detach.supervise(detach_dir, ["make"])  # type: ignore[arg-type]

    record = world.exit_record(detach_dir)
    assert code == 127
    assert record["exit_code"] == 127
    assert record["error"] == "FileNotFoundError: no such make"


def test_a_terminate_during_the_job_reaches_it_and_its_exit_is_still_recorded(
    world: World,
) -> None:
    world.job = FakeJob(world, code=-15, deliver=(signal.SIGTERM,))
    detach_dir = world.path("/j/detach")

    code = detach.supervise(detach_dir, ["make"])  # type: ignore[arg-type]

    assert world.job.sent == [signal.SIGTERM]
    assert code == -15
    assert world.exit_record(detach_dir)["exit_code"] == -15


def test_a_terminate_before_the_job_starts_is_delivered_right_after_it_starts(
    world: World,
) -> None:
    world.early = (signal.SIGINT,)
    detach_dir = world.path("/j/detach")

    detach.supervise(detach_dir, ["make"])  # type: ignore[arg-type]

    assert world.job.sent == [signal.SIGINT]
    assert (detach_dir / detach.EXIT_FILE).is_file()


# ---- launch ----------------------------------------------------------------------------


@pytest.fixture
def git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        detach, "git_state", lambda _cwd: {"commit": "abc", "dirty": False, "error": None}
    )


def _experiment(world: World, text: str = "id: toy__exp\n") -> MemPath:
    path = world.path("/work/exp.yaml")
    path.write_text(text)
    return path


def test_a_launch_records_the_job_and_starts_a_detached_supervisor(world: World, git: None) -> None:
    launched = detach.launch(
        ["make", "run"],
        experiment=_experiment(world),  # type: ignore[arg-type]
        jobs_dir=world.path("/jobs"),  # type: ignore[arg-type]
        job_id="main-01",
        cwd=world.path("/work"),  # type: ignore[arg-type]
    )

    job_dir = world.path("/jobs/toy__exp/main-01")
    record = json.loads((job_dir / DETACH_DIR / detach.LAUNCH_FILE).read_text())
    argv, kwargs = world.started[0]
    assert launched.job_dir == job_dir
    assert launched.pid == 777
    assert launched.log == job_dir / DETACH_DIR / detach.LOG_FILE
    assert record["command"] == ["make", "run", "JOB_ID=main-01"]
    assert record["git"]["commit"] == "abc"
    assert argv[-3:] == record["command"]
    assert kwargs["start_new_session"] is True
    assert kwargs["cwd"] == world.path("/work")


def test_a_job_directory_is_never_reused(world: World, git: None) -> None:
    world.path("/jobs/toy__exp/main-01").mkdir(parents=True)

    with pytest.raises(DetachError, match="already exists"):
        detach.launch(
            ["make"],
            experiment=_experiment(world),  # type: ignore[arg-type]
            jobs_dir=world.path("/jobs"),  # type: ignore[arg-type]
            job_id="main-01",
        )

    assert world.started == []


def test_a_launch_without_a_command_is_refused(world: World) -> None:
    with pytest.raises(DetachError, match="no command to launch"):
        detach.launch(
            [],
            experiment=world.path("/work/exp.yaml"),  # type: ignore[arg-type]
            jobs_dir=world.path("/jobs"),  # type: ignore[arg-type]
        )


def test_the_default_job_id_is_a_utc_timestamp() -> None:
    assert re.fullmatch(r"\d{8}T\d{6}Z", detach.default_job_id())


def test_an_unreadable_or_malformed_experiment_file_is_refused(world: World) -> None:
    with pytest.raises(DetachError, match="cannot read"):
        detach.experiment_id(world.path("/work/absent.yaml"))  # type: ignore[arg-type]
    with pytest.raises(DetachError, match="cannot read"):
        detach.experiment_id(_experiment(world, "id: [unclosed\n"))  # type: ignore[arg-type]
    with pytest.raises(DetachError, match="names no experiment id"):
        detach.experiment_id(_experiment(world, "title: no id\n"))  # type: ignore[arg-type]


# ---- status of a job directory ---------------------------------------------------------


def _job(
    world: World,
    *,
    detached: bool = True,
    pid: str | None = "4242",
    exit_code: int | None = None,
    manifest: dict[str, Any] | str | None = None,
    log: str | None = "one\ntwo\nthree\n",
    trials: int = 0,
) -> MemPath:
    job = world.path("/jobs/exp/job")
    (job / TRIAL_RECORDS_DIR).mkdir(parents=True)
    for number in range(trials):
        (job / TRIAL_RECORDS_DIR / f"{number}.json").write_text("{}")
    if manifest is not None:
        (job / JOB_MANIFEST).write_text(
            manifest if isinstance(manifest, str) else json.dumps(manifest)
        )
    if detached:
        (job / DETACH_DIR).mkdir(parents=True)
        (job / DETACH_DIR / detach.LAUNCH_FILE).write_text("{}")
        if pid is not None:
            (job / DETACH_DIR / detach.PID_FILE).write_text(pid)
        if log is not None:
            (job / DETACH_DIR / detach.LOG_FILE).write_text(log)
        if exit_code is not None:
            (job / DETACH_DIR / detach.EXIT_FILE).write_text(
                json.dumps({"exit_code": exit_code, "finished_at": NOW})
            )
    return job


def test_a_missing_directory_is_not_a_job(world: World) -> None:
    with pytest.raises(DetachError, match="is not a job directory"):
        detach.status(world.path("/jobs/absent"))  # type: ignore[arg-type]


def test_a_running_job_reports_its_supervisor_progress_and_log_tail(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(detach, "_alive", lambda pid: pid == 4242)
    job = _job(world, manifest={"started_at": "t0", "selected": [1, 2, 3]}, trials=2)

    result = detach.status(job, log_lines=2)  # type: ignore[arg-type]

    assert result.state == "running"
    assert result.trials_recorded == 2
    assert result.trials_selected == 3
    assert result.log_tail == ("two", "three")
    assert "supervisor pid 4242; job started t0" in result.detail
    assert "trials recorded: 2 of 3" in result.render()


def test_a_finished_job_is_read_from_its_exit_record_without_looking_up_the_supervisor(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected(_pid: int) -> bool:
        raise AssertionError("a job with an exit record needs no supervisor lookup")

    monkeypatch.setattr(detach, "_alive", unexpected)
    job = _job(
        world,
        exit_code=0,
        manifest={"finished_at": "t1", "summary": {"trials": 4, "valid": 3}},
    )

    result = detach.status(job, log_lines=0)  # type: ignore[arg-type]

    assert result.state == "finished"
    assert result.log_tail == ()
    assert "4 trial(s), 3 valid" in result.detail


def test_a_job_missing_its_pid_and_log_is_not_started_and_has_no_log(world: World) -> None:
    job = _job(world, pid=None, log=None, manifest="not json")

    result = detach.status(job)  # type: ignore[arg-type]

    assert result.state == "not-started"
    assert result.log_tail == ()
    assert result.trials_selected is None


def test_a_supervisor_that_vanished_without_an_exit_record_died(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(detach, "_alive", lambda _pid: False)

    result = detach.status(_job(world, pid="4242\n"))  # type: ignore[arg-type]

    assert result.state == "died"
    assert "left no exit record" in result.detail


def test_an_undetached_unfinished_job_is_unknown_with_no_log(world: World) -> None:
    job = _job(world, detached=False, manifest={"selected": [1]})

    result = detach.status(job)  # type: ignore[arg-type]

    assert result.state == "unknown"
    assert result.log_tail == ()
    assert result.trials_selected == 1


# ---- command line ----------------------------------------------------------------------


def test_the_status_command_prints_the_rendered_state(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[int] = []

    def status(job_dir: Any, *, log_lines: int) -> detach.JobStatus:
        seen.append(log_lines)
        return detach.JobStatus(job_dir, "running", "supervisor pid 1", 2, 5, ("tail",))

    monkeypatch.setattr(detach, "status", status)

    assert detach.main(["status", "/jobs/exp/job", "--lines", "1"]) == 0

    out = capsys.readouterr().out
    assert seen == [1]
    assert "state: running" in out
    assert "trials recorded: 2 of 5" in out
    assert "  tail" in out


def test_a_refusal_is_reported_on_stderr_with_exit_status_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert detach.main(["status", "/nonexistent/raidar/job"]) == 2

    assert "is not a job directory" in capsys.readouterr().err


def test_a_launch_command_without_a_command_is_refused_before_anything_starts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = detach.main(["launch", "--experiment", "e.yaml", "--jobs-dir", "/jobs", "--"])

    assert code == 2
    assert "no command to launch" in capsys.readouterr().err


def test_the_launch_command_reports_where_to_poll_the_job(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def launch(command: list[str], **kwargs: Any) -> detach.Launched:
        assert command == ["make", "run"]  # the "--" separator is not part of the command
        job_dir = kwargs["jobs_dir"] / "x" / kwargs["job_id"]
        return detach.Launched(job_dir, kwargs["job_id"], 9, job_dir / "log")

    monkeypatch.setattr(detach, "launch", launch)

    argv = ["launch", "--experiment", "e.yaml", "--jobs-dir", "/jobs", "--job-id", "m-9"]
    code = detach.main([*argv, "--", "make", "run"])

    out = capsys.readouterr().out
    assert code == 0
    assert "launched detached job m-9 (supervisor pid 9)" in out
    assert "poll: make experiment-status JOB=/jobs/x/m-9" in out


def test_the_supervise_command_returns_the_jobs_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(detach, "supervise", lambda _dir, command: 5 if command == ["make"] else 0)

    assert detach.main(["supervise", "/j/detach", "--", "make"]) == 5
