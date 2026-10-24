"""Rule families of the blocking findings a commit gate reports.

A finding's code is mapped to one family; the map is fixed here (it is part of
``gate-effort``'s identity) and names Ruff, mypy and the python-guardrails
bespoke codes. Codes come in two shapes:

- ``<LETTERS><DIGITS>`` (Ruff and the bespoke ``PYQ``/``PYS``/``TST`` rules):
  matched on the exact letter prefix, then the longest digit prefix, so
  ``C901`` is complexity while ``C4xx`` is style, ``E9xx`` (syntax errors) is a
  defect while other ``E`` codes are style, and ``PLR0904`` (too many public
  methods) is structure while the other ``PLR09xx`` limits are complexity;
- dotted or bare words (``MYPY.arg-type``, ``FANOUT.general``, ``FORMAT``,
  ``SUPPRESS``): matched on the part before the first dot, except that every
  ``TEST.*`` code (the python-guardrails tests check) is in the tests family.

A code the map does not name is ``other``, never dropped.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

COMPLEXITY = "complexity"
TYPING = "typing"
DEFECTS = "defects"
STRUCTURE = "structure"
STYLE = "style"
NAMING = "naming"
TESTS = "tests"
SUPPRESSIONS = "suppressions"
OTHER = "other"
TEST_CODE_PREFIX = "TEST."
FAMILIES = (COMPLEXITY, TYPING, DEFECTS, STRUCTURE, STYLE, NAMING, TESTS, SUPPRESSIONS, OTHER)

# (letters, digit prefix) -> family; the longest digit prefix of the same letters wins.
_NUMBERED: dict[tuple[str, str], str] = {
    ("C", "90"): COMPLEXITY,
    ("PLR", "09"): COMPLEXITY,
    ("PYS", "25"): COMPLEXITY,
    ("F", ""): DEFECTS,
    ("B", ""): DEFECTS,
    ("E", "9"): DEFECTS,
    ("PLE", ""): DEFECTS,
    ("PYS", "0"): DEFECTS,
    ("PYQ", "299"): DEFECTS,
    ("PLR", "0904"): STRUCTURE,
    ("PYQ", "10"): STRUCTURE,
    ("PYQ", "2"): STRUCTURE,
    ("E", ""): STYLE,
    ("W", ""): STYLE,
    ("I", ""): STYLE,
    ("UP", ""): STYLE,
    ("C", "4"): STYLE,
    ("SIM", ""): STYLE,
    ("D", ""): STYLE,
    ("A", ""): NAMING,
    ("N", ""): NAMING,
    ("PYQ", "103"): NAMING,
    ("PYQ", "104"): NAMING,
    ("PYQ", "105"): NAMING,
    ("PYQ", "107"): NAMING,
    ("PT", ""): TESTS,
    ("TST", ""): TESTS,
    ("PYQ", "102"): TESTS,
    ("PGH", ""): SUPPRESSIONS,
    ("RUF", "100"): SUPPRESSIONS,
}
_WORDS: dict[str, str] = {
    "MYPY": TYPING,
    "FANOUT": STRUCTURE,
    "PARITY": STRUCTURE,
    "FORMAT": STYLE,
    "SUPPRESS": SUPPRESSIONS,
}
_NUMBERED_RE = re.compile(r"^(?P<letters>[A-Z]+)(?P<digits>\d+)$")


def family_of(code: str) -> str:
    """The family of one finding code (``other`` when the map does not name it)."""
    if code.startswith(TEST_CODE_PREFIX):
        return TESTS
    match = _NUMBERED_RE.match(code)
    if match is None:
        return _WORDS.get(code.split(".", 1)[0], OTHER)
    letters, digits = match["letters"], match["digits"]
    prefixes = [
        prefix for (head, prefix) in _NUMBERED if head == letters and digits.startswith(prefix)
    ]
    if not prefixes:
        return OTHER
    return _NUMBERED[(letters, max(prefixes, key=len))]


def family_counts(codes: Iterable[str]) -> dict[str, int]:
    """Findings per family, every family present (zero when none of its codes occurred)."""
    counted = Counter(family_of(code) for code in codes)
    return {family: counted.get(family, 0) for family in FAMILIES}
