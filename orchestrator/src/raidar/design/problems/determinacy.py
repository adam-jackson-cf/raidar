"""Spec-determinacy audit, static half: which asserted values a checkpoint's spec determines.

A hidden test may assert things a conforming program cannot know from the
specification: an error detail's exact wording, that the detail contains an
input value, that it *equals* an identifier, or the output's byte format. This
module finds those assertions in a problem's test files (Python ``ast``, no
execution) and classifies each against the specification text of its origin
checkpoint and every earlier one. The dynamic half
(``raidar.design.problems.variants``) grades spec-conformant variants of the
reference solutions; ``raidar.design.problems.audit`` combines the two.

Audited assertion forms (``AssertionKind``):

- ``error_text_equals`` / ``error_text_contains``: a keyword ``detail``,
  ``detail_contains``, ``message`` or ``message_contains`` of a call to an
  ``assert*`` helper (``assert_error(..., detail_contains="x")``); the last
  positional argument of a helper whose name contains ``detail`` or ``message``
  (``assert_detail_contains(payload, "x")``); or a bare ``assert`` comparing a
  value read from an error's ``detail``/``message`` (``x in text``,
  ``text == x``);
- ``error_text_pattern``: ``re.search``/``re.match``/``re.fullmatch`` of a
  pattern against such a value;
- ``output_bytes``: a call to a test helper that compares ``<result>.stdout``
  byte for byte (``==`` or ``.endswith``), or such a comparison in the test.

Rules (``Determination.rule``), applied in this order:

- ``literal-in-spec``: the expected value is a string literal that occurs in
  the specification text (case-, whitespace- and markup-insensitive);
- ``format-stated`` / ``format-unstated``: an ``output_bytes`` assertion, and
  whether the specification names an output byte format (``FORMAT_TERMS``);
- ``wording-unstated``: an error-text literal that occurs neither in the
  specification nor among the test's own inputs, i.e. a wording the program
  would have to guess;
- ``exact-stated`` / ``exact-unstated``: an equality on an input-derived
  value, and whether a paragraph of the specification states the exact
  content of that error category's detail (``EXACT_PHRASES``);
- ``content-stated`` / ``content-unstated``: a containment or pattern on an
  input-derived value, and whether a paragraph states what that error
  category's detail contains (``CONTENT_VERBS``).

A paragraph speaks for an error category when it mentions ``detail`` or
``message`` and the category (``not_found``, ``CycleError``), either in its own
text or in its heading ("Not Found Detail"). The category of an assertion is
the ``category``/``error_type`` keyword of the nearest ``assert*`` call at or
before it in the same test. The rules are deliberately syntactic: a reviewer
can recompute every verdict from the report's ``assertion`` and ``why``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

AUDIT_VERSION = 1
TEXT_KEYWORDS = {
    "detail": "error_text_equals",
    "detail_contains": "error_text_contains",
    "message": "error_text_equals",
    "message_contains": "error_text_contains",
}
CATEGORY_KEYWORDS = ("category", "error_type")
ERROR_TEXT_KEYS = frozenset({"detail", "message"})
PATTERN_FUNCTIONS = frozenset({"search", "match", "fullmatch"})
CONTENT_VERBS = (
    "include",
    "identif",
    "list",
    "name",
    "contain",
    "describ",
    "report",
    "mention",
)
EXACT_PHRASES = ("exactly", "equals", "is the bare", "consists only of", "nothing but")
FORMAT_TERMS = (
    "compact",
    "sorted key",
    "sort_keys",
    "key order",
    "keys sorted",
    "whitespace between",
    "no whitespace",
    "trailing newline",
    "no newline",
    "indent",
    "separators",
)
_MARKUP = re.compile(r"[`*_]")
_SPACE = re.compile(r"\s+")
_HEADING = re.compile(r"^#{1,6}\s+(.*)$")


class AssertionKind(StrEnum):
    ERROR_TEXT_EQUALS = "error_text_equals"
    ERROR_TEXT_CONTAINS = "error_text_contains"
    ERROR_TEXT_PATTERN = "error_text_pattern"
    OUTPUT_BYTES = "output_bytes"

    @property
    def is_error_text(self) -> bool:
        return self is not AssertionKind.OUTPUT_BYTES


@dataclass(frozen=True)
class AuditedAssertion:
    """One audited assertion in one test function."""

    test_file: str
    function: str
    line: int
    kind: AssertionKind
    source: str
    expected: str
    literal: str | None
    input_derived: bool
    category: str | None

    @property
    def function_id(self) -> str:
        return f"{self.test_file}::{self.function}"


@dataclass(frozen=True)
class Determination:
    determined: bool
    rule: str
    why: str


# ---- specification text -------------------------------------------------------


def normalise(text: str) -> str:
    """Lower case, markup (backticks, emphasis, underscores) and runs of whitespace folded."""
    return _SPACE.sub(" ", _MARKUP.sub(" ", text.lower())).strip()


@dataclass(frozen=True)
class SpecParagraph:
    heading: str
    text: str


@dataclass(frozen=True)
class SpecText:
    """The specification a checkpoint's cases are judged against (it and every earlier step)."""

    paragraphs: tuple[SpecParagraph, ...]

    @classmethod
    def from_markdown(cls, texts: Iterable[str]) -> SpecText:
        paragraphs: list[SpecParagraph] = []
        for text in texts:
            paragraphs.extend(_paragraphs(text))
        return cls(tuple(paragraphs))

    @property
    def normalised(self) -> str:
        return " ".join(normalise(f"{p.heading} {p.text}") for p in self.paragraphs)

    def contains(self, literal: str) -> bool:
        needle = normalise(literal)
        return bool(needle) and needle in self.normalised

    def states(self, category: str | None, phrases: Sequence[str]) -> SpecParagraph | None:
        """The first paragraph stating the category's detail/message with one of ``phrases``."""
        if category is None:
            return None
        for paragraph in self.paragraphs:
            body = normalise(paragraph.text)
            mentions_text = "detail" in body or "message" in body
            if mentions_text and _speaks_for(paragraph, category) and _any_in(body, phrases):
                return paragraph
        return None

    def names_format(self) -> str | None:
        text = self.normalised
        return next((term for term in FORMAT_TERMS if normalise(term) in text), None)


def _paragraphs(text: str) -> Iterator[SpecParagraph]:
    heading = ""
    block: list[str] = []
    for line in [*text.splitlines(), ""]:
        match = _HEADING.match(line.strip())
        if match or not line.strip():
            if block:
                yield SpecParagraph(heading, " ".join(block))
                block = []
            if match:
                heading = match.group(1)
            continue
        block.append(line.strip())


def _speaks_for(paragraph: SpecParagraph, category: str) -> bool:
    wanted = normalise(category)
    return wanted in normalise(paragraph.text) or wanted in normalise(paragraph.heading)


def _any_in(text: str, phrases: Sequence[str]) -> bool:
    return any(phrase in text for phrase in phrases)


# ---- test assertions --------------------------------------------------------------


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _strings(node: ast.AST) -> Iterator[str]:
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            yield child.value


def _mentions_stdout_bytes(node: ast.AST) -> bool:
    """``<x>.stdout == ...`` (not against ``""``) or ``<x>.stdout.endswith(...)``."""
    for child in ast.walk(node):
        if isinstance(child, ast.Compare) and _is_stdout_equality(child):
            return True
        if (
            isinstance(child, ast.Call)
            and _call_name(child) == "endswith"
            and isinstance(child.func, ast.Attribute)
            and _is_stdout(child.func.value)
        ):
            return True
    return False


def _is_stdout(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "stdout"


def _is_stdout_equality(node: ast.Compare) -> bool:
    if not any(isinstance(op, ast.Eq | ast.NotEq) for op in node.ops):
        return False
    sides = [node.left, *node.comparators]
    empty = any(isinstance(side, ast.Constant) and side.value == "" for side in sides)
    return any(_is_stdout(side) for side in sides) and not empty


@dataclass(frozen=True)
class HelperIndex:
    """What the problem's non-test helper modules do, by function name."""

    format_helpers: frozenset[str]
    text_helpers: dict[str, AssertionKind]

    @classmethod
    def scan(cls, sources: Iterable[str]) -> HelperIndex:
        formats: set[str] = set()
        texts: dict[str, AssertionKind] = {}
        for source in sources:
            for node in ast.parse(source).body:
                if not isinstance(node, ast.FunctionDef):
                    continue
                if _mentions_stdout_bytes(node):
                    formats.add(node.name)
                lowered = node.name.lower()
                if lowered.startswith("assert") and ("detail" in lowered or "message" in lowered):
                    texts[node.name] = (
                        AssertionKind.ERROR_TEXT_CONTAINS
                        if "contain" in lowered
                        else AssertionKind.ERROR_TEXT_EQUALS
                    )
        return cls(frozenset(formats), texts)


@dataclass
class _FunctionScan:
    test_file: str
    function: ast.FunctionDef
    helpers: HelperIndex

    def __post_init__(self) -> None:
        self.text_names: set[str] = set()
        self.categories = self._categories()
        self.inputs = self._input_strings()
        self.text_names = self._error_text_names()

    def assertions(self) -> list[AuditedAssertion]:
        found: list[AuditedAssertion] = []
        for node in ast.walk(self.function):
            if isinstance(node, ast.Call):
                found.extend(self._call(node))
            elif isinstance(node, ast.Assert):
                found.extend(self._bare_assert(node))
        return sorted(found, key=lambda item: (item.line, item.kind.value, item.expected))

    def _categories(self) -> list[tuple[int, str]]:
        found = [
            (node.lineno, str(keyword.value.value))
            for node in ast.walk(self.function)
            if isinstance(node, ast.Call) and _call_name(node).startswith("assert")
            for keyword in node.keywords
            if keyword.arg in CATEGORY_KEYWORDS and isinstance(keyword.value, ast.Constant)
        ]
        return sorted(found)

    def _category_at(self, line: int) -> str | None:
        before = [category for at, category in self.categories if at <= line]
        return before[-1] if before else None

    def _asserted_ids(self) -> set[int]:
        """Node ids of the values ``assert*`` calls assert (text and category keywords)."""
        keywords = [
            keyword.value
            for node in ast.walk(self.function)
            if isinstance(node, ast.Call) and _call_name(node).startswith("assert")
            for keyword in node.keywords
            if keyword.arg in TEXT_KEYWORDS or keyword.arg in CATEGORY_KEYWORDS
        ]
        return {id(child) for value in keywords for child in ast.walk(value)}

    def _input_strings(self) -> list[tuple[int, str]]:
        """``(node id, value)`` of the test's string constants that are not asserted values.

        Everything but the asserted values (arguments, payloads, parametrize
        decorators) is an input the program receives.
        """
        asserted = self._asserted_ids()
        return [
            (id(child), child.value)
            for child in ast.walk(self.function)
            if isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and id(child) not in asserted
        ]

    def _derived(self, expected: ast.AST, literal: str) -> bool:
        """Whether ``literal`` is (part of) one of the test's inputs, other than itself."""
        own = {id(child) for child in ast.walk(expected)}
        return any(literal in value for key, value in self.inputs if key not in own)

    def _error_text_names(self) -> set[str]:
        """Names bound to an error's ``detail``/``message`` or to a value derived from one."""
        changed = True
        while changed:
            changed = False
            for node in ast.walk(self.function):
                if not isinstance(node, ast.Assign) or not self._reads_error_text(node.value):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id not in self.text_names:
                        self.text_names.add(target.id)
                        changed = True
        return self.text_names

    def _reads_error_text(self, node: ast.AST) -> bool:
        if any(text in ERROR_TEXT_KEYS for text in _strings(node)):
            return True
        return any(
            isinstance(child, ast.Name) and child.id in self.text_names for child in ast.walk(node)
        )

    def _make(
        self, node: ast.AST, kind: AssertionKind, expected: ast.AST | None
    ) -> AuditedAssertion:
        literal: str | None = None
        derived = True
        if isinstance(expected, ast.Constant) and isinstance(expected.value, str):
            literal = expected.value
            derived = self._derived(expected, literal)
        line = getattr(node, "lineno", self.function.lineno)
        return AuditedAssertion(
            test_file=self.test_file,
            function=self.function.name,
            line=line,
            kind=kind,
            source=ast.unparse(node),
            expected=ast.unparse(expected) if expected is not None else "",
            literal=literal,
            input_derived=expected is not None and derived,
            category=self._category_at(line),
        )

    def _call(self, node: ast.Call) -> list[AuditedAssertion]:
        name = _call_name(node)
        found: list[AuditedAssertion] = []
        if name.startswith("assert"):
            found.extend(
                self._make(node, AssertionKind(TEXT_KEYWORDS[keyword.arg]), keyword.value)
                for keyword in node.keywords
                if keyword.arg in TEXT_KEYWORDS
            )
        if name in self.helpers.text_helpers and len(node.args) >= 2:
            found.append(self._make(node, self.helpers.text_helpers[name], node.args[-1]))
        if name in self.helpers.format_helpers:
            found.append(self._make(node, AssertionKind.OUTPUT_BYTES, None))
        return found

    def _bare_assert(self, node: ast.Assert) -> list[AuditedAssertion]:
        test = node.test
        if _mentions_stdout_bytes(test):
            return [self._make(node, AssertionKind.OUTPUT_BYTES, None)]
        if isinstance(test, ast.Call) and _call_name(test) in PATTERN_FUNCTIONS and test.args:
            if len(test.args) >= 2 and self._reads_error_text(test.args[1]):
                return [self._make(node, AssertionKind.ERROR_TEXT_PATTERN, test.args[0])]
            return []
        if isinstance(test, ast.Compare) and len(test.ops) == 1:
            return self._comparison(node, test)
        return []

    def _comparison(self, node: ast.Assert, test: ast.Compare) -> list[AuditedAssertion]:
        op, right = test.ops[0], test.comparators[0]
        if isinstance(op, ast.In) and self._reads_error_text(right):
            return [self._make(node, AssertionKind.ERROR_TEXT_CONTAINS, test.left)]
        if isinstance(op, ast.Eq):
            if self._reads_error_text(test.left) and not self._reads_error_text(right):
                return [self._make(node, AssertionKind.ERROR_TEXT_EQUALS, right)]
            if self._reads_error_text(right) and not self._reads_error_text(test.left):
                return [self._make(node, AssertionKind.ERROR_TEXT_EQUALS, test.left)]
        return []


def scan_test_file(test_file: str, source: str, helpers: HelperIndex) -> list[AuditedAssertion]:
    """Every audited assertion in the module's ``test_*`` functions."""
    found: list[AuditedAssertion] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
            found.extend(_FunctionScan(test_file, node, helpers).assertions())
    return found


def scan_tests(tests_dir: Path, test_files: Sequence[str]) -> list[AuditedAssertion]:
    """Audited assertions of ``test_files``, with helpers from the directory's other modules."""
    helper_sources = [
        path.read_text()
        for path in sorted(tests_dir.glob("*.py"))
        if not path.name.startswith("test_") and path.name != "conftest.py"
    ]
    helpers = HelperIndex.scan(helper_sources)
    found: list[AuditedAssertion] = []
    for name in test_files:
        found.extend(scan_test_file(name, (tests_dir / name).read_text(), helpers))
    return found


# ---- classification -----------------------------------------------------------------


def _quoted(paragraph: SpecParagraph) -> str:
    where = f" (under '{paragraph.heading}')" if paragraph.heading else ""
    return f"'{paragraph.text[:160]}'{where}"


def _format_rule(spec: SpecText) -> Determination:
    term = spec.names_format()
    if term is not None:
        return Determination(True, "format-stated", f"the specification names '{term}'")
    return Determination(
        False,
        "format-unstated",
        "the test compares stdout byte for byte, but the specification names no output "
        f"byte format (none of: {', '.join(FORMAT_TERMS)})",
    )


def _statement_rule(
    item: AuditedAssertion, spec: SpecText, phrases: Sequence[str], rule: str, what: str
) -> Determination:
    paragraph = spec.states(item.category, phrases)
    if paragraph is not None:
        return Determination(True, f"{rule}-stated", f"stated by {_quoted(paragraph)}")
    category = item.category or "<unknown category>"
    return Determination(
        False,
        f"{rule}-unstated",
        f"the {item.kind.value} assertion needs the {category} error text to {what} "
        f"{item.expected}, but no paragraph of the specification states what a "
        f"{category} detail or message contains",
    )


def classify(item: AuditedAssertion, spec: SpecText) -> Determination:
    """Whether ``spec`` determines the value ``item`` asserts (rules in the module docstring)."""
    if item.kind is AssertionKind.OUTPUT_BYTES:
        return _format_rule(spec)
    if item.literal is not None and spec.contains(item.literal):
        return Determination(True, "literal-in-spec", f"{item.literal!r} occurs in the spec")
    if item.literal is not None and not item.input_derived:
        return Determination(
            False,
            "wording-unstated",
            f"{item.literal!r} occurs neither in the specification nor among the test's inputs, "
            "so a conforming program would have to guess this wording",
        )
    if item.kind is AssertionKind.ERROR_TEXT_EQUALS:
        return _statement_rule(item, spec, EXACT_PHRASES, "exact", "equal")
    return _statement_rule(item, spec, CONTENT_VERBS, "content", "contain")
