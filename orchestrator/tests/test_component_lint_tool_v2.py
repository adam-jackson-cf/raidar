"""lint-tool@2: the pinned Ruff runnable as 'ruff', 'python -m ruff' and 'uv run ruff'.

Covers the contract (registration, @1 left as it was, the install and check
scripts), the discovery probe over recorded evidence (every invocation enforced
before the first step's agent, reported after), and, on OrbStack, a real
two-step Harbor trial on the pack image with the nop agent.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from harbor.models.trial.config import AgentConfig

from raidar.components import ComponentRegistry, ComponentRejectedError, StepEvidence
from raidar.components.builtins.tools import LINT_TOOL
from raidar.components.builtins.tools_v2 import INVOCATIONS, LINT_TOOL_V2, invocation_results
from raidar.components.provision import PROVISION_CHANNEL, condition_provisions
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


def test_lint_tool_v2_registers_beside_an_unchanged_v1() -> None:
    registry = ComponentRegistry()
    v1, v2 = registry.get("lint-tool@1"), registry.get("lint-tool@2")

    assert v1 is LINT_TOOL
    assert v2 is LINT_TOOL_V2
    assert v2.manifest.kind is v1.manifest.kind
    assert v2.manifest.network == v1.manifest.network
    assert v2.manifest.discovery_probe.id == "lint-tool-invocable"
    assert set(v1.manifest.fingerprint) < set(v2.manifest.fingerprint)
    assert "raidar/components/builtins/tools_v2.py" not in v1.manifest.fingerprint
    assert (
        compile_uses(*WITH_CONFIG).digest
        != compile_uses(WITH_CONFIG[0], use("lint-tool@1", config="ruff.toml")).digest
    )


def test_lint_tool_v2_takes_v1_configuration() -> None:
    with pytest.raises(ComponentRejectedError, match=re.escape("'with.version' must be one of")):
        compile_uses(use("lint-tool@2", version="0.0.1"))
    (provision,) = condition_provisions(compile_uses(*WITH_CONFIG))
    assert (provision.slug, provision.component) == (SLUG, "lint-tool@2")


def test_install_extends_v1_with_a_root_owned_pth_per_interpreter() -> None:
    v1 = LINT_TOOL.provision(_context())
    install = LINT_TOOL_V2.provision(_context()).install

    assert install.startswith(v1.install)
    assert "for py in python3 python; do" in install
    assert 'sysconfig.get_path("purelib")' in install
    assert f"printf '%s\\n' {INSTALL_DIR} > \"$site/\"raidar-{SLUG}-ruff.pth" in install
    assert '"$bin" -m ruff --version' in install
    assert "/app" not in install


def test_check_runs_every_invocation_without_writing_to_the_workspace() -> None:
    v1 = LINT_TOOL.provision(_context())
    check = LINT_TOOL_V2.provision(_context()).check

    assert check.startswith(v1.check)
    for line in (
        "invoke ruff ruff",
        "invoke python_m python -m ruff",
        "invoke python3_m python3 -m ruff",
    ):
        assert line in check
    assert 'invoke uv_run env UV_PROJECT_ENVIRONMENT="$uv_env/venv" uv run --no-sync' in check
    assert "echo uv_project=uv.lock" in check


def test_invocation_results_mark_only_the_pinned_answers() -> None:
    values = _values(python_m="", python_m_rc="1", python_m_error="No module named ruff ")
    values |= {"uv_run": "ruff 0.9.0"}

    results = invocation_results(values, "0.8.6")

    assert [result.label for result in results] == LABELS
    assert [result.pinned for result in results] == [True, False, True, False]
    assert results[1].describe() == "python -m ruff -> 'No module named ruff' (exit 1)"


def test_probe_passes_and_records_every_usable_invocation(tmp_path: Path) -> None:
    outcome = LINT_TOOL_V2.probe(_context(), _evidence(tmp_path, _values()), 0)

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
    outcome = LINT_TOOL_V2.probe(_context(), _evidence(tmp_path, _values(**overrides)), 0)

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

    outcome = LINT_TOOL_V2.probe(_context(), _evidence(tmp_path, shadowed), 2)

    assert outcome.passed, outcome.detail
    assert "runs the pin: python3 -m ruff, uv run ruff" in outcome.detail
    assert "not the pin: ruff -> 'ruff 0.9.0' (exit 0); python -m ruff" in outcome.detail
    assert "uv project uv.lock (project .venv ruff: yes)" in outcome.detail


def test_later_steps_still_require_the_provisioned_install(tmp_path: Path) -> None:
    moved = _values(pinned_target="/tmp/ruff")

    outcome = LINT_TOOL_V2.probe(_context(), _evidence(tmp_path, moved), 3)

    assert not outcome.passed
    assert "not the install" in outcome.detail


def test_probe_without_a_record_or_with_a_failed_check_fails(tmp_path: Path) -> None:
    (tmp_path / "agent").mkdir()
    missing = LINT_TOOL_V2.probe(_context(), StepEvidence(tmp_path / "agent"), 0)
    assert "no provision record" in missing.detail

    agent_dir = tmp_path / "failed"
    record = {"slug": SLUG, "install": {"ok": True}, "check": {"ok": False, "error": "boom"}}
    write_jsonl(agent_dir / "raidar-components" / f"{SLUG}.{PROVISION_CHANNEL}.jsonl", [record])
    failed = LINT_TOOL_V2.probe(_context(), StepEvidence(agent_dir), 0)
    assert (failed.passed, failed.detail) == (False, "check failed: boom")


docker_trial = pytest.mark.skipif(
    not (docker_target_ready()),
    reason="needs a ready Docker target (data check passing) and the built python pack image",
)


@docker_trial
def test_every_invocation_runs_the_pin_in_a_real_two_step_harbor_trial(tmp_path: Path) -> None:
    compiled = compile_uses(*WITH_CONFIG)
    task = tmp_path / "lint-tool-v2"
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
        values = record["check"]["values"]
        results = invocation_results(values, "0.8.6")
        assert all(result.pinned for result in results), values
        assert values["path_ruff"] == "/usr/local/bin/ruff"
        assert values["uv_project"] == "none"
        assert values["project_venv_ruff"] == "no"
