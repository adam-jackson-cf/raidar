import json

import pytest

from raidar.runtime import command_records
from raidar.runtime.models import CommandRecord


def test_git_command_parsing_handles_env_options_and_bypass_modes():
    assert command_records._git_command_tokens("not-a-git command") == ({}, [])
    assert command_records._git_command_tokens("unterminated 'git commit")[1] == [
        "unterminated",
        "'git",
        "commit",
    ]
    assert command_records._strip_shell_env_prefix(["env", "A=1", "git", "commit"]) == (
        {"A": "1"},
        ["git", "commit"],
    )
    assert command_records._strip_shell_env_prefix(["1BAD=x", "git"]) == ({}, ["1BAD=x", "git"])
    assert (
        command_records._git_subcommand_index(
            ["git", "-C", "repo", "-cuser.name=x", "--", "commit"]
        )
        == 5
    )

    assert command_records._git_commit_uses_verification_bypass("git status") is False
    assert command_records._git_commit_uses_verification_bypass("HUSKY=0 git commit -m x") is True
    assert (
        command_records._git_commit_uses_verification_bypass("NO_VERIFY=yes git commit -m x")
        is True
    )
    assert (
        command_records._git_commit_uses_verification_bypass(
            "git -c core.hooksPath=/dev/null commit -m x"
        )
        is True
    )
    assert command_records._git_commit_uses_verification_bypass("git commit -n -m x") is True
    assert command_records._should_record_command("git commit -m x", include_git_commit=True)
    assert not command_records._should_record_command("git commit -m x", include_git_commit=False)


def test_shell_normalization_handles_empty_heredoc_bad_quotes_and_aliases():
    assert command_records._normalize_command("   ") == ""
    assert command_records._normalized_joined_command([]) is None
    assert command_records._split_token_by_shell_separators("bun&&npm;pnpm||yarn") == [
        "bun",
        "&&",
        "npm",
        ";",
        "pnpm",
        "||",
        "yarn",
    ]
    assert command_records._shell_command_segments("cat <<EOF\nhello\nEOF\nbun run build") == [
        "cat <<EOF\nhello\nEOF",
        "bun run build",
    ]
    assert command_records._normalized_shell_subcommands("bun run 'unterminated") == [
        "bun run 'unterminated"
    ]
    assert command_records._unwrap_shell_wrapper("sh -lc 'npm run lint'") == "npm run lint"
    assert command_records._normalize_verification_alias("pnpm build") == "bun run build"
    assert command_records._normalize_verification_alias("npx ultracite lint") == "bun run lint"
    assert command_records._normalize_verification_alias("tsc --noEmit") == "bun run typecheck"


def test_codex_command_records_extracts_outputs_failures_and_git_commits(monkeypatch):
    entries = [
        {"item": None},
        {"item": {"type": "message"}},
        {
            "item": {
                "type": "command_execution",
                "command": "bun run test && git commit -m ok",
                "status": "failed",
                "exit_code": "2",
                "aggregated_output": "combined",
            }
        },
        {
            "item": {
                "type": "command_execution",
                "command": "npm run build",
                "stdout": "out",
                "stderr": "err",
            }
        },
    ]
    monkeypatch.setattr(command_records, "_extract_item_completed", lambda entry: entry["item"])

    records = command_records._command_records(entries, include_git_commit=True)

    assert records == [
        CommandRecord(command="bun run test", failed=True, output="combined", exit_code=2),
        CommandRecord(command="git commit -m ok", failed=True, output="combined", exit_code=2),
        CommandRecord(command="bun run build", failed=False, output="out\nerr", exit_code=None),
    ]


def test_command_records_route_supported_harnesses_and_reject_unknown(monkeypatch, tmp_path):
    trial = tmp_path / "trial"
    agent = trial / "agent"
    agent.mkdir(parents=True)
    (agent / "codex.txt").write_text("", encoding="utf-8")
    (agent / "command-001").mkdir()
    (agent / "command-001" / "stdout.txt").write_text("$ bun run build\n", encoding="utf-8")
    (agent / "gemini-cli.txt").write_text("ran `bun run lint` successfully\n", encoding="utf-8")

    monkeypatch.setattr(command_records, "_read_jsonl_dicts", lambda _path: [])
    assert command_records._command_records_for_harness(trial, "codex-cli") == []
    assert [
        record.command for record in command_records._command_records_for_harness(trial, "cursor")
    ] == ["bun run build"]
    assert [
        record.command for record in command_records._command_records_for_harness(trial, "gemini")
    ] == [
        "bun run build",
        "bun run lint",
    ]
    assert command_records._command_records_from_harness_stdout(tmp_path / "missing") == []
    with pytest.raises(ValueError, match="Unsupported harness"):
        command_records._command_records_for_harness(trial, "unknown")


def test_claude_stdout_records_tool_use_failures_and_ignores_invalid_shapes(tmp_path):
    stdout = tmp_path / "claude-code.txt"
    tool_use = {
        "message": {
            "content": [
                {"type": "tool_use", "name": "Read", "id": "skip", "input": {}},
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "id": "tool-1",
                    "input": {"command": "bun run typecheck"},
                },
            ]
        }
    }
    tool_result = {
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": "missing", "is_error": True},
                {"type": "tool_result", "tool_use_id": "tool-1", "is_error": True},
            ]
        }
    }
    stdout.write_text(
        "\n".join(
            [
                "",
                "I will run `bun run build`",
                "verified with `bun run lint` and it failed",
                json.dumps(tool_use),
                json.dumps(tool_result),
                "[]",
            ]
        ),
        encoding="utf-8",
    )

    records = command_records._command_records_from_claude_stdout_file(stdout)

    assert [(record.command, record.failed) for record in records] == [
        ("bun run lint", True),
        ("bun run typecheck", True),
    ]
    assert command_records._claude_message_content({"message": "bad"}) is None
    assert command_records._claude_bash_tool_use_command("bad") is None
    assert (
        command_records._claude_bash_tool_use_command({"type": "tool_use", "name": "Bash"}) is None
    )
    assert command_records._claude_failed_tool_result_ids({"message": {"content": ["bad"]}}) == []


def test_stdout_line_and_gemini_trajectory_command_extraction(tmp_path):
    assert command_records._command_records_from_line("") == []
    assert command_records._command_records_from_line("I will run `bun run build`") == []
    assert command_records._command_records_from_line("no command here") == []
    assert command_records._command_records_from_line("$ git commit -m ok") == []
    assert (
        command_records._command_records_from_line("$ git commit -m ok", include_git_commit=True)[
            0
        ].command
        == "git commit -m ok"
    )
    assert command_records._quoted_command_records("ran `echo hi`") == []
    assert (
        command_records._keyword_command_records("verified with lint and test coverage")[0].command
        == "bun run lint"
    )
    assert (
        command_records._keyword_command_records(
            "verified with git commit", include_git_commit=True
        )[-1].command
        == "git commit"
    )

    agent = tmp_path / "trial" / "agent"
    agent.mkdir(parents=True)
    (agent / "gemini-cli.trajectory.json").write_text(
        json.dumps(
            {
                "messages": [
                    "bad",
                    {"toolCalls": "bad"},
                    {
                        "toolCalls": [
                            "bad",
                            {"name": "other"},
                            {"name": "run_shell_command", "args": "bad"},
                            {"name": "run_shell_command", "args": {"command": ""}},
                            {
                                "name": "run_shell_command",
                                "status": "error",
                                "args": {"command": "bun run test"},
                            },
                        ]
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    records = command_records._command_records_from_gemini_trajectory(tmp_path / "trial")

    assert records == [CommandRecord(command="bun run test", failed=True, output="bun run test")]
    (agent / "gemini-cli.trajectory.json").write_text('{"messages":{}}', encoding="utf-8")
    assert command_records._command_records_from_gemini_trajectory(tmp_path / "trial") == []
