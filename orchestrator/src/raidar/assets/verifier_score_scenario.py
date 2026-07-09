#!/usr/bin/env python
"""Runtime-neutral Python verifier for Python task environments."""

from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

APP_DIR = Path(os.environ.get("RAIDAR_APP_DIR", "/app"))
LOG_DIR = Path(os.environ.get("RAIDAR_LOG_DIR", "/logs/verifier"))


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _run_command(argv: list[str], *, cwd: Path = APP_DIR) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        return {
            "command": " ".join(argv),
            "exit_code": 127,
            "stdout": "",
            "stderr": str(exc),
            "duration_sec": round(time.monotonic() - started, 3),
        }
    return {
        "command": " ".join(argv),
        "exit_code": result.returncode,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
        "duration_sec": round(time.monotonic() - started, 3),
    }


def _gate_event(gate: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "gate_name": str(gate.get("name") or "gate"),
        "command": result["command"],
        "exit_code": result["exit_code"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "duration_sec": result["duration_sec"],
        "failure_category": None,
        "is_repeat": False,
    }


def _parse_test_counts(output: str) -> tuple[int, int]:
    passed = [int(match) for match in re.findall(r"(\d+)\s+passed", output, re.I)]
    failed = [int(match) for match in re.findall(r"(\d+)\s+failed", output, re.I)]
    errors = [int(match) for match in re.findall(r"(\d+)\s+errors?", output, re.I)]
    pass_count = max(passed) if passed else 0
    fail_count = (max(failed) if failed else 0) + (max(errors) if errors else 0)
    return pass_count, pass_count + fail_count


def _python_files() -> list[Path]:
    excluded = {".git", ".pytest_cache", ".ruff_cache", "__pycache__", ".venv", "venv"}
    return sorted(
        path
        for path in APP_DIR.rglob("*.py")
        if path.is_file() and not (excluded & set(path.relative_to(APP_DIR).parts))
    )


def _has_python_tests(files: list[Path]) -> bool:
    return any(
        "tests" in path.relative_to(APP_DIR).parts
        or path.name.startswith("test_")
        or path.name.endswith("_test.py")
        for path in files
    )


def _coverage_summary() -> tuple[float | None, str | None]:
    coverage_json = LOG_DIR / "coverage.json"
    coverage_file = LOG_DIR / ".coverage"
    env = os.environ.copy()
    env["COVERAGE_FILE"] = str(coverage_file)
    run = subprocess.run(
        [sys.executable, "-m", "coverage", "run", "-m", "pytest", "-q"],
        cwd=APP_DIR,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if run.returncode != 0:
        return None, "coverage:pytest_failed"
    report = subprocess.run(
        [sys.executable, "-m", "coverage", "json", "-o", str(coverage_json)],
        cwd=APP_DIR,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if report.returncode != 0:
        return None, "coverage:json_failed"
    payload = _read_json(coverage_json, {})
    totals = payload.get("totals") if isinstance(payload, dict) else None
    measured = totals.get("percent_covered") if isinstance(totals, dict) else None
    if not isinstance(measured, int | float):
        return None, None
    return max(0.0, min(1.0, float(measured) / 100.0)), str(coverage_json)


def _source_texts() -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for path in _python_files():
        try:
            values.append((str(path.relative_to(APP_DIR)), path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            continue
    return values


def _files_matching(pattern: str) -> list[str]:
    return [
        str(Path(path).relative_to(APP_DIR))
        for path in glob.glob(str(APP_DIR / pattern), recursive=True)
        if Path(path).is_file()
    ]


def _run_requirement_check(requirement: dict[str, Any], sources: list[tuple[str, str]]) -> bool:
    check = requirement.get("check") if isinstance(requirement, dict) else {}
    check_type = check.get("type") if isinstance(check, dict) else None
    pattern = str(check.get("pattern") or "")
    if check_type == "file_exists":
        return bool(_files_matching(pattern))
    if check_type == "import_present":
        return any(pattern in text for _path, text in sources)
    if check_type == "no_pattern":
        return not any(pattern in text for _path, text in sources)
    return False


def _requirements_coverage(scenario_spec: dict[str, Any]) -> dict[str, Any]:
    requirements = scenario_spec.get("requirements", {}).get("items", [])
    sources = _source_texts()
    missing: list[str] = []
    satisfied = 0
    for requirement in requirements:
        if _run_requirement_check(requirement, sources):
            satisfied += 1
        else:
            missing.append(str(requirement.get("id", "unknown")))
    total = len(requirements)
    return {
        "total_requirements": total,
        "satisfied_requirements": satisfied,
        "mapped_requirements": total,
        "mapped_satisfied_requirements": satisfied,
        "missing_requirement_ids": missing,
        "requirement_gap_ids": [],
        "requirement_test_evidence_gaps": {},
    }


def _performance_gates(
    *,
    gate_history: list[dict[str, Any]],
    functional: dict[str, Any],
    coverage: dict[str, Any],
    requirements: dict[str, Any],
) -> dict[str, Any]:
    return {
        "checks": [
            {
                "name": "quality_gates_passed",
                "passed": all(event["exit_code"] == 0 for event in gate_history),
                "evidence": (
                    f"{sum(1 for event in gate_history if event['exit_code'] == 0)}/"
                    f"{len(gate_history)} gates passed."
                ),
            },
            {
                "name": "functional_passed",
                "passed": functional["passed"],
                "evidence": (
                    f"build={functional['build_succeeded']}, "
                    f"tests={functional['tests_passed']}/{functional['tests_total']}"
                ),
            },
            {
                "name": "coverage_threshold_met",
                "passed": coverage["passed"],
                "evidence": (
                    f"threshold={coverage['threshold']}, measured={coverage['measured']}, "
                    f"source={coverage['source']}"
                ),
            },
            {
                "name": "all_requirements_present",
                "passed": requirements["satisfied_requirements"]
                >= requirements["total_requirements"],
                "evidence": (
                    f"satisfied={requirements['satisfied_requirements']}/"
                    f"{requirements['total_requirements']}, "
                    f"missing={requirements['missing_requirement_ids']}"
                ),
            },
        ]
    }


def _run_gates(scenario_spec: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    gate_history: list[dict[str, Any]] = []
    gate_failures = 0
    max_gate_failures = int(scenario_spec.get("verification", {}).get("max_gate_failures") or 3)
    for gate in scenario_spec.get("verification", {}).get("gates", []):
        result = _run_command(list(gate.get("command") or []))
        gate_history.append(_gate_event(gate, result))
        if result["exit_code"] != 0:
            gate_failures += 1
            if gate.get("on_failure") == "terminate" or gate_failures >= max_gate_failures:
                break
    return gate_history, gate_failures


def _functional_score(
    gate_history: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[Path]]:
    files = _python_files()
    compile_result = _run_command([sys.executable, "-m", "compileall", "-q", "."])
    if _has_python_tests(files):
        test_result = _run_command([sys.executable, "-m", "pytest", "-q"])
    else:
        test_result = {
            "command": f"{sys.executable} -m pytest -q",
            "exit_code": 1,
            "stdout": "",
            "stderr": "No Python test files found.",
            "duration_sec": 0,
        }
    tests_passed, tests_total = _parse_test_counts(
        f"{test_result['stdout']}\n{test_result['stderr']}"
    )
    functional = {
        "passed": compile_result["exit_code"] == 0 and test_result["exit_code"] == 0,
        "tests_passed": tests_passed,
        "tests_total": tests_total,
        "build_succeeded": compile_result["exit_code"] == 0,
        "gates_passed": sum(1 for event in gate_history if event["exit_code"] == 0),
        "gates_total": len(gate_history),
    }
    return functional, compile_result, test_result, files


def _coverage_score(scenario_spec: dict[str, Any], files: list[Path]) -> dict[str, Any]:
    threshold = scenario_spec.get("verification", {}).get("coverage_threshold")
    measured, source = _coverage_summary() if _has_python_tests(files) else (None, None)
    return {
        "threshold": threshold,
        "measured": measured,
        "source": source,
        "passed": threshold is None
        or threshold <= 0
        or (measured is not None and measured >= threshold),
    }


def _write_verifier_outputs(payload: dict[str, Any]) -> None:
    _write_json(LOG_DIR / "scorecard.json", payload)
    _write_json(LOG_DIR / "gate-history.json", payload["gate_history"])
    _write_json(LOG_DIR / "execution-validity.json", payload["execution_validity"])
    _write_json(LOG_DIR / "performance-gates.json", payload["performance_gates"])
    (LOG_DIR / "reward.txt").write_text(
        "1" if payload["functional"]["passed"] and payload["test_coverage"]["passed"] else "0",
        encoding="utf-8",
    )


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: verifier_score_scenario.py scenario-spec.json")
    scenario_spec = _read_json(Path(sys.argv[1]), {})
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    gate_history, gate_failures = _run_gates(scenario_spec)
    functional, compile_result, test_result, files = _functional_score(gate_history)
    coverage = _coverage_score(scenario_spec, files)
    requirements = _requirements_coverage(scenario_spec)
    verification_stability = {
        "total_gate_failures": gate_failures,
        "unique_failure_categories": 0,
        "repeat_failures": 0,
    }
    execution_validity = {
        "checks": [
            {
                "name": "run_completed",
                "passed": True,
                "evidence": "Run completed without early termination.",
            }
        ]
    }
    performance_gates = _performance_gates(
        gate_history=gate_history,
        functional=functional,
        coverage=coverage,
        requirements=requirements,
    )
    payload = {
        "functional": functional,
        "visual": None,
        "verification_stability": verification_stability,
        "test_coverage": coverage,
        "requirements_coverage": requirements,
        "execution_validity": execution_validity,
        "performance_gates": performance_gates,
        "metric_scores": [],
        "gate_history": gate_history,
        "metadata": {
            "command_timings_sec": {
                "compile": compile_result["duration_sec"],
                "test": test_result["duration_sec"],
            },
            "environment": scenario_spec.get("environment", {}),
        },
    }
    _write_verifier_outputs(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
