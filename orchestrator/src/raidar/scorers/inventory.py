"""Assessor-owned file inventory (plan F3, design memo §3.3).

The assessor, not the agent, classifies every snapshot file as production,
test, generated or ignored. Classification follows the SCB fork's inventory
(``metrics/scoring/inventory.py``: recognised extensions, test path rules,
generated > test > production precedence) with one deliberate difference:
nothing the agent writes can move a file out of measurement.

- ``.gitignore`` (and any other ignore file) is never read.
- In-file ``@generated`` / ``Code generated`` / ``GENERATED CODE`` markers are
  recorded as agent claims but do not change a file's role. A file is
  ``generated`` only when an assessor-owned glob matches it.

The only exclusions are the oracle's snapshot excludes, which the grader
never sees either. Every excluded or ignored path is listed with a reason.

Evidence the assessor cannot read faithfully makes the inventory invalid
(``score_evidence_invalid``, as in the SCB fork) instead of silently shrinking
it: a recognised source file that is not UTF-8 or cannot be read, a path that
is not UTF-8, and a symlink whose target is missing, outside the snapshot or
not a regular file. A scorer that measures a snapshot must then report that
checkpoint as ``ineligible`` (enforced by ``validate_result``). Symlinks to
in-snapshot regular files are classified by their own path with the target's
bytes, as in the fork. One divergence from the fork is deliberate: a symlink
to a directory outside the snapshot is invalid here (the fork skips every
directory symlink); an in-snapshot directory symlink is listed as excluded,
because its target is inventoried at its real path.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from pydantic import Field

from raidar.oracle.snapshot import SNAPSHOT_EXCLUDES
from raidar.scorers.sdk.manifest import SdkModel
from raidar.spec.canonical import digest_json

RECOGNIZED_EXTENSIONS = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".java",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".cxx",
        ".hpp",
        ".cs",
        ".kt",
        ".kts",
        ".swift",
        ".scala",
    }
)
_TEST_SEGMENTS = frozenset({"test", "tests", "testing", "__tests__"})
_MARKER_LINES = 5
_GENERATED_MARKER = re.compile(
    r"(?:#|//|/\*).*?(?:@generated\b|\bCode generated\b|\bGENERATED CODE\b)", re.IGNORECASE
)
GENERATED_MARKER_CLAIM = "generated_marker"
INVALID_EVIDENCE = "score_evidence_invalid"


class FileRole(StrEnum):
    PRODUCTION = "production"
    TEST = "test"
    GENERATED = "generated"
    IGNORED = "ignored"


class InventoryPolicy(SdkModel):
    """Assessor-owned classification rules (from the experiment or scorer config)."""

    generated_globs: tuple[str, ...] = ()
    test_globs: tuple[str, ...] = ()


class InventoryFile(SdkModel):
    path: str = Field(min_length=1)
    role: FileRole
    sha256: str | None = None
    extension: str | None = None
    matched_rules: tuple[str, ...] = ()
    agent_claims: tuple[str, ...] = ()
    ignored_reason: str | None = None
    is_symlink: bool = False
    resolved_path: str | None = None


class InventoryExclusion(SdkModel):
    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class InventoryIssue(SdkModel):
    """Why the snapshot's evidence cannot be measured faithfully."""

    path: str = Field(min_length=1)
    code: str = INVALID_EVIDENCE
    detail: str = Field(min_length=1)


class Inventory(SdkModel):
    files: tuple[InventoryFile, ...]
    exclusions: tuple[InventoryExclusion, ...]
    invalid: tuple[InventoryIssue, ...] = ()

    @property
    def eligible(self) -> bool:
        """False when any evidence is unreadable; the checkpoint is then ineligible."""
        return not self.invalid

    def with_role(self, role: FileRole) -> tuple[InventoryFile, ...]:
        return tuple(item for item in self.files if item.role is role)

    def digest(self) -> str:
        return digest_json(self.model_dump(mode="json"))


def _match_segment(value: str, expression: str) -> bool:
    """``*`` and ``?`` within one path segment (SCB fork glob semantics)."""
    value_index = expression_index = 0
    star_index = retry_index = -1
    while value_index < len(value):
        if expression_index < len(expression) and expression[expression_index] in (
            "?",
            value[value_index],
        ):
            value_index += 1
            expression_index += 1
        elif expression_index < len(expression) and expression[expression_index] == "*":
            star_index = expression_index
            expression_index += 1
            retry_index = value_index
        elif star_index >= 0:
            expression_index = star_index + 1
            retry_index += 1
            value_index = retry_index
        else:
            return False
    return all(character == "*" for character in expression[expression_index:])


def matches_glob(path: str, pattern: str) -> bool:
    """Full-path-anchored glob: ``*``/``?`` stay in a segment, ``**`` spans segments."""
    path_parts = path.split("/")
    pattern_parts = pattern.split("/")

    def match(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        segment = pattern_parts[pattern_index]
        if segment == "**":
            return any(
                match(next_index, pattern_index + 1)
                for next_index in range(path_index, len(path_parts) + 1)
            )
        return (
            path_index < len(path_parts)
            and _match_segment(path_parts[path_index], segment)
            and match(path_index + 1, pattern_index + 1)
        )

    return match(0, 0)


@dataclass(frozen=True)
class _Entry:
    path: str
    absolute: Path
    is_symlink: bool


def _display_path(path: Path, root: Path) -> tuple[str, bool]:
    """The snapshot-relative path, and whether it is valid UTF-8."""
    relative = path.relative_to(root).as_posix()
    try:
        relative.encode("utf-8")
    except UnicodeEncodeError:
        return os.fsencode(relative).decode("utf-8", "backslashreplace"), False
    return relative, True


def _walk(root: Path) -> Iterator[_Entry | InventoryExclusion | InventoryIssue]:
    """Files and symlinks in byte order; assessor-excluded directories are reported."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        kept: list[str] = []
        for name in sorted(dirnames):
            relative, readable = _display_path(base / name, root)
            if not readable:
                yield InventoryIssue(path=relative, detail="path is not UTF-8")
            elif (base / name).is_symlink():
                yield _Entry(relative, base / name, True)
            elif name in SNAPSHOT_EXCLUDES:
                yield InventoryExclusion(path=relative, reason=f"snapshot_exclude:{name}")
            else:
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            path = base / name
            relative, readable = _display_path(path, root)
            if readable:
                yield _Entry(relative, path, path.is_symlink())
            else:
                yield InventoryIssue(path=relative, detail="path is not UTF-8")


def _basename_test_rules(basename: str, extension: str) -> list[str]:
    rules = []
    if basename == "conftest.py":
        rules.append("test_basename:conftest.py")
    if extension == ".py" and (basename.startswith("test_") or basename.endswith("_test.py")):
        rules.append("test_basename:python")
    if basename[: -len(extension)].endswith((".test", ".spec")):
        rules.append("test_basename:dot_test_or_spec")
    return rules


def _test_rules(path: str, extension: str, policy: InventoryPolicy) -> list[str]:
    parts = path.split("/")
    rules = [f"test_glob:{pattern}" for pattern in policy.test_globs if matches_glob(path, pattern)]
    rules += [f"test_segment:{part}" for part in parts[:-1] if part in _TEST_SEGMENTS]
    return rules + _basename_test_rules(parts[-1], extension)


@dataclass(frozen=True)
class _Target:
    """Where an entry's bytes live: itself, an in-snapshot symlink target, or nowhere."""

    path: Path | None
    resolved_path: str | None = None
    directory: bool = False
    invalid: str | None = None


def _target(entry: _Entry, root: Path) -> _Target:
    if not entry.is_symlink:
        return _Target(entry.absolute)
    try:
        resolved = entry.absolute.resolve(strict=True)
    except (OSError, RuntimeError):
        return _Target(None, invalid="symlink target is missing")
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError:
        return _Target(None, invalid="symlink target is outside the snapshot")
    if resolved.is_dir():
        return _Target(None, relative, directory=True)
    if not resolved.is_file():
        return _Target(None, relative, invalid="symlink target is not a regular file")
    return _Target(resolved, relative)


def _ignored(
    entry: _Entry,
    target: _Target,
    reason: str,
    *,
    extension: str | None = None,
    sha256: str | None = None,
) -> InventoryFile:
    return InventoryFile(
        path=entry.path,
        role=FileRole.IGNORED,
        sha256=sha256,
        extension=extension,
        ignored_reason=reason,
        is_symlink=entry.is_symlink,
        resolved_path=target.resolved_path,
    )


def _roles(
    entry: _Entry, target: _Target, extension: str, data: bytes, policy: InventoryPolicy
) -> tuple[InventoryFile, InventoryIssue | None]:
    """Role of a readable recognised source file (generated > test > production)."""
    sha256 = hashlib.sha256(data).hexdigest()
    try:
        head = data.decode("utf-8").splitlines()[:_MARKER_LINES]
    except UnicodeDecodeError:
        return _ignored(
            entry, target, "non_utf8", sha256=sha256, extension=extension
        ), InventoryIssue(path=entry.path, detail="source is not UTF-8")
    claims = (GENERATED_MARKER_CLAIM,) if any(_GENERATED_MARKER.search(x) for x in head) else ()
    generated = [
        f"generated_glob:{pattern}"
        for pattern in policy.generated_globs
        if matches_glob(entry.path, pattern)
    ]
    tests = _test_rules(entry.path, extension, policy)
    role = FileRole.GENERATED if generated else FileRole.TEST if tests else FileRole.PRODUCTION
    return InventoryFile(
        path=entry.path,
        role=role,
        sha256=sha256,
        extension=extension,
        matched_rules=(*generated, *tests),
        agent_claims=claims,
        is_symlink=entry.is_symlink,
        resolved_path=target.resolved_path,
    ), None


def _classify(
    entry: _Entry, root: Path, policy: InventoryPolicy
) -> tuple[InventoryFile, InventoryIssue | None]:
    extension = PurePosixPath(entry.path).suffix
    target = _target(entry, root)
    issue = InventoryIssue(path=entry.path, detail=target.invalid) if target.invalid else None
    if target.directory:
        return _ignored(entry, target, "symlink_to_directory"), None
    if extension not in RECOGNIZED_EXTENSIONS:
        reason = "unrecognized_extension" if extension else "extensionless"
        return _ignored(entry, target, reason), issue
    if target.path is None:
        return _ignored(entry, target, "symlink_invalid", extension=extension), issue
    try:
        data = target.path.read_bytes()
    except OSError as exc:
        detail = f"unreadable: {exc.strerror or type(exc).__name__}"
        return _ignored(entry, target, "unreadable", extension=extension), InventoryIssue(
            path=entry.path, detail=detail
        )
    return _roles(entry, target, extension, data, policy)


def _collect(
    root: Path, policy: InventoryPolicy
) -> tuple[list[InventoryFile], list[InventoryExclusion], list[InventoryIssue]]:
    resolved_root = root.resolve(strict=True)
    files: list[InventoryFile] = []
    exclusions: list[InventoryExclusion] = []
    issues: list[InventoryIssue] = []
    for item in _walk(root):
        if isinstance(item, InventoryExclusion):
            exclusions.append(item)
        elif isinstance(item, InventoryIssue):
            issues.append(item)
            exclusions.append(InventoryExclusion(path=item.path, reason="non_utf8_path"))
        else:
            classified, issue = _classify(item, resolved_root, policy)
            files.append(classified)
            issues.extend([issue] if issue is not None else [])
    return files, exclusions, issues


def build_inventory(root: Path, policy: InventoryPolicy | None = None) -> Inventory:
    """Classify every file under a snapshot root; agent-written exclusions are ignored."""
    mode = root.lstat().st_mode if root.exists() or root.is_symlink() else 0
    if not stat.S_ISDIR(mode):
        raise NotADirectoryError(f"snapshot root is not a directory: {root}")
    files, exclusions, issues = _collect(root, policy or InventoryPolicy())
    ordered = sorted(files, key=lambda item: item.path.encode())
    exclusions.extend(
        InventoryExclusion(path=item.path, reason=item.ignored_reason)
        for item in ordered
        if item.ignored_reason is not None
    )
    return Inventory(
        files=tuple(ordered),
        exclusions=tuple(sorted(exclusions, key=lambda item: item.path.encode())),
        invalid=tuple(sorted(issues, key=lambda item: item.path.encode())),
    )
