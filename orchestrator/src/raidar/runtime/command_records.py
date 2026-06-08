"""Harness command record extraction and normalization."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from raidar.runtime.harbor_results import _load_json_dict
from raidar.runtime.harness_logs import _as_int, _extract_item_completed, _read_jsonl_dicts
from raidar.runtime.models import CommandRecord

BACKTICK_COMMAND_PATTERN = re.compile(r"`([^`\n]+)`")

SHELL_COMMAND_PREFIX_PATTERN = re.compile(r"^(?:bun|npm|npx|pnpm|yarn|biome|tsc|next|vitest)\b")

COMMAND_INTENT_PATTERN = re.compile(r"\b(i will|i'll|i am going to|i'm going to|i plan to)\b")

COMMAND_FAILURE_PATTERN = re.compile(r"\b(failed|failure|error|unable|did not|non-zero)\b")

COMMAND_EXECUTION_HINTS = (
    "verified with",
    "verified the changes with",
    "verifying the changes with",
    "by running",
    "ran `",
    "running `",
    "executed `",
    "all of which passed",
    "verification steps passed",
    "passed successfully",
)

VERIFIED_WITH_PATTERN = re.compile(r"\bverif(?:y|ied|ying)\b.*\bwith\b")

KEYWORD_COMMAND_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bun run typecheck", ("type-check", "typecheck", "type checking", "tsc")),
    ("bun run lint", ("lint", "linting")),
    ("bun run test:coverage", ("test:coverage", "coverage")),
    ("bun run test", ("run test", "test command", "testing", "tests")),
    ("bun run build", ("build", "compil", "next build")),
)

_HEREDOC_PATTERN = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")


@dataclass(frozen=True, slots=True)
class ClaudeToolUseAppendRequest:
    """Input for appending Claude tool-use command records."""

    payload: dict
    output: str
    records: list[CommandRecord]
    record_idx_by_tool_use_id: dict[str, int]
    include_git_commit: bool = False


def _normalize_command(command: str) -> str:
    commands = _normalized_shell_subcommands(command)
    if commands:
        return commands[0]
    return command.strip()


def _strip_shell_env_prefix(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
    env_assignments: dict[str, str] = {}
    idx = 0
    if idx < len(tokens) and tokens[idx] == "env":
        idx += 1
    while idx < len(tokens):
        token = tokens[idx]
        if "=" not in token or token.startswith("-"):
            break
        key, value = token.split("=", 1)
        if not key or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            break
        env_assignments[key] = value
        idx += 1
    return env_assignments, tokens[idx:]


def _git_command_tokens(command: str) -> tuple[dict[str, str], list[str]]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        lowered = command.lower().strip()
        if "git" not in lowered:
            return {}, []
        return {}, lowered.split()
    env_assignments, tokens = _strip_shell_env_prefix(tokens)
    if not tokens or tokens[0] != "git":
        return env_assignments, []
    idx = _git_subcommand_index(tokens)
    return env_assignments, tokens[idx:]


def _git_option_consumes_value(token: str) -> bool:
    return token in {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix"}


def _git_subcommand_index(tokens: list[str]) -> int:
    idx = 1
    while idx < len(tokens):
        token = tokens[idx]
        if _git_option_consumes_value(token):
            idx += 2
            continue
        if token.startswith("-c") and token != "-c":
            idx += 1
            continue
        if token == "--":
            return idx + 1
        if token.startswith("-"):
            idx += 1
            continue
        break
    return idx


def _is_git_commit_command(command: str) -> bool:
    _env_assignments, tokens = _git_command_tokens(command)
    return bool(tokens) and tokens[0] == "commit"


def _git_commit_uses_verification_bypass(command: str) -> bool:
    env_assignments, tokens = _git_command_tokens(command)
    if not tokens or tokens[0] != "commit":
        return False

    bypass_env_values = {
        "HUSKY": {"0"},
        "HUSKY_SKIP_HOOKS": {"1", "true", "yes"},
        "NO_VERIFY": {"1", "true", "yes"},
    }
    for key, truthy_values in bypass_env_values.items():
        value = env_assignments.get(key)
        if value is None:
            continue
        if value.lower() in truthy_values:
            return True

    lowered = command.lower()
    if "core.hookspath=/dev/null" in lowered:
        return True

    return "--no-verify" in tokens or "-n" in tokens


def _should_record_command(command: str, *, include_git_commit: bool) -> bool:
    if _looks_like_shell_command(command):
        return True
    return include_git_commit and _is_git_commit_command(command)


def _is_shell_separator(token: str) -> bool:
    return token in {"&&", "||", ";"}


def _normalized_joined_command(tokens: list[str]) -> str | None:
    if not tokens:
        return None
    return _normalize_verification_alias(shlex.join(tokens).strip())


def _split_token_by_shell_separators(token: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    idx = 0
    while idx < len(token):
        pair = token[idx : idx + 2]
        if pair in {"&&", "||"}:
            if current:
                parts.append("".join(current))
                current = []
            parts.append(pair)
            idx += 2
            continue
        if token[idx] == ";":
            if current:
                parts.append("".join(current))
                current = []
            parts.append(";")
            idx += 1
            continue
        current.append(token[idx])
        idx += 1
    if current:
        parts.append("".join(current))
    return [part for part in parts if part]


def _expand_shell_separator_tokens(tokens: list[str]) -> list[str]:
    expanded: list[str] = []
    for token in tokens:
        expanded.extend(_split_token_by_shell_separators(token))
    return expanded


def _split_normalized_subcommands(tokens: list[str]) -> list[str]:
    expanded_tokens = _expand_shell_separator_tokens(tokens)
    subcommands: list[str] = []
    current: list[str] = []
    for token in expanded_tokens:
        if _is_shell_separator(token):
            normalized = _normalized_joined_command(current)
            if normalized:
                subcommands.append(normalized)
            current = []
            continue
        current.append(token)

    normalized = _normalized_joined_command(current)
    if normalized:
        subcommands.append(normalized)
    return subcommands


def _shell_command_segments(command_text: str) -> list[str]:
    segments: list[str] = []
    lines = command_text.splitlines()
    idx = 0

    while idx < len(lines):
        line = lines[idx].strip()
        idx += 1
        if not line:
            continue

        heredoc_match = _HEREDOC_PATTERN.search(line)
        if not heredoc_match:
            segments.append(line)
            continue

        terminator = heredoc_match.group(1)
        heredoc_lines = [line]
        while idx < len(lines):
            heredoc_line = lines[idx]
            heredoc_lines.append(heredoc_line)
            idx += 1
            if heredoc_line.strip() == terminator:
                break
        segments.append("\n".join(heredoc_lines).strip())

    return segments


def _normalized_shell_subcommands(command: str) -> list[str]:
    command_text = _unwrap_shell_wrapper(command)
    if not command_text:
        return []
    subcommands: list[str] = []
    for segment in _shell_command_segments(command_text):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            subcommands.append(_normalize_verification_alias(segment))
            continue
        if not tokens:
            continue
        subcommands.extend(_split_normalized_subcommands(tokens))
    return subcommands


def _unwrap_shell_wrapper(command: str) -> str:
    command = command.strip()
    if not command:
        return command
    try:
        tokens = shlex.split(command)
    except ValueError:
        return command
    if "-lc" in tokens:
        idx = tokens.index("-lc")
        if idx + 1 < len(tokens):
            return tokens[idx + 1].strip()
    return command


def _normalize_verification_alias(command: str) -> str:
    lowered = command.lower().strip()
    if lowered in {"bun run typecheck", "npm run typecheck", "pnpm typecheck", "yarn typecheck"}:
        return "bun run typecheck"
    if lowered in {"bun run lint", "npm run lint", "pnpm lint", "yarn lint"}:
        return "bun run lint"
    if lowered in {"bun run build", "npm run build", "pnpm build", "yarn build"}:
        return "bun run build"
    if "tsc --noemit" in lowered:
        return "bun run typecheck"
    if "ultracite lint" in lowered or lowered.startswith("eslint "):
        return "bun run lint"
    return command


def _command_failed(item: dict) -> bool:
    status = item.get("status")
    exit_code = int(item.get("exit_code", 0) or 0)
    return status == "failed" or exit_code != 0


def _command_output(item: dict) -> str:
    aggregated = item.get("aggregated_output")
    if isinstance(aggregated, str) and aggregated:
        return aggregated
    stdout = str(item.get("stdout", "") or "")
    stderr = str(item.get("stderr", "") or "")
    return "\n".join(part for part in (stdout, stderr) if part)


def _command_records(
    entries: list[dict], *, include_git_commit: bool = False
) -> list[CommandRecord]:
    records: list[CommandRecord] = []
    for entry in entries:
        item = _extract_item_completed(entry)
        if not item or item.get("type") != "command_execution":
            continue
        failed = _command_failed(item)
        exit_code = _as_int(item.get("exit_code"))
        output = _command_output(item)
        commands = _normalized_shell_subcommands(str(item.get("command", "")))
        for command in commands:
            if not _should_record_command(command, include_git_commit=include_git_commit):
                continue
            records.append(
                CommandRecord(
                    command=command,
                    failed=failed,
                    output=output,
                    exit_code=exit_code,
                )
            )
    return records


def _command_records_for_harness(
    trial_dir: Path, harness: str, *, include_git_commit: bool = False
) -> list[CommandRecord]:
    if harness == "codex-cli":
        return _command_records(
            _read_jsonl_dicts(trial_dir / "agent" / "codex.txt"),
            include_git_commit=include_git_commit,
        )
    if harness == "claude-code":
        return _command_records_from_claude_stdout(trial_dir, include_git_commit=include_git_commit)
    if harness == "gemini":
        stdout_records = _command_records_from_harness_stdout(
            trial_dir,
            additional_stdout_files=("gemini-cli.txt",),
            include_git_commit=include_git_commit,
        )
        if stdout_records:
            return stdout_records
        return _command_records_from_gemini_trajectory(
            trial_dir, include_git_commit=include_git_commit
        )
    if harness in {"cursor", "copilot", "pi"}:
        return _command_records_from_harness_stdout(
            trial_dir, include_git_commit=include_git_commit
        )
    raise ValueError(f"Unsupported harness for command extraction: {harness}")


def _command_records_from_harness_stdout(
    trial_dir: Path,
    *,
    additional_stdout_files: tuple[str, ...] = (),
    include_git_commit: bool = False,
) -> list[CommandRecord]:
    harness_dir = trial_dir / "agent"
    if not harness_dir.exists():
        return []
    records: list[CommandRecord] = []
    stdout_paths: list[Path] = sorted(harness_dir.glob("command-*/stdout.txt"))
    stdout_paths.extend(harness_dir / name for name in additional_stdout_files)
    for stdout_path in stdout_paths:
        if not stdout_path.exists():
            continue
        records.extend(
            _command_records_from_stdout(stdout_path, include_git_commit=include_git_commit)
        )
    return records


def _command_records_from_claude_stdout(
    trial_dir: Path, *, include_git_commit: bool = False
) -> list[CommandRecord]:
    agent_dir = trial_dir / "agent"
    if not agent_dir.exists():
        return []
    records: list[CommandRecord] = []
    stdout_paths: list[Path] = sorted(agent_dir.glob("command-*/stdout.txt"))
    stdout_paths.append(agent_dir / "claude-code.txt")
    for stdout_path in stdout_paths:
        if not stdout_path.exists():
            continue
        records.extend(
            _command_records_from_claude_stdout_file(
                stdout_path, include_git_commit=include_git_commit
            )
        )
    return records


def _command_records_from_claude_stdout_file(
    stdout_path: Path, *, include_git_commit: bool = False
) -> list[CommandRecord]:
    try:
        lines = stdout_path.read_text(errors="ignore").splitlines()
    except OSError:
        return []

    records: list[CommandRecord] = []
    record_idx_by_tool_use_id: dict[str, int] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        payload = _line_as_json_dict(stripped)
        if payload is None:
            records.extend(
                _command_records_from_line(stripped, include_git_commit=include_git_commit)
            )
            continue
        _append_claude_tool_use_records(
            ClaudeToolUseAppendRequest(
                payload=payload,
                output=stripped,
                records=records,
                record_idx_by_tool_use_id=record_idx_by_tool_use_id,
                include_git_commit=include_git_commit,
            )
        )
        _mark_claude_failed_tool_records(
            payload=payload,
            records=records,
            record_idx_by_tool_use_id=record_idx_by_tool_use_id,
        )
    return records


def _append_claude_tool_use_records(request: ClaudeToolUseAppendRequest) -> None:
    for tool_use_id, command in _claude_bash_tool_use_commands(request.payload):
        matched_indexes: list[int] = []
        for normalized in _normalized_shell_subcommands(command):
            if not _should_record_command(
                normalized,
                include_git_commit=request.include_git_commit,
            ):
                continue
            matched_indexes.append(len(request.records))
            request.records.append(
                CommandRecord(
                    command=normalized,
                    failed=False,
                    output=request.output,
                )
            )
        if matched_indexes:
            request.record_idx_by_tool_use_id[tool_use_id] = matched_indexes[0]


def _mark_claude_failed_tool_records(
    *,
    payload: dict,
    records: list[CommandRecord],
    record_idx_by_tool_use_id: dict[str, int],
) -> None:
    for tool_use_id in _claude_failed_tool_result_ids(payload):
        idx = record_idx_by_tool_use_id.get(tool_use_id)
        if idx is None:
            continue
        original = records[idx]
        records[idx] = CommandRecord(
            command=original.command,
            failed=True,
            output=original.output,
        )


def _line_as_json_dict(line: str) -> dict | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _claude_bash_tool_use_commands(payload: dict) -> list[tuple[str, str]]:
    content = _claude_message_content(payload)
    if content is None:
        return []
    commands: list[tuple[str, str]] = []
    for part in content:
        command = _claude_bash_tool_use_command(part)
        if command is not None:
            commands.append(command)
    return commands


def _claude_message_content(payload: dict) -> list | None:
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, list) else None


def _claude_bash_tool_use_command(part: object) -> tuple[str, str] | None:
    if not isinstance(part, dict):
        return None
    if part.get("type") != "tool_use" or part.get("name") != "Bash":
        return None
    tool_input = part.get("input")
    if not isinstance(tool_input, dict):
        return None
    tool_use_id = str(part.get("id", "")).strip()
    command = str(tool_input.get("command", "")).strip()
    if not tool_use_id or not command:
        return None
    return tool_use_id, command


def _claude_failed_tool_result_ids(payload: dict) -> list[str]:
    content = _claude_message_content(payload)
    if content is None:
        return []
    failed_tool_ids: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") != "tool_result":
            continue
        if not bool(part.get("is_error", False)):
            continue
        tool_use_id = str(part.get("tool_use_id", "")).strip()
        if tool_use_id:
            failed_tool_ids.append(tool_use_id)
    return failed_tool_ids


def _command_records_from_stdout(
    stdout_path: Path, *, include_git_commit: bool = False
) -> list[CommandRecord]:
    try:
        lines = stdout_path.read_text(errors="ignore").splitlines()
    except OSError:
        return []
    records: list[CommandRecord] = []
    for line in lines:
        records.extend(_command_records_from_line(line, include_git_commit=include_git_commit))
    return records


def _command_records_from_line(
    line: str, *, include_git_commit: bool = False
) -> list[CommandRecord]:
    stripped = line.strip()
    if not stripped:
        return []
    if stripped.startswith("$ "):
        return _prompt_command_record(
            stripped[2:],
            output=stripped,
            include_git_commit=include_git_commit,
        )
    if _line_is_command_intent(stripped):
        return []
    if not _line_reports_command_execution(stripped):
        return []
    quoted_records = _quoted_command_records(stripped, include_git_commit=include_git_commit)
    if quoted_records:
        return quoted_records
    return _keyword_command_records(stripped, include_git_commit=include_git_commit)


def _prompt_command_record(
    command_text: str, *, output: str, include_git_commit: bool = False
) -> list[CommandRecord]:
    commands = _normalized_shell_subcommands(command_text)
    return [
        CommandRecord(command=command, failed=False, output=output)
        for command in commands
        if _should_record_command(command, include_git_commit=include_git_commit)
    ]


def _quoted_command_records(line: str, *, include_git_commit: bool = False) -> list[CommandRecord]:
    commands: list[str] = []
    for match in BACKTICK_COMMAND_PATTERN.findall(line):
        commands.extend(_normalized_shell_subcommands(match))
    commands = [
        command
        for command in commands
        if _should_record_command(command, include_git_commit=include_git_commit)
    ]
    if not commands:
        return []
    failed = _line_reports_command_failure(line)
    return [CommandRecord(command=command, failed=failed, output=line) for command in commands]


def _command_records_from_gemini_trajectory(
    trial_dir: Path, *, include_git_commit: bool = False
) -> list[CommandRecord]:
    payload = _load_json_dict(trial_dir / "agent" / "gemini-cli.trajectory.json")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return []
    records: list[CommandRecord] = []
    for message in messages:
        records.extend(
            _command_records_from_gemini_message(message, include_git_commit=include_git_commit)
        )
    return records


def _command_records_from_gemini_message(
    message: dict, *, include_git_commit: bool = False
) -> list[CommandRecord]:
    if not isinstance(message, dict):
        return []
    tool_calls = message.get("toolCalls")
    if not isinstance(tool_calls, list):
        return []
    records: list[CommandRecord] = []
    for tool_call in tool_calls:
        records.extend(
            _command_records_from_gemini_tool_call(tool_call, include_git_commit=include_git_commit)
        )
    return records


def _command_records_from_gemini_tool_call(
    tool_call: dict, *, include_git_commit: bool = False
) -> list[CommandRecord]:
    if not isinstance(tool_call, dict):
        return []
    if tool_call.get("name") != "run_shell_command":
        return []
    args = tool_call.get("args")
    if not isinstance(args, dict):
        return []
    command_text = str(args.get("command", "")).strip()
    if not command_text:
        return []
    failed = str(tool_call.get("status", "")).strip().lower() == "error"
    commands = _normalized_shell_subcommands(command_text)
    return [
        CommandRecord(
            command=command,
            failed=failed,
            output=command_text,
        )
        for command in commands
        if _should_record_command(command, include_git_commit=include_git_commit)
    ]


def _keyword_command_records(line: str, *, include_git_commit: bool = False) -> list[CommandRecord]:
    lowered = f" {line.lower()} "
    commands: list[str] = []
    for command, keywords in KEYWORD_COMMAND_PATTERNS:
        if any(keyword in lowered for keyword in keywords):
            commands.append(command)
    if include_git_commit and " git " in lowered and " commit " in lowered:
        commands.append("git commit")
    deduped = list(dict.fromkeys(commands))
    if not deduped:
        return []
    failed = _line_reports_command_failure(line)
    return [CommandRecord(command=command, failed=failed, output=line) for command in deduped]


def _looks_like_shell_command(command: str) -> bool:
    return bool(command and SHELL_COMMAND_PREFIX_PATTERN.match(command))


def _line_is_command_intent(line: str) -> bool:
    return bool(COMMAND_INTENT_PATTERN.search(line.lower()))


def _line_reports_command_execution(line: str) -> bool:
    lowered = line.lower()
    if any(hint in lowered for hint in COMMAND_EXECUTION_HINTS):
        return True
    return bool(VERIFIED_WITH_PATTERN.search(lowered))


def _line_reports_command_failure(line: str) -> bool:
    return bool(COMMAND_FAILURE_PATTERN.search(line.lower()))
