"""The engine's judgement of the acceptance criteria (define-build-verify's ``[judge]``).

Pure host-side functions, ported from factory-x-loops at ``ac5db0f`` and kept to what the
aspect judge of ``define-build-verify/v2`` reads: ``capability/judge/questions.py`` (the question,
wording ``dbv5-aspect/1``), ``capability/judge/context.py`` (the state each aspect is asked on),
``capability/verify/collected.py`` (which tests a gate run collected),
``engine/workflow/criteria.py`` (statuses, stop rules and the pushback). Nothing here touches the
sandbox, the network or the
clock: ``raidar.subject.workflow.package_judge`` asks, this module decides.

The judge is asked one question per ``Aspect:`` of a criterion: does a mapped test set up this
situation and assert this outcome. The state it sees is the criterion's id and text, the aspect
and the mapped tests' source with one hop of same-module helpers and their gate results: no
production code. A criterion is ``open`` when a mapped test did not pass the gate, it maps no
test or has no aspect, an ``Example:`` value appears in no mapped test, or any aspect is answered
at most ``open_at``; ``closed`` when every mapped test passed and every aspect is answered at
least ``closed_at``; otherwise ``undecided`` (a missing answer included), which counts as open
when the loop stops. A ``Kind: regression`` criterion is never asked: it closes when the gate
accepted the work and, with ``regression_needs_baseline``, a mapped test passed on the base.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from raidar.judge.protocol import NoulCriteria, NoulQuestion
from raidar.subject.primitive.judge import JudgeConfig
from raidar.subject.workflow import pysource
from raidar.subject.workflow.acceptance import REGRESSION, Criterion, Feature, appears

ASPECT_ASSERTED = "aspect_asserted"
INSTRUCTIONS = (
    "Does a test in `tests` set up the situation in `aspect.situation` and contain "
    "an assertion that checks the outcome in `aspect.outcome`?"
)
TRUE = (
    "some test in `tests` sets up this situation (in its own body or through a "
    "helper it calls) and has an assertion that checks this outcome, its presence "
    "or its value. An assertion is an assert statement, a unittest assert method, "
    "or an expected-exception check (pytest.raises, assertRaises). An "
    "exact-equality assertion on a whole result (a complete list, dict, body or "
    "row set) checks every outcome that value fixes"
)
FALSE = (
    "no test sets up this situation, or no assertion checks this outcome. An "
    "assertion on a different outcome of the same situation does not count: only "
    "the exit code, only a status, only another field of the same response or "
    "record, or only that something was returned"
)

CLOSED = "closed"
OPEN = "open"
UNDECIDED = "undecided"
NOT_ASKED = "not_asked"
GATE = "gate"
BASELINE = "baseline"
UNMAPPED = "unmapped"
NO_ASPECTS = "no_aspects"
PRECHECK = "precheck"
UNSETTLED = "undecided"
PRECHECK_REASONS = frozenset({UNMAPPED, NO_ASPECTS, PRECHECK})
"""Why a criterion may be open with no aspect asked (its record's ``failing``)."""

PASSED = "passed"
FAILED = "failed"
SKIPPED = "skipped"
NOT_COLLECTED = "not_collected"
NOT_RUN = "not_run"
UNKNOWN = "unknown"
DISQUALIFYING = frozenset({FAILED, SKIPPED, NOT_COLLECTED, NOT_RUN})
RESULT_TEXT = {
    FAILED: "failed at the gate",
    SKIPPED: "was skipped (or xfailed) at the gate, so it asserted nothing",
    NOT_COLLECTED: "was not collected by the gate at this commit (a mapping fault)",
    NOT_RUN: "was collected but did not run at the gate",
}
EXAMPLE_ABSENT = "the Example value `{literal}` appears in no mapped test"
CHARS_PER_TOKEN = 3.0
ANSWER_TOKENS = 1024
SHORTEST_CUT = 200
STALL_PASSES = 2

VERIFIED = "verified"
PASS_LIMIT = "pass_limit"
NO_PROGRESS = "no_progress"

MISSING = {
    UNMAPPED: (
        "no test is mapped to this criterion: write one and list it for the criterion in `ac_tests`"
    ),
    NO_ASPECTS: (
        "the feature definition gives this criterion no `Aspect:` line, so the "
        "engine has no situation and outcome to check"
    ),
    GATE: (
        "the verify gate did not accept the work, so the earlier tests this "
        "criterion rests on are not shown to pass: fix what the gate reports"
    ),
    BASELINE: (
        "this criterion says earlier behaviour still works, but no test mapped "
        "to it passed before the change: list for it in `ac_tests` the "
        "repository's earlier tests that cover that behaviour (ones that passed "
        "before the change)"
    ),
}
ASPECT_MISSING = "no test sets up {situation} and asserts {outcome}"


def questions() -> dict[str, NoulQuestion]:
    """The one question every aspect is asked (wording ``QUESTION_SET``), in Jev's format."""
    return {
        ASPECT_ASSERTED: NoulQuestion(
            instructions=INSTRUCTIONS, criteria=NoulCriteria(true=TRUE, false=FALSE)
        )
    }


# Tests the gate collected ----------------------------------------------------------------


def normalise_test_id(identifier: str) -> str:
    """A test id as pytest writes a node id: ``/`` paths, no leading ``./``."""
    return identifier.strip().replace("\\", "/").removeprefix("./")


def is_collected(identifier: str, collected: Collection[str]) -> bool:
    """Whether ``identifier`` names a collected test (or a parametrised one's base)."""
    wanted = normalise_test_id(identifier)
    if not wanted or wanted in collected:
        return bool(wanted)
    return any(found.startswith(f"{wanted}[") for found in collected)


def _outcome(test: str, outcomes: Mapping[str, object]) -> str | None:
    """A test's recorded outcome (a parametrised one's worst); None if absent."""
    wanted = normalise_test_id(test)
    said = {
        str(found)
        for ident, found in outcomes.items()
        if ident == wanted or ident.startswith(f"{wanted}[")
    }
    for result in (FAILED, SKIPPED, PASSED):
        if result in said:
            return result
    return None


def gate_results(tests: Sequence[str], record: Mapping[str, Any] | None) -> dict[str, str]:
    """Each mapped test's result at the gate run whose tests check wrote ``record``.

    ``record`` is ``{"collected": [ids], "outcomes": {id: passed|failed|skipped}}``; without
    one (the run recorded none) every result is ``unknown``.
    """
    collected = (record or {}).get("collected")
    recorded = (record or {}).get("outcomes")
    if not isinstance(collected, list) or not isinstance(recorded, dict):
        return dict.fromkeys(tests, UNKNOWN)
    found: dict[str, str] = {}
    for test in tests:
        if not is_collected(test, collected):
            found[test] = NOT_COLLECTED
            continue
        result = _outcome(test, recorded)
        found[test] = NOT_RUN if result is None else result
    return found


def passed_tests(record: Mapping[str, Any] | None) -> frozenset[str]:
    """The ids of the tests that passed in ``record`` (the base's tests check)."""
    recorded = (record or {}).get("outcomes")
    if not isinstance(recorded, dict):
        return frozenset()
    return frozenset(ident for ident, found in recorded.items() if found == PASSED)


# The state an aspect is asked on ---------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """A deliberately high token estimate for ``text``."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


@dataclass
class MappedTest:
    """A mapped test's state entry and its one-hop helpers."""

    entry: dict[str, Any]
    file: str
    helpers: list[dict[str, Any]] = field(default_factory=list)


def _absent(path: str, text: str | None) -> str | None:
    """Say why a test module cannot be read, or None when it parses."""
    if text is None:
        return f"(`{path}` is not in the reviewed work)"
    try:
        pysource.imported_modules(text)
    except SyntaxError:
        return f"(`{path}` does not parse at this commit)"
    return None


def mapped_test(test: str, result: str, text: Callable[[str], str | None]) -> MappedTest:
    """A mapped test's entry: its source and helpers, or why there is none."""
    path, _ = pysource.split_node_id(normalise_test_id(test))
    found = MappedTest({"id": test, "gate_result": result}, path)
    module = text(path)
    absent = _absent(path, module)
    source = pysource.extract(module, test, True) if module is not None and absent is None else None
    if source is None:
        found.entry["source"] = absent or f"(no test of this name in `{path}`)"
        return found
    found.entry.update(
        file=path,
        lines=[source.source.start, source.source.end],
        source=source.source.numbered(),
    )
    if source.generated:
        found.entry["generated"] = True
    for helper in source.helpers:
        found.helpers.append(
            {"name": helper.name, "lines": [helper.start, helper.end], "source": helper.numbered()}
        )
    return found


def _test_text(test: MappedTest) -> str:
    shown = [str(test.entry.get("source", "")), *(str(h["source"]) for h in test.helpers)]
    return "\n".join(line for text in shown for line in pysource.numbered_lines(text).values())


def example_findings(criterion: Criterion, tests: Sequence[MappedTest]) -> tuple[str, ...]:
    """One line per ``Example:`` value that appears in no mapped test's source or helpers."""
    wanted = [item for example in criterion.examples for item in example.literals]
    text = "\n".join(_test_text(test) for test in tests if "file" in test.entry)
    if not wanted or not text:
        return ()
    missing = [literal for literal in dict.fromkeys(wanted) if not appears(literal, text)]
    return tuple(EXAMPLE_ABSENT.format(literal=literal) for literal in missing)


def aspect_state(
    criterion: Criterion, situation: str, outcome: str, tests: Sequence[Any]
) -> dict[str, Any]:
    """The state ``aspect_asserted`` is asked on."""
    return {
        "criterion": {"id": criterion.id, "text": criterion.text},
        "aspect": {"situation": situation, "outcome": outcome},
        "tests": list(tests),
    }


def _fit_tests(state: dict[str, Any], entries: list[dict[str, Any]], limit: int) -> bool:
    """Halve the longest test source until the tests alone fit; drop the last tests if need be."""
    cut = False
    while entries and estimate_tokens(json.dumps(state)) > limit:
        longest = max(entries, key=lambda entry: len(str(entry.get("source", ""))))
        source = str(longest.get("source", ""))
        if len(source) < SHORTEST_CUT:
            break
        kept = source[: len(source) // 2].rsplit("\n", 1)[0]
        longest["source"] = kept + "\n... (cut to fit)"
        cut = True
    while entries and estimate_tokens(json.dumps(state)) > limit:
        entries.pop()
        state["tests"] = list(entries)
        cut = True
    return cut or estimate_tokens(json.dumps(state)) > limit


def fit_tests(
    state: dict[str, Any], entries: list[dict[str, Any]], tests: Sequence[MappedTest], limit: int
) -> bool:
    """Cut the mapped tests in ``state`` to ``limit``: sources first, then helpers added back."""
    for entry in entries:
        entry["helpers"] = []
    cut = _fit_tests(state, entries, limit)
    for entry, test in zip(entries, tests[: len(entries)], strict=True):
        kept: list[dict[str, Any]] = []
        entry["helpers"] = kept
        for helper in test.helpers:
            kept.append(helper)
            if estimate_tokens(json.dumps(state)) > limit:
                kept.pop()
                cut = True
    return cut


@dataclass(frozen=True)
class Request:
    """One aspect's question: its id (``F1.AC1.A1``) and the state it is asked on."""

    aspect: str
    state: dict[str, Any]


def aspect_requests(
    criterion: Criterion, tests: Sequence[MappedTest], context_tokens: int
) -> tuple[tuple[Request, ...], bool]:
    """One request per askable aspect, and whether the tests were cut (once, on the widest)."""
    askable = [item for item in criterion.aspects if item.situation and item.outcome]
    if not askable:
        return (), False
    asked = json.dumps(
        {ASPECT_ASSERTED: questions()[ASPECT_ASSERTED].model_dump(exclude_none=True)}
    )
    limit = context_tokens - estimate_tokens(asked) - ANSWER_TOKENS
    entries = [dict(test.entry) for test in tests]
    widest = max(askable, key=lambda item: len(item.situation) + len(item.outcome))
    cut = fit_tests(
        aspect_state(criterion, widest.situation, widest.outcome, entries), entries, tests, limit
    )
    return (
        tuple(
            Request(item.id, aspect_state(criterion, item.situation, item.outcome, entries))
            for item in askable
        ),
        cut,
    )


@dataclass(frozen=True)
class Subject:
    """A registered criterion: with its feature and the tests the builder maps to it now."""

    criterion: Criterion
    feature: str
    feature_name: str
    tests: tuple[str, ...]


def subjects(features: Sequence[Feature], ac_tests: Mapping[str, Sequence[str]]) -> list[Subject]:
    """Every criterion in definition order, with its mapped tests."""
    return [
        Subject(item, feature.id, feature.name, tuple(ac_tests.get(item.id, ())))
        for feature in features
        for item in feature.traced
    ]


@dataclass(frozen=True)
class Context:
    """What one criterion is asked on, and why it may be open before any question."""

    results: Mapping[str, str]
    requests: tuple[Request, ...] = ()
    opened: tuple[str, ...] = ()
    cut: bool = False
    files: frozenset[str] = frozenset()

    @property
    def tests_ok(self) -> bool:
        return bool(self.results) and all(found == PASSED for found in self.results.values())


def assemble(
    subject: Subject,
    gate_tests: Mapping[str, Any] | None,
    text: Callable[[str], str | None],
    context_tokens: int,
) -> Context:
    """The criterion's context: results, the requests, and any reason to open it unasked."""
    results = gate_results(subject.tests, gate_tests)
    if not subject.tests:
        return Context(results)
    tests = [mapped_test(test, results[test], text) for test in subject.tests]
    failed = [
        f"`{test}` {RESULT_TEXT[found]}"
        for test, found in results.items()
        if found in DISQUALIFYING
    ]
    opened = (*failed, *example_findings(subject.criterion, tests))
    requests, cut = (
        ((), False) if opened else aspect_requests(subject.criterion, tests, context_tokens)
    )
    return Context(results, requests, opened, cut, frozenset(test.file for test in tests))


# Statuses --------------------------------------------------------------------------------


def aspect_status(p: float | None, settings: JudgeConfig, cut: bool) -> str:
    """One aspect's status from its probability (a low answer on cut tests opens nothing)."""
    if p is None:
        return UNDECIDED
    if p <= settings.open_at:
        return UNDECIDED if cut else OPEN
    return CLOSED if p >= settings.closed_at else UNDECIDED


def decide_aspects(
    probabilities: Mapping[str, float | None], settings: JudgeConfig, tests_ok: bool, cut: bool
) -> tuple[str, tuple[str, ...]]:
    """A criterion's status and the aspects keeping it from closing."""
    statuses = {ident: aspect_status(p, settings, cut) for ident, p in probabilities.items()}
    opened = _with_status(statuses, OPEN)
    if opened:
        return OPEN, opened
    if statuses and tests_ok and not _with_status(statuses, UNDECIDED):
        return CLOSED, ()
    waiting = tuple(ident for ident, status in statuses.items() if status != CLOSED)
    return UNDECIDED, waiting or (UNSETTLED,)


def _with_status(statuses: Mapping[str, str], wanted: str) -> tuple[str, ...]:
    return tuple(ident for ident, status in statuses.items() if status == wanted)


def decide_regression(
    subject: Subject, gate_passed: bool, baseline: Collection[str] | None
) -> tuple[str, tuple[str, ...]]:
    """A ``Kind: regression`` criterion: the gate's acceptance and, when asked, a base test."""
    if not gate_passed:
        return OPEN, (GATE,)
    if baseline is not None and not any(is_collected(test, baseline) for test in subject.tests):
        return OPEN, (BASELINE,)
    return CLOSED, ()


@dataclass(frozen=True)
class AspectResult:
    """One aspect's answer for a pass; ``calls`` index the pass's judge calls."""

    id: str
    situation: str
    outcome: str
    text: str
    p: float | None
    status: str
    calls: tuple[int, ...] = ()

    def record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "situation": self.situation,
            "outcome": self.outcome,
            "probability": self.p,
            "status": self.status,
            "calls": list(self.calls),
        }


@dataclass(frozen=True)
class Outcome:
    """One criterion's result for a pass."""

    subject: Subject
    status: str
    failing: tuple[str, ...]
    context: Context
    aspects: tuple[AspectResult, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return self.subject.criterion.id

    def record(self) -> dict[str, Any]:
        criterion = self.subject.criterion
        return {
            "id": criterion.id,
            "feature": self.subject.feature,
            "text": criterion.text,
            "kind": criterion.kind or "behaviour",
            "status": self.status,
            "failing": list(self.failing),
            "tests": list(self.subject.tests),
            "test_results": dict(self.context.results),
            "notes": list(self.notes),
            "aspects": [item.record() for item in self.aspects],
        }


@dataclass(frozen=True)
class PassResult:
    """Every criterion's outcome for one pass, in definition order."""

    number: int
    outcomes: tuple[Outcome, ...]

    @property
    def standing(self) -> bool:
        return any(item.status != CLOSED for item in self.outcomes)

    @property
    def closed(self) -> frozenset[str]:
        return frozenset(item.id for item in self.outcomes if item.status == CLOSED)

    @property
    def unclosed(self) -> tuple[Outcome, ...]:
        return tuple(item for item in self.outcomes if item.status != CLOSED)

    @property
    def signature(self) -> frozenset[tuple[str, tuple[str, ...]]]:
        return frozenset((item.id, item.failing) for item in self.unclosed)

    @property
    def files(self) -> frozenset[str]:
        return frozenset(path for item in self.unclosed for path in item.context.files)

    def listing(self) -> dict[str, list[str]]:
        """The ids by status."""
        return {
            status: [item.id for item in self.outcomes if item.status == status]
            for status in (CLOSED, OPEN, UNDECIDED)
        }


def outcome_of(
    subject: Subject,
    context: Context,
    answers: Mapping[str, tuple[float | None, tuple[int, ...]]],
    settings: JudgeConfig,
    gate_passed: bool,
    baseline: Collection[str] | None,
) -> Outcome:
    """Decide one criterion from its context and the answers its aspects got.

    ``answers`` maps an aspect id to ``(probability, call indices)``; ``baseline`` is the ids of
    the tests that passed on the base, or None when a base test is not asked for.
    """
    criterion = subject.criterion
    if criterion.kind == REGRESSION:
        status, failing = decide_regression(subject, gate_passed, baseline)
        return Outcome(
            subject, status, failing, context, notes=tuple(MISSING[why] for why in failing)
        )
    why = _opening(subject, context)
    if why is not None:
        aspects = tuple(
            AspectResult(item.id, item.situation, item.outcome, item.text, None, NOT_ASKED)
            for item in criterion.aspects
        )
        notes = (MISSING[why[0]],) if why[0] in MISSING else context.opened
        return Outcome(subject, OPEN, why, context, aspects, notes)
    probabilities = {item.id: answers.get(item.id, (None, ()))[0] for item in criterion.aspects}
    status, failing = decide_aspects(probabilities, settings, context.tests_ok, context.cut)
    aspects = tuple(
        AspectResult(
            item.id,
            item.situation,
            item.outcome,
            item.text,
            probabilities[item.id],
            aspect_status(probabilities[item.id], settings, context.cut),
            answers.get(item.id, (None, ()))[1],
        )
        for item in criterion.aspects
    )
    return Outcome(subject, status, failing, context, aspects)


def _opening(subject: Subject, context: Context) -> tuple[str, ...] | None:
    """Why a criterion is open before any aspect is asked, else None."""
    if not subject.criterion.aspects:
        return (NO_ASPECTS,)
    if not subject.tests:
        return (UNMAPPED,)
    if context.opened:
        return (PRECHECK,)
    return None


# The loop's stop rules -------------------------------------------------------------------


def _remapped(previous: PassResult, last: PassResult) -> bool:
    before = {item.id: item.subject.tests for item in previous.outcomes}
    return any(before.get(item.id) != item.subject.tests for item in last.unclosed)


def stalled(
    history: Sequence[PassResult], settings: JudgeConfig, changed: Collection[str] | None
) -> bool:
    """No progress: nothing closed, nothing changed and no remap, or the same failures again."""
    if len(history) < STALL_PASSES:
        return False
    last, previous = history[-1], history[-2]
    newly = last.closed - previous.closed
    touched = changed is None or bool(changed)
    if not newly and not touched and not _remapped(previous, last):
        return True
    window = history[-(settings.stall_repeats + 1) :]
    return len(window) == settings.stall_repeats + 1 and all(
        item.signature == last.signature for item in window
    )


def stop_reason(
    history: Sequence[PassResult],
    settings: JudgeConfig,
    max_passes: int,
    changed: Collection[str] | None,
    gate_passed: bool,
) -> str | None:
    """Why the loop stops after the last pass, or None to rework.

    Every criterion closed over a commit the gate refused is not ``verified``: an unchanged tree
    is no progress, and the pass limit still holds. A pass over no criteria never verifies.
    """
    last = history[-1]
    if not last.outcomes:
        return NO_PROGRESS
    if not last.standing:
        if gate_passed:
            return VERIFIED
        if changed is not None and not changed:
            return NO_PROGRESS
        return PASS_LIMIT if last.number >= max_passes else None
    if stalled(history, settings, changed):
        return NO_PROGRESS
    return PASS_LIMIT if last.number >= max_passes else None


# The pushback ----------------------------------------------------------------------------


def _quoted(said: str) -> str:
    return "\n".join(f"> {line}" for line in said.splitlines() or [""])


def _aspect_lines(outcome: Outcome) -> list[str]:
    lines = [f"- {note}" for note in outcome.notes]
    for found in outcome.aspects:
        if found.status in (CLOSED, NOT_ASKED):
            continue
        said = "no answer" if found.p is None else f"p {found.p:.2f}"
        missing = ASPECT_MISSING.format(situation=found.situation, outcome=found.outcome)
        lines.append(f"- {found.id} ({said}): {missing}")
    return lines


def _criterion_text(outcome: Outcome) -> list[str]:
    item = outcome.subject.criterion
    parts = [f"### {item.id}: {item.text}", ""]
    if item.sources:
        parts.append("The issue's words:")
        parts += [_quoted(source) for source in item.sources]
    else:
        parts += ["The criterion:", _quoted(item.text)]
    status = "open" if outcome.status == OPEN else "undecided (counts as open)"
    parts += ["", f"Status: {status}. What is missing:", *_aspect_lines(outcome)]
    results = outcome.context.results
    if results:
        parts.append(
            "Mapped tests: "
            + "; ".join(
                f"`{test}` ({RESULT_TEXT.get(found, found)})" for test, found in results.items()
            )
        )
    return [*parts, ""]


def pushback_text(result: PassResult) -> str:
    """The open and undecided criteria for the builder, by feature, each with the issue's words.

    Only the aspects not closed are listed, with their probability and one fixed sentence, and
    the mapped tests with their gate results.
    """
    parts: list[str] = []
    feature = None
    for outcome in result.unclosed:
        if outcome.subject.feature != feature:
            feature = outcome.subject.feature
            parts += [f"## {feature} {outcome.subject.feature_name}".rstrip(), ""]
        parts += _criterion_text(outcome)
    return "\n".join(parts).strip()
