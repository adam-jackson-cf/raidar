"""Codex isolation under Harbor: what every command carries, and what is refused before it."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from harbor.models.agent.context import AgentContext

from raidar.subject.agent.config import Harness
from raidar.subject.agent.protocol import IsolationError
from tests.native_support import native_agent

FEATURES = ["plugins", "remote_plugin", "apps", "plugin_sharing", "recommended_plugins"]
BUNDLED = ["imagegen", "skill-installer"]
HOST_SKILL = "/home/agent/.agents/skills/house-style/SKILL.md"
PROBE = (
    "user=agent\npath=/usr/local/bin:/usr/bin\ntmpdir=\n"
    f"skill={HOST_SKILL}\nskill=/etc/codex/skills/admin/SKILL.md\n"
)


class ScriptedEnvironment:
    """Records every command; answers the isolation checks and the probe as told."""

    default_user = "agent"
    capabilities = SimpleNamespace(mounted=True)

    def __init__(self, *, managed: str = "", probe: str = PROBE) -> None:
        self.managed = managed
        self.probe = probe
        self.calls: list[dict[str, Any]] = []

    async def exec(self, command: str, **kwargs: Any) -> Any:
        self.calls.append({"command": command, **kwargs})
        stdout = ""
        if "/etc/codex/config.toml" in command:
            stdout = self.managed
        elif "SKILL.md" in command and "codex exec" not in command:
            stdout = self.probe
        return SimpleNamespace(return_code=0, stdout=stdout, stderr="")

    def commands(self, needle: str) -> list[str]:
        return [call["command"] for call in self.calls if needle in call["command"]]


def _agent(logs_dir: Path, **kwargs: Any) -> Any:
    return native_agent(
        Harness.CODEX_CLI, logs_dir, features_off=FEATURES, bundled_skills=BUNDLED, **kwargs
    )


def _turn(agent: Any, environment: ScriptedEnvironment, *, resume: bool) -> None:
    turn = agent.resume if resume else agent.run
    asyncio.run(turn("do the task", environment, AgentContext()))


@pytest.mark.parametrize("resume", [False, True], ids=["first-turn", "resumed-turn"])
def test_every_command_carries_the_isolation_flags(tmp_path: Path, resume: bool) -> None:
    environment = ScriptedEnvironment()
    agent = _agent(tmp_path)

    _turn(agent, environment, resume=resume)

    (command,) = environment.commands("codex exec")
    assert ("codex exec resume --last" in command) is resume
    assert "--ignore-user-config" in command
    for feature in FEATURES:
        assert f"--disable {feature}" in command
    skills = (
        "'skills.config=["
        '{path="/tmp/codex-home/skills/.system/imagegen/SKILL.md",enabled=false},'
        '{path="/tmp/codex-home/skills/.system/skill-installer/SKILL.md",enabled=false},'
        '{path="/etc/codex/skills/admin/SKILL.md",enabled=false},'
        f'{{path="{HOST_SKILL}",enabled=false}}]\''
    )
    assert f"-c {skills}" in command
    assert command.index("--ignore-user-config") < command.index("-- ")
    assert agent.harness_isolation is not None
    assert agent.harness_isolation["features_off"] == FEATURES
    assert agent.process_facts() == {
        "user": "agent",
        "path": "/usr/local/bin:/usr/bin",
        "tmpdir": None,
    }


def test_the_turn_starts_from_a_fresh_codex_home(tmp_path: Path) -> None:
    environment = ScriptedEnvironment()

    _turn(_agent(tmp_path), environment, resume=False)

    commands = [call["command"] for call in environment.calls]
    removed = next(i for i, c in enumerate(commands) if "rm -rf /tmp/codex-home" in c)
    created = next(i for i, c in enumerate(commands) if 'mkdir -p "$CODEX_HOME"' in c)
    assert removed < created
    assert environment.calls[removed]["user"] == "root"


def test_a_container_with_nothing_to_switch_off_adds_only_the_config_isolation(
    tmp_path: Path,
) -> None:
    environment = ScriptedEnvironment(probe="user=agent\npath=/usr/bin\ntmpdir=/scratch\n")
    agent = native_agent(Harness.CODEX_CLI, tmp_path, features_off=[], bundled_skills=[])

    _turn(agent, environment, resume=False)

    (command,) = environment.commands("codex exec")
    assert "--ignore-user-config" in command
    assert "--disable" not in command
    assert "skills.config" not in command
    assert agent.process_facts() == {"user": "agent", "path": "/usr/bin", "tmpdir": "/scratch"}


def test_a_command_cannot_be_built_before_the_turn_was_isolated(tmp_path: Path) -> None:
    with pytest.raises(IsolationError, match="before the turn was isolated"):
        _agent(tmp_path).build_cli_flags()


def test_a_container_with_managed_codex_config_is_refused(tmp_path: Path) -> None:
    environment = ScriptedEnvironment(managed="/etc/codex/requirements.toml\n")

    with pytest.raises(IsolationError, match="requirements.toml"):
        _turn(_agent(tmp_path), environment, resume=False)
    assert environment.commands("codex exec") == []


def test_a_harbor_skills_dir_is_refused(tmp_path: Path) -> None:
    environment = ScriptedEnvironment()

    with pytest.raises(IsolationError, match="skills_dir"):
        _turn(_agent(tmp_path, skills_dir="/skills"), environment, resume=False)
    assert environment.calls == []


def test_a_native_config_that_would_land_in_codex_home_is_refused(tmp_path: Path) -> None:
    environment = ScriptedEnvironment()

    with pytest.raises(IsolationError, match=r"config\.toml"):
        _turn(_agent(tmp_path, config={"model": "o3"}), environment, resume=False)
    assert environment.commands("codex exec") == []


@pytest.mark.parametrize("resume", [False, True], ids=["first-turn", "resumed-turn"])
def test_instructions_are_placed_at_the_definitions_path_in_the_isolated_home(
    tmp_path: Path, resume: bool
) -> None:
    environment = ScriptedEnvironment()
    text = "Use tabs.\nüñí\n"
    agent = _agent(tmp_path, instructions=text, instructions_path="{home}/notes/RULES.md")

    _turn(agent, environment, resume=resume)

    (placed,) = environment.commands("base64 -d")
    assert placed.endswith("| base64 -d > /tmp/codex-home/notes/RULES.md")
    assert base64.b64encode(text.encode()).decode() in placed
    assert "/app/AGENTS.md" not in placed
    call = next(c for c in environment.calls if "base64 -d" in c["command"])
    assert call["env"]["CODEX_HOME"] == "/tmp/codex-home"


def test_no_instructions_place_nothing(tmp_path: Path) -> None:
    environment = ScriptedEnvironment()

    _turn(_agent(tmp_path, instructions_path="{home}/AGENTS.md"), environment, resume=False)

    assert environment.commands("base64 -d") == []


@pytest.mark.parametrize("path", [None, "/app/AGENTS.md", "{home}/../../app/AGENTS.md"])
def test_instructions_never_land_outside_the_isolated_home(
    tmp_path: Path, path: str | None
) -> None:
    environment = ScriptedEnvironment()
    agent = _agent(tmp_path, instructions="x", instructions_path=path)

    with pytest.raises(IsolationError):
        _turn(agent, environment, resume=False)
    assert environment.commands("base64 -d") == []
