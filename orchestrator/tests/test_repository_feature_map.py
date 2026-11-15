"""Fast behavioural proofs for the feature-map and journey policy."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
LEVER = "node scripts/surface/lever.mjs"
JOURNEY = "review-surface/journeys/plan.journey.mjs"
FEATURE = f"""# Plan an experiment

Intro.

## Sub-features

- Lock an experiment.

## How to get to it (user POV)

Run `raidar experiment lock`.

## Driving it with `{LEVER}`

See the [journey](../../{JOURNEY}) and [gotchas](#gotchas).

## Gotchas

None known.
"""
README = "# Features\n\n| [Plan](plan.md) |\n"


class Checker(Protocol):
    def feature_map_findings(self, root: Path, inventory: Sequence[str]) -> list[str]: ...

    def journey_findings(self, root: Path, inventory: Sequence[str]) -> list[str]: ...


@pytest.fixture
def checker() -> Checker:
    spec = importlib.util.spec_from_file_location(
        "feature_map_proof", ROOT / "scripts/checks/check-feature-map.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("Checker", module)


def write_tree(root: Path, files: dict[str, str]) -> list[str]:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return sorted(files)


def clean_files() -> dict[str, str]:
    return {
        "docs/features/README.md": README,
        "docs/features/plan.md": FEATURE,
        JOURNEY: f"// Run: {LEVER} --journey {JOURNEY}\n",
        "scripts/surface/lever.mjs": "export function surface() {}\n",
    }


def test_a_complete_feature_map_and_mapped_journey_are_accepted(
    tmp_path: Path, checker: Checker
) -> None:
    inventory = write_tree(tmp_path, clean_files())
    assert checker.feature_map_findings(tmp_path, inventory) == []
    assert checker.journey_findings(tmp_path, inventory) == []


@pytest.mark.parametrize(
    ("document", "problem"),
    [
        (FEATURE.replace("Intro.", "# Second title"), "has 2 H1 headings"),
        (FEATURE.replace("# Plan an experiment\n", ""), "has 0 H1 headings"),
        (FEATURE.replace("## Gotchas\n\nNone known.\n", ""), "H2 headings are"),
        (
            FEATURE.replace("## Sub-features", "## Gotchas").replace(
                "## Gotchas\n\nNone known.", "## Sub-features\n\nNone known."
            ),
            "H2 headings are",
        ),
        (FEATURE.replace(f"`{LEVER}`", "make"), "H2 headings are"),
        (FEATURE.replace("- Lock an experiment.", ""), "section 'Sub-features' is empty"),
        (FEATURE.replace("../../", "../"), "does not resolve"),
        (FEATURE.replace("(../../", "(../../../../"), "does not resolve"),
        (FEATURE.replace("#gotchas", "#missing"), "names a missing heading"),
        (FEATURE + "\n[readme](README.md#nowhere)\n", "names a missing heading"),
    ],
)
def test_feature_documents_breaking_the_map_shape_are_rejected(
    tmp_path: Path, checker: Checker, document: str, problem: str
) -> None:
    inventory = write_tree(tmp_path, {**clean_files(), "docs/features/plan.md": document})
    found = checker.feature_map_findings(tmp_path, inventory)
    assert found
    assert all(line.startswith("docs/features/plan.md: SURFACE.feature-map: ") for line in found)
    assert any(problem in line for line in found)


def test_index_links_are_checked_and_duplicate_features_are_rejected(
    tmp_path: Path, checker: Checker
) -> None:
    files = {
        **clean_files(),
        "docs/features/README.md": README + "[gone](gone.md)\n",
        "docs/features/again.md": FEATURE,
    }
    found = checker.feature_map_findings(tmp_path, write_tree(tmp_path, files))
    assert any("README.md: SURFACE.feature-map: link 'gone.md'" in line for line in found)
    assert any(
        line.startswith("docs/features/plan.md: SURFACE.feature-map: feature 'Plan an experiment'")
        and "also documented in docs/features/again.md" in line
        for line in found
    )


def test_an_empty_feature_map_is_rejected(tmp_path: Path, checker: Checker) -> None:
    found = checker.feature_map_findings(tmp_path, write_tree(tmp_path, {"README.md": "# R\n"}))
    assert found == [
        "docs/features: SURFACE.feature-map: the feature map holds no documents; "
        "write one document per feature"
    ]


@pytest.mark.parametrize(
    ("files", "problem"),
    [
        ({JOURNEY: "export default async () => {};\n"}, "does not reference the lever"),
        (
            {"docs/features/plan.md": FEATURE.replace(JOURNEY, "README.md")},
            "not linked from any feature-map document",
        ),
        (
            {"review-surface/journeys/plan.spec.mjs": f"// {LEVER}\n"},
            "does not match 'review-surface/journeys/*.journey.mjs'",
        ),
    ],
)
def test_journeys_off_pattern_unmapped_or_bypassing_the_lever_are_rejected(
    tmp_path: Path, checker: Checker, files: dict[str, str], problem: str
) -> None:
    inventory = write_tree(tmp_path, {**clean_files(), **files})
    found = checker.journey_findings(tmp_path, inventory)
    assert found
    assert all("SURFACE.journeys" in line for line in found)
    assert any(problem in line for line in found)


def test_a_journey_reaching_the_lever_by_import_is_accepted(
    tmp_path: Path, checker: Checker
) -> None:
    journey = "import { surface } from '../../scripts/surface/lever.mjs';\n"
    inventory = write_tree(tmp_path, {**clean_files(), JOURNEY: journey})
    assert checker.journey_findings(tmp_path, inventory) == []
