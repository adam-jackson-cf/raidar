"""lint-tool@2: the pinned Ruff runnable as 'ruff', 'python -m ruff' and 'uv run ruff'.

Covers the contract (manifest, configuration, the install and check scripts), the
discovery probe over recorded evidence (every invocation enforced before the first
step's agent, reported after), the pre-agent provisioning hook (install once as
root, check every step as the agent user, never raise), and, on OrbStack, a real
two-step Harbor trial on the pack image with the nop agent.
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

from raidar.design.experiment.references import RefKind, RegistryRef
from raidar.subject.treatment import (
    ComponentKind,
    ComponentRegistry,
    ComponentRejectedError,
    ComponentResolver,
    StepEvidence,
)
from raidar.subject.treatment.catalog.tools import (
    INVOCATIONS,
    LINT_TOOL,
    RUFF_WHEEL_SHA256,
    invocation_results,
    resolved_config,
)
from raidar.subject.treatment.definition.contribution import evidence_log
from raidar.subject.treatment.definition.registry import component_runtimes
from raidar.subject.treatment.runtime.provision import (
    PROVISION_CHANNEL,
    Provision,
    install_provisioning,
    parse_check_output,
    trial_provisions,
)
from tests.component_support import PROBE_DIR, compile_uses, use, write_jsonl
from tests.component_trial_support import (
    build_task,
    docker_target_ready,
    run_trial_sync,
)

WITH_CONFIG = (
    use("lint-config@1", (f"{PROBE_DIR}/ruff.toml",)),
    use("lint-tool@2", config="ruff.toml"),
)
SLUG = "01-lint-tool"
INSTALL_DIR = f"/opt/raidar-tools/{SLUG}/ruff-0.8.6"
RESOLVE = "[ruff::resolve][DEBUG] Using configuration file (via parent) at: /app/ruff.toml"
LABELS = ["ruff", "python -m ruff", "python3 -m ruff", "uv run ruff"]


def _context() -> Any:
    return compile_uses(*WITH_CONFIG).uses[1].context


def _values(**overrides: str) -> dict[str, str]:
    values = {
        "workspace": "/app",
        "path_ruff": "/usr/local/bin/ruff",
        "pinned_target": f"{INSTALL_DIR}/bin/ruff",
        "version": "ruff 0.8.6",
        "resolve": RESOLVE,
        "uv_project": "none",
        "project_venv_ruff": "no",
    }
    for key, _ in INVOCATIONS:
        values |= {key: "ruff 0.8.6", f"{key}_rc": "0"}
    return values | overrides


def _evidence(tmp_path: Path, values: dict[str, str]) -> StepEvidence:
    agent_dir = tmp_path / "agent"
    record = {
        "kind": "raidar-provision",
        "slug": SLUG,
        "install": {"ok": True, "output": ""},
        "check": {"ok": True, "values": values},
    }
    write_jsonl(agent_dir / "raidar-components" / f"{SLUG}.{PROVISION_CHANNEL}.jsonl", [record])
    return StepEvidence(agent_dir)


def test_lint_tool_is_a_python_service_component_with_declared_egress() -> None:
    definition = ComponentRegistry().get("lint-tool@2")
    manifest = definition.manifest

    assert definition is LINT_TOOL
    assert manifest.kind is ComponentKind.SERVICE
    assert component_runtimes(definition) == ("python",)
    assert manifest.network.egress_hosts == ("files.pythonhosted.org", "pypi.org")
    assert manifest.discovery_probe.id == "lint-tool-invocable"
    assert "raidar/subject/treatment/runtime/provision.py" in manifest.fingerprint
    ref = RegistryRef.parse(RefKind.COMPONENT, "lint-tool@2")
    resolved = ComponentResolver().resolve(ref, base_dir=Path("."))
    assert resolved.binding["runtimes"] == ["python"]
    assert resolved.metadata["discovery_probe"] == "lint-tool-invocable"


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
        compile_uses(use("lint-tool@2", files, **with_))


def test_lint_tool_changes_no_workspace_or_harness_input_only_the_provisions() -> None:
    compiled = compile_uses(*WITH_CONFIG)
    baseline = compile_uses(WITH_CONFIG[0])

    assert dict(compiled.overlay) == dict(baseline.overlay)
    assert compiled.native_config == {}
    assert compiled.skills == ()
    assert compiled.network_egress == ("files.pythonhosted.org", "pypi.org")
    assert compiled.digest != baseline.digest
    assert trial_provisions(baseline) == ()
    (provision,) = trial_provisions(compiled)
    assert (provision.slug, provision.component) == (SLUG, "lint-tool@2")


def test_install_is_hash_pinned_cache_free_and_outside_the_workspace() -> None:
    install = LINT_TOOL.provision(_context()).install

    assert "--require-hashes" in install
    assert "--no-cache" in install
    assert "ruff==0.8.6" in install
    assert all(f"--hash=sha256:{digest}" in install for digest in RUFF_WHEEL_SHA256["0.8.6"])
    assert f"--target {INSTALL_DIR}" in install
    assert f"ln -sf {INSTALL_DIR}/bin/ruff /usr/local/bin/ruff" in install
    assert "/app" not in install


def test_install_adds_a_root_owned_pth_per_interpreter() -> None:
    install = LINT_TOOL.provision(_context()).install

    assert "for py in python3 python; do" in install
    assert 'sysconfig.get_path("purelib")' in install
    assert f"printf '%s\\n' {INSTALL_DIR} > \"$site/\"raidar-{SLUG}-ruff.pth" in install
    assert '"$bin" -m ruff --version' in install


def test_check_runs_every_invocation_without_linting_or_writing_to_the_workspace() -> None:
    check = LINT_TOOL.provision(_context()).check

    for flag in ("--show-files", "--no-cache", "--exit-zero"):
        assert flag in check
    for line in (
        "invoke ruff ruff",
        "invoke python_m python -m ruff",
        "invoke python3_m python3 -m ruff",
    ):
        assert line in check
    assert 'invoke uv_run env UV_PROJECT_ENVIRONMENT="$uv_env/venv" uv run --no-sync' in check
    assert "echo uv_project=uv.lock" in check
    assert parse_check_output("version=ruff 0.8.6\nnoise\nresolve=a=b\n") == {
        "version": "ruff 0.8.6",
        "resolve": "a=b",
    }


def test_invocation_results_mark_only_the_pinned_answers() -> None:
    values = _values(python_m="", python_m_rc="1", python_m_error="No module named ruff ")
    values |= {"uv_run": "ruff 0.9.0"}

    results = invocation_results(values, "0.8.6")

    assert [result.label for result in results] == LABELS
    assert [result.pinned for result in results] == [True, False, True, False]
    assert results[1].describe() == "python -m ruff -> 'No module named ruff' (exit 1)"


def test_probe_passes_and_records_every_usable_invocation(tmp_path: Path) -> None:
    outcome = LINT_TOOL.probe(_context(), _evidence(tmp_path, _values()), 0)

    assert outcome.passed, outcome.detail
    assert f"runs the pin: {', '.join(LABELS)}" in outcome.detail
    assert "config /app/ruff.toml" in outcome.detail
    assert outcome.evidence[-1] == ",".join(LABELS)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (
            {"python_m": "", "python_m_rc": "1", "python_m_error": "No module named ruff"},
            "python -m ruff -> 'No module named ruff' (exit 1)",
        ),
        ({"python3_m": "ruff 0.9.0"}, "python3 -m ruff -> 'ruff 0.9.0' (exit 0)"),
        ({"uv_run_rc": "2", "uv_run": "", "uv_run_error": "bad toml"}, "uv run ruff -> 'bad toml'"),
        ({"ruff_rc": "127", "ruff": ""}, "ruff -> 'no output' (exit 127)"),
        ({"path_ruff": "/home/agent/.local/bin/ruff"}, "resolves on PATH to"),
        ({"resolve": "[ruff::resolve][DEBUG] Using Ruff default settings"}, "config None"),
        ({"version": "ruff 0.9.0"}, "pinned ruff answers 'ruff 0.9.0'"),
    ],
)
def test_probe_fails_at_the_first_step_unless_every_invocation_runs_the_pin(
    tmp_path: Path, overrides: dict[str, str], reason: str
) -> None:
    outcome = LINT_TOOL.probe(_context(), _evidence(tmp_path, _values(**overrides)), 0)

    assert not outcome.passed
    assert reason in outcome.detail


def test_later_steps_report_agent_changes_without_failing(tmp_path: Path) -> None:
    shadowed = _values(
        path_ruff="/home/agent/.local/bin/ruff",
        ruff="ruff 0.9.0",
        python_m="ruff 0.9.0",
        resolve="[ruff::resolve][DEBUG] Using Ruff default settings",
        uv_project="uv.lock",
        project_venv_ruff="yes",
    )

    outcome = LINT_TOOL.probe(_context(), _evidence(tmp_path, shadowed), 2)

    assert outcome.passed, outcome.detail
    assert "runs the pin: python3 -m ruff, uv run ruff" in outcome.detail
    assert "not the pin: ruff -> 'ruff 0.9.0' (exit 0); python -m ruff" in outcome.detail
    assert "uv project uv.lock (project .venv ruff: yes)" in outcome.detail


def test_later_steps_still_require_the_provisioned_install(tmp_path: Path) -> None:
    moved = _values(pinned_target="/tmp/ruff")

    outcome = LINT_TOOL.probe(_context(), _evidence(tmp_path, moved), 3)

    assert not outcome.passed
    assert "not the install" in outcome.detail


def test_probe_without_a_record_or_with_a_failed_check_fails(tmp_path: Path) -> None:
    (tmp_path / "agent").mkdir()
    missing = LINT_TOOL.probe(_context(), StepEvidence(tmp_path / "agent"), 0)
    assert "no provision record" in missing.detail

    agent_dir = tmp_path / "failed"
    record = {"slug": SLUG, "install": {"ok": True}, "check": {"ok": False, "error": "boom"}}
    write_jsonl(agent_dir / "raidar-components" / f"{SLUG}.{PROVISION_CHANNEL}.jsonl", [record])
    failed = LINT_TOOL.probe(_context(), StepEvidence(agent_dir), 0)
    assert (failed.passed, failed.detail) == (False, "check failed: boom")


def test_resolved_config_reads_the_ruff_resolve_line() -> None:
    assert resolved_config(RESOLVE) == "/app/ruff.toml"
    assert resolved_config("[ruff::resolve][DEBUG] Using Ruff default settings") is None


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
    install_provisioning(cast(Any, trial), [Provision(SLUG, "lint-tool@2", "INSTALL", "CHECK")])
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
def test_every_invocation_runs_the_pin_in_a_real_two_step_harbor_trial(tmp_path: Path) -> None:
    compiled = compile_uses(*WITH_CONFIG)
    task = tmp_path / "lint-tool"
    build_task(task, compiled, {"checkpoint_1": "step one", "checkpoint_2": "step two"})

    trial = run_trial_sync(
        task,
        tmp_path / "trials",
        AgentConfig(name="nop"),
        task.name,
        trial_provisions(compiled),
    )

    steps = trial / "steps"
    for index, step in enumerate(("checkpoint_1", "checkpoint_2")):
        report = compiled.probe_step(steps / step / "agent", index)
        assert report.passed, report.to_json()
        record = StepEvidence(steps / step / "agent").component_log(SLUG, PROVISION_CHANNEL)[-1]
        assert record["install"]["ran_this_step"] is (index == 0)
        values = record["check"]["values"]
        results = invocation_results(values, "0.8.6")
        assert all(result.pinned for result in results), values
        assert values["path_ruff"] == "/usr/local/bin/ruff"
        assert values["uv_project"] == "none"
        assert values["project_venv_ruff"] == "no"
