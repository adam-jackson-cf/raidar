"""Harness token usage extraction."""

from __future__ import annotations

from pathlib import Path

from raidar.runtime.harbor_results import _load_json_dict
from raidar.runtime.harness_logs import _as_int, _read_jsonl_dicts


def _usage_tuple_from_payload(
    usage: dict | None,
    *,
    input_key: str = "input_tokens",
    cached_keys: tuple[str, ...] = ("cached_input_tokens",),
    output_key: str = "output_tokens",
) -> tuple[int, int, int] | None:
    if not isinstance(usage, dict):
        return None
    input_tokens = _as_int(usage.get(input_key))
    output_tokens = _as_int(usage.get(output_key))
    if input_tokens is None or output_tokens is None:
        return None
    cached_input_tokens = 0
    for key in cached_keys:
        candidate = _as_int(usage.get(key))
        if candidate is not None:
            cached_input_tokens = candidate
            break
    return input_tokens, cached_input_tokens, output_tokens


def _extract_codex_usage(entry: dict) -> tuple[int, int, int] | None:
    if entry.get("type") != "turn.completed":
        return None
    return _usage_tuple_from_payload(entry.get("usage"))


def _usage_from_codex_log(trial_dir: Path) -> tuple[int, int, int] | None:
    entries = _read_jsonl_dicts(trial_dir / "agent" / "codex.txt")
    usages = [_extract_codex_usage(entry) for entry in entries]
    return next((usage for usage in reversed(usages) if usage), None)


def _usage_from_trial_result(trial_dir: Path) -> tuple[int, int, int] | None:
    payload = _load_json_dict(trial_dir / "result.json")
    agent_result = payload.get("agent_result")
    if not isinstance(agent_result, dict):
        return None
    input_tokens = _as_int(agent_result.get("n_input_tokens"))
    output_tokens = _as_int(agent_result.get("n_output_tokens"))
    cached_tokens = _as_int(agent_result.get("n_cache_tokens")) or 0
    if input_tokens is None or output_tokens is None:
        return None
    return input_tokens, cached_tokens, output_tokens


def _record_claude_usage(
    entry: dict,
    *,
    message_usage_by_id: dict[str, tuple[int, int, int]],
    result_usage: tuple[int, int, int] | None,
) -> tuple[int, int, int] | None:
    if entry.get("type") == "result":
        usage_tuple = _usage_tuple_from_payload(
            entry.get("usage"),
            cached_keys=("cached_input_tokens", "cache_read_input_tokens"),
        )
        if usage_tuple:
            result_usage = usage_tuple
    message = entry.get("message")
    if not isinstance(message, dict):
        return result_usage
    message_id = str(message.get("id", "")).strip()
    usage_tuple = _usage_tuple_from_payload(
        message.get("usage"),
        cached_keys=("cached_input_tokens", "cache_read_input_tokens"),
    )
    if message_id and usage_tuple:
        message_usage_by_id[message_id] = usage_tuple
    return result_usage


def _usage_from_claude_log(trial_dir: Path) -> tuple[int, int, int] | None:
    result_usage: tuple[int, int, int] | None = None
    message_usage_by_id: dict[str, tuple[int, int, int]] = {}
    agent_dir = trial_dir / "agent"
    candidate_paths = sorted(agent_dir.glob("command-*/stdout.txt"))
    candidate_paths.append(agent_dir / "claude-code.txt")
    for path in candidate_paths:
        for entry in _read_jsonl_dicts(path):
            result_usage = _record_claude_usage(
                entry,
                message_usage_by_id=message_usage_by_id,
                result_usage=result_usage,
            )

    if result_usage:
        return result_usage
    if not message_usage_by_id:
        return None
    input_tokens = sum(usage[0] for usage in message_usage_by_id.values())
    cached_tokens = sum(usage[1] for usage in message_usage_by_id.values())
    output_tokens = sum(usage[2] for usage in message_usage_by_id.values())
    return input_tokens, cached_tokens, output_tokens


def _usage_from_gemini_trajectory(trial_dir: Path) -> tuple[int, int, int] | None:
    payload = _load_json_dict(trial_dir / "agent" / "gemini-cli.trajectory.json")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return None
    input_tokens = 0
    cached_tokens = 0
    output_tokens = 0
    found = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        token_block = message.get("tokens")
        if not isinstance(token_block, dict):
            continue
        msg_input = _as_int(token_block.get("input"))
        msg_cached = _as_int(token_block.get("cached")) or 0
        msg_output = _as_int(token_block.get("output"))
        if msg_input is None or msg_output is None:
            continue
        input_tokens += msg_input
        cached_tokens += msg_cached
        output_tokens += msg_output
        found = True
    if not found:
        return None
    return input_tokens, cached_tokens, output_tokens


def _usage_tuple_for_harness(trial_dir: Path, harness: str) -> tuple[int, int, int] | None:
    trial_usage = _usage_from_trial_result(trial_dir)
    if trial_usage:
        return trial_usage
    if harness == "codex-cli":
        return _usage_from_codex_log(trial_dir)
    if harness == "claude-code":
        return _usage_from_claude_log(trial_dir)
    if harness == "gemini":
        return _usage_from_gemini_trajectory(trial_dir)
    if harness in {"cursor", "copilot", "pi"}:
        return None
    raise ValueError(f"Unsupported harness for usage extraction: {harness}")
