"""Harness token usage extraction."""

from __future__ import annotations

from raidar.harness.result_manifest import HarborArtifactRole, HarborResultManifest
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


def _usage_from_codex_log(manifest: HarborResultManifest) -> tuple[int, int, int] | None:
    entries = _read_jsonl_dicts(manifest.harness_artifact("codex.txt"))
    usages = [_extract_codex_usage(entry) for entry in entries]
    return next((usage for usage in reversed(usages) if usage), None)


def _usage_from_trial_result(manifest: HarborResultManifest) -> tuple[int, int, int] | None:
    payload = _load_json_dict(manifest.path(HarborArtifactRole.TRIAL_RESULT))
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


def _usage_from_claude_log(manifest: HarborResultManifest) -> tuple[int, int, int] | None:
    result_usage: tuple[int, int, int] | None = None
    message_usage_by_id: dict[str, tuple[int, int, int]] = {}
    candidate_paths = [
        *manifest.command_stdout_paths(),
        manifest.harness_artifact("claude-code.txt"),
    ]
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


def _usage_from_gemini_trajectory(
    manifest: HarborResultManifest,
) -> tuple[int, int, int] | None:
    payload = _load_json_dict(manifest.harness_artifact("gemini-cli.trajectory.json"))
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


def _usage_tuple_from_manifest(
    manifest: HarborResultManifest,
) -> tuple[int, int, int] | None:
    trial_usage = _usage_from_trial_result(manifest)
    if trial_usage:
        return trial_usage
    definition = manifest.definition
    parser = definition.usage_policy.parser
    if parser == "codex-jsonl":
        return _usage_from_codex_log(manifest)
    if parser == "claude-jsonl":
        return _usage_from_claude_log(manifest)
    if parser == "gemini-trajectory":
        return _usage_from_gemini_trajectory(manifest)
    if not definition.usage_policy.supported:
        return None
    raise ValueError(f"Unsupported usage parser for harness {definition.id}: {parser}")
