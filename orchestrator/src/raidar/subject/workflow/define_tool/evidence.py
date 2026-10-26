"""The evidence check on an intake reply's ``features``.

Each named feature says where its behaviour is stated (``behaviour_from``) and
gives the ``evidence`` for it. The check is deterministic:

- ``issue``: ``evidence`` cites the issue's numbered units (or an earlier round's
  answers), and some cited unit's text must state the behaviour
  (``compact.feature_check``, which asks ``quote_problem`` of it). Both sides
  are compared in a normal form (``normal``): markdown code and emphasis
  markers, bullets, heading hashes and table pipes dropped, curly quotes and
  dashes made plain, sentence punctuation dropped, whitespace collapsed and case
  folded. A backslash before ASCII punctuation is an escape, as CommonMark
  reads it, and is dropped on both sides; a backslash before a letter or a
  digit is kept. What is left must hold at least ``MIN_WORDS`` words that are
  not a word of the feature's name and not filler (``FILLER``); markdown
  punctuation never counts. So a heading, the bare name, or a passing mention
  ("Pagination is supported.") fails, as does a list mention: a sentence naming
  the feature as one item beside other features of the same reply
  (``LIST_NAMES``, ``LIST_SHARE``).
- ``code`` or ``tests``: ``evidence`` is ``path:line`` (or ``path:first-last``)
  naming a file in the workspace, with the line inside the file.
- ``none``: nothing is checked; the feature must be asked about.

A feature whose evidence fails counts as ``none``: an ``objective_unclear``
question must name it, or the reply goes to repair.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from .core import JsonObject, text
from .sections import Fences

OK = "ok"
NONE = "none"
MIN_WORDS = 3
# Words that say nothing about behaviour, left out of the word count.
_FILLER_WORDS = """
a an the this that these those it its is are be been was were will and or but
of to in on at by for with as also there here has have support supports
supported feature features section
"""
FILLER = frozenset(_FILLER_WORDS.split())
_WORD = re.compile(r"\w+")
# A quote is compared with markdown, quote-mark, dash, sentence-punctuation,
# spacing and case differences set aside, on both sides.
_SPELLING = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u2032": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u2033": '"',
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u00a0": " ",
    }
)
# Quotation marks, dropped on both sides by ``verbatim_problem``: a quote of a
# sentence that itself quotes may change or drop the inner marks.
_UNQUOTED = str.maketrans("", "", "'\"")
# A CommonMark backslash escape: a backslash and the ASCII punctuation it escapes.
_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
# Backslashes before a quotation mark, however many a quote's author wrote to
# escape it or to escape the escape (``\"``, ``\\\"``): the mark is what counts.
_ESCAPED_QUOTE = re.compile(r"\\+(?=[\"'])")
# A code span.
_SPAN = re.compile(r"(`[^`]*`)")
_HEADING_MARK = re.compile(r"^ {0,3}#{1,6}(?:\s+|$)")
# A bullet, wherever a quote that runs list items together may put it; a
# number marker keeps its number (``1. Add`` and ``1 Add`` compare equal).
_BULLET = re.compile(r"(?:^|(?<=\s))[-*+]\s+")
# ``*x*``, ``**x**`` or ``_x_`` as emphasis, not an ``_`` inside a name.
_EMPHASIS = re.compile(r"(?<!\w)(\*{1,3}|_{1,3})(?=\S)(.+?)(?<=\S)\1(?!\w)")
_PUNCTUATION = re.compile(r"[.,;:!?]+(?=\s|$)")
# A quote that names the feature as one item beside at least LIST_NAMES other
# features of the same reply, with those other names making up at least
# LIST_SHARE of its words that are not its own name or filler, is a list
# mention, not evidence:
# "support foreign key relationships, custom indexes, and advanced constraints"
# says nothing of what advanced constraints do. A sentence that states a
# behaviour with other features as its operands is not caught.
LIST_NAMES = 2
LIST_SHARE = 0.5
LIST_PROBLEM = (
    "the `from:` evidence is a list mention, naming other features too ({}): quote the "
    "issue's sentence that says what this feature does"
)
_REFERENCE = re.compile(r"(?P<path>[^\s:]+):(?P<first>\d+)(?:-(?P<last>\d+))?")
# An earlier round's reply is quoted in the prior text as `  > line`.
_QUOTED = re.compile(r"^\s*>\s?", re.MULTILINE)


def source_text(issue: str, prior: str = "") -> str:
    """Return the text an ``issue`` quote may come from: issue and earlier answers."""
    return f"{issue}\n{_QUOTED.sub('', prior)}"


def _normal_line(line: str, unquoted: bool = False) -> str:
    """One line without its markdown, spelling variants and spacing.

    ``unquoted`` also drops every quotation mark (``verbatim_problem``). Backslash
    escapes of punctuation are undone first, on every side that is compared.
    """
    line = _ESCAPE.sub(r"\1", _ESCAPED_QUOTE.sub("", line))
    line = _BULLET.sub("", _HEADING_MARK.sub("", line.translate(_SPELLING)))
    line = _EMPHASIS.sub(r"\2", line.replace("`", "").replace("|", " "))
    if unquoted:
        line = line.translate(_UNQUOTED)
    return " ".join(_PUNCTUATION.sub("", line).split()).casefold()


def normal(value: str, unquoted: bool = False) -> str:
    """``value`` as quotes are compared: markdown, quote marks and spacing aside."""
    lines = (_normal_line(line, unquoted) for line in value.splitlines())
    return " ".join(filter(None, lines))


def _source(source: str, unquoted: bool = False) -> tuple[str, list[tuple[int, int]]]:
    """Return the normal source and the span each heading line takes in it.

    A ``#`` line inside a fenced code block is a code comment, not a heading.
    """
    parts: list[str] = []
    headings: list[tuple[int, int]] = []
    at = 0
    fences = Fences()
    for line in source.splitlines():
        code = fences.delimits(line) or fences.inside
        flat = _normal_line(line, unquoted)
        if not flat:
            continue
        if not code and _HEADING_MARK.match(line.translate(_SPELLING)):
            headings.append((at, at + len(flat)))
        parts.append(flat)
        at += len(flat) + 1
    return " ".join(parts), headings


def _words(value: str) -> list[str]:
    return _WORD.findall(value.casefold())


def _whole(said: str, flat: str, start: int) -> bool:
    """Whether ``said`` at ``start`` in ``flat`` starts and ends on word boundaries."""
    end = start + len(said)
    before = start > 0 and _WORD.match(flat[start - 1]) and _WORD.match(said[0])
    after = end < len(flat) and _WORD.match(flat[end]) and _WORD.match(said[-1])
    return not (before or after)


def _opened(headings: Sequence[tuple[int, int]], start: int, end: int) -> int | None:
    """How much of the span ``start``..``end`` is the heading line it opens with (``0``: none).

    ``None`` when a heading starts inside the span: it runs past one.
    """
    if any(start < h[0] < end for h in headings):
        return None
    opening = [h for h in headings if h[0] <= start < h[1]]
    return max(0, opening[0][1] - start) if opening else 0


def _occurrence(
    said: str, source: str, verbatim: bool = False, whole: bool = False
) -> tuple[bool, int | None]:
    """Whether ``said`` is in ``source`` and, if it can be, the heading it opens with.

    Returns ``(found, cut)``: ``cut`` is how much of ``said`` is the heading
    line it opens with (``0`` when none) for the first occurrence that stays in
    one section, or ``None`` when every occurrence runs past a heading.
    ``verbatim`` (``verbatim_problem``): quotation marks are dropped from the
    source, as from ``said``. ``whole`` (also set by ``verbatim``): an
    occurrence counts only when it starts and ends on word boundaries, so part
    of a word is never the issue's words.
    """
    flat, headings = _source(source, verbatim)
    found = False
    start = flat.find(said)
    while start >= 0:
        if (verbatim or whole) and not _whole(said, flat, start):
            start = flat.find(said, start + 1)
            continue
        found = True
        cut = _opened(headings, start, start + len(said))
        if cut is not None:
            return True, cut
        start = flat.find(said, start + 1)
    return found, None


def _named(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")


def listing(name: str, said: str, names: Sequence[str]) -> list[str]:
    """Return the other features ``said`` (a normal quote) lists beside ``name``.

    Empty unless ``said`` names the feature itself too. A name is matched
    whole, longest first, so one that only occurs inside a longer feature name
    (``id`` in ``_row_id``) is not counted again.
    """
    own = normal(name)
    if not own or not _named(own).search(said):
        return []
    rest = _named(own).sub(" ", said)
    found: list[str] = []
    for other in sorted({normal(n) for n in names} - {own, ""}, key=len, reverse=True):
        pattern = _named(other)
        if pattern.search(rest):
            found.append(other)
            rest = pattern.sub(" ", rest)
    return found


def _list_share(said: str, items: Sequence[str]) -> float:
    """Return the share of ``said``'s non-filler words that are the names ``items``."""
    words = [w for w in _words(said) if w not in FILLER]
    rest = said
    for item in sorted(items, key=len, reverse=True):
        rest = _named(item).sub(" ", rest)
    left = [w for w in _words(rest) if w not in FILLER]
    return 1 - len(left) / len(words) if words else 0.0


def _body_problem(name: str, body: str, names: Sequence[str]) -> str:
    """Why ``body`` (a quote, its heading cut off) does not state ``name``'s behaviour."""
    named = set(_words(normal(name)))
    content = [w for w in _words(body) if w not in named and w not in FILLER]
    if len(content) < MIN_WORDS:
        return (
            "the `from:` evidence is only a heading, the name or a passing mention: quote "
            "the issue's sentence that says what the feature does"
        )
    others = listing(name, body, names)
    if len(others) >= LIST_NAMES and (
        _list_share(_named(normal(name)).sub(" ", body), others) >= LIST_SHARE
    ):
        return LIST_PROBLEM.format(", ".join(f"`{o}`" for o in others))
    return ""


def quote_problem(name: str, quote: str, source: str, names: Sequence[str] = ()) -> str:
    """Why ``quote`` does not state ``name``'s behaviour from ``source`` (``""``: it does).

    ``names`` are the names of every feature in the same reply.
    """
    said = normal(quote)
    if not said:
        return "the `from:` evidence is empty"
    found, cut = _occurrence(said, source, whole=True)
    if not found:
        return "the `from:` evidence is not a verbatim quote from the issue"
    if cut is None:
        return (
            "the `from:` evidence runs past a heading: quote one section, the one that "
            "says what the feature does"
        )
    return _body_problem(name, said[cut:], names)


def _case_changed(quote: str, source: str) -> bool:
    """Whether ``quote`` writes a word the issue sets in a code span in another case.

    Case is set aside when quotes are compared, but a code span's case is
    part of what it says: ``json`` is not ``JSON``. A word of the quote is
    changed when the issue has it only in other cases and at least one of
    them is in a code span; plain prose may change case (a sentence start).
    """
    cases: dict[str, set[str]] = {}
    spanned: set[str] = set()
    for at, part in enumerate(_SPAN.split(source)):
        for word in _WORD.findall(part):
            cases.setdefault(word.casefold(), set()).add(word)
            if at % 2:
                spanned.add(word.casefold())
    return any(
        word.casefold() in spanned and word not in cases[word.casefold()]
        for word in _WORD.findall(quote)
    )


def verbatim_problem(quote: str, source: str, min_words: int = 1) -> str:
    """Why ``quote`` is not the issue's own words from one section (``""``: it is).

    The same normal form and one-section rule as a feature's ``issue``
    evidence (``quote_problem``), without its list rule, and with quotation
    marks set aside on both sides (an inner quote may change its marks). The
    quote must match whole words: it starts and ends on a word boundary of the
    issue text. Past any heading it opens with, it holds at least ``min_words``
    words that are not filler (``FILLER``). An acceptance criterion's
    ``Source:`` or a decision's issue quote may be a
    short clause, but never a paraphrase, a stitched quote, a fragment of a
    word or a bare heading.
    """
    said = normal(quote, unquoted=True)
    if not said:
        return "is empty"
    found, cut = _occurrence(said, source, verbatim=True)
    if not found or _case_changed(quote, source):
        return "is not a verbatim quote from the issue (whole words, in order)"
    if cut is None:
        return "runs past a heading: quote one section"
    body = said[cut:]
    if not body.strip():
        return "is only a heading"
    if len([w for w in _words(body) if w not in FILLER]) < min_words:
        return (
            f"is too short to state anything: quote at least {min_words} words "
            "that say what the issue asks"
        )
    return ""


def reference_problem(reference: str, workdir: Path | None) -> str:
    """Why ``reference`` is not a ``path:line`` in ``workdir`` (``""``: it is)."""
    match = _REFERENCE.fullmatch(reference.strip())
    if match is None:
        return "the `from:` evidence is not `path:line`"
    if workdir is None:
        return ""
    root = workdir.resolve()
    path = (root / match["path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return f"`{match['path']}` is not a file in the workspace"
    try:
        length = len(path.read_bytes().splitlines())
    except OSError:
        return f"`{match['path']}` cannot be read"
    last = int(match["last"] or match["first"])
    if not 1 <= int(match["first"]) <= last <= length:
        return f"`{reference.strip()}` is past the end of `{match['path']}`"
    return ""


def check(feature: JsonObject, workdir: Path | None) -> str:
    """``ok``, ``none`` (nothing claimed), or why a ``code`` or ``tests`` feature's evidence fails.

    An ``issue`` feature's evidence is unit ids, checked where the units are known
    (``compact.feature_check``), which asks ``quote_problem`` of each unit's text.
    """
    claimed = text(feature, "behaviour_from")
    if claimed in ("code", "tests"):
        return reference_problem(text(feature, "evidence"), workdir) or OK
    return NONE
