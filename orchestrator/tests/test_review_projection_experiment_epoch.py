"""Behaviour of the experiment-epoch review projection (plan K1, K3).

The synthetic fixture is written with the production models and scored by the
real correctness/resource-usage scorers, so these tests exercise the same path
a real run takes: run dir + lock + Assessment store -> frozen projection.
"""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from harbor.models.trajectories import ToolCall

from raidar.review_projection import main, merge_review_projections
from raidar.review_projection.assessments import TrialAssessments
from raidar.review_projection.atif import tool_label
from raidar.review_projection.comparability import (
    MISMATCH_CATEGORY,
    SCENARIO_EPOCH_POLICY,
    mark_policy_mismatches,
)
from raidar.review_projection.conditions import ArmSide, condition_diff
from raidar.review_projection.fixture import SCENARIO_EPOCH_DIR, generate_review_fixture
from raidar.review_projection.headline import COMPONENT_METRICS, headline, policy_shares
from raidar.review_projection.merge import project_experiment_epoch
from raidar.review_projection.sources import RunSourceError
from raidar.schemas.review import ReviewProjectionArtifact, ReviewRunDetail
from raidar.spec.policies import DEFAULT_POLICY_REGISTRY
from raidar.spec.schema import ExperimentSpec

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
        run_roots=[fixture_root / "runs"], stores=[fixture_root / "store"]
    )


def _detail(projection: ReviewProjectionArtifact, run_id: str) -> ReviewRunDetail:
    return projection.run_details[run_id]


def _span(detail: ReviewRunDetail, name: str) -> dict[str, Any]:
    span = next(span for span in detail.spans if span.name == name)
    return json.loads(span.output_payload or "{}")


def test_frozen_concepts_map_to_problem_condition_and_agent(
    projection: ReviewProjectionArtifact,
) -> None:
    run = _detail(projection, PASSING).run
    assert run.scenario == "synthetic/ledger-balance@1"
    assert run.revision == "baseline"
    assert run.agent_spec == "codex-cli · openai/gpt-5.5 [medium]"
    assert run.synthetic
    families = {(record.scenario, record.revision) for record in projection.experiments}
    assert ("synthetic/skill-coding@1", "skills") in families
    assert {
        record.scenario: record.aggregate["headline_policy"] for record in projection.experiments
    } == {
        "synthetic/ledger-balance@1": "scb-canonical@1",
        "synthetic/skill-coding@1": "scb-canonical@1",
        "synthetic/policy-mix@1": "scb-canonical-oneshot@1",
    }


def test_arms_of_one_condition_on_different_harnesses_share_a_revision(
    fixture_root: Path, projection: ReviewProjectionArtifact
) -> None:
    trajectories = fixture_root / "runs/synthetic-skill/synthetic-skill/trajectories"
    digests = {
        json.loads(path.read_text())["trial"]["hashes"]["condition"]
        for path in trajectories.glob("baseline-*-01.json")
    }
    assert len(digests) == 2  # codex-cli and claude-code compile the condition differently
    revisions = {
        (record.harness, record.revision)
        for record in projection.experiments
        if record.scenario == "synthetic/skill-coding@1"
    }
    assert revisions == {
        ("codex-cli", "baseline"),
        ("claude-code", "baseline"),
        ("codex-cli", "skills"),
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
        quality = _span(detail, "scorer:code-quality@1")
        scoring = _span(detail, "scoring")
        components = {
            "C": Decimal(str(run.diagnostic_score)),
            **{
                components_by_metric[item["metric_id"]]: item["score"]
                for item in quality["metric_contributions"]
            },
        }
        policies.add(scoring["headline_policy"])
        expected = DEFAULT_POLICY_REGISTRY.get(scoring["headline_policy"]).score(
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
    assert run.diagnostic_score == pytest.approx(2 / 3)
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
    mentions = [reason for reason in run.unscored_reasons if "harness exited" in reason]
    assert mentions == [
        "Trial exception: NonZeroAgentExitCodeError: "
        "the harness exited before verification finished (exit code 137)"
    ]
    assert any(
        reason.startswith("scb-canonical@1 ineligible: C") for reason in run.unscored_reasons
    )


def test_missing_assessments_leave_runs_unscored_never_zero(fixture_root: Path) -> None:
    artifact = merge_review_projections(run_roots=[fixture_root / "runs"])
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


def test_unreadable_trajectory_is_one_error_span(fixture_root: Path, tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    shutil.copytree(fixture_root / "runs" / "synthetic-skill", runs / "synthetic-skill")
    trace = runs / "synthetic-skill/synthetic-skill/trials/skills-gpt-01/steps/checkpoint_1/agent"
    (trace / "trajectory.json").write_text("{not atif")
    detail = _detail(merge_review_projections(run_roots=[runs]), "synthetic-skill-skills-gpt-01")
    broken = [span for span in detail.spans if span.name == "trajectory unreadable"]
    assert len(broken) == 1
    assert broken[0].status == "ERROR"


def test_condition_diff_pairs_baseline_with_the_same_agent(
    projection: ReviewProjectionArtifact,
) -> None:
    [diff] = projection.revision_diffs
    assert diff.key == "synthetic/skill-coding@1:baseline->skills"
    assert diff.summary == ["arm baseline-gpt -> skills-gpt", "+ component skills@1"]
    assert diff.comparable_warnings == []
    assert diff.change_categories == ["rules"]
    assert "workdir/skills/tdd/SKILL.md" in diff.sections
    assert set(diff.files) == {"scenario", "prompt"}


def _spec(factors: list[str]) -> ExperimentSpec:
    return ExperimentSpec.model_validate(
        {
            "schema_version": 1,
            "id": "guarded",
            "problems": [{"ref": "synthetic/p@1"}],
            "agents": [
                {
                    "id": "a",
                    "harness": "codex-cli",
                    "provider": "openai",
                    "model": "m1",
                    "version": "1.0",
                },
                {
                    "id": "b",
                    "harness": "codex-cli",
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
            "analysis": {"baseline_arm": "base", "blocks": 1, "seed": 0},
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
    skill = [r for r in records if r.scenario == "synthetic/skill-coding@1"][0]
    legacy = skill.model_copy(update={"experiment_id": "legacy", "aggregate": {}})
    marked, diffs = mark_policy_mismatches([*records, other, legacy], projection.revision_diffs)
    flagged = {
        record.scenario
        for record in marked
        if any(finding["category"] == MISMATCH_CATEGORY for finding in record.findings)
    }
    assert flagged == {"synthetic/ledger-balance@1", "synthetic/skill-coding@1"}
    assert SCENARIO_EPOCH_POLICY in diffs[0].comparable_warnings[-1]
    unchanged, same = mark_policy_mismatches(records, projection.revision_diffs)
    assert unchanged == records
    assert same == projection.revision_diffs


def test_run_without_its_lock_is_refused(fixture_root: Path, tmp_path: Path) -> None:
    run = tmp_path / "runs/synthetic-bugfix/synthetic-bugfix"
    shutil.copytree(fixture_root / "runs/synthetic-bugfix/synthetic-bugfix", run)
    lock = run / "experiment.lock.json"
    moved = tmp_path / "elsewhere.lock.json"
    lock.rename(moved)
    with pytest.raises(RunSourceError, match="pass it with --lock"):
        project_experiment_epoch([tmp_path / "runs"])
    epoch = project_experiment_epoch([tmp_path / "runs"], locks=[moved])
    assert len(epoch.details) == 5 * 3


def test_colliding_review_ids_are_qualified_by_experiment(
    fixture_root: Path, tmp_path: Path
) -> None:
    source = fixture_root / "runs/synthetic-skill/synthetic-skill"
    for experiment in ("synthetic-skill", "copy"):
        shutil.copytree(source, tmp_path / "runs" / experiment / "synthetic-skill")
    manifest = tmp_path / "runs/copy/synthetic-skill/run.json"
    data = json.loads(manifest.read_text())
    manifest.write_text(json.dumps({**data, "experiment": "copy"}))
    ids = set(merge_review_projections(run_roots=[tmp_path / "runs"]).run_details)
    assert "synthetic-skill-skills-gpt-01" not in ids
    assert {
        "copy-synthetic-skill-skills-gpt-01",
        "synthetic-skill-synthetic-skill-skills-gpt-01",
    } <= ids


def test_merge_combines_epochs_and_fails_closed_without_sources(
    fixture_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(ValueError, match="no current-version review projections"):
        merge_review_projections(run_roots=[tmp_path])
    empty = tmp_path / "runs/pending/r1"
    empty.mkdir(parents=True)
    shutil.copy(fixture_root / "runs/synthetic-skill/synthetic-skill/run.json", empty / "run.json")
    shutil.copy(
        fixture_root / "runs/synthetic-skill/synthetic-skill/plan.json", empty / "plan.json"
    )
    shutil.copy(
        fixture_root / "runs/synthetic-skill/synthetic-skill/experiment.lock.json",
        empty / "experiment.lock.json",
    )
    output = tmp_path / "out.json"
    main(
        [
            "--runs-root",
            str(fixture_root / "runs"),
            "--runs-root",
            str(tmp_path / "runs"),
            "--store",
            str(fixture_root / "store"),
            "--output",
            str(output),
        ]
    )
    artifact = ReviewProjectionArtifact.model_validate_json(output.read_text())
    assert len(artifact.runs) == 30
    assert len(artifact.experiments) == 8
    captured = capsys.readouterr()
    assert "Skipping run without trajectory records" in captured.err
    with pytest.raises(SystemExit):
        main(["--output", str(output)])


def test_unknown_headline_policy_is_ineligible_not_zero() -> None:
    result = headline("nope@1", 3, TrialAssessments())
    assert result.composite is None
    assert result.score is None
    assert result.reasons
    assert "unknown score policy" in result.reasons[0]


def test_unassessable_trial_inputs_are_reported_per_scorer(
    fixture_root: Path, tmp_path: Path
) -> None:
    runs = tmp_path / "runs"
    shutil.copytree(fixture_root / "runs/synthetic-skill", runs / "synthetic-skill")
    shutil.copytree(fixture_root / "bundles", tmp_path / "bundles")
    moved = tmp_path / "bundles/synthetic-skill/oracle-bundle.json"
    moved.write_text(moved.read_text().replace("python:3.12-slim", "python:3.13-slim"))
    for config in runs.glob("synthetic-skill/synthetic-skill/trials/*/config.json"):
        config.write_text(config.read_text().replace(str(fixture_root), str(tmp_path)))
    for record in runs.glob(
        "synthetic-skill/synthetic-skill/trials/*/raidar-oracle/steps/*/record.json"
    ):
        record.write_text(record.read_text().replace(str(fixture_root), str(tmp_path)))
    run = merge_review_projections(run_roots=[runs], stores=[fixture_root / "store"]).runs[0]
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
    run = tmp_path / "runs" / experiment / experiment
    shutil.copytree(fixture_root / "runs" / experiment / experiment, run)
    return run


def _edit_records(run: Path, edit: Any, pattern: str = "*.json") -> None:
    for path in sorted((run / "trajectories").glob(pattern)):
        record = json.loads(path.read_text())
        edit(record)
        path.write_text(json.dumps(record))


def test_unassessed_hint_names_the_runs_assessment_config(
    fixture_root: Path, tmp_path: Path
) -> None:
    artifact = merge_review_projections(
        run_roots=[fixture_root / "runs"], stores=[tmp_path / "empty-store"]
    )
    run = _detail(artifact, PASSING).run
    config = fixture_root / "runs/synthetic-bugfix/synthetic-bugfix/assessment-config.yaml"
    hints = [reason for reason in run.unscored_reasons if "raidar assess --rescore" in reason]
    assert hints
    assert all(f"--config {config}" in hint for hint in hints)
    assert all(reason.startswith("scb-canonical@1 unassessed:") for reason in hints)


def test_truncated_dev_run_is_withheld_and_never_pooled_with_arm_evidence(
    fixture_root: Path, tmp_path: Path
) -> None:
    full = _copy_run(fixture_root, tmp_path, "synthetic-bugfix")
    dev = tmp_path / "runs/synthetic-bugfix/dev"
    shutil.copytree(full, dev)
    manifest = json.loads((dev / "run.json").read_text())
    (dev / "run.json").write_text(json.dumps({**manifest, "run_id": "dev"}))
    plan = json.loads((dev / "plan.json").read_text())
    (dev / "plan.json").write_text(json.dumps({**plan, "options": {"max_checkpoints": 2}}))
    _edit_records(dev, lambda record: record.update(truncated_to=2))
    artifact = merge_review_projections(
        run_roots=[tmp_path / "runs"], stores=[fixture_root / "store"]
    )
    medium = [
        r
        for r in artifact.experiments
        if r.agent_spec.startswith("codex-cli · openai/gpt-5.5 [medium]")
    ]
    assert len(medium) == 2
    [truncated] = [r for r in medium if r.experiment_id.endswith(":first-2")]
    [arm] = [r for r in medium if r is not truncated]
    assert "dev run, first 2 checkpoint(s)" in truncated.agent_spec
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
    run_dir = _copy_run(fixture_root, tmp_path, "synthetic-skill")

    def fail_probe(record: dict[str, Any]) -> None:
        for check in record["validity"]["checks"]:
            if check["name"] == "component_probes":
                check.update(passed=False, detail="AGENTS.md lines not in the harness context")

    _edit_records(run_dir, fail_probe, "skills-gpt-01.json")
    artifact = merge_review_projections(
        run_roots=[tmp_path / "runs"], stores=[fixture_root / "store"]
    )
    run = _detail(artifact, "synthetic-skill-skills-gpt-01").run
    assert not run.valid
    assert run.unscored
    assert (run.composite_score, run.quality_score, run.diagnostic_score) == (None, None, None)
    assert run.unscored_reasons[0] == (
        "Execution invalid: component_probes: AGENTS.md lines not in the harness context"
    )
    record = next(
        r for r in artifact.experiments if r.revision == "skills" and r.harness == "codex-cli"
    )
    assert record.aggregate["run_count_scored"] == 2
    detail = _detail(artifact, "synthetic-skill-skills-gpt-01")
    assert _span(detail, "metric:problem_verbosity")["status"] == "ok"


def test_lock_whose_body_was_edited_is_refused(fixture_root: Path, tmp_path: Path) -> None:
    run_dir = _copy_run(fixture_root, tmp_path, "synthetic-bugfix")
    lock_path = run_dir / "experiment.lock.json"
    lock = json.loads(lock_path.read_text())
    lock["assessment"]["headline_policy"]["ref"] = "scb-canonical-oneshot@1"
    lock_path.write_text(json.dumps(lock))
    with pytest.raises(RunSourceError, match="section assessment does not match its digest"):
        project_experiment_epoch([tmp_path / "runs"])


def test_policy_shares_come_from_the_policy() -> None:
    for ref in ("scb-canonical@1", "scb-canonical-oneshot@1"):
        base, inertia = policy_shares(DEFAULT_POLICY_REGISTRY.get(ref))
        assert (base, inertia) == pytest.approx((0.70, 0.30))


def test_fixture_family_mixing_policies_renders_the_mismatch(fixture_root: Path) -> None:
    artifact = merge_review_projections(
        [fixture_root / SCENARIO_EPOCH_DIR],
        run_roots=[fixture_root / "runs"],
        stores=[fixture_root / "store"],
    )
    flagged = {
        (record.scenario, record.revision)
        for record in artifact.experiments
        if any(finding["category"] == MISMATCH_CATEGORY for finding in record.findings)
    }
    assert flagged == {("synthetic/policy-mix@1", "baseline"), ("synthetic/policy-mix@1", "v001")}
    assert any(run.scenario == "skill-benchmark-coding-test" for run in artifact.runs)


def test_following_the_unassessed_hint_stores_the_assessment_the_review_finds(
    fixture_root: Path, tmp_path: Path
) -> None:
    from click.testing import CliRunner

    from raidar.commands.assess import assess

    run_dir = _copy_run(fixture_root, tmp_path, "synthetic-bugfix")
    config = run_dir / "assessment-config.yaml"
    config.write_text(
        config.read_text().replace(
            "scorers: {}", "scorers:\n  correctness@1:\n    progression: continue"
        )
    )
    runs, store = tmp_path / "runs", tmp_path / "store"
    before = merge_review_projections(run_roots=[runs], stores=[store])
    [hint] = [
        reason
        for reason in _detail(before, PASSING).run.unscored_reasons
        if "correctness@1" in reason and "raidar assess" in reason
    ]
    command = hint.split("`")[1].replace("<store>", str(store)).split()
    result = CliRunner().invoke(assess, command[2:])
    assert result.exit_code == 0, result.output
    after = merge_review_projections(run_roots=[runs], stores=[store])
    scoring = _span(_detail(after, PASSING), "scoring")
    assert "correctness@1" in scoring["assessments"]
    assert "correctness@1" not in scoring["not_assessed"]
