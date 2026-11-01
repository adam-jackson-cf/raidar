"""Job lifecycle seams with real processes: detached supervisor, session, signals, real git.

The decisions (status states, launch records, exit records, guard scheduling) are fast tests in
``test_detach_job.py`` and ``test_target_loss_job_guard.py``; these prove that the real
supervisor, ``ps`` and ``git`` behave as those decisions assume.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from raidar.contract.job_layout import DETACH_DIR, JOB_MANIFEST, TRIAL_RECORDS_DIR
from raidar.engine.job.detach import (
    EXIT_FILE,
    LAUNCH_FILE,
    LOG_FILE,
    PID_FILE,
    DetachError,
    experiment_id,
    git_state,
    launch,
    main,
    read_job_files,
    status,
)
from raidar.engine.job.run import create_job_dir

pytestmark = pytest.mark.slow

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
    record = json.loads((detach / LAUNCH_FILE).read_text())
    assert set(record["git"]) == {"commit", "dirty", "error"}
    _wait_for(detach / PID_FILE)
    assert status(launched.job_dir).state in {"running", "finished"}
    _wait_for(detach / EXIT_FILE)
    assert json.loads((detach / EXIT_FILE).read_text())["exit_code"] == 0
    log = (detach / LOG_FILE).read_text()
    job_session = int(log.split("session ")[1].split()[0])
    assert job_session != os.getsid(0), "the job must not share the launching session"
    done = status(launched.job_dir, log_lines=5)
    assert (done.state, done.trials_recorded, done.trials_selected) == ("finished", 1, 2)
    assert "fake job main-02" in done.render()


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


def test_a_recycled_pid_is_not_a_running_supervisor(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    detach = job_dir / DETACH_DIR
    detach.mkdir(parents=True)
    (detach / LAUNCH_FILE).write_text("{}")
    (detach / PID_FILE).write_text(f"{os.getpid()}\n")  # alive, but this is pytest

    assert status(job_dir).state == "died"


def test_a_terminate_sent_to_the_supervisor_ends_the_job_and_leaves_an_exit_record(
    tmp_path: Path,
) -> None:
    launched = launch(
        _command(tmp_path, sleep=60),
        experiment=_experiment(tmp_path),
        jobs_dir=tmp_path / "jobs",
        job_id="stopped",
        cwd=tmp_path,
    )
    detach = launched.job_dir / DETACH_DIR
    _wait_for(detach / PID_FILE)
    _wait_for(detach / LOG_FILE)
    deadline = time.monotonic() + 30
    while "fake job" not in (detach / LOG_FILE).read_text():
        assert time.monotonic() < deadline, "detached child never became ready"
        time.sleep(0.05)

    os.kill(int((detach / PID_FILE).read_text()), signal.SIGTERM)
    _wait_for(detach / EXIT_FILE)

    assert json.loads((detach / EXIT_FILE).read_text())["exit_code"] != 0
    assert status(launched.job_dir).state == "failed"


def test_the_real_git_reports_the_commit_and_dirtiness_the_launch_record_assumes(
    tmp_path: Path,
) -> None:
    git = shutil.which("git")
    assert git is not None, "git is required for the launch provenance seam"
    repo = tmp_path / "repo"
    repo.mkdir()
    identity = {"NAME": "t", "EMAIL": "t@t"}
    env = os.environ | {
        f"GIT_{who}_{key}": v for who in ("AUTHOR", "COMMITTER") for key, v in identity.items()
    }
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "c"]):
        subprocess.run(["git", *args], cwd=repo, check=True, env=env)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    assert git_state(repo) == {"commit": head, "dirty": False, "error": None}
    (repo / "new.txt").write_text("x")
    assert git_state(repo)["dirty"] is True
    (repo / "new.txt").unlink()
    (repo / "docs").mkdir()
    (repo / "docs" / "note.md").write_text("x")
    assert git_state(repo, exclude=("docs",))["dirty"] is False
    (repo / "code.py").write_text("x")
    assert git_state(repo, exclude=("docs",))["dirty"] is True


def test_a_launch_persists_its_record_and_never_reuses_a_job_dir(tmp_path: Path) -> None:
    experiment = _experiment(tmp_path)
    (tmp_path / "jobs" / EXPERIMENT_ID / "taken").mkdir(parents=True)
    with pytest.raises(DetachError, match="never reused"):
        launch(["true"], experiment=experiment, jobs_dir=tmp_path / "jobs", job_id="taken")
    with pytest.raises(DetachError, match="no command"):
        launch([], experiment=experiment, jobs_dir=tmp_path / "jobs")

    launched = launch(
        [sys.executable, "-c", "pass"], experiment=experiment, jobs_dir=tmp_path / "jobs"
    )

    detach = launched.job_dir / DETACH_DIR
    record = json.loads((detach / LAUNCH_FILE).read_text())
    assert record["command"][-1] == f"JOB_ID={launched.job_id}"
    assert launched.job_dir.parent == (tmp_path / "jobs" / EXPERIMENT_ID).resolve()
    _wait_for(detach / EXIT_FILE)


def test_raidar_run_accepts_a_detached_job_dir_but_never_a_used_one(tmp_path: Path) -> None:
    detached = tmp_path / "detached"
    (detached / DETACH_DIR).mkdir(parents=True)
    create_job_dir(detached)
    (detached / "plan.json").write_text("{}")
    with pytest.raises(FileExistsError):
        create_job_dir(detached)
    fresh = tmp_path / "fresh" / "job"
    create_job_dir(fresh)
    assert fresh.is_dir()


def test_the_experiment_file_names_the_job_dir_and_unreadable_ones_are_refused(
    tmp_path: Path,
) -> None:
    assert experiment_id(_experiment(tmp_path)) == EXPERIMENT_ID
    bad = (("id: [unclosed\n", "cannot read"), ("kind: ab-test\n", "no experiment id"))
    for text, message in bad:
        path = tmp_path / "bad.yaml"
        path.write_text(text)
        with pytest.raises(DetachError, match=message):
            experiment_id(path)
    with pytest.raises(DetachError, match="cannot read"):
        experiment_id(tmp_path / "absent.yaml")


def test_a_job_directory_is_read_into_the_files_status_decides_from(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    (job_dir / TRIAL_RECORDS_DIR).mkdir(parents=True)
    (job_dir / TRIAL_RECORDS_DIR / "t1.json").write_text("{}")
    (job_dir / JOB_MANIFEST).write_text(json.dumps({"selected": ["a"], "finished_at": "f"}))
    assert status(job_dir).state == "finished"
    plain = read_job_files(job_dir)
    assert (plain.detached, plain.trial_records, plain.pid, plain.log_text) == (False, 1, None, "")

    detach = job_dir / DETACH_DIR
    detach.mkdir()
    (detach / LAUNCH_FILE).write_text("{}")
    (detach / PID_FILE).write_text("not a pid")
    (detach / LOG_FILE).write_text("a\nb\n")
    (detach / EXIT_FILE).write_text(json.dumps({"exit_code": 0}))
    detached = read_job_files(job_dir)
    assert (detached.detached, detached.pid, detached.log_text) == (True, None, "a\nb\n")
    assert detached.exit_record == {"exit_code": 0}
    assert status(job_dir, log_lines=1).log_tail == ("b",)
    with pytest.raises(DetachError, match="not a job directory"):
        status(tmp_path / "missing")

    for path, expected in (
        (job_dir / JOB_MANIFEST, "failed"),
        (detach / EXIT_FILE, "not-started"),
        (detach / PID_FILE, "finished"),
    ):
        original = path.read_bytes()
        path.write_bytes(b"\xff")
        assert status(job_dir).state == expected
        path.write_bytes(original)


def test_the_cli_reports_status_and_refuses_a_missing_job(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["status", str(tmp_path / "nowhere")]) == 2
    assert "not a job directory" in capsys.readouterr().err

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    assert main(["status", str(job_dir)]) == 0
    assert "state: unknown" in capsys.readouterr().out
    assert main(["launch", "--experiment", str(tmp_path / "x.yaml"), "--jobs-dir", "j", "a"]) == 2


def test_a_command_that_cannot_start_leaves_an_exit_record_of_127(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))  # No caffeinate wrapper can mask exec's failure.
    launched = launch(
        ["/nonexistent/raidar-detach-binary"],
        experiment=_experiment(tmp_path),
        jobs_dir=tmp_path / "jobs",
        job_id="missing",
        cwd=tmp_path,
    )
    detach = launched.job_dir / DETACH_DIR
    _wait_for(detach / EXIT_FILE)

    ended = json.loads((detach / EXIT_FILE).read_text())

    assert ended["exit_code"] == 127
    assert "FileNotFoundError" in ended["error"]
    assert status(launched.job_dir).state == "failed"
