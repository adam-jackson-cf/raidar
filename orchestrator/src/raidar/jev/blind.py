"""Blind labelling: a labeller answers Jev's questions without seeing Jev's answers.

Judging Jev's answer directly anchors the reviewer on it. Blind labelling
avoids that:

1. ``export_blind`` writes, for each answered and not yet labelled decision of
   a policy, what Jev was given (``state`` and ``questions``) and nothing it
   returned: no answers, verdict, model, cost or cache key.
2. The labeller (a person, or a model named by its id) answers each question
   in the shape Jev uses: ``{"type": "noul", "noul": p}``,
   ``{"type": "choice", "choice": option}`` or ``{"type": "score", "score": level}``.
   An answer of ``null``, or ``"uncertain": true`` on the row, means the
   labeller cannot tell.
3. ``derive_blind_labels`` compares those answers with Jev's recorded ones at
   the provisional threshold (``calibration.PROVISIONAL_THRESHOLD``): each side
   is positive when any of its noul answers reaches it. Same class: ``correct``;
   Jev positive and labeller negative: ``false_positive``; the reverse:
   ``false_negative``; a labeller who cannot tell: ``uncertain``. A decision
   with no noul question cannot be classed and is ``correct`` only when every
   choice and score matches, else ``uncertain``. Disagreements on individual
   questions are named in the label's note.

The derived rows carry ``blind: true`` and the reviewer, and go through
``calibration.import_labels`` like any other label.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from typing import Any

from raidar.jev.audit import AuditLog, DecisionRecord, DecisionStage, ReviewerKind
from raidar.jev.calibration import PROVISIONAL_THRESHOLD, LabelImportError, decision_score

_NOTE_CHARS = 4000


def export_blind(
    audit: AuditLog,
    policy: str,
    *,
    trials: Collection[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """What Jev was asked, without its answers, for each unlabelled answered decision."""
    labelled = set(audit.labels(policy))
    rows = []
    for record in audit.decisions(policy):
        if record.stage is not DecisionStage.JEV or not record.answers:
            continue
        if record.request_id in labelled or (trials is not None and record.trial not in trials):
            continue
        rows.append(
            {
                "request_id": record.request_id,
                "policy": policy,
                "question_set": record.question_set,
                "trial": record.trial,
                "subject": record.subject,
                "state": record.state,
                "questions": record.questions,
            }
        )
    rows.sort(key=lambda row: row["request_id"])
    return rows if limit is None else rows[:limit]


def _noul_ok(_question: Mapping[str, Any], answer: Mapping[str, Any]) -> bool:
    value = answer.get("noul")
    return not isinstance(value, bool) and isinstance(value, int | float) and 0 <= value <= 1


def _choice_ok(question: Mapping[str, Any], answer: Mapping[str, Any]) -> bool:
    return answer.get("choice") in (question.get("criteria") or {})


def _score_ok(question: Mapping[str, Any], answer: Mapping[str, Any]) -> bool:
    value, levels = answer.get("score"), len(question.get("criteria") or ())
    return not isinstance(value, bool) and isinstance(value, int) and 0 <= value < levels


_VALID = {"noul": _noul_ok, "choice": _choice_ok, "score": _score_ok}
_EXPECTED = {
    "noul": "a probability in [0, 1]",
    "choice": "one of its options",
    "score": "a level index of its criteria",
}


def _checked(question_id: str, question: Mapping[str, Any], answer: Any) -> Mapping[str, Any]:
    kind = str(question.get("type"))
    if not isinstance(answer, Mapping) or answer.get("type") != kind:
        raise ValueError(f"answer to {question_id!r} must be a {kind} answer")
    if not _VALID[kind](question, answer):
        raise ValueError(f"{kind} answer to {question_id!r} must be {_EXPECTED[kind]}")
    return answer


def _answers(record: DecisionRecord, row: Mapping[str, Any]) -> dict[str, Any] | None:
    """The labeller's checked answers, or None when they cannot tell."""
    questions = record.questions or {}
    given = row.get("answers")
    if not isinstance(given, Mapping):
        raise ValueError("answers must be an object keyed by question id")
    unknown = sorted(set(given) - set(questions))
    missing = sorted(set(questions) - set(given))
    if unknown or missing:
        raise ValueError(f"answers must cover exactly the questions: {unknown or missing}")
    if row.get("uncertain") or any(answer is None for answer in given.values()):
        return None
    return {qid: _checked(qid, questions[qid], given[qid]) for qid in sorted(questions)}


def _positive(answers: Mapping[str, Any]) -> bool:
    return any(
        answer.get("type") == "noul" and float(answer["noul"]) >= PROVISIONAL_THRESHOLD
        for answer in answers.values()
    )


def _disagreements(jev: Mapping[str, Any], labeller: Mapping[str, Any]) -> list[str]:
    differ = []
    for qid, mine in labeller.items():
        theirs = jev.get(qid) or {}
        if mine["type"] == "noul":
            if (float(mine["noul"]) >= PROVISIONAL_THRESHOLD) != (
                float(theirs.get("noul", 0.0)) >= PROVISIONAL_THRESHOLD
            ):
                differ.append(qid)
        elif mine.get(mine["type"]) != theirs.get(mine["type"]):
            differ.append(qid)
    return differ


def blind_label(record: DecisionRecord, labeller: Mapping[str, Any] | None) -> tuple[str, str]:
    """``(label, note)`` from comparing the labeller's answers with Jev's recorded ones."""
    if labeller is None:
        return "uncertain", "blind: the labeller could not tell"
    jev = record.answers or {}
    differ = _disagreements(jev, labeller)
    detail = f"; questions answered differently: {', '.join(differ)}" if differ else ""
    if not any(answer.get("type") == "noul" for answer in labeller.values()):
        return ("uncertain" if differ else "correct"), f"blind: no noul question{detail}"
    jev_positive = decision_score(record) >= PROVISIONAL_THRESHOLD
    mine = _positive(labeller)
    if jev_positive == mine:
        label = "correct"
    else:
        label = "false_positive" if jev_positive else "false_negative"
    side = "positive" if mine else "negative"
    return label, f"blind: labeller {side} at {PROVISIONAL_THRESHOLD}{detail}"


def _derived_row(
    record: DecisionRecord,
    row: Mapping[str, Any],
    reviewer: ReviewerKind,
    reviewer_model: str | None,
) -> dict[str, Any]:
    label, note = blind_label(record, _answers(record, row))
    if row.get("note"):
        note = f"{note}. {row['note']}"
    derived: dict[str, Any] = {
        "request_id": record.request_id,
        "label": label,
        "note": note[:_NOTE_CHARS],
        "reviewer": reviewer.value,
        "blind": True,
    }
    if reviewer is ReviewerKind.MODEL:
        derived["reviewer_model"] = reviewer_model
    return derived


def derive_blind_labels(
    audit: AuditLog,
    policy: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    reviewer: ReviewerKind,
    reviewer_model: str | None = None,
) -> list[dict[str, Any]]:
    """Label rows for ``import_labels`` from a labeller's blind answers."""
    if reviewer is ReviewerKind.MODEL and not (reviewer_model or "").strip():
        raise LabelImportError("a model labeller needs its model id (reviewer_model)")
    decisions = {record.request_id: record for record in audit.decisions(policy)}
    derived = []
    for index, row in enumerate(rows):
        record = decisions.get(str(row.get("request_id")))
        if record is None:
            raise LabelImportError(f"row {index}: no {policy} decision {row.get('request_id')!r}")
        if record.stage is not DecisionStage.JEV or not record.answers:
            raise LabelImportError(f"row {index}: decision {record.request_id} has no Jev answer")
        try:
            derived.append(_derived_row(record, row, reviewer, reviewer_model))
        except ValueError as error:
            raise LabelImportError(f"row {index}: {error}") from error
    return derived
