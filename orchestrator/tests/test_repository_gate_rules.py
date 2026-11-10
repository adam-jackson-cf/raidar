"""Fast behavioural proofs for the repository gate and per-path policy."""

from __future__ import annotations

import importlib.util
import json
import os
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


dispatcher = _load_script("scripts/checks/run-repository-gate.py", "repository_gate_dispatcher")
paths = _load_script("scripts/checks/check-repository-paths.py", "repository_gate_paths")


def _write_gate(root: Path, gate: object) -> None:
    directory = root / ".omp"
    directory.mkdir()
    (directory / "gate.json").write_text(json.dumps(gate))


def _unexpected_run(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("This input must not launch a process")


def _assert_error_line(capsys: pytest.CaptureFixture[str]) -> None:
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert captured.err.strip()


def _assert_finding(line: str, path: str, rule: str) -> None:
    assert re.fullmatch(rf"{re.escape(path)}: {re.escape(rule)}: [^\r\n;]+; [^\r\n]+", line)


@pytest.mark.parametrize("check", ["commit", "ci"])
def test_check_argv_selects_the_declared_check(check: str) -> None:
    gate = {
        "schemaVersion": 2,
        "checks": {"commit": ["make", "gate-commit"], "ci": ["make", "gate-ci"]},
    }
    assert dispatcher.check_argv(gate, check) == ["make", f"gate-{check}"]


@pytest.mark.parametrize(
    ("gate", "check"),
    [
        (None, "commit"),
        ({"schemaVersion": 1, "checks": {"commit": ["make"]}}, "commit"),
        ({"schemaVersion": 2, "checks": {}}, "commit"),
        ({"schemaVersion": 2}, "ci"),
        ({"schemaVersion": 2, "checks": {"commit": []}}, "commit"),
        ({"schemaVersion": 2, "checks": {"commit": ["make", " "]}}, "commit"),
        ({"schemaVersion": 2, "checks": {"commit": ["make", 17]}}, "commit"),
        ({"schemaVersion": 2, "checks": {"commit": "make"}}, "commit"),
        ({"schemaVersion": 2, "checks": {"unknown": ["make"]}}, "unknown"),
    ],
)
def test_check_argv_refuses_invalid_configuration(gate: object, check: str) -> None:
    with pytest.raises(dispatcher.GateConfigError, match=r"^GATE\.local-ci-parity: .+"):
        dispatcher.check_argv(gate, check)


@pytest.mark.parametrize("check", ["commit", "ci"])
@pytest.mark.parametrize("returncode", [0, 17])
def test_dispatcher_propagates_the_selected_command_exit_code(
    tmp_path: Path, check: str, returncode: int, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_gate(
        tmp_path,
        {
            "schemaVersion": 2,
            "checks": {"commit": ["make", "gate-commit"], "ci": ["make", "gate-ci"]},
        },
    )
    calls = []

    def run(
        command: list[str], *, cwd: Path, check: bool, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, cwd, check))
        return subprocess.CompletedProcess(command, returncode)

    assert dispatcher.main([check], root=tmp_path, run=run) == returncode
    assert calls == [(["make", f"gate-{check}"], tmp_path, False)]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("check", ["commit", "ci"])
def test_dispatcher_children_locate_repositories_from_their_own_working_directory(
    tmp_path: Path, check: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_gate(
        tmp_path,
        {
            "schemaVersion": 2,
            "checks": {"commit": ["make", "gate-commit"], "ci": ["make", "gate-ci"]},
        },
    )
    repository_variables = {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_PREFIX",
    }
    for variable in repository_variables:
        monkeypatch.setenv(variable, f"/invoking-repository/{variable}")
    for variable in ("PATH", "HOME", "RAIDAR_X"):
        monkeypatch.setenv(variable, f"/retained/{variable}")
    expected_environment = {
        name: value for name, value in os.environ.items() if name not in repository_variables
    }
    child_environments: list[dict[str, str] | None] = []

    def run(
        command: list[str], *, cwd: Path, check: bool, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        child_environments.append(env)
        return subprocess.CompletedProcess(command, 0)

    assert dispatcher.main([check], root=tmp_path, run=run) == 0
    assert len(child_environments) == 1
    child_environment = child_environments[0]
    assert child_environment is not None
    assert child_environment.keys() == expected_environment.keys()
    for name, value in expected_environment.items():
        assert child_environment[name] == value, name


@pytest.mark.parametrize(
    "configuration", ["missing", "unreadable", "invalid-json", "invalid-schema"]
)
def test_dispatcher_reports_configuration_errors_without_launching(
    tmp_path: Path, configuration: str, capsys: pytest.CaptureFixture[str]
) -> None:
    if configuration != "missing":
        directory = tmp_path / ".omp"
        directory.mkdir()
        gate = directory / "gate.json"
        if configuration == "unreadable":
            gate.mkdir()
        else:
            gate.write_text("{" if configuration == "invalid-json" else '{"schemaVersion": 1}')
    assert dispatcher.main(["commit"], root=tmp_path, run=_unexpected_run) == 2
    _assert_error_line(capsys)


def test_dispatcher_reports_launch_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_gate(tmp_path, {"schemaVersion": 2, "checks": {"commit": ["missing-tool"]}})

    def run(*args: object, **kwargs: object) -> NoReturn:
        raise OSError("missing executable")

    assert dispatcher.main(["commit"], root=tmp_path, run=run) == 2
    _assert_error_line(capsys)


@pytest.mark.parametrize("argv", [[], ["unknown"], ["commit", "ci"]])
def test_dispatcher_reports_bad_usage(
    tmp_path: Path, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    _write_gate(tmp_path, {"schemaVersion": 2, "checks": {"commit": ["make"]}})
    assert dispatcher.main(argv, root=tmp_path, run=_unexpected_run) == 2
    _assert_error_line(capsys)


def test_path_policy_accepts_owned_paths(tmp_path: Path) -> None:
    sources = {
        "README.md": "# Repository\n",
        "orchestrator/src/raidar/engine/trial/probe.py": "VALUE = 1\n",
        "orchestrator/tests/test_probe.py": "def test_probe():\n    assert True\n",
    }
    for relative, source in sources.items():
        candidate = tmp_path / relative
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(source)
    assert paths.path_findings(tmp_path, list(sources)) == []


@pytest.mark.parametrize(
    ("relative", "source", "rule"),
    [
        ("notes.txt", "misplaced\n", "STRUCT.folder-placement"),
        ("orchestrator/src/raidar/engine/trial/v2/probe.py", "VALUE = 1\n", "NAME.conventions"),
        (
            "orchestrator/src/raidar/engine/trial/probe.py",
            "def test_probe():\n    pass\n",
            "TEST.placement",
        ),
        (
            "orchestrator/tests/test_probe.py",
            "import pytest\n\n@pytest.mark.slow\ndef test_probe():\n    pass\n",
            "TEST.placement",
        ),
        ("orchestrator/src/raidar/engine/trial/probe.py", "def broken(\n", "PYS000"),
    ],
)
def test_path_policy_reports_actionable_rule_findings(
    tmp_path: Path, relative: str, source: str, rule: str
) -> None:
    candidate = tmp_path / relative
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(source)
    found = paths.path_findings(tmp_path, [relative])
    assert len(found) == 1
    _assert_finding(found[0], relative, rule)


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
    relative = "lab/stages/x/y/fixtures/program.py"
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
