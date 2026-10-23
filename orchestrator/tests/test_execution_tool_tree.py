"""T1 tool-tree hashing hooks: baseline once, after every agent phase, host-side record."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from harbor.trial.hooks import TrialEvent

from raidar.execution.health.tool_tree import (
    TOOL_HASHES,
    ToolTreeReading,
    hash_tool_tree,
    install_tool_tree_watch,
    load_tool_hashes,
    parse_tool_output,
)

SHA = "ab" * 32


class Env:
    """Answers each root exec with the next scripted stdout (or raises it)."""

    def __init__(self, replies: list[Any]) -> None:
        self.replies = replies
        self.calls: list[dict[str, Any]] = []

    async def exec(self, command: str, **kwargs: Any) -> Any:
        self.calls.append({"command": command, **kwargs})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(stdout=reply, stderr="", return_code=0)


@pytest.mark.parametrize(
    ("text", "reading"),
    [
        (f"noise\n@@TOOLS present {SHA} 42\n", ToolTreeReading(True, SHA, 42)),
        ("@@TOOLS absent\n", ToolTreeReading(False, None, 0)),
        ("@@RAIDAR_ERROR tool tree manifest failed\n", None),
        ("@@TOOLS present nonsense\n", None),
        ("", None),
    ],
)
def test_the_hash_output_is_parsed_or_unmeasured(
    text: str, reading: ToolTreeReading | None
) -> None:
    assert parse_tool_output(text) == reading


def test_a_failed_hash_exec_is_an_unmeasured_reading() -> None:
    reading, error = asyncio.run(hash_tool_tree(Env([RuntimeError("container stopped")])))
    assert reading is None
    assert error is not None
    assert "container stopped" in error
    reading, error = asyncio.run(hash_tool_tree(Env(["garbage"])))
    assert reading is None
    assert error is not None
    assert "unusable" in error


def test_the_script_hashes_the_tool_tree_and_its_provisioned_links_as_root() -> None:
    env = Env([f"@@TOOLS present {SHA} 3"])
    reading, _ = asyncio.run(hash_tool_tree(env))
    assert reading == ToolTreeReading(True, SHA, 3)
    (call,) = env.calls
    assert call["user"] == "root"
    assert "/opt/raidar-tools" in call["command"]
    assert "/usr/local/bin" in call["command"]
    assert "sha256sum" in call["command"]
    assert "./.staging" in call["command"]  # the install's scratch area is not hashed


def test_hooks_take_one_baseline_and_a_hash_after_every_agent_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies = [f"@@TOOLS present {SHA} 3", f"@@TOOLS present {SHA} 3", RuntimeError("blip")]
    env = Env(replies)
    hooks: dict[Any, list[Any]] = {}
    trial = SimpleNamespace(
        agent_environment=env,
        paths=SimpleNamespace(trial_dir=tmp_path / "trial"),
        add_hook=lambda event, hook: hooks.setdefault(event, []).append(hook),
    )
    bundle = SimpleNamespace(resolve_step=lambda name: f"resolved-{name}")
    monkeypatch.setattr("raidar.execution.health.tool_tree.load_bundle", lambda root: bundle)
    install_tool_tree_watch(cast(Any, trial), tmp_path / "bundle")
    event = SimpleNamespace(
        result=SimpleNamespace(step_results=[SimpleNamespace(step_name="checkpoint_1")])
    )

    async def phases() -> None:
        before, after = hooks[TrialEvent.AGENT_START][0], hooks[TrialEvent.AGENT_END][0]
        await before(event)
        await after(event)
        await before(event)  # a later step's agent start takes no new baseline
        await after(event)

    asyncio.run(phases())
    recorded = load_tool_hashes(tmp_path / "trial")
    assert recorded is not None
    points = recorded["points"]
    assert [p["point"] for p in points] == ["baseline", "after_agent", "after_agent"]
    assert [p["measured"] for p in points] == [True, True, False]
    assert {p["step"] for p in points} == {"resolved-checkpoint_1"}
    assert "blip" in points[2]["unmeasured"]
    assert points[0]["sha256"] == SHA
    assert len(env.calls) == 3
    assert json.loads((tmp_path / "trial" / TOOL_HASHES).read_text())["root"] == "/opt/raidar-tools"


def test_no_hash_file_is_none(tmp_path: Path) -> None:
    assert load_tool_hashes(tmp_path) is None
    (tmp_path / TOOL_HASHES).write_text("[]")
    assert load_tool_hashes(tmp_path) is None
