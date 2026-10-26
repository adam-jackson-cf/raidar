"""Reading markdown the engine's checks work on: sections, tables and row ids.

The feature definition and the summary are markdown files. :func:`split` cuts
one into its ``## `` sections, :func:`table` reads the first markdown table in
a section (the ADR table) and :func:`lead` takes a row's id. :class:`Fences`
follows fenced code blocks line by line, the one rule every reader of an issue
or a definition shares.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}")
# A fence line: a run of three or more backticks or tildes (CommonMark).
_FENCE = re.compile(r"^\s*(?P<marker>`{3,}|~{3,})(?P<rest>.*)$")

type Row = dict[str, str]


@dataclass(frozen=True)
class Table:
    columns: tuple[str, ...]
    rows: tuple[Row, ...]


@dataclass
class Fences:
    """Follow fenced code blocks over lines read in order.

    A block opens with a run of backticks or tildes and closes with a run of
    the same character, at least as long and with nothing after it (CommonMark);
    a fence line of the other kind inside a block is part of its code. Without a
    closing fence the block runs to the end, as markdown reads it.
    """

    marker: str = ""

    @property
    def inside(self) -> bool:
        """Whether the lines read so far leave a block open."""
        return bool(self.marker)

    def delimits(self, line: str) -> bool:
        """Read ``line``; whether it opens or closes a block (and so is no code)."""
        found = _FENCE.match(line)
        if found is None:
            return False
        marker = found["marker"]
        if not self.marker:
            # A backtick run with a backtick after it is a code span, not a fence.
            if marker[0] == "`" and "`" in found["rest"]:
                return False
            self.marker = marker
            return True
        closes = (
            marker[0] == self.marker[0]
            and len(marker) >= len(self.marker)
            and not found["rest"].strip()
        )
        if closes:
            self.marker = ""
        return closes


def split(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Split markdown into the text before the first ``## `` and its sections."""
    head: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    fences = Fences()
    for line in text.splitlines():
        fenced = fences.delimits(line) or fences.inside
        if not fenced and line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            head.append(line)
    return "\n".join(head), [(name, "\n".join(body)) for name, body in sections]


def _cells(line: str) -> list[str]:
    body = line.strip()
    body = body.removeprefix("|")
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and body[index + 1 : index + 2] == "|":
            current.append("|")
            index += 2
            continue
        if char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    cells.append("".join(current).strip())
    return cells


def table(body: str) -> Table | None:
    """Read the first markdown table in ``body``; ``None`` when it has none."""
    lines = [line for line in body.splitlines() if line.strip().startswith("|")]
    if len(lines) < 2 or not _SEPARATOR.match(lines[1].strip()):
        return None
    columns = tuple(_cells(lines[0]))
    rows = []
    for line in lines[2:]:
        cells = _cells(line)
        cells += [""] * (len(columns) - len(cells))
        rows.append(dict(zip(columns, cells, strict=False)))
    return Table(columns, tuple(rows))


def lead(cell: str) -> str:
    """Return a row's id: the first word of ``F1 Feature name``."""
    words = cell.split()
    return words[0] if words else ""
