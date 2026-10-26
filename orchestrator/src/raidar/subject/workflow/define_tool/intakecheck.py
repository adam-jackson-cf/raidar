"""The checks on the intake's reply as a whole: its named features, questions and replies, then its
definition.

The intake builds its reply through the ``define`` tool (``capability.define.tool``);
the engine and the tool run these same checks on the reply the tool's state
holds, so a finding means the same thing wherever it is read. ``check_reply``
is pure: it reads the reply, the numbered units of the issue, the text a quote
may come from and the workspace a ``path:line`` names, and writes nothing.

``Kind.REPLY`` findings come first (the reply's own faults: its replies, named
features and questions), then those of the definition (``compact.check``) when
the status is ``ready``.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import compact, evidence, owed
from .aspects import Finding
from .core import BEHAVIOUR_FROM_KEY, JsonObject, blank, rows, text
from .kinds import Kind
from .owed import Owed

QUESTION_KINDS = ("objective_unclear", "feature_conflict", "missing_input")
REPLY_OUTCOMES = ("accepted", "overridden", "answered", "unclear", "unanswered")
# Where a named feature's behaviour is stated; ``none`` must be asked about.
FEATURE_SOURCES = ("issue", "code", "tests", "none")
MAX_QUESTIONS = 3
# The line of a question that gives each field the check needs, by field.
QUESTION_LINES = {"proposal": "proposal", "options": "option"}
RE_ASKED = frozenset({"unclear", "unanswered"})
READY = "ready"
NEEDS_INPUT = "needs_input"


def question_errors(questions: Sequence[JsonObject]) -> list[str]:
    errors: list[str] = []
    if not questions:
        errors.append("`define done ask` needs at least one `question`")
    if len(questions) > MAX_QUESTIONS:
        errors.append(
            f"ask at most {MAX_QUESTIONS} questions, only what blocks a feature "
            "itself; decide every other detail and put it in a `decision`"
        )
    for question in questions:
        errors += [
            f"the question about `{text(question, 'feature')}` has no `{line}:` line"
            for name, line in QUESTION_LINES.items()
            if blank(question, name)
        ]
    return errors


def _named(name: str) -> str:
    return " ".join(name.casefold().split())


def feature_errors(
    value: JsonObject,
    units: compact.Units,
    source: str = "",
    workdir: Path | None = None,
) -> list[str]:
    """Every named feature with no stated behaviour must be asked about.

    A feature has no stated behaviour when its ``behaviour_from`` is ``none`` or
    its ``evidence`` fails the check (``compact.feature_check``): ``source`` is
    the text an ``issue`` quote must come from, ``workdir`` the workspace a
    ``path:line`` must name. A question names the feature when its ``feature``
    or its ``question`` contains the feature's name (case and spacing aside),
    and only an ``objective_unclear`` question counts.
    """
    asked = [
        _named(f"{text(q, 'feature')} {text(q, 'question')}")
        for q in rows(value, "questions")
        if text(q, "kind") == "objective_unclear"
    ]
    errors: list[str] = []
    features = rows(value, "features")
    names = [text(feature, "name") for feature in features]
    for feature in features:
        name = _named(text(feature, "name"))
        outcome = compact.feature_check(feature, units, source, workdir, names)
        if outcome == evidence.OK or not name:
            continue
        if any(name in question for question in asked):
            continue
        label = f"named feature `{text(feature, 'name')}`"
        if outcome == evidence.NONE:
            errors.append(
                f"{label} has `from: none` but no `objective_unclear` question "
                "names it: ask about it (a `question` line, then `define done "
                "ask`), or set its `from:` line to where the issue, code or tests "
                "state its behaviour, with that evidence"
            )
        else:
            errors.append(
                f"{label} has `from: {text(feature, BEHAVIOUR_FROM_KEY)}` but "
                f"{outcome}, so nothing states its behaviour: give a `from:` line "
                "whose evidence states it, or ask about it in an `objective_unclear` "
                "question (`define done ask`)"
            )
    return errors


def _re_asked(replies: Sequence[JsonObject]) -> list[str]:
    """Return the error for each reply that left its question open."""
    return [
        f"`reply {text(reply, 'question')}` is {text(reply, 'outcome')}: re-ask "
        "it (`define done ask`)"
        for reply in replies
        if text(reply, "outcome") in RE_ASKED
    ]


def reply_errors(value: JsonObject, open_ids: Sequence[str]) -> list[str]:
    replies = rows(value, "replies")
    named = Counter(text(reply, "question") for reply in replies)
    errors = [
        f"there is no `reply {ident}:` line: answer every open question"
        for ident in open_ids
        if ident not in named
    ]
    errors += [
        f"`reply {ident}` names no open question: drop it (`define drop {ident}`)"
        for ident in named
        if ident not in open_ids
    ]
    errors += [f"`reply {ident}` is given more than once" for ident, n in named.items() if n > 1]
    if text(value, "status") == READY:
        errors += _re_asked(replies)
    return errors


@dataclass(frozen=True)
class ReplyChecked:
    """What ``check_reply`` finds in an intake reply.

    ``head`` is the reply's own findings (all ``Kind.REPLY``, blocking) and
    ``file`` those of its definition (``compact.Checked``, ``None`` for a reply
    that is not ``ready``). ``owed`` is what a ready definition still owes the
    acceptance probes (``owed.findings``; none when the workflow derives no
    example probes). ``findings`` are all of them, in the order a repair turn
    lists them.
    """

    head: tuple[str, ...]
    file: compact.Checked | None
    owed: tuple[Finding, ...] = ()

    @property
    def findings(self) -> tuple[Finding, ...]:
        found = tuple(Finding(item, kind=Kind.REPLY) for item in self.head)
        return found + (self.file.findings if self.file is not None else ()) + self.owed

    @property
    def errors(self) -> tuple[str, ...]:
        return tuple(item.text for item in self.findings)

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(item.kind for item in self.findings)

    @property
    def blocking(self) -> tuple[str, ...]:
        return tuple(item.text for item in self.findings if not item.advisory)


def check_reply(
    value: JsonObject,
    units: compact.Units,
    source: str,
    workdir: Path | None,
    open_ids: Sequence[str],
) -> ReplyChecked:
    """Check an intake reply (``schemas.INTAKE_REPLY_SCHEMA``); ``ready`` also its definition."""
    errors = reply_errors(value, open_ids) if open_ids else []
    errors += feature_errors(value, units, source, workdir)
    questions = rows(value, "questions")
    status = text(value, "status")
    if status == NEEDS_INPUT:
        errors += question_errors(questions)
    elif questions:
        errors.append(
            "`define done ready` needs no `question`: drop them, or run `define done ask`"
        )
    file: compact.Checked | None = None
    if status == READY:
        body = value.get("definition")
        file = compact.check(body if isinstance(body, dict) else {}, units, source)
    return ReplyChecked(tuple(errors), file)


def with_probes(checked: ReplyChecked, value: JsonObject, probes: Owed | None) -> ReplyChecked:
    """Return ``checked`` with what a ready definition still owes the acceptance probes.

    ``probes`` is what the issue obliges the definition to give examples for,
    when the workflow derives probes from them (``owed``); ``None``, or a reply
    that is not ready, owes nothing.
    """
    if probes is None or text(value, "status") != READY:
        return checked
    return dataclasses.replace(checked, owed=owed.findings(probes, value))
