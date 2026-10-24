"""Structure and naming rules (Enaible PYQ102-PYQ107, adapted to a single-root workspace).

Kept: canonical test groups (PYQ102), no generic catch-all directory or module
names (PYQ103, PYQ104), snake_case package directories (PYQ105) and module names
(PYQ107). Enaible's own roots, script allowlist and skill directories (PYQ100,
PYQ101, PYQ106) have no counterpart in a workspace its contributors lay out.
The generic-name allowlist starts empty: the workspace owns no exceptions.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath

from guardrails.diagnostics import Diagnostic
from guardrails.scope import SourceFile

GENERIC_NAME_PARTS: frozenset[str] = frozenset({"common", "helpers", "misc", "stuff", "temp"})
GENERIC_NAME_ALLOWLIST: frozenset[str] = frozenset()
TEST_ROOT = "tests"
ALLOWED_TEST_ROOT_FILES: frozenset[str] = frozenset({"tests/__init__.py", "tests/conftest.py"})
ALLOWED_TEST_GROUPS: frozenset[str] = frozenset(
    {"fixtures", "integration", "seams", "support", "unit"}
)


def _is_snake_case(name: str) -> bool:
    if name == "__init__":
        return True
    return bool(name) and all(char == "_" or char.islower() or char.isdigit() for char in name)


def _generic_names(relpath: str) -> list[Diagnostic]:
    found: list[Diagnostic] = []
    parts = PurePosixPath(relpath).parts
    for index, part in enumerate(parts[:-1]):
        prefix = "/".join(parts[: index + 1])
        if part in GENERIC_NAME_PARTS and prefix not in GENERIC_NAME_ALLOWLIST:
            found.append(
                Diagnostic("PYQ103", relpath, 1, f"generic catch-all directory name '{part}'")
            )
    stem = PurePosixPath(relpath).stem
    if stem in GENERIC_NAME_PARTS and relpath not in GENERIC_NAME_ALLOWLIST:
        found.append(Diagnostic("PYQ104", relpath, 1, f"generic catch-all module name '{stem}'"))
    return found


def _test_group(source: SourceFile) -> list[Diagnostic]:
    if not source.is_test or source.relpath in ALLOWED_TEST_ROOT_FILES:
        return []
    parts = PurePosixPath(source.relpath).parts
    if len(parts) >= 3 and parts[0] == TEST_ROOT and parts[1] in ALLOWED_TEST_GROUPS:
        return []
    groups = ", ".join(sorted(ALLOWED_TEST_GROUPS))
    return [
        Diagnostic(
            "PYQ102",
            source.relpath,
            1,
            f"test file must live under tests/<group>/ with group one of: {groups}",
        )
    ]


def _package_dirs(relpath: str) -> list[Diagnostic]:
    return [
        Diagnostic("PYQ105", relpath, 1, f"package directory '{part}' must use snake_case")
        for part in PurePosixPath(relpath).parts[:-1]
        if not _is_snake_case(part)
    ]


def _module_name(relpath: str) -> list[Diagnostic]:
    path = PurePosixPath(relpath)
    if path.stem.startswith("test-"):
        return [
            Diagnostic("PYQ107", relpath, 1, f"test module '{path.name}' must use test_snake_case")
        ]
    if _is_snake_case(path.stem):
        return []
    return [Diagnostic("PYQ107", relpath, 1, f"module '{path.name}' must use snake_case")]


def structure_diagnostics(sources: Iterable[SourceFile]) -> list[Diagnostic]:
    """Return naming and layout diagnostics for every discovered file."""
    found: list[Diagnostic] = []
    for source in sources:
        found.extend(_test_group(source))
        found.extend(_package_dirs(source.relpath))
        found.extend(_module_name(source.relpath))
        found.extend(_generic_names(source.relpath))
    return found
