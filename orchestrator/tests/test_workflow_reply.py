"""The structured build reply: factory-x-loops' parser cases replayed, and its shape sentence."""

from __future__ import annotations

import time

import pytest

from raidar.subject.workflow.reply import (
    BUILD_SCHEMA,
    MAX_VIOLATIONS,
    VIOLATION_WIDTH,
    JsonObject,
    drop_unknown,
    extract_object,
    read_reply,
    shape_suffix,
    skeleton,
    violations,
)

NESTED: JsonObject = {
    "type": "object",
    "required": ["name", "tags", "child", "rows"],
    "properties": {
        "name": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "kind": {"type": "string", "enum": ["a", "b", ""]},
        "child": {
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
        },
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["x"],
                "properties": {"x": {"type": "string"}},
            },
        },
        "count": {"type": "integer"},
    },
}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param('{"a": 1}', {"a": 1}, id="bare"),
        pytest.param('```json\n{"a": 1}\n```', {"a": 1}, id="fenced"),
        pytest.param('Done. Here it is: {"a": 1} Thanks.', {"a": 1}, id="prose-around"),
        pytest.param('{"a": 1}\nthen\n{"a": 2}', {"a": 2}, id="last-of-two"),
        pytest.param(
            '{"a": {"b": 1}, "c": [{"d": 2}]}',
            {"a": {"b": 1}, "c": [{"d": 2}]},
            id="nested-braces-are-not-top-level",
        ),
        pytest.param(
            '{"a": "} not a close {", "b": "say \\"}\\" now\\\\"}',
            {"a": "} not a close {", "b": 'say "}" now\\'},
            id="braces-in-strings",
        ),
        pytest.param('{"a": 1} then {not json}', {"a": 1}, id="last-that-parses"),
        pytest.param('{"a": 1} then [1, 2]', {"a": 1}, id="array-is-not-an-object"),
        pytest.param('use { then {"a": 1}', {"a": 1}, id="unclosed-brace-before"),
        pytest.param(
            'Format is {"verdict": "pass"|"fail"}.\n{"verdict":"pass"}',
            {"verdict": "pass"},
            id="a-broken-example-before-the-answer",
        ),
        pytest.param(
            'I will return {"verdict": ...\n```json\n{"verdict":"pass"}\n```',
            {"verdict": "pass"},
            id="an-elided-example-before-the-answer",
        ),
    ],
)
def test_extract_object_takes_the_last_balanced_top_level_object(
    text: str, expected: JsonObject
) -> None:
    assert extract_object(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("{" * 50_000, id="unmatched-open-braces"),
        pytest.param('{"a":' * 20_000, id="repeated-open-objects"),
        pytest.param("{ [" * 30_000 + '{"a": 1}', id="unclosed-then-an-object"),
    ],
)
def test_extract_object_scans_a_long_unbalanced_reply_in_linear_time(text: str) -> None:
    started = time.perf_counter()
    try:
        found = extract_object(text)
    except ValueError:
        found = None
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0
    assert found in (None, {"a": 1})


def test_extract_object_keeps_the_object_before_a_trailing_brace() -> None:
    assert extract_object('{"a": {"b": 1}} {') == {"a": {"b": 1}}
    assert extract_object('{"a": 1} then { see above }') == {"a": 1}


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1} then {"b": }',
        '{"draft": 1}\nfinal: {"a": 1,, }',
        '{"draft": 1}\nfinal: {"a": [1, 2',
    ],
)
def test_a_broken_object_after_a_good_one_is_a_syntax_fault_never_the_earlier_one(
    text: str,
) -> None:
    with pytest.raises(ValueError, match="reply is not valid JSON"):
        extract_object(text)


def test_a_broken_outer_object_is_a_syntax_fault_never_its_inner_object() -> None:
    text = '{"features":[{"name":"A"},{"name":"B"}],"note":"a " b"}'

    with pytest.raises(ValueError, match="reply is not valid JSON") as caught:
        extract_object(text)

    message = str(caught.value)
    assert message.startswith("reply is not valid JSON: Expecting ',' delimiter")
    assert f"(char {text.index('b')})" in message
    assert '"note":"a " b"}' in message


def test_the_fault_excerpt_is_short_and_the_position_is_in_the_whole_reply() -> None:
    padding = "x " * 400
    text = f'{padding}{{"a": [1, 2 3], "b": "{"y" * 400}"}}'

    with pytest.raises(ValueError, match="reply is not valid JSON") as caught:
        extract_object(text)

    assert f"(char {text.index('3]')})" in str(caught.value)
    assert len(str(caught.value)) < 250


def test_an_object_nested_too_deeply_is_a_syntax_fault() -> None:
    with pytest.raises(ValueError, match="nested too deeply"):
        extract_object('{"a":' * 20_000 + "1")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        pytest.param("", "reply holds no JSON object", id="empty"),
        pytest.param("um", "reply holds no JSON object", id="prose"),
        pytest.param("[1, 2]", "reply holds no JSON object", id="array-only"),
        pytest.param("{ never closed", "reply holds no JSON object", id="unclosed"),
        pytest.param(
            '{"result": {"ok": true}',
            "reply is not valid JSON: Expecting ',' delimiter",
            id="a-cut-off-outer-is-not-its-inner",
        ),
        pytest.param(
            '{"a": "cut off',
            "reply is not valid JSON: Unterminated string",
            id="cut-off-in-a-string",
        ),
        pytest.param(
            '{ // answer\n "result": {"ok": true} }',
            "reply is not valid JSON: Expecting property name",
            id="a-comment-before-the-first-key",
        ),
        pytest.param(
            "{'a': 1}",
            "reply is not valid JSON: Expecting property name",
            id="single-quotes",
        ),
        pytest.param(
            '{oops} {"b": }',
            "reply is not valid JSON: Expecting value",
            id="the-last-spans-fault-is-reported",
        ),
    ],
)
def test_extract_object_names_why_a_reply_has_no_object(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        extract_object(text)


def test_drop_unknown_removes_every_undeclared_property_at_any_depth() -> None:
    value = {
        "name": "n",
        "tags": ["t"],
        "child": {"id": "c", "stray": 1},
        "rows": [{"x": "1", "y": 2}, {"x": "2"}, {"z": 3}],
        "title": "misplaced",
        "decisions": [],
    }

    kept, dropped = drop_unknown(value, NESTED)

    assert kept == {
        "name": "n",
        "tags": ["t"],
        "child": {"id": "c"},
        "rows": [{"x": "1"}, {"x": "2"}, {}],
    }
    assert dropped == ["/child/stray", "/rows/0/y", "/rows/2/z", "/title", "/decisions"]
    assert value["title"] == "misplaced"  # the reply is not mutated


def test_drop_unknown_escapes_the_names_in_a_pointer() -> None:
    _, dropped = drop_unknown({"a/b": 1, "c~d": 2}, NESTED)

    assert dropped == ["/a~1b", "/c~0d"]


def test_drop_unknown_leaves_what_the_schema_does_not_describe() -> None:
    schema: JsonObject = {
        "type": "object",
        "properties": {
            "free": {},
            "list": {"type": "array"},
            "scalar": {"type": "string"},
            "flat": {"type": "object"},
        },
    }
    value = {
        "free": {"anything": 1},
        "list": [{"x": 1}],
        "scalar": ["not", "a", "string"],
        "flat": {"x": 1},
    }

    assert drop_unknown(value, schema) == (value, [])


def test_violations_lists_every_fault_path_qualified() -> None:
    value = {
        "name": 3,
        "tags": ["ok", 4],
        "kind": "c",
        "child": {},
        "rows": [{"x": "1"}, {}],
        "count": "many",
    }

    found = violations(value, NESTED)

    assert [line.split(": ", 1)[0] for line in found] == [
        "/child",
        "/count",
        "/kind",
        "/name",
        "/rows/1",
        "/tags/1",
    ]
    assert found[0] == "/child: 'id' is a required property"
    assert found[3] == "/name: 3 is not of type 'string'"


def test_violations_are_bounded_and_each_message_is_short() -> None:
    value = {"name": "n" * 1000, "tags": [1] * 20, "child": {"id": "c"}, "rows": []}

    found = violations(value, NESTED)

    assert len(found) == MAX_VIOLATIONS + 1
    assert found[-1] == f"(and {20 - MAX_VIOLATIONS} more)"
    assert all(len(line) < VIOLATION_WIDTH + 40 for line in found)


def test_a_schema_is_rendered_as_the_shape_of_the_object_it_asks_for() -> None:
    schema: JsonObject = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {
                "type": "array",
                "items": {"type": "object", "properties": {"c": {"type": "string"}}},
            },
            "d": {"type": "array", "items": {"type": "string"}},
        },
    }

    assert skeleton(schema) == '{"a": string, "b": [{"c": string}], "d": [string]}'


def test_the_builder_reply_names_each_schema_property_once_at_the_prompts_end() -> None:
    shown = skeleton(BUILD_SCHEMA)
    properties = list(BUILD_SCHEMA["properties"])  # type: ignore[call-overload]

    assert [shown.index(f'"{name}":') for name in properties] == sorted(
        shown.index(f'"{name}":') for name in properties
    )
    assert all(shown.count(f'"{name}":') == 1 for name in properties)
    suffix = shape_suffix(BUILD_SCHEMA)
    assert suffix.endswith(shown)
    assert suffix.count(shown) == 1


def test_a_schema_node_with_no_type_cannot_be_rendered() -> None:
    with pytest.raises(ValueError, match="no type or enum"):
        skeleton({"anyOf": []})
    assert skeleton({"enum": ["a", "b"]}) == '"a" | "b"'
    assert skeleton({"type": ["string", "null"]}) == "string | null"


def test_read_reply_returns_the_value_with_what_was_dropped() -> None:
    text = (
        'All done.\n{"account": "built", "adrs": [], "ac_tests": '
        '[{"criterion": "F1.AC1", "tests": ["t.py::test_a"]}], "note": "extra"}'
    )

    value, error, dropped = read_reply(text, BUILD_SCHEMA)

    assert error is None
    assert dropped == ["/note"]
    assert value == {
        "account": "built",
        "adrs": [],
        "ac_tests": [{"criterion": "F1.AC1", "tests": ["t.py::test_a"]}],
    }


def test_a_refused_reply_names_the_violations_and_the_misplaced_properties() -> None:
    text = '{"account": "built", "adrs": [], "result": {"ac_tests": []}}'

    value, error, dropped = read_reply(text, BUILD_SCHEMA)

    assert value is None
    assert dropped == ["/result"]
    assert error == (
        "reply violates the schema: (root): 'ac_tests' is a required property; ignored: /result"
    )


def test_an_unreadable_reply_is_a_syntax_error_with_no_dropped_list() -> None:
    assert read_reply("", BUILD_SCHEMA) == (None, "reply holds no JSON object", [])


def test_a_refused_reply_that_dropped_nothing_says_nothing_was_ignored() -> None:
    value, error, dropped = read_reply('{"account": "built", "adrs": []}', BUILD_SCHEMA)

    assert value is None
    assert dropped == []
    assert error == "reply violates the schema: (root): 'ac_tests' is a required property"
