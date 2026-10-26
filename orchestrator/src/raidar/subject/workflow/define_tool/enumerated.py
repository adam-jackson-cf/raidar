"""Which items of a listed set a test tree exercises: the token rule.

An issue often lists the members of a set it specifies (flags, error codes,
options, fields) on one line: ``Flags: `-v`, `-q`, `-x` `` (a *labelled list*,
read from an issue by ``labelled.labelled_list``). Three readers apply this
module's rule to the lists of one issue:

- the run-health fault checks C5 and C7 (``evaluation.health.faults``) ask
  whether the tests of a finished checkpoint exercise every item of every list its
  issue enumerates (``normative.enumerations``), C5 by rule 1 and C7 by rule 2;
- the verify gate's rule ``LIST.item-untested`` (gate policy 2) refuses a commit
  whose test tree leaves a listed item unexercised. The gate pack ships a
  byte-identical copy of this file (``packs/python/guardrails/enumerated.py``,
  held equal by a digest test), so it imports nothing but the standard library
  and the two cannot drift apart: a change to the rule is a new rule version, a
  new check version and (once measured) a new gate policy;
- the replay of that rule over recorded trees (``replay rules``).

**A list's items** are distinct, in order. **Exercised.** A test tree exercises
an item when the item occurs as a token in what the tests *say to the program*,
never as a word of their prose. The text a test file says is every string
literal of its code (an f-string's literal parts included, docstrings and
comments excluded) and, for an item that is a name or a number, its
identifiers, attribute and argument names and numeric literals. The token rule,
by the kind of item:

- a *word* item (only letters, digits and ``_``: ``and``, ``BAD_FLAG``, ``404``)
  is a token when it stands between non-word characters, so ``or`` is not in
  ``color``; it also counts as a name or number of the test's code (a Python
  keyword or builtin is the language's, never the program's: ``not`` in
  ``assert not x`` and ``int`` in ``isinstance(x, int)`` say nothing; a number
  that only initialises a variable, ``x = 0``, says nothing either);
- a *symbol* item (no letter, digit or ``_``: ``!``, ``*``, ``-``, ``&&``) counts
  in string literals only: the operators of a test's own Python say nothing
  about the program's. An occurrence inside a longer symbol item of the same
  list does not count (``<`` in ``<=`` when ``<=`` is listed), nor does one next
  to the same character on either side (``*`` in ``**``, ``-`` in ``--``: a
  different token), nor does a ``-`` or ``/`` inside a date, a path or a hyphenated
  word (``2024-01-01``, ``a/b``, ``read-only``: a token of letters, digits, ``_``
  and ``.`` joined by it). Rule 2 also reads where a symbol stands, in two clauses
  that name no symbol (they read the shape of a test's strings, not the program's
  grammar). A symbol **glued behind a word with no operand after it** (``true!``
  and then the end of the line, a closing quote or group character, ``,`` or
  ``;``) is the punctuation of a data string, not an operator, and does not
  count. The clause cannot tell that punctuation from a postfix operator (``5!``,
  ``i++``, ``50%``) without naming symbols, so an item used only so, with nothing
  after it, is refused; ``5! + 1`` counts. A symbol the tests use **only in prefix form** (every
  standing use has
  nothing to its left, or an operator character, ``,``, ``:``, ``;`` or an
  opening group character before it: ``a && !x``) is a prefix operator, and
  counts only when some line or quoted value of the tests' strings also
  **begins** with it, after whitespace (``!x``, ``'!x'``, ``{"k": "!x"}``): an
  item exercised only after another operator is not exercised at the start of an
  expression. A symbol with any use between two operands (``a + b``) is a binary
  operator and is unaffected, as is a word item (``not``), whose prefix use cannot
  be told from its infix use without naming words;
- a *pair* item (``()``, ``[]``, ``{}``) counts when the pair, or its opening and
  closing character each, occur in a string literal: a grouped expression is
  ``(a + b)``, never ``()``;
- any other item (``--top``, ``a.b``) is a token when its first and last
  characters, where they are word characters, have no word character beside
  them.

The message of an ``assert`` is prose, not something said to the program, and is
left out. Case matters. A test file that does not parse is left out.

**Versions.** The rule is versioned (``LATEST``). Rule 1 is the one the run-health
check C5 was measured with, and ``version=1`` reads neither position clause, so a
recorded capture stays reproducible. Rule 2 is C7's, gate policy 2's
``LIST.item-untested`` and the intake's list-item example obligation.
"""

from __future__ import annotations

import ast
import builtins
import keyword
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass

_WORD = re.compile(r"\w")
_PAIRS = {"()": ("(", ")"), "[]": ("[", "]"), "{}": ("{", "}")}
# A date, a path or a hyphenated word: not an operator between two operands.
_INCIDENTAL = re.compile(r"^[\w.]+(?:[-/][\w.]+)+$")
_INCIDENTAL_SYMBOLS = frozenset("-/")
# The token rule's versions: 1 is what C5 was measured with, 2 adds the position clauses.
LATEST = 2
_QUOTES = frozenset("\"'`")
_BLANKS = " \t"
_OPENING = frozenset("([{")
_CLOSING = frozenset(")]}")
# What may follow a symbol that has no operand (besides the end of the line and a closing quote).
_ENDING = _CLOSING | frozenset(",;")
# What the language, not the program under test, names.
_LANGUAGE = frozenset(dir(builtins)) | frozenset(keyword.kwlist) | frozenset(keyword.softkwlist)


@dataclass(frozen=True)
class Listing:
    """A labelled list: its label and its distinct items, in order."""

    label: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class Exercised:
    """What a test tree says: its string literals joined, and the names and numbers of its code."""

    strings: str
    names: frozenset[str]


def _docstrings(tree: ast.AST) -> set[int]:
    """Return the ids of the string constants that are docstrings."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(id(first.value))
    return found


# The field of each kind of node that holds the name it binds or uses.
_NAME_FIELDS: dict[type[ast.AST], str] = {
    ast.Name: "id",
    ast.Attribute: "attr",
    ast.arg: "arg",
    ast.keyword: "arg",
}


def _identifier(node: ast.AST) -> str | None:
    """Return the name a node binds or uses, ``None`` when it is none or the language's own."""
    field = _NAME_FIELDS.get(type(node))
    name = getattr(node, field, None) if field else None
    if not isinstance(name, str) or (isinstance(node, ast.Name) and name in _LANGUAGE):
        return None
    return name


def _unsaid(tree: ast.AST) -> set[int]:
    """Return the ids of the constants that say nothing to the program.

    The strings of an ``assert`` message and a number that only initialises a variable.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert) and node.msg is not None:
            found.update(id(part) for part in ast.walk(node.msg))
        elif (
            isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int | float)
        ):
            found.add(id(node.value))
    return found


def _tokens(tree: ast.AST) -> tuple[list[str], set[str]]:
    """Return a tree's string literals (docstrings aside) and its names and numbers."""
    docs = _docstrings(tree) | _unsaid(tree)
    strings: list[str] = []
    names: set[str] = set()
    for node in ast.walk(tree):
        if (name := _identifier(node)) is not None:
            names.add(name)
        if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
            continue
        if id(node) in docs:
            continue
        if isinstance(node.value, str):
            strings.append(node.value)
        elif isinstance(node.value, int | float):
            names.add(repr(node.value))
    return strings, names


def exercised(sources: Mapping[str, str]) -> Exercised:
    """Return what the test files ``sources`` (path to text) say; one that does not parse is left
    out."""
    strings: list[str] = []
    names: set[str] = set()
    for text in sources.values():
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        found, used = _tokens(tree)
        strings += found
        names |= used
    return Exercised("\n".join(strings), frozenset(names))


def _occurrences(text: str, token: str) -> Iterator[tuple[int, int]]:
    """Yield the span of every occurrence of ``token`` in ``text``."""
    start = text.find(token)
    while start >= 0:
        yield start, start + len(token)
        start = text.find(token, start + 1)


def _is_word(char: str) -> bool:
    return bool(_WORD.match(char))


def _bounded(item: str, text: str) -> bool:
    """Whether ``item`` occurs in ``text`` with no word character beside a word edge."""
    for start, end in _occurrences(text, item):
        before = text[start - 1 : start] if start else ""
        after = text[end : end + 1]
        if (_is_word(item[0]) and before and _is_word(before)) or (
            _is_word(item[-1]) and after and _is_word(after)
        ):
            continue
        return True
    return False


def _incidental(item: str, text: str, start: int, end: int) -> bool:
    """Whether the ``-`` or ``/`` at ``start:end`` joins the parts of a date, a path or a word."""
    if item not in _INCIDENTAL_SYMBOLS:
        return False
    first = max(text.rfind(space, 0, start) for space in " \t\n") + 1
    last = min(
        (text.find(space, end) for space in " \t\n" if space in text[end:]),
        default=len(text),
    )
    return bool(_INCIDENTAL.match(text[first:last]))


def _opens(text: str, index: int) -> bool:
    """Whether the quote at ``index`` opens a quoted span: an even number of its kind came before
    on the line."""
    line = text.rfind("\n", 0, index) + 1
    return text.count(text[index], line, index) % 2 == 0


def _left(text: str, start: int) -> str:
    """Return the character left of ``start`` past blanks; ``""`` where a line or a quoted value
    begins."""
    index = start
    while index > 0:
        char = text[index - 1]
        if char == "\n":
            return ""
        if char in _QUOTES and _opens(text, index - 1):
            return ""
        if char not in _BLANKS:
            return char
        index -= 1
    return ""


def _operand_follows(text: str, end: int) -> bool:
    """Whether anything but the end of the line, a separator, a closing group or a closing quote
    follows ``end``."""
    index = end
    while index < len(text) and text[index] in _BLANKS:
        index += 1
    char = text[index : index + 1]
    if not char or char == "\n" or char in _ENDING:
        return False
    return not (char in _QUOTES and not _opens(text, index))


def _prefix_form(text: str, start: int) -> bool:
    """Whether a symbol at ``start`` has no operand to its left: nothing, an operator, a separator
    or an opening group."""
    char = _left(text, start)
    return (
        not char or char in _OPENING or not (_is_word(char) or char in _CLOSING or char in _QUOTES)
    )


def _punctuation(text: str, start: int, end: int) -> bool:
    """Whether a symbol is glued behind a word and nothing follows it to operate on: punctuation of
    a data string."""
    before = text[start - 1 : start]
    return bool(before) and _is_word(before) and not _operand_follows(text, end)


def _alone(
    item: str, text: str, span: tuple[int, int], covered: Iterable[tuple[int, int]], version: int
) -> bool:
    """Whether the occurrence of ``item`` at ``span`` stands alone: no other symbol holds it."""
    start, end = span
    before = text[start - 1 : start] if start else ""
    return not (
        any(first <= start and end <= last for first, last in covered)
        or before == item[0]
        or text[end : end + 1] == item[-1]
        or _incidental(item, text, start, end)
        or (version >= 2 and _punctuation(text, start, end))
    )


def _standing(
    item: str, text: str, listed: Iterable[str], version: int
) -> Iterator[tuple[int, int]]:
    """Yield the span of each occurrence of a symbol ``item`` that stands alone in ``text``."""
    covered = [
        span
        for other in listed
        if len(other) > len(item) and item in other
        for span in _occurrences(text, other)
    ]
    for span in _occurrences(text, item):
        if _alone(item, text, span, covered, version):
            yield span


def _symbol_said(item: str, text: str, listed: Iterable[str], version: int) -> bool:
    """Whether a symbol ``item`` stands alone in ``text``, and at its start when only used in
    prefix form (see the module docstring)."""
    spans = [start for start, _ in _standing(item, text, listed, version)]
    if version < 2 or not spans:
        return bool(spans)
    prefix_only = all(_prefix_form(text, start) for start in spans)
    return not prefix_only or any(not _left(text, start) for start in spans)


def _symbolic(item: str) -> bool:
    return not any(_is_word(char) for char in item)


def item_exercised(
    item: str, said: Exercised, listed: Iterable[str], version: int = LATEST
) -> bool:
    """Whether the tests say ``item`` by rule ``version`` (see the module docstring); ``listed``
    are its list's items."""
    if item in _PAIRS:
        return item in said.strings or all(c in said.strings for c in _PAIRS[item])
    if _symbolic(item):
        return _symbol_said(item, said.strings, listed, version)
    return _bounded(item, said.strings) or item in said.names


def untested(listing: Listing, said: Exercised, version: int = LATEST) -> tuple[str, ...]:
    """Return the items of ``listing`` the tests do not exercise by rule ``version``, in order."""
    return tuple(
        item for item in listing.items if not item_exercised(item, said, listing.items, version)
    )
