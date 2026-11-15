"""Fast behavioural proofs for the test-integrity policy."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEST = "orchestrator/tests/test_probe.py"
PRODUCTION = "orchestrator/src/raidar/probe.py"
ADR = "docs/adr/adr-001-probe.md"


class Finding(Protocol):
    path: str
    rule: str

    def render(self) -> str: ...


class Checker(Protocol):
    def findings(self, root: Path, inventory: Sequence[str]) -> list[Finding]: ...


@pytest.fixture
def checker() -> Checker:
    spec = importlib.util.spec_from_file_location(
        "test_integrity_proof", ROOT / "scripts/checks/check-test-integrity.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("Checker", module)


def run(
    root: Path, checker: Checker, files: dict[str, str], waivers: Sequence[dict[str, str]] = ()
) -> list[str]:
    tree = {
        "scripts/checks/test-integrity-exceptions.json": json.dumps({"exceptions": list(waivers)}),
        ADR: "# ADR-001\n",
        **files,
    }
    for relative, text in tree.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    inventory = sorted(path for path in tree if path.endswith(".py"))
    return [item.render() for item in checker.findings(root, inventory)]


def assert_rule(found: list[str], path: str, rule: str) -> None:
    assert found
    assert all(line.startswith(f"{path}: {rule}: line ") for line in found), found


@pytest.mark.parametrize(
    "source",
    [
        "from unittest.mock import patch\npatch('raidar.probe.clock')\n",
        "from unittest import mock\nimport raidar.probe\n"
        "mock.patch.object(raidar.probe, 'clock')\n",
        "from raidar import probe\ndef test_x(monkeypatch):\n"
        "    monkeypatch.setattr(probe, 'clock', None)\n",
        "from raidar.probe import Clock\nfrom unittest.mock import MagicMock\n"
        "MagicMock(spec=Clock)\n",
        "import raidar.probe as probe\nprobe.clock = None\n",
    ],
)
def test_tests_patching_owned_code_are_rejected(
    tmp_path: Path, checker: Checker, source: str
) -> None:
    assert_rule(run(tmp_path, checker, {TEST: source}), TEST, "TEST.no-self-mocking")


def test_tests_faking_only_third_party_boundaries_are_accepted(
    tmp_path: Path, checker: Checker
) -> None:
    source = (
        "import subprocess\nfrom unittest.mock import MagicMock, patch\n"
        "patch('subprocess.run')\nMagicMock(spec=subprocess.Popen)\n"
        "def test_x(monkeypatch):\n    monkeypatch.setattr(subprocess, 'run', None)\n"
        "class Box:\n    pass\nBox().value = 1\n"
    )
    assert run(tmp_path, checker, {TEST: source}) == []


@pytest.mark.parametrize(
    "source",
    [
        "import pytest\n@pytest.mark.skip(reason='later')\ndef test_x(): pass\n",
        "import pytest\n@pytest.mark.skipif(True, reason='host')\ndef test_x(): pass\n",
        "import unittest\nclass T(unittest.TestCase):\n"
        "    @unittest.expectedFailure\n    def test_x(self): pass\n",
        "import pytest\ndef test_x():\n    pytest.skip('host')\n",
        "import pytest\npytest.importorskip('numpy')\n",
        "import pytest\npytestmark = [pytest.mark.slow, pytest.mark.xfail]\n",
    ],
)
def test_tests_that_skip_or_expect_failure_are_rejected(
    tmp_path: Path, checker: Checker, source: str
) -> None:
    assert_rule(run(tmp_path, checker, {TEST: source}), TEST, "TEST.no-evasion")


def test_tests_that_run_unconditionally_are_accepted(tmp_path: Path, checker: Checker) -> None:
    source = (
        "import pytest\npytestmark = pytest.mark.slow\n"
        "@pytest.mark.parametrize('x', [1])\ndef test_x(x): assert x\n"
    )
    assert run(tmp_path, checker, {TEST: source}) == []


@pytest.mark.parametrize(
    "decorators",
    ["@given(st.integers())", "@hypothesis.given(st.integers())\n@settings(max_examples=5)"],
)
def test_property_tests_without_a_fixed_or_reported_seed_are_rejected(
    tmp_path: Path, checker: Checker, decorators: str
) -> None:
    source = (
        "import hypothesis\nfrom hypothesis import given, settings\n"
        f"{decorators}\ndef test_x(n): pass\n"
    )
    assert_rule(run(tmp_path, checker, {TEST: source}), TEST, "TEST.property-seed")


@pytest.mark.parametrize(
    "decorator", ["@seed(7)", "@settings(print_blob=True)", "@settings(derandomize=True)"]
)
def test_property_tests_with_a_fixed_or_reported_seed_are_accepted(
    tmp_path: Path, checker: Checker, decorator: str
) -> None:
    source = (
        "from hypothesis import given, seed, settings\n"
        f"@given(st.integers())\n{decorator}\ndef test_x(n): pass\n"
    )
    assert run(tmp_path, checker, {TEST: source}) == []


@pytest.mark.parametrize(
    ("production", "test"),
    [
        ("import os\nMODE = os.environ.get('PYTEST_CURRENT_TEST')\n", ""),
        ("RUNNER = 'pytest'\n", ""),
        ("def reset_for_testing():\n    pass\n", ""),
        (
            "def run():\n    return 1\n\ndef peek():\n    return 2\n",
            "from raidar.probe import peek\ndef test_x(): assert peek()\n",
        ),
    ],
)
def test_production_test_hooks_are_rejected(
    tmp_path: Path, checker: Checker, production: str, test: str
) -> None:
    found = run(tmp_path, checker, {PRODUCTION: production, TEST: test})
    assert_rule(found, PRODUCTION, "TEST.no-test-hooks")


def test_production_symbols_with_a_production_caller_are_accepted(
    tmp_path: Path, checker: Checker
) -> None:
    files = {
        PRODUCTION: '"""Wraps pytest."""\ndef peek():\n    return 2\n',
        "orchestrator/src/raidar/cli.py": "from raidar.probe import peek\n"
        "def main():\n    return peek()\n",
        TEST: "from raidar.cli import main\nfrom raidar.probe import peek\n"
        "def test_x(): assert main() == peek()\n",
    }
    assert run(tmp_path, checker, files) == []


@pytest.mark.parametrize(
    "files",
    [
        {
            "orchestrator/src/raidar/engine.py": "PLUGIN = 'raidar.probe:Peek'\n",
        },
        {
            "orchestrator/pyproject.toml": "[project]\nname = 'raidar'\n"
            "[project.entry-points.'raidar.plugins']\npeek = 'raidar.probe:Peek'\n",
        },
        {
            "orchestrator/pyproject.toml": "[project]\nname = 'raidar'\n"
            "[project.scripts]\npeek = 'raidar.probe:Peek'\n",
        },
    ],
    ids=["module-symbol-string", "entry-point", "script"],
)
def test_symbols_a_third_party_loads_by_reference_are_accepted(
    tmp_path: Path, checker: Checker, files: dict[str, str]
) -> None:
    production = {PRODUCTION: "class Peek:\n    pass\n"}
    test = {TEST: "from raidar.probe import Peek\ndef test_x(): assert Peek()\n"}
    assert run(tmp_path, checker, {**production, **test, **files}) == []


def test_a_reference_to_another_module_or_symbol_does_not_excuse_a_test_only_symbol(
    tmp_path: Path, checker: Checker
) -> None:
    files = {
        PRODUCTION: "class Peek:\n    pass\n",
        "orchestrator/src/raidar/engine.py": "A = 'raidar.other:Peek'\nB = 'raidar.probe:Look'\n",
        TEST: "from raidar.probe import Peek\ndef test_x(): assert Peek()\n",
    }
    assert_rule(run(tmp_path, checker, files), PRODUCTION, "TEST.no-test-hooks")


def test_click_registered_commands_are_accepted_and_plain_decorators_are_not(
    tmp_path: Path, checker: Checker
) -> None:
    production = (
        "import click\n@click.group()\ndef cli():\n    pass\n\n"
        "@cli.command('peek')\ndef peek():\n    pass\n"
    )
    test = "from raidar.probe import cli, peek\ndef test_x(): assert cli and peek\n"
    assert run(tmp_path, checker, {PRODUCTION: production, TEST: test}) == []
    cached = "import functools\n@functools.cache\ndef peek():\n    pass\n"
    found = run(tmp_path, checker, {PRODUCTION: cached, TEST: test})
    assert_rule(found, PRODUCTION, "TEST.no-test-hooks")


def test_exceptions_waive_their_path_and_rule_and_stale_ones_are_rejected(
    tmp_path: Path, checker: Checker
) -> None:
    files = {TEST: "import pytest\npytest.skip('host')\n"}
    waiver = {"path": "orchestrator/tests/test_*.py", "rule": "TEST.no-evasion", "adr": ADR}
    assert run(tmp_path, checker, files, [waiver]) == []
    stale = {**waiver, "rule": "TEST.property-seed"}
    missing = {**waiver, "adr": "docs/adr/adr-404.md §1"}
    found = run(tmp_path, checker, files, [waiver, stale, missing])
    assert found == [
        "scripts/checks/test-integrity-exceptions.json: TEST.exceptions: entry 2: exception for "
        "orchestrator/tests/test_*.py waives no TEST.property-seed finding; "
        "remove or correct the entry",
        "scripts/checks/test-integrity-exceptions.json: TEST.exceptions: entry 3: exception for "
        "orchestrator/tests/test_*.py names missing ADR docs/adr/adr-404.md §1; "
        "remove or correct the entry",
    ]


def test_fixture_programs_are_never_judged(tmp_path: Path, checker: Checker) -> None:
    program = "lab/stages/raidar/verify/fixtures/refuse/state/raidar/app.py"
    assert run(tmp_path, checker, {program: "RUNNER = 'pytest'\n"}) == []
