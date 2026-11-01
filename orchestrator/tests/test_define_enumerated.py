"""The labelled lists an issue enumerates, and the token rule for a test exercising an item."""

from __future__ import annotations

import pytest

from define_tool import labelled, normative
from define_tool.enumerated import (
    Exercised,
    Listing,
    exercised,
    item_exercised,
    untested,
)
from define_tool.labelled import labelled_list
from define_tool.normative import enumerations
from define_tool.unitrules import V2

# --- what a labelled list is -----------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "label", "items"),
    [
        ("Operators: `!`, `*`, `()`", "Operators", ("!", "*", "()")),
        ("* Operators: `!`, `*`, … `&&`, `||`.", "Operators", ("!", "*", "&&", "||")),
        ("- **Error codes:** `A_B` and `C_D`", "Error codes", ("A_B", "C_D")),
        (
            "1. Flags: `-a`; `-b` / `-c` or `-d`, etc.",
            "Flags",
            ("-a", "-b", "-c", "-d"),
        ),
        ("Keys: `a`, `b`, `a`", "Keys", ("a", "b")),
        ("Kinds: `x`, `y`, ...", "Kinds", ("x", "y")),
        # An operator is not a field set to a value.
        ("Comparisons: `==`, `!=`, `<=`", "Comparisons", ("==", "!=", "<=")),
        ("Flags: `--mode=fast`, `--x=1`", "Flags", ("--mode=fast", "--x=1")),
    ],
)
def test_a_label_and_backticked_items_make_a_list(
    line: str, label: str, items: tuple[str, ...]
) -> None:
    assert labelled_list(line) == Listing(label, items)


@pytest.mark.parametrize(
    "line",
    [
        "Literals: numbers, `true`, `false`",
        "`op`: `a`, `b`",
        "Just one: `a`",
        "Same: `a`, `a`",
        "No colon `a`, `b`",
        "1: `a`, `b`",
        f"{'x' * 61}: `a`, `b`",
        "Prose: see `a` for the details of `b`",
        "Empty: `` , ``",
        "",
        # An illustration or a pointer, not a set whose every member is specified.
        "Examples: `a.b`, `c.d`",
        "Valid examples: `5s`, `2h`",
        "e.g.: `a`, `b`",
        "Note: `a`, `b`",
        "See: `README.md` and `docs`",
        # One record or one case: a field set to a value, a mapping.
        "Entry metadata: `mtime=0`, `mode=0o644`",
        'Missing: `field = "name"`, `type = "gone"`',
        'Cases: `1 -> "1"`, `2 -> "2"`',
        "Cases: `a \u2192 b`, `c \u2192 d`",
    ],
)
def test_other_lines_are_not_lists(line: str) -> None:
    assert labelled_list(line) is None


def test_lists_are_the_units_that_are_labelled_lists_so_the_intake_and_c5_agree() -> None:
    issue = """\
# Heading: `h1`, `h2`

Ops: `a`, `b`

```
Fenced: `c`, `d`
```

<!-- Hidden: `e`, `f` -->
* Codes: `g`, `h`
* Allowed ops: `i`, `j`. No symlinks are followed.

| Name | Meaning |
| --- | --- |
| Row: `k`, `l` | m |
"""

    found = enumerations(issue)

    assert found == [
        Listing("Ops", ("a", "b")),
        Listing("Codes", ("g", "h")),
        Listing("Allowed ops", ("i", "j")),
    ]
    # Every list is a unit the intake's rules 2 call an enumeration.
    units = [u for u in normative.units(issue, V2) if "enumeration" in u.signals]
    assert [labelled.labelled_list(u.text) for u in units] == found


# --- when the tests exercise an item ---------------------------------------------------


def said(*files: str) -> Exercised:
    return exercised({f"tests/test_{n}.py": text for n, text in enumerate(files)})


def gaps(items: tuple[str, ...], *files: str) -> tuple[str, ...]:
    return untested(Listing("L", items), said(*files))


def test_a_word_item_is_a_token_of_a_string_never_a_substring_of_a_word() -> None:
    code = 'def test_a():\n    run("x or y", "color", "BAD_VALUE_2")\n'

    assert gaps(("or", "BAD_VALUE"), code) == ("BAD_VALUE",)
    assert gaps(("or", "olor"), code) == ("olor",)
    assert gaps(("BAD_VALUE_2", "x"), code) == ()


def test_a_word_item_may_be_a_name_or_a_number_of_the_code() -> None:
    code = (
        "import m\n"
        "def test_a(case):\n"
        "    m.parse(strict=True, limit=404)\n"
        "    assert case.fields == 0.5\n"
    )

    assert gaps(("parse", "strict", "limit", "fields", "case", "404", "0.5"), code) == ()
    assert gaps(("True", "None", "not"), code) == ("True", "None", "not")


def test_a_builtin_a_message_and_an_initialiser_say_nothing_about_the_item() -> None:
    types = "def test_a(x, y):\n    assert isinstance(x, int) and isinstance(y, float)\n"
    assert gaps(("int", "float", "string"), types) == ("int", "float", "string")
    aggregates = "def test_a(x):\n    assert sum(x) == min(x) + max(x)\n"
    assert gaps(("sum", "min", "max"), aggregates) == ("sum", "min", "max")

    message = 'def test_a(x):\n    assert x, f"should not be BAD_FLAG {x}"\n'
    assert gaps(("BAD_FLAG",), message) == ("BAD_FLAG",)
    assert gaps(("BAD_FLAG",), 'def test_a(x):\n    assert run("BAD_FLAG")\n') == ()

    start = "def test_a():\n    x = 0\n    y: int = 1\n    z = 2\n    z += 3\n"
    assert gaps(("0", "1", "2", "3"), start) == ("0", "1", "2", "3")
    used = "def test_a(x):\n    assert x == 404\n    run(0)\n"
    assert gaps(("404", "0"), used) == ()
    # A string assigned to a name is still said to the program when it is used.
    assert gaps(("!",), 'def test_a():\n    expr = "!x"\n    run(expr)\n') == ()


def test_a_python_keyword_of_the_test_says_nothing_about_the_item() -> None:
    code = "def test_a(x):\n    assert not x and True or None\n"

    assert gaps(("not", "and", "or"), code) == ("not", "and", "or")
    assert gaps(("not", "and", "or"), 'def test_a():\n    run("not x and y or z")\n') == ()


def test_a_docstring_or_comment_is_prose_and_a_string_is_not() -> None:
    code = (
        '"""Module: BAD_A."""\n'
        "class TestK:\n"
        '    """Class: BAD_B."""\n'
        "    def test_a(self):\n"
        '        """Function: BAD_C."""\n'
        "        # comment BAD_D\n"
        '        note("BAD_E")\n'
        "    async def test_b(self):\n"
        '        """Async: BAD_F."""\n'
    )

    assert gaps(tuple(f"BAD_{c}" for c in "ABCDEF"), code) == tuple(f"BAD_{c}" for c in "ABCDF")


def test_the_literal_parts_of_an_f_string_and_every_file_are_read() -> None:
    first = 'def test_a(v):\n    run(f"{v} && 1")\n    data = b"\\x00 BYTES"\n'
    second = 'def test_b():\n    run("||")\n'

    assert gaps(("&&", "||", "BYTES"), first, second) == ("BYTES",)


def test_a_file_that_does_not_parse_is_left_out() -> None:
    assert gaps(("a", "b"), "def (:\n", 'def test_a():\n    run("a")\n') == ("b",)
    assert gaps(("a", "b"), "x = '\\x00'\x00") == ("a", "b")


def test_a_symbol_item_counts_in_strings_only() -> None:
    code = 'def test_a(x, y):\n    assert x * y - 1 < (x)\n    run("!x")\n'

    assert gaps(("!", "*", "-", "<", "()"), code) == ("*", "-", "<", "()")


def test_a_hyphen_or_slash_joining_a_date_a_path_or_a_word_is_not_an_operator() -> None:
    incidental = 'def test_a():\n    run("2024-01-01 a/b read-only v1.2/x")\n'
    assert gaps(("-", "/"), incidental) == ("-", "/")
    operators = 'def test_a():\n    run("x - 1", "a / b", "-1", "/", "3 -")\n'
    assert gaps(("-", "/"), operators) == ()
    # A longer symbol is not a path or a date.
    assert gaps(("+", "*"), 'def test_a():\n    run("a+b*c")\n') == ()


def test_a_symbol_inside_a_longer_listed_symbol_is_not_the_shorter_one() -> None:
    items = ("<", "<=", "!", "!=")

    assert gaps(items, 'def test_a():\n    run("a <= b", "a != b")\n') == ("<", "!")
    assert gaps(items, 'def test_a():\n    run("a <= b", "a < b")\n') == ("!", "!=")
    # Not listed, so ``<`` in ``<<`` is its own (the repeat rule decides below).
    assert gaps(("<",), 'def test_a():\n    run("a < b")\n') == ()


def test_a_symbol_beside_its_own_character_is_a_different_token() -> None:
    assert gaps(("*",), 'def test_a():\n    run("a ** b")\n') == ("*",)
    assert gaps(("-",), 'def test_a():\n    run("a--b", "--c")\n') == ("-",)
    assert gaps(("-",), 'def test_a():\n    run("a -b", "c-")\n') == ()
    assert gaps(("*",), 'def test_a():\n    run("*a")\n') == ()
    assert gaps(("*",), 'def test_a():\n    run("a * b", "**")\n') == ()


def test_a_pair_item_is_the_pair_or_its_two_characters_in_strings() -> None:
    assert gaps(("()", "[]", "{}"), 'def test_a():\n    run("(a) + f()", "[1]")\n') == ("{}",)
    assert gaps(("()",), 'def test_a():\n    run("(a")\n') == ("()",)
    assert gaps(("()",), "def test_a():\n    run(a)\n") == ("()",)


def test_a_mixed_item_needs_no_word_beside_its_word_edges() -> None:
    code = 'def test_a():\n    run(["--top", "x --top5", "a.b"])\n'

    assert gaps(("--top", "a.b"), code) == ()
    assert gaps(("--top5",), code) == ()
    assert gaps(("--to",), code) == ("--to",)
    assert gaps(("a.b ",), code) == ("a.b ",)


# --- token rule 2: where a symbol stands ----------------------------------------------


def test_a_prefix_operator_used_only_after_another_operator_must_also_begin_a_string() -> None:
    mid = 'def test_a():\n    run("a && !b", "(!c)", "f(x, !y)")\n'
    start = 'def test_b():\n    run("!d")\n'

    assert gaps(("!",), mid) == ("!",)
    assert gaps(("!",), mid, start) == ()
    # Whitespace before it, a quoted value and any line of a longer string begin one.
    assert gaps(("!",), 'def test_a():\n    run("  !a")\n') == ()
    assert gaps(("!",), 'def test_a():\n    run("a && !b", "run \'!a\'")\n') == ()
    assert gaps(("!",), 'def test_a():\n    run(\'{"k": "!a"}\', "a && !b")\n') == ()
    assert gaps(("!",), 'def test_a():\n    run("x\\n!y", "a && !b")\n') == ()


def test_a_symbol_used_between_two_operands_is_a_binary_operator_the_clause_leaves_alone() -> None:
    assert gaps(("+",), 'def test_a():\n    run("a + b", "a + +b")\n') == ()
    assert gaps(("-",), 'def test_a():\n    run("2 * -1")\n') == ("-",)
    assert gaps(("-",), 'def test_a():\n    run("2 * -1", "5 - 2")\n') == ()
    assert gaps(("-",), 'def test_a():\n    run("2 * -1", "-3")\n') == ()
    # A word item is not read for position.
    assert gaps(("not",), 'def test_a():\n    run("a and not b")\n') == ()


def test_a_symbol_glued_behind_a_word_with_nothing_after_it_is_punctuation() -> None:
    data = 'def test_a():\n    run("done!", \'"true!"\', "(wow!)")\n'

    assert gaps(("!",), data) == ("!",)
    # With an operand after it, it is an operator.
    assert gaps(("!",), 'def test_a():\n    run("wow! ok")\n') == ()
    assert gaps(("*",), 'def test_a():\n    run("ab*", "ab*c")\n') == ()
    assert gaps(("*",), 'def test_a():\n    run("ab*")\n') == ("*",)


def test_rule_one_reads_neither_position_clause_so_a_recorded_capture_reproduces() -> None:
    mid = said('def test_a():\n    run("a && !b")\n')
    data = said('def test_a():\n    run("done!")\n')
    listing = Listing("L", ("!",))

    assert untested(listing, mid) == ("!",)
    assert untested(listing, mid, version=1) == ()
    assert untested(listing, data, version=1) == ()
    assert item_exercised("!", mid, listing.items, version=1)
    assert not item_exercised("!", mid, listing.items)
