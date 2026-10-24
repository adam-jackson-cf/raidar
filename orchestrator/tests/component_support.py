"""Builders and evidence fixtures for component SDK tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from raidar.design.experiments.schema import AgentEntry, ComponentUse, ConditionSpec
from raidar.subjects.agents.config import Harness, agent_name_for
from raidar.subjects.treatments import CompiledCondition, compile_condition

REPO_ROOT = Path(__file__).resolve().parents[2]
# Component proof fixtures: probe files every built-in compiles from, relative to FIXTURES_DIR.
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
# A copy of the guided-pilot experiment (its components and spec), so no test reads a tracked one.
PILOT_DIR = FIXTURES_DIR / "guided_pilot"
PROBE_DIR = "components/probe"
CODEX_SETTINGS: dict[str, JsonValue] = {
    "developer_instructions": "RAIDAR-PROBE-SETTINGS: prefer the standard library.",
}
CLAUDE_SETTINGS: dict[str, JsonValue] = {"outputStyle": "Explanatory"}


def agent(harness: Harness = Harness.CODEX_CLI) -> AgentEntry:
    return AgentEntry(
        id="agent",
        name=agent_name_for(harness),
        provider="openai" if harness is Harness.CODEX_CLI else "anthropic",
        model="gpt-6-luna" if harness is Harness.CODEX_CLI else "claude-sonnet",
        version="0.156.1",
    )


def use(ref: str, files: tuple[str, ...] = (), **with_: JsonValue) -> ComponentUse:
    return ComponentUse.model_validate({"use": ref, "files": files, "with": with_})


def condition(*uses: ComponentUse, condition_id: str = "arm") -> ConditionSpec:
    return ConditionSpec(id=condition_id, components=uses)


def all_builtin_uses(harness: Harness) -> list[ComponentUse]:
    """One use of every harness/workspace/prompt built-in (the batched probe condition)."""
    settings = CODEX_SETTINGS if harness is Harness.CODEX_CLI else CLAUDE_SETTINGS
    return [
        use("rules-file@1", (f"{PROBE_DIR}/AGENTS.md",)),
        use("workspace-files@1", (f"{PROBE_DIR}/starter",), dest="docs"),
        use("lint-config@1", (f"{PROBE_DIR}/ruff.toml",)),
        use("skills@1", (f"{PROBE_DIR}/skills/raidar-probe-skill",)),
        use("harness-settings@1", settings=settings),
        use("stop-hook@1", command="true"),
        use(
            "mcp-server@1",
            (f"{PROBE_DIR}/mcp_echo_server.py",),
            name="raidar-echo",
            command="python3",
            args=["{component_dir}/mcp_echo_server.py"],
        ),
        use("prompt-wrapper@1", (f"{PROBE_DIR}/wrapper.md",)),
    ]


def compile_uses(
    *uses: ComponentUse, harness: Harness = Harness.CODEX_CLI, base_dir: Path = FIXTURES_DIR
) -> CompiledCondition:
    return compile_condition(condition(*uses), agent(harness), base_dir=base_dir)


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


def write_step_reports(
    agent_dir: Path, workspace_files: dict[str, str | None], *, residual: bool = False
) -> None:
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "raidar-components.json").write_text(
        json.dumps({"kind": "raidar-components", "workspace_files": workspace_files})
    )
    (agent_dir / "raidar-reset.json").write_text(
        json.dumps(
            {
                "kind": "raidar-step-reset",
                "killed": [{"pid": 7, "args": "sleep 99"}],
                "residual": [{"pid": 8, "args": "x"}] if residual else [],
                "state": [{"path": "/tmp/codex-home", "reset_failed": False}],
            }
        )
    )


def codex_message(role: str, text: str) -> dict[str, Any]:
    return {
        "type": "response_item",
        "payload": {"type": "message", "role": role, "content": [{"text": text}]},
    }
