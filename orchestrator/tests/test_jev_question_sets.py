"""H3: SCB rubric per changed symbol with carry-forward, and the process residue interface."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.jev.audit import DecisionStage, SufficiencyLevel
from raidar.jev.carry_forward import is_span_unchanged, parse_diff_hunks, unified_diff_text
from raidar.jev.codebase import CodebaseIndex
from raidar.jev.process import (
    EVENTS_BUDGET_CHARS,
    EventKind,
    ProcessCheck,
    ResidueCase,
    TraceEvent,
    ask_residue,
    record_settled,
    residue_state,
)
from raidar.jev.report import rubric_shadow_report
from raidar.jev.rubric import (
    RUBRIC_POLICY,
    CheckpointSource,
    JudgementStatus,
    RubricRunner,
)
from raidar.jev.rubric_questions import (
    SCB_RUBRIC_FILE,
    QuestionSetError,
    Requirement,
    load_rubric_question_set,
)
from raidar.jev.symbols import MODULE_QUALNAME, SymbolKind, SymbolParseError, extract_symbols
from tests.jev_support import RecordedTransport, make_runtime, noul_answer, policy

PROCESS = "process-compliance.residue"


def test_rubric_question_set_is_the_pinned_scb_criteria_as_noul_questions(tmp_path: Path) -> None:
    question_set = load_rubric_question_set()
    names = [criterion.name for criterion in question_set.criteria]
    assert (question_set.ref, len(names), len(set(names))) == ("scb-rubric@2", 45, 45)
    stub = next(item for item in question_set.criteria if item.name == "stub_implementations")
    question = stub.question()
    assert question.type == "noul"
    assert stub.description in question.instructions
    assert question.criteria is not None
    assert stub.positives[0] in (question.criteria.true or "")
    by_requirement = {
        req: [c.name for c in question_set.criteria if c.requires is req] for req in Requirement
    }
    assert "unrequested_features" in by_requirement[Requirement.SPEC]
    assert "lonely_interface" in by_requirement[Requirement.CODEBASE]

    copied = tmp_path / "sets"
    shutil.copytree(SCB_RUBRIC_FILE.parent, copied)
    criteria_file = copied / "scb_rubric_llm_judge.jsonl"
    criteria_file.write_text(criteria_file.read_text() + "\n")
    with pytest.raises(QuestionSetError, match="does not match the pinned"):
        load_rubric_question_set(copied / SCB_RUBRIC_FILE.name)


def _golden_diff() -> str:
    prior = "".join(f"line{i}\n" for i in range(1, 41))
    lines = [f"line{i}\n" for i in range(1, 41)]
    lines[9] = "changed10\n"
    lines[24:24] = ["ins_a\n", "ins_b\n"]
    del lines[35]
    return unified_diff_text(prior, "".join(lines), "f.py")


# Values produced by the SCB fork's metrics/rubric/carry_forward.py on the same diffs.
SCB_GOLDEN = [
    ((1, 5), (True, 1, 5)),
    ((1, 6), (True, 1, 6)),
    ((7, 7), (False, 7, 7)),
    ((10, 12), (False, 10, 12)),
    ((14, 19), (True, 14, 19)),
    ((18, 21), (True, 18, 21)),
    ((26, 30), (False, 26, 32)),
    ((29, 30), (True, 31, 32)),
    ((29, 31), (False, 31, 33)),
    ((33, 34), (False, 35, 36)),
    ((38, 40), (True, 39, 41)),
    ((20, None), (True, 20, None)),
]
SCB_ZERO_CONTEXT_GOLDEN = [
    ((1, 5), (True, 1, 5)),
    ((5, 6), (False, 5, 8)),
    ((4, 5), (True, 4, 5)),
    ((6, 8), (True, 8, 10)),
]


def test_carry_forward_span_rules_match_the_scb_fork() -> None:
    hunks = parse_diff_hunks(_golden_diff())
    assert [(h.old_start, h.old_count, h.new_start, h.new_count) for h in hunks] == [
        (7, 7, 7, 7),
        (22, 6, 22, 8),
        (31, 7, 33, 6),
    ]
    for span, expected in SCB_GOLDEN:
        assert is_span_unchanged(hunks, *span) == expected, span
    zero = parse_diff_hunks("@@ -5,0 +6,2 @@\n+a\n+b")
    for span, expected in SCB_ZERO_CONTEXT_GOLDEN:
        assert is_span_unchanged(zero, *span) == expected, span


def test_symbols_cover_classes_methods_functions_and_module_statements() -> None:
    source = (
        '"""Doc."""\nimport os\n\nREGISTRY = {"a": 1}\n\n\n'
        "@decorator\ndef top():\n    return 1\n\n\n"
        "class Box:\n    size = 1\n\n    def grow(self):\n        def inner():\n"
        "            return 2\n        return inner()\n"
    )
    symbols = {symbol.qualname: symbol for symbol in extract_symbols("app.py", source)}
    assert set(symbols) == {MODULE_QUALNAME, "top", "Box", "Box.grow"}
    assert (symbols["top"].kind, symbols["top"].start, symbols["top"].end) == (
        SymbolKind.FUNCTION,
        7,
        9,
    )
    assert symbols["Box.grow"].kind is SymbolKind.METHOD
    assert symbols[MODULE_QUALNAME].source == 'REGISTRY = {"a": 1}\n'
    with pytest.raises(SymbolParseError):
        extract_symbols("bad.py", "def (:\n")


APP_V1 = "def stable():\n    return 1\n\n\n\n\n\n\ndef edited():\n    return 2\n"
APP_V2 = (
    "def stable():\n    return 1\n\n\n\n\n\n\n"
    "def edited():\n    return 3\n\n\ndef added():\n    pass\n"
)


def _trajectory(root: Path) -> list[CheckpointSource]:
    for name, text, extra in (("checkpoint_1", APP_V1, None), ("checkpoint_2", APP_V2, "x.js")):
        snapshot = root / name
        snapshot.mkdir(parents=True)
        (snapshot / "app.py").write_text(text)
        (snapshot / ".gitignore").write_text("app.py\n")
        if extra:
            (snapshot / extra).write_text("export const x = 1;\n")
    return [
        CheckpointSource("checkpoint_1", root / "checkpoint_1", spec="Build app."),
        CheckpointSource("checkpoint_2", root / "checkpoint_2", spec="Build app.\nAdd more."),
    ]


def test_rubric_asks_changed_symbols_carries_unchanged_ones_and_replays_for_free(
    tmp_path: Path,
) -> None:
    sources = _trajectory(tmp_path / "snapshots")
    transport = RecordedTransport(
        answer_for=lambda qid, _q: noul_answer(0.5 if qid == "stub_implementations" else 0.1)
    )
    runtime = make_runtime(
        tmp_path / "store", transport=transport, policies=(policy(RUBRIC_POLICY),)
    )
    question_set = load_rubric_question_set()
    first, second = RubricRunner(runtime, question_set).run(sources)

    def by_symbol(checkpoint, qualname):
        return [item for item in checkpoint.judgements if item.symbol.qualname == qualname]

    assert first.python_files == 1  # .gitignore does not hide app.py from the assessor
    # Every requirement, codebase scope included, has its input: all are asked.
    assert {item.status for item in first.judgements} == {JudgementStatus.ANSWERED}
    stable = by_symbol(second, "stable")
    carried = [item for item in stable if item.status is JudgementStatus.CARRIED]
    # Only changed symbols are asked (memo §3.5): an unchanged span carries every
    # settled request, although the file and the cumulative spec both changed.
    assert {item.requires for item in carried} == set(Requirement)
    assert JudgementStatus.ANSWERED not in {item.status for item in stable}
    assert all(item.carried_from == "checkpoint_1" for item in carried)
    assert all(not item.span_unchanged for item in by_symbol(second, "edited"))
    assert all(item.status is not JudgementStatus.CARRIED for item in by_symbol(second, "added"))
    assert second.unsupported_files == ("x.js",)

    # The audit alone rebuilds each carry: its answers and its origin decision.
    audited = {record.request_id: record for record in runtime.audit.decisions(RUBRIC_POLICY)}
    carry_records = [r for r in audited.values() if r.stage is DecisionStage.DETERMINISTIC]
    assert len(carry_records) == len(carried)
    for item in carried:
        record = audited[item.request_id]
        origin = audited[record.carried_from or ""]
        assert record.rule == "carry_forward:checkpoint_1"
        assert (origin.stage, origin.answers) == (DecisionStage.JEV, record.answers)
        assert {name: answer["noul"] for name, answer in (record.answers or {}).items()} == dict(
            item.answers or {}
        )
    asked_questions = [q for call in transport.calls for q in call["body"]["questions"]]
    codebase = {c.name for c in question_set.criteria if c.requires is Requirement.CODEBASE}
    assert "implicit_default_parameters" in codebase & set(asked_questions)

    # ΣP is a number only for fully settled criteria, never a partial sum.
    counts = first.expected_counts()
    assert counts["stub_implementations"] == pytest.approx(0.5 * 2)
    assert None not in counts.values()
    assert first.complete is True
    assert set(second.expected_counts().values()) == {None}  # x.js is unjudged code

    replay = make_runtime(tmp_path / "store", replay_only=True, policies=(policy(RUBRIC_POLICY),))
    again = RubricRunner(replay, question_set).run(sources)
    assert (replay.totals.provider_calls, replay.totals.cost_usd) == (0, 0.0)
    assert [c.expected_counts() for c in again] == [
        first.expected_counts(),
        second.expected_counts(),
    ]


def test_shadow_report_gives_coverage_and_input_sufficiency_with_denominators(
    tmp_path: Path,
) -> None:
    sources = _trajectory(tmp_path / "snapshots")
    (sources[1].root / "broken.py").write_text("def (:\n")
    runtime = make_runtime(tmp_path / "store", policies=(policy(RUBRIC_POLICY),))
    results = RubricRunner(runtime, load_rubric_question_set()).run(sources)
    report = rubric_shadow_report(results, runtime, policy_id=RUBRIC_POLICY)
    assert (report["mode"], report["counts_toward_verdicts"], report["live_client"]) == (
        "shadow",
        False,
        False,
    )
    totals = report["totals"]
    assert totals["question_coverage"]["value"] == 0.0
    assert totals["failure_reasons"] == {"missing_key": totals["requests"]["error"]}
    sufficiency = totals["sufficiency"]
    assert sufficiency["missing"] == totals["requests"].get("insufficient", 0) == 0
    assert (
        totals["input_sufficiency"]["denominator"] == totals["asked"] == sum(sufficiency.values())
    )
    assert totals["decisions_audited"] == len(runtime.audit.decisions(RUBRIC_POLICY))
    checkpoint_2 = report["checkpoints"][1]
    assert (checkpoint_2["symbols"], checkpoint_2["symbols_changed"]) == (3, 2)
    assert checkpoint_2["requests_carry_eligible"] > 0
    assert set(checkpoint_2["expected_counts"].values()) == {None}
    assert checkpoint_2["measured_criteria"]["numerator"] == 0
    assert checkpoint_2["python_files"] == 2
    assert [error.split(":")[0] for error in checkpoint_2["parse_errors"]] == ["broken.py"]


def test_shadow_rubric_cli_writes_the_report_for_a_trial_layout(tmp_path: Path) -> None:
    for source in _trajectory(tmp_path / "flat"):
        shutil.copytree(source.root, tmp_path / "steps" / source.name / "artifacts" / "app")
    specs = tmp_path / "specs" / "checkpoint_1"
    specs.mkdir(parents=True)
    (specs / "instruction.md").write_text("Build app.")
    output = tmp_path / "report.json"
    result = CliRunner().invoke(
        main,
        [
            "jev",
            "shadow-rubric",
            "--snapshots",
            str(tmp_path / "steps"),
            "--within",
            "artifacts/app",
            "--trial",
            "luna-1",
            "--specs",
            str(tmp_path / "specs"),
            "--store",
            str(tmp_path / "store"),
            "--replay-only",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(output.read_text())
    assert [c["checkpoint"] for c in report["checkpoints"]] == ["checkpoint_1", "checkpoint_2"]
    assert [c["python_files"] for c in report["checkpoints"]] == [1, 1]
    assert report["totals"]["failure_reasons"].keys() == {"cache_miss"}
    audit = make_runtime(tmp_path / "store").audit
    assert {record.trial for record in audit.decisions(RUBRIC_POLICY)} == {"luna-1"}


def _events(count: int, size: int) -> tuple[TraceEvent, ...]:
    return tuple(
        TraceEvent(index=i, kind=EventKind.COMMAND, text=f"{i}:" + "x" * size) for i in range(count)
    )


def test_process_residue_state_sufficiency_and_audit(tmp_path: Path) -> None:
    transport = RecordedTransport(answer_for=lambda _id, _q: noul_answer(0.3))
    runtime = make_runtime(tmp_path, transport=transport, policies=(policy(PROCESS),))
    case = ResidueCase(
        step="checkpoint_2",
        check=ProcessCheck.TESTS_RUN_BEFORE_FINISH,
        events=_events(3, 10),
        ambiguity="test command is a custom script",
    )
    outcome = ask_residue(runtime, case)
    assert outcome.noul("tests_run_before_finish") == pytest.approx(0.3)
    assert outcome.counts_toward_verdicts is False
    assert outcome.record.input_sufficiency is not None
    assert outcome.record.input_sufficiency.level is SufficiencyLevel.SUFFICIENT
    assert list(transport.calls[0]["body"]["questions"]) == ["tests_run_before_finish"]

    long_state, long_sufficiency = residue_state(
        ResidueCase("checkpoint_2", ProcessCheck.PLAN_BEFORE_EDIT, _events(60, 1_900), "long")
    )
    kept = [event for event in long_state["events"] if "index" in event]
    assert (kept[0]["index"], kept[-1]["index"]) == (0, 59)
    assert sum(len(event["text"]) for event in kept) <= EVENTS_BUDGET_CHARS
    assert long_sufficiency.level is SufficiencyLevel.PARTIAL

    empty = ask_residue(
        runtime, ResidueCase("checkpoint_3", ProcessCheck.TESTS_FIRST, (), "no trace")
    )
    assert (empty.ok, empty.record.stage) == (False, DecisionStage.SKIPPED)
    settled = record_settled(
        runtime,
        step="checkpoint_1",
        check=ProcessCheck.PLAN_BEFORE_EDIT,
        rule="plan_file",
        complied=True,
    )
    assert (settled.stage, settled.verdict) == (DecisionStage.DETERMINISTIC, "complied")
    assert len(runtime.audit.decisions(PROCESS)) == 3
    assert len(transport.calls) == 1


CODEBASE_APP = """from abc import ABC, abstractmethod
from functools import cached_property


class Store(ABC):
    @abstractmethod
    def save(self, item): ...


class FileStore(Store):
    def save(self, item):
        return item

    @cached_property
    def path(self):
        return "/tmp/x"


def render(item, sep=", "):
    return sep.join(item)


def main():
    store = FileStore()
    store.save(render(["a"]))
    return store.path, render(["b"])
"""


def test_codebase_scope_criteria_get_a_bounded_whole_codebase_state(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshots" / "checkpoint_1"
    snapshot.mkdir(parents=True)
    (snapshot / "app.py").write_text(CODEBASE_APP)
    transport = RecordedTransport()
    runtime = make_runtime(
        tmp_path / "store", transport=transport, policies=(policy(RUBRIC_POLICY),)
    )
    question_set = load_rubric_question_set()
    source = CheckpointSource("checkpoint_1", snapshot, spec="Store items.")
    (result,) = RubricRunner(runtime, question_set).run([source])

    codebase = [item for item in result.judgements if item.requires is Requirement.CODEBASE]
    assert {item.status for item in codebase} == {JudgementStatus.ANSWERED}
    assert {item.sufficiency for item in codebase} == {SufficiencyLevel.SUFFICIENT}
    states = {
        call["body"]["state"]["symbol"]["qualname"]: call["body"]["state"]["codebase"]
        for call in transport.calls
        if "codebase" in call["body"]["state"]
    }
    store_focus = states["Store"]["focus"]
    assert "class Store(ABC) [abstract]" in store_focus
    assert "direct subclasses: app.py::FileStore" in store_focus
    assert "calls of .save: 1 site(s)" in store_focus
    assert "accesses of .path: 1 site(s)" in states["FileStore"]["focus"]
    render_focus = states["render"]["focus"]
    assert "def render(item, sep=', ')" in render_focus
    assert "calls of render: 2 site(s)" in render_focus
    assert "app.py:26 in main: render(['b'])" in render_focus
    assert "class FileStore(Store)" in states["render"]["inventory"]
    # Every criterion is measured: none waits on a missing codebase input.
    assert None not in result.expected_counts().values()
    report = rubric_shadow_report([result], runtime, policy_id=RUBRIC_POLICY)
    assert report["totals"]["input_sufficiency"]["value"] == 1.0

    # A codebase too large for the budget is clipped (partial) and stays under the cap.
    label = "x" * 400
    big = "\n".join(
        f"def helper_{i}(value, scale=1, label='{label}'):\n    return render([value])\n"
        for i in range(120)
    )
    (snapshot / "big.py").write_text("from app import render\n\n" + big)
    capped = make_runtime(
        tmp_path / "s2", transport=RecordedTransport(), policies=(policy(RUBRIC_POLICY),)
    )
    (large,) = RubricRunner(capped, question_set).run([source])
    render_requests = [
        item
        for item in large.judgements
        if item.requires is Requirement.CODEBASE and item.symbol.qualname == "render"
    ]
    assert [item.sufficiency for item in render_requests] == [SufficiencyLevel.PARTIAL]
    assert render_requests[0].status is JudgementStatus.ANSWERED
    assert capped.totals.failures == {}


def test_codebase_focus_for_module_statements_lists_uses_of_module_names() -> None:
    files = {
        "cfg.py": "NAMES = ['a', 'b']\nSIZES = [1, 2]\n",
        "use.py": "from cfg import NAMES, SIZES\n\n\ndef pair():\n    return zip(NAMES, SIZES)\n",
        "bad.py": "def (:\n",
    }
    index = CodebaseIndex(files)
    (module,) = [
        s for s in extract_symbols("cfg.py", files["cfg.py"]) if s.kind is SymbolKind.MODULE
    ]
    focus = index.focus(module)
    assert "uses of NAMES: 1 site(s)" in focus
    assert "use.py:5 in pair: NAMES" in focus
    assert "bad.py" not in index.inventory()  # unparsable files are reported by the rubric
    sections = index.state(module, budget=2000).sections
    assert set(sections) == {"focus", "inventory"}
