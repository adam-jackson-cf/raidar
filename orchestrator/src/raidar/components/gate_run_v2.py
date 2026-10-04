"""The gate run of ``guardrail-gate@2``: @1's run, a ``shadow`` mode and git evidence.

``raidar.components.gate_run`` (guardrail-gate@1) is frozen: its bytes are in
released locks. This module adds, for @2 only:

- ``shadow`` mode: the same commit check as ``commit`` mode (``git add -A`` and
  ``git commit`` with the gate command as the pre-commit hook), but in a git
  directory outside the workspace (``<work_dir>/shadow/.git`` with the
  workspace as its work tree), so the workspace is never written: no ``.git``,
  no commit, no hook in it. The wrapper records the run and never refuses;
- git evidence after every commit attempt (``commit`` and ``shadow``): ``HEAD``
  before and after, ``git log --oneline`` and ``git status --porcelain`` of the
  repository the commit went to, so a refusal is verifiable from git itself
  (``HEAD`` unchanged, the staged paths still in the status) and a pass shows
  its commit.

A run is read back into @1's ``GateRun`` (a shadow run decides like a commit
run) plus ``GitEvidence``; ``run_record`` writes @1's run record with the mode
and the git evidence added.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from pydantic import Field

from raidar.components.gate_run import (
    GIT_IDENTITY,
    MAX_OUTPUT_CHARS,
    OUTPUT_SEPARATOR,
    REPORT_DIR_ENV,
    RUN_ENV,
    GateMode,
    GateRun,
    GuardrailGateOptions,
    _hook_script,
    _int,
    report_dir_for,
)
from raidar.oracle.snapshot import SNAPSHOT_EXCLUDES

COMPONENT = "guardrail-gate@2"
VERSION = "2"
SHADOW_DIR = "shadow"
GIT_LOG_MARK = "----raidar-guardrail-gate-git-log----"
GIT_STATUS_MARK = "----raidar-guardrail-gate-git-status----"
GIT_LOG_LINES = 50
GIT_STATUS_LINES = 200
SHADOW_TERMINATION = "shadow_recorded"


class GateModeV2(StrEnum):
    COMMIT = "commit"
    COMMAND = "command"
    SHADOW = "shadow"


ENGINE_MODE = {
    GateModeV2.COMMIT: GateMode.COMMIT,
    GateModeV2.COMMAND: GateMode.COMMAND,
    GateModeV2.SHADOW: GateMode.COMMIT,  # a shadow run decides exactly as a commit run
}


class GuardrailGateOptionsV2(GuardrailGateOptions):
    mode: GateModeV2 = GateModeV2.COMMIT  # type: ignore[assignment]
    component: str = Field(default=COMPONENT)

    @property
    def shadow(self) -> bool:
        return self.mode is GateModeV2.SHADOW

    @property
    def engine_mode(self) -> GateMode:
        return ENGINE_MODE[self.mode]


@dataclass(frozen=True)
class GitEvidence:
    """The commit's repository right after the attempt (``workspace`` or ``shadow``)."""

    repository: str
    head_before: str | None
    head_after: str | None
    log: tuple[str, ...]
    status: tuple[str, ...]

    @property
    def head_moved(self) -> bool:
        return self.head_after is not None and self.head_after != self.head_before

    def record(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "head_before": self.head_before,
            "head_after": self.head_after,
            "head_moved": self.head_moved,
            "log_oneline": list(self.log),
            "status_porcelain": list(self.status),
        }


def _git_command(shadow: bool, git_dir: PurePosixPath) -> str:
    base = f"git -c safe.directory='*' {GIT_IDENTITY} -c commit.gpgsign=false"
    if not shadow:
        return base
    # The work tree is the workspace (the current directory); git writes only git_dir.
    return f"{base} --git-dir={shlex.quote(str(git_dir))} --work-tree=."


def _commit_lines(options: GuardrailGateOptionsV2, run: int, work: PurePosixPath) -> list[str]:
    """Stage everything and commit with the gate as the pre-commit hook, then git evidence."""
    shadow_git = work / SHADOW_DIR / ".git"
    git = _git_command(options.shadow, shadow_git)
    excludes = " ".join(shlex.quote(name) for name in (*SNAPSHOT_EXCLUDES, "*.pyc"))
    message = shlex.quote(f"guardrail-gate: commit after session (gate run {run})")
    hooks = shlex.quote(str(work / "hooks"))
    commit = (
        f"{git} add -A && {git} -c core.hooksPath={hooks} "
        f"commit --allow-empty --no-edit -q -m {message}"
    )
    if options.shadow:
        q_shadow = shlex.quote(str(work / SHADOW_DIR))
        setup = [
            f"rm -rf {q_shadow} && git -c init.defaultBranch=main init -q {q_shadow} && "
            f"printf '%s\\n' {excludes} >> {shlex.quote(str(shadow_git / 'info' / 'exclude'))}",
            "echo repo=shadow",
        ]
    else:
        setup = [
            f'top="$({git} rev-parse --show-toplevel 2>/dev/null || true)"',
            'if [ "$top" != "$(pwd -P)" ]; then',
            f"  {git} -c init.defaultBranch=main init -q . && mkdir -p .git/info && "
            f"printf '%s\\n' {excludes} >> .git/info/exclude && echo repo_created=1",
            "fi",
            "echo repo=workspace",
        ]
    timeout = f"timeout -k 10 {options.timeout_sec}"
    hook = _hook_script(options.command, str(work / "hook-rc"))
    return [
        *setup,
        'mkdir -p "$work/hooks"',
        f"printf '%s' {shlex.quote(hook)} > \"$work/hooks/pre-commit\" && "
        'chmod 755 "$work/hooks/pre-commit"',
        f"printf '%s\\n' {shlex.quote(commit)} > \"$work/commit.sh\"",
        f'echo "head_before=$({git} rev-parse -q --verify HEAD 2>/dev/null)"',
        f'{timeout} bash "$work/commit.sh" > "$log" 2>&1 </dev/null',
        'echo "git_rc=$?"',
        'echo "rc=$(cat "$work/hook-rc" 2>/dev/null)"',
        f'echo "head_after=$({git} rev-parse -q --verify HEAD 2>/dev/null)"',
        f'evidence_log="$({git} log --oneline -n {GIT_LOG_LINES} 2>/dev/null)"',
        f'evidence_status="$({git} status --porcelain 2>/dev/null | head -n {GIT_STATUS_LINES})"',
    ]


def gate_script(options: GuardrailGateOptionsV2, run: int, log: PurePosixPath) -> str:
    """The bash run in the workspace: the gate, key=value lines, git evidence, the output."""
    work = PurePosixPath(options.work_dir)
    q_log, q_work = shlex.quote(str(log)), shlex.quote(str(work))
    q_reports = shlex.quote(str(report_dir_for(log)))
    lines = [
        "set -u",
        f"log={q_log}; work={q_work}",
        f"export {RUN_ENV}={run} {REPORT_DIR_ENV}={q_reports}",
        'mkdir -p "$(dirname "$log")" "$work" "$' + REPORT_DIR_ENV + '" && rm -f "$work/hook-rc"',
    ]
    evidence: list[str] = []
    if options.mode is GateModeV2.COMMAND:
        timeout = f"timeout -k 10 {options.timeout_sec}"
        lines += [
            f'{timeout} bash -c {shlex.quote(options.command)} > "$log" 2>&1 </dev/null',
            'echo "rc=$?"',
        ]
    else:
        lines += _commit_lines(options, run, work)
        evidence = [
            f"echo {GIT_LOG_MARK}",
            '[ -n "$evidence_log" ] && printf \'%s\\n\' "$evidence_log"',
            f"echo {GIT_STATUS_MARK}",
            '[ -n "$evidence_status" ] && printf \'%s\\n\' "$evidence_status"',
        ]
    half = MAX_OUTPUT_CHARS // 2
    lines += [
        'bytes="$(wc -c < "$log" | tr -d " ")"',
        'echo "bytes=$bytes"',
        'echo "sha256=$(sha256sum < "$log" | cut -d" " -f1)"',
        *evidence,
        f"echo {OUTPUT_SEPARATOR}",
        f'if [ "$bytes" -le {MAX_OUTPUT_CHARS} ]; then cat "$log"; else head -c {half} "$log"; '
        "printf '\\n... gate output truncated (%s bytes in all) ...\\n' \"$bytes\"; "
        f'tail -c {half} "$log"; fi',
    ]
    return "\n".join(lines) + "\n"


def _sections(head: str) -> tuple[dict[str, str], tuple[str, ...], tuple[str, ...]]:
    """key=value lines, git log lines and git status lines of the script's head."""
    values_text, has_log, rest = head.partition(GIT_LOG_MARK + "\n")
    log_text, _, status_text = rest.partition(GIT_STATUS_MARK + "\n")
    values = dict(line.split("=", 1) for line in values_text.splitlines() if "=" in line)
    if not has_log:
        return values, (), ()
    return values, tuple(log_text.splitlines()), tuple(status_text.splitlines())


@dataclass(frozen=True)
class GateRunV2:
    """One gate run: @1's decided run plus the mode it ran in and the git evidence."""

    gate: GateRun
    mode: GateModeV2
    git: GitEvidence | None

    def record(self) -> dict[str, Any]:
        record = self.gate.record()
        record["mode"] = self.mode.value
        if self.mode is GateModeV2.SHADOW:
            # Nothing is committed to the workspace; the commit went to the shadow repository.
            record["committed"] = None
            record["shadow_committed"] = self.gate.passed
        record["git"] = self.git.record() if self.git is not None else None
        return record


def read_gate_run(stdout: str, *, run: int, after: str, mode: GateModeV2, log: str) -> GateRunV2:
    head, _, output = stdout.partition(OUTPUT_SEPARATOR + "\n")
    values, git_log, git_status = _sections(head)
    engine = ENGINE_MODE[mode]
    gate = GateRun(
        run=run,
        after=after,
        mode=engine,
        exit_code=_int(values.get("rc")),
        git_exit_code=_int(values.get("git_rc")) if engine is GateMode.COMMIT else None,
        output=output,
        output_sha256=values.get("sha256", ""),
        output_bytes=_int(values.get("bytes")) or 0,
        log=log,
        repo_created=values.get("repo_created") == "1",
    )
    git = None
    if "repo" in values:
        git = GitEvidence(
            repository=values["repo"],
            head_before=values.get("head_before") or None,
            head_after=values.get("head_after") or None,
            log=git_log,
            status=git_status,
        )
    return GateRunV2(gate=gate, mode=mode, git=git)
