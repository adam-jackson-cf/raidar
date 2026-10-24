"""Behaviour of the experiment-epoch review projection.

The synthetic fixture is written with the production models and scored by the
real correctness/resource-usage scorers, so these tests exercise the same path
a real run takes: job dir + lock + Assessment store -> frozen projection.
"""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from harbor.models.trajectories import ToolCall

from raidar.contract.review import ReviewProjectionArtifact, ReviewRunDetail
from raidar.design.experiment.schema import ExperimentSpec
from raidar.evaluation.analysis.sources import AnalysisInputError
from raidar.evaluation.review.projection import main, merge_review_projections
from raidar.evaluation.review.projection.assessments import TrialAssessments
from raidar.evaluation.review.projection.atif import tool_label
from raidar.evaluation.review.projection.comparability import (
    MISMATCH_CATEGORY,
    UNKNOWN_POLICY,
    mark_policy_mismatches,
)
from raidar.evaluation.review.projection.conditions import ArmSide, condition_diff
from raidar.evaluation.review.projection.headline import COMPONENT_METRICS, headline, policy_shares
from raidar.evaluation.review.projection.merge import project_experiment_epoch
from raidar.evaluation.review.projection.sources import LockIndex, load_job
from raidar.scoring.framework.metrics import DEFAULT_METRIC_REGISTRY
from tests.review_fixtures.projection.generate import (
    REVIEW_ONLY_DIR,
    generate_review_fixture,
)

FAILING = "synthetic-bugfix-gpt-5.5-low-04"
UNSCORED = "synthetic-bugfix-gpt-5.5-low-05"
PASSING = "synthetic-bugfix-gpt-5.5-medium-01"


@pytest.fixture(scope="module")
def fixture_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("review-fixture")
    generate_review_fixture(root)
    return root


@pytest.fixture(scope="module")
def projection(fixture_root: Path) -> ReviewProjectionArtifact:
    return merge_review_projections(
        job_roots=[fixture_root / "jobs"], stores=[fixture_root / "store"]
    )


def _detail(projection: ReviewProjectionArtifact, job_id: str) -> ReviewRunDetail:
    return projection.run_details[job_id]


def _span(detail: ReviewRunDetail, name: str) -> dict[str, Any]:
    span = next(span for span in detail.spans if span.name == name)
    return json.loads(span.output_payload or "{}")


def test_frozen_concepts_map_to_problem_condition_and_agent(
    projection: ReviewProjectionArtifact,
) -> None:
    run = _detail(projection, PASSING).run
    assert run.problem == "synthetic/ledger-balance@1"
    assert run.arm == "baseline"
    assert run.agent_spec == "codex · openai/gpt-5.5 [medium]"
    assert run.synthetic
    families = {(record.problem, record.arm) for record in projection.experiments}
    assert ("synthetic/skill-coding@1", "skills") in families
    assert {
        (record.problem, record.aggregate["headline_policy"]) for record in projection.experiments
    } == {
        ("synthetic/ledger-balance@1", "scb-canonical@1"),
        ("synthetic/skill-coding@1", "scb-canonical@1"),
        ("synthetic/policy-mix@1", "scb-canonical-oneshot@1"),
        ("synthetic/policy-mix@1", "scb-canonical@1"),
    }


def test_arms_of_one_condition_on_different_harnesses_share_an_arm_label(
    fixture_root: Path, projection: ReviewProjectionArtifact
) -> None:
    trials = fixture_root / "jobs/synthetic-skill/synthetic-skill/trial-records"
    digests = {
        json.loads(path.read_text())["trial"]["hashes"]["condition"]
        for path in trials.glob("baseline-*-01.json")
    }
    assert len(digests) == 2  # codex and claude-code compile the condition differently
    labels = {
        (record.harness, record.arm)
        for record in projection.experiments
        if record.problem == "synthetic/skill-coding@1"
    }
    assert labels == {
        ("codex", "baseline"),
        ("claude-code", "baseline"),
        ("codex", "skills"),
        ("claude-code", "skills"),
    }


def test_composite_is_the_headline_policy_score_over_one_hundred(
    projection: ReviewProjectionArtifact,
) -> None:
    components_by_metric = {metric: key for key, metric in COMPONENT_METRICS.items()}
    policies = set()
    for run in projection.runs:
        if run.unscored:
            continue
        detail = _detail(projection, run.id)
        quality = _span(detail, "scorer:code-quality@3")
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


def test_missing_assessments_leave_runs_unscored_never_zero(fixture_root: Path) -> None:
    artifact = merge_review_projections(job_roots=[fixture_root / "jobs"])
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


def test_unreadable_trial_is_one_error_span(fixture_root: Path, tmp_path: Path) -> None:
    runs = tmp_path / "jobs"
    shutil.copytree(fixture_root / "jobs" / "synthetic-skill", runs / "synthetic-skill")
    trace = runs / "synthetic-skill/synthetic-skill/trials/skills-gpt-01/steps/checkpoint_1/agent"
    (trace / "trajectory.json").write_text("{not atif")
    detail = _detail(merge_review_projections(job_roots=[runs]), "synthetic-skill-skills-gpt-01")
    broken = [span for span in detail.spans if span.name == "trajectory unreadable"]
    assert len(broken) == 1
    assert broken[0].status == "ERROR"


def test_condition_diff_pairs_baseline_with_the_same_agent(
    projection: ReviewProjectionArtifact,
) -> None:
    [diff] = projection.arm_diffs
    assert diff.key == "synthetic/skill-coding@1:baseline->skills"
    assert diff.summary == ["arm baseline-gpt -> skills-gpt", "+ component skills@1"]
    assert diff.comparable_warnings == []
    assert diff.change_categories == ["rules"]
    assert "workdir/skills/tdd/SKILL.md" in diff.sections
    assert set(diff.files) == {"condition", "prompt"}


def _spec(factors: list[str]) -> ExperimentSpec:
    return ExperimentSpec.model_validate(
        {
            "schema_version": 2,
            "kind": "ab-test",
            "id": "guarded",
            "problems": [{"ref": "synthetic/p@1"}],
            "runtime": "python",
            "agents": [
                {
                    "id": "a",
                    "name": "codex",
                    "provider": "openai",
                    "model": "m1",
                    "version": "1.0",
                },
                {
                    "id": "b",
                    "name": "codex",
                    "provider": "openai",
                    "model": "m2",
                    "version": "1.0",
                },
            ],
            "factors": factors,
            "conditions": [
                {"id": "base"},
                {"id": "rules", "components": [{"use": "rules-file@1"}]},
            ],
            "arms": [
                {"id": "base", "condition": "base", "agent": "a"},
                {"id": "rules", "condition": "rules", "agent": "b"},
            ],
            "assessment": {"scorers": ["correctness@1"]},
            "analysis": {"baseline_arm": "base", "attempts": 1, "seed": 0},
        }
    )


@pytest.mark.parametrize(
    ("factors", "undeclared"),
    [(["condition", "agent"], []), (["condition"], ["agent.model"])],
)
def test_condition_diff_warns_on_nouns_outside_the_declared_factors(
    factors: list[str], undeclared: list[str]
) -> None:
    spec = _spec(factors)
    base, rules = spec.arms
    diff = condition_diff(
        spec,
        "synthetic/p@1",
        ArmSide(base, "base", [], None),
        ArmSide(rules, "rules", [{"use": "rules-file@1", "files": {}}], None),
    )
    assert [warning.split(" ")[0] for warning in diff.comparable_warnings] == undeclared
    assert diff.change_categories == ["metadata", "rules"]


def test_policy_mismatch_marks_every_compared_record_and_diff(
    projection: ReviewProjectionArtifact,
) -> None:
    records = list(projection.experiments)
    other = records[0].model_copy(
        update={
            "experiment_id": "other",
            "aggregate": {**records[0].aggregate, "headline_policy": "scb-canonical-oneshot@1"},
        }
    )
    skill = [r for r in records if r.problem == "synthetic/skill-coding@1"][0]
    unrecorded = skill.model_copy(update={"experiment_id": "unrecorded", "aggregate": {}})
    single = [r for r in records if r.problem != "synthetic/policy-mix@1"]
    marked, diffs = mark_policy_mismatches([*single, other, unrecorded], projection.arm_diffs)
    flagged = {
        record.problem
        for record in marked
        if any(finding["category"] == MISMATCH_CATEGORY for finding in record.findings)
    }
    assert flagged == {"synthetic/ledger-balance@1", "synthetic/skill-coding@1"}
    assert UNKNOWN_POLICY in diffs[0].comparable_warnings[-1]
    unchanged, same = mark_policy_mismatches(single, projection.arm_diffs)
    assert unchanged == single
    assert same == projection.arm_diffs


def test_run_without_its_lock_is_skipped_not_fatal(
    fixture_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _copy_run(fixture_root, tmp_path, "synthetic-bugfix")
    shutil.copytree(
        fixture_root / "jobs/synthetic-skill/synthetic-skill",
        tmp_path / "jobs/synthetic-skill/synthetic-skill",
    )
    lock = run / "experiment.lock.json"
    moved = tmp_path / "elsewhere.lock.json"
    lock.rename(moved)
    skipped = project_experiment_epoch([tmp_path / "jobs"])
    assert "pass it with --lock" in capsys.readouterr().err
    assert skipped.details  # the other run is still projected
    assert not any("bugfix" in detail.run.id for detail in skipped.details)
    with pytest.raises(AnalysisInputError, match="pass it with --lock"):
        load_job(run, LockIndex())
    epoch = project_experiment_epoch([tmp_path / "jobs/synthetic-bugfix"], locks=[moved])
    assert len(epoch.details) == 5 * 3


def test_colliding_review_ids_are_qualified_by_experiment(
    fixture_root: Path, tmp_path: Path
) -> None:
    source = fixture_root / "jobs/synthetic-skill/synthetic-skill"
    for experiment in ("synthetic-skill", "copy"):
        shutil.copytree(source, tmp_path / "jobs" / experiment / "synthetic-skill")
    manifest = tmp_path / "jobs/copy/synthetic-skill/job.json"
    data = json.loads(manifest.read_text())
    manifest.write_text(json.dumps({**data, "experiment": "copy"}))
    ids = set(merge_review_projections(job_roots=[tmp_path / "jobs"]).run_details)
    assert "synthetic-skill-skills-gpt-01" not in ids
    assert {
        "copy-synthetic-skill-skills-gpt-01",
        "synthetic-skill-synthetic-skill-skills-gpt-01",
    } <= ids


def test_merge_combines_run_roots_and_fails_closed_without_sources(
    fixture_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(ValueError, match="no projectable runs found under"):
        merge_review_projections(job_roots=[tmp_path])
    empty = tmp_path / "jobs/pending/r1"
    empty.mkdir(parents=True)
    shutil.copy(fixture_root / "jobs/synthetic-skill/synthetic-skill/job.json", empty / "job.json")
    shutil.copy(
        fixture_root / "jobs/synthetic-skill/synthetic-skill/plan.json", empty / "plan.json"
    )
    shutil.copy(
        fixture_root / "jobs/synthetic-skill/synthetic-skill/experiment.lock.json",
        empty / "experiment.lock.json",
    )
    output = tmp_path / "out.json"
    main(
        [
            "--jobs-root",
            str(fixture_root / "jobs"),
            "--jobs-root",
            str(tmp_path / "jobs"),
            "--store",
            str(fixture_root / "store"),
            "--output",
            str(output),
        ]
    )
    artifact = ReviewProjectionArtifact.model_validate_json(output.read_text())
    assert len(artifact.runs) == 33
    assert len(artifact.experiments) == 9
    captured = capsys.readouterr()
    assert "Skipping run without trial records" in captured.err
    with pytest.raises(SystemExit):
        main(["--output", str(output)])


def test_unknown_headline_policy_is_ineligible_not_zero() -> None:
    result = headline(
        "nope@1", ("checkpoint_1", "checkpoint_2", "checkpoint_3"), TrialAssessments()
    )
    assert result.composite is None
    assert result.score is None
    assert result.reasons
    assert "unknown metric" in result.reasons[0]


def test_unassessable_trial_inputs_are_reported_per_scorer(
    fixture_root: Path, tmp_path: Path
) -> None:
    runs = tmp_path / "jobs"
    shutil.copytree(fixture_root / "jobs/synthetic-skill", runs / "synthetic-skill")
    shutil.copytree(fixture_root / "bundles", tmp_path / "bundles")
    moved = tmp_path / "bundles/synthetic-skill/oracle-bundle.json"
    moved.write_text(moved.read_text().replace("python:3.12-slim", "python:3.13-slim"))
    for config in runs.glob("synthetic-skill/synthetic-skill/trials/*/config.json"):
        config.write_text(config.read_text().replace(str(fixture_root), str(tmp_path)))
    for record in runs.glob(
        "synthetic-skill/synthetic-skill/trials/*/raidar-oracle/steps/*/record.json"
    ):
        record.write_text(record.read_text().replace(str(fixture_root), str(tmp_path)))
    run = merge_review_projections(job_roots=[runs], stores=[fixture_root / "store"]).runs[0]
    assert run.unscored
    assert any("trial inputs cannot be assessed" in reason for reason in run.unscored_reasons)


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


def _copy_run(fixture_root: Path, tmp_path: Path, experiment: str) -> Path:
    """A copy of a fixture run whose manifest names the copy's own experiment file and lock."""
    run = tmp_path / "jobs" / experiment / experiment
    shutil.copytree(fixture_root / "jobs" / experiment / experiment, run)
    manifest = json.loads((run / "job.json").read_text())
    (run / "job.json").write_text(
        json.dumps({**manifest, "experiment_file": str(run / "experiment.yaml")})
    )
    return run


def _edit_records(run: Path, edit: Any, pattern: str = "*.json") -> None:
    for path in sorted((run / "trial-records").glob(pattern)):
        record = json.loads(path.read_text())
        edit(record)
        path.write_text(json.dumps(record))


def test_unassessed_hint_names_the_lock_whose_settings_key_the_assessment(
    fixture_root: Path, tmp_path: Path
) -> None:
    artifact = merge_review_projections(
        job_roots=[fixture_root / "jobs"], stores=[tmp_path / "empty-store"]
    )
    run = _detail(artifact, PASSING).run
    lock = (fixture_root / "jobs/synthetic-bugfix/synthetic-bugfix/experiment.lock.json").resolve()
    hints = [reason for reason in run.unscored_reasons if "raidar assess --rescore" in reason]
    assert hints
    assert all(f"--lock {lock}" in hint for hint in hints)
    assert all(reason.startswith("scb-canonical@1 unassessed:") for reason in hints)


def test_truncated_dev_run_is_withheld_and_never_pooled_with_arm_evidence(
    fixture_root: Path, tmp_path: Path
) -> None:
    full = _copy_run(fixture_root, tmp_path, "synthetic-bugfix")
    dev = tmp_path / "jobs/synthetic-bugfix/dev"
    shutil.copytree(full, dev)
    manifest = json.loads((dev / "job.json").read_text())
    (dev / "job.json").write_text(json.dumps({**manifest, "job_id": "dev"}))
    plan = json.loads((dev / "plan.json").read_text())
    (dev / "plan.json").write_text(json.dumps({**plan, "options": {"max_checkpoints": 2}}))
    _edit_records(dev, lambda record: record.update(truncated_to=2))
    artifact = merge_review_projections(
        job_roots=[tmp_path / "jobs"], stores=[fixture_root / "store"]
    )
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


def test_invalid_execution_withholds_the_headline_even_when_measured(
    fixture_root: Path, tmp_path: Path
) -> None:
    job_dir = _copy_run(fixture_root, tmp_path, "synthetic-skill")

    def fail_probe(record: dict[str, Any]) -> None:
        for check in record["validity"]["checks"]:
            if check["name"] == "component_probes":
                check.update(passed=False, detail="AGENTS.md lines not in the harness context")

    _edit_records(job_dir, fail_probe, "skills-gpt-01.json")
    artifact = merge_review_projections(
        job_roots=[tmp_path / "jobs"], stores=[fixture_root / "store"]
    )
    run = _detail(artifact, "synthetic-skill-skills-gpt-01").run
    assert not run.valid
    assert run.unscored
    assert (run.composite_score, run.quality_score, run.diagnostic_score) == (None, None, None)
    assert run.unscored_reasons[0] == (
        "Execution invalid: component_probes: AGENTS.md lines not in the harness context"
    )
    record = next(r for r in artifact.experiments if r.arm == "skills" and r.harness == "codex")
    assert record.aggregate["run_count_scored"] == 2
    detail = _detail(artifact, "synthetic-skill-skills-gpt-01")
    assert _span(detail, "metric:problem_verbosity")["status"] == "ok"


def test_lock_whose_body_was_edited_is_refused(fixture_root: Path, tmp_path: Path) -> None:
    job_dir = _copy_run(fixture_root, tmp_path, "synthetic-bugfix")
    lock_path = job_dir / "experiment.lock.json"
    lock = json.loads(lock_path.read_text())
    lock["assessment"]["metric"]["ref"] = "scb-canonical-oneshot@1"
    lock_path.write_text(json.dumps(lock))
    with pytest.raises(AnalysisInputError, match="section assessment does not match its digest"):
        LockIndex([lock_path])
    # Found beside the experiment file, the edited lock is refused and the run is not projected.
    assert not project_experiment_epoch([tmp_path / "jobs"]).details


def test_policy_shares_come_from_the_policy() -> None:
    for ref in ("scb-canonical@1", "scb-canonical-oneshot@1"):
        base, inertia = policy_shares(DEFAULT_METRIC_REGISTRY.get(ref))
        assert (base, inertia) == pytest.approx((0.70, 0.30))


def test_fixture_family_mixing_policies_renders_the_mismatch(fixture_root: Path) -> None:
    artifact = merge_review_projections(
        job_roots=[fixture_root / "jobs", fixture_root / REVIEW_ONLY_DIR],
        stores=[fixture_root / "store"],
    )
    flagged = {
        (record.problem, record.aggregate["headline_policy"])
        for record in artifact.experiments
        if any(finding["category"] == MISMATCH_CATEGORY for finding in record.findings)
    }
    assert flagged == {
        ("synthetic/policy-mix@1", "scb-canonical-oneshot@1"),
        ("synthetic/policy-mix@1", "scb-canonical@1"),
    }
    review_only = [run for run in artifact.runs if run.problem == "synthetic/review-only@1"]
    assert len(review_only) == 3
    assert all(not run.unscored for run in review_only)


def test_following_the_unassessed_hint_stores_the_assessment_the_review_finds(
    fixture_root: Path, tmp_path: Path
) -> None:
    from click.testing import CliRunner

    from raidar.cli.assess import assess

    job_dir = _copy_run(fixture_root, tmp_path, "synthetic-bugfix")
    config = job_dir / "assessment-config.yaml"
    config.write_text(
        config.read_text().replace(
            "scorers: {}", "scorers:\n  correctness@1:\n    progression: continue"
        )
    )
    runs, store = tmp_path / "jobs", tmp_path / "store"
    before = merge_review_projections(job_roots=[runs], stores=[store])
    [hint] = [
        reason
        for reason in _detail(before, PASSING).run.unscored_reasons
        if "correctness@1" in reason and "raidar assess" in reason
    ]
    command = hint.split("`")[1].replace("<store>", str(store)).split()
    result = CliRunner().invoke(assess, command[2:])
    assert result.exit_code == 0, result.output
    after = merge_review_projections(job_roots=[runs], stores=[store])
    scoring = _span(_detail(after, PASSING), "scoring")
    assert "correctness@1" in scoring["assessments"]
    assert "correctness@1" not in scoring["not_assessed"]


def test_a_headline_scorer_the_experiment_never_declared_is_named_not_unassessed() -> None:
    from raidar.evaluation.review.projection.assessments import TrialAssessments
    from raidar.evaluation.review.projection.headline import UNDECLARED_MARKER, headline

    missing = TrialAssessments(missing={"correctness@1": "no stored Assessment"})
    one = ("checkpoint_1",)
    result = headline("scb-canonical-oneshot@1", one, missing, declared=["correctness@1"])
    assert result.composite is None
    (reason,) = result.reasons
    assert "needs code-quality@3" in reason
    assert UNDECLARED_MARKER in reason
    declared = headline(
        "scb-canonical-oneshot@1", one, missing, declared=["correctness@1", "code-quality@3"]
    )
    assert all("unassessed" in reason for reason in declared.reasons)


CODEX_OK = (
    "[{'type': 'input_text', 'text': 'Script completed\\nWall time 0.1 seconds\\nOutput:\\n'}, "
    "{'type': 'input_text', 'text': 'assert data[\"exit_code\"] == 2\\n1 passed\\n'}]"
)
CODEX_FAILED = (
    "[{'type': 'input_text', 'text': 'Script failed\\nWall time 0.0 seconds\\nOutput:\\n'}, "
    "{'type': 'input_text', 'text': 'Script error:\\napply_patch verification failed'}]"
)


def test_codex_tool_results_are_decoded_and_statused_by_their_header() -> None:
    from raidar.evaluation.review.projection.atif import _text, _tool_status

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
