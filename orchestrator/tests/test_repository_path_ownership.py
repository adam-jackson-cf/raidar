"""Fail-closed owner-map proofs for the four repository-path rule IDs."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]


class ImportResult(Protocol):
    """Result fields consumed by the import-contract shell."""

    @property
    def returncode(self) -> int: ...


class ImportRunner(Protocol):
    """Bounded import checker process boundary."""

    def __call__(self, args: Sequence[str], **kwargs: object) -> ImportResult: ...


class PathPolicy(Protocol):
    """Public path decisions; root permits isolated source-edge fixtures."""

    def path_findings(self, root: Path, paths: Sequence[str]) -> list[str]: ...

    def dependency_findings(
        self, paths: Sequence[str], *, root: Path, run: ImportRunner
    ) -> list[str]: ...


@pytest.fixture
def policy() -> PathPolicy:
    """Import the actual gate checker, not a copy of its decisions."""
    spec = importlib.util.spec_from_file_location(
        "repository_path_ownership_proof", ROOT / "scripts/checks/check-repository-paths.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("PathPolicy", module)


def _source(root: Path, relative: str, source: str) -> None:
    candidate = root / relative
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(source, encoding="utf-8")


def _assert_actionable(findings: list[str], relative: str, rule: str) -> None:
    matched = [line for line in findings if line.startswith(f"{relative}: {rule}: ")]
    assert matched, f"{rule}: {relative} was accepted without an owner/fix"
    for line in matched:
        problem, separator, repair = line.partition(";")
        assert problem.removeprefix(f"{relative}: {rule}: ").strip()
        assert separator
        assert repair.strip()
        assert "\n" not in line


class SuccessfulImportCheck:
    """A passing legacy layer checker cannot mask unmapped source edges."""

    returncode = 0

    def __call__(self, args: Sequence[str], **kwargs: object) -> ImportResult:
        return self


@pytest.mark.parametrize(
    "relative",
    [
        "orchestrator/src/raidar/engine/trial/probe.py",
        "contract/src/raidar_contract/probe.py",
        "README.md",
    ],
)
def test_declared_capability_paths_are_accepted(
    policy: PathPolicy, tmp_path: Path, relative: str
) -> None:
    """STRUCT.folder-placement: established source/data owners remain usable."""
    _source(tmp_path, relative, "VALUE = 1\n")
    assert policy.path_findings(tmp_path, [relative]) == []


@pytest.mark.parametrize(
    "relative",
    [
        "orchestrator/unmapped/probe.py",
        "contract/unmapped/probe.py",
        "scripts/unmapped/probe.py",
        "review-surface/unmapped/probe.py",
        "orchestrator/src/raidar/engine/trial/notes.txt",
    ],
)
def test_allowed_top_level_directory_does_not_own_arbitrary_descendants(
    policy: PathPolicy, tmp_path: Path, relative: str
) -> None:
    """STRUCT.folder-placement: a root allowlist is not a complete owner map."""
    _source(tmp_path, relative, "VALUE = 1\n")
    _assert_actionable(
        policy.path_findings(tmp_path, [relative]), relative, "STRUCT.folder-placement"
    )


@pytest.mark.parametrize(
    ("source", "accepted"),
    [
        ("def run_probe() -> None:\n    pass\n", True),
        ("class ProbeResult:\n    pass\n", True),
        ("def RunProbe() -> None:\n    pass\n", False),
        ("class probe_result:\n    pass\n", False),
    ],
)
def test_symbol_names_obey_the_owning_python_convention(
    policy: PathPolicy, tmp_path: Path, source: str, *, accepted: bool
) -> None:
    """NAME.conventions: valid module placement must not bypass symbol names."""
    relative = "orchestrator/src/raidar/engine/trial/probe.py"
    _source(tmp_path, relative, source)
    found = policy.path_findings(tmp_path, [relative])
    if accepted:
        assert found == []
    else:
        _assert_actionable(found, relative, "NAME.conventions")


@pytest.mark.parametrize(
    ("relative", "tier", "accepted"),
    [
        ("orchestrator/tests/test_repository_gate_rules.py", None, True),
        ("orchestrator/tests/test_repository_gate_rules.py", "quality", False),
        ("orchestrator/tests/test_unmapped_capability.py", None, False),
        ("contract/conformance/test_unmapped_capability.py", None, False),
        ("lab/stages/unmapped/unmapped/tests/test_probe.py", None, False),
    ],
)
def test_test_directory_alone_does_not_establish_behavior_ownership(
    policy: PathPolicy, tmp_path: Path, relative: str, tier: str | None, *, accepted: bool
) -> None:
    """TEST.placement: tests need the declared behavior owner and tier."""
    source = "def test_probe() -> None:\n    assert True\n"
    if tier is not None:
        source = f"import pytest\npytestmark = pytest.mark.{tier}\n\n{source}"
    _source(tmp_path, relative, source)
    found = policy.path_findings(tmp_path, [relative])
    if accepted:
        assert found == []
    else:
        _assert_actionable(found, relative, "TEST.placement")


@pytest.mark.parametrize(
    ("relative", "source", "accepted"),
    [
        (
            "orchestrator/src/raidar/engine/trial/probe.py",
            "from raidar.subject.primitive import execution\n",
            True,
        ),
        (
            "orchestrator/src/raidar/engine/trial/probe.py",
            "from raidar.unmapped_owner import execution\n",
            False,
        ),
        (
            "contract/src/raidar_contract/probe.py",
            "from raidar.engine.trial import execution\n",
            False,
        ),
        (
            "lab/stages/raidar/define/tool/src/define_tool/probe.py",
            "from raidar.engine.trial import execution\n",
            False,
        ),
    ],
)
def test_dependency_edges_require_a_declared_source_and_destination_owner(
    policy: PathPolicy, tmp_path: Path, relative: str, source: str, *, accepted: bool
) -> None:
    """STRUCT.dependency-direction: even a green legacy checker fails unknown edges."""
    _source(tmp_path, relative, source)
    found = policy.dependency_findings([relative], root=tmp_path, run=SuccessfulImportCheck())
    if accepted:
        assert found == []
    else:
        _assert_actionable(found, relative, "STRUCT.dependency-direction")
        assert any("raidar." in line for line in found)
