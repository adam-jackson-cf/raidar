"""Typed scenario comparison and revision projection."""

from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from raidar.schemas.review import (
    DiffLine,
    FileDiff,
    ResolvedScorerReviewConfig,
    RevisionChangeCategory,
    RevisionDiffArtifact,
    ScenarioComparableConfig,
    ScenarioReviewContract,
    TextDiff,
)
from raidar.schemas.scenario import ScenarioDefinition


def _json_text(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _artifact_hashes(scenario_dir: Path | None, relative_paths: list[str]) -> dict[str, str]:
    if scenario_dir is None:
        return {}
    hashes: dict[str, str] = {}
    for relative_path in relative_paths:
        artifact = scenario_dir / relative_path
        if artifact.is_file():
            hashes[relative_path] = _sha256_bytes(artifact.read_bytes())
    return hashes


def _rules_hashes(scenario_dir: Path | None) -> dict[str, str]:
    if scenario_dir is None:
        return {}
    rules_dir = scenario_dir / "rules"
    if not rules_dir.is_dir():
        return {}
    return {
        str(path.relative_to(scenario_dir)): _sha256_bytes(path.read_bytes())
        for path in sorted(rules_dir.iterdir())
        if path.is_file()
    }


def scenario_comparable_config(
    scenario: ScenarioDefinition,
    *,
    scenario_dir: Path | None = None,
) -> ScenarioComparableConfig:
    """Project a loaded scenario and resolved scorers into exact comparable semantics."""

    prompt_paths = [scenario.prompt.entry, *scenario.prompt.includes]
    payload: dict[str, Any] = {
        "scenario": scenario.name,
        "display_name": scenario.display_name,
        "revision": scenario.scenario_revision,
        "parent_revision": scenario.parent_revision,
        "description": scenario.description,
        "difficulty": scenario.difficulty,
        "task_kind": scenario.task_kind,
        "delivery_mode": scenario.delivery_mode,
        "workspace_state": scenario.workspace_state,
        "starting_conditions": scenario.starting_conditions,
        "languages": scenario.languages,
        "evaluation_role": scenario.evaluation_role,
        "catalog_status": scenario.catalog_status,
        "family_id": scenario.family_id,
        "variant_axes": scenario.variant_axes,
        "timeout_sec": scenario.timeout_sec,
        "environment": scenario.environment.model_dump(mode="json"),
        "starter": scenario.starter.model_dump(mode="json"),
        "verification": scenario.verification.model_dump(mode="json"),
        "requirements": scenario.requirements.model_dump(mode="json"),
        "evidence": scenario.evidence.model_dump(mode="json"),
        "visual": scenario.visual.model_dump(mode="json") if scenario.visual else None,
        "scorers": [
            ResolvedScorerReviewConfig(
                id=scorer.id,
                version=scorer.version,
                category=scorer.category,
                weight=scorer.weight,
                metrics=[metric.model_dump(mode="json") for metric in scorer.metrics],
            ).model_dump(mode="json")
            for scorer in scenario.resolved_scorers()
        ],
        "prompt": scenario.prompt.model_dump(mode="json"),
        "prompt_artifacts": _artifact_hashes(scenario_dir, prompt_paths),
        "rules_artifacts": _rules_hashes(scenario_dir),
    }
    payload["fingerprint"] = _sha256_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return ScenarioComparableConfig.model_validate(payload)


def _text_diff(before: object, after: object, *, max_lines: int = 160) -> TextDiff:
    before_lines = _json_text(before).splitlines()
    after_lines = _json_text(after).splitlines()
    raw_lines = list(difflib.ndiff(before_lines, after_lines))
    changed = [line for line in raw_lines if not line.startswith("? ")]
    projected: list[DiffLine] = []
    added = 0
    removed = 0
    for line in changed[:max_lines]:
        line_type: Literal["context", "added", "removed"]
        if line.startswith("+ "):
            line_type = "added"
            added += 1
        elif line.startswith("- "):
            line_type = "removed"
            removed += 1
        else:
            line_type = "context"
        projected.append(DiffLine(type=line_type, text=line[2:]))
    return TextDiff(
        added=added,
        removed=removed,
        truncated=len(changed) > max_lines,
        lines=projected,
    )


def _changed_categories(
    before: ScenarioComparableConfig,
    after: ScenarioComparableConfig,
) -> list[RevisionChangeCategory]:
    checks: list[tuple[RevisionChangeCategory, object, object]] = [
        (
            "metadata",
            (
                before.display_name,
                before.description,
                before.difficulty,
                before.task_kind,
                before.delivery_mode,
                before.workspace_state,
                before.starting_conditions,
                before.languages,
                before.evaluation_role,
                before.catalog_status,
                before.family_id,
                before.variant_axes,
                before.timeout_sec,
            ),
            (
                after.display_name,
                after.description,
                after.difficulty,
                after.task_kind,
                after.delivery_mode,
                after.workspace_state,
                after.starting_conditions,
                after.languages,
                after.evaluation_role,
                after.catalog_status,
                after.family_id,
                after.variant_axes,
                after.timeout_sec,
            ),
        ),
        ("environment", before.environment, after.environment),
        ("starter", before.starter, after.starter),
        ("verification", before.verification, after.verification),
        ("requirements", before.requirements, after.requirements),
        ("evidence", before.evidence, after.evidence),
        ("visual", before.visual, after.visual),
        ("scorers", before.scorers, after.scorers),
        (
            "prompt",
            (before.prompt, before.prompt_artifacts),
            (after.prompt, after.prompt_artifacts),
        ),
        ("rules", before.rules_artifacts, after.rules_artifacts),
    ]
    return [category for category, old, new in checks if old != new]


def revision_diff_artifact(
    before: ScenarioComparableConfig,
    after: ScenarioComparableConfig,
) -> RevisionDiffArtifact:
    """Build a typed semantic revision diff from persisted comparable configurations."""

    if before.scenario != after.scenario:
        raise ValueError("revision comparison requires the same scenario")
    categories = _changed_categories(before, after)
    before_dump = before.model_dump(mode="json")
    after_dump = after.model_dump(mode="json")
    metadata_keys = (
        "display_name",
        "description",
        "difficulty",
        "task_kind",
        "delivery_mode",
        "workspace_state",
        "starting_conditions",
        "languages",
        "evaluation_role",
        "catalog_status",
        "family_id",
        "variant_axes",
        "timeout_sec",
    )
    section_values: dict[str, tuple[object, object]] = {
        "metadata": (
            {key: before_dump[key] for key in metadata_keys},
            {key: after_dump[key] for key in metadata_keys},
        ),
        "environment": (before.environment, after.environment),
        "starter": (before.starter, after.starter),
        "verification": (before.verification, after.verification),
        "requirements": (before.requirements, after.requirements),
        "evidence": (before.evidence, after.evidence),
        "visual": (before.visual, after.visual),
        "scorers": (
            [item.model_dump(mode="json") for item in before.scorers],
            [item.model_dump(mode="json") for item in after.scorers],
        ),
        "prompt": (
            {"config": before.prompt, "artifacts": before.prompt_artifacts},
            {"config": after.prompt, "artifacts": after.prompt_artifacts},
        ),
        "rules": (before.rules_artifacts, after.rules_artifacts),
    }
    sections = {
        name: FileDiff(path=f"scenario-config:{name}", diff=_text_diff(old, new))
        for name, (old, new) in section_values.items()
    }
    summary = [f"{category} changed" for category in categories] or ["metadata unchanged"]
    return RevisionDiffArtifact(
        key=f"{after.scenario}:{before.revision}:{after.revision}",
        scenario=after.scenario,
        from_revision=before.revision,
        to_revision=after.revision,
        change_categories=categories,
        summary=summary,
        comparable_warnings=["evaluation profile changed"] if "scorers" in categories else [],
        sections=sections,
        files={
            "scenario": FileDiff(
                path="scenario-config",
                diff=_text_diff(before_dump, after_dump),
            ),
            "prompt": sections["prompt"],
        },
    )


def scenario_review_contract(
    scenario: ScenarioDefinition,
    *,
    scenario_dir: Path | None = None,
    previous_scenario: ScenarioDefinition | None = None,
    previous_scenario_dir: Path | None = None,
) -> ScenarioReviewContract:
    """Build the versioned scenario comparison contract persisted with an experiment."""

    current = scenario_comparable_config(scenario, scenario_dir=scenario_dir)
    if previous_scenario is None and scenario.parent_revision and scenario_dir is not None:
        previous_path = scenario_dir.parent / scenario.parent_revision / "scenario.yaml"
        if not previous_path.is_file():
            raise ValueError(
                f"parent revision scenario is required for comparison: {previous_path}"
            )
        from raidar.application.scenario_catalog import load_scenario

        previous_scenario = load_scenario(previous_path)
        previous_scenario_dir = previous_path.parent
    if previous_scenario is None:
        return ScenarioReviewContract(
            comparable_config=current,
            change_categories=["initial-revision"],
        )
    previous = scenario_comparable_config(previous_scenario, scenario_dir=previous_scenario_dir)
    revision_diff = revision_diff_artifact(previous, current)
    return ScenarioReviewContract(
        comparable_config=current,
        change_categories=revision_diff.change_categories,
        revision_diff=revision_diff,
    )
