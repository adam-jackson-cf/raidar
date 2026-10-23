"""Detached jobs (``make experiment-execute DETACH=1``, ``make experiment-status``).

A detached launch must return at once, run its command under a supervisor in
its own session (so the end of the launching shell or agent session cannot
reach it), keep the supervisor's pid, the job's log and its exit record under
``<job dir>/detach/``, and leave a job directory that ``raidar run`` accepts.
Status is read-only and names the state.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from raidar.execution.detach import (
    EXIT_FILE,
    LAUNCH_FILE,
    LOG_FILE,
    PID_FILE,
    DetachError,
    experiment_id,
    git_state,
    launch,
    main,
    status,
)
from raidar.execution.run import create_job_dir
from raidar.job_layout import DETACH_DIR, JOB_MANIFEST, TRIAL_RECORDS_DIR

EXPERIMENT_ID = "python__toy__detach"
# Stands in for `make experiment-execute DETACH=0 ...`: writes the job like raidar run does.
FAKE_JOB = """
import json, os, sys, time
job_id = sys.argv[-1].split("=", 1)[1]
job_dir = os.path.join(sys.argv[1], job_id)
print("fake job", job_id, "session", os.getsid(0), flush=True)
time.sleep(float(sys.argv[2]))
os.makedirs(os.path.join(job_dir, "trial-records"), exist_ok=True)
open(os.path.join(job_dir, "trial-records", "t1.json"), "w").write("{}")
json.dump(
    {"selected": ["t1", "t2"], "started_at": "s", "finished_at": "f",
     "summary": {"trials": 1, "valid": 1}},
    open(os.path.join(job_dir, "job.json"), "w"),
)
raise SystemExit(int(sys.argv[3]))
"""


def _experiment(tmp_path: Path) -> Path:
    path = tmp_path / "experiment.yaml"
    path.write_text(f"schema_version: 2\nid: {EXPERIMENT_ID}\n")
    return path


def _command(tmp_path: Path, *, sleep: float = 0.2, code: int = 0) -> list[str]:
    script = tmp_path / "fake_job.py"
    script.write_text(FAKE_JOB)
    jobs = tmp_path / "jobs" / EXPERIMENT_ID
    return [sys.executable, str(script), str(jobs), str(sleep), str(code)]


def _wait_for(path: Path, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} never appeared"
        time.sleep(0.05)


def test_a_detached_job_runs_in_its_own_session_and_records_how_it_ended(
    tmp_path: Path,
) -> None:
    launched = launch(
        _command(tmp_path, sleep=0.5),
        experiment=_experiment(tmp_path),
        jobs_dir=tmp_path / "jobs",
        job_id="main-02",
        cwd=tmp_path,
    )

    detach = launched.job_dir / DETACH_DIR
    assert launched.job_dir == (tmp_path / "jobs" / EXPERIMENT_ID / "main-02").resolve()
    record = json.loads((detach / LAUNCH_FILE).read_text())
    assert record["command"][-1] == "JOB_ID=main-02"
    assert set(record["git"]) == {"commit", "dirty", "error"}
    assert record["job_dir"] == str(launched.job_dir)
    _wait_for(detach / PID_FILE)
    running = status(launched.job_dir)
    assert running.state in {"running", "finished"}
    _wait_for(detach / EXIT_FILE)
    ended = json.loads((detach / EXIT_FILE).read_text())
    assert ended["exit_code"] == 0
    log = (detach / LOG_FILE).read_text()
    assert "fake job main-02" in log
    job_session = int(log.split("session ")[1].split()[0])
    assert job_session != os.getsid(0), "the job must not share the launching session"
    done = status(launched.job_dir, log_lines=5)
    assert (done.state, done.trials_recorded, done.trials_selected) == ("finished", 1, 2)
    rendered = done.render()
    assert rendered.startswith("state: finished\n")
    assert "trials recorded: 1 of 2" in rendered
    assert "fake job main-02" in rendered


def test_a_failing_command_reads_failed_with_its_exit_code(tmp_path: Path) -> None:
    launched = launch(
        _command(tmp_path, code=3),
        experiment=_experiment(tmp_path),
        jobs_dir=tmp_path / "jobs",
        job_id="j",
        cwd=tmp_path,
    )
    _wait_for(launched.job_dir / DETACH_DIR / EXIT_FILE)

    found = status(launched.job_dir)

    assert found.state == "failed"
    assert "exited 3" in found.detail


def test_a_supervisor_that_vanished_without_an_exit_record_reads_died(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    detach = job_dir / DETACH_DIR
    detach.mkdir(parents=True)
    (detach / LAUNCH_FILE).write_text("{}")
    (detach / PID_FILE).write_text("999999\n")

    assert status(job_dir).state == "died"
    (detach / PID_FILE).unlink()
    assert status(job_dir).state == "not-started"


def test_status_reads_jobs_that_were_not_detached(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    (job_dir / TRIAL_RECORDS_DIR).mkdir(parents=True)
    (job_dir / JOB_MANIFEST).write_text(json.dumps({"selected": ["a"], "started_at": "s"}))

    assert status(job_dir).state == "unknown"
    (job_dir / JOB_MANIFEST).write_text(
        json.dumps({"selected": ["a"], "finished_at": "f", "aborted": "CancelledError"})
    )
    assert status(job_dir).state == "failed"
    with pytest.raises(DetachError, match="not a job directory"):
        status(tmp_path / "missing")


def test_launch_refuses_an_existing_job_dir_and_raidar_run_accepts_a_detached_one(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "jobs" / EXPERIMENT_ID / "taken"
    existing.mkdir(parents=True)
    with pytest.raises(DetachError, match="never reused"):
        launch(
            ["true"], experiment=_experiment(tmp_path), jobs_dir=tmp_path / "jobs", job_id="taken"
        )

    detached = tmp_path / "detached"
    (detached / DETACH_DIR).mkdir(parents=True)
    create_job_dir(detached)
    (detached / "plan.json").write_text("{}")
    with pytest.raises(FileExistsError):
        create_job_dir(detached)
    fresh = tmp_path / "fresh" / "job"
    create_job_dir(fresh)
    assert fresh.is_dir()


def test_the_experiment_id_names_the_job_dir_and_the_cli_reports_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert experiment_id(_experiment(tmp_path)) == EXPERIMENT_ID
    empty = tmp_path / "empty.yaml"
    empty.write_text("kind: ab-test\n")
    with pytest.raises(DetachError, match="names no experiment id"):
        experiment_id(empty)

    code = main(["status", str(tmp_path / "nowhere")])

    assert code == 2
    assert "not a job directory" in capsys.readouterr().err


def test_the_launch_record_names_the_commit_and_whether_the_tree_was_dirty(
    tmp_path: Path,
) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "repo"
    repo.mkdir()
    identity = {"NAME": "t", "EMAIL": "t@t"}
    env = os.environ | {
        f"GIT_{who}_{key}": v for who in ("AUTHOR", "COMMITTER") for key, v in identity.items()
    }
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "c"]):
        subprocess.run([git, *args], cwd=repo, check=True, env=env)  # noqa: S603
    head = subprocess.run(  # noqa: S603
        [git, "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    assert git_state(repo) == {"commit": head, "dirty": False, "error": None}
    (repo / "new.txt").write_text("x")
    assert git_state(repo) == {"commit": head, "dirty": True, "error": None}
    (repo / "new.txt").unlink()
    (repo / "docs").mkdir()
    (repo / "docs" / "note.md").write_text("x")
    assert git_state(repo, exclude=("docs",))["dirty"] is False
    (repo / "code.py").write_text("x")
    assert git_state(repo, exclude=("docs",))["dirty"] is True


def test_a_launch_without_git_records_why_instead_of_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("raidar.execution.detach.shutil.which", lambda _name: None)
    assert git_state(tmp_path) == {"commit": None, "dirty": None, "error": "git not found"}
    monkeypatch.undo()
    monkeypatch.setattr("raidar.execution.detach.shutil.which", lambda _name: "/nonexistent/git")
    failed = git_state(tmp_path)
    assert (failed["commit"], failed["dirty"]) == (None, None)
    assert "FileNotFoundError" in failed["error"]
