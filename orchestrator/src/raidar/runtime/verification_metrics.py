"""Verification command and failure aggregation helpers."""

from __future__ import annotations

import shlex

from raidar.runtime.command_records import (
    _git_commit_uses_verification_bypass,
    _is_git_commit_command,
)
from raidar.runtime.models import CommandRecord
from raidar.schemas.events import GateEvent
from raidar.schemas.scenario import ScenarioDefinition

PROCESS_FAILURE_MISSING_COMMAND_SNIPPETS: tuple[str, ...] = (
    "command not found",
    "not found",
    "no such file or directory",
    "enoent",
)

PROCESS_FAILURE_PERMISSION_SNIPPETS: tuple[str, ...] = (
    "permission denied",
    "operation not permitted",
    "eacces",
)

PROCESS_FAILURE_TIMEOUT_SNIPPETS: tuple[str, ...] = (
    "timed out",
    "timeout",
    "time limit exceeded",
)

PROCESS_FAILURE_RESOURCE_SNIPPETS: tuple[str, ...] = (
    "out of memory",
    "cannot allocate memory",
    "no space left on device",
    "enospc",
    "killed",
)

PROCESS_FAILURE_INVOCATION_SNIPPETS: tuple[str, ...] = (
    "exec format error",
    "bad substitution",
    "syntax error near unexpected token",
    "invalid option",
)


def _verification_command_strings(task: ScenarioDefinition) -> list[str]:
    patterns = [shlex.join(gate.command) for gate in task.verification.gates]
    patterns.extend(shlex.join(command) for command in task.verification.required_commands)
    deduped = list(dict.fromkeys(patterns))
    return [pattern for pattern in deduped if pattern]


def _command_matches_pattern(command: str, patterns: list[str]) -> str | None:
    for pattern in sorted(patterns, key=len, reverse=True):
        if command == pattern or command.startswith(f"{pattern} "):
            return pattern
    return None


def _verification_attempts(
    records: list[CommandRecord],
    verification_patterns: list[str],
) -> tuple[dict[str, int], dict[str, int]]:
    attempts_by_pattern: dict[str, int] = dict.fromkeys(verification_patterns, 0)
    failures_by_pattern: dict[str, int] = dict.fromkeys(verification_patterns, 0)
    for record in records:
        matched = _command_matches_pattern(record.command, verification_patterns)
        if not matched:
            continue
        attempts_by_pattern[matched] += 1
        if record.failed:
            failures_by_pattern[matched] += 1
    return attempts_by_pattern, failures_by_pattern


def _observed_verification_attempts(
    gate_history: list[GateEvent], verification_patterns: list[str]
) -> dict[str, int]:
    attempts_by_pattern: dict[str, int] = dict.fromkeys(verification_patterns, 0)
    for event in gate_history:
        matched = _command_matches_pattern(event.command, verification_patterns)
        if not matched:
            continue
        attempts_by_pattern[matched] += 1
    return attempts_by_pattern


def _first_pass_status(
    records: list[CommandRecord], verification_patterns: list[str]
) -> dict[str, str]:
    status: dict[str, str] = dict.fromkeys(verification_patterns, "missing")
    for record in records:
        matched = _command_matches_pattern(record.command, verification_patterns)
        if not matched or status[matched] != "missing":
            continue
        status[matched] = "fail" if record.failed else "pass"
    return status


def _contains_snippet(text: str, snippets: tuple[str, ...]) -> bool:
    return any(snippet in text for snippet in snippets)


def _failure_category(record: CommandRecord) -> str | None:
    combined = f"{record.command}\n{record.output}".lower()
    if record.exit_code in {126, 127} or _contains_snippet(
        combined, PROCESS_FAILURE_MISSING_COMMAND_SNIPPETS
    ):
        return "missing_command"
    if _contains_snippet(combined, PROCESS_FAILURE_PERMISSION_SNIPPETS):
        return "permission_denied"
    if _contains_snippet(combined, PROCESS_FAILURE_TIMEOUT_SNIPPETS):
        return "command_timeout"
    if _contains_snippet(combined, PROCESS_FAILURE_RESOURCE_SNIPPETS):
        return "resource_exhausted"
    if _contains_snippet(combined, PROCESS_FAILURE_INVOCATION_SNIPPETS):
        return "command_invocation_error"
    return None


def _failure_category_counts(records: list[CommandRecord]) -> dict[str, int]:
    categories: dict[str, int] = {}
    for record in records:
        if not record.failed:
            continue
        category = _failure_category(record)
        if category is None:
            continue
        categories[category] = categories.get(category, 0) + 1
    return categories


def _count_failed_commands(records: list[CommandRecord]) -> int:
    return sum(1 for record in records if record.failed)


def _count_process_failed_commands(failure_categories: dict[str, int]) -> int:
    return sum(failure_categories.values())


def _count_repeated_failures(failures_by_pattern: dict[str, int]) -> int:
    return sum(max(0, count - 1) for count in failures_by_pattern.values())


def _count_executed_required(attempts_by_pattern: dict[str, int]) -> int:
    return sum(1 for count in attempts_by_pattern.values() if count > 0)


def _git_commit_bypass_commands(records: list[CommandRecord]) -> list[str]:
    commands: list[str] = []
    for record in records:
        if not _is_git_commit_command(record.command):
            continue
        if not _git_commit_uses_verification_bypass(record.command):
            continue
        commands.append(record.command)
    return list(dict.fromkeys(commands))


def _first_pass_counts(first_pass_status: dict[str, str]) -> tuple[int, int, int]:
    passed = sum(1 for status in first_pass_status.values() if status == "pass")
    failed = sum(1 for status in first_pass_status.values() if status == "fail")
    missing = sum(1 for status in first_pass_status.values() if status == "missing")
    return passed, failed, missing
