"""The autofix step of ``guardrail-gate``: its script, its result and its typed errors.

Pure and host-side, like ``raidar.components.runtime.gate_run``: ``autofix_script``
renders the bash the wrapper (``raidar.components.runtime.guardrail_gate``) runs in
the trial container and ``read_autofix_run`` reads its result back.

The command is the pack's safe-fix command (``with.autofix_command``, for
example python-guardrails@8's ``guardrail-check --autofix``); the gate never
decides which fixers run. Its contract: run in the current directory, change
only what its safe fixers change, exit 0 when the pass completed, and write
one JSON report (``verdict``, ``files_changed``, ``lines_changed``,
``codes_fixed``, ``codes_fixed_blocking``, ``error``, ``error_type``) to
``$RAIDAR_GATE_REPORT_DIR``.

Two modes:

- ``apply`` (an enforcing ``commit`` gate): the command runs in the workspace,
  right before the gate's ``git add -A`` and ``git commit`` with the gate as the
  pre-commit hook, so the commit holds the fixed tree, as a repository's
  pre-commit autofix hooks leave it;
- ``dry_run`` (a ``shadow`` gate): the workspace is copied (``tar``, the
  snapshot excludes left out; a top-level ``.venv`` linked, not copied, so the
  gate's mypy and tests see the same interpreter) to a throwaway directory
  under the gate's work dir, outside the workspace, and the command runs there.
  The workspace is never written; the wrapper then runs the same shadow check
  in the copy and deletes it.

An autofix that cannot complete is never silent: ``AutofixRun.error_type``
types it (``ERROR_TYPES``) and the gate runs without autofix for that gate run
(apply), or records no post-autofix check (dry run).
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from raidar.components.runtime.gate_run import REPORT_DIR_ENV, RUN_ENV, TIMEOUT_EXIT_CODES
from raidar.core.snapshot import SNAPSHOT_EXCLUDES

AUTOFIX_LOG = "autofix_run_{run}.log"
POST_CHECK_LOG = "post_autofix_run_{run}.log"
COPY_DIR = "autofix"
"""Under the gate's work dir: ``<work>/autofix/app`` (the copy) and ``<work>/autofix/gate``."""
REPORT_MARK = "----raidar-guardrail-gate-autofix-report----"
LINKED_DIRS = (".venv",)
"""Top-level directories a dry run links into its copy instead of copying."""
NOT_FOUND_EXIT = 127


class AutofixMode(StrEnum):
    APPLY = "apply"
    DRY_RUN = "dry_run"


ERROR_TYPES = (
    "exec_failed",  # the container exec itself raised
    "copy_failed",  # dry run: the workspace could not be copied
    "timeout",  # the in-container timeout ended the command
    "not_runnable",  # exit 127 (command not found) with no report
    "command_failed",  # a non-zero exit and no report
    "report_missing",  # exit 0 but no single report in the report dir
    "report_invalid",  # the report is not a JSON object with a verdict
    "command_error",  # the command reported its own error (its error_type is kept)
)


def copy_root(work_dir: str) -> PurePosixPath:
    return PurePosixPath(work_dir) / COPY_DIR / "app"


def copy_gate_dir(work_dir: str) -> PurePosixPath:
    """The post-autofix shadow check's own work dir (its hook and shadow git dir)."""
    return PurePosixPath(work_dir) / COPY_DIR / "gate"


def _copy_lines(work_dir: str) -> list[str]:
    base = shlex.quote(str(PurePosixPath(work_dir) / COPY_DIR))
    copy = shlex.quote(str(copy_root(work_dir)))
    excludes = " ".join(f"--exclude={shlex.quote(name)}" for name in SNAPSHOT_EXCLUDES)
    links = " ".join(shlex.quote(name) for name in LINKED_DIRS)
    return [
        "set -o pipefail",
        f"copy={copy}",
        f'if rm -rf {base} && mkdir -p "$copy" && '
        f'tar -cf - {excludes} . | tar -xf - -C "$copy"; then',
        f'  for name in {links}; do [ -d "$name" ] && ln -s "$(pwd -P)/$name" "$copy/$name"; done',
        '  echo "copy_rc=0"; cd "$copy" || exit 0',
        "else",
        '  echo "copy_rc=1"; exit 0',
        "fi",
    ]


def autofix_script(
    command: str, mode: AutofixMode, run: int, log: PurePosixPath, work_dir: str, timeout_sec: int
) -> str:
    """The bash for one autofix pass: (copy,) the command, its exit code and its report."""
    reports = log.with_suffix("")
    lines = [
        "set -u",
        f"log={shlex.quote(str(log))}; reports={shlex.quote(str(reports))}",
        f'export {RUN_ENV}={run} {REPORT_DIR_ENV}="$reports"',
        'mkdir -p "$(dirname "$log")" && rm -rf "$reports" && mkdir -p "$reports"',
    ]
    if mode is AutofixMode.DRY_RUN:
        lines += _copy_lines(work_dir)
    lines += [
        f'timeout -k 10 {timeout_sec} bash -c {shlex.quote(command)} > "$log" 2>&1 </dev/null',
        'echo "rc=$?"',
        'count="$(find "$reports" -maxdepth 1 -name \'*.json\' -type f | wc -l | tr -d " ")"',
        'echo "reports=$count"',
        f"echo {REPORT_MARK}",
        'if [ "$count" = 1 ]; then cat "$reports"/*.json; fi',
    ]
    return "\n".join(lines) + "\n"


def cleanup_script(work_dir: str) -> str:
    return f"rm -rf {shlex.quote(str(PurePosixPath(work_dir) / COPY_DIR))}\n"


@dataclass(frozen=True)
class AutofixRun:
    """One autofix pass, as read back from the container."""

    run: int
    after: str  # the session it followed
    mode: AutofixMode
    log: str
    report_dir: str
    exit_code: int | None = None
    error_type: str | None = None
    error: str | None = None
    report: dict[str, Any] | None = None
    before_gate_run: int | None = None  # apply: the gate run it preceded
    after_gate_run: int | None = None  # dry run: the shadow gate run it followed
    duration_sec: float | None = None
    post_check: dict[str, Any] | None = None  # dry run: the shadow check of the fixed copy

    @property
    def ok(self) -> bool:
        return self.error_type is None

    def _reported(self, key: str) -> Any:
        return self.report.get(key) if self.ok and self.report is not None else None

    def record(self) -> dict[str, Any]:
        changed = self._reported("files_changed")
        return {
            "run": self.run,
            "after": self.after,
            "mode": self.mode.value,
            "before_gate_run": self.before_gate_run,
            "after_gate_run": self.after_gate_run,
            "exit_code": self.exit_code,
            "error_type": self.error_type,
            "error": self.error,
            # apply: the gate run that follows is @3's (no fix pass before it).
            "fallback": self.mode is AutofixMode.APPLY and not self.ok,
            "verdict": self._reported("verdict"),
            "files_changed": changed,
            "files_changed_count": len(changed) if isinstance(changed, list) else None,
            "lines_changed": self._reported("lines_changed"),
            "codes_fixed": self._reported("codes_fixed"),
            "codes_fixed_blocking": self._reported("codes_fixed_blocking"),
            "report_schema": self.report.get("schema") if self.report is not None else None,
            "log": self.log,
            "report_dir": self.report_dir,
            "duration_sec": self.duration_sec,
            "post_check": self.post_check,
        }


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _report(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) and "verdict" in payload else None


Classified = tuple[str | None, str | None, dict[str, Any] | None]
"""(error_type, error, report) of one pass."""


def _exit_error(values: dict[str, str]) -> tuple[str, str] | None:
    """The pass's error from its exit alone (copy, exit code, timeout, report count)."""
    rc = _int(values.get("rc"))
    if values.get("copy_rc", "0") != "0":
        found = ("copy_failed", "the workspace could not be copied for the dry run")
    elif rc is None:
        found = ("exec_failed", "the autofix script reported no exit code")
    elif rc in TIMEOUT_EXIT_CODES:
        found = ("timeout", f"the autofix command did not finish (exit {rc})")
    elif values.get("reports") == "1":
        return None
    elif rc == NOT_FOUND_EXIT:
        found = ("not_runnable", f"the autofix command is not runnable (exit {rc})")
    elif rc != 0:
        found = ("command_failed", f"the autofix command failed (exit {rc}) with no report")
    else:
        found = ("report_missing", f"exit 0 with {values.get('reports')} report(s), not 1")
    return found


def _classify(values: dict[str, str], body: str) -> Classified:
    """(error_type, error, report) from the script's key=value lines and report body."""
    failed = _exit_error(values)
    if failed is not None:
        return (*failed, None)
    report = _report(body)
    if report is None:
        return "report_invalid", "the autofix report is not a JSON object with a verdict", None
    rc = _int(values.get("rc"))
    if rc != 0 or report.get("verdict") == "error":
        detail = f"{report.get('error_type')}: {report.get('error')} (exit {rc})"
        return "command_error", detail, report
    return None, None, report


def read_autofix_run(stdout: str, base: AutofixRun) -> AutofixRun:
    """``base`` (run, session, mode, paths) completed from the script's output."""
    head, _, body = stdout.partition(REPORT_MARK + "\n")
    values = dict(line.split("=", 1) for line in head.splitlines() if "=" in line)
    error_type, error, report = _classify(values, body)
    return AutofixRun(
        run=base.run,
        after=base.after,
        mode=base.mode,
        log=base.log,
        report_dir=base.report_dir,
        exit_code=_int(values.get("rc")),
        error_type=error_type,
        error=error,
        report=report,
        before_gate_run=base.before_gate_run,
        after_gate_run=base.after_gate_run,
    )


__all__ = [
    "AUTOFIX_LOG",
    "ERROR_TYPES",
    "POST_CHECK_LOG",
    "AutofixMode",
    "AutofixRun",
    "autofix_script",
    "cleanup_script",
    "copy_gate_dir",
    "copy_root",
    "read_autofix_run",
]
