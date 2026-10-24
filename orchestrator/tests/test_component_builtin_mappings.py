"""Built-in components mapped onto Harbor 0.23 inputs, with probes.

The real-trial probe run is Codex-only (model policy); the Claude Code mapping
and its probe evidence shapes are covered here.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import pytest
from harbor.models.trial.config import AgentConfig
from pydantic import JsonValue

from raidar.agents.config import Harness
from raidar.components.definition.compiled import RESET_PROBE
from raidar.components.runtime.mcp_shim import parse_args, run
from tests.component_support import (
    CLAUDE_SETTINGS,
    CODEX_SETTINGS,
    FIXTURES_DIR,
    PROBE_DIR,
    all_builtin_uses,
    codex_message,
    compile_uses,
    use,
    write_jsonl,
    write_step_reports,
)

RULES_TEXT = (FIXTURES_DIR / PROBE_DIR / "AGENTS.md").read_text()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _overlay_evidence(harness: Harness) -> dict[str, str | None]:
    probe = FIXTURES_DIR / PROBE_DIR
    rules = "AGENTS.md" if harness is Harness.CODEX_CLI else "CLAUDE.md"
    return {
        rules: _sha(probe / "AGENTS.md"),
        "docs/starter/NOTES.txt": _sha(probe / "starter" / "NOTES.txt"),
        "ruff.toml": _sha(probe / "ruff.toml"),
    }


@pytest.mark.parametrize(
    ("harness", "agent_name", "rules_target"),
    [(Harness.CODEX_CLI, "codex", "AGENTS.md"), (Harness.CLAUDE_CODE, "claude-code", "CLAUDE.md")],
)
def test_builtins_map_onto_harbor_agent_inputs(
    harness: Harness, agent_name: str, rules_target: str
) -> None:
    compiled = compile_uses(*all_builtin_uses(harness), harness=harness)
    base = AgentConfig(name=agent_name, model_name="m", kwargs={"version": "1.0.0"})

    config = compiled.apply_to_agent_config(base)

    assert sorted(compiled.overlay) == sorted([rules_target, "docs/starter/NOTES.txt", "ruff.toml"])
    assert config.kwargs["version"] == "1.0.0"
    assert config.kwargs["prompt_template_path"].endswith("probe/wrapper.md")
    assert [Path(skill).name for skill in config.skills] == ["raidar-probe-skill"]
    (server,) = config.mcp_servers
    assert server.transport == "stdio"
    assert server.command == "python3"
    assert server.args[0] == "/tmp/raidar-components/06-mcp-server/mcp_shim.py"
    assert server.args[-1] == "/tmp/raidar-components/06-mcp-server/files/mcp_echo_server.py"
    native = config.kwargs["config"]
    wrapper = "/tmp/raidar-components/05-stop-hook/stop-hook.sh"
    if harness is Harness.CODEX_CLI:
        assert native["notify"] == ["bash", wrapper]
        assert native["developer_instructions"] == CODEX_SETTINGS["developer_instructions"]
    else:
        assert native["hooks"]["Stop"][0]["hooks"][0]["command"] == f"bash {wrapper}"
        assert native["outputStyle"] == CLAUDE_SETTINGS["outputStyle"]
    assert set(compiled.support_files) == {
        "05-stop-hook/stop-hook.sh",
        "05-stop-hook/hook-body.sh",
        "06-mcp-server/mcp_shim.py",
        "06-mcp-server/files/mcp_echo_server.py",
    }


def test_workflow_wraps_the_agent_and_keeps_its_inputs() -> None:
    compiled = compile_uses(
        use("review-loop@2", max_review_rounds=2),
        use("prompt-wrapper@1", (f"{PROBE_DIR}/wrapper.md",)),
    )
    config = compiled.apply_to_agent_config(
        AgentConfig(name="codex", model_name="openai/gpt-6-luna", kwargs={"version": "0.156.1"})
    )

    assert config.name is None
    assert config.import_path == "raidar.components.runtime.review_loop:ReviewLoopAgent"
    assert config.model_name == "openai/gpt-6-luna"
    assert config.kwargs["max_review_rounds"] == 2
    inner = config.kwargs["inner_agent"]
    assert inner["name"] == "codex"
    assert inner["kwargs"]["version"] == "0.156.1"
    assert inner["kwargs"]["prompt_template_path"].endswith("wrapper.md")


def test_agent_config_refuses_a_non_builtin_agent_or_an_owned_kwarg() -> None:
    compiled = compile_uses(use("harness-settings@1", settings=CODEX_SETTINGS))

    with pytest.raises(ValueError, match="apply to Harbor's 'codex' agent"):
        compiled.apply_to_agent_config(AgentConfig(import_path="x.y:Agent"))
    with pytest.raises(ValueError, match="'config' is already set"):
        compiled.apply_to_agent_config(AgentConfig(name="codex", kwargs={"config": {}}))


def test_codex_probes_pass_on_real_evidence_shapes(tmp_path: Path) -> None:
    compiled = compile_uses(*all_builtin_uses(Harness.CODEX_CLI))
    step = tmp_path / "steps" / "checkpoint_1" / "agent"
    write_step_reports(step, _overlay_evidence(Harness.CODEX_CLI))
    write_jsonl(
        step / "sessions" / "2026" / "rollout-2026-09-30T10-00-00-x.jsonl",
        [
            codex_message("developer", "### Available skills\n- raidar-probe-skill: fixture"),
            codex_message("developer", CODEX_SETTINGS["developer_instructions"]),  # type: ignore[arg-type]
            codex_message("user", f"# AGENTS.md instructions for /app\n\n{RULES_TEXT}"),
            codex_message("user", "RAIDAR-PROBE-WRAPPER: complete the task below.\n\nDo it."),
        ],
    )
    write_jsonl(step / "raidar-components" / "05-stop-hook.hook.jsonl", [{"event": "stop"}])
    write_jsonl(
        step / "raidar-components" / "06-mcp-server.mcp.jsonl",
        [
            {"dir": "client", "method": "initialize"},
            {"dir": "server", "result_of": "initialize"},
            {"dir": "server", "result_of": "tools/list", "tools": ["raidar_echo"]},
        ],
    )

    report = compiled.probe_step(step, 0)

    assert report.passed, report.to_json()
    assert [result.component for result in report.results][0] == RESET_PROBE
    assert len(report.results) == 9
    assert compiled.probe_step(step, 3).passed


def test_claude_probes_read_init_event_and_transcripts(tmp_path: Path) -> None:
    compiled = compile_uses(*all_builtin_uses(Harness.CLAUDE_CODE), harness=Harness.CLAUDE_CODE)
    step = tmp_path / "agent"
    write_step_reports(step, _overlay_evidence(Harness.CLAUDE_CODE))
    write_jsonl(
        step / "claude-code.txt",
        [
            {
                "type": "system",
                "subtype": "init",
                "output_style": "Explanatory",
                "skills": ["raidar-probe-skill"],
            }
        ],
    )
    write_jsonl(
        step / "sessions" / "projects" / "-app" / "s.jsonl",
        [
            {"type": "user", "message": {"content": [{"type": "text", "text": RULES_TEXT}]}},
            {"message": "RAIDAR-PROBE-WRAPPER: complete the task below.\n\nDo it.", "n": 1},
        ],
    )
    write_jsonl(step / "raidar-components" / "05-stop-hook.hook.jsonl", [{"event": "stop"}])
    write_jsonl(
        step / "raidar-components" / "06-mcp-server.mcp.jsonl",
        [
            {"dir": "client", "method": "initialize"},
            {"dir": "server", "result_of": "initialize"},
            {"dir": "server", "result_of": "tools/list", "tools": ["raidar_echo"]},
        ],
    )

    assert compiled.probe_step(step, 0).passed


def test_probes_fail_when_a_component_did_not_take_effect(tmp_path: Path) -> None:
    compiled = compile_uses(*all_builtin_uses(Harness.CODEX_CLI))
    step = tmp_path / "agent"
    evidence = _overlay_evidence(Harness.CODEX_CLI)
    evidence["ruff.toml"] = None
    evidence["docs/starter/NOTES.txt"] = "0" * 64
    write_step_reports(step, evidence, residual=True)
    write_jsonl(
        step / "raidar-components" / "06-mcp-server.mcp.jsonl",
        [{"dir": "client", "method": "initialize"}],
    )
    write_jsonl(step / "sessions" / "rollout-1.jsonl", [{"type": "turn_context", "payload": {}}])

    report = compiled.probe_step(step, 0)
    failed = {result.component: result.detail for result in report.failures()}

    assert not report.passed
    assert set(failed) == {
        RESET_PROBE,
        "rules-file@1",
        "workspace-files@1",
        "lint-config@1",
        "skills@1",
        "harness-settings@1",
        "stop-hook@1",
        "mcp-server@1",
        "prompt-wrapper@1",
    }
    assert "residual processes" in failed[RESET_PROBE]
    assert "absent" in failed["lint-config@1"]
    assert "content differs" in failed["workspace-files@1"]
    assert "never listed" in failed["mcp-server@1"]
    assert compiled.probe_step(tmp_path / "empty", 0).failures()[0].detail.startswith("no raidar")


def test_rules_file_changed_by_the_agent_is_not_held_against_later_steps(tmp_path: Path) -> None:
    compiled = compile_uses(use("rules-file@1", (f"{PROBE_DIR}/AGENTS.md",)))
    step = tmp_path / "agent"
    write_step_reports(step, {"AGENTS.md": "f" * 64})

    later = compiled.probe_step(step, 2)

    assert later.passed
    assert "changed by the agent" in later.results[1].detail


def test_mcp_shim_relays_and_records_the_handshake(tmp_path: Path) -> None:
    log = tmp_path / "mcp.jsonl"
    server = FIXTURES_DIR / PROBE_DIR / "mcp_echo_server.py"
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": "t", "method": "tools/list"},
        "not json",
    ]
    stdin = io.BytesIO(
        "".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in requests).encode()
    )
    stdout = io.BytesIO()

    code = run(["--log", str(log), "--", sys.executable, str(server)], stdin, stdout)

    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert code == 1  # the fixture server exits on the non-JSON line
    assert [reply["id"] for reply in replies] == [1, "t"]
    assert {"dir": "client", "method": "initialize"} in records
    assert {"dir": "server", "result_of": "tools/list", "tools": ["raidar_echo"]} in records
    assert {"dir": "client", "unparsed": True} in records
    with pytest.raises(SystemExit):
        parse_args(["--log", "x"])


def test_settings_probe_compares_every_declared_setting_with_the_harness_report(
    tmp_path: Path,
) -> None:
    codex = compile_uses(
        use("harness-settings@1", settings={"model_reasoning_summary": "detailed"}),
        use("harness-settings@1", settings={"developer_instructions": "Prefer the stdlib."}),
    )
    step = tmp_path / "agent"
    write_step_reports(step, {})
    write_jsonl(
        step / "rollout-1.jsonl",
        [
            {"type": "turn_context", "payload": {"summary": "detailed"}},
            codex_message("developer", "Unrelated developer text."),
        ],
    )
    claude = compile_uses(
        use("harness-settings@1", settings={"outputStyle": "Explanatory"}),
        harness=Harness.CLAUDE_CODE,
    )
    write_jsonl(step / "claude-code.txt", [{"type": "system", "subtype": "init"}])

    report = codex.probe_step(step, 0)

    assert [result.passed for result in report.results[1:]] == [True, False]
    assert "expected 'Prefer the stdlib.'" in report.results[2].detail
    assert report.to_json()["results"][2]["component"] == "harness-settings@1"
    assert not claude.probe_step(step, 0).passed


@pytest.mark.parametrize(
    ("harness", "settings"),
    [
        (Harness.CODEX_CLI, {"approval_policy": "on-request"}),
        (Harness.CODEX_CLI, {"sandbox_mode": "read-only"}),
        (Harness.CLAUDE_CODE, {"permissions": {"defaultMode": "plan"}}),
    ],
    ids=["codex-approval", "codex-sandbox", "claude-permission-mode"],
)
def test_settings_harbor_always_overrides_are_refused_at_compile_time(
    harness: Harness, settings: dict[str, JsonValue]
) -> None:
    from raidar.components import ComponentRejectedError

    with pytest.raises(ComponentRejectedError, match="always overridden"):
        compile_uses(use("harness-settings@1", settings=settings), harness=harness)


def test_invalid_prompt_templates_are_rejected(tmp_path: Path) -> None:
    from raidar.components import ComponentRejectedError

    for text, message in (
        ("no slot", "exactly one"),
        ("{{ instruction }} {% if x %}", "no Jinja syntax"),
        ("{{ instruction }}\n", "add text around"),
    ):
        (tmp_path / "t.md").write_text(text)
        with pytest.raises(ComponentRejectedError, match=message):
            compile_uses(use("prompt-wrapper@1", ("t.md",)), base_dir=tmp_path)


def test_native_config_merge_appends_entry_lists_only_and_refuses_clashes() -> None:
    from raidar.components.definition.contribution import (
        ContributionConflictError,
        merge_native_config,
    )

    merged = merge_native_config(
        {"a": {"x": 1}, "hooks": {"Stop": [1]}}, {"a": {"y": 2}, "hooks": {"Stop": [2]}, "b": 3}
    )

    assert merged == {"a": {"x": 1, "y": 2}, "hooks": {"Stop": [1, 2]}, "b": 3}
    assert merge_native_config({"a": 1}, {"a": 1}) == {"a": 1}
    assert merge_native_config({"l": [1]}, {"l": [1]}) == {"l": [1]}
    with pytest.raises(ContributionConflictError, match="'a.x' is set to both"):
        merge_native_config({"a": {"x": 1}}, {"a": {"x": 2}})
    # A list that is one value (Codex notify argv) cannot take a second value.
    with pytest.raises(ContributionConflictError, match="'notify' is set to both"):
        merge_native_config({"notify": ["bash", "a"]}, {"notify": ["bash", "b"]})


def test_skill_name_falls_back_to_the_directory_and_probe_errors_fail_closed(
    tmp_path: Path,
) -> None:
    from raidar.components.catalog.harness import skill_name

    assert skill_name("---\nname: from-front\n---\nbody", "dir") == "from-front"
    assert skill_name("# no front matter", "dir") == "dir"
    rules = tmp_path / "rules.md"
    rules.write_text("# rules\n")
    compiled = compile_uses(use("rules-file@1", ("rules.md",)), base_dir=tmp_path)
    rules.unlink()
    step = tmp_path / "agent"
    write_step_reports(step, {"AGENTS.md": None})

    (_, result) = compiled.probe_step(step, 0).results

    assert not result.passed
    assert result.detail.startswith("probe error:")


def test_evidence_reader_tolerates_malformed_logs(tmp_path: Path) -> None:
    from raidar.components.definition.evidence import StepEvidence

    (tmp_path / "raidar-components.json").write_text("not json")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "raidar-components.json").write_text('{"workspace_files": [1]}')
    (tmp_path / "rollout-1.jsonl").write_text(
        'warning line\n[1]\n{"type":"response_item","payload":{"type":"message","role":"user",'
        '"content":"plain text"}}\n{"type":"response_item","payload":{"type":"message",'
        '"role":"user","content":7}}\n'
    )
    evidence = StepEvidence(tmp_path)

    assert evidence.workspace_files == {}
    assert evidence.codex_messages == [("user", "plain text"), ("user", "")]
    assert evidence.codex_turn_contexts == []


def test_mcp_recorder_marks_errors_and_ignores_non_objects() -> None:
    from raidar.components.runtime.mcp_shim import Recorder

    log = io.StringIO()
    recorder = Recorder(log)
    recorder.observe("client", b'{"id": 1, "method": "tools/call"}')
    recorder.observe("server", b'{"id": 1, "error": {"code": 1}}')
    recorder.observe("server", b"[1, 2]")

    records = [json.loads(line) for line in log.getvalue().splitlines()]
    assert records == [
        {"dir": "client", "method": "tools/call"},
        {"dir": "server", "error": True, "result_of": "tools/call"},
    ]


def test_stop_hook_body_can_come_from_a_component_file() -> None:
    hook_file = f"{PROBE_DIR}/ruff.toml"
    compiled = compile_uses(use("stop-hook@1", (hook_file,)))

    body = compiled.support_files["00-stop-hook/hook-body.sh"]
    assert body.content == (FIXTURES_DIR / hook_file).read_bytes()
    assert body.executable


@pytest.mark.parametrize(
    ("records", "detail"),
    [
        (
            [
                {"dir": "client", "method": "initialize"},
                {"dir": "server", "result_of": "initialize", "error": True},
                {"dir": "server", "result_of": "tools/list", "error": True},
            ],
            "never answered initialize without an error",
        ),
        (
            [
                {"dir": "client", "method": "initialize"},
                {"dir": "server", "result_of": "initialize"},
                {"dir": "server", "result_of": "tools/list", "tools": []},
            ],
            "listed no tools",
        ),
    ],
)
def test_mcp_probe_fails_on_error_replies_or_an_empty_tool_list(
    tmp_path: Path, records: list[dict[str, object]], detail: str
) -> None:
    from raidar.components.catalog.harness import MCP_SERVER
    from raidar.components.definition.evidence import StepEvidence
    from raidar.components.definition.manifest import ComponentUseContext

    write_jsonl(tmp_path / "raidar-components" / "06-mcp-server.mcp.jsonl", records)
    use = ComponentUseContext("06-mcp-server", Harness.CODEX_CLI, tmp_path)
    outcome = MCP_SERVER.probe(use, StepEvidence(tmp_path), 0)
    assert not outcome.passed
    assert detail in outcome.detail


@pytest.mark.parametrize(("body", "passed"), [("exit 127", False), ("true", True)])
def test_stop_hook_probe_reads_the_body_exit_status(
    tmp_path: Path, body: str, passed: bool
) -> None:
    import subprocess

    from raidar.components.catalog.harness import STOP_HOOK, _hook_wrapper
    from raidar.components.definition.evidence import StepEvidence
    from raidar.components.definition.manifest import ComponentUseContext

    logs, components = tmp_path / "logs", tmp_path / "components"
    script = (
        _hook_wrapper("05-stop-hook", Harness.CODEX_CLI)
        .decode()
        .replace("/logs/agent", str(logs))
        .replace("/tmp/raidar-components", str(components))
    )
    (components / "05-stop-hook").mkdir(parents=True)
    (components / "05-stop-hook" / "hook-body.sh").write_text(f"#!/usr/bin/env bash\n{body}\n")
    wrapper = tmp_path / "stop-hook.sh"
    wrapper.write_text(script)

    done = subprocess.run(["bash", str(wrapper), "{}"], check=False)

    assert done.returncode == (0 if passed else 127)
    use = ComponentUseContext("05-stop-hook", Harness.CODEX_CLI, tmp_path)
    outcome = STOP_HOOK.probe(use, StepEvidence(logs), 0)
    assert outcome.passed is passed, outcome.detail
