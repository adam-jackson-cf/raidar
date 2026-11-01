"""The ``define`` tool and the draft it keeps: calls, findings, state and the exit codes."""

from __future__ import annotations

import hashlib
import io
import json
import runpy
from pathlib import Path

import pytest

from define_tool import block, tool
from define_tool.block import Item
from define_tool.core import JsonObject
from define_tool.draft import Draft, DraftError
from define_tool.kinds import Kind
from tests import define_support as dbv


@pytest.fixture
def state(tmp_path: Path) -> Path:
    directory = tmp_path / "state"
    context = tool.Context(dbv.BODY, (), "(first round)", (), str(tmp_path), 1)
    tool.start(directory, context)
    return directory


@pytest.fixture
def draft() -> Draft:
    return Draft()


def call(state: Path | Draft, *args: str, stdin: str = "") -> tool.Outcome:
    if isinstance(state, Draft):
        context = tool.Context(dbv.BODY, (), "(first round)", (), "", 1)
        return tool._command(state, context, args, lambda: stdin)
    return tool.run(state, list(args), lambda: stdin)


def put(state: Path | Draft, reply: JsonObject) -> tool.Outcome:
    return call(state, "put", stdin=dbv.block_of(reply))


def drafted(state: Path | Draft) -> Draft:
    if isinstance(state, Draft):
        return state
    found = tool.read_draft(state)
    assert found is not None
    return found


# --- the state -------------------------------------------------------------------------


def test_a_call_starts_with_an_empty_state_holding_only_the_context(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "state"
    directory.mkdir()
    (directory / "old.json").write_text("{}", encoding="utf-8")
    context = tool.Context("issue", ("a",), "prior", ("Q1.1",), "/work", 1)

    tool.start(directory, context)

    assert sorted(path.name for path in directory.iterdir()) == [tool.CONTEXT_FILE]
    held = json.loads((directory / tool.CONTEXT_FILE).read_text(encoding="utf-8"))
    assert tool.Context.read(held) == context
    assert tool.read_draft(directory) is None
    assert tool.read_calls(directory) == []


@pytest.mark.parametrize("make", ["link", "file"])
def test_a_state_path_an_agent_replaced_is_made_a_directory_again(
    tmp_path: Path, make: str
) -> None:
    directory = tmp_path / "state"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    if make == "link":
        directory.symlink_to(elsewhere, target_is_directory=True)
    else:
        directory.write_text("mine", encoding="utf-8")

    tool.start(directory, tool.Context("", (), "", (), "", 1))

    assert directory.is_dir()
    assert not directory.is_symlink()
    assert list(elsewhere.iterdir()) == []


def test_a_state_that_does_not_read_is_an_error() -> None:
    def item(**fields: object) -> JsonObject:
        return {"kind": "feature", "key": "F1", "data": {}, **fields}

    records: list[object] = [
        [],
        {"items": "x", "sealed": ""},
        {"items": [], "sealed": "later"},
        {"items": [], "sealed": None},
        {"items": [1], "sealed": ""},
        {"items": [item(kind="nope")], "sealed": ""},
        {"items": [item(key=1)], "sealed": ""},
        {"items": [item(data=[])], "sealed": ""},
        {"items": [item(kind="decision", key="one")], "sealed": ""},
        {"items": [item(kind="ac", key="F1.AC1", owner="F1")], "sealed": ""},
    ]
    for record in records:
        with pytest.raises(DraftError):
            Draft.read(record)


def test_a_state_file_that_is_not_json_is_an_error(state: Path) -> None:
    (state / tool.STATE_FILE).write_text("{", encoding="utf-8")

    with pytest.raises(DraftError, match="cannot be read"):
        tool.read_draft(state)
    broken = call(state, "show")
    assert (broken.code, broken.call.command) == (tool.BROKEN, "error")
    assert broken.output.startswith("define: ERROR, the state cannot be used")


def test_the_record_of_calls_keeps_each_with_its_findings_in_order(
    state: Path,
) -> None:
    call(state, "put", stdin="colour: red\n")
    call(state, "show")
    (path := state / tool.CALLS_FILE).write_text(
        path.read_text(encoding="utf-8") + "not json\n[1]\n", encoding="utf-8"
    )

    first, second = tool.read_calls(state)

    assert (first.command, second.command) == ("put", "show")
    assert first.texts[0] == "line 1: unknown key `colour`"
    assert first.kinds[0] == Kind.GRAMMAR
    assert first.advisory[0] is False
    assert len(first.blocking) == len(first.texts) - 2
    assert first.advisory[-2:] == (True, True)
    assert second.texts == ()


# --- the commands ----------------------------------------------------------------------


def test_one_put_of_the_whole_definition_builds_it_and_done_seals_it(
    draft: Draft,
) -> None:
    built = put(draft, dbv.ready())

    assert built.code == 0
    assert built.output.splitlines()[0] == (
        "put: applied 10 items (1 title, 1 objective, 1 named feature, 1 feature, "
        "5 criteria, 1 decision)"
    )
    assert built.output.endswith(
        "nothing to fix; run `define done ready` (or `ask`) when the draft is finished\n"
    )
    sealed = call(draft, "done", "ready")
    assert (sealed.code, sealed.output) == (0, "done ready: sealed\n")
    held = drafted(draft)
    assert held.sealed == "ready"
    assert held.reply("ready") == dbv.ready()


def test_what_show_prints_a_put_reads_back_as_the_same_definition(
    state: Path,
) -> None:
    put(state, dbv.ready())
    shown = call(state, "show")

    assert shown.code == 0
    again = state.parent / "again"
    tool.start(again, tool.Context(dbv.BODY, (), "", (), "/", 1))
    call(again, "put", stdin=shown.output)

    assert drafted(again).reply("ready") == dbv.ready()
    assert call(again, "show").output == shown.output


def test_a_put_names_what_is_wrong_by_item_and_still_applies_the_rest(
    draft: Draft,
) -> None:
    whole = dbv.block_of(dbv.ready())
    text = whole + "colour: red\nac F1.AC9: nine\naspect: no arrow\n"
    first = len(whole.splitlines()) + 1

    outcome = call(draft, "put", stdin=text)

    assert outcome.code == tool.FAULT
    assert f"- line {first}: unknown key `colour`" in outcome.output
    assert (
        f"- F1.AC9 line {first + 2}: `aspect:` needs one `->` outside backticks" in outcome.output
    )
    assert "has no `aspect:` line" in outcome.output
    assert outcome.output.endswith(" to fix\n")
    held = drafted(draft)
    assert len(held.kind(block.AC)) == 6
    assert held.sealed == ""


def test_a_fix_is_a_put_of_the_one_item_and_leaves_the_rest(draft: Draft) -> None:
    bad = dbv.body([dbv.criterion("F1.AC1", sources=["U9"])])
    broken = put(draft, dbv.ready(bad))
    fix = block.show([Item(block.AC, "F1.AC1", dbv.criterion("F1.AC1"), "F1")])

    fixed = call(draft, "put", stdin=fix)

    assert broken.code == tool.FAULT
    assert "`U9`, which is no unit" in broken.output
    assert (fixed.code, fixed.output.splitlines()[0]) == (
        0,
        "put: applied 1 item (1 criterion)",
    )
    assert drafted(draft).reply("ready")["definition"] == dbv.body([dbv.criterion("F1.AC1")])
    assert tool.recheck(draft, tool.Context(dbv.BODY, (), "", (), "", 1), "ready").blocking == ()


def test_putting_the_same_block_twice_changes_nothing(draft: Draft) -> None:
    put(draft, dbv.ready())
    once = call(draft, "show").output

    put(draft, dbv.ready())

    assert call(draft, "show").output == once


@pytest.mark.parametrize("prefix", ["", "title: t\n"])
def test_a_criterion_whose_feature_is_not_defined_is_refused(draft: Draft, prefix: str) -> None:
    outcome = call(draft, "put", stdin=f"{prefix}ac F4.AC1: orphan\nsource: U1\n")
    line = len(prefix.splitlines()) + 1

    assert outcome.code == tool.FAULT
    assert (
        f"- F4.AC1 line {line}: feature F4 is not defined: put `feature F4: <name>` with it"
        in outcome.output
    )
    assert outcome.output.startswith(
        "put: applied 1 item (1 title)\n" if prefix else "put: applied nothing\n"
    )
    assert draft.items == (
        [Item(block.TITLE, block.TITLE, {"text": "t"}, line=1)] if prefix else []
    )


def test_a_criterion_may_come_before_its_feature_in_one_block(draft: Draft) -> None:
    call(draft, "put", stdin="ac F1.AC1: t\nsource: U1\nfeature F1: f\n")

    assert [item.key for item in drafted(draft).items] == ["F1", "F1.AC1"]


def test_check_lists_the_findings_without_changing_anything(draft: Draft) -> None:
    put(draft, dbv.ready(dbv.EMPTY))
    before = call(draft, "show").output

    checked = call(draft, "check")

    assert checked.code == tool.FAULT
    assert checked.output.startswith("check:\n")
    assert "- the definition has no feature: put `feature F1: <name>`" in checked.output
    assert call(draft, "show").output == before


def test_done_refuses_while_a_finding_blocks_and_seals_when_only_advice_is_left(
    state: Path,
) -> None:
    put(state, dbv.ready(dbv.EMPTY))

    refused = call(state, "done", "ready")

    assert refused.code == tool.FAULT
    assert refused.output.startswith("done ready: not sealed\n")
    assert drafted(state).sealed == ""
    # A requirement no criterion cites only advises: `done` seals and lists it.
    first = dbv.criterion("F1.AC1", sources=["U2"])
    uncited = dbv.body([first, *(dbv.criterion(i) for i in dbv.AC[1:])])
    put(state, dbv.ready(uncited))

    sealed = call(state, "done", "ready")

    assert sealed.code == 0
    assert "- (advisory) the issue statement U1" in sealed.output
    assert drafted(state).sealed == "ready"
    assert tool.read_calls(state)[-1].blocking == ()


def test_the_second_unit_rules_make_an_uncited_requirement_block_done(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "state"
    tool.start(directory, tool.Context(dbv.BODY, (), "(first round)", (), "/", 2))
    first = dbv.criterion("F1.AC1", sources=["U2"])
    uncited = dbv.body([first, *(dbv.criterion(i) for i in dbv.AC[1:])])
    put(directory, dbv.ready(uncited))

    refused = call(directory, "done", "ready")

    assert refused.code == tool.FAULT
    assert refused.output.startswith("done ready: not sealed\n")
    assert "- the issue statement U1" in refused.output
    assert "(advisory)" not in refused.output
    assert drafted(directory).sealed == ""


@pytest.mark.parametrize("found", [None, 0, 3, "1", True])
def test_a_context_that_names_no_unit_rules_is_the_tool_failing(
    tmp_path: Path, found: object
) -> None:
    record = tool.Context(dbv.BODY, (), "", (), "/", 1).record()
    record["unit_rules"] = found
    with pytest.raises(ValueError, match="names no unit rule set"):
        tool.Context.read(record)
    directory = tmp_path / "state"
    directory.mkdir()
    (directory / tool.CONTEXT_FILE).write_text(json.dumps(record), encoding="utf-8")

    outcome = call(directory, "show")

    assert outcome.code == tool.BROKEN
    assert tool.read_calls(directory)[-1].command == tool.ERROR_CALL


def test_a_change_after_done_unseals_the_draft(draft: Draft) -> None:
    put(draft, dbv.ready())
    call(draft, "done", "ready")

    call(draft, "put", stdin="title: A new title\n")
    assert drafted(draft).sealed == ""
    call(draft, "done", "ready")
    call(draft, "drop", "decision", "1")

    assert drafted(draft).sealed == ""


@pytest.mark.parametrize("origin", ["issue", "none"])
def test_done_ask_seals_the_questions_and_sends_the_definition_empty(
    draft: Draft,
    origin: str,
) -> None:
    reply = dbv.asks(features=[dbv.named(behaviour_from=origin)])
    put(draft, reply)

    asked = call(draft, "done", "ask")

    assert asked.code == 0
    assert drafted(draft).reply("needs_input") == reply
    wrong = call(draft, "done", "ready")
    assert wrong.code == tool.FAULT
    assert "- `define done ready` needs no `question`" in wrong.output


def test_a_question_alone_is_checked_as_asking_and_a_definition_as_ready(
    draft: Draft,
) -> None:
    put(draft, dbv.ready())
    assert drafted(draft).presumed() == "ready"

    put(draft, dbv.asks())

    assert drafted(draft).presumed() == "needs_input"


def test_replies_are_checked_against_the_questions_open_this_call(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "state"
    tool.start(directory, tool.Context(dbv.BODY, (), "", ("Q1.1", "Q1.2"), str(tmp_path), 1))
    reply = dbv.ready(replies=[dbv.reply("Q1.1")])

    outcome = put(directory, reply)

    assert "- there is no `reply Q1.2:` line" in outcome.output


def test_drop_removes_an_item_and_a_feature_takes_its_criteria(draft: Draft) -> None:
    put(draft, dbv.ready())

    one = call(draft, "drop", "F1.AC5")
    assert one.output.splitlines()[0] == "drop: removed `F1.AC5`"
    assert len(drafted(draft).kind(block.AC)) == 4

    for words, kind in (
        (("decision", "1"), block.DECISION),
        (("named", "F1", "greeting"), block.NAMED),
        (("F1",), block.FEATURE),
    ):
        assert call(draft, "drop", *words).code in (0, tool.FAULT)
        assert drafted(draft).kind(kind) == []
    assert drafted(draft).kind(block.AC) == []


def test_drop_of_nothing_says_so(draft: Draft) -> None:
    put(draft, dbv.ready())

    outcome = call(draft, "drop", "F9")

    assert outcome.code == tool.FAULT
    assert outcome.output.startswith("drop: nothing is named `F9`; the draft holds ")
    assert "`F1`, `F1.AC1`" in outcome.output


def test_put_reads_a_file_when_given_one(state: Path, tmp_path: Path) -> None:
    path = tmp_path / "block.txt"
    path.write_text("title: From a file\n", encoding="utf-8")

    outcome = call(state, "put", str(path))

    assert outcome.output.startswith("put: applied 1 item (1 title)")
    assert drafted(state).kind(block.TITLE)[0].data == {"text": "From a file"}


def test_a_file_that_is_missing_is_a_usage_error_and_changes_nothing(
    state: Path, tmp_path: Path
) -> None:
    outcome = call(state, "put", str(tmp_path / "nope.txt"))

    assert outcome.code == tool.USAGE_ERROR
    assert outcome.output.startswith("define: ")
    assert tool.read_draft(state) is None


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("frob",),
        ("put", "a", "b"),
        ("show", "x"),
        ("check", "x"),
        ("drop",),
        ("done",),
        ("done", "maybe"),
    ],
)
def test_anything_else_prints_the_usage(draft: Draft, args: tuple[str, ...]) -> None:
    outcome = call(draft, *args)

    assert (outcome.code, outcome.output) == (tool.USAGE_ERROR, tool.USAGE + "\n")


# --- the command, as the shell runs it ---------------------------------------------------


def run_main(args: list[str], env: dict[str, str], stdin: str = "") -> tuple[int, str]:
    out: list[str] = []
    code = tool.main(args, env, lambda: stdin, out.append)
    return code, "".join(out)


def test_the_command_without_a_state_directory_builds_in_the_default_one(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tool, "DEFAULT_STATE", str(state))

    code, output = run_main(["show"], {})

    assert (code, output) == (0, "")


def test_a_state_directory_that_is_not_there_is_the_tool_failing(tmp_path: Path) -> None:
    code, output = run_main(["show"], {tool.STATE_ENV: str(tmp_path / "nowhere")})

    assert code == tool.BROKEN
    assert not (tmp_path / "nowhere").exists()
    assert output.startswith("define: ERROR, the state cannot be used (")


def sealed_in(state: Path, output: Path | None = None) -> tool.Outcome:
    put(state, dbv.ready())
    return tool.run(state, ["done", "ready"], lambda: "", output)


def test_done_ready_writes_the_rendered_definition_where_the_context_says(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    tool.start(state, tool.Context(dbv.BODY, (), "(first round)", (), str(tmp_path / "app"), 1))

    sealed = sealed_in(state)

    assert sealed.code == 0, sealed.output
    written = (tmp_path / "app" / ".raidar" / "feature-definition.md").read_text(encoding="utf-8")
    context = tool.Context.read(json.loads((state / tool.CONTEXT_FILE).read_text("utf-8")))
    assert written == tool.definition_text(drafted(state), context)


def test_done_ready_writes_to_the_output_it_is_given(tmp_path: Path) -> None:
    state = tmp_path / "state"
    tool.start(state, tool.Context(dbv.BODY, (), "(first round)", (), str(tmp_path / "app"), 1))
    elsewhere = tmp_path / "elsewhere" / "definition.md"

    sealed_in(state, elsewhere)

    assert elsewhere.is_file()
    assert not (tmp_path / "app").exists()


def test_a_refused_done_writes_no_definition(tmp_path: Path) -> None:
    state = tmp_path / "state"
    tool.start(state, tool.Context(dbv.BODY, (), "(first round)", (), str(tmp_path / "app"), 1))
    put(state, dbv.ready(dbv.EMPTY))

    refused = call(state, "done", "ready")

    assert refused.code == tool.FAULT
    assert not (tmp_path / "app").exists()


def test_every_call_is_traced_in_the_events_beside_the_call_log(state: Path) -> None:
    put(state, dbv.ready())
    call(state, "show")
    call(state, "frob")
    call(state, "done", "ready")

    calls = tool.read_calls(state)
    events = [
        json.loads(line)
        for line in (state / tool.EVENTS_FILE).read_text(encoding="utf-8").splitlines()
    ]

    assert [event["command"] for event in events] == [c.command for c in calls]
    assert [event["argv"] for event in events] == [["put"], ["show"], ["frob"], ["done", "ready"]]
    assert events[0]["input"] == dbv.block_of(dbv.ready())
    assert events[1]["input"] is None
    final = (state / tool.STATE_FILE).read_bytes()
    assert events[-1]["state_sha256"] == hashlib.sha256(final).hexdigest()
    assert events[-1]["sealed"] == "ready"
    assert events[0]["sealed"] == ""
    assert all(
        event["context_sha256"]
        == hashlib.sha256((state / tool.CONTEXT_FILE).read_bytes()).hexdigest()
        for event in events
    )


def test_the_next_round_archives_the_draft_and_keeps_the_logs(state: Path, tmp_path: Path) -> None:
    put(state, dbv.asks())
    call(state, "done", "ask")
    first = (state / tool.STATE_FILE).read_text(encoding="utf-8")

    kept = tool.next_round(
        state, tool.Context(dbv.BODY, ("accepted",), "(second round)", ("Q1.1",), str(tmp_path), 1)
    )

    assert kept == 1
    assert (state / tool.ROUNDS_DIR / "1" / tool.STATE_FILE).read_text(encoding="utf-8") == first
    assert not (state / tool.STATE_FILE).exists()
    assert len(tool.read_calls(state)) == 2
    assert tool.read_draft(state) is None
    assert tool.Context.read(json.loads((state / tool.CONTEXT_FILE).read_text("utf-8"))).open == (
        "Q1.1",
    )
    assert tool.next_round(state, tool.Context(dbv.BODY, (), "", (), "", 1)) == 2


def test_the_command_takes_its_arguments_and_input_from_the_process(
    state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["define", "put"])
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"title: From stdin \xff\xfe\n")))
    monkeypatch.setenv(tool.STATE_ENV, str(state))

    with pytest.raises(SystemExit) as stopped:
        runpy.run_module("define_tool", run_name="__main__")

    assert stopped.value.code == tool.FAULT

    assert capsys.readouterr().out.startswith("put: applied 1 item (1 title)")
    held = drafted(state).kind(block.TITLE)[0].data["text"]
    assert isinstance(held, str)
    assert held.startswith("From stdin ")


# --- the draft ---------------------------------------------------------------------------


def test_a_draft_orders_its_items_as_a_reader_wants_them() -> None:
    draft = Draft()
    items = dbv.items(dbv.ready())
    draft.put(list(reversed(items)))

    refs = [item.ref for item in draft.ordered()]
    assert refs[:4] == ["title", "objective", "named f1 greeting", "F1"]
    assert sorted(refs[4:9]) == sorted(dbv.AC)
    assert refs[9:] == ["decision 1"]


def test_a_draft_and_its_record_read_back_the_same() -> None:
    draft = Draft()
    draft.put(dbv.items(dbv.ready()))
    draft.sealed = "ready"

    again = Draft.read(json.loads(json.dumps(draft.record())))

    assert again == draft


# --- identity, drop and the summary ---------------------------------------------------------

QUESTION = "question F1: {}\nkind: {}\nproposal: p\noption: o\n"


def test_rewording_a_question_replaces_it_and_another_kind_is_another_question(
    draft: Draft,
) -> None:
    for words in ("What greeting?", "What greeting text?"):
        call(draft, "put", stdin=QUESTION.format(words, "missing_input"))

    asked = drafted(draft).kind(block.QUESTION)
    assert [item.data["question"] for item in asked] == ["What greeting text?"]

    call(draft, "put", stdin=QUESTION.format("Why?", "objective_unclear"))

    assert len(drafted(draft).kind(block.QUESTION)) == 2


@pytest.mark.parametrize(
    "reference",
    [
        "retire tests/x.py::t",
        "retire: tests/x.py::t",
        "named Greeting",
        "Named:   greeting",
        "decision 3",
        "decision 03",
        "decision: 3",
        "  DECISION   3 ",
        "question F1 missing_input",
    ],
)
def test_drop_names_an_item_as_show_writes_it(draft: Draft, reference: str) -> None:
    put_all = (
        "named: Greeting\nfrom: none\nretire: tests/x.py::t\nsource: U1\nreason: r\n"
        f"decision 3: d\nrung: 2\n{QUESTION.format('Why?', 'missing_input')}"
    )
    call(draft, "put", stdin=put_all)
    held = len(drafted(draft).items)

    outcome = call(draft, "drop", *reference.split(" "))

    assert outcome.output.startswith(f"drop: removed `{reference}`")
    assert len(drafted(draft).items) == held - 1


def test_a_drop_that_names_nothing_lists_what_the_draft_holds(draft: Draft) -> None:
    empty = call(draft, "drop", "F1")
    put(draft, dbv.ready())
    held = call(draft, "drop", "F1.AC9")

    assert empty.output == "drop: nothing is named `F1`; the draft holds nothing yet\n"
    assert held.output.startswith("drop: nothing is named `F1.AC9`; the draft holds `")
    assert held.output.endswith("`decision 1`\n")


@pytest.mark.parametrize("number", ["²", "٣", "1" * 5000, "-1"])
def test_a_decision_number_that_is_not_ascii_digits_is_a_finding_not_a_crash(
    draft: Draft, number: str
) -> None:
    outcome = call(draft, "put", stdin=f"decision {number}: text\nrung: 1\n")

    assert outcome.code == tool.FAULT
    assert "is not a number: write `decision 1: <text>`" in outcome.output
    assert drafted(draft).items == []
    assert outcome.call.command == "put"


# --- a tool that cannot work, and calls that overlap -----------------------------------------


def test_a_state_that_cannot_be_written_is_the_tool_failing_and_is_marked(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(directory: Path, draft: Draft) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(tool, "_write_draft", full)

    outcome = call(state, "put", stdin="title: t\n")

    assert outcome.code == tool.BROKEN
    assert outcome.output == "define: ERROR, the state cannot be used (disk full)\n"
    assert [item.command for item in tool.read_calls(state)] == [tool.ERROR_CALL]
    assert tool.read_draft(state) is None


# --- what a finding says --------------------------------------------------------------------

# Words of the rendered file and of the reply object: the model writes neither.
FILE_WORDS = (
    "## ",
    "### ",
    "`Outcome:",
    "`Aspect:",
    "`Example:",
    "`Kind:",
    "`Source:",
    "`Verification:",
    "Acceptance criteria:",
    "`sources`",
    "`evidence`",
    "behaviour_from",
    "`replies`",
    "status `needs_input`",
    "a `ready` reply",
)
FAULTY = [
    ("", ["check", "done ready", "done ask"]),
    ("feature F1: f\n", ["check", "done ready"]),
    (
        "title: t\nobjective: o\nfeature F1: f\noutcome: x\nac F1.AC1: c\nac F1.AC2: d\n"
        "source: U99\naspect: no arrow\nexample: no arrow\nkind: smoke\nac F1.AC3: e\n"
        "kind: regression\naspect: a -> b\nac F1.AC4: g\nexample: i -> o\n"
        "retire: not a test id\nreason: r\ndecision 1: d\nrung: 3\nsource: U98\n",
        ["check", "done ready"],
    ),
    ("named: Farewell\nfrom: none\nnamed: Wave\nfrom: issue U9\n", ["check"]),
    (
        "named: Farewell\nfrom: none\nquestion F1: a\nkind: missing_input\n"
        "question F1: b\nkind: feature_conflict\nquestion F1: c\nkind: objective_unclear\n"
        "question F2: d\nkind: missing_input\nproposal: p\noption: o\n",
        ["check", "done ask", "done ready"],
    ),
    (
        "reply Q1.1: unclear\nreply Q9.9: accepted\nfeature F1: f\nac F1.AC1: c\n",
        ["check", "done ready"],
    ),
]


def test_findings_name_the_lines_of_the_block_not_the_file_it_is_rendered_to() -> None:
    said = []
    for text, commands in FAULTY:
        draft = Draft()
        context = tool.Context(dbv.BODY, (), "", ("Q1.1", "Q1.2"), "", 1)
        said.append(tool._command(draft, context, ["put"], lambda source=text: source).output)
        said += [
            tool._command(draft, context, command.split(), lambda: "").output
            for command in commands
        ]

    findings = [line for output in said for line in output.splitlines() if line[:2] == "- "]
    assert len(findings) > 40
    assert [(line, word) for line in findings for word in FILE_WORDS if word in line] == []
