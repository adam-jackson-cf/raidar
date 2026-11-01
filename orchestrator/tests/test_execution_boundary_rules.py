"""V2/V5 reject new install paths and unrecorded container transports."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/checks/check-python-quality.py"
spec = importlib.util.spec_from_file_location("execution_boundary_rules", SCRIPT)
assert spec is not None
assert spec.loader is not None
rules = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rules
spec.loader.exec_module(rules)
PREFIX = "orchestrator/src/raidar/"


@pytest.mark.parametrize(
    "source",
    [
        'command = "uv pip install example"',
        'command = "python -m pip install example"',
        'argv = ["uv", "sync", "--offline"]',
        'argv = ["pip", "install", "example"]',
    ],
)
def test_install_commands_are_refused_outside_owned_installers(source: str) -> None:
    found = rules.execution_boundary_diagnostics_for_source(
        source, PREFIX + "subject/workflow/new.py"
    )
    assert {item.code for item in found} == {"PYQ300"}


def test_install_ownership_does_not_ban_documentation_or_trusted_installer() -> None:
    prose = '"""Use uv pip install inside the container."""'
    assert (
        rules.execution_boundary_diagnostics_for_source(prose, PREFIX + "subject/workflow/new.py")
        == []
    )
    assert (
        rules.execution_boundary_diagnostics_for_source(
            'command = "uv pip install --offline example"', PREFIX + "subject/primitive/install.py"
        )
        == []
    )


@pytest.mark.parametrize("operation", ["exec", "upload_file"])
@pytest.mark.parametrize("module", ["subject/workflow/new.py", "subject/primitive/new.py"])
def test_container_transport_cannot_bypass_call_boundary(operation: str, module: str) -> None:
    source = f"async def run(environment):\n    await environment.{operation}('payload')\n"
    found = rules.execution_boundary_diagnostics_for_source(source, PREFIX + module)
    assert [(item.code, item.line) for item in found] == [("PYQ301", 2)]


def test_call_boundary_and_its_consumers_are_allowed() -> None:
    assert (
        rules.execution_boundary_diagnostics_for_source(
            "await environment.exec(command)", PREFIX + "subject/primitive/calls.py"
        )
        == []
    )
    assert (
        rules.execution_boundary_diagnostics_for_source(
            "await calls.upload_file(environment, source, target)",
            PREFIX + "subject/workflow/new.py",
        )
        == []
    )
    assert (
        rules.execution_boundary_diagnostics_for_source(
            "await calls.trusted_exec(environment, command)", PREFIX + "subject/workflow/new.py"
        )
        == []
    )


@pytest.mark.parametrize(
    "prefix",
    [
        "orchestrator/tests/fixtures/stages/acme/third-party-check/tool/src/",
        "lab/stages/third-party/docs-check/tool/src/",
        "lab/stages/helpers/common/tool/src/",
    ],
)
def test_stage_data_names_do_not_relax_python_module_names(prefix: str) -> None:
    assert rules.structure_diagnostics_for_relpath(prefix + "checker/main.py") == []
    found = rules.structure_diagnostics_for_relpath(prefix + "bad-module/main.py")
    assert [item.code for item in found] == ["PYQ105"]
    found = rules.structure_diagnostics_for_relpath(prefix + "helpers/main.py")
    assert [item.code for item in found] == ["PYQ103"]
