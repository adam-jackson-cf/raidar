"""Fast behavioural proofs for the per-path repository policy."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import NoReturn

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_script(relative: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


paths = _load_script("scripts/checks/check-repository-paths.py", "repository_gate_paths")


def _unexpected_run(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("This input must not launch a process")


def _assert_finding(line: str, path: str, rule: str) -> None:
    assert re.fullmatch(rf"{re.escape(path)}: {re.escape(rule)}: [^\r\n;]+; [^\r\n]+", line)


def test_path_policy_accepts_owned_paths(tmp_path: Path) -> None:
    sources = {
        "README.md": "# Repository\n",
        "orchestrator/src/raidar/engine/trial/probe.py": "VALUE = 1\n",
        "orchestrator/tests/test_repository_gate_rules.py": "def test_probe():\n    assert True\n",
    }
    for relative, source in sources.items():
        candidate = tmp_path / relative
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(source)
    assert paths.path_findings(tmp_path, list(sources)) == []


@pytest.mark.parametrize(
    ("relative", "source", "rules"),
    [
        ("notes.txt", "misplaced\n", ("STRUCT.folder-placement",)),
        (
            "orchestrator/src/raidar/engine/trial/v2/probe.py",
            "VALUE = 1\n",
            ("STRUCT.folder-placement", "NAME.conventions"),
        ),
        (
            "orchestrator/src/raidar/engine/trial/probe.py",
            "def test_probe():\n    pass\n",
            ("TEST.placement",),
        ),
        (
            "orchestrator/tests/test_repository_gate_rules.py",
            "import pytest\n\n@pytest.mark.slow\ndef test_probe():\n    pass\n",
            ("TEST.placement",),
        ),
        ("orchestrator/src/raidar/engine/trial/probe.py", "def broken(\n", ("PYS000",)),
    ],
)
def test_path_policy_reports_actionable_rule_findings(
    tmp_path: Path, relative: str, source: str, rules: tuple[str, ...]
) -> None:
    candidate = tmp_path / relative
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(source)
    found = paths.path_findings(tmp_path, [relative])
    assert len(found) == len(rules)
    for line, rule in zip(found, rules, strict=True):
        _assert_finding(line, relative, rule)


@pytest.mark.parametrize("relative", ["/outside.py", "../outside.py", "orchestrator/../outside.py"])
def test_path_policy_refuses_paths_outside_the_repository(tmp_path: Path, relative: str) -> None:
    found = paths.path_findings(tmp_path, [relative])
    assert len(found) == 1
    _assert_finding(found[0], relative, "STRUCT.folder-placement")


@pytest.mark.parametrize("relative", ["docs/v2/test_probe.py", "var/v2/test_probe.py"])
def test_path_policy_skips_documentation_and_generated_files(tmp_path: Path, relative: str) -> None:
    candidate = tmp_path / relative
    candidate.parent.mkdir(parents=True)
    candidate.write_text("def broken(\n")
    assert paths.path_findings(tmp_path, [relative]) == []


def test_stage_fixture_programs_are_not_tests(tmp_path: Path) -> None:
    relative = "lab/stages/raidar/define/fixtures/program.py"
    candidate = tmp_path / relative
    candidate.parent.mkdir(parents=True)
    candidate.write_text("def test_input():\n    return 1\n")
    assert paths.path_findings(tmp_path, [relative]) == []


@pytest.mark.parametrize("returncode", [0, 17])
def test_dependency_policy_attributes_import_failures_to_each_source(returncode: int) -> None:
    sources = [
        "orchestrator/src/raidar/engine/trial/probe.py",
        "contract/src/raidar_contract/probe.py",
    ]

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode)

    found = paths.dependency_findings([*sources, "orchestrator/tests/test_probe.py"], run=run)
    if returncode == 0:
        assert found == []
    else:
        assert len(found) == len(sources)
        for line, source in zip(found, sources, strict=True):
            _assert_finding(line, source, "STRUCT.dependency-direction")


def test_dependency_policy_does_not_launch_for_non_source_paths() -> None:
    assert (
        paths.dependency_findings(
            ["README.md", "orchestrator/tests/test_probe.py"], run=_unexpected_run
        )
        == []
    )
