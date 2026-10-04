"""The gate run of ``guardrail-gate@1``: its options, script, outcome and fix prompt.

Pure and host-side: ``gate_script`` renders the bash the wrapper
(``raidar.components.guardrail_gate``) runs in the trial container,
``read_gate_run`` reads its result back, ``parse_checks`` finds the checks in the
gate's output and ``fix_prompt`` tells the agent what refused its work.
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

from raidar.components.review_loop import PROMPT_ARG_BUDGET, InnerAgent, render
from raidar.oracle.snapshot import SNAPSHOT_EXCLUDES

COMPONENT = "guardrail-gate@1"
VERSION = "1"
GATE_DIR_NAME = "guardrail-gate"
GATE_RECORD = "guardrail-gate.json"
SIGNAL = "gate_clean_at_step_end"
PROMPT_TEMPLATE_KWARG = "prompt_template_path"
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


class GateMode(StrEnum):
    COMMIT = "commit"
    COMMAND = "command"


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
    inner_agent: InnerAgent
    command: str = Field(min_length=1, description="The workspace's commit-gate command")
    mode: GateMode = GateMode.COMMIT
    max_rounds: int = Field(default=3, ge=1, le=5, description="Fix sessions at most")
    timeout_sec: int = Field(default=600, ge=10, le=3600)
    component: str = Field(default=COMPONENT)
    work_dir: str = Field(default=DEFAULT_WORK_DIR, pattern=r"^/[A-Za-z0-9._/-]+$")


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
class GateRun:
    """One run of the commit gate, as read back from the container."""

    run: int
    after: str  # the session it followed, e.g. "stage_1_work"
    mode: GateMode
    exit_code: int | None  # the gate command's own exit code (the hook's, in commit mode)
    git_exit_code: int | None  # ``git commit`` (commit mode only)
    output: str
    output_sha256: str
    output_bytes: int
    log: str
    repo_created: bool = False

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
        if self.mode is GateMode.COMMIT and self.exit_code == 0 and self.git_exit_code != 0:
            return f"git commit failed after the hook passed (exit {self.git_exit_code})"
        return None

    @property
    def passed(self) -> bool:
        if self.mode is GateMode.COMMIT:
            return self.exit_code == 0 and self.git_exit_code == 0
        return self.exit_code == 0

    def record(self) -> dict[str, Any]:
        checks = parse_checks(self.output)
        return {
            "run": self.run,
            "after": self.after,
            "mode": self.mode.value,
            "passed": self.passed,
            "committed": self.passed if self.mode is GateMode.COMMIT else None,
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
            "repo_created": self.repo_created,
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
        "# guardrail-gate@1: the workspace's commit gate as the pre-commit hook.\n"
        f"bash -c {shlex.quote(command)} </dev/null\n"
        "rc=$?\n"
        f"printf '%s\\n' \"$rc\" > {shlex.quote(status_path)}\n"
        'exit "$rc"\n'
    )


def gate_script(options: GuardrailGateOptions, run: int, log: PurePosixPath) -> str:
    """The bash run in the workspace: the gate, then key=value lines and the output."""
    work = PurePosixPath(options.work_dir)
    q_log, q_work = shlex.quote(str(log)), shlex.quote(str(work))
    timeout = f"timeout -k 10 {options.timeout_sec}"
    lines = [
        "set -u",
        f"log={q_log}; work={q_work}",
        'mkdir -p "$(dirname "$log")" "$work" && rm -f "$work/hook-rc"',
    ]
    if options.mode is GateMode.COMMIT:
        excludes = " ".join(shlex.quote(name) for name in (*SNAPSHOT_EXCLUDES, "*.pyc"))
        git = f"git -c safe.directory='*' {GIT_IDENTITY} -c commit.gpgsign=false"
        message = shlex.quote(f"guardrail-gate: commit after session (gate run {run})")
        # commit.sh runs in its own bash: every path in it is absolute, no shell variable.
        hooks = shlex.quote(str(work / "hooks"))
        commit = (
            f"{git} add -A && {git} -c core.hooksPath={hooks} "
            f"commit --allow-empty --no-edit -q -m {message}"
        )
        lines += [
            f'top="$({git} rev-parse --show-toplevel 2>/dev/null || true)"',
            'if [ "$top" != "$(pwd -P)" ]; then',
            f"  {git} -c init.defaultBranch=main init -q . && mkdir -p .git/info && "
            f"printf '%s\\n' {excludes} >> .git/info/exclude && echo repo_created=1",
            "fi",
            'mkdir -p "$work/hooks"',
            f"printf '%s' {shlex.quote(_hook_script(options.command, str(work / 'hook-rc')))} "
            '> "$work/hooks/pre-commit" && chmod 755 "$work/hooks/pre-commit"',
            f"printf '%s\\n' {shlex.quote(commit)} > \"$work/commit.sh\"",
            f'{timeout} bash "$work/commit.sh" > "$log" 2>&1 </dev/null',
            'echo "git_rc=$?"',
            'echo "rc=$(cat "$work/hook-rc" 2>/dev/null)"',
        ]
    else:
        lines += [
            f'{timeout} bash -c {shlex.quote(options.command)} > "$log" 2>&1 </dev/null',
            'echo "rc=$?"',
        ]
    half = MAX_OUTPUT_CHARS // 2
    lines += [
        'bytes="$(wc -c < "$log" | tr -d " ")"',
        'echo "bytes=$bytes"',
        'echo "sha256=$(sha256sum < "$log" | cut -d" " -f1)"',
        f"echo {OUTPUT_SEPARATOR}",
        f'if [ "$bytes" -le {MAX_OUTPUT_CHARS} ]; then cat "$log"; else head -c {half} "$log"; '
        "printf '\\n... gate output truncated (%s bytes in all) ...\\n' \"$bytes\"; "
        f'tail -c {half} "$log"; fi',
    ]
    return "\n".join(lines) + "\n"


def read_gate_run(stdout: str, *, run: int, after: str, mode: GateMode, log: str) -> GateRun:
    head, _, output = stdout.partition(OUTPUT_SEPARATOR + "\n")
    values = dict(line.split("=", 1) for line in head.splitlines() if "=" in line)
    return GateRun(
        run=run,
        after=after,
        mode=mode,
        exit_code=_int(values.get("rc")),
        git_exit_code=_int(values.get("git_rc")) if mode is GateMode.COMMIT else None,
        output=output,
        output_sha256=values.get("sha256", ""),
        output_bytes=_int(values.get("bytes")) or 0,
        log=log,
        repo_created=values.get("repo_created") == "1",
    )


# ---- prompts ---------------------------------------------------------------------------


def _quoted_size(text: str) -> int:
    """Bytes ``text`` takes inside a single-quoted shell word (' becomes '"'"')."""
    return len(text.encode()) + 4 * text.count("'")


def fix_prompt(task: str, gate: GateRun, command: str, timeout_sec: int) -> str:
    """The fix session's prompt; the gate output is cut only to fit the argument limit."""
    event = GATE_EVENTS[gate.mode] % command
    if gate.timed_out:
        event += f" did not finish within {timeout_sec} seconds"
    else:
        event += f" failed with exit status {gate.exit_code}"
    tokens = {"TASK": task, "GATE_EVENT": event, "OUTPUT": ""}
    budget = PROMPT_ARG_BUDGET - _quoted_size(render(FIX_PROMPT, tokens)) - 2
    output = _fit(gate.output or "(no output)", budget)
    return render(FIX_PROMPT, {**tokens, "OUTPUT": output})


def _fit(text: str, budget: int) -> str:
    """``text``, cut so its quoted size stays within ``budget`` bytes."""
    if _quoted_size(text) <= budget:
        return text
    marker = "\n... gate output truncated to fit the harness prompt limit ...\n"
    used = _quoted_size(marker)
    for index, char in enumerate(text):
        used += _quoted_size(char)
        if used > budget:
            return text[:index] + marker
    return text  # pragma: no cover - the loop returns once over budget
