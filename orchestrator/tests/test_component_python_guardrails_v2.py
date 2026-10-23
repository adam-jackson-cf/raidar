"""python-guardrails@2: python-guardrails@1's pack with the workspace config copies optional.

``workspace_configs: true`` (the default) is @1 exactly; ``false`` installs the
same payload and puts nothing in the workspace, so a shadow gate can check a
baseline arm whose agent must not see the gate or its configs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from raidar.components import ComponentRegistry, ComponentRejectedError
from raidar.components.catalog.guardrails import (
    PAYLOAD_FILES,
    PAYLOAD_PACKAGE_DIR,
    PYTHON_GUARDRAILS,
    WORKSPACE_CONFIGS,
    payload_bytes,
)
from raidar.components.catalog.guardrails_v2 import PYTHON_GUARDRAILS_V2
from raidar.components.definition.evidence import StepEvidence
from raidar.components.definition.registry import fingerprint
from raidar.components.runtime.provision import condition_provisions
from tests.component_support import compile_uses, use
from tests.test_component_python_guardrails import (
    SLUG,
    _agent_dir,
    _report,
    _trajectory,
    _values,
    _write_report,
)

REF = "python-guardrails@2"
NO_COPIES = use(REF, workspace_configs=False)


def _context(**with_: object) -> object:
    return compile_uses(use(REF, **with_)).uses[0].context


def test_v2_fingerprints_the_same_untouched_payload_and_leaves_v1_frozen() -> None:
    registry = ComponentRegistry()
    manifest = registry.get(REF).manifest

    for relative in PAYLOAD_FILES:
        assert f"{PAYLOAD_PACKAGE_DIR}/{relative}" in manifest.fingerprint
    assert "raidar/components/catalog/guardrails.py" in manifest.fingerprint
    assert "raidar/components/catalog/guardrails_v2.py" in manifest.fingerprint
    assert "raidar/components/catalog/guardrails_v2.py" not in (
        PYTHON_GUARDRAILS.manifest.fingerprint
    )
    assert fingerprint(registry.get(REF)) != fingerprint(registry.get("python-guardrails@1"))


def test_the_default_is_v1_exactly_and_false_puts_nothing_in_the_workspace() -> None:
    default = compile_uses(use(REF))
    copies = compile_uses(use(REF, workspace_configs=True))
    hidden = compile_uses(NO_COPIES)
    v1 = compile_uses(use("python-guardrails@1"))

    assert dict(default.overlay) == dict(copies.overlay) == dict(v1.overlay)
    assert dict(default.overlay) == {
        name: payload_bytes(f"config/{name}") for name in WORKSPACE_CONFIGS
    }
    assert dict(hidden.overlay) == {}
    assert set(hidden.support_files) == set(v1.support_files)
    (provision,) = condition_provisions(hidden)
    assert (provision.slug, provision.component) == (SLUG, REF)
    assert provision.install == PYTHON_GUARDRAILS.provision(_context()).install  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("with_", "message"),
    [
        ({"workspace_configs": "no"}, "'with.workspace_configs' must be true or false"),
        ({"policy": "warn"}, "unknown 'with' keys"),
    ],
)
def test_invalid_uses_reject_the_arm(with_: dict[str, object], message: str) -> None:
    with pytest.raises(ComponentRejectedError, match=re.escape(message)):
        compile_uses(use(REF, **with_))


def _missing() -> dict[str, str]:
    return {f"workspace_{name.replace('.', '_')}": "missing" for name in WORKSPACE_CONFIGS}


def test_without_copies_step_one_requires_them_absent(tmp_path: Path) -> None:
    context = _context(workspace_configs=False)
    clean = _agent_dir(tmp_path / "clean", _values(**_missing()), workspace_files={})

    outcome = PYTHON_GUARDRAILS_V2.probe(context, StepEvidence(clean), 0)  # type: ignore[arg-type]

    assert outcome.passed, outcome.detail
    assert "no workspace copies" in outcome.detail
    planted = _agent_dir(tmp_path / "planted", _values(), workspace_files={})
    refused = PYTHON_GUARDRAILS_V2.probe(context, StepEvidence(planted), 0)  # type: ignore[arg-type]
    assert not refused.passed
    assert "workspace ruff.toml present at step start without workspace copies" in refused.detail
    unprotected = _agent_dir(
        tmp_path / "open", _values(home_writable="/opt", **_missing()), workspace_files={}
    )
    assert not PYTHON_GUARDRAILS_V2.probe(context, StepEvidence(unprotected), 0).passed  # type: ignore[arg-type]


def test_with_copies_the_probe_is_v1s(tmp_path: Path) -> None:
    agent_dir = _agent_dir(tmp_path, _values())

    outcome = PYTHON_GUARDRAILS_V2.probe(_context(), StepEvidence(agent_dir), 0)  # type: ignore[arg-type]

    assert outcome.passed, outcome.detail
    missing = _agent_dir(tmp_path / "m", _values(**_missing()), workspace_files={})
    assert not PYTHON_GUARDRAILS_V2.probe(_context(), StepEvidence(missing), 0).passed  # type: ignore[arg-type]


def test_without_copies_only_protection_flags_are_raised(tmp_path: Path) -> None:
    context = _context(workspace_configs=False)
    values = _values(home_writable="/opt/raidar-tools/python-guardrails", **_missing())
    agent_dir = _agent_dir(tmp_path, values, workspace_files={})
    _write_report(agent_dir, 1, _report({"ruff.toml": "missing"}, {"SUPPRESS": 2}))
    _trajectory(agent_dir, "git commit --no-verify -m x")

    flags = PYTHON_GUARDRAILS_V2.step_flags(context, StepEvidence(agent_dir), 1)  # type: ignore[arg-type]

    assert flags == ("protected-tree-writable: /opt/raidar-tools/python-guardrails",)
    with_copies = PYTHON_GUARDRAILS_V2.step_flags(_context(), StepEvidence(agent_dir), 1)  # type: ignore[arg-type]
    assert any(flag.startswith("workspace-config-modified") for flag in with_copies)
    assert any(flag.startswith("suppression-refused") for flag in with_copies)
    assert any(flag.startswith("hook-bypass-attempt: git-no-verify") for flag in with_copies)
