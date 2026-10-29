"""The native agent protocol for both harnesses: accounting, replies, isolation and audit."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from harbor.models.agent.context import AgentContext

from raidar.subject.agent.config import Harness
from raidar.subject.agent.native import adapter_class_for_harness
from raidar.subject.agent.protocol import IsolationError, Tokens
from tests.native_support import native_agent

AUDIT_KEYS = {
    "harness",
    "version",
    "records",
    "sources",
    "skill_roots",
    "instructions",
    "messages",
    "violations",
    "unmeasured",
}
INIT = {
    "type": "system",
    "subtype": "init",
    "claude_code_version": "2.1.285",
    "skills": [],
    "plugins": [],
    "mcp_servers": [],
}
ONE = (1, 0, 0, 1)


def _context(input_: int | None, cached: int, output: int, cost: float | None) -> AgentContext:
    return AgentContext(
        n_input_tokens=input_, n_cache_tokens=cached, n_output_tokens=output, cost_usd=cost
    )


def _codex(logs_dir: Path) -> Any:
    return native_agent(Harness.CODEX_CLI, logs_dir)


def _claude(logs_dir: Path, **kwargs: Any) -> Any:
    isolation = adapter_class_for_harness(Harness.CLAUDE_CODE).isolation_kwargs(SimpleNamespace())
    return native_agent(Harness.CLAUDE_CODE, logs_dir, **isolation, **kwargs)


def _usage(turn_dir: Path) -> Tokens | None:
    """The turn's own usage as the claude agent leaves it in the turn's context."""
    context = AgentContext()
    _claude(turn_dir).populate_context_post_run(context)
    if context.n_input_tokens is None:
        return None
    return Tokens(
        context.n_input_tokens,
        context.n_cache_tokens or 0,
        context.n_output_tokens or 0,
        context.cost_usd,
    )


def _lines(*events: dict[str, Any]) -> str:
    return "\n".join(json.dumps(event) for event in events) + "\n"


def _result(text: str, *, tokens: tuple[int, int, int, int], cost: float | None) -> dict[str, Any]:
    fresh, read, written, output = tokens
    envelope: dict[str, Any] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": text,
        "usage": {
            "input_tokens": fresh,
            "cache_read_input_tokens": read,
            "cache_creation_input_tokens": written,
            "output_tokens": output,
        },
    }
    if cost is not None:
        # ``total_cost_usd`` is the CLI's own table: it must never be read.
        envelope["total_cost_usd"] = cost
        envelope["modelUsage"] = {
            "claude-x": {
                "inputTokens": fresh,
                "outputTokens": output,
                "cacheReadInputTokens": read,
                "cacheCreationInputTokens": written,
            }
        }
    return envelope


# --- accounting -----------------------------------------------------------------------------


def test_codex_session_totals_are_subtracted_and_claude_turns_are_added(tmp_path: Path) -> None:
    codex, claude = _codex(tmp_path / "x"), _claude(tmp_path / "y")
    first, second = _context(100, 40, 10, 0.5), _context(250, 90, 30, 1.25)

    codex_first = codex.accounting(first, None)
    codex_second = codex.accounting(second, codex_first[1])
    claude_first = claude.accounting(first, None)
    claude_second = claude.accounting(second, claude_first[1])

    assert codex_second == (Tokens(150, 50, 20, 0.75), Tokens(250, 90, 30, 1.25))
    assert claude_second == (Tokens(250, 90, 30, 1.25), Tokens(350, 130, 40, 1.75))


@pytest.mark.parametrize("make", [_codex, _claude], ids=["codex", "claude"])
def test_usage_the_harness_did_not_record_is_unknown_not_zero(tmp_path: Path, make: Any) -> None:
    agent = make(tmp_path)
    known = Tokens(10, 5, 2, 0.1)

    assert agent.accounting(_context(None, 0, 0, None), known) == (None, None)


def test_a_claude_turn_is_metered_from_its_own_stream(tmp_path: Path) -> None:
    first, second = tmp_path / "build-1", tmp_path / "build-2"
    first.mkdir()
    second.mkdir()
    (first / "claude-code.txt").write_text(
        _lines(INIT, _result("one", tokens=(10, 5, 3, 7), cost=0.5))
    )
    (second / "claude-code.txt").write_text(
        "not json\n"
        + _lines(
            INIT,
            _result("two", tokens=(1, 20, 0, 4), cost=0.25),
            _result("x", tokens=ONE, cost=0.25),
        )
    )

    # Priced per token from the one table (stubbed 0.01 USD per prompt+completion token),
    # not from the self-reported total_cost_usd (0.5, 0.25).
    first_usage, second_usage = _usage(first), _usage(second)
    assert first_usage is not None
    assert second_usage is not None
    assert (first_usage.input, first_usage.cached, first_usage.output) == (18, 5, 7)
    assert first_usage.cost_usd == pytest.approx(0.25)
    assert (second_usage.input, second_usage.cached, second_usage.output) == (22, 20, 5)
    assert second_usage.cost_usd == pytest.approx(0.27)  # 25 + 2 tokens
    assert _usage(tmp_path / "missing") is None


@pytest.fixture(autouse=True)
def _stub_price_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared table, stubbed to 0.01 USD per prompt+completion token."""

    def price(model: str, *, prompt: int, completion: int, **cache: int) -> float:
        return 0.01 * (prompt + completion)

    monkeypatch.setattr("raidar.subject.agent.adapters.claude_audit.price_tokens", price)


def test_a_model_the_table_does_not_price_makes_the_cost_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unpriced = "raidar.subject.agent.adapters.claude_audit.price_tokens"
    monkeypatch.setattr(unpriced, lambda *args, **kwargs: None)
    (tmp_path / "claude-code.txt").write_text(
        _lines(INIT, _result("one", tokens=(10, 5, 3, 7), cost=0.5))
    )

    assert _usage(tmp_path) == Tokens(18, 5, 7, None)  # never the self-reported 0.5


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (None, None),
        ({}, None),
        ({"input_tokens": 3}, None),
        ({"output_tokens": 2}, None),
        ({"input_tokens": 0, "output_tokens": 0}, Tokens(0, 0, 0, None)),
    ],
)
def test_claude_usage_distinguishes_unmeasured_from_zero(
    tmp_path: Path, usage: dict[str, int] | None, expected: Tokens | None
) -> None:
    (tmp_path / "claude-code.txt").write_text(_lines({"type": "result", "usage": usage}))

    assert _usage(tmp_path) == expected


# --- final text -----------------------------------------------------------------------------


def test_claude_last_message_is_the_turns_own_successful_result(tmp_path: Path) -> None:
    agent = _claude(tmp_path)
    done, failed, silent = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for folder in (done, failed, silent):
        folder.mkdir()
    final = _result("final {}", tokens=ONE, cost=None)
    (done / "claude-code.txt").write_text(_lines(INIT, final))
    error = {**_result("", tokens=ONE, cost=None), "is_error": True, "subtype": "error"}
    (failed / "claude-code.txt").write_text(_lines(INIT, error))

    assert agent.last_message(done) == "final {}"
    assert agent.last_message(failed) is None
    assert agent.last_message(silent) is None


def test_a_resumed_codex_turn_without_a_new_message_has_none(tmp_path: Path) -> None:
    agent = _codex(tmp_path)
    first, resumed = tmp_path / "build-1", tmp_path / "fix-2"
    first.mkdir()
    resumed.mkdir()
    reply = {"type": "item.completed", "item": {"type": "agent_message", "text": "first reply"}}
    (first / "codex.txt").write_text(_lines({"type": "turn.started"}, reply))
    (resumed / "codex.txt").write_text(_lines({"type": "turn.started"}, {"type": "turn.completed"}))
    store = resumed / "sessions" / "2026" / "10" / "27"
    store.mkdir(parents=True)
    (store / "rollout-2026-10-27T10-00-00-abc.jsonl").write_text(
        _lines({"payload": {"type": "message", "role": "assistant", "content": [{"text": "x"}]}})
    )

    assert agent.last_message(first) == "first reply"
    assert agent.last_message(resumed) is None


# --- isolation ------------------------------------------------------------------------------


class ClaudeEnvironment:
    """Records every command; answers the managed-settings check and the probe as told."""

    default_user = "agent"
    capabilities = SimpleNamespace(mounted=True)

    def __init__(self, *, managed: str = "", host_skill: str = "") -> None:
        self.managed, self.host_skill = managed, host_skill
        self.calls: list[dict[str, Any]] = []

    async def exec(self, command: str, **kwargs: Any) -> Any:
        self.calls.append({"command": command, **kwargs})
        stdout = ""
        if "/etc/claude-code/managed-settings.json" in command:
            stdout = self.managed
        elif "SKILL.md" in command:
            stdout = f"user=agent\npath=/usr/bin\ntmpdir=\n{self.host_skill}"
        return SimpleNamespace(return_code=0, stdout=stdout, stderr="")

    def commands(self, needle: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if needle in call["command"]]


def _turn(agent: Any, env: ClaudeEnvironment, *, resume: bool) -> None:
    send = agent.resume if resume else agent.run
    asyncio.run(send("do the task", env, AgentContext()))


@pytest.mark.parametrize("resume", [False, True], ids=["first-turn", "resumed-turn"])
def test_every_claude_command_is_isolated(tmp_path: Path, resume: bool) -> None:
    env = ClaudeEnvironment()
    agent = _claude(tmp_path)

    _turn(agent, env, resume=resume)

    (run,) = env.commands("claude --verbose")
    assert "--setting-sources user,project,local" in run["command"]
    assert "--strict-mcp-config" in run["command"]
    assert ("--continue" in run["command"]) is resume
    assert run["env"]["DISABLE_AUTOUPDATER"] == "1"
    assert agent.harness_isolation is not None
    assert agent.harness_isolation["flags"] == [
        "--setting-sources",
        "user,project,local",
        "--strict-mcp-config",
    ]
    assert agent.process_facts() == {"user": "agent", "path": "/usr/bin", "tmpdir": None}


def test_a_session_starts_from_a_fresh_home_and_a_resume_keeps_its_transcripts(
    tmp_path: Path,
) -> None:
    first_env, resumed_env = ClaudeEnvironment(), ClaudeEnvironment()

    _turn(_claude(tmp_path / "a"), first_env, resume=False)
    _turn(_claude(tmp_path / "b"), resumed_env, resume=True)

    (first,) = first_env.commands("/etc/claude-code/managed-settings.json")
    (resumed,) = resumed_env.commands("/etc/claude-code/managed-settings.json")
    assert first["command"].endswith("rm -rf /logs/agent/sessions")
    assert not resumed["command"].endswith("rm -rf /logs/agent/sessions")
    assert "rm -rf /logs/agent/sessions/skills" in resumed["command"]
    assert "/logs/agent/sessions/projects" not in resumed["command"]
    assert first["user"] == resumed["user"] == "root"


def test_managed_claude_settings_are_refused_before_any_claude_command(tmp_path: Path) -> None:
    env = ClaudeEnvironment(managed="/etc/claude-code/managed-settings.json\n")

    with pytest.raises(IsolationError, match="managed-settings"):
        _turn(_claude(tmp_path), env, resume=False)

    assert env.commands("claude --verbose") == []


def test_container_host_skills_would_be_copied_into_the_home_and_are_refused(
    tmp_path: Path,
) -> None:
    env = ClaudeEnvironment(host_skill="skill=/home/agent/.claude/skills/x/SKILL.md\n")

    with pytest.raises(IsolationError, match=r"~/\.claude/skills"):
        _turn(_claude(tmp_path), env, resume=False)

    assert env.commands("claude --verbose") == []


def test_harbor_inputs_that_land_in_the_claude_home_are_refused(tmp_path: Path) -> None:
    env = ClaudeEnvironment()

    with pytest.raises(IsolationError, match="skills_dir"):
        _turn(_claude(tmp_path, skills_dir="/skills"), env, resume=False)

    assert env.calls == []


# --- session audit --------------------------------------------------------------------------


def _rollout(turn_dir: Path, *rows: dict[str, Any]) -> Path:
    folder = turn_dir / "sessions" / "2026" / "10" / "27"
    folder.mkdir(parents=True)
    path = folder / "rollout-2026-10-27T10-00-00-abc.jsonl"
    path.write_text(_lines(*rows))
    return path


def _message(role: str, text: str) -> dict[str, Any]:
    content = [{"text": text}]
    payload = {"type": "message", "role": role, "content": content}
    return {"type": "response_item", "payload": payload}


def test_a_codex_audit_normalizes_roots_instructions_and_developer_messages(tmp_path: Path) -> None:
    rollout = _rollout(
        tmp_path,
        {"type": "session_meta", "payload": {"cli_version": "0.156.1"}},
        _message("developer", "be brief"),
        _message(
            "user",
            "<skills_instructions>\n- `r0` = `/app/.agents/skills`\n- `r1` = `/etc/codex/skills`\n",
        ),
        _message(
            "user",
            "# AGENTS.md instructions for /app\n\n<INSTRUCTIONS>\nUse tabs.\n</INSTRUCTIONS>",
        ),
    )

    audit = _codex(tmp_path).session_audit(tmp_path)

    assert audit["harness"] == "codex"
    assert audit["version"] == "0.156.1"
    assert audit["records"] == 4
    assert audit["skill_roots"] == ["/app/.agents/skills", "/etc/codex/skills"]
    assert audit["instructions"] == [{"path": "/app", "text": "\nUse tabs.\n"}]
    assert [m["role"] for m in audit["messages"]] == ["developer"]
    assert len(audit["messages"][0]["sha256"]) == 64
    assert audit["sources"][0]["path"] == rollout.relative_to(tmp_path).as_posix()
    assert audit["violations"] == []
    assert audit["unmeasured"] == []


def test_a_codex_audit_reports_plugin_recommendations_and_a_missing_rollout(
    tmp_path: Path,
) -> None:
    empty = _codex(tmp_path).session_audit(tmp_path / "none")
    _rollout(tmp_path, _message("user", "<recommended_plugins>\nuse x\n"))
    recommended = _codex(tmp_path).session_audit(tmp_path)

    assert empty["unmeasured"] == ["no rollout recorded"]
    assert empty["records"] == 0
    assert empty["sources"] == []
    assert recommended["violations"] == ["plugin recommendations reached the agent"]


def _transcript(turn_dir: Path, *events: dict[str, Any]) -> None:
    folder = turn_dir / "sessions" / "projects" / "-app"
    folder.mkdir(parents=True)
    (folder / "s1.jsonl").write_text(_lines(*events))


def test_a_claude_audit_reads_instructions_skill_roots_and_system_messages(tmp_path: Path) -> None:
    stream = _lines(INIT, _result("ok", tokens=ONE, cost=None))
    (tmp_path / "claude-code.txt").write_text(stream)
    reminder = (
        "<system-reminder>\n# claudeMd\nContents of /app/CLAUDE.md (project instructions, "
        "checked into the codebase):\n\nUse tabs.\n</system-reminder>"
    )
    _transcript(
        tmp_path,
        {"type": "user", "version": "2.1.285", "message": {"content": [{"text": reminder}]}},
        {
            "type": "user",
            "message": {"content": "Base directory for this skill: /app/.claude/skills/r"},
        },
        {"type": "system", "content": "compacted"},
    )

    audit = _claude(tmp_path).session_audit(tmp_path)

    assert set(audit) == AUDIT_KEYS
    assert audit["harness"] == "claude-code"
    assert audit["version"] == "2.1.285"
    assert audit["skill_roots"] == ["/app/.claude/skills"]
    assert [(i["path"], i["text"].strip()) for i in audit["instructions"]] == [
        ("/app/CLAUDE.md", "Use tabs.")
    ]
    assert [m["role"] for m in audit["messages"]] == ["system"]
    assert {source["path"] for source in audit["sources"]} == {
        "claude-code.txt",
        "sessions/projects/-app/s1.jsonl",
    }
    assert audit["violations"] == []
    assert audit["unmeasured"] == []


def test_a_claude_audit_flags_plugins_and_does_not_pass_unrecorded_skill_roots(
    tmp_path: Path,
) -> None:
    init = {
        **INIT,
        "plugins": [{"name": "p"}],
        "mcp_servers": [{"name": "m"}],
        "skills": ["a", "b"],
    }
    (tmp_path / "claude-code.txt").write_text(_lines(init))
    _transcript(tmp_path, {"type": "user", "message": {"content": "hi"}})

    audit = _claude(tmp_path).session_audit(tmp_path)

    assert audit["violations"] == ["claude loaded 1 plugin(s)", "claude loaded 1 MCP server(s)"]
    assert any("2 skill(s)" in reason for reason in audit["unmeasured"])


def test_a_claude_audit_without_records_is_unmeasured(tmp_path: Path) -> None:
    audit = _claude(tmp_path).session_audit(tmp_path)

    assert audit["records"] == 0
    assert audit["sources"] == []
    assert audit["violations"] == []
    assert len(audit["unmeasured"]) == 2
