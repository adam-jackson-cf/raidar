"""Unchanged-span detection for carrying Jev answers forward (plan H3).

A port of the diff-hunk arithmetic in the SCB fork's
``metrics/rubric/carry_forward.py`` (``parse_diff_hunks``,
``compute_line_offset``, ``is_span_unchanged``), with the same unified diff
the fork stores in ``diff.json`` (``difflib.unified_diff`` with 3 context
lines, ``lineterm=""``). A span that touches a hunk, context lines included,
counts as changed, exactly as in the fork.

SCB carries a flagged *region* forward when it was not re-flagged. Raidar asks
per *symbol*, so a symbol is carried when its prior span is unchanged under
these rules and it sits at the offset-adjusted position in the new file; its
prior answers are then reused without a new Jev call.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
CONTEXT_LINES = 3


@dataclass(frozen=True)
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int

    @property
    def old_end(self) -> int:
        return self.old_start + self.old_count

    @property
    def delta(self) -> int:
        return self.new_count - self.old_count


def unified_diff_text(prior: str, current: str, path: str) -> str:
    """The fork's per-file ``diff_text`` for a prior and current file."""
    lines = difflib.unified_diff(
        prior.splitlines(keepends=True),
        current.splitlines(keepends=True),
        fromfile=path,
        tofile=path,
        lineterm="",
        n=CONTEXT_LINES,
    )
    return "\n".join(lines)


def parse_diff_hunks(diff_text: str) -> list[DiffHunk]:
    hunks = []
    for line in diff_text.splitlines():
        match = HUNK_HEADER.match(line)
        if match:
            old_count, new_count = match.group(2), match.group(4)
            hunks.append(
                DiffHunk(
                    old_start=int(match.group(1)),
                    old_count=int(old_count) if old_count else 1,
                    new_start=int(match.group(3)),
                    new_count=int(new_count) if new_count else 1,
                )
            )
    return hunks


def compute_line_offset(hunks: list[DiffHunk], old_line: int) -> tuple[int, bool]:
    """``(new line, inside a changed hunk)`` for a line of the old file."""
    cumulative = 0
    for hunk in hunks:
        if hunk.old_count == 0:
            # A zero-context insertion after old_start; the boundary line stays before it.
            if old_line <= hunk.old_start:
                return old_line + cumulative, False
            cumulative += hunk.delta
            continue
        if old_line < hunk.old_start:
            return old_line + cumulative, False
        if old_line < hunk.old_end:
            return hunk.new_start + (old_line - hunk.old_start), True
        cumulative += hunk.delta
    return old_line + cumulative, False


def is_span_unchanged(
    hunks: list[DiffHunk], start: int, end: int | None
) -> tuple[bool, int, int | None]:
    """``(unchanged, new start, new end)`` for an old-file span, as in the fork."""
    effective_end = end if end is not None else start
    new_start, start_changed = compute_line_offset(hunks, start)
    new_end, end_changed = compute_line_offset(hunks, effective_end)
    reported_end = new_end if end is not None else None
    if start_changed or end_changed:
        return False, new_start, reported_end
    for hunk in hunks:
        if hunk.old_count == 0:
            if start <= hunk.old_start < effective_end:
                return False, new_start, reported_end
            continue
        if hunk.old_start >= start and hunk.old_end <= effective_end + 1:
            return False, new_start, reported_end
    return True, new_start, reported_end
