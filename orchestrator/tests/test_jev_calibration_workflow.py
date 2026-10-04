"""K4: reviewer provenance, blind labelling and threshold promotion from the Jev audit."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from raidar.cli import main
from raidar.jev.audit import LabelRecord, ReviewerKind
from raidar.jev.blind import derive_blind_labels, export_blind
from raidar.jev.calibration import (
    LabelImportError,
    import_labels,
    promotion_readiness,
    supersede_labels,
)
from raidar.jev.ledger import current_labels, label_history, superseded_request_ids
from raidar.jev.policies import CalibrationSplit, PolicyMode, load_policy
from raidar.jev.promotion import (
    PromotionError,
    default_split,
    fit_threshold,
    next_minor,
    regression_corpus,
    split_metrics,
)
from raidar.jev.protocol import ChoiceQuestion, NoulQuestion, ScoreQuestion
from tests.jev_support import (
    SUFFICIENT,
    RecordedTransport,
    make_runtime,
    noul_answer,
    shadow_policy_dir,
)

RUBRIC = "code-quality.rubric-smells"
HAIKU = "anthropic/claude-haiku-4-5"
QUESTIONS = {
    "stub": NoulQuestion(instructions="Is this a stub?"),
    "echo": NoulQuestion(instructions="Does the docstring echo the name?"),
}


def _store(root: Path, answers: list[tuple[float, float]], trials: tuple[str, ...]):
    """One decision per (stub, echo) answer pair, spread over ``trials`` in order."""
    asked: list[str] = []

    def answer_for(question_id: str, _question: object) -> dict[str, object]:
        asked.append(question_id)  # two questions per decision, in any order
        stub, echo = answers[(len(asked) - 1) // 2]
        return noul_answer(stub if question_id == "stub" else echo)

    transport = RecordedTransport(answer_for=answer_for)
    records = []
    for index in range(len(answers)):
        trial = trials[index * len(trials) // len(answers)]
        runtime = make_runtime(root, transport=transport, trial=trial)
        outcome = runtime.decide(
            RUBRIC,
            subject=f"cp1:s{index}",
            state={"case": index},
            questions=QUESTIONS,
            sufficiency=SUFFICIENT,
        )
        records.append(outcome.record)
    return runtime, records


def test_label_records_name_their_reviewer_and_readiness_counts_by_kind(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="reviewer_model"):
        LabelRecord(timestamp="t", request_id="r", policy=RUBRIC, label="correct", reviewer="model")
    with pytest.raises(ValueError, match="takes no reviewer_model"):
        LabelRecord(
            timestamp="t", request_id="r", policy=RUBRIC, label="correct", reviewer_model=HAIKU
        )
    runtime, records = _store(tmp_path, [(0.9, 0.1), (0.1, 0.1), (0.2, 0.3), (0.8, 0.2)], ("t1",))
    rows = [
        {"request_id": records[0].request_id, "label": "correct", "reviewer": "user"},
        {"request_id": records[1].request_id, "label": "correct"},
        {
            "request_id": records[2].request_id,
            "label": "false_negative",
            "reviewer": "model",
            "reviewer_model": HAIKU,
        },
        {
            "request_id": records[3].request_id,
            "label": "uncertain",
            "reviewer": "model",
            "reviewer_model": HAIKU,
        },
    ]
    with pytest.raises(LabelImportError, match="reviewer 'robot' is not one of"):
        import_labels(runtime.audit, RUBRIC, [{**rows[0], "reviewer": "robot"}])
    assert import_labels(runtime.audit, RUBRIC, rows) == 4
    labels = runtime.audit.labels(RUBRIC)
    assert labels[records[0].request_id].reviewer is ReviewerKind.HUMAN  # "user" is a human
    assert labels[records[2].request_id].reviewer_ref == f"model:{HAIKU}"
    readiness = promotion_readiness(runtime.audit, RUBRIC)
    # Uncertain labels have no reference class, so they count for no reviewer.
    assert readiness.by_reviewer == {"human": 2, f"model:{HAIKU}": 1}
    assert readiness.reviewer_kinds == {"human": 2, "model": 1}


def test_blind_export_hides_jev_answers_and_import_derives_labels(tmp_path: Path) -> None:
    runtime, records = _store(
        tmp_path, [(0.9, 0.1), (0.1, 0.2), (0.8, 0.1), (0.1, 0.1), (0.3, 0.3)], ("t1", "t2")
    )
    exported = export_blind(runtime.audit, RUBRIC)
    assert [row["request_id"] for row in exported] == sorted(r.request_id for r in records)
    assert set(exported[0]) == {
        "request_id",
        "policy",
        "question_set",
        "trial",
        "subject",
        "state",
        "questions",
    }
    assert '"noul":' not in json.dumps(exported)  # no answer leaks into the blind file
    assert len(export_blind(runtime.audit, RUBRIC, trials={"t2"}, limit=1)) == 1

    def answers(stub: float | None, echo: float | None = 0.0) -> dict[str, object]:
        return {
            q: None if v is None else {"type": "noul", "noul": v}
            for q, v in (("stub", stub), ("echo", echo))
        }

    rows = [
        {"request_id": records[0].request_id, "answers": answers(0.95)},  # both positive
        {"request_id": records[1].request_id, "answers": answers(0.1)},  # both negative
        {"request_id": records[2].request_id, "answers": answers(0.1), "note": "not a stub"},
        {"request_id": records[3].request_id, "answers": answers(0.1, 0.7)},  # Jev missed echo
        {"request_id": records[4].request_id, "answers": answers(None)},  # cannot tell
    ]
    derived = derive_blind_labels(
        runtime.audit, RUBRIC, rows, reviewer=ReviewerKind.MODEL, reviewer_model=HAIKU
    )
    assert [row["label"] for row in derived] == [
        "correct",
        "correct",
        "false_positive",
        "false_negative",
        "uncertain",
    ]
    assert "questions answered differently: echo" in derived[3]["note"]
    assert derived[2]["note"].endswith("not a stub")
    assert all(row["blind"] and row["reviewer_model"] == HAIKU for row in derived)

    wrong_type = [
        {
            "request_id": records[0].request_id,
            "answers": {
                "stub": {"type": "score", "score": 1},
                "echo": {"type": "noul", "noul": 0.1},
            },
        }
    ]
    partial = [{"request_id": records[0].request_id, "answers": {"stub": answers(0.1)["stub"]}}]
    for bad, message in ((wrong_type, "must be a noul answer"), (partial, "exactly the questions")):
        with pytest.raises(LabelImportError, match=message):
            derive_blind_labels(runtime.audit, RUBRIC, bad, reviewer=ReviewerKind.HUMAN)
    with pytest.raises(LabelImportError, match="model id"):
        derive_blind_labels(runtime.audit, RUBRIC, rows, reviewer=ReviewerKind.MODEL)

    answers_file = tmp_path / "answers.jsonl"
    answers_file.write_text("".join(json.dumps(row) + "\n" for row in rows))
    blind_file = tmp_path / "blind.jsonl"
    common = ["--store", str(tmp_path), "--policy", RUBRIC]
    shipped = CliRunner().invoke(
        main, ["jev", "export-blind", *common, "--output", str(blind_file)]
    )
    assert shipped.exit_code == 0, shipped.output
    assert len(blind_file.read_text().splitlines()) == 5
    imported = CliRunner().invoke(
        main,
        [
            "jev",
            "import-blind",
            *common,
            "--reviewer",
            "model",
            "--reviewer-model",
            HAIKU,
            str(answers_file),
        ],
    )
    assert imported.exit_code == 0, imported.output
    report = json.loads(imported.output)
    assert report["derived"] == {
        "correct": 2,
        "false_negative": 1,
        "false_positive": 1,
        "uncertain": 1,
    }
    assert report["labelled_by_reviewer_kind"] == {"model": 4}
    stored = runtime.audit.labels(RUBRIC)
    assert all(label.blind for label in stored.values())
    # Labelled decisions leave the blind export.
    assert export_blind(runtime.audit, RUBRIC) == []


def test_threshold_fit_metrics_and_split_are_deterministic() -> None:
    points = [(0.9, True), (0.7, True), (0.6, False), (0.3, False), (0.2, False)]
    assert fit_threshold(points) == pytest.approx(0.65)
    metrics = split_metrics(points, 0.65)
    assert (metrics.precision, metrics.recall, metrics.agreement) == (1.0, 1.0, 1.0)
    noisy = split_metrics([(0.9, True), (0.8, False), (0.1, True)], 0.5)
    assert (noisy.precision, noisy.recall, noisy.agreement) == (0.5, 0.5, 0.3333)
    # Equal agreement: the cut nearest the provisional 0.5 wins.
    assert fit_threshold([(0.8, True), (0.2, False)]) == 0.5
    with pytest.raises(PromotionError, match="positives and negatives"):
        fit_threshold([(0.8, True), (0.9, True)])

    split = default_split(["c", "a", "b", "a"])
    assert split == default_split(["b", "a", "c"])
    assert not set(split.calibration) & set(split.evaluation)
    assert len(split.calibration) == 2
    assert (next_minor("1"), next_minor("1.4")) == ("1.1", "1.5")


def _promotable(root: Path):
    # 40 decisions over 4 trials; stub scores split cleanly at 0.6, echo stays low.
    answers = [(0.85, 0.1) if index % 4 == 0 else (0.15, 0.1) for index in range(40)]
    runtime, records = _store(root, answers, ("t1", "t2", "t3", "t4"))
    rows: list[dict[str, object]] = []
    for index, record in enumerate(records):
        row: dict[str, object] = {"request_id": record.request_id, "label": "correct"}
        if index % 3 == 0:
            row.update(reviewer="model", reviewer_model=HAIKU)
        rows.append(row)
    rows[1]["label"] = "false_negative"  # a 0.15 decision the reviewer called positive
    import_labels(runtime.audit, RUBRIC, rows)
    return runtime, records


def test_promote_writes_enforce_only_when_the_audit_verifies(tmp_path: Path) -> None:
    policies = shadow_policy_dir(tmp_path / "policies")
    store = tmp_path / "store"
    runner = CliRunner()
    base = ["--store", str(store), "--policy", RUBRIC, "--policy-dir", str(policies)]

    _store(store, [(0.9, 0.1), (0.1, 0.1)], ("t1", "t2"))
    refused = runner.invoke(main, ["jev", "promote", *base])
    assert refused.exit_code == 1
    assert json.loads(refused.output)["written"] is None
    assert load_policy(policies / f"{RUBRIC}.yaml").mode is PolicyMode.SHADOW

    shutil.rmtree(store)
    runtime, records = _promotable(store)
    dry = runner.invoke(main, ["jev", "promote", *base, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert json.loads(dry.output)["written"] is None
    split_args = ["--calibration", "t1 t2", "--evaluation", "t3,t4"]
    promoted = runner.invoke(
        main, ["jev", "promote", *base, *split_args, "--allow-pending-scorer-bump"]
    )
    assert promoted.exit_code == 0, promoted.output
    report = json.loads(promoted.output)
    assert report["blocked_on_scorer_bump"] is False
    assert report["scorer_bump_required"] == {"scorer": "code-quality", "from": "1", "to": "1.1"}

    written = load_policy(policies / f"{RUBRIC}.yaml")
    promotion = written.promotion
    assert (written.mode, written.version) == (PolicyMode.ENFORCE, "4")
    assert promotion is not None
    assert promotion.split == CalibrationSplit(calibration=("t1", "t2"), evaluation=("t3", "t4"))
    assert (promotion.labelled, promotion.minority) == (40, 11)
    assert promotion.reviewers == {"human": 26, "model": 14}
    assert promotion.reviewer_models == (HAIKU,)
    assert promotion.scorer == "code-quality@1.1"
    assert promotion.calibration_metrics is not None
    # t1+t2: five 0.85 positives, fourteen 0.15 negatives and one 0.15 the reviewer
    # called positive, so the fitted 0.5 cut misses exactly that one.
    assert promotion.threshold == 0.5
    metrics = promotion.calibration_metrics
    assert (metrics.labelled, metrics.precision, metrics.recall) == (20, 1.0, 0.8333)
    assert promotion.regression_corpus == regression_corpus(runtime.audit, RUBRIC, promotion.split)
    header = (policies / f"{RUBRIC}.yaml").read_text().splitlines()
    assert header[0].startswith("# SCB rubric")
    assert any("Promoted to enforce" in line and "jev-promote" in line for line in header)
    assert yaml.safe_load((policies / f"{RUBRIC}.yaml").read_text())["mode"] == "enforce"

    # Everything re-derives; only the scorer version the promotion ships with is missing.
    verified = runner.invoke(main, ["jev", "verify-promotion", *base])
    assert verified.exit_code == 1
    assert json.loads(verified.output)["failures"] == [
        "scorer code-quality is at 1; the promotion ships with code-quality@1.1, so bump its "
        "manifest (and registrations) first"
    ]
    # A label added after the promotion changes the corpus the promotion froze.
    late = _store(store, [(0.9, 0.1)], ("t1",))[1][0]
    import_labels(runtime.audit, RUBRIC, [{"request_id": late.request_id, "label": "correct"}])
    stale = json.loads(runner.invoke(main, ["jev", "verify-promotion", *base]).output)
    assert any("regression_corpus" in failure for failure in stale["failures"])


def test_promote_refuses_to_write_while_the_scorer_bump_is_pending(tmp_path: Path) -> None:
    # The plan verifies, but code-quality@1.1 does not exist yet: writing enforce now would
    # leave a repo policy that `jev verify-promotion` rejects, so no write without the flag.
    policies = shadow_policy_dir(tmp_path / "policies")
    store = tmp_path / "store"
    _promotable(store)
    base = ["--store", str(store), "--policy", RUBRIC, "--policy-dir", str(policies)]
    split_args = ["--calibration", "t1 t2", "--evaluation", "t3,t4"]
    runner = CliRunner()

    dry = runner.invoke(main, ["jev", "promote", *base, *split_args, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert json.loads(dry.output)["blocked_on_scorer_bump"] is True
    pending = runner.invoke(main, ["jev", "promote", *base, *split_args])
    assert pending.exit_code == 1, pending.output
    report = json.loads(pending.output)
    assert (report["verified"], report["written"], report["blocked_on_scorer_bump"]) == (
        True,
        None,
        True,
    )
    assert report["scorer_bump_required"] == {"scorer": "code-quality", "from": "1", "to": "1.1"}
    assert load_policy(policies / f"{RUBRIC}.yaml").mode is PolicyMode.SHADOW


def test_blind_decisions_without_noul_questions_compare_choices_and_scores(
    tmp_path: Path,
) -> None:
    questions = {
        "kind": ChoiceQuestion(instructions="Which kind?", criteria={"a": None, "b": None}),
        "size": ScoreQuestion(instructions="How big?", criteria=("small", "medium", "large")),
    }

    def answer_for(question_id: str, _question: object) -> dict[str, object]:
        if question_id == "kind":
            return {
                "type": "choice",
                "choice": "a",
                "probabilities": {"a": 0.9, "b": 0.1},
                "confidence": 0.9,
            }
        return {
            "type": "score",
            "score": 1,
            "probabilities": {"0": 0.1, "1": 0.8, "2": 0.1},
            "confidence": 0.8,
        }

    runtime = make_runtime(tmp_path, transport=RecordedTransport(answer_for=answer_for))
    record = runtime.decide(
        RUBRIC, subject="s", state={"x": 1}, questions=questions, sufficiency=SUFFICIENT
    ).record

    def row(choice: str, score: int) -> dict[str, object]:
        return {
            "request_id": record.request_id,
            "answers": {
                "kind": {"type": "choice", "choice": choice},
                "size": {"type": "score", "score": score},
            },
        }

    def derive(*rows: dict[str, object]) -> list[dict[str, object]]:
        return derive_blind_labels(runtime.audit, RUBRIC, rows, reviewer=ReviewerKind.HUMAN)

    agree, differ = derive(row("a", 1), row("b", 1))
    assert (agree["label"], differ["label"]) == ("correct", "uncertain")
    assert "questions answered differently: kind" in str(differ["note"])
    for bad, message in ((row("c", 1), "one of its options"), (row("a", 3), "level index")):
        with pytest.raises(LabelImportError, match=message):
            derive(bad)


def test_superseded_labels_leave_calibration_but_stay_on_file(tmp_path: Path) -> None:
    # A selector who also labelled cannot show blindness: its labels are superseded, not
    # deleted, and the decisions may be relabelled by a separate blind labeller.
    policies = shadow_policy_dir(tmp_path / "policies")
    store = tmp_path / "store"
    runtime, records = _promotable(store)
    before = promotion_readiness(runtime.audit, RUBRIC)
    assert before.labelled == 40
    old = [record.request_id for record in records[:10]]
    with pytest.raises(LabelImportError, match="needs a reason"):
        supersede_labels(runtime.audit, RUBRIC, reason=" ", request_ids=old)
    with pytest.raises(LabelImportError, match="no current"):
        supersede_labels(runtime.audit, RUBRIC, reason="x", request_ids=["jevp_unknown"])
    assert (
        len(supersede_labels(runtime.audit, RUBRIC, reason="selector labelled", request_ids=old))
        == 10
    )

    assert promotion_readiness(runtime.audit, RUBRIC).labelled == 30
    assert len(label_history(runtime.audit, RUBRIC)) == 40  # nothing deleted
    assert len(runtime.audit.decisions(RUBRIC)) == 40  # supersessions are not decisions
    assert superseded_request_ids(runtime.audit, RUBRIC) == set(old)
    # Superseding again finds no current label; the decision can be relabelled.
    with pytest.raises(LabelImportError, match="no current"):
        supersede_labels(runtime.audit, RUBRIC, reason="again", request_ids=old[:1])
    import_labels(runtime.audit, RUBRIC, [{"request_id": old[0], "label": "false_positive"}])
    assert current_labels(runtime.audit, RUBRIC)[old[0]].label == "false_positive"

    # A selector names exactly the decisions to export; labelled ones are refused.
    chosen = old[1:4]
    exported = export_blind(runtime.audit, RUBRIC, request_ids=chosen)
    assert [row["request_id"] for row in exported] == sorted(chosen)
    assert '"noul":' not in json.dumps(exported)
    with pytest.raises(LabelImportError, match="not unlabelled answered"):
        export_blind(runtime.audit, RUBRIC, request_ids=[old[0], *chosen])

    # A split recorded ahead of labelling is what `jev promote` uses by default.
    runner = CliRunner()
    common = ["--store", str(store), "--policy", RUBRIC]
    bad = runner.invoke(
        main,
        [
            "jev",
            "record-split",
            *common,
            "--split-id",
            "r1",
            "--calibration",
            "t1 t9",
            "--evaluation",
            "t3",
            "--reason",
            "x",
        ],
    )
    assert bad.exit_code != 0
    assert "t9" in bad.output
    recorded = runner.invoke(
        main,
        [
            "jev",
            "record-split",
            *common,
            "--split-id",
            "r1",
            "--calibration",
            "t1,t2",
            "--evaluation",
            "t3 t4",
            "--reason",
            "stratified by arm",
        ],
    )
    assert recorded.exit_code == 0, recorded.output
    again = runner.invoke(
        main,
        [
            "jev",
            "record-split",
            *common,
            "--split-id",
            "r1",
            "--calibration",
            "t1",
            "--evaluation",
            "t3",
            "--reason",
            "x",
        ],
    )
    assert again.exit_code != 0
    assert "already recorded" in again.output
    dry = runner.invoke(
        main, ["jev", "promote", *common, "--policy-dir", str(policies), "--dry-run"]
    )
    assert dry.exit_code == 0, dry.output
    split = json.loads(dry.output)["promotion"]["split"]
    assert split == {"calibration": ["t1", "t2"], "evaluation": ["t3", "t4"]}
    assert json.loads(dry.output)["promotion"]["labelled"] == 31

    superseded = runner.invoke(main, ["jev", "supersede-labels", *common, "--reason", "r"])
    assert superseded.exit_code != 0  # needs --request-id or --all-current
    cleared = runner.invoke(
        main, ["jev", "supersede-labels", *common, "--reason", "round reset", "--all-current"]
    )
    assert cleared.exit_code == 0, cleared.output
    assert json.loads(cleared.output)["labelled"] == 0
