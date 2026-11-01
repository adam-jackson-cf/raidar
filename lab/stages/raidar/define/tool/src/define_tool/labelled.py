"""The lists an issue enumerates: which line of an issue is a labelled list.

An issue often lists the members of a set it specifies (flags, error codes,
options, fields) on one line: ``Flags: `-v`, `-q`, `-x` ``. Two readers use this
module's definition of such a line (a *labelled list*) and both apply it to the
same lines, the issue's units (``normative.units``: a sentence of a paragraph, a
list item or a table row, never a heading, a code block or a comment):

- the intake's unit rules (``normative``, rules 2) make the unit a required,
  citable unit, so a definition has to cover the whole set;
- the engine hands the lists to the verify gate (gate policy 2), the run-health
  fault check C5 reads them, and both ask whether a test tree exercises every
  item (``enumerated``, which holds the token rule).

**A labelled list** is one unit (a list marker, emphasis and a trailing period
aside) of a label, a colon and two or more distinct backticked items, with
nothing between the items but ``,``, ``;``, ``/``, ``|``, ``&``, ``and``, ``or``,
``etc.`` and an ellipsis. A unit with any other word after the label (``Literals:
numbers, `true` ``) is prose with code in it, not a list. A unit is not a list
when it only illustrates or records: its label names an example, a sample or
an illustration, or opens with ``e.g.``, ``Note``, ``See`` or ``Such as``, or any item is a field
set to a value (``mtime=0``, ``type = "x"``) or a mapping (``1 -> "1"``): those
are one record or one case, not a set whose every member must be tested.
"""

from __future__ import annotations

import re

from . import issuetext
from .enumerated import Listing

_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
# Between the items of a list: separators, ``and``/``or``/``etc.`` and an ellipsis.
_BETWEEN = re.compile(r"\b(?:and|or|etc)\b|\.\.\.|…|[\s,;/|&.\x00]+")
MIN_ITEMS = 2
_MAX_LABEL = 60
# A label that introduces an illustration or a pointer, not a set.
_ILLUSTRATIVE = re.compile(
    r"\b(?:examples?|samples?|illustrations?)\b|^(?:e\.?g\.?|notes?|see|such\s+as)(?!\w)",
    re.IGNORECASE,
)
# An item that is a field set to a value (``mtime=0``) or a mapping (``1 -> "1"``).
_RECORD_ITEM = re.compile(r"^\w[\w.\[\]\"']*\s*=(?!=)\s*\S|\S\s*(?:->|→)\s*\S")


def _is_label(head: str, colon: str, label: str) -> bool:
    """Whether the text before the colon names a list: a short word-bearing label, no code span."""
    return (
        bool(colon)
        and "`" not in head
        and 0 < len(label) <= _MAX_LABEL
        and any(char.isalpha() for char in label)
        and not _ILLUSTRATIVE.search(label)
    )


def labelled_list(line: str) -> Listing | None:
    """Return the labelled list ``line`` is, ``None`` when it is not one."""
    body = _MARKER.sub("", line, count=1).strip()
    head, colon, rest = body.partition(":")
    label = head.strip("*_ \t")
    if not _is_label(head, colon, label):
        return None
    items = tuple(dict.fromkeys(item for item in issuetext.code_spans(rest) if item))
    if len(items) < MIN_ITEMS or any(_RECORD_ITEM.search(item) for item in items):
        return None
    left = _BETWEEN.sub("", issuetext.blank_code(rest).strip("*_ \t"))
    return Listing(label, items) if not left else None
