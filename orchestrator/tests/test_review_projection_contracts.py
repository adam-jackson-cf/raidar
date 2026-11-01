"""The review projection's decisions, driven from in-memory runs and Assessments.

The fixture's runs are built as the projection's own inputs (``JobSource`` records, Assessments,
the files a review reads), so nothing here constructs a run directory. The real-directory seams
(discovery, stores, the CLI over a job tree) are in ``test_review_projection_from_runs``.
"""

from __future__ import annotations

import copy
import json
import runpy
import sys
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from harbor.models.trajectories import (
    Agent,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)

from raidar.contract.review import ReviewProjectionArtifact, ReviewRunDetail
from raidar.contract.trial import CheckpointState, ExecutionValidity, TrialRecord, ValidityCheck
from raidar.design.experiment.lock import default_lock_path
from raidar.evaluation.analysis.pooling import PoolingError
from raidar.evaluation.analysis.sources import AnalysisInputError, JobDir
from raidar.evaluation.review.integrity import check_review_jobs
from raidar.evaluation.review.projection import main, merge_review_projections
from raidar.evaluation.review.projection.assessments import (
    AssessmentLookup,
    TrialAssessments,
    disk_inputs,
)
from raidar.evaluation.review.projection.atif import (
    _text,
    _tool_status,
    project_trajectory,
    tool_label,
)
from raidar.evaluation.review.projection.comparability import (
    MISMATCH_CATEGORY,
    UNKNOWN_POLICY,
    mark_policy_mismatches,
)
from raidar.evaluation.review.projection.experiment_epoch import EpochProjection, project_sources
from raidar.evaluation.review.projection.headline import (
    COMPONENT_METRICS,
    UNDECLARED_MARKER,
    headline,
    needed_scorers,
    policy_shares,
    withheld_reasons,
)
from raidar.evaluation.review.projection.merge import assemble_projection
from raidar.evaluation.review.projection.sources import (
    JobSource,
    LockIndex,
    admit_jobs,
    discover_job_dirs,
    experiment_lock,
    load_job,
    load_sources,
    lock_from_document,
)
from raidar.evaluation.review.projection.spans import (
    PAYLOAD_CAP,
    SpanBuilder,
    payload,
    read_disk,
    to_ms,
)
from raidar.scoring.framework.metrics import DEFAULT_METRIC_REGISTRY
from raidar.scoring.framework.trial_inputs import BundleCache, TrialInputError
from tests.review_fixtures.projection.assessments import manifest_registry
from tests.review_fixtures.projection.generate import (
    JOBS_DIR,
    MEMORY_ROOT,
    REVIEW_ONLY_DIR,
    MemoryReview,
    memory_review_fixture,
)

FAILING = "synthetic-bugfix-gpt-5.5-low-04"
UNSCORED = "synthetic-bugfix-gpt-5.5-low-05"
PASSING = "synthetic-bugfix-gpt-5.5-medium-01"
ROOTS = [MEMORY_ROOT / JOBS_DIR, MEMORY_ROOT / REVIEW_ONLY_DIR]


@pytest.fixture(scope="module")
def review() -> MemoryReview:
    return memory_review_fixture()


def _project(
    review: MemoryReview,
    sources: tuple[JobSource, ...] | list[JobSource] | None = None,
    *,
    lookup: Any = None,
    read: Callable[[Path], str] | None = None,
) -> ReviewProjectionArtifact:
    epoch = project_sources(
        review.sources if sources is None else sources,
        review if lookup is None else lookup,
        read or review.read,
    )
    return assemble_projection(epoch, ROOTS)


@pytest.fixture(scope="module")
def projection(review: MemoryReview) -> ReviewProjectionArtifact:
    return _project(review)


def _detail(projection: ReviewProjectionArtifact, job_id: str) -> ReviewRunDetail:
    return projection.run_details[job_id]


def _span(detail: ReviewRunDetail, name: str) -> dict[str, Any]:
    span = next(span for span in detail.spans if span.name == name)
    return json.loads(span.output_payload or "{}")


def _with_records(
    source: JobSource, edit: Callable[[TrialRecord], TrialRecord], only: str | None = None
) -> JobSource:
    return replace(
        source,
        records=tuple(
            edit(record) if only is None or record.trial.trial_id == only else record
            for record in source.records
        ),
    )


def test_review_projection_helpers_cover_bounded_and_invalid_values() -> None:
    assert payload(None) is None
    text = payload("x" * (PAYLOAD_CAP + 1))
    assert text is not None
    assert text.endswith("...[truncated]")
    assert to_ms(12.9) == 12
    assert to_ms(None) is None
    assert to_ms("not-a-timestamp") is None


def test_projection_wire_contract_uses_problem_and_arm(
    projection: ReviewProjectionArtifact,
) -> None:
    document = json.loads(projection.model_dump_json())
    assert document["schema_version"] == 2
    assert not {"revision_diffs", "arm_diffs"} & document.keys()
    run = document["runs"][0]
    assert {"problem", "arm"} <= run.keys()
    assert not {"scenario", "revision"} & run.keys()
    experiment = document["experiments"][0]
    assert {"problem", "arm", "problem_meta"} <= experiment.keys()
    assert not {"scenario", "revision", "scenario_meta"} & experiment.keys()
    assert set(experiment["problem_meta"]) == {
        "display_name",
        "description",
        "difficulty",
        "languages",
        "checkpoints",
        "timeout_sec",
    }
    assert experiment["comparable_config"]["fingerprint"]


def test_frozen_concepts_map_to_problem_arm_and_agent(
    projection: ReviewProjectionArtifact,
) -> None:
    run = _detail(projection, PASSING).run
    assert run.problem == "synthetic/ledger-balance@1"
    assert run.arm == "gpt55-medium"
    assert run.agent_spec == "codex · openai/gpt-5.5 [medium] · one-shot@1"
    assert run.synthetic
    assert {
        (record.problem, record.aggregate["headline_policy"]) for record in projection.experiments
    } == {
        ("synthetic/ledger-balance@1", "scb-canonical@1"),
        ("synthetic/skill-coding@1", "scb-canonical@1"),
        ("synthetic/policy-mix@1", "scb-canonical-oneshot@1"),
        ("synthetic/policy-mix@1", "scb-canonical@1"),
        ("synthetic/review-only@1", "scb-canonical@1"),
    }
    skill = {
        (record.arm, record.harness, record.agent_spec)
        for record in projection.experiments
        if record.problem == "synthetic/skill-coding@1"
    }
    assert skill == {
        ("gpt55-low", "codex", "codex · openai/gpt-5.5 [low] · one-shot@1"),
        ("sonnet", "claude-code", "claude-code · anthropic/claude-sonnet-4.6 · one-shot@1"),
    }


def test_an_agent_override_replaces_the_arms_agent_labels(review: MemoryReview) -> None:
    source = review.source("synthetic-bugfix")
    overridden = replace(source, plan={**source.plan, "options": {"agent_override": "oracle"}})
    run = _detail(_project(review, [overridden]), PASSING).run
    assert (run.harness, run.model) == ("oracle", "reference solution")
    assert run.agent_spec == "oracle override · reference solution"
    assert run.experiment_id.endswith(":oracle")


def test_composite_is_the_headline_policy_score_over_one_hundred(
    projection: ReviewProjectionArtifact,
) -> None:
    components_by_metric = {metric: key for key, metric in COMPONENT_METRICS.items()}
    policies = set()
    for run in projection.runs:
        if run.unscored:
            continue
        detail = _detail(projection, run.id)
        quality = _span(detail, "scorer:code-quality@9")
        scoring = _span(detail, "scoring")
        components = {
            "C": Decimal(str(run.diagnostic_score)),
            **{
                components_by_metric[item["metric_id"]]: item["score"]
                for item in quality["metric_contributions"]
            },
        }
        policies.add(scoring["headline_policy"])
        expected = DEFAULT_METRIC_REGISTRY.get(scoring["headline_policy"]).score(
            checkpoints=scoring["checkpoints"], components=components
        )
        assert expected.score is not None
        assert run.composite_score == pytest.approx(float(expected.score / 100), abs=1e-6)
        assert run.quality_score == pytest.approx(float(expected.inertia or 0), abs=1e-9)
    assert policies == {"scb-canonical@1", "scb-canonical-oneshot@1"}


def test_failing_checkpoint_is_a_failed_gate_with_a_regression_finding(
    projection: ReviewProjectionArtifact,
) -> None:
    detail = _detail(projection, FAILING)
    run = detail.run
    assert run.failed_gates == ["checkpoint_3"]
    # C = mean case pass rate (1, 1, 1/3), the fork's C_p; solved/K would be 2/3.
    assert run.diagnostic_score == pytest.approx((1 + 1 + 1 / 3) / 3)
    assert 0.5 <= (run.composite_score or 0) < 0.75
    categories = {note.category for note in detail.annotations}
    assert {"failed-gate", "regressed-checkpoint", "quality-evidence"} <= categories
    gate = next(note for note in detail.annotations if note.category == "failed-gate")
    assert gate.span_id == next(
        span.id for span in detail.spans if span.name == "gate:checkpoint_3"
    )


def test_infrastructure_failure_is_unscored_with_harbors_reason_once(
    projection: ReviewProjectionArtifact,
) -> None:
    run = _detail(projection, UNSCORED).run
    assert run.unscored
    assert run.composite_score is None
    assert not run.valid
    assert run.status == "ERROR"
    assert "infra" in run.failed_gates
    mentions = [reason for reason in run.unscored_reasons if "agent exited" in reason]
    assert mentions == [
        "Trial exception: NonZeroAgentExitCodeError: "
        "the agent exited before verification finished (exit code 137)"
    ]
    assert any(
        reason.startswith("scb-canonical@1 ineligible: C") for reason in run.unscored_reasons
    )


def test_missing_assessments_leave_runs_unscored_never_zero(review: MemoryReview) -> None:
    storeless = AssessmentLookup([], manifest_registry())
    artifact = _project(review, lookup=storeless)
    for run in artifact.runs:
        assert run.unscored
        assert run.composite_score is None
        assert run.diagnostic_score is None
        assert any("no assessment store was given" in reason for reason in run.unscored_reasons)
    detail = _detail(artifact, PASSING)
    assert not any(span.name.startswith("scorer:") for span in detail.spans)
    assert _span(detail, "metric:problem_verbosity")["status"] == "missing"


def test_atif_steps_become_prompt_turn_and_tool_spans(projection: ReviewProjectionArtifact) -> None:
    detail = _detail(projection, FAILING)
    names = [span.name for span in detail.spans]
    assert names.count("user prompt") == 1
    assert names.count("checkpoint prompt") == 2
    tests = [span for span in detail.spans if span.name == "uv run pytest -q"]
    assert [span.status for span in tests] == ["OK", "OK", "ERROR"]
    turn = next(span for span in detail.spans if span.span_type == "LLM_GENERATION")
    assert turn.model == "openai/gpt-5.5"
    assert turn.input_tokens == 6000
    checkpoints = [span for span in detail.spans if span.name.startswith("checkpoint_")]
    parents = {span.parent_span_id for span in detail.spans if span.name == "user prompt"}
    assert parents == {checkpoints[0].id}


def test_an_atif_session_shows_system_prompts_unanswered_calls_and_embedded_subagents() -> None:
    agent = Agent(name="synthetic", version="0.0.0", model_name="m")
    inner = Trajectory(
        schema_version="ATIF-v1.7",
        trajectory_id="t-inner",
        session_id="inner",
        agent=agent,
        steps=[
            Step(step_id=1, source="user", message="delegated"),
            Step(step_id=2, source="agent", message="done", model_name="m"),
        ],
    )
    call = ToolCall(tool_call_id="c1", function_name="exec_command", arguments={"cmd": "ls"})
    outer = Trajectory(
        schema_version="ATIF-v1.7",
        session_id="outer",
        agent=agent,
        steps=[
            Step(step_id=1, source="system", message="rules"),
            Step(step_id=2, source="user", message="task"),
            Step(
                step_id=3,
                source="agent",
                message="working",
                model_name="m",
                tool_calls=[call],
                observation=Observation(results=[ObservationResult(content="listing")]),
            ),
            Step(
                step_id=4,
                source="agent",
                message="again",
                tool_calls=[ToolCall(tool_call_id="c2", function_name="read_file", arguments={})],
            ),
        ],
        subagent_trajectories=[inner],
    )
    builder = SpanBuilder("r")
    project_trajectory(
        builder,
        Path("/trial/steps/checkpoint_1/agent"),
        "checkpoint",
        first_checkpoint=True,
        read=lambda _: outer.model_dump_json(exclude_none=True),
    )
    assert [span.name for span in builder.spans] == [
        "system prompt",
        "user prompt",
        "assistant message",
        "ls",
        "assistant message",
        "read_file",
        "session inner",
        "subagent prompt",
        "assistant message",
    ]
    session = next(span for span in builder.spans if span.name == "session inner")
    nested = builder.spans[builder.spans.index(session) + 1 :]
    assert {span.parent_span_id for span in nested} == {session.id}
    assert builder.spans[3].output_payload == "listing"
    assert builder.spans[5].output_payload is None


def test_the_default_reader_reads_utf8_files_and_trial_inputs_name_unreadable_directories(
    review: MemoryReview, tmp_path: Path
) -> None:
    path = tmp_path / "result.json"
    path.write_text("café", encoding="utf-8")
    assert read_disk(path) == "café"
    with pytest.raises(FileNotFoundError):
        read_disk(tmp_path / "absent.json")
    with pytest.raises(TrialInputError):
        disk_inputs(tmp_path, review.source("synthetic-bugfix").assessment, BundleCache())


def test_an_experiment_without_its_task_document_is_labelled_by_its_problem(
    review: MemoryReview,
) -> None:
    def read(path: Path) -> str:
        if path.name == "task.toml":
            raise FileNotFoundError(path)
        return review.read(path)

    record = next(
        record
        for record in _project(review, read=read).experiments
        if record.problem == "synthetic/ledger-balance@1"
    )
    assert record.problem_meta.display_name == "synthetic/ledger-balance@1"
    assert record.problem_meta.difficulty == "unrated"


def test_unreadable_trajectory_is_one_error_span(review: MemoryReview) -> None:
    source = review.source("synthetic-skill")
    trial = next(r for r in source.records if r.trial.trial_id == "gpt-5.5-low-01")
    broken_path = source.job_dir / trial.trial_dir / "steps/checkpoint_1/agent/trajectory.json"

    def read(path: Path) -> str:
        return "{not atif" if path == broken_path else review.read(path)

    detail = _detail(_project(review, read=read), "synthetic-skill-gpt-5.5-low-01")
    broken = [span for span in detail.spans if span.name == "trajectory unreadable"]
    assert len(broken) == 1
    assert broken[0].status == "ERROR"


def test_a_trial_without_harbors_result_takes_its_timing_from_the_job(
    review: MemoryReview,
) -> None:
    started = to_ms(review.source("synthetic-bugfix").manifest["started_at"])

    def read(path: Path) -> str:
        if path.name == "result.json":
            raise FileNotFoundError(path)
        return review.read(path)

    run = _detail(_project(review, read=read), PASSING).run
    assert run.started_at == started
    assert run.duration_ms > 0
    assert not run.unscored


@pytest.mark.parametrize("damage", ["{not json", "[1, 2]"])
def test_an_unusable_harbor_result_is_treated_as_absent(review: MemoryReview, damage: str) -> None:
    def read(path: Path) -> str:
        return damage if path.name == "result.json" else review.read(path)

    run = _detail(_project(review, read=read), UNSCORED).run
    assert run.duration_ms > 0
    # Harbor's message is gone with its result; the trial record's own exception remains.
    assert "Trial exception: NonZeroAgentExitCodeError" in run.unscored_reasons


def test_policy_mismatch_marks_every_compared_record(
    projection: ReviewProjectionArtifact,
) -> None:
    records = [
        record for record in projection.experiments if record.problem != "synthetic/policy-mix@1"
    ]
    other = records[0].model_copy(
        update={
            "experiment_id": "other",
            "aggregate": {**records[0].aggregate, "headline_policy": "scb-canonical-oneshot@1"},
        }
    )
    skill = [r for r in records if r.problem == "synthetic/skill-coding@1"][0]
    unrecorded = skill.model_copy(update={"experiment_id": "unrecorded", "aggregate": {}})
    marked = mark_policy_mismatches([*records, other, unrecorded])
    flagged = {
        record.problem
        for record in marked
        if any(finding["category"] == MISMATCH_CATEGORY for finding in record.findings)
    }
    assert flagged == {records[0].problem, "synthetic/skill-coding@1"}
    finding = next(r for r in marked if r.experiment_id == "unrecorded").findings[-1]
    assert UNKNOWN_POLICY in json.dumps(finding)
    assert mark_policy_mismatches(records) == records


def test_the_fixture_family_mixing_policies_renders_the_mismatch(
    projection: ReviewProjectionArtifact,
) -> None:
    flagged = {
        (record.problem, record.aggregate["headline_policy"])
        for record in projection.experiments
        if any(finding["category"] == MISMATCH_CATEGORY for finding in record.findings)
    }
    assert flagged == {
        ("synthetic/policy-mix@1", "scb-canonical-oneshot@1"),
        ("synthetic/policy-mix@1", "scb-canonical@1"),
    }
    review_only = [run for run in projection.runs if run.problem == "synthetic/review-only@1"]
    assert len(review_only) == 3
    assert all(not run.unscored for run in review_only)


def test_colliding_review_ids_are_qualified_by_experiment(review: MemoryReview) -> None:
    source = review.source("synthetic-skill")
    copied = replace(source, manifest={**source.manifest, "experiment": "copy"})
    ids = set(_project(review, [source, copied]).run_details)
    assert "synthetic-skill-gpt-5.5-low-01" not in ids
    assert {
        "copy-synthetic-skill-gpt-5.5-low-01",
        "synthetic-skill-synthetic-skill-gpt-5.5-low-01",
    } <= ids


def test_an_excluded_checkpoint_is_neutral_in_review_and_keeps_its_raw_diagnostic(
    review: MemoryReview,
) -> None:
    def exclude_first(record: TrialRecord) -> TrialRecord:
        first, *rest = record.step_results
        excluded = first.model_copy(
            update={
                "checkpoint_state": CheckpointState.EXCLUDED,
                "rewards": {**first.rewards, "halt_gate": 1.0, "solvable_excluded": 1.0},
            }
        )
        return record.model_copy(update={"step_results": (excluded, *rest)})

    source = _with_records(review.source("synthetic-bugfix"), exclude_first, "gpt-5.5-medium-01")
    detail = _detail(_project(review, [source]), PASSING)
    gate = _span(detail, "gate:checkpoint_1")
    assert gate["checkpoint_state"] == "excluded"
    assert "gate:checkpoint_1" not in detail.run.failed_gates
    assert _span(detail, "metric:checkpoint_1")["passed"] is None
    metric = next(item for item in detail.run.metric_scores if item["metric_id"] == "checkpoint_1")
    assert metric["passed"] is None
    assert metric["score"] == 1.0
    contribution = _span(detail, "scorer:correctness@10")["metric_contributions"][0]
    assert contribution["score"] == 1.0


def test_truncated_dev_run_is_withheld_and_never_pooled_with_arm_evidence(
    review: MemoryReview,
) -> None:
    def truncate(record: TrialRecord) -> TrialRecord:
        *ran, unrun = record.step_results
        skipped = unrun.model_copy(update={"checkpoint_state": CheckpointState.NOT_ATTEMPTED})
        return record.model_copy(
            update={"truncated_to": 2, "job_id": "dev", "step_results": (*ran, skipped)}
        )

    full = review.source("synthetic-bugfix")
    dev = replace(
        full,
        manifest={**full.manifest, "job_id": "dev"},
        records=tuple(truncate(record) for record in full.records),
    )
    artifact = _project(review, [full, dev])
    medium = [
        r
        for r in artifact.experiments
        if r.agent_spec.startswith("codex · openai/gpt-5.5 [medium]")
    ]
    assert len(medium) == 2
    [truncated] = [r for r in medium if r.experiment_id.endswith(":first-2")]
    [arm] = [r for r in medium if r is not truncated]
    assert "dev job, first 2 checkpoint(s)" in truncated.agent_spec
    assert truncated.aggregate["run_count_scored"] == 0
    assert arm.aggregate["run_count_scored"] == 5
    run = _detail(artifact, "dev-gpt-5.5-medium-01").run
    assert run.unscored
    assert run.composite_score is None
    assert run.diagnostic_score is None
    assert run.unscored_reasons[0].startswith("Truncated run: only the first 2 checkpoint(s)")
    names = {span.name for span in _detail(artifact, "dev-gpt-5.5-medium-01").spans}
    assert {"checkpoint_1", "checkpoint_2"} <= names
    assert not {"checkpoint_3", "gate:checkpoint_3", "metric:checkpoint_3"} & names


def test_invalid_execution_withholds_the_headline_even_when_measured(
    review: MemoryReview,
) -> None:
    def fail_probe(record: TrialRecord) -> TrialRecord:
        checks = tuple(
            check.model_copy(
                update={"passed": False, "detail": "the per-step context reset left stale files"}
            )
            if check.name == "lab_checks"
            else check
            for check in record.validity.checks
        )
        return record.model_copy(update={"validity": ExecutionValidity(checks=checks)})

    source = _with_records(review.source("synthetic-skill"), fail_probe, "gpt-5.5-low-01")
    artifact = _project(review, [source])
    run = _detail(artifact, "synthetic-skill-gpt-5.5-low-01").run
    assert not run.valid
    assert run.unscored
    assert (run.composite_score, run.quality_score, run.diagnostic_score) == (None, None, None)
    assert run.unscored_reasons[0] == (
        "Execution invalid: lab_checks: the per-step context reset left stale files"
    )
    record = next(r for r in artifact.experiments if r.arm == "gpt55-low" and r.harness == "codex")
    assert record.aggregate["run_count_scored"] == 2
    assert "lab-checks" in run.failed_gates
    detail = _detail(artifact, "synthetic-skill-gpt-5.5-low-01")
    assert _span(detail, "metric:problem_verbosity")["status"] == "ok"


def test_a_failed_validity_check_without_a_detail_still_names_the_check(
    review: MemoryReview,
) -> None:
    def fail(record: TrialRecord) -> TrialRecord:
        checks = (*record.validity.checks, ValidityCheck(name="progression", passed=False))
        return record.model_copy(update={"validity": ExecutionValidity(checks=checks)})

    source = _with_records(review.source("synthetic-skill"), fail, "gpt-5.5-low-01")
    run = _detail(_project(review, [source]), "synthetic-skill-gpt-5.5-low-01").run
    assert run.unscored_reasons[0] == "Execution invalid: progression: failed"


def test_unknown_headline_policy_is_ineligible_not_zero() -> None:
    result = headline(
        "nope@1", ("checkpoint_1", "checkpoint_2", "checkpoint_3"), TrialAssessments()
    )
    assert result.composite is None
    assert result.score is None
    assert "unknown metric" in result.reasons[0]


def test_policy_shares_come_from_the_policy() -> None:
    for ref in ("scb-canonical@1", "scb-canonical-oneshot@1"):
        base, inertia = policy_shares(DEFAULT_METRIC_REGISTRY.get(ref))
        assert (base, inertia) == pytest.approx((0.70, 0.30))


def test_a_headline_scorer_the_experiment_never_declared_is_named_not_unassessed() -> None:
    missing = TrialAssessments(missing={"correctness@10": "no stored Assessment"})
    one = ("checkpoint_1",)
    result = headline("scb-canonical-oneshot@1", one, missing, declared=["correctness@10"])
    assert result.composite is None
    (reason,) = result.reasons
    assert UNDECLARED_MARKER in reason
    declared = headline(
        "scb-canonical-oneshot@1", one, missing, declared=["correctness@10", "code-quality@9"]
    )
    assert all("unassessed" in reason for reason in declared.reasons)


def test_unassessable_trial_inputs_are_reported_per_scorer(review: MemoryReview) -> None:
    def unreadable(*_: object) -> dict[str, object]:
        raise TrialInputError("snapshot changed since grading")

    lookup = AssessmentLookup([_EmptyStore()], manifest_registry(), load_inputs=unreadable)
    run = _detail(_project(review, lookup=lookup), PASSING).run
    assert run.unscored
    assert any("trial inputs cannot be assessed" in reason for reason in run.unscored_reasons)
    assert any("snapshot changed since grading" in reason for reason in run.unscored_reasons)


class _EmptyStore:
    def get(self, key: str) -> None:
        return None


def test_unassessed_hint_names_the_lock_whose_settings_key_the_assessment(
    review: MemoryReview,
) -> None:
    source = review.source("synthetic-bugfix")
    inputs = {"steps": [], "problem": "synthetic/ledger-balance@1"}
    lookup = AssessmentLookup([_EmptyStore()], manifest_registry(), load_inputs=lambda *_: inputs)
    run = _detail(_project(review, [source], lookup=lookup), PASSING).run
    hints = [reason for reason in run.unscored_reasons if "raidar assess --rescore" in reason]
    assert hints
    assert all(f"--lock {source.assessment_path}" in hint for hint in hints)
    assert all(reason.startswith("scb-canonical@1 unassessed:") for reason in hints)


def test_a_lookup_finds_the_assessment_its_key_names_and_reports_unregistered_scorers(
    review: MemoryReview,
) -> None:
    source = review.source("synthetic-bugfix")
    trial_dir = source.job_dir / source.records[0].trial_dir
    stored = review.assessments[trial_dir].found["correctness@10"]

    class Keyed:
        """A store holding one Assessment, whatever key is asked for."""

        def get(self, key: str) -> Any:
            return stored.model_copy(update={"key": key})

    lookup = AssessmentLookup(
        [_EmptyStore(), Keyed()], manifest_registry(), load_inputs=lambda *_: {}
    )
    found = lookup.for_trial(trial_dir, ["correctness@10"], source.assessment)
    assert found.get("correctness@10") is not None
    assert not found.missing
    unknown = lookup.for_trial(trial_dir, ["no-such-scorer@1"], source.assessment)
    assert "not registered" in unknown.missing["no-such-scorer@1"]


def test_run_without_its_lock_or_trial_records_is_skipped_not_fatal(
    review: MemoryReview, capsys: pytest.CaptureFixture[str]
) -> None:
    kept = review.source("synthetic-skill")
    empty = replace(review.source("synthetic-bugfix"), records=())
    admitted = admit_jobs(
        [
            (kept.job_dir, kept),
            (Path("/jobs/lockless"), AnalysisInputError("no experiment lock; pass it with --lock")),
            (empty.job_dir, empty),
        ]
    )
    assert [source.job_dir for source in admitted] == [kept.job_dir]
    err = capsys.readouterr().err
    assert "Skipping run, lock unavailable: /jobs/lockless" in err
    assert "pass it with --lock" in err
    assert f"Skipping run without trial records: {empty.job_dir}" in err


def test_a_historical_job_is_labelled_unmeasured_in_its_review(
    review: MemoryReview,
) -> None:
    source = review.source("synthetic-skill")
    (admitted,) = admit_jobs([(source.job_dir, source)])
    assert admitted.integrity_note is not None
    artifact = _project(review, [admitted])
    assert all(
        any(note.category == "provenance-unmeasured" for note in detail.annotations)
        for detail in artifact.run_details.values()
    )


def _modern_job(name: str, **manifest: Any) -> JobDir:
    document: dict[str, Any] = {
        "experiment": "exp",
        "lock_sha256": "abc",
        "job_id": name,
        "mechanics_epoch": "mechanics-v1",
        "runner": {"dirty": False, "toolchain_sha256": "toolchain", "execution_sha256": "run"},
        "recipes": {"pack": "pack", "graders": "grader"},
        "effective_limits": {"agent_s": 1800},
        "execution_target": {"name": "target-a", "docker": {"arch": "arm64"}},
    }
    document.update(manifest)
    return JobDir(Path("/jobs") / name, document, {}, ())


def _changed(change: str) -> JobDir:
    manifest = copy.deepcopy(_modern_job("two").manifest)
    if change == "target":
        manifest["execution_target"]["name"] = "target-b"
    elif change in {"toolchain", "execution"}:
        manifest["runner"][f"{change}_sha256"] = "another"
    elif change in {"pack", "grader"}:
        manifest["recipes"]["graders" if change == "grader" else "pack"] = "another"
    elif change == "limits":
        manifest["effective_limits"]["agent_s"] = 60
    elif change == "dirty":
        manifest["runner"]["dirty"] = True
    else:
        manifest["mechanics_epoch"] = "another-epoch"
    return JobDir(Path("/jobs/two"), manifest, {}, ())


@pytest.mark.parametrize(
    "change", ["target", "toolchain", "execution", "pack", "grader", "limits", "dirty", "epoch"]
)
def test_review_refuses_incompatible_provenance_before_combining_jobs(change: str) -> None:
    with pytest.raises(PoolingError, match="cannot be pooled"):
        check_review_jobs([_modern_job("one"), _changed(change)])


def test_review_combines_jobs_of_compatible_provenance_and_labels_historical_ones() -> None:
    assert check_review_jobs([_modern_job("one"), _modern_job("two")]) == {}
    historical = JobDir(
        Path("/jobs/old"), {"experiment": "exp", "lock_sha256": "abc", "job_id": "old"}, {}, ()
    )
    assert check_review_jobs([historical]) == {
        historical.path: "provenance unmeasured: historical job has no mechanics epoch"
    }


def test_merging_fails_closed_without_projectable_runs_or_with_duplicate_ids(
    review: MemoryReview, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="no projectable runs found under"):
        merge_review_projections(job_roots=[tmp_path / "empty"])
    with pytest.raises(ValueError, match="no projectable runs found under no source"):
        assemble_projection(EpochProjection(), [])
    epoch = project_sources(review.sources, review, review.read)
    duplicated = EpochProjection(epoch.experiments, [*epoch.details, epoch.details[0]])
    with pytest.raises(ValueError, match="duplicate job ids"):
        assemble_projection(duplicated, ROOTS)


def test_cli_requires_a_jobs_root_and_writes_the_projection_it_is_given(
    projection: ReviewProjectionArtifact,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match="--jobs-root"):
        main(["--output", str(tmp_path / "out.json")])
    requested: dict[str, list[Path]] = {}

    def merge(**roots: list[Path]) -> ReviewProjectionArtifact:
        requested.update(roots)
        return projection

    output = tmp_path / "nested" / "review.json"
    main(
        [
            "--jobs-root",
            str(tmp_path / "jobs"),
            "--store",
            str(tmp_path / "store"),
            "--lock",
            str(tmp_path / "x.lock.json"),
            "--output",
            str(output),
        ],
        merge=merge,
    )
    assert requested == {
        "job_roots": [(tmp_path / "jobs").resolve()],
        "stores": [(tmp_path / "store").resolve()],
        "locks": [(tmp_path / "x.lock.json").resolve()],
    }
    written = ReviewProjectionArtifact.model_validate_json(output.read_text())
    assert set(written.run_details) == set(projection.run_details)
    assert f"Projected {len(projection.runs)} run(s)" in capsys.readouterr().out


def test_the_module_entrypoint_runs_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["review_projection", "--output", "unused.json"])
    with pytest.raises(SystemExit, match="--jobs-root"):
        runpy.run_module("raidar.evaluation.review.projection", run_name="__main__")


def test_locks_are_read_sealed_found_by_hash_and_refused_when_edited(
    review: MemoryReview, tmp_path: Path
) -> None:
    document = review.locks["synthetic-bugfix"]
    experiment_file = tmp_path / "experiment.yaml"
    lock_path = default_lock_path(experiment_file)
    lock_path.write_text(json.dumps(document))
    lock = experiment_lock(lock_path)
    assert lock.sha256 == document["lock_sha256"]
    assert lock.metric == "scb-canonical@1"
    assert set(lock.arms) == {"gpt55-medium", "gpt55-high", "gpt55-low"}

    given = LockIndex([lock_path])
    assert given.find(lock.sha256, job_dir=tmp_path, experiment_file=None) == lock
    beside = LockIndex()
    assert beside.find(lock.sha256, job_dir=tmp_path, experiment_file=str(experiment_file)) == lock
    assert beside.find(lock.sha256, job_dir=tmp_path, experiment_file=str(experiment_file)) == lock
    with pytest.raises(AnalysisInputError, match="pass it with --lock"):
        beside.find("0" * 64, job_dir=tmp_path, experiment_file=str(experiment_file))
    with pytest.raises(AnalysisInputError, match="pass it with --lock"):
        LockIndex().find(lock.sha256, job_dir=tmp_path, experiment_file=None)

    edited: dict[str, Any] = copy.deepcopy(dict(document))
    edited["assessment"]["metric"]["ref"] = "scb-canonical-oneshot@1"
    lock_path.write_text(json.dumps(edited))
    with pytest.raises(AnalysisInputError, match="does not match its digest"):
        LockIndex([lock_path])
    with pytest.raises(AnalysisInputError, match="is not an experiment lock"):
        lock_from_document({}, None)


def test_job_directories_are_found_at_the_root_or_one_or_two_levels_below(
    tmp_path: Path,
) -> None:
    job = {"kind": "raidar-job"}
    layout = {
        "job.json": job,
        "a/job.json": job,
        "b/c/job.json": job,
        "b/c/d/job.json": job,
        "other/job.json": {"kind": "something-else"},
    }
    for name, content in layout.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(json.dumps(content))
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken/job.json").write_text("{not json")
    (tmp_path / "list").mkdir()
    (tmp_path / "list/job.json").write_text("[]")
    assert discover_job_dirs([tmp_path, tmp_path]) == sorted(
        (tmp_path / name).resolve() for name in ("", "a", "b/c")
    )


def test_loading_a_job_reads_its_manifest_plan_and_lock_and_skips_jobs_without_records(
    review: MemoryReview, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = review.source("synthetic-bugfix")
    experiment_file = tmp_path / "experiment.yaml"
    default_lock_path(experiment_file).write_text(json.dumps(review.locks["synthetic-bugfix"]))
    (tmp_path / "job.json").write_text(
        json.dumps({**source.manifest, "experiment_file": str(experiment_file)})
    )
    (tmp_path / "plan.json").write_text(json.dumps(source.plan))
    loaded = load_job(tmp_path, LockIndex())
    assert loaded.lock.sha256 == source.lock.sha256
    assert loaded.experiment == "synthetic-bugfix"
    assert loaded.records == ()
    assert loaded.assessment_path == default_lock_path(experiment_file).resolve()
    assert load_sources([tmp_path], []) == []
    assert "Skipping run without trial records" in capsys.readouterr().err
    default_lock_path(experiment_file).unlink()
    assert load_sources([tmp_path], []) == []
    assert "Skipping run, lock unavailable" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("arguments", "label"),
    [
        ({"command": ["git", "status"]}, "git status"),
        ({"input": 'await tools.exec_command({cmd:"ls -la",workdir:"/app"})'}, "ls -la"),
        ({"path": "notes.md"}, "apply_patch"),
    ],
)
def test_tool_spans_are_named_by_their_command(arguments: dict[str, object], label: str) -> None:
    call = ToolCall(tool_call_id="c1", function_name="apply_patch", arguments=arguments)
    assert tool_label(call) == label


CODEX_OK = (
    "[{'type': 'input_text', 'text': 'Script completed\\nWall time 0.1 seconds\\nOutput:\\n'}, "
    "{'type': 'input_text', 'text': 'assert data[\"exit_code\"] == 2\\n1 passed\\n'}]"
)
CODEX_FAILED = (
    "[{'type': 'input_text', 'text': 'Script failed\\nWall time 0.0 seconds\\nOutput:\\n'}, "
    "{'type': 'input_text', 'text': 'Script error:\\napply_patch verification failed'}]"
)


def test_codex_tool_results_are_decoded_and_statused_by_their_header() -> None:
    ok = _text(CODEX_OK)
    assert ok.splitlines()[:4] == [
        "Script completed",
        "Wall time 0.1 seconds",
        "Output:",
        'assert data["exit_code"] == 2',
    ]
    assert _tool_status(ok) == "OK"  # body text mentioning exit_code is the program's
    failed = _text(CODEX_FAILED)
    assert failed.startswith("Script failed\n")
    assert _tool_status(failed) == "ERROR"
    assert _tool_status("Process exited with code 1\nboom") == "ERROR"
    assert _tool_status("Process exited with code 0\nok") == "OK"
    assert _tool_status("line one\nline two\nline three\nexit code 2") == "OK"
    assert _text("[{'type': not python") == "[{'type': not python"


def test_message_content_parts_join_their_texts_and_other_messages_have_none() -> None:
    class Part:
        def __init__(self, text: str | None) -> None:
            self.text = text

    assert _text([Part("one"), Part(None), Part("two")]) == "one\ntwo"
    assert _text(None) == ""
    assert _text("[{'type'}]") == "[{'type'}]"


class _WithoutQuality:
    """The review's stored Assessments, minus the code-quality ones."""

    def __init__(self, review: MemoryReview) -> None:
        self._review = review

    def for_trial(
        self, trial_dir: Path, scorer_refs: Any, settings: Any, *, config_path: Any = None
    ) -> TrialAssessments:
        stored = self._review.assessments[trial_dir].found
        return TrialAssessments(
            found={ref: item for ref, item in stored.items() if ref != "code-quality@9"},
            missing={"code-quality@9": "no stored code-quality@9 Assessment"},
        )


def test_a_headline_needing_a_scorer_the_experiment_does_not_declare_is_flagged(
    review: MemoryReview,
) -> None:
    source = review.source("synthetic-bugfix")
    spec = source.lock.spec
    declared = spec.assessment.model_copy(update={"scorers": ["correctness@10"]})
    narrowed = replace(
        source, lock=replace(source.lock, spec=spec.model_copy(update={"assessment": declared}))
    )
    artifact = _project(review, [narrowed], lookup=_WithoutQuality(review))
    run = _detail(artifact, PASSING).run
    assert run.unscored
    assert any(UNDECLARED_MARKER in reason for reason in run.unscored_reasons)
    flagged = [
        record
        for record in artifact.experiments
        if any(finding["category"] == "policy-scorer-mismatch" for finding in record.findings)
    ]
    assert {record.arm for record in flagged} == {"gpt55-medium", "gpt55-high", "gpt55-low"}


def test_a_lock_naming_an_unknown_headline_metric_leaves_runs_unscored(
    review: MemoryReview,
) -> None:
    source = review.source("synthetic-bugfix")
    unknown = replace(source, lock=replace(source.lock, metric="nope@1"))
    artifact = _project(review, [unknown])
    assert all(run.unscored for run in artifact.runs)
    assert all(any("nope@1" in reason for reason in run.unscored_reasons) for run in artifact.runs)
    assert {record.aggregate["run_count_scored"] for record in artifact.experiments} == {0}


def test_a_failed_checkpoint_without_a_verifier_record_says_there_is_no_record(
    review: MemoryReview,
) -> None:
    def drop_record(record: TrialRecord) -> TrialRecord:
        *earlier, last = record.step_results
        return record.model_copy(
            update={"step_results": (*earlier, last.model_copy(update={"verifier_record": None}))}
        )

    source = _with_records(review.source("synthetic-bugfix"), drop_record, "gpt-5.5-low-04")
    detail = _detail(_project(review, [source]), FAILING)
    gate = next(note for note in detail.annotations if note.category == "failed-gate")
    assert "no verifier record" in gate.note


def test_a_trial_without_validity_checks_has_no_validity_gates(review: MemoryReview) -> None:
    def no_checks(record: TrialRecord) -> TrialRecord:
        return record.model_copy(update={"validity": ExecutionValidity(checks=())})

    source = _with_records(review.source("synthetic-bugfix"), no_checks, "gpt-5.5-medium-01")
    detail = _detail(_project(review, [source]), PASSING)
    assert not {"gate:infra", "gate:lab-checks"} & {span.name for span in detail.spans}
    assert detail.run.valid


def test_a_trial_that_raised_without_a_failed_check_is_still_withheld(
    review: MemoryReview,
) -> None:
    record = (
        review.source("synthetic-skill").records[0].model_copy(update={"trial_exception": "Boom"})
    )
    assert withheld_reasons(record) == ("Execution invalid: the trial raised an exception",)


def test_a_headline_without_a_declaration_to_check_reports_unassessed_scorers() -> None:
    missing = TrialAssessments(missing={"correctness@10": "no store", "code-quality@9": "no store"})
    result = headline("scb-canonical@1", ("checkpoint_1", "checkpoint_2", "checkpoint_3"), missing)
    assert result.composite is None
    assert [reason.split(": ")[0] for reason in result.reasons] == [
        "scb-canonical@1 unassessed",
        "scb-canonical@1 unassessed",
    ]


def test_a_policy_without_quality_components_needs_only_correctness() -> None:
    no_quality = SimpleNamespace(inertia_weights={})
    assert needed_scorers(no_quality) == ("correctness@10",)  # type: ignore[arg-type]
    assert "code-quality@9" in needed_scorers(DEFAULT_METRIC_REGISTRY.get("scb-canonical@1"))
