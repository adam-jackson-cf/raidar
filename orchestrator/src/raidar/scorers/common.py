"""Shared helpers for scorer implementations."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from raidar.schemas.scenario import ScorerMetricDefinition
from raidar.schemas.scorecard import MetricScore

SOURCE_ROOTS = ("src", "app", "lib", "package")
TEST_NAME_PATTERNS = (
    ".test.",
    ".spec.",
    "_test.",
    "test_",
)


def metric(
    metric_id: str,
    metric_type: str,
    weight: float,
    *,
    evidence: str,
    score_derivation: str,
    pass_fail: str,
    config: dict | None = None,
) -> ScorerMetricDefinition:
    return ScorerMetricDefinition(
        id=metric_id,
        type=metric_type,
        weight=weight,
        evidence=evidence,
        score_derivation=score_derivation,
        pass_fail=pass_fail,
        config=config or {},
    )


def functional_metric_score(outputs) -> MetricScore:
    return MetricScore(
        metric_id="functional",
        score=outputs.functional.score,
        passed=outputs.functional.passed,
        evidence=(
            f"build={outputs.functional.build_succeeded}, "
            f"tests={outputs.functional.tests_passed}/{outputs.functional.tests_total}"
        ),
    )


def verification_stability_metric_score(outputs) -> MetricScore:
    return verification_stability_score(outputs.verification_stability)


def verification_stability_score(score) -> MetricScore:
    return MetricScore(
        metric_id="verification-stability",
        score=score.score,
        passed=score.score > 0,
        evidence=f"failures={score.total_gate_failures}",
    )


def coverage_ratio_score(coverage) -> float:
    threshold = getattr(coverage, "threshold", None)
    measured = getattr(coverage, "measured", None)
    if threshold is None or threshold <= 0:
        return 1.0 if getattr(coverage, "passed", False) else 0.0
    if measured is None:
        return 0.0
    return min(1.0, measured / threshold)


def workspace_files(workspace: Path) -> list[Path]:
    excluded = {
        ".git",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "coverage",
        "dist",
        "htmlcov",
        "node_modules",
        "venv",
    }
    return sorted(
        path
        for path in workspace.rglob("*")
        if path.is_file()
        and not any(part in excluded for part in path.relative_to(workspace).parts)
    )


def is_test_path(path: Path) -> bool:
    text = path.as_posix().lower()
    name = path.name.lower()
    return (
        "/test/" in text
        or "/tests/" in text
        or any(pattern in name for pattern in TEST_NAME_PATTERNS)
    )


def is_source_path(path: Path) -> bool:
    parts = path.parts
    return bool(parts) and parts[0] in SOURCE_ROOTS and not is_test_path(path)


def source_and_test_files(workspace: Path) -> tuple[list[Path], list[Path]]:
    relatives = [path.relative_to(workspace) for path in workspace_files(workspace)]
    sources = [path for path in relatives if is_source_path(path)]
    tests = [path for path in relatives if is_test_path(path)]
    return sources, tests


def change_containment_metric_score(
    *,
    workspace: Path,
    workspace_changes: dict[str, Any] | None = None,
    expected_paths: tuple[str, ...] = (),
    metric_id: str = "change-containment",
) -> MetricScore:
    sources, tests, has_change_evidence = _source_test_paths_for_change_metric(
        workspace, workspace_changes
    )
    expected = tuple(path for path in expected_paths if path)
    outside = _outside_expected_paths(sources, expected)
    score = _change_containment_score(
        production_count=len(sources),
        test_count=len(tests),
        outside_count=len(outside),
    )
    evidence_parts = [
        "direct: retained changed-file evidence evaluated"
        if has_change_evidence
        else "proxy: changed-file evidence unavailable; using workspace source/test inventory",
        f"production_files={len(sources)}",
        f"test_files={len(tests)}",
        f"outside_expected={len(outside)}",
    ]
    return MetricScore(
        metric_id=metric_id,
        score=score,
        passed=has_change_evidence and score >= 0.8,
        missing_patterns=[path.as_posix() for path in outside[:5]],
        evidence=", ".join(evidence_parts),
    )


def _outside_expected_paths(paths: list[Path], expected: tuple[str, ...]) -> list[Path]:
    if not expected:
        return []
    return [path for path in paths if not _path_matches_any(path, expected)]


def _source_test_paths_for_change_metric(
    workspace: Path, workspace_changes: dict[str, Any] | None
) -> tuple[list[Path], list[Path], bool]:
    has_change_evidence = _has_changed_file_evidence(workspace_changes)
    if not has_change_evidence:
        sources, tests = source_and_test_files(workspace)
        return sources, tests, False
    changed_files = _changed_file_paths(workspace_changes)
    return (
        [path for path in changed_files if is_source_path(path)],
        [path for path in changed_files if is_test_path(path)],
        True,
    )


def _path_matches_any(path: Path, patterns: tuple[str, ...]) -> bool:
    path_text = path.as_posix()
    return any(path.match(pattern) or path_text.startswith(pattern) for pattern in patterns)


def _change_containment_score(
    *,
    production_count: int,
    test_count: int,
    outside_count: int,
) -> float:
    score = 1.0 - (0.25 * outside_count)
    if production_count > max(1, test_count * 3):
        score -= 0.15
    return round(max(0.0, score), 3)


def production_code_guardrail_metric_score(
    workspace: Path, workspace_changes: dict[str, Any] | None = None
) -> MetricScore:
    sources, tests, has_change_evidence = _source_test_paths_for_change_metric(
        workspace, workspace_changes
    )
    score = round(max(0.0, 1.0 - (0.25 * len(sources))), 3) if sources else 1.0
    evidence_prefix = (
        "direct: retained changed-file evidence evaluated, "
        if has_change_evidence
        else "proxy: changed-file evidence unavailable; production/test classification "
        "uses source-root and test-file heuristics, "
    )
    return MetricScore(
        metric_id="production-code-guardrail",
        score=score,
        passed=has_change_evidence and not sources,
        missing_patterns=[path.as_posix() for path in sources[:5]],
        evidence=f"{evidence_prefix}production_files={len(sources)}, test_files={len(tests)}",
    )


def assertion_strength_metric_score(workspace: Path) -> MetricScore:
    _sources, tests = source_and_test_files(workspace)
    if not tests:
        return MetricScore(
            metric_id="assertion-strength",
            score=0.0,
            passed=False,
            missing_patterns=["test files"],
            evidence="proxy: regex test inspection found no test files",
        )
    text = "\n".join(
        (workspace / path).read_text(encoding="utf-8", errors="ignore") for path in tests
    )
    non_empty = bool(re.search(r"\b(it|test|def\s+test[_a-z0-9]*)\b", text))
    assertions = len(
        re.findall(r"\b(assert|expect|assertEqual|assertTrue|toBe|toEqual|toContain)\b", text)
    )
    diversity = len(
        {
            match.lower()
            for match in re.findall(
                r"\b(assert|expect|assertequal|asserttrue|tobe|toequal|tocontain)\b", text
            )
        }
    )
    skipped = bool(re.search(r"\b(skip|xit|xtest|pytest\.mark\.skip|describe\.skip)\b", text))
    snapshot_only = bool(re.search(r"snapshot", text, re.IGNORECASE)) and assertions <= 1
    score = 0.0
    score += 0.4 if non_empty else 0.0
    score += 0.3 if assertions > 0 else 0.0
    score += 0.2 if diversity > 1 else 0.0
    score += 0.1 if not skipped else 0.0
    if snapshot_only:
        score = min(score, 0.5)
    score = round(score, 3)
    return MetricScore(
        metric_id="assertion-strength",
        score=score,
        passed=score >= 0.8,
        evidence=(
            "proxy: regex assertion inspection, "
            f"test_files={len(tests)}, assertions={assertions}, diversity={diversity}, "
            f"skipped={skipped}, snapshot_only={snapshot_only}"
        ),
    )


def coverage_lift_metric_score(outputs: Any) -> MetricScore:
    coverage = getattr(outputs, "test_coverage", None)
    threshold = _numeric_or_none(getattr(coverage, "threshold", None))
    measured = _numeric_or_none(getattr(coverage, "measured", None))
    passed = bool(getattr(coverage, "passed", False))
    if measured is None:
        return MetricScore(
            metric_id="coverage-lift",
            score=0.0,
            passed=False,
            missing_patterns=["coverage baseline or final measurement"],
            evidence="proxy: no baseline coverage and no final coverage measurement",
        )
    if threshold is None or threshold <= 0:
        score = min(0.8, measured)
    else:
        score = min(0.8, measured / threshold)
    return MetricScore(
        metric_id="coverage-lift",
        score=round(max(0.0, min(1.0, score)), 3),
        passed=False,
        evidence=(
            "proxy: no baseline coverage; scored final coverage against threshold, "
            f"threshold={threshold}, measured={measured}, final_passed={passed}"
        ),
    )


def _has_changed_file_evidence(workspace_changes: dict[str, Any] | None) -> bool:
    if not isinstance(workspace_changes, dict):
        return False
    return _changed_files_are_valid(
        workspace_changes.get("changed_files")
    ) and workspace_changes.get("error") in (None, "")


def _changed_file_paths(workspace_changes: dict[str, Any] | None) -> list[Path]:
    if not _has_changed_file_evidence(workspace_changes):
        return []
    changed = workspace_changes.get("changed_files", [])
    return [Path(path) for path in changed if isinstance(path, str) and path]


def _changed_files_are_valid(changed_files: Any) -> bool:
    return isinstance(changed_files, list) and all(
        isinstance(path, str) and bool(path.strip()) for path in changed_files
    )


def valid_changed_file_paths(workspace_changes: dict[str, Any] | None) -> list[Path]:
    return _changed_file_paths(workspace_changes)


def _numeric_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def requirement_mapping_metric_score(workspace: Path, scenario: Any) -> MetricScore:
    requirements = list(getattr(getattr(scenario, "requirements", None), "items", []) or [])
    _sources, tests = source_and_test_files(workspace)
    missing = _requirement_mapping_missing_inputs(requirements, tests)
    if missing:
        return MetricScore(
            metric_id="requirement-mapping",
            score=0.0,
            passed=False,
            missing_patterns=missing,
            evidence="proxy: missing requirements or tests for requirement mapping",
        )
    test_text = _joined_test_text(workspace, tests)
    test_words = set(re.findall(r"[a-z0-9]{4,}", test_text))
    mapped = sum(
        1
        for requirement in requirements
        if _requirement_is_mapped(requirement, test_text, test_words)
    )
    score = mapped / len(requirements)
    return MetricScore(
        metric_id="requirement-mapping",
        score=round(score, 3),
        passed=score >= 1.0,
        evidence=(
            "proxy: requirement mapping inferred from requirement ids and test text, "
            f"requirements={len(requirements)}, mapped={mapped}, test_files={len(tests)}"
        ),
    )


def _requirement_mapping_missing_inputs(requirements: list[Any], tests: list[Path]) -> list[str]:
    missing = []
    if not requirements:
        missing.append("scenario requirements")
    if not tests:
        missing.append("test files")
    return missing


def _joined_test_text(workspace: Path, tests: list[Path]) -> str:
    return "\n".join(
        (workspace / path).read_text(encoding="utf-8", errors="ignore") for path in tests
    ).lower()


def _requirement_is_mapped(requirement: Any, test_text: str, test_words: set[str]) -> bool:
    requirement_id = str(getattr(requirement, "id", "")).lower()
    normalized_requirement_id = requirement_id.replace("-", "_")
    if requirement_id and (requirement_id in test_text or normalized_requirement_id in test_text):
        return True
    description_words = set(
        re.findall(
            r"[a-z0-9]{4,}",
            getattr(requirement, "description", "").lower(),
        )
    )
    return bool(description_words and len(description_words & test_words) >= 2)


def artifact_metric_score(workspace: Path, required_artifacts: tuple[str, ...]) -> MetricScore:
    missing = missing_required_artifacts(workspace, required_artifacts)
    matched_count = len(required_artifacts) - len(missing)
    score = 1.0 if not required_artifacts else matched_count / len(required_artifacts)
    return MetricScore(
        metric_id="artifact-checks",
        score=round(score, 3),
        passed=score >= 1.0,
        matched_count=matched_count,
        missing_patterns=missing,
        evidence=f"required_artifacts={len(required_artifacts)}, matched={matched_count}",
    )


def code_task_artifact_metric_score(
    *,
    language_label: str,
    files: list[Path],
    tests: list[Path],
    workspace: Path,
    required_artifacts: tuple[str, ...],
    is_test_file: Callable[[Path, Path], bool],
) -> MetricScore:
    source_files = [path for path in files if not is_test_file(path, workspace)]
    missing_required = missing_required_artifacts(workspace, required_artifacts)
    missing = []
    if not source_files:
        missing.append(f"{language_label} source files")
    if not tests:
        missing.append(f"{language_label} test files")
    missing.extend(missing_required)
    structure_score = (0.5 if source_files else 0.0) + (0.5 if tests else 0.0)
    required_score = (
        1.0
        if not required_artifacts
        else (len(required_artifacts) - len(missing_required)) / len(required_artifacts)
    )
    score = round(structure_score * required_score, 3)
    return MetricScore(
        metric_id="artifact-checks",
        score=score,
        passed=score >= 1.0,
        matched_count=len(source_files) + len(tests),
        missing_patterns=missing,
        evidence=f"source_files={len(source_files)}, test_files={len(tests)}",
    )


def required_artifact_patterns(scenario, scorer_id: str) -> tuple[str, ...]:
    patterns: list[str] = []
    for scorer_ref in getattr(scenario, "scorers", ()):
        if getattr(scorer_ref, "id", None) != scorer_id:
            continue
        artifact_config = getattr(scorer_ref, "config", {}).get("artifact-checks", {})
        required_paths = artifact_config.get("required_paths", [])
        if isinstance(required_paths, list):
            patterns.extend(path for path in required_paths if isinstance(path, str))
    return tuple(dict.fromkeys(patterns))


def missing_required_artifacts(workspace: Path, patterns: tuple[str, ...]) -> list[str]:
    return [
        pattern
        for pattern in patterns
        if not any(path.is_file() for path in workspace.glob(pattern))
    ]
