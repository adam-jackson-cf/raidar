"""Condition diffs: what differs between the baseline arm and another arm of one experiment.

The surface calls these "contract changes" between arms. Each diff compares the
baseline arm's condition with another condition of the same experiment lock and
problem:

- ``files.condition``: the locked condition manifest (components, file hashes, ``with``);
- ``files.prompt``: the first checkpoint's rendered instruction, as the agent saw it;
- ``sections``: every file of the materialised workspace overlay.

``comparable_warnings`` come from the arm-diff guard: every noun (the condition
or an agent field) that differs between the two arms but is not a declared
factor. The lock refuses such experiments, so a warning here means the compared
arms did not come from one factor-guarded design.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from raidar.schemas.review import (
    ArmChangeCategory,
    ArmDiffArtifact,
    DiffLine,
    FileDiff,
    TextDiff,
)
from raidar.spec.factor_guard import arm_nouns
from raidar.spec.schema import ArmSpec, ExperimentSpec

MAX_DIFF_LINES = 160
INSTRUCTION_FILE = "instruction.md"
_COMPONENT_CATEGORIES: dict[str, ArmChangeCategory] = {
    "rules-file": "rules",
    "skills": "rules",
    "workspace-files": "starter",
    "lint-config": "starter",
    "prompt-wrapper": "prompt",
    "harness-settings": "environment",
    "stop-hook": "environment",
    "mcp-server": "environment",
    "review-loop": "environment",
}


@dataclass(frozen=True)
class ArmSide:
    """One side of a condition diff: an arm, its condition label and its materialised task."""

    arm: ArmSpec
    label: str
    manifest: object
    task_dir: Path | None


def text_diff(before: str, after: str) -> TextDiff:
    changed = [
        line
        for line in difflib.ndiff(before.splitlines(), after.splitlines())
        if not line.startswith("? ")
    ]
    lines: list[DiffLine] = []
    counts = {"added": 0, "removed": 0}
    for line in changed[:MAX_DIFF_LINES]:
        kind: Literal["context", "added", "removed"] = (
            "added" if line.startswith("+ ") else "removed" if line.startswith("- ") else "context"
        )
        if kind != "context":
            counts[kind] += 1
        lines.append(DiffLine(type=kind, text=line[2:]))
    return TextDiff(
        added=counts["added"],
        removed=counts["removed"],
        truncated=len(changed) > MAX_DIFF_LINES,
        lines=lines,
    )


def _overlay(task_dir: Path | None) -> dict[str, str]:
    """Instruction and workspace overlay of the first checkpoint, by relative path."""
    if task_dir is None:
        return {}
    steps = sorted(path for path in (task_dir / "steps").glob("*") if path.is_dir())
    if not steps:
        return {}
    first = steps[0]
    return {
        path.relative_to(first).as_posix(): path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(first.rglob("*"))
        if path.is_file()
    }


def _manifest_text(side: ArmSide) -> str:
    return yaml.safe_dump({"condition": side.label, "components": side.manifest}, sort_keys=True)


def _component_names(manifest: object) -> set[str]:
    if not isinstance(manifest, list):
        return set()
    return {str(item.get("use", "")) for item in manifest if isinstance(item, Mapping)}


def _categories(before: ArmSide, after: ArmSide, nouns: Sequence[str]) -> list[ArmChangeCategory]:
    changed = _component_names(before.manifest) ^ _component_names(after.manifest)
    categories: set[ArmChangeCategory] = {
        _COMPONENT_CATEGORIES.get(ref.split("@", 1)[0], "environment") for ref in changed
    }
    if before.manifest != after.manifest and not changed:
        categories.add("rules")
    if any(noun.startswith("agent.") for noun in nouns):
        categories.add("metadata")
    return sorted(categories)


def differing_nouns(spec: ExperimentSpec, before: ArmSpec, after: ArmSpec) -> list[str]:
    first, second = arm_nouns(spec, before), arm_nouns(spec, after)
    return sorted(noun for noun in first if first[noun] != second[noun])


def _undeclared(spec: ExperimentSpec, nouns: Sequence[str]) -> list[str]:
    return [
        f"{noun} differs outside the declared factors ({', '.join(spec.factors) or 'none'})"
        for noun in nouns
        if not any(noun == factor or noun.startswith(f"{factor}.") for factor in spec.factors)
    ]


def _summary(before: ArmSide, after: ArmSide, nouns: Sequence[str]) -> list[str]:
    added = sorted(_component_names(after.manifest) - _component_names(before.manifest))
    removed = sorted(_component_names(before.manifest) - _component_names(after.manifest))
    lines = [f"arm {before.arm.id} -> {after.arm.id}"]
    lines += [f"+ component {ref}" for ref in added]
    lines += [f"- component {ref}" for ref in removed]
    lines += [f"{noun} differs" for noun in nouns if noun != "condition"]
    return lines


def condition_diff(
    spec: ExperimentSpec,
    problem: str,
    before: ArmSide,
    after: ArmSide,
    extra_warnings: Sequence[str] = (),
) -> ArmDiffArtifact:
    nouns = differing_nouns(spec, before.arm, after.arm)
    old, new = _overlay(before.task_dir), _overlay(after.task_dir)
    sections = {
        "condition.yaml": FileDiff(
            path="condition.yaml", diff=text_diff(_manifest_text(before), _manifest_text(after))
        ),
        **{
            path: FileDiff(path=path, diff=text_diff(old.get(path, ""), new.get(path, "")))
            for path in sorted(set(old) | set(new))
            if old.get(path) != new.get(path)
        },
    }
    prompt_path = INSTRUCTION_FILE
    return ArmDiffArtifact(
        key=f"{problem}:{before.label}->{after.label}",
        problem=problem,
        from_arm=before.label,
        to_arm=after.label,
        change_categories=_categories(before, after, nouns),
        summary=_summary(before, after, nouns),
        comparable_warnings=[*_undeclared(spec, nouns), *extra_warnings],
        sections=sections,
        files={
            "condition": sections["condition.yaml"],
            "prompt": FileDiff(
                path=prompt_path,
                diff=text_diff(old.get(prompt_path, ""), new.get(prompt_path, "")),
            ),
        },
    )
