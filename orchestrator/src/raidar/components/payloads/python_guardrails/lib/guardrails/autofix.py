"""``guardrail-check --autofix``: the pack's safe auto-fixers, applied atomically.

python-guardrails@8. The fix pass a repository's pre-commit autofix hook runs
before its check, and nothing else:

- ``ruff check --fix --no-unsafe-fixes`` with the protected config (a fix Ruff
  marks unsafe is never applied, whatever a config says), then ``ruff format``
  with the protected config;
- the targets are the scope's Python files (``guardrails.scope``) only; mypy,
  the tests check and every other check never run, and no other file is
  written;
- atomic: both fixers run in a throwaway copy of the workspace (excluded
  directories left out, as the tests check's copy) and only when both succeed
  are the changed files written back, each through its own regular-file path
  inside the root, never through a symlink. Any failure before the write-back
  leaves the workspace untouched.

What it reports (``AutofixResult.report``): the files changed and the lines
changed (lines added plus lines removed, per file and in all), the codes fixed
(each Ruff code's count before minus after, and ``FORMAT``: the files
``ruff format`` changed), the same for the block-tier codes only, and the
per-tool timings.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from guardrails.scope import EXCLUDED_DIRS, discover

AUTOFIX_SCHEMA = "python-guardrails.autofix-report/1"
FORMAT_CODE = "FORMAT"
TOOL_TIMEOUT_SEC = 120
_UNPARSABLE = re.compile(r"Failed to (parse|read)")


class AutofixError(RuntimeError):
    """The fix pass could not complete; ``kind`` names why (the report's ``error_type``)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class AutofixResult:
    """What one fix pass changed (or would have, before the write-back)."""

    files_checked: int = 0
    lines: dict[str, int] = field(default_factory=dict)
    """Changed file (root-relative) -> lines added plus lines removed."""
    before: Counter[str] = field(default_factory=Counter)
    after: Counter[str] = field(default_factory=Counter)
    unparsable: list[str] = field(default_factory=list)
    written: list[str] = field(default_factory=list)
    tools: list[dict[str, object]] = field(default_factory=list)

    def fixed(self) -> dict[str, int]:
        return dict(sorted((self.before - self.after).items()))

    def report(self, tier: Callable[[str], str]) -> dict[str, object]:
        fixed = self.fixed()
        return {
            "files_checked": self.files_checked,
            "files_changed": sorted(self.lines),
            "lines_changed": sum(self.lines.values()),
            "lines_changed_by_file": dict(sorted(self.lines.items())),
            "codes_fixed": fixed,
            "codes_fixed_blocking": {
                code: count for code, count in fixed.items() if tier(code) == "block"
            },
            "codes_before": dict(sorted(self.before.items())),
            "codes_after": dict(sorted(self.after.items())),
            "unparsable": sorted(self.unparsable),
            "written": self.written,
            "tools": self.tools,
        }


@dataclass(frozen=True)
class _Ruff:
    executable: str
    config: str

    def argv(self, *args: str) -> list[str]:
        verb, *rest = args
        return [self.executable, verb, "--config", self.config, "--no-cache", *rest]


def _run(name: str, argv: Sequence[str], cwd: Path, result: AutofixResult) -> tuple[int, str, str]:
    started = time.monotonic()
    try:
        done = subprocess.run(
            list(argv),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=TOOL_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise AutofixError("tool_timeout", f"{name} timed out after {TOOL_TIMEOUT_SEC}s") from exc
    except OSError as exc:
        raise AutofixError("tool_failed", f"{name} could not start: {exc}") from exc
    seconds = round(time.monotonic() - started, 3)
    result.tools.append({"name": name, "seconds": seconds, "returncode": done.returncode})
    return done.returncode, done.stdout, done.stderr


def _copy_workspace(root: Path, target: Path) -> None:
    def ignored(folder: str, names: list[str]) -> list[str]:
        del folder
        return [name for name in names if name in EXCLUDED_DIRS or name.endswith(".egg-info")]

    shutil.copytree(root, target, symlinks=True, ignore=ignored)


def _lint_codes(
    ruff: _Ruff, copy: Path, targets: Sequence[str], result: AutofixResult
) -> Counter[str]:
    rc, out, err = _run(
        "ruff-check",
        ruff.argv("check", "--output-format", "json", "--no-fix", "--exit-zero", *targets),
        copy,
        result,
    )
    if rc != 0:
        raise AutofixError("tool_failed", f"ruff check failed: {err.strip()[-400:]}")
    return Counter(str(item.get("code") or "E999") for item in json.loads(out or "[]"))


def _digests(copy: Path, targets: Sequence[str]) -> dict[str, bytes]:
    return {relpath: (copy / relpath).read_bytes() for relpath in targets}


def _format(
    ruff: _Ruff, copy: Path, targets: Sequence[str], result: AutofixResult, name: str
) -> int:
    """One ``ruff format`` pass; the files it changed (counted by their bytes, not its output)."""
    before = _digests(copy, targets)
    rc, out, err = _run(name, ruff.argv("format", *targets), copy, result)
    if rc != 0 and not _UNPARSABLE.search(err + out):
        raise AutofixError("tool_failed", f"ruff format failed: {err.strip()[-400:]}")
    if rc != 0:
        result.unparsable = sorted({n for n in targets if n in err + out} | set(result.unparsable))
    after = _digests(copy, targets)
    return sum(1 for relpath in targets if before[relpath] != after[relpath])


def _fix(ruff: _Ruff, copy: Path, targets: Sequence[str], result: AutofixResult) -> None:
    """Format, Ruff's safe fixes, format again; then what is left.

    ``FORMAT`` before is the files the first pass changed (what the gate's
    ``ruff format --check`` refuses on the raw tree); after, the files a last
    pass would still change (0 for an idempotent formatter). A file neither tool
    can parse is left as it is.
    """
    result.before = _lint_codes(ruff, copy, targets, result)
    formatted = _format(ruff, copy, targets, result, "ruff-format")
    rc, _, err = _run(
        "ruff-fix",
        ruff.argv("check", "--fix", "--no-unsafe-fixes", "--exit-zero", "--quiet", *targets),
        copy,
        result,
    )
    if rc != 0:
        raise AutofixError("tool_failed", f"ruff check --fix failed: {err.strip()[-400:]}")
    _format(ruff, copy, targets, result, "ruff-format")
    result.after = _lint_codes(ruff, copy, targets, result)
    remaining = _format(ruff, copy, targets, result, "ruff-format-recheck")
    if formatted:
        result.before[FORMAT_CODE] = formatted
    if remaining:
        result.after[FORMAT_CODE] = remaining


def lines_changed(old: bytes, new: bytes) -> int:
    """Lines removed plus lines added between two versions of a file."""
    matcher = difflib.SequenceMatcher(a=old.splitlines(), b=new.splitlines(), autojunk=False)
    return sum(
        (i2 - i1) + (j2 - j1) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"
    )


def _changes(root: Path, copy: Path, targets: Sequence[str]) -> dict[str, int]:
    changed: dict[str, int] = {}
    for relpath in targets:
        old, new = (root / relpath).read_bytes(), (copy / relpath).read_bytes()
        if old != new:
            changed[relpath] = lines_changed(old, new)
    return changed


def _write_back(root: Path, copy: Path, relpaths: Sequence[str], result: AutofixResult) -> None:
    """Write each changed file through its own regular-file path inside the root."""
    resolved = root.resolve()
    for relpath in relpaths:
        target = root / relpath
        if target.is_symlink() or not target.parent.resolve().is_relative_to(resolved):
            raise AutofixError("write_refused", f"{relpath} is not a regular file inside the root")
        data = (copy / relpath).read_bytes()
        try:
            handle = os.open(target, os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
        except OSError as exc:
            raise AutofixError(
                "write_back_failed",
                f"writing {relpath} failed after {len(result.written)} file(s): {exc}",
            ) from exc
        result.written.append(relpath)


def run_autofix(
    root: Path, ruff_executable: str, ruff_config: Path, *, apply: bool = True
) -> AutofixResult:
    """Fix the scope's Python files in a copy; write the changed ones back when ``apply``."""
    if not root.is_dir():
        raise AutofixError("root_missing", f"workspace root does not exist: {root}")
    ruff = _Ruff(ruff_executable, str(ruff_config))
    result = AutofixResult()
    targets = [source.relpath for source in discover(root)]
    result.files_checked = len(targets)
    if not targets:
        return result
    with tempfile.TemporaryDirectory(prefix="guardrails-autofix-") as tmp:
        copy = Path(tmp) / "workspace"
        try:
            _copy_workspace(root, copy)
        except (OSError, shutil.Error) as exc:
            raise AutofixError("copy_failed", f"copying the workspace failed: {exc}") from exc
        _fix(ruff, copy, targets, result)
        result.lines = _changes(root, copy, targets)
        if apply:
            _write_back(root, copy, sorted(result.lines), result)
    return result


__all__ = [
    "AUTOFIX_SCHEMA",
    "FORMAT_CODE",
    "AutofixError",
    "AutofixResult",
    "lines_changed",
    "run_autofix",
]
