"""lint-tool@1: a hash-pinned Ruff installed per trial (component SDK provisioning).

Covers the contract (manifest, configuration, the install and check scripts),
the discovery probe over recorded evidence, the pre-agent provisioning hook
(install once as root, check every step as the agent user, never raise), and,
on OrbStack, a real two-step Harbor trial on the pack image.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from harbor.models.trial.config import AgentConfig
from harbor.trial.hooks import TrialEvent

from raidar.components import (
    ComponentKind,
    ComponentRegistry,
    ComponentRejectedError,
    ComponentResolver,
    StepEvidence,
)
from raidar.components.catalog.tools import LINT_TOOL, RUFF_WHEEL_SHA256, resolved_config
from raidar.components.definition.contribution import evidence_log
from raidar.components.definition.registry import component_runtimes
from raidar.components.runtime.provision import (
    PROVISION_CHANNEL,
    Provision,
    condition_provisions,
    install_provisioning,
    parse_check_output,
)
from raidar.spec.references import RefKind, RegistryRef
from tests.component_support import PROBE_DIR, compile_uses, use, write_jsonl
from tests.component_trial_support import (
    build_task,
    docker_target_ready,
    run_trial_sync,
)

LINT_TOOL_USE = use("lint-tool@1", config="ruff.toml")
WITH_CONFIG = (use("lint-config@1", (f"{PROBE_DIR}/ruff.toml",)), LINT_TOOL_USE)
SLUG = "01-lint-tool"
TARGET = f"/opt/raidar-tools/{SLUG}/ruff-0.8.6/bin/ruff"
RESOLVE_PREFIX = "[ruff::resolve][DEBUG]"


def _context() -> Any:
    return compile_uses(*WITH_CONFIG).uses[1].context


def _check(**overrides: str) -> dict[str, Any]:
    values = {
        "workspace": "/app",
        "path_ruff": "/usr/local/bin/ruff",
        "pinned_target": TARGET,
        "version": "ruff 0.8.6",
        "resolve": f"{RESOLVE_PREFIX} Using configuration file (via parent) at: /app/ruff.toml",
    }
    return {"ok": True, "values": values | overrides}


def _record(check: dict[str, Any] | None, *, install_ok: bool = True) -> dict[str, Any]:
    return {
        "kind": "raidar-provision",
        "slug": SLUG,
        "install": {"ok": install_ok, "output": "" if install_ok else "network unreachable"},
        "check": check,
    }


def _evidence(tmp_path: Path, *records: dict[str, Any]) -> StepEvidence:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    if records:
        log = agent_dir / "raidar-components" / f"{SLUG}.{PROVISION_CHANNEL}.jsonl"
        write_jsonl(log, list(records))
    return StepEvidence(agent_dir)


def test_lint_tool_is_a_python_service_component_with_declared_egress() -> None:
    definition = ComponentRegistry().get("lint-tool@1")
    manifest = definition.manifest

    assert manifest.kind is ComponentKind.SERVICE
    assert component_runtimes(definition) == ("python",)
    assert manifest.network.egress_hosts == ("files.pythonhosted.org", "pypi.org")
    assert "raidar/components/runtime/provision.py" in manifest.fingerprint
    ref = RegistryRef.parse(RefKind.COMPONENT, "lint-tool@1")
    resolved = ComponentResolver().resolve(ref, base_dir=Path("."))
    assert resolved.binding["runtimes"] == ["python"]
    assert resolved.metadata["discovery_probe"] == "lint-tool-installed"


@pytest.mark.parametrize(
    ("with_", "files", "message"),
    [
        ({"version": "0.0.1"}, (), "'with.version' must be one of"),
        ({"config": "sub/ruff.toml"}, (), "'with.config' must be a file name"),
        ({"tool": "black"}, (), "unknown 'with' keys"),
        ({}, (f"{PROBE_DIR}/ruff.toml",), "expects 0 file(s)"),
    ],
)
def test_invalid_lint_tool_uses_reject_the_arm(
    with_: dict[str, str], files: tuple[str, ...], message: str
) -> None:
    with pytest.raises(ComponentRejectedError, match=re.escape(message)):
        compile_uses(use("lint-tool@1", files, **with_))


def test_lint_tool_changes_no_workspace_or_harness_input_only_the_provisions() -> None:
    compiled = compile_uses(*WITH_CONFIG)
    baseline = compile_uses(WITH_CONFIG[0])

    assert dict(compiled.overlay) == dict(baseline.overlay)
    assert compiled.native_config == {}
    assert compiled.skills == ()
    assert compiled.network_egress == ("files.pythonhosted.org", "pypi.org")
    assert compiled.digest != baseline.digest
    assert condition_provisions(baseline) == ()
    (provision,) = condition_provisions(compiled)
    assert (provision.slug, provision.component) == (SLUG, "lint-tool@1")


def test_install_is_hash_pinned_cache_free_and_outside_the_workspace() -> None:
    install = LINT_TOOL.provision(_context()).install

    assert "--require-hashes" in install
    assert "--no-cache" in install
    assert "ruff==0.8.6" in install
    assert all(f"--hash=sha256:{digest}" in install for digest in RUFF_WHEEL_SHA256["0.8.6"])
    assert f"--target /opt/raidar-tools/{SLUG}/ruff-0.8.6" in install
    assert f"ln -sf /opt/raidar-tools/{SLUG}/ruff-0.8.6/bin/ruff /usr/local/bin/ruff" in install
    assert "/app" not in install


def test_check_reports_without_linting_or_writing_a_cache() -> None:
    check = LINT_TOOL.provision(_context()).check

    for flag in ("--show-files", "--no-cache", "--exit-zero"):
        assert flag in check
    assert parse_check_output("version=ruff 0.8.6\nnoise\nresolve=a=b\n") == {
        "version": "ruff 0.8.6",
        "resolve": "a=b",
    }


def test_probe_passes_on_the_pinned_tool_and_the_expected_config(tmp_path: Path) -> None:
    outcome = LINT_TOOL.probe(_context(), _evidence(tmp_path, _record(_check())), 0)

    assert outcome.passed, outcome.detail
    assert "config /app/ruff.toml" in outcome.detail


@pytest.mark.parametrize(
    ("record", "reason"),
    [
        (None, "no provision record"),
        (_record(None, install_ok=False), "install failed: network unreachable"),
        (_record({"ok": False, "error": "boom"}), "check failed: boom"),
        (_record(_check(version="ruff 0.9.0")), "pinned ruff answers 'ruff 0.9.0'"),
        (_record(_check(pinned_target="/home/agent/.local/bin/ruff")), "not the install"),
        (
            _record(_check(resolve=f"{RESOLVE_PREFIX} Using Ruff default settings")),
            "ruff resolves config None",
        ),
    ],
)
def test_probe_fails_when_the_tool_did_not_take_effect(
    tmp_path: Path, record: dict[str, Any] | None, reason: str
) -> None:
    evidence = _evidence(tmp_path, *([record] if record else []))

    outcome = LINT_TOOL.probe(_context(), evidence, 0)

    assert not outcome.passed
    assert reason in outcome.detail


def test_config_is_enforced_at_the_first_step_only(tmp_path: Path) -> None:
    moved = _record(_check(resolve=f"{RESOLVE_PREFIX} Using Ruff default settings"))

    later = LINT_TOOL.probe(_context(), _evidence(tmp_path, moved), 3)

    assert later.passed
    assert "config ruff defaults" in later.detail


def test_resolved_config_reads_the_ruff_resolve_line() -> None:
    line = "[ruff::resolve][DEBUG] Using configuration file (via parent) at: /app/ruff.toml"
    assert resolved_config(line) == "/app/ruff.toml"
    assert resolved_config(f"{RESOLVE_PREFIX} Using Ruff default settings") is None


class _FakeEnv:
    def __init__(self, *, install_rc: int = 0, raises_on: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.install_rc = install_rc
        self.raises_on = raises_on
        self.records: list[tuple[str, dict[str, Any]]] = []

    async def exec(self, command: str, user: str | None = None, timeout_sec: int = 0) -> Any:
        kind = "install" if command == "INSTALL" else "check" if command == "CHECK" else "record"
        self.calls.append({"kind": kind, "user": user})
        if kind == self.raises_on:
            raise RuntimeError("container gone")
        if kind == "record":
            encoded = command.split("printf %s ", 1)[1].split(" ", 1)[0]
            target = command.rsplit(">> ", 1)[1].split(" ", 1)[0]
            self.records.append((target, json.loads(base64.b64decode(encoded))))
            return SimpleNamespace(return_code=0, stdout="", stderr="")
        if kind == "install":
            return SimpleNamespace(return_code=self.install_rc, stdout="", stderr="pypi down")
        return SimpleNamespace(return_code=0, stdout="version=ruff 0.8.6\n", stderr="")


def _hooked(env: _FakeEnv) -> Any:
    hooks: dict[TrialEvent, list[Any]] = {}
    trial = SimpleNamespace(
        task=SimpleNamespace(config=SimpleNamespace(agent=SimpleNamespace(user="agent"))),
        agent_environment=env,
        add_hook=lambda event, hook: hooks.setdefault(event, []).append(hook),
    )
    install_provisioning(cast(Any, trial), [Provision(SLUG, "lint-tool@1", "INSTALL", "CHECK")])
    (hook,) = hooks[TrialEvent.AGENT_START]
    return hook


def _step(name: str) -> Any:
    return SimpleNamespace(result=SimpleNamespace(step_results=[SimpleNamespace(step_name=name)]))


def test_hook_installs_once_as_root_and_checks_every_step_as_the_agent_user() -> None:
    env = _FakeEnv()
    hook = _hooked(env)

    asyncio.run(hook(_step("checkpoint_1")))
    asyncio.run(hook(_step("checkpoint_2")))

    kinds = [(call["kind"], call["user"]) for call in env.calls]
    assert kinds == [
        ("install", "root"),
        ("check", "agent"),
        ("record", "root"),
        ("check", "agent"),
        ("record", "root"),
    ]
    first, second = (record for _, record in env.records)
    assert {target for target, _ in env.records} == {str(evidence_log(SLUG, PROVISION_CHANNEL))}
    assert (first["step"], first["install"]["ran_this_step"]) == ("checkpoint_1", True)
    assert (second["step"], second["install"]["ran_this_step"]) == ("checkpoint_2", False)
    assert second["install"]["ok"]
    assert second["check"]["values"] == {"version": "ruff 0.8.6"}


def test_a_failed_install_is_recorded_not_raised_and_skips_the_check() -> None:
    env = _FakeEnv(install_rc=1)
    hook = _hooked(env)

    asyncio.run(hook(_step("checkpoint_1")))

    (_, record) = env.records[0]
    assert record["install"]["ok"] is False
    assert "pypi down" in record["install"]["output"]
    assert record["check"] is None
    assert [call["kind"] for call in env.calls] == ["install", "record"]


def test_an_exec_failure_never_escapes_the_hook() -> None:
    env = _FakeEnv(raises_on="install")
    asyncio.run(_hooked(env)(_step("checkpoint_1")))
    assert "install exec failed: RuntimeError" in env.records[0][1]["install"]["output"]

    broken_record = _FakeEnv(raises_on="record")
    asyncio.run(_hooked(broken_record)(_step("checkpoint_1")))
    assert broken_record.records == []


def test_no_provisions_adds_no_hook() -> None:
    added: list[Any] = []
    trial = SimpleNamespace(add_hook=lambda *args: added.append(args))
    install_provisioning(cast(Any, trial), [])
    assert added == []


docker_trial = pytest.mark.skipif(
    not (docker_target_ready()),
    reason="needs a ready Docker target (data check passing) and the built python pack image",
)


@docker_trial
def test_pinned_ruff_is_installed_per_trial_and_probed_in_a_real_harbor_trial(
    tmp_path: Path,
) -> None:
    compiled = compile_uses(*WITH_CONFIG)
    task = tmp_path / "lint-tool"
    build_task(task, compiled, {"checkpoint_1": "step one", "checkpoint_2": "step two"})

    trial = run_trial_sync(
        task,
        tmp_path / "trials",
        AgentConfig(name="nop"),
        task.name,
        condition_provisions(compiled),
    )

    steps = trial / "steps"
    for index, step in enumerate(("checkpoint_1", "checkpoint_2")):
        report = compiled.probe_step(steps / step / "agent", index)
        assert report.passed, report.to_json()
        record = StepEvidence(steps / step / "agent").component_log(SLUG, PROVISION_CHANNEL)[-1]
        assert record["install"]["ran_this_step"] is (index == 0)
        assert record["check"]["values"]["path_ruff"] == "/usr/local/bin/ruff"
