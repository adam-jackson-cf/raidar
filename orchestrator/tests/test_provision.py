"""Per-trial installs at environment start with a cheap per-step check (``provision``).

The install of a ``TrialProvision`` runs once, as root, right after the
container starts (inside ``RunScopedDockerEnvironment.start``, so its time is
``environment_start``), from a payload uploaded to a root-only staging dir; the
per-step check runs as the agent user before every step's agent and each step
records the outcome. A frozen ``Provision`` keeps its released path.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from harbor.trial.hooks import TrialEvent

from raidar.components.definition.contribution import SupportFile, evidence_log
from raidar.components.runtime.provision import (
    AGENT_USER_ENV,
    INSTALL_PHASE,
    PAYLOAD_ENV,
    PROVISION_CHANNEL,
    STAGING_ROOT,
    Provision,
    TrialProvision,
    install_trial_provisioning,
)
from raidar.execution import environment as env_module
from raidar.execution.environment import RunScopedDockerEnvironment

SLUG = "02-tool"
PROVISION = TrialProvision(
    SLUG,
    "tool@3",
    {"bin/tool": SupportFile(b"#!/bin/sh\n", executable=True), "config/a.toml": SupportFile(b"x")},
    "INSTALL",
    "CHECK",
)


class _Env:
    def __init__(self, *, install_rc: int = 0, start_hooks: bool = True) -> None:
        self.calls: list[dict[str, Any]] = []
        self.uploads: list[tuple[dict[str, bytes], str]] = []
        self.records: list[tuple[str, dict[str, Any]]] = []
        self.install_rc = install_rc
        self.hooks: list[Any] = []
        if start_hooks:
            self.add_post_start_hook = self.hooks.append

    async def exec(
        self,
        command: str,
        user: str | None = None,
        timeout_sec: int = 0,
        env: dict[str, str] | None = None,
    ) -> Any:
        kind = {"INSTALL": "install", "CHECK": "check"}.get(command)
        kind = kind or ("record" if ">>" in command else "stage")
        self.calls.append({"kind": kind, "user": user, "env": env, "command": command})
        if kind == "record":
            encoded = command.split("printf %s ", 1)[1].split(" ", 1)[0]
            target = command.rsplit(">> ", 1)[1].split(" ", 1)[0]
            self.records.append((target, json.loads(base64.b64decode(encoded))))
        if kind == "install":
            return SimpleNamespace(return_code=self.install_rc, stdout="", stderr="no pypi")
        return SimpleNamespace(return_code=0, stdout="tree_digest=abc\n", stderr="")

    async def upload_dir(self, source: str, target: str) -> None:
        files = {
            path.relative_to(source).as_posix(): path.read_bytes()
            for path in Path(source).rglob("*")
            if path.is_file()
        }
        self.uploads.append((files, target))


def _trial(env: _Env, provisions: list[Any]) -> dict[TrialEvent, list[Any]]:
    hooks: dict[TrialEvent, list[Any]] = {}
    trial = SimpleNamespace(
        task=SimpleNamespace(config=SimpleNamespace(agent=SimpleNamespace(user="agent"))),
        agent_environment=env,
        add_hook=lambda event, hook: hooks.setdefault(event, []).append(hook),
    )
    install_trial_provisioning(cast(Any, trial), provisions)
    return hooks


def _step(name: str) -> Any:
    return SimpleNamespace(result=SimpleNamespace(step_results=[SimpleNamespace(step_name=name)]))


def test_the_install_runs_once_at_start_and_every_step_only_checks() -> None:
    env = _Env()
    hooks = _trial(env, [PROVISION])
    (before_agent,) = hooks[TrialEvent.AGENT_START]
    (at_start,) = env.hooks

    asyncio.run(at_start())
    asyncio.run(before_agent(_step("checkpoint_1")))
    asyncio.run(before_agent(_step("checkpoint_2")))

    kinds = [(call["kind"], call["user"]) for call in env.calls]
    assert kinds == [
        ("stage", "root"),
        ("install", "root"),
        ("check", "agent"),
        ("record", "root"),
        ("check", "agent"),
        ("record", "root"),
    ]
    install = next(call for call in env.calls if call["kind"] == "install")
    staging = str(STAGING_ROOT / SLUG)
    assert install["env"] == {PAYLOAD_ENV: staging, AGENT_USER_ENV: "agent"}
    ((files, target),) = env.uploads
    assert (set(files), target) == ({"bin/tool", "config/a.toml"}, staging)
    first, second = (record for _, record in env.records)
    assert {target for target, _ in env.records} == {str(evidence_log(SLUG, PROVISION_CHANNEL))}
    assert (first["step"], second["step"]) == ("checkpoint_1", "checkpoint_2")
    assert first["install"]["phase"] == INSTALL_PHASE
    assert first["install"]["ok"] is True
    assert second["check"]["values"] == {"tree_digest": "abc"}


def test_a_failed_install_is_recorded_and_no_step_checks() -> None:
    env = _Env(install_rc=1)
    hooks = _trial(env, [PROVISION])

    asyncio.run(env.hooks[0]())
    asyncio.run(hooks[TrialEvent.AGENT_START][0](_step("checkpoint_1")))

    ((_, record),) = env.records
    assert record["install"]["ok"] is False
    assert "no pypi" in record["install"]["output"]
    assert record["check"] is None


def test_an_environment_that_cannot_run_work_at_start_records_a_failed_install() -> None:
    env = _Env(start_hooks=False)
    hooks = _trial(env, [PROVISION])

    asyncio.run(hooks[TrialEvent.AGENT_START][0](_step("checkpoint_1")))

    ((_, record),) = env.records
    assert record["install"]["ok"] is False
    assert "cannot run an install at environment start" in record["install"]["output"]


def test_a_frozen_provision_keeps_its_released_path_beside_a_trial_provision() -> None:
    env = _Env()
    legacy = Provision("01-lint-tool", "lint-tool@1", "INSTALL", "CHECK")
    hooks = _trial(env, [legacy, PROVISION])

    assert len(hooks[TrialEvent.AGENT_START]) == 2
    assert len(env.hooks) == 1, "only the trial provision installs at environment start"
    assert _trial(_Env(), []) == {}


async def test_post_start_hooks_run_inside_start_after_the_container_is_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def compose_start(self: Any, force_build: bool) -> None:
        calls.append("start")

    async def rootless() -> bool:
        return False

    async def warm() -> None:
        return None

    monkeypatch.setattr("harbor.environments.docker.docker.DockerEnvironment.start", compose_start)
    monkeypatch.setattr(env_module, "warm_agent_imports", warm)
    env = object.__new__(RunScopedDockerEnvironment)
    env._post_start = []  # noqa: SLF001
    monkeypatch.setattr(env, "_is_rootless_docker", rootless, raising=False)

    async def hook() -> None:
        calls.append("install")

    env.add_post_start_hook(hook)
    await env.start(False)

    assert calls == ["start", "install"]
