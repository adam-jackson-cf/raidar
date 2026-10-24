"""Detached jobs: launch a long job so it survives the launching shell or agent session.

A job started as a background task of an interactive shell or an agent session
dies with that session (``main-01`` of the guardrails experiment was killed
about 90 s after launch that way). ``make experiment-execute DETACH=1`` runs
``launch``, which:

1. resolves the job directory (``<jobs dir>/<experiment id>/<job id>``; a job
   id is generated when none is given) and refuses one that already exists;
2. writes ``<job dir>/detach/launch.json`` (command, working directory, job id,
   start time, and the git commit the job runs from with whether the tree was
   dirty, so "``make quality`` passed at the job's commit" can be checked from
   the record rather than inferred) and starts a supervisor in a new session (``setsid``
   semantics: no controlling terminal, its own process group), so a hang-up or
   the end of the launching session does not reach it;
3. returns at once, printing the job directory, the supervisor's pid and how
   to poll it.

The supervisor (``supervise``) runs the command (``make experiment-execute
DETACH=0 JOB_ID=<id>`` with the caller's other variables) under ``caffeinate
-i`` when that exists (macOS: no idle sleep while the job runs), with its
output in ``detach/job.log``; it records its pid in ``detach/pid`` and, when
the command ends, ``detach/exit.json`` (exit code, end time). ``raidar run``
accepts a job directory that holds only ``detach/``
(``raidar.execution.run.create_job_dir``).

``status`` (``make experiment-status JOB=<job dir>``) reads a job directory,
detached or not, and reports its state (``running``, ``finished``,
``failed``, ``died``, ``not-started`` or ``unknown``), the trials recorded
against the trials selected, and the log's last lines. Polling never changes
anything.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from raidar.core.job_layout import DETACH_DIR, JOB_MANIFEST, TRIAL_RECORDS_DIR

LAUNCH_FILE = "launch.json"
PID_FILE = "pid"
LOG_FILE = "job.log"
EXIT_FILE = "exit.json"
DEFAULT_LOG_LINES = 20


class DetachError(ValueError):
    """The job cannot be launched detached as asked."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def default_job_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def experiment_id(experiment: Path) -> str:
    """The experiment's ``id`` (the job directory's parent name)."""
    try:
        document = yaml.safe_load(experiment.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DetachError(f"cannot read {experiment}: {exc}") from exc
    found = document.get("id") if isinstance(document, dict) else None
    if not isinstance(found, str) or not found:
        raise DetachError(f"{experiment} names no experiment id")
    return found


@dataclass(frozen=True)
class Launched:
    job_dir: Path
    job_id: str
    pid: int
    log: Path


def git_state(cwd: Path, *, exclude: Sequence[str] = ()) -> dict[str, Any]:
    """The checkout's HEAD commit and whether its tree is dirty (provenance only).

    ``exclude`` names repo-root paths whose changes (tracked or untracked) do not
    count as dirty. Never fails a launch: outside a git checkout, or without git,
    the fields are null and ``error`` says why.
    """
    git = shutil.which("git")
    if git is None:
        return {"commit": None, "dirty": None, "error": "git not found"}
    try:
        head = subprocess.run(  # noqa: S603 - fixed git arguments
            [git, "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True
        )
        pathspec = [":(top)", *(f":(top,exclude){path}" for path in exclude)]
        status = subprocess.run(  # noqa: S603 - fixed git arguments
            [git, "status", "--porcelain", "--", *pathspec],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"commit": None, "dirty": None, "error": f"{type(exc).__name__}: {exc}"}
    return {"commit": head.stdout.strip(), "dirty": bool(status.stdout.strip()), "error": None}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def launch(
    command: Sequence[str],
    *,
    experiment: Path,
    jobs_dir: Path,
    job_id: str | None = None,
    cwd: Path | None = None,
) -> Launched:
    """Start ``command JOB_ID=<id>`` under a detached supervisor; return without waiting."""
    if not command:
        raise DetachError("no command to launch")
    job_id = job_id or default_job_id()
    job_dir = jobs_dir.resolve() / experiment_id(experiment) / job_id
    if job_dir.exists():
        raise DetachError(f"{job_dir} already exists; a job directory is never reused")
    detach = job_dir / DETACH_DIR
    detach.mkdir(parents=True)
    full = [*command, f"JOB_ID={job_id}"]
    workdir = (cwd or Path.cwd()).resolve()
    _write_json(
        detach / LAUNCH_FILE,
        {
            "job_id": job_id,
            "job_dir": str(job_dir),
            "experiment": str(experiment),
            "command": full,
            "cwd": str(workdir),
            "launched_at": _now(),
            "log": str(detach / LOG_FILE),
            "git": git_state(workdir),
        },
    )
    supervisor = [sys.executable, "-m", "raidar.execution.detach", "supervise", str(detach), "--"]
    with open(os.devnull, "rb") as stdin, (detach / LOG_FILE).open("ab") as log:
        process = subprocess.Popen(  # noqa: S603 - the caller's own make command
            [*supervisor, *full],
            cwd=workdir,
            stdin=stdin,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return Launched(job_dir=job_dir, job_id=job_id, pid=process.pid, log=detach / LOG_FILE)


def supervise(detach: Path, command: Sequence[str]) -> int:
    """Run ``command`` to completion (kept awake where possible) and record how it ended."""
    (detach / PID_FILE).write_text(f"{os.getpid()}\n", encoding="utf-8")
    keep_awake = shutil.which("caffeinate")
    full = [keep_awake, "-i", *command] if keep_awake else list(command)
    # A hang-up is not the job's concern; a TERM/INT still ends it (forwarded below).
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    code: int | None = None
    error: str | None = None
    children: list[subprocess.Popen[bytes]] = []
    early: list[int] = []

    def forward(signum: int, _frame: object) -> None:
        if children:
            children[0].send_signal(signum)
        else:
            early.append(signum)  # arrived before the job started: delivered right after

    # Installed before the job starts: a TERM in that window must not kill the supervisor
    # without its exit record.
    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)
    try:
        process = subprocess.Popen(full, stdin=subprocess.DEVNULL)  # noqa: S603
        children.append(process)
        for signum in early:
            process.send_signal(signum)
        code = process.wait()
    except OSError as exc:
        error = f"{type(exc).__name__}: {exc}"
        code = 127
    finally:
        _write_json(
            detach / EXIT_FILE,
            {
                "exit_code": code,
                "finished_at": _now(),
                "error": error,
                "kept_awake": bool(keep_awake),
            },
        )
    return code if code is not None else 1


# ---- status ----------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _alive(pid: int) -> bool:
    """Whether ``pid`` is still this module's supervisor (a recycled pid is not).

    After a reboot or a long wait the recorded pid may belong to an unrelated process, so
    a live pid counts only when its command line is the supervisor's.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False  # another user's process cannot be our supervisor
    ps = shutil.which("ps")
    if ps is None:
        return True
    try:
        listed = subprocess.run(  # noqa: S603 - fixed ps arguments
            [ps, "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=False
        )
    except OSError:
        return True
    return "raidar.execution.detach" in listed.stdout


def _pid(detach: Path) -> int | None:
    try:
        return int((detach / PID_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _tail(path: Path, lines: int) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:] if lines > 0 else []


@dataclass(frozen=True)
class JobStatus:
    job_dir: Path
    state: str
    detail: str
    trials_recorded: int
    trials_selected: int | None
    log_tail: tuple[str, ...]

    def render(self) -> str:
        selected = "?" if self.trials_selected is None else str(self.trials_selected)
        lines = [
            f"state: {self.state}",
            f"job: {self.job_dir}",
            f"trials recorded: {self.trials_recorded} of {selected}",
            f"detail: {self.detail}",
        ]
        if self.log_tail:
            lines += ["log (last lines):", *(f"  {line}" for line in self.log_tail)]
        return "\n".join(lines)


def _job_state(manifest: dict[str, Any] | None) -> tuple[str, str] | None:
    """The state ``job.json`` alone decides (a finished or aborted job), else None."""
    if manifest is None or not manifest.get("finished_at"):
        return None
    if manifest.get("aborted"):
        return "failed", f"job aborted ({manifest['aborted']}) at {manifest['finished_at']}"
    summary = manifest.get("summary") or {}
    return "finished", (
        f"finished at {manifest['finished_at']}: {summary.get('trials')} trial(s), "
        f"{summary.get('valid')} valid"
    )


def _detached_state(detach: Path, manifest: dict[str, Any] | None) -> tuple[str, str]:
    exit_record = _read_json(detach / EXIT_FILE)
    pid = _pid(detach)
    if exit_record is not None:
        code = exit_record.get("exit_code")
        ended = _job_state(manifest)
        if code == 0 and ended is not None:
            return ended
        if code == 0:
            return "failed", "the command exited 0 but job.json has no finished_at"
        return "failed", f"the command exited {code} at {exit_record.get('finished_at')}"
    if pid is not None and _alive(pid):
        started = (manifest or {}).get("started_at")
        return "running", f"supervisor pid {pid}" + (f"; job started {started}" if started else "")
    if pid is None:
        return "not-started", "launched, but the supervisor has not recorded its pid yet"
    return "died", f"supervisor pid {pid} is gone and left no exit record (killed or rebooted)"


def status(job_dir: Path, *, log_lines: int = DEFAULT_LOG_LINES) -> JobStatus:
    """The job's state from its directory; read-only."""
    if not job_dir.is_dir():
        raise DetachError(f"{job_dir} is not a job directory")
    manifest = _read_json(job_dir / JOB_MANIFEST)
    detach = job_dir / DETACH_DIR
    recorded = len(list((job_dir / TRIAL_RECORDS_DIR).glob("*.json")))
    selected = manifest.get("selected") if manifest else None
    if (detach / LAUNCH_FILE).is_file():
        state, detail = _detached_state(detach, manifest)
        tail = _tail(detach / LOG_FILE, log_lines)
    else:
        state, detail = _job_state(manifest) or (
            "unknown",
            "not launched detached and job.json has no finished_at: running in some session, "
            "or ended without finishing (make harbor-cleanup JOB=... after a stopped job)",
        )
        tail = []
    return JobStatus(
        job_dir=job_dir,
        state=state,
        detail=detail,
        trials_recorded=recorded,
        trials_selected=len(selected) if isinstance(selected, list) else None,
        log_tail=tuple(tail),
    )


# ---- command line ----------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m raidar.execution.detach")
    commands = parser.add_subparsers(dest="action", required=True)
    start = commands.add_parser("launch", help="start a command detached for one job")
    start.add_argument("--experiment", type=Path, required=True)
    start.add_argument("--jobs-dir", type=Path, required=True)
    start.add_argument("--job-id", default=None)
    start.add_argument("command", nargs=argparse.REMAINDER)
    run = commands.add_parser("supervise", help=argparse.SUPPRESS)
    run.add_argument("detach_dir", type=Path)
    run.add_argument("command", nargs=argparse.REMAINDER)
    show = commands.add_parser("status", help="report a job's state")
    show.add_argument("job_dir", type=Path)
    show.add_argument("--lines", type=int, default=DEFAULT_LOG_LINES)
    return parser


def _command(parts: list[str]) -> list[str]:
    return parts[1:] if parts[:1] == ["--"] else parts


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "supervise":
            return supervise(args.detach_dir, _command(args.command))
        if args.action == "status":
            print(status(args.job_dir, log_lines=args.lines).render())
            return 0
        launched = launch(
            _command(args.command),
            experiment=args.experiment,
            jobs_dir=args.jobs_dir,
            job_id=args.job_id or None,
        )
    except DetachError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"launched detached job {launched.job_id} (supervisor pid {launched.pid})")
    print(f"job dir: {launched.job_dir}")
    print(f"log: {launched.log}")
    print(f"poll: make experiment-status JOB={launched.job_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
