"""TypeScript scorer evidence adapters."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from raidar.schemas.events import GateEvent
from raidar.schemas.scenario import RequirementSpec
from raidar.schemas.scorecard import CoverageScore, RequirementsCoverageScore
from raidar.scorers.deterministic import run_deterministic_check


def coverage_from_istanbul_summary(workspace: Path) -> tuple[float | None, str | None]:
    summary_path = workspace / "coverage" / "coverage-summary.json"
    if not summary_path.exists():
        return None, None
    try:
        payload = json.loads(summary_path.read_text())
    except json.JSONDecodeError:
        return None, None
    total = payload.get("total")
    if not isinstance(total, dict):
        return None, None
    values: list[float] = []
    for key in ("lines", "statements", "functions", "branches"):
        metric = total.get(key)
        if not isinstance(metric, dict):
            continue
        pct = metric.get("pct")
        if isinstance(pct, (int, float)):
            values.append(float(pct))
    if not values:
        return None, None
    return min(values) / 100.0, str(summary_path)


def parse_istanbul_coverage_percent(output: str) -> float | None:
    values: list[float] = []
    for pattern in (
        r"Lines\s*:\s*([0-9]+(?:\.[0-9]+)?)%",
        r"Statements\s*:\s*([0-9]+(?:\.[0-9]+)?)%",
        r"Functions\s*:\s*([0-9]+(?:\.[0-9]+)?)%",
        r"Branches\s*:\s*([0-9]+(?:\.[0-9]+)?)%",
    ):
        values.extend(float(match) for match in re.findall(pattern, output, re.IGNORECASE))
    table_match = re.search(
        (
            r"All files\s*\|\s*([0-9]+(?:\.[0-9]+)?)\s*\|\s*([0-9]+(?:\.[0-9]+)?)\s*\|\s*"
            r"([0-9]+(?:\.[0-9]+)?)\s*\|\s*([0-9]+(?:\.[0-9]+)?)"
        ),
        output,
    )
    if table_match:
        values.extend(float(value) for value in table_match.groups())
    if not values:
        return None
    return min(values) / 100.0


def coverage_from_gate_history(
    gate_history: list[GateEvent],
) -> tuple[float | None, str | None]:
    for event in reversed(gate_history):
        gate_hint = f"{event.gate_name} {event.command}".lower()
        if "coverage" not in gate_hint:
            continue
        parsed = parse_istanbul_coverage_percent(f"{event.stdout}\n{event.stderr}")
        if parsed is not None:
            return parsed, f"gate:{event.gate_name}"
    return None, None


def evaluate_typescript_coverage(
    workspace: Path,
    gate_history: list[GateEvent],
    threshold: float | None,
) -> CoverageScore:
    """Evaluate TypeScript/Istanbul coverage evidence for scorer output."""

    measured, source = coverage_from_istanbul_summary(workspace)
    if measured is None:
        measured, source = coverage_from_gate_history(gate_history)
    passed = threshold is None or threshold <= 0 or (measured is not None and measured >= threshold)
    return CoverageScore(
        threshold=threshold,
        measured=measured,
        source=source,
        passed=passed,
    )


def typescript_test_file_paths(workspace: Path) -> list[Path]:
    patterns = (
        "**/*.test.ts",
        "**/*.test.tsx",
        "**/*.spec.ts",
        "**/*.spec.tsx",
    )
    test_paths: list[Path] = []
    for pattern in patterns:
        test_paths.extend((workspace / "src").glob(pattern))
    return test_paths


def test_evidence_label(evidence: dict[str, Any]) -> str:
    evidence_type = str(evidence.get("type", "unknown"))
    if evidence_type == "query_role":
        role = str(evidence.get("role", "unknown"))
        min_count = int(evidence.get("min_count", 1) or 1)
        parts = [role]
        if evidence.get("level") is not None:
            parts.append(f"level={evidence['level']}")
        if evidence.get("name"):
            parts.append(f"name={evidence['name']}")
        return f"query_role:{','.join(parts)} x{min_count}"
    if evidence_type == "query_text":
        pattern = str(evidence.get("pattern", "unknown"))
        min_count = int(evidence.get("min_count", 1) or 1)
        return f"query_text:{pattern} x{min_count}"
    return evidence_type


def count_role_query_matches(test_sources: list[str], evidence: dict[str, Any]) -> int:
    role = re.escape(str(evidence.get("role", "")))
    if not role:
        return 0
    query_pattern = re.compile(
        r"(?:screen\.)?(?:get|find|query)(?:All)?ByRole\s*\(\s*(['\"])"
        + role
        + r"\1(?P<options>\s*,\s*\{[\s\S]*?\})?",
        re.MULTILINE,
    )
    level = evidence.get("level")
    name = evidence.get("name")
    count = 0
    for source in test_sources:
        for match in query_pattern.finditer(source):
            options = match.group("options") or ""
            if level is not None and not re.search(rf"level\s*:\s*{int(level)}\b", options):
                continue
            if name is not None and not re.search(re.escape(str(name)), options, re.IGNORECASE):
                continue
            count += 1
    return count


def count_text_query_matches(test_sources: list[str], evidence: dict[str, Any]) -> int:
    pattern = str(evidence.get("pattern", ""))
    if not pattern:
        return 0
    count = 0
    query_pattern = re.compile(r"(?:screen\.)?(?:get|find|query)(?:All)?ByText\s*\(", re.MULTILINE)
    for source in test_sources:
        if not query_pattern.search(source):
            continue
        count += len(re.findall(pattern, source, re.MULTILINE | re.IGNORECASE))
    return count


def missing_typescript_test_evidence(
    test_sources: list[str],
    required_test_evidence: list[Any],
) -> list[str]:
    missing: list[str] = []
    for evidence in required_test_evidence:
        payload = evidence.model_dump(mode="json") if hasattr(evidence, "model_dump") else evidence
        evidence_type = payload.get("type")
        min_count = int(payload.get("min_count", 1) or 1)
        if evidence_type == "query_role":
            matched = count_role_query_matches(test_sources, payload)
        elif evidence_type == "query_text":
            matched = count_text_query_matches(test_sources, payload)
        else:
            matched = 0
        if matched < min_count:
            missing.append(test_evidence_label(payload))
    return missing


def evaluate_typescript_requirements(
    workspace: Path,
    requirements: list[RequirementSpec],
) -> RequirementsCoverageScore:
    """Evaluate deterministic requirements plus TypeScript test mapping evidence."""

    if not requirements:
        return RequirementsCoverageScore()

    test_sources = [
        path.read_text(errors="ignore") for path in typescript_test_file_paths(workspace)
    ]
    missing_ids: list[str] = []
    gap_ids: list[str] = []
    evidence_gaps: dict[str, list[str]] = {}
    satisfied = 0
    mapped = 0
    mapped_satisfied = 0

    for requirement in requirements:
        requirement_check = run_deterministic_check(requirement.check, workspace)
        missing_evidence = missing_typescript_test_evidence(
            test_sources, requirement.required_test_evidence
        )
        if requirement_check.passed:
            satisfied += 1
        else:
            missing_ids.append(requirement.id)

        mapped_for_requirement = not missing_evidence
        if mapped_for_requirement:
            mapped += 1
            if requirement_check.passed:
                mapped_satisfied += 1
        else:
            gap_ids.append(requirement.id)
            evidence_gaps[requirement.id] = missing_evidence

    return RequirementsCoverageScore(
        total_requirements=len(requirements),
        satisfied_requirements=satisfied,
        mapped_requirements=mapped,
        mapped_satisfied_requirements=mapped_satisfied,
        missing_requirement_ids=missing_ids,
        requirement_gap_ids=gap_ids,
        requirement_test_evidence_gaps=evidence_gaps,
    )
