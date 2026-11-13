"""Fast behavioural proofs for repository hygiene policy."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]


class Checker(Protocol):
    def skill_findings(self, root: Path, inventory: Sequence[str]) -> list[str]: ...


@pytest.fixture
def checker() -> Checker:
    spec = importlib.util.spec_from_file_location(
        "skill_hygiene_proof", ROOT / "scripts/checks/check-test-hygiene.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("Checker", module)


@pytest.mark.parametrize("prefix", ["Use when", "USE WHEN", "use when"])
def test_valid_skill_metadata_and_existing_support_links_are_accepted(
    tmp_path: Path, checker: Checker, prefix: str
) -> None:
    skill = tmp_path / ".agents/skills/probe"
    skill.mkdir(parents=True)
    (skill / "support.txt").write_text("support", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        f"---\nname: probe\ndescription: {prefix} checking probes.\n---\n"
        "[Support](support.txt#section) [Web](https://example.org/missing) [Here](#section)\n",
        encoding="utf-8",
    )
    assert (
        checker.skill_findings(
            tmp_path, [".agents/skills/probe/SKILL.md", ".agents/skills/probe/support.txt"]
        )
        == []
    )


@pytest.mark.parametrize(
    "contents",
    [
        "# Missing frontmatter\n",
        "---\n- not-a-mapping\n---\n",
        "---\nname: probe\n---\n",
        "---\ndescription: Use when checking.\n---\n",
        "---\nname: different\ndescription: Use when checking.\n---\n",
        "---\nname: probe\ndescription: General advice.\n---\n",
        "---\nname: probe\ndescription: 17\n---\n",
        "---\nname: probe\ndescription: Use when checking.\n---\n[Support](missing.txt)\n",
    ],
)
def test_invalid_metadata_or_missing_support_is_rejected(
    tmp_path: Path, checker: Checker, contents: str
) -> None:
    path = tmp_path / ".agents/skills/probe/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(contents, encoding="utf-8")
    found = checker.skill_findings(tmp_path, [path.relative_to(tmp_path).as_posix()])
    assert found
    assert all("SKILL.metadata" in line for line in found)
    assert any(".agents/skills/probe/SKILL.md" in line for line in found)
