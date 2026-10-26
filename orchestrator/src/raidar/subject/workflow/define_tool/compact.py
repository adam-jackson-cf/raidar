"""The intake's reply: the model cites the issue by unit id, the engine writes the file.

The intake replies with JSON that *references* the issue (it copies none of its
words) and the engine does the rest, in this module (a pure one):

- ``units`` numbers what the intake may cite. ``U<n>`` is a statement of the
  issue (``normative.statements``, lead-ins included), ``C<n>`` a line of one of
  its fenced code blocks and ``A<n>`` an answer from an earlier round. Required
  units (``*`` in ``Units.text``) are exactly the normative statements
  ``normative.units`` gives: every criterion set must cite each of them. What
  is normative, what is required and whether an uncovered required unit blocks
  ``define done`` are the workflow's unit rules (``unitrules``), carried by
  ``Units.rules``.
- ``check`` runs the deterministic checks on the reply's ``definition``: its
  ids, its sources, and then the rules of a feature definition
  (``featuredef.findings``), on the file ``render`` writes from the reply.
- ``render`` writes ``.raidar/feature-definition.md`` in markdown, so every
  reader of the file (``featuredef.parse``, the judge's context, the builder
  prompt, the definition guard) reads one shape. Each
  ``Source:`` line is a cited unit's text, verbatim by construction, and each
  ``Verification:`` line is a constant: the model writes neither.
- ``feature_check`` is the named-features evidence check with unit ids.

Everything a field holds is put on one line (whitespace collapsed), so no field
can open a markdown heading, bullet or label. ``check`` reads the rendered file
back and reports any field that does not read back as written.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import (
    aspects,
    evidence,
    featuredef,
    normative,
)
from .aspects import Finding
from .core import BEHAVIOUR_FROM_KEY, JsonObject, rows, strings, text
from .kinds import Kind
from .unitrules import UnitRules

# The `Verification:` lines the engine writes: no model token reaches them. The
# judge's closing of a criterion goes by the builder's ``ac_tests`` mapping.
VERIFICATION = (
    "a test mapped to this criterion sets up each aspect's situation and asserts its outcome"
)
REGRESSION_VERIFICATION = "the repository's earlier tests that cover it keep passing"
# Not inside a longer id (``F1.A1``, ``XU5``). An id may open a range of the same
# kind: ``U3-U5`` or ``U3-5`` cites ``U3``, ``U4`` and ``U5``.
UNIT_ID = re.compile(
    r"(?<![\w.])(?P<kind>[UCA])(?P<first>[1-9][0-9]*)"
    r"(?:\s*[-\u2013\u2014]\s*(?P=kind)?(?P<last>[1-9][0-9]*))?\b"
)
# The longest range read as a run of ids; a longer one cites its ends only.
_MAX_RANGE = 200
REVIEW = featuredef.REQUIRES_REVIEW
REGRESSION = aspects.REGRESSION

_FEATURE_ID = re.compile(r"F\d+")
# A test id as ``featuredef.retired_tests`` reads it between backticks.
_TEST_ID = re.compile(r"[^`\s]+\.py::[^`\n]+")
# What `featuredef.findings` says of ids that `_reply_findings` already says, by
# its own wording from the start of the message: other findings quote the model's
# lines, so a phrase inside one must not drop it.
_ID_FAULT = re.compile(
    r"(?:(?:feature|acceptance criterion) id `.*` is used more than once\Z"
    r"|acceptance criterion `.*` is under feature `.*`: "
    r"|acceptance criterion \d+ of feature `.*` has no id: "
    r"|`### .*` under `## Features` is not a `### F<n> <name>` feature\Z)",
    re.DOTALL,
)
# The signal of a labelled list (unit rules 2): its items are what it says, so
# it is quoted whole whatever its word count.
ENUMERATION = "enumeration"
# The fewest non-filler words a unit cited by a retirement holds (as
# ``featuredef.retired_tests`` asks of a retirement's quotes).
_RETIRE_MIN_WORDS = 2


@dataclass(frozen=True)
class SourceUnit:
    """One thing the intake may cite: ``id`` (``U3``, ``C1``, ``A2``) and its text.

    ``text`` is verbatim as ``normative`` (or the answer line) gives it.
    ``required`` marks a normative, non-lead-in ``U`` unit that is not a
    fragment; ``signals`` says why it is normative. ``citable`` is false for an
    issue unit that could not stand as a ``Source:`` quote (a fragment such as
    ``ok``): it is not listed for the intake to cite, though its number is kept.
    A normative fragment, with too few words to be cited at all, is also not
    required; a normative unit that fails the quote check for another reason
    stays required and listed under unit rules 1, where an uncited one is
    advisory; under rules 2, where it blocks, it is neither required nor listed.
    A labelled list (signal ``enumeration``, unit
    rules 2) is required and citable whatever its word count: its items are
    what it says.
    """

    id: str
    text: str
    required: bool
    signals: tuple[str, ...] = ()
    citable: bool = True


@dataclass(frozen=True)
class Units:
    """The numbered units of one intake (``units``) and the unit rules they were cut under."""

    items: tuple[SourceUnit, ...]
    rules: UnitRules

    def get(self, ident: str) -> SourceUnit | None:
        return next((item for item in self.items if item.id == ident), None)

    def required(self) -> tuple[SourceUnit, ...]:
        """Return the units every criterion set must cite, in order."""
        return tuple(item for item in self.items if item.required)

    def text(self) -> str:
        """Return the list the prompt shows: ``U12*: …`` (required) or ``U13: …``."""
        return "\n".join(
            f"{item.id}{'*' if item.required else ''}: {_one(item.text)}"
            for item in self.items
            if item.citable
        )


def _quotable(said: str, issue: str) -> bool:
    """Return whether ``said`` could stand as a criterion's ``Source:`` quote."""
    return not evidence.verbatim_problem(said, issue, featuredef.SOURCE_MIN_WORDS)


def _fragment(said: str) -> bool:
    """Return whether ``said`` has too few non-filler words to stand as a quote."""
    words = re.findall(r"\w+", evidence.normal(said, unquoted=True))
    return (
        len([word for word in words if word not in evidence.FILLER]) < featuredef.SOURCE_MIN_WORDS
    )


def _required(unit: normative.Unit, lead: bool) -> bool:
    """Whether a unit must be cited: normative, no lead-in, and a quote or a labelled list."""
    return unit.normative and not lead and (not _fragment(unit.text) or ENUMERATION in unit.signals)


def units(issue: str, answers: Sequence[str], rules: UnitRules) -> Units:
    """Return the numbered units: statements (``U``), code lines (``C``), answers (``A``).

    ``issue`` is the issue alone; ``answers`` are the earlier rounds' answer
    lines, which are citable but never required; ``rules`` are the workflow's
    unit rules.
    """
    items: list[SourceUnit] = []
    for number, (unit, lead) in enumerate(normative.statements(issue, rules), start=1):
        quotable = _quotable(unit.text, issue)
        # A unit that blocks ``define done`` until cited must be citable: one that
        # fails the quote check would be refused when cited and when not.
        required = _required(unit, lead) and (
            quotable or ENUMERATION in unit.signals or not rules.uncovered_blocks
        )
        citable = required or quotable
        items.append(SourceUnit(f"U{number}", unit.text, required, unit.signals, citable))
    items += [
        SourceUnit(f"C{number}", line, False, (), _quotable(line, issue))
        for number, line in enumerate(normative.code_lines(issue), start=1)
    ]
    items += [
        SourceUnit(f"A{number}", answer, False) for number, answer in enumerate(answers, start=1)
    ]
    return Units(tuple(items), rules)


def cited(said: str) -> tuple[str, ...]:
    """Return the unit ids in ``said``, in order, each once.

    A range of one kind (``U3-U5``, ``U3-5``) cites every unit in it, the middle
    ones too; one running backwards, or longer than ``_MAX_RANGE``, cites its two
    ends only.
    """
    ids: list[str] = []
    for found in UNIT_ID.finditer(said):
        kind, first = found["kind"], int(found["first"])
        last = int(found["last"] or first)
        if not first < last <= first + _MAX_RANGE:
            ids.append(f"{kind}{first}")
            if last != first:
                ids.append(f"{kind}{last}")
            continue
        ids.extend(f"{kind}{number}" for number in range(first, last + 1))
    return tuple(dict.fromkeys(ids))


def _one(said: str) -> str:
    """Return ``said`` on one line: its line breaks joined by one space.

    Runs of spaces inside a line are kept: a literal may depend on them.
    """
    return " ".join(part for line in said.splitlines() if (part := line.strip()))


def _short(said: str, limit: int = 60) -> str:
    return said if len(said) <= limit else f"{said[: limit - 1]}…"


def _resolved(ids: Sequence[str], found: Units) -> list[SourceUnit]:
    return [item for ident in ids if (item := found.get(ident)) is not None]


def _sources(item: JsonObject) -> tuple[str, ...]:
    """Return the unit ids an item's ``sources`` cite (``U3, U4`` in one string counts)."""
    return cited(" ".join(strings(item, "sources")))


# Named features ---------------------------------------------------------------------


def feature_check(
    feature: JsonObject,
    found: Units,
    source: str,
    workdir: Path | None,
    names: Sequence[str],
) -> str:
    """``ok``, ``none`` or why a named feature's evidence fails (see ``evidence``).

    For ``behaviour_from: issue`` the evidence is unit ids: some cited unit must
    state the feature's behaviour as ``evidence.quote_problem`` asks of a quote.
    """
    if text(feature, "behaviour_from") != "issue":
        return evidence.check(feature, workdir)
    ids = cited(text(feature, "evidence"))
    if not ids:
        return "the `from:` evidence names no unit id: cite the units (`U3`) that say what it does"
    resolved = {item.id for item in _resolved(ids, found)}
    unknown = [ident for ident in ids if ident not in resolved]
    if unknown:
        return (
            f"the `from:` evidence cites `{unknown[0]}`, which is no unit: cite ids from "
            "the numbered list only"
        )
    name = text(feature, "name")
    problems = [
        evidence.quote_problem(name, item.text, source, names) for item in _resolved(ids, found)
    ]
    return evidence.OK if "" in problems else problems[0]


def feature_record(
    feature: JsonObject,
    found: Units,
    source: str,
    workdir: Path | None,
    names: Sequence[str],
) -> JsonObject:
    """Return one named feature as ``gate.json`` keeps it, with the units it cites."""
    claimed = text(feature, "behaviour_from")
    ids = cited(text(feature, "evidence")) if claimed == "issue" else ()
    return {
        "name": text(feature, "name"),
        BEHAVIOUR_FROM_KEY: claimed,
        "evidence": text(feature, "evidence"),
        "evidence_check": feature_check(feature, found, source, workdir, names),
        "evidence_units": [item.text for item in _resolved(ids, found)],
    }


# Rendering --------------------------------------------------------------------------


def _where(ids: Sequence[str], refs: Sequence[str], found: Units) -> str:
    """Return where a decision comes from: the cited units, then the refs."""
    parts = [f'"{_one(item.text)}"' for item in _resolved(ids, found)]
    parts += [f"`{_one(ref)}`" for ref in refs if _one(ref)]
    return "; ".join(parts) or "`default`"


def _criterion_lines(item: JsonObject, found: Units) -> list[str]:
    lines = [f"- {_one(text(item, 'id'))}: {_one(text(item, 'text'))}"]
    lines += [f'  Source: "{_one(unit.text)}"' for unit in _resolved(_sources(item), found)]
    lines += [
        f"  Aspect: {_one(text(a, 'situation'))} -> {_one(text(a, 'outcome'))}"
        for a in rows(item, "aspects")
    ]
    lines += [
        f"  Example: {_one(text(e, 'input'))} -> {_one(text(e, 'expected'))}"
        for e in rows(item, "examples")
    ]
    if text(item, "kind") == REGRESSION:
        lines += [
            f"  Kind: {REGRESSION}",
            f"  Verification: {REGRESSION_VERIFICATION}",
        ]
    else:
        lines.append(f"  Verification: {VERIFICATION}")
    return lines


def _decision_bullets(definition: JsonObject, found: Units) -> list[str]:
    """Return the decisions and retirements as the bullets (without ``- ``) rendered."""
    bullets = []
    for item in rows(definition, "decisions"):
        reverse = _one(text(item, "reverse"))
        where = _where(_sources(item), strings(item, "refs"), found)
        bullets.append(
            f"{_one(text(item, 'text'))}"
            f"{'; to reverse: ' + reverse if reverse else ''}"
            f" (source: rung {_one(text(item, 'rung'))}: {where})"
            f"{' ' + REVIEW if text(item, 'risk') == REVIEW else ''}"
        )
    for item in rows(definition, "retires"):
        where = _where(_sources(item), (), found)
        bullets.append(
            f"retires `{_one(text(item, 'test'))}`: {_one(text(item, 'reason'))} "
            f"(source: rung 2: {where}) {REVIEW}"
        )
    return bullets


def render(definition: JsonObject, found: Units) -> str:
    """Return the feature definition file for a reply's ``definition``.

    The shape is the one an intake writes (``featuredef``): every field on one
    line, a ``Source:`` line per cited unit that exists, the constant
    ``Verification:`` line, and ``- None.`` when nothing was decided. It works
    for any reply the schema admits: unknown ids are skipped, lists may be empty.
    """
    lines = [
        f"# {_one(text(definition, 'title'))}",
        "",
        f"## {featuredef.OBJECTIVE}",
        "",
        _one(text(definition, "objective")),
        "",
        f"## {featuredef.FEATURES}",
    ]
    for feature in rows(definition, "features"):
        lines += [
            "",
            f"### {_one(text(feature, 'id'))} {_one(text(feature, 'name'))}",
            "",
            f"Outcome: {_one(text(feature, 'outcome'))}",
        ]
        if text(feature, "risk") == REVIEW:
            lines.append(f"Risk: {REVIEW}")
        lines += ["", "Acceptance criteria:"]
        for item in rows(feature, "criteria"):
            lines += _criterion_lines(item, found)
    bullets = _decision_bullets(definition, found)
    lines += ["", f"## {featuredef.DECISIONS}", ""]
    lines += [f"- {bullet}" for bullet in bullets] or ["- None."]
    return "\n".join(lines) + "\n"


# Checking ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Checked:
    """What ``check`` finds in a reply's ``definition``.

    ``findings`` go to the repair turn: the blocking ones and the uncovered
    required units (advisory under unit rules 1, blocking under 2). ``notes`` are advisories
    recorded and never sent
    to the model (a literal no aspect carries). ``uncovered`` are the required
    units no criterion cites, ``rendered`` the file the engine writes and
    ``retired`` the tests the reply's ``retires`` entries grant.
    """

    findings: tuple[Finding, ...]
    notes: tuple[Finding, ...]
    uncovered: tuple[SourceUnit, ...]
    rendered: str
    retired: tuple[str, ...]

    @property
    def errors(self) -> tuple[str, ...]:
        """The text of each finding, in order."""
        return tuple(item.text for item in self.findings)

    @property
    def blocking(self) -> tuple[str, ...]:
        """The text of the findings that are not advisory."""
        return tuple(item.text for item in self.findings if not item.advisory)

    @property
    def kinds(self) -> tuple[str, ...]:
        """The kind of each finding, in the order of ``errors``."""
        return tuple(item.kind for item in self.findings)


def uncovered_error(unit: SourceUnit) -> str:
    """Return the repair error for a required unit no criterion cites."""
    said = (
        f'the issue statement {unit.id} "{_one(unit.text)}" states a requirement '
        f"({', '.join(unit.signals)})"
    )
    return (
        f"{said} that no acceptance criterion cites in a `source:` line: add "
        f"{unit.id} to the `source:` line of the criterion that covers it, or add "
        "a criterion for it"
    )


def _fragment_findings(
    owner: str, known: Sequence[SourceUnit], source: str, words: int
) -> list[Finding]:
    """Return a finding for each cited unit that is too short to state what the issue asks."""
    findings: list[Finding] = []
    for unit in known:
        needed = 0 if ENUMERATION in unit.signals else words
        problem = unit.id[0] != "A" and evidence.verbatim_problem(unit.text, source, needed)
        if problem:
            findings.append(
                Finding(
                    f"the unit {unit.id} cited by {owner} {problem}: cite the "
                    "units that say what the issue asks, not a fragment",
                    kind=Kind.QUOTE_VERBATIM,
                )
            )
    return findings


def _unit_findings(
    owner: str,
    item: JsonObject,
    found: Units,
    source: str,
    words: int = 0,
) -> list[Finding]:
    """Return what is wrong with the unit ids ``item`` cites in ``sources``.

    ``words`` is set for a criterion or a retirement, not a decision: citing no
    unit is then a fault, and a cited issue unit (``U`` or ``C``) must also be
    citable as a ``Source:`` quote of that many non-filler words
    (``evidence.verbatim_problem``), as ``featuredef`` asks of a quote.
    """
    ids = _sources(item)
    known = _resolved(ids, found)
    findings = [
        Finding(
            f"the `source:` line of {owner} cites `{ident}`, which is no unit: cite "
            "ids from the numbered list only",
            kind=Kind.QUOTE_VERBATIM,
        )
        for ident in ids
        if ident not in {unit.id for unit in known}
    ]
    if words and not ids:
        findings.append(
            Finding(
                f"{owner} has no `source:` line: list the ids (`U3`) of the issue "
                "statements it derives from",
                kind=Kind.QUOTE_MISSING,
            )
        )
    return findings + _fragment_findings(owner, known if words else (), source, words)


def _retirement_findings(entry: JsonObject, found: Units, source: str) -> list[Finding]:
    """Return what is wrong with one ``retires`` entry (none: it is granted)."""
    test = text(entry, "test")
    owner = f"the retirement of `{test}`"
    findings = _unit_findings(owner, entry, found, source, _RETIRE_MIN_WORDS)
    if not _TEST_ID.fullmatch(test):
        findings.append(
            Finding(
                f"{owner} is not a test id: write it as `path/test_x.py::test_name`",
                kind=Kind.STRUCTURE,
            )
        )
    return findings


def _reply_findings(
    definition: JsonObject, found: Units, source: str
) -> tuple[list[Finding], list[str]]:
    """Return the reply's own faults (ids, sources, retirements) and the tests it retires."""
    findings: list[Finding] = []
    features: set[str] = set()
    criteria: set[str] = set()
    for feature in rows(definition, "features"):
        ident = text(feature, "id")
        if not _FEATURE_ID.fullmatch(ident):
            findings.append(
                Finding(
                    f"feature id `{ident}` is not `F<n>`: number the features `F1`, `F2`, …",
                    kind=Kind.STRUCTURE,
                )
            )
        elif ident in features:
            findings.append(
                Finding(f"feature id `{ident}` is used more than once", kind=Kind.STRUCTURE)
            )
        features.add(ident)
        pattern = re.compile(rf"{re.escape(ident)}\.AC\d+")
        for item in rows(feature, "criteria"):
            name = text(item, "id")
            if not pattern.fullmatch(name):
                findings.append(
                    Finding(
                        f"acceptance criterion id `{name}` under feature `{ident}` "
                        f"is not `{ident}.AC<n>`: number it `{ident}.AC1`, "
                        f"`{ident}.AC2`, …",
                        kind=Kind.STRUCTURE,
                    )
                )
            elif name in criteria:
                findings.append(
                    Finding(
                        f"acceptance criterion id `{name}` is used more than once",
                        kind=Kind.STRUCTURE,
                    )
                )
            criteria.add(name)
            findings += _unit_findings(
                f"acceptance criterion `{name}`",
                item,
                found,
                source,
                featuredef.SOURCE_MIN_WORDS,
            )
    for number, item in enumerate(rows(definition, "decisions"), start=1):
        findings += _unit_findings(f"decision {number}", item, found, source)
    retired: list[str] = []
    for entry in rows(definition, "retires"):
        problems = _retirement_findings(entry, found, source)
        findings += problems
        if not problems:
            retired.append(text(entry, "test"))
    return findings, list(dict.fromkeys(retired))


def _differences(label: str, pairs: Sequence[tuple[str, object, object]]) -> list[Finding]:
    """Return a finding per ``(field, written, read back)`` that differs."""
    return [
        Finding(
            f"the {name} of {label} reads back as `{_short(str(got))}` instead of "
            f"`{_short(str(want))}`: the text starts with markdown or a label; "
            "reword it",
            kind=Kind.STRUCTURE,
        )
        for name, want, got in pairs
        if want != got
    ]


def _criterion_differences(item: JsonObject, read: featuredef.Criterion) -> list[Finding]:
    return _differences(
        f"acceptance criterion `{text(item, 'id')}`",
        [
            ("id", text(item, "id"), read.id),
            ("text", _one(text(item, "text")), read.text),
            ("kind", REGRESSION if text(item, "kind") == REGRESSION else "", read.kind),
            (
                "aspects",
                [
                    (_one(text(a, "situation")), _one(text(a, "outcome")))
                    for a in rows(item, "aspects")
                ],
                [(a.situation, a.outcome) for a in read.aspects],
            ),
            (
                "examples",
                [
                    (_one(text(e, "input")), _one(text(e, "expected")))
                    for e in rows(item, "examples")
                ],
                [(e.given, e.expected) for e in read.examples],
            ),
        ],
    )


def _round_trip(
    definition: JsonObject, rendered: str, found: Units, blocked: Sequence[str]
) -> list[Finding]:
    """Return what does not read back from the rendered file as the reply wrote it.

    A feature or criterion another finding names (``blocked``) is left out:
    that finding is what to fix first.
    """
    parsed = featuredef.parse(rendered, True, True, True)
    given = rows(definition, "features")

    def named(ident: str) -> bool:
        return any(f"`{ident}`" in line for line in blocked)

    def ids(items: Sequence[JsonObject]) -> list[str]:
        return [text(item, "id") for item in items]

    findings = _differences(
        "the objective",
        [
            (
                "text",
                _one(text(definition, "objective")),
                parsed.sections.get(featuredef.OBJECTIVE, "").strip(),
            )
        ],
    )
    # Only when nothing else is wrong: a bad feature id is reported on its own.
    if not blocked:
        findings += _differences(
            "the definition",
            [("features", ids(given), [read.id for read in parsed.features])],
        )
    for want, read in zip(given, parsed.features, strict=False):
        ident = text(want, "id")
        if named(ident):
            continue
        findings += _differences(
            f"feature `{ident}`",
            [
                ("id", ident, read.id),
                ("name", _one(text(want, "name")), read.name),
                ("outcome", _one(text(want, "outcome")), read.outcome),
                (
                    "criteria",
                    ids(rows(want, "criteria")),
                    [back.id for back in read.traced],
                ),
            ],
        )
        for item, back in zip(rows(want, "criteria"), read.traced, strict=False):
            if not named(text(item, "id")):
                findings += _criterion_differences(item, back)
    return findings + _decision_differences(definition, parsed, found)


def _decision_differences(
    definition: JsonObject, parsed: featuredef.FeatureDefinition, found: Units
) -> list[Finding]:
    """Return a finding for each decision that does not read back as written."""
    written = _decision_bullets(definition, found)
    findings: list[Finding] = []
    for number, (want, got) in enumerate(zip(written, parsed.decisions, strict=False), start=1):
        findings += _differences(f"decision {number}", [("text", want, got)])
    return findings


def _rendered_findings(rendered: str, source: str) -> tuple[list[Finding], list[Finding]]:
    """Return the feature definition rules' findings on the rendered file, and its notes.

    The quote findings are the reply's own (``_reply_findings``); a literal no aspect carries
    is a note, and a fault in an id is reported once, by the reply.
    """
    findings: list[Finding] = []
    notes: list[Finding] = []
    for item in featuredef.findings(
        rendered, traced=True, anchored=True, source=source, aspects=True
    ):
        if item.kind in (Kind.QUOTE_VERBATIM, Kind.QUOTE_MISSING):
            continue
        if item.kind == Kind.VALUE_LITERAL:
            notes.append(item)
        elif not _ID_FAULT.match(item.text):
            findings.append(item)
    return findings, notes


def _cited(definition: JsonObject) -> set[str]:
    """Return the unit ids any criterion of ``definition`` cites."""
    return {
        ident
        for feature in rows(definition, "features")
        for item in rows(feature, "criteria")
        for ident in _sources(item)
    }


def check(definition: JsonObject, found: Units, source: str) -> Checked:
    """Check a reply's ``definition`` and render it (see the module docstring).

    The order of ``Checked.findings``: the reply's own faults, the faults the
    feature definition's rules find in the rendered file, what does not read back
    as written, and the required units nothing cites (advisory under unit rules 1,
    blocking under 2).
    """
    rendered = render(definition, found)
    findings, retired = _reply_findings(definition, found, source)
    rule_findings, notes = _rendered_findings(rendered, source)
    findings += rule_findings
    blocked = [item.text for item in findings]
    findings += _round_trip(definition, rendered, found, blocked)
    cites = _cited(definition)
    uncovered = tuple(unit for unit in found.required() if unit.id not in cites)
    findings += [
        Finding(
            uncovered_error(unit),
            advisory=not found.rules.uncovered_blocks,
            kind=Kind.NORMATIVE_UNDISPOSED,
        )
        for unit in uncovered
    ]
    return Checked(tuple(findings), tuple(notes), uncovered, rendered, tuple(retired))
