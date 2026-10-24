"""The gate run of ``guardrail-gate``: its options, script, outcome and fix prompt.

Pure and host-side: ``gate_script`` renders the bash the wrapper
(``raidar.subjects.treatments.runtime.guardrail_gate``) runs in the trial container,
``read_gate_run`` reads its result back, ``parse_checks`` finds the checks in the
gate's output and ``fix_prompt`` tells the agent what refused its work.

Modes: ``commit`` (``git add -A`` and ``git commit`` with the gate command as the
pre-commit hook, in the workspace), ``command`` (the command alone) and ``shadow``
(the commit check in a git directory outside the workspace,
``<work_dir>/shadow/.git`` with the workspace as its work tree, so the workspace
is never written; the wrapper records the run and never refuses). A commit or
shadow run also reads git evidence back: ``HEAD`` before and after, ``git log
--oneline`` and ``git status --porcelain`` of the repository the commit went to,
so a refusal is verifiable from git itself.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from harbor.agents.options import AgentOptions
from pydantic import Field

from raidar.contracts.snapshot import SNAPSHOT_EXCLUDES
from raidar.subjects.treatments.runtime.review_loop import PROMPT_ARG_BUDGET, quoted_size, render
from raidar.subjects.treatments.runtime.session_chain import InnerAgentSpec

COMPONENT = "guardrail-gate@4"
VERSION = "4"
GATE_DIR_NAME = "guardrail-gate"
GATE_RECORD = "guardrail-gate.json"
SIGNAL = "gate_clean_at_step_end"
# Exported to the gate command: the run number, and a fresh directory under the
# gate's own log dir where the command may write machine-readable reports (for
# example ``guardrail-check --json-dir "$RAIDAR_GATE_REPORT_DIR"``). The wrapper
# never moves that dir into a session's stage dir, so a report stays with its run.
RUN_ENV = "RAIDAR_GATE_RUN"
REPORT_DIR_ENV = "RAIDAR_GATE_REPORT_DIR"
# Under the workflow scratch dir the per-step reset owns (step_reset.sh
# RAIDAR_WORKFLOW_DIR), so hooks from one step never survive into the next.
DEFAULT_WORK_DIR = "/tmp/raidar-review/guardrail-gate"
# Gate output read back from the container (head and tail beyond this).
MAX_OUTPUT_CHARS = 60_000
OUTPUT_SEPARATOR = "----raidar-guardrail-gate-output----"
TIMEOUT_EXIT_CODES = (124, 137)
NOT_RUNNABLE_EXIT_CODES = (126, 127)
GIT_IDENTITY = "-c user.name=raidar-guardrail-gate -c user.email=guardrail-gate@raidar.invalid"
# Paths the hidden verifier uses (Harbor uploads tests to /tests at verification).
VERIFIER_PATH_RE = re.compile(r"(^|[\s'\"=:;(])/(tests|logs/verifier)(/|\b)|raidar-oracle")
SHADOW_DIR = "shadow"
GIT_LOG_MARK = "----raidar-guardrail-gate-git-log----"
GIT_STATUS_MARK = "----raidar-guardrail-gate-git-status----"
GIT_LOG_LINES = 50
GIT_STATUS_LINES = 200
SHADOW_TERMINATION = "shadow_recorded"


class GateMode(StrEnum):
    COMMIT = "commit"
    COMMAND = "command"
    SHADOW = "shadow"  # the commit check in a git directory outside the workspace, never enforced


GATE_EVENTS = {
    GateMode.COMMIT: "the repository refused your commit: its pre-commit hook `%s`",
    GateMode.COMMAND: "the repository's commit gate `%s`",
}

FIX_PROMPT = """\
You were working on the task below in the directory you are already in. When you
stopped, %%%GATE_EVENT%%%. Its output follows.

Fix what it reports in the working tree, keeping the work the task asks for.
The gate runs again when you stop.

%%%TASK%%%

The gate's output:

%%%OUTPUT%%%
"""


class GuardrailGateOptions(AgentOptions):
    inner_agent: InnerAgentSpec
    command: str = Field(min_length=1, description="The workspace's commit-gate command")
    mode: GateMode = GateMode.COMMIT
    max_rounds: int = Field(default=3, ge=1, le=5, description="Fix sessions at most")
    timeout_sec: int = Field(default=600, ge=10, le=3600)
    component: str = Field(default=COMPONENT)
    work_dir: str = Field(default=DEFAULT_WORK_DIR, pattern=r"^/[A-Za-z0-9._/-]+$")
    autofix_before_check: bool = False
    autofix_command: str | None = None

    @property
    def shadow(self) -> bool:
        return self.mode is GateMode.SHADOW

    @property
    def autofix(self) -> bool:
        return self.autofix_before_check and bool(self.autofix_command)


# ---- parsing the gate's output -------------------------------------------------

_PRE_COMMIT_RE = re.compile(
    r"^(?P<name>\S.*?)\.{3,}(?:\([^)]*\))?(?P<status>Passed|Failed|Skipped)\s*$"
)
_GATE_CHECK_RE = re.compile(r"^gate-check\s+(?P<name>\S+)\s+(?P<status>pass|fail)\s*$")
_MAKE_RE = re.compile(r"make(?:\[\d+\])?: \*\*\* \[(?:[^\]]*?:\d+: )?(?P<name>[^\]]+)\] Error \d+")


@dataclass(frozen=True)
class CheckSummary:
    """Checks a gate run reported, when its output's format is recognised."""

    formats: tuple[str, ...]
    failed: tuple[str, ...]
    passed: tuple[str, ...]

    @property
    def parsed(self) -> bool:
        return bool(self.formats)


def _line_check(line: str) -> tuple[str, str, bool | None] | None:
    """(format, name, failed) for one recognised line; ``failed`` is None when skipped."""
    if match := _PRE_COMMIT_RE.match(line):
        skipped = match["status"] == "Skipped"
        return "pre-commit", match["name"].strip(), None if skipped else match["status"] == "Failed"
    if match := _GATE_CHECK_RE.match(line):
        return "gate-check", match["name"], match["status"] == "fail"
    if match := _MAKE_RE.search(line):
        return "make", match["name"].strip(), True
    return None


def parse_checks(output: str) -> CheckSummary:
    formats: list[str] = []
    failed: list[str] = []
    passed: list[str] = []
    for line in output.splitlines():
        found = _line_check(line.rstrip())
        if found is None:
            continue
        form, name, is_failure = found
        formats += [form] if form not in formats else []
        if is_failure is None:
            continue
        target = failed if is_failure else passed
        target += [name] if name not in target else []
    return CheckSummary(tuple(formats), tuple(failed), tuple(passed))


# ---- one gate run ----------------------------------------------------------------


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


@dataclass(frozen=True)
class GateRun:
    """One run of the commit gate, as read back from the container."""

    run: int
    after: str  # the session it followed, e.g. "stage_1_work"
    mode: GateMode
    exit_code: int | None  # the gate command's own exit code (the hook's, in commit mode)
    git_exit_code: int | None  # ``git commit`` (commit and shadow modes)
    output: str
    output_sha256: str
    output_bytes: int
    log: str
    repo_created: bool = False
    report_dir: str | None = None
    duration_sec: float | None = None  # host-measured, the whole gate script
    git: GitEvidence | None = None  # commit and shadow modes

    @property
    def timed_out(self) -> bool:
        """The in-container ``timeout`` ended the gate (``git commit`` in commit mode)."""
        code = self.exit_code if self.mode is GateMode.COMMAND else self.git_exit_code
        return code in TIMEOUT_EXIT_CODES

    @property
    def error(self) -> str | None:
        """Why the gate could not decide (infrastructure, not the agent's work), else ``None``."""
        if self.timed_out:
            return None
        if self.exit_code is None:
            return f"the gate command did not run (git commit exit {self.git_exit_code})"
        if self.exit_code in NOT_RUNNABLE_EXIT_CODES:
            return f"the gate command is not runnable (exit {self.exit_code})"
        if self.mode is not GateMode.COMMAND and self.exit_code == 0 and self.git_exit_code != 0:
            return f"git commit failed after the hook passed (exit {self.git_exit_code})"
        return None

    @property
    def passed(self) -> bool:
        if self.mode is GateMode.COMMAND:
            return self.exit_code == 0
        return self.exit_code == 0 and self.git_exit_code == 0

    def record(self) -> dict[str, Any]:
        checks = parse_checks(self.output)
        return {
            "run": self.run,
            "after": self.after,
            "mode": self.mode.value,
            "passed": self.passed,
            # Nothing is committed to the workspace in shadow mode: the commit went to the
            # shadow repository.
            "committed": self.passed if self.mode is GateMode.COMMIT else None,
            **({"shadow_committed": self.passed} if self.mode is GateMode.SHADOW else {}),
            "timed_out": self.timed_out,
            "error": self.error,
            "exit_code": self.exit_code,
            "git_exit_code": self.git_exit_code,
            "checks_parsed": checks.parsed,
            "check_formats": list(checks.formats),
            "checks_failed": list(checks.failed) if checks.parsed else None,
            "checks_passed": list(checks.passed) if checks.parsed else None,
            "output_sha256": self.output_sha256,
            "output_bytes": self.output_bytes,
            "log": self.log,
            "report_dir": self.report_dir,
            "duration_sec": self.duration_sec,
            "repo_created": self.repo_created,
            "git": self.git.record() if self.git is not None else None,
        }


def _int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _hook_script(command: str, status_path: str) -> str:
    return (
        "#!/bin/sh\n"
        "# guardrail-gate: the workspace's commit gate as the pre-commit hook.\n"
        f"bash -c {shlex.quote(command)} </dev/null\n"
        "rc=$?\n"
        f"printf '%s\\n' \"$rc\" > {shlex.quote(status_path)}\n"
        'exit "$rc"\n'
    )


def _git_command(shadow: bool, git_dir: PurePosixPath) -> str:
    base = f"git -c safe.directory='*' {GIT_IDENTITY} -c commit.gpgsign=false"
    if not shadow:
        return base
    # The work tree is the workspace (the current directory); git writes only git_dir.
    return f"{base} --git-dir={shlex.quote(str(git_dir))} --work-tree=."


def _commit_lines(options: GuardrailGateOptions, run: int, work: PurePosixPath) -> list[str]:
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


def report_dir_for(log: PurePosixPath) -> PurePosixPath:
    """The run's report directory: its log path without the suffix (``gate_run_<n>``)."""
    return log.with_suffix("")


def gate_script(options: GuardrailGateOptions, run: int, log: PurePosixPath) -> str:
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
    if options.mode is GateMode.COMMAND:
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


def read_gate_run(stdout: str, *, run: int, after: str, mode: GateMode, log: str) -> GateRun:
    head, _, output = stdout.partition(OUTPUT_SEPARATOR + "\n")
    values, git_log, git_status = _sections(head)
    git = None
    if "repo" in values:
        git = GitEvidence(
            repository=values["repo"],
            head_before=values.get("head_before") or None,
            head_after=values.get("head_after") or None,
            log=git_log,
            status=git_status,
        )
    return GateRun(
        run=run,
        after=after,
        mode=mode,
        exit_code=_int(values.get("rc")),
        git_exit_code=_int(values.get("git_rc")) if mode is not GateMode.COMMAND else None,
        output=output,
        output_sha256=values.get("sha256", ""),
        output_bytes=_int(values.get("bytes")) or 0,
        log=log,
        repo_created=values.get("repo_created") == "1",
        git=git,
    )


# ---- prompts ---------------------------------------------------------------------------


def fix_prompt(task: str, gate: GateRun, command: str, timeout_sec: int) -> str:
    """The fix session's prompt; the gate output is cut only to fit the argument limit."""
    event = GATE_EVENTS[gate.mode] % command
    if gate.timed_out:
        event += f" did not finish within {timeout_sec} seconds"
    else:
        event += f" failed with exit status {gate.exit_code}"
    tokens = {"TASK": task, "GATE_EVENT": event, "OUTPUT": ""}
    budget = PROMPT_ARG_BUDGET - quoted_size(render(FIX_PROMPT, tokens)) - 2
    output = _fit(gate.output or "(no output)", budget)
    return render(FIX_PROMPT, {**tokens, "OUTPUT": output})


def _fit(text: str, budget: int) -> str:
    """``text``, cut so its quoted size stays within ``budget`` bytes."""
    if quoted_size(text) <= budget:
        return text
    marker = "\n... gate output truncated to fit the harness prompt limit ...\n"
    used = quoted_size(marker)
    for index, char in enumerate(text):
        used += quoted_size(char)
        if used > budget:
            return text[:index] + marker
    return text  # pragma: no cover - the loop returns once over budget
