"""Python verifier adapter helper tests."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from raidar.assets import verifier_score_scenario as verifier


def test_python_verifier_counts_tests_and_detects_test_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(verifier, "APP_DIR", tmp_path)
    source = tmp_path / "src" / "app.py"
    test_file = tmp_path / "tests" / "test_app.py"
    source.parent.mkdir()
    test_file.parent.mkdir()
    source.write_text("import decimal\nVALUE = 1\n", encoding="utf-8")
    test_file.write_text("def test_value():\n    assert True\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("ignored = True\n", encoding="utf-8")

    files = verifier._python_files()

    assert verifier._parse_test_counts("2 passed, 1 failed, 3 errors") == (2, 6)
    assert verifier._has_python_tests(files) is True
    assert [path.relative_to(tmp_path).as_posix() for path in files] == [
        "src/app.py",
        "tests/test_app.py",
    ]


def test_python_verifier_requirement_checks_and_outputs(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(verifier, "APP_DIR", tmp_path)
    monkeypatch.setattr(verifier, "LOG_DIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir()
    (tmp_path / "app.py").write_text("import decimal\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not python\n", encoding="utf-8")
    (tmp_path / "payload.json").write_text(json.dumps({"ok": True}), encoding="utf-8")

    scenario_spec = {
        "requirements": {
            "items": [
                {"id": "file", "check": {"type": "file_exists", "pattern": "*.py"}},
                {"id": "import", "check": {"type": "import_present", "pattern": "decimal"}},
                {"id": "missing", "check": {"type": "no_pattern", "pattern": "forbidden"}},
            ]
        }
    }

    requirements = verifier._requirements_coverage(scenario_spec)
    missing_requirements = verifier._requirements_coverage(
        {"requirements": {"items": [{"id": "bad", "check": {"type": "unknown"}}]}}
    )

    assert verifier._read_json(tmp_path / "payload.json", {}) == {"ok": True}
    assert verifier._read_json(tmp_path / "missing.json", {"fallback": True}) == {"fallback": True}
    assert verifier._files_matching("*.py") == ["app.py"]
    assert requirements["satisfied_requirements"] == 3
    assert requirements["missing_requirement_ids"] == []
    assert missing_requirements["missing_requirement_ids"] == ["bad"]


def test_python_verifier_performance_and_coverage_helpers(monkeypatch) -> None:
    monkeypatch.setattr(verifier, "_coverage_summary", lambda: (0.8, "coverage.json"))

    coverage = verifier._coverage_score(
        {"verification": {"coverage_threshold": 0.75}},
        [verifier.APP_DIR / "tests" / "test_app.py"],
    )
    performance = verifier._performance_gates(
        gate_history=[{"exit_code": 0}],
        functional={
            "passed": True,
            "build_succeeded": True,
            "tests_passed": 2,
            "tests_total": 2,
        },
        coverage=coverage,
        requirements={
            "satisfied_requirements": 1,
            "total_requirements": 1,
            "missing_requirement_ids": [],
        },
    )

    assert coverage["passed"] is True
    assert all(check["passed"] for check in performance["checks"])


def test_python_verifier_command_and_coverage_summary(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(verifier, "APP_DIR", tmp_path)
    monkeypatch.setattr(verifier, "LOG_DIR", tmp_path)
    (tmp_path / "coverage.json").write_text(
        json.dumps({"totals": {"percent_covered": 87.5}}),
        encoding="utf-8",
    )

    def fake_run(argv, **_kwargs):
        if argv[:3] == [sys.executable, "-m", "coverage"] and "json" in argv:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    result = verifier._run_command(["echo", "ok"], cwd=tmp_path)
    measured, source = verifier._coverage_summary()

    assert result["exit_code"] == 0
    assert result["stdout"] == "ok"
    assert measured == 0.875
    assert source == str(tmp_path / "coverage.json")


def test_python_verifier_gate_functional_and_output_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(verifier, "APP_DIR", tmp_path)
    monkeypatch.setattr(verifier, "LOG_DIR", tmp_path)
    (tmp_path / "test_app.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    def fake_run_command(argv, **_kwargs):
        return {
            "command": " ".join(argv),
            "exit_code": 0,
            "stdout": "1 passed",
            "stderr": "",
            "duration_sec": 0.01,
        }

    monkeypatch.setattr(verifier, "_run_command", fake_run_command)
    gate_history, gate_failures = verifier._run_gates(
        {
            "verification": {
                "max_gate_failures": 1,
                "gates": [{"name": "unit", "command": ["pytest", "-q"]}],
            }
        }
    )
    functional, compile_result, test_result, files = verifier._functional_score(gate_history)
    payload = {
        "functional": functional,
        "test_coverage": {"passed": True},
        "gate_history": gate_history,
        "execution_validity": {"checks": []},
        "performance_gates": {"checks": []},
    }

    verifier._write_verifier_outputs(payload)

    assert gate_failures == 0
    assert functional["passed"] is True
    assert compile_result["exit_code"] == 0
    assert test_result["exit_code"] == 0
    assert verifier._has_python_tests(files) is True
    assert (tmp_path / "reward.txt").read_text(encoding="utf-8") == "1"


def test_python_verifier_main_writes_payload(tmp_path, monkeypatch) -> None:
    spec_path = tmp_path / "scenario-spec.json"
    spec_path.write_text(json.dumps({"environment": {"id": "python:3.12"}}), encoding="utf-8")
    captured = {}

    monkeypatch.setattr(sys, "argv", ["verifier_score_scenario.py", str(spec_path)])
    monkeypatch.setattr(verifier, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(verifier, "_run_gates", lambda _spec: ([{"exit_code": 0}], 0))
    monkeypatch.setattr(
        verifier,
        "_functional_score",
        lambda _history: (
            {
                "passed": True,
                "tests_passed": 1,
                "tests_total": 1,
                "build_succeeded": True,
                "gates_passed": 1,
                "gates_total": 1,
            },
            {"duration_sec": 0.1},
            {"duration_sec": 0.2},
            [],
        ),
    )
    monkeypatch.setattr(
        verifier,
        "_coverage_score",
        lambda _spec, _files: {
            "threshold": None,
            "measured": None,
            "source": None,
            "passed": True,
        },
    )
    monkeypatch.setattr(
        verifier,
        "_requirements_coverage",
        lambda _spec: {
            "satisfied_requirements": 0,
            "total_requirements": 0,
            "missing_requirement_ids": [],
        },
    )
    monkeypatch.setattr(
        verifier, "_write_verifier_outputs", lambda payload: captured.update(payload)
    )

    assert verifier.main() == 0
    assert captured["metadata"]["environment"] == {"id": "python:3.12"}


def test_python_verifier_error_branches(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(verifier, "APP_DIR", tmp_path)
    monkeypatch.setattr(verifier, "LOG_DIR", tmp_path)
    bad_source = tmp_path / "bad.py"
    bad_source.write_bytes(b"\xff")

    def missing_run(_argv, **_kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(verifier.subprocess, "run", missing_run)
    missing = verifier._run_command(["missing"], cwd=tmp_path)
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda _argv, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="failed"),
    )

    assert missing["exit_code"] == 127
    assert verifier._coverage_summary() == (None, "coverage:pytest_failed")
    assert verifier._source_texts() == []
    assert verifier._run_requirement_check({"check": {"type": "unknown"}}, []) is False


def test_python_verifier_failure_branches(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(verifier, "APP_DIR", tmp_path)
    monkeypatch.setattr(verifier, "LOG_DIR", tmp_path)

    def failed_run_command(argv, **_kwargs):
        return {
            "command": " ".join(argv),
            "exit_code": 1,
            "stdout": "",
            "stderr": "failed",
            "duration_sec": 0.01,
        }

    monkeypatch.setattr(verifier, "_run_command", failed_run_command)
    gate_history, gate_failures = verifier._run_gates(
        {
            "verification": {
                "gates": [
                    {
                        "name": "unit",
                        "command": ["pytest", "-q"],
                        "on_failure": "terminate",
                    }
                ]
            }
        }
    )
    functional, _compile_result, test_result, _files = verifier._functional_score(gate_history)

    assert gate_failures == 1
    assert gate_history[0]["exit_code"] == 1
    assert test_result["stderr"] == "No Python test files found."
    assert functional["passed"] is False


def test_python_verifier_coverage_summary_json_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(verifier, "APP_DIR", tmp_path)
    monkeypatch.setattr(verifier, "LOG_DIR", tmp_path)

    def fake_run(argv, **_kwargs):
        if "json" in argv:
            return SimpleNamespace(returncode=1, stdout="", stderr="bad json")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    assert verifier._coverage_summary() == (None, "coverage:json_failed")


def test_python_verifier_coverage_summary_missing_percent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(verifier, "APP_DIR", tmp_path)
    monkeypatch.setattr(verifier, "LOG_DIR", tmp_path)
    (tmp_path / "coverage.json").write_text(json.dumps({"totals": {}}), encoding="utf-8")
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda _argv, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    assert verifier._coverage_summary() == (None, None)
