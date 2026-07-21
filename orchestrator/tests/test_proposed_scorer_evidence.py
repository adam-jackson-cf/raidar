"""Tests for promotion-ready proposed scorer evidence contracts."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from raidar.schemas.scenario import DeterministicCheck
from raidar.schemas.scorecard import (
    CoverageScore,
    FunctionalScore,
    MetricScore,
    RequirementsCoverageScore,
)
from raidar.scorers.base import ScorerContext
from raidar.scorers.code_task.bugfix import Bugfix, BugfixV2
from raidar.scorers.code_task.refactor import Refactor, RefactorV2
from raidar.scorers.common import (
    assertion_strength_metric_score,
    coverage_lift_metric_score,
    requirement_mapping_metric_score,
)
from raidar.scorers.evidence_contract import (
    ArchiveEvidenceResult,
    CommandEvidenceResult,
    EvidenceActionError,
    EvidenceActionResult,
    EvidenceArtifactResult,
    EvidenceOutput,
    EvidenceRunnerMetadata,
    JsonEvidenceResult,
    PublicContractFileEvidence,
    PublicContractSnapshotEvidenceResult,
    StructuralSnapshotEvidenceResult,
    StructuralSnapshotFileEvidence,
)
from raidar.scorers.plan_to_code import PlanToCode, PlanToCodeV2
from raidar.scorers.test_generation import TestGeneration, TestGenerationV2
from raidar.scorers.verification import (
    VerificationEvidence,
)


def _context(
    workspace: Path,
    *,
    functional: FunctionalScore | None = None,
    coverage: CoverageScore | None = None,
    requirements: RequirementsCoverageScore | None = None,
    workspace_changes: dict | None = None,
    retained_evidence: dict | None = None,
    baseline_exit_code: int | None = None,
    baseline_coverage: float | None = None,
    coverage_source: str = "python",
    baseline_coverage_source: str = "python",
) -> ScorerContext:
    requirement_items = []
    if isinstance(requirements, RequirementsCoverageScore) and requirements.total_requirements:
        requirement_items = [
            SimpleNamespace(
                id="req-defect",
                description="Defect behavior",
                check=DeterministicCheck(
                    type="file_exists",
                    pattern="src/app.py",
                    description="Source file exists",
                ),
            )
        ]
    command_result = EvidenceActionResult(
        id="bugfix.required.0",
        kind="command",
        required=True,
        status="ok",
        duration_sec=0.1,
        result=CommandEvidenceResult(
            command=["python", "-m", "pytest"],
            cwd=".",
            duration_sec=0.1,
            exit_code=0 if functional is None or functional.passed else 1,
            stdout="passed",
            stderr="",
            timed_out=False,
        ),
    )
    coverage_value = coverage.measured if coverage is not None else 0.8
    coverage_result = EvidenceActionResult(
        id=f"test-generation.coverage.{coverage_source}",
        kind="read_json",
        required=False,
        status="ok",
        duration_sec=0.1,
        result=JsonEvidenceResult(
            bytes=10,
            content={"totals": {"percent_covered": coverage_value * 100}},
            path=".raidar/coverage.json",
            sha256="0" * 64,
            truncated=False,
        ),
    )
    actions = {
        command_result.id: command_result,
        coverage_result.id: coverage_result,
    }
    aliases = {
        f"{owner}.required.0": command_result.id
        for owner in ("bugfix", "refactor", "test-generation", "plan-to-code")
    }
    aliases.update(
        {
            f"test-generation.coverage.{coverage_source}": coverage_result.id,
        }
    )
    baseline_actions = {}
    baseline_aliases = {}
    if baseline_exit_code is not None:
        baseline_command = command_result.model_copy(
            update={
                "result": command_result.result.model_copy(update={"exit_code": baseline_exit_code})
            }
        )
        baseline_actions[baseline_command.id] = baseline_command
        baseline_aliases.update(aliases)
    if baseline_coverage is not None:
        baseline_result = coverage_result.model_copy(
            update={
                "id": f"test-generation.coverage.{baseline_coverage_source}",
                "result": coverage_result.result.model_copy(
                    update={"content": {"totals": {"percent_covered": baseline_coverage * 100}}}
                ),
            }
        )
        baseline_actions[baseline_result.id] = baseline_result
        baseline_aliases[f"test-generation.coverage.{baseline_coverage_source}"] = (
            baseline_result.id
        )
    return ScorerContext(
        workspace=workspace,
        scenario_dir=workspace,
        scenario=SimpleNamespace(
            languages=["python"],
            requirements=SimpleNamespace(items=requirement_items),
            scorers=[],
            verification=SimpleNamespace(coverage_threshold=0.8),
        ),
        execution=SimpleNamespace(),
        resource_efficiency=SimpleNamespace(),
        execution_validity=SimpleNamespace(),
        workspace_changes=workspace_changes or {},
        retained_evidence=retained_evidence or {},
        verification_evidence=VerificationEvidence(actions=actions, aliases=aliases),
        baseline_verification_evidence=VerificationEvidence(
            actions=baseline_actions, aliases=baseline_aliases
        ),
    )


def _v2_context(
    workspace: Path,
    *,
    baseline_exit_code: int | None = None,
    baseline_coverage: float | None = None,
) -> ScorerContext:
    context = _context(workspace)
    owners = ("bugfix-v2", "refactor-v2", "test-generation-v2", "plan-to-code-v2")
    actions = {
        action.id: action
        for owner in owners
        for action in (_command_action(f"{owner}.required.0"),)
    }
    coverage = next(
        action
        for action in context.verification_evidence.actions.values()
        if action.kind == "read_json"
    ).model_copy(update={"id": "test-generation-v2.coverage.python"})
    actions[coverage.id] = coverage
    aliases = {action_id: action_id for action_id in actions}
    baseline_actions = dict(actions)
    if baseline_exit_code is not None:
        command = actions["bugfix-v2.required.0"]
        baseline_actions[command.id] = command.model_copy(
            update={"result": command.result.model_copy(update={"exit_code": baseline_exit_code})}
        )
    if baseline_coverage is not None:
        baseline_actions[coverage.id] = coverage.model_copy(
            update={
                "result": coverage.result.model_copy(
                    update={"content": {"totals": {"percent_covered": baseline_coverage * 100}}}
                )
            }
        )
    return replace(
        context,
        verification_evidence=VerificationEvidence(actions=actions, aliases=aliases),
        baseline_verification_evidence=VerificationEvidence(
            actions=baseline_actions, aliases=aliases
        ),
    )


def test_direct_evidence_successors_use_versioned_metric_and_owner_namespaces(
    tmp_path: Path,
) -> None:
    context = _v2_context(_workspace(tmp_path))
    scorers = (
        (
            BugfixV2(),
            [
                "bugfix-defect-resolution-v2",
                "bugfix-regression-protection-v2",
                "bugfix-change-containment-v2",
                "bugfix-verification-stability-v2",
                "bugfix-defect-evidence-completeness-v2",
            ],
            "bugfix-v2",
        ),
        (
            RefactorV2(),
            [
                "refactor-behavior-preservation-v2",
                "refactor-structural-improvement-v2",
                "refactor-public-contract-stability-v2",
                "refactor-change-containment-v2",
                "refactor-verification-stability-v2",
            ],
            "refactor-v2",
        ),
        (
            TestGenerationV2(),
            [
                "test-generation-requirement-mapping-v2",
                "test-generation-assertion-strength-v2",
                "test-generation-coverage-lift-v2",
                "test-generation-production-code-guardrail-v2",
                "test-generation-verification-stability-v2",
            ],
            "test-generation-v2",
        ),
    )

    for scorer, metric_ids, owner in scorers:
        scores = scorer.collect_evidence(context).metric_scores

        assert [score.metric_id for score in scores] == metric_ids
        assert all(score.metric_id.startswith(f"{owner.removesuffix('-v2')}-") for score in scores)
        assert context.verification_evidence.action(owner, "required.0") is not None
        assert f"{owner}.required.0" in context.verification_evidence.actions


def _workspace(tmp_path: Path) -> Path:
    tmp_path.mkdir(exist_ok=True)
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "app.py").write_text(
        "def add(left, right):\n    return left + right\n", encoding="utf-8"
    )
    (tests / "test_app.py").write_text(
        "def test_req_add():\n    assert 1 + 2 == 3\n    assert 2 + 2 == 4\n",
        encoding="utf-8",
    )
    return tmp_path


def _write_authoritative_plan(scenario_dir: Path, content: str) -> Path:
    plan_dir = scenario_dir / ".enaible" / "intent-plan" / sha256(content.encode()).hexdigest()
    plan_dir.mkdir(parents=True)
    plan_path = plan_dir / "intentplan.md"
    plan_path.write_text(content, encoding="utf-8")
    return plan_path


def _command_action(action_id: str = "owner.required.0") -> EvidenceActionResult:
    return EvidenceActionResult(
        id=action_id,
        kind="command",
        required=True,
        status="ok",
        duration_sec=0.1,
        result=CommandEvidenceResult(
            command=["python", "-m", "pytest"],
            cwd=".",
            duration_sec=0.1,
            exit_code=0,
            stdout="passed",
            stderr="",
            timed_out=False,
        ),
    )


def _evidence_output(**overrides) -> EvidenceOutput:
    payload = {
        "schema_version": "1",
        "runner_contract": "python-evidence-runner@2",
        "aliases": {"owner.required.0": "owner.required.0"},
        "actions": [_command_action()],
        "artifacts": [],
        "metadata": EvidenceRunnerMetadata(
            action_count=1,
            error_count=0,
            required_error_count=0,
            workspace="/workspace",
        ),
    }
    payload.update(overrides)
    return EvidenceOutput.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "ok", "result": None},
        {
            "status": "ok",
            "result": _command_action().result,
            "error": EvidenceActionError(type="failure", message="bad"),
        },
        {"status": "error", "result": None, "error": None},
        {
            "status": "error",
            "result": _command_action().result,
            "error": EvidenceActionError(type="failure", message="bad"),
        },
        {"status": "ok", "result": {"not": "a command"}},
    ],
)
def test_evidence_action_status_and_result_contracts(payload: dict) -> None:
    with pytest.raises(ValueError, match="evidence action"):
        EvidenceActionResult(
            id="owner.required.0",
            kind="command",
            required=True,
            duration_sec=0.1,
            **payload,
        )


def test_evidence_output_rejects_inconsistent_identity_graphs() -> None:
    action = _command_action()
    failed = EvidenceActionResult(
        id="owner.failed.0",
        kind="command",
        required=True,
        status="error",
        duration_sec=0.1,
        error=EvidenceActionError(type="failure", message="bad"),
    )
    archive = EvidenceActionResult(
        id="owner.archive.0",
        kind="archive",
        required=False,
        status="ok",
        duration_sec=0.1,
        result=ArchiveEvidenceResult(archive="out.tar", archived=[], exists=True),
    )

    invalid_payloads = [
        {
            "actions": [action, action],
            "metadata": EvidenceRunnerMetadata(
                action_count=2, error_count=0, required_error_count=0, workspace="/workspace"
            ),
        },
        {"aliases": {"alias": "missing"}},
        {
            "metadata": EvidenceRunnerMetadata(
                action_count=2, error_count=0, required_error_count=0, workspace="/workspace"
            )
        },
        {
            "actions": [failed],
            "aliases": {},
            "metadata": EvidenceRunnerMetadata(
                action_count=1, error_count=0, required_error_count=1, workspace="/workspace"
            ),
        },
        {
            "actions": [failed],
            "aliases": {},
            "metadata": EvidenceRunnerMetadata(
                action_count=1, error_count=1, required_error_count=0, workspace="/workspace"
            ),
        },
        {"artifacts": [EvidenceArtifactResult(action_id=action.id, path="out.tar", exists=True)]},
        {
            "actions": [archive],
            "aliases": {},
            "artifacts": [
                EvidenceArtifactResult(action_id=archive.id, path="a.tar", exists=True),
                EvidenceArtifactResult(action_id=archive.id, path="b.tar", exists=True),
            ],
        },
    ]
    for overrides in invalid_payloads:
        with pytest.raises(ValueError, match="evidence output"):
            _evidence_output(**overrides)


def test_bugfix_collects_concept_specific_proxy_evidence(tmp_path: Path) -> None:
    scores = {
        score.metric_id: score
        for score in Bugfix().collect_evidence(_context(_workspace(tmp_path))).metric_scores
    }

    assert list(scores) == [
        "defect-resolution",
        "regression-protection",
        "change-containment",
        "verification-stability",
        "defect-evidence-completeness",
    ]
    assert scores["defect-resolution"].missing_patterns == ["defect-linked requirement evidence"]
    assert not scores["regression-protection"].passed
    assert scores["regression-protection"].missing_patterns == [
        "behavior-specific regression tests"
    ]
    assert "proxy:" in str(scores["change-containment"].evidence)
    assert not scores["change-containment"].passed


def test_bugfix_defect_resolution_requires_a_paired_failure_and_success(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    final_only = {
        score.metric_id: score
        for score in BugfixV2().collect_evidence(_v2_context(workspace)).metric_scores
    }
    paired = {
        score.metric_id: score
        for score in BugfixV2()
        .collect_evidence(_v2_context(workspace, baseline_exit_code=1))
        .metric_scores
    }

    assert not final_only["bugfix-defect-resolution-v2"].passed
    assert "starter command failure" in final_only["bugfix-defect-resolution-v2"].missing_patterns
    assert paired["bugfix-defect-resolution-v2"].passed
    assert not paired["bugfix-regression-protection-v2"].passed


def test_bugfix_defect_resolution_handles_malformed_requirement_evidence(
    tmp_path: Path,
) -> None:
    context = _context(_workspace(tmp_path), requirements=SimpleNamespace(total_requirements="bad"))

    scores = {score.metric_id: score for score in Bugfix().collect_evidence(context).metric_scores}

    assert not scores["defect-resolution"].passed


def test_bugfix_defect_evidence_requires_paired_outcomes_and_changed_test_link(
    tmp_path: Path,
) -> None:
    context = replace(
        _v2_context(_workspace(tmp_path), baseline_exit_code=1),
        workspace_changes={
            "changed_files": ["src/app.py", "tests/test_app.py"],
            "changed_file_count": 2,
            "error": None,
        },
        retained_evidence={"regression_tests": ["tests/test_app.py"]},
    )

    scores = {
        score.metric_id: score for score in BugfixV2().collect_evidence(context).metric_scores
    }

    assert scores["bugfix-defect-evidence-completeness-v2"].passed
    assert scores["bugfix-defect-evidence-completeness-v2"].score == 1.0


def test_bugfix_defect_evidence_does_not_pass_from_source_inventory(tmp_path: Path) -> None:
    scores = {
        score.metric_id: score
        for score in Bugfix().collect_evidence(_context(_workspace(tmp_path))).metric_scores
    }

    assert not scores["defect-evidence-completeness"].passed
    assert scores["defect-evidence-completeness"].missing_patterns == [
        "reproduction note",
        "regression test evidence",
        "changed-file evidence",
    ]


def test_bugfix_malformed_changed_file_evidence_is_incomplete(tmp_path: Path) -> None:
    context = _context(
        _workspace(tmp_path),
        requirements=RequirementsCoverageScore(total_requirements=1, satisfied_requirements=1),
        workspace_changes={"changed_files": "src/app.py", "error": None},
        retained_evidence={
            "reproduction_note": "Observed failing defect before fix.",
            "regression_tests": ["tests/test_app.py::test_req_add"],
            "verification_evidence": "pytest passed",
        },
    )

    scores = {score.metric_id: score for score in Bugfix().collect_evidence(context).metric_scores}

    assert not scores["change-containment"].passed
    assert "proxy:" in str(scores["change-containment"].evidence)
    assert not scores["defect-evidence-completeness"].passed
    assert "changed-file evidence" in scores["defect-evidence-completeness"].missing_patterns


def test_refactor_collects_concept_specific_proxy_evidence(tmp_path: Path) -> None:
    scores = {
        score.metric_id: score
        for score in Refactor().collect_evidence(_context(_workspace(tmp_path))).metric_scores
    }

    assert list(scores) == [
        "behavior-preservation",
        "structural-improvement",
        "public-contract-stability",
        "change-containment",
        "verification-stability",
    ]
    assert scores["behavior-preservation"].passed
    assert not scores["structural-improvement"].passed
    assert not scores["public-contract-stability"].passed
    assert "proxy:" in str(scores["structural-improvement"].evidence)
    assert "proxy:" in str(scores["public-contract-stability"].evidence)


def test_test_generation_collects_guardrail_and_assertion_evidence(tmp_path: Path) -> None:
    scenario = SimpleNamespace(
        requirements=SimpleNamespace(
            items=[
                SimpleNamespace(id="req-add", description="Add numbers correctly"),
            ]
        ),
        scorers=[],
        verification=SimpleNamespace(coverage_threshold=0.8),
    )
    context = _context(_workspace(tmp_path))
    context = ScorerContext(
        workspace=context.workspace,
        scenario_dir=context.scenario_dir,
        scenario=scenario,
        execution=context.execution,
        resource_efficiency=context.resource_efficiency,
        execution_validity=context.execution_validity,
        workspace_changes={
            "changed_files": ["tests/test_app.py"],
            "changed_file_count": 1,
            "error": None,
        },
        verification_evidence=context.verification_evidence,
    )
    scores = {
        score.metric_id: score for score in TestGeneration().collect_evidence(context).metric_scores
    }

    assert list(scores) == [
        "requirement-mapping",
        "assertion-strength",
        "coverage-lift",
        "production-code-guardrail",
        "verification-stability",
    ]
    assert scores["requirement-mapping"].passed
    assert scores["assertion-strength"].passed
    assert scores["production-code-guardrail"].passed
    assert "direct:" in str(scores["production-code-guardrail"].evidence)


def test_test_generation_guardrail_requires_changed_file_evidence(tmp_path: Path) -> None:
    scores = {
        score.metric_id: score
        for score in TestGeneration().collect_evidence(_context(_workspace(tmp_path))).metric_scores
    }

    assert not scores["production-code-guardrail"].passed
    assert "proxy:" in str(scores["production-code-guardrail"].evidence)


def test_test_generation_guardrail_rejects_malformed_changed_file_evidence(
    tmp_path: Path,
) -> None:
    scores = {
        score.metric_id: score
        for score in TestGeneration()
        .collect_evidence(
            _context(
                _workspace(tmp_path),
                workspace_changes={"changed_files": ["tests/test_app.py", ""], "error": None},
            )
        )
        .metric_scores
    }

    assert not scores["production-code-guardrail"].passed
    assert "proxy:" in str(scores["production-code-guardrail"].evidence)


def test_plan_to_code_caps_judge_when_functional_execution_fails(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_judge(**_kwargs):
        return MetricScore(
            metric_id="plan-to-code-plan-adherence-v2",
            score=1.0,
            passed=True,
            evidence="judge passed",
        )

    monkeypatch.setattr("raidar.scorers.plan_to_code.evaluate_llm_as_judge_metric", fake_judge)
    _write_authoritative_plan(
        tmp_path,
        "## Feature Dashboard\n\n"
        "| Feature | Status | Evidence Passed | Changed Surfaces |\n"
        "| --- | --- | --- | --- |\n"
        "| F1 | passed | 1 | src/app.py |\n\n"
        "## Acceptance Tracker\n\n"
        "| AC | Status | Passing Evidence |\n"
        "| --- | --- | --- |\n"
        "| AC1 | passed | pytest |\n",
    )
    context = _v2_context(_workspace(tmp_path))
    command = context.verification_evidence.actions["plan-to-code-v2.required.0"]
    failed_command = command.model_copy(
        update={"result": command.result.model_copy(update={"exit_code": 1})}
    )
    actions = dict(context.verification_evidence.actions)
    actions[failed_command.id] = failed_command
    context = replace(
        context,
        verification_evidence=VerificationEvidence(
            actions=actions,
            aliases=context.verification_evidence.aliases,
        ),
    )
    scores = {
        score.metric_id: score for score in PlanToCodeV2().collect_evidence(context).metric_scores
    }

    assert scores["plan-to-code-plan-adherence-v2"].score == 0.0
    assert scores["plan-to-code-plan-adherence-v2"].passed is False
    assert "deterministic functional execution failed" in str(
        scores["plan-to-code-plan-adherence-v2"].evidence
    )
    assert scores["plan-to-code-planned-scope-coverage-v2"].passed


def test_plan_to_code_scores_retained_plan_packet(monkeypatch, tmp_path: Path) -> None:
    _write_authoritative_plan(
        tmp_path,
        "## Feature Dashboard\n\n"
        "| Feature | Status | Evidence Passed | Changed Surfaces |\n"
        "| --- | --- | --- | --- |\n"
        "| F1 | passed | 1 | src/app.py |\n"
        "| F2 | passed | 2 | tests/test_app.py |\n\n"
        "## Acceptance Tracker\n\n"
        "| AC | Status | Passing Evidence |\n"
        "| --- | --- | --- |\n"
        "| AC1 | passed | make quality |\n"
        "| AC2 | passed | pytest |\n",
    )

    def fake_judge(**kwargs):
        assert kwargs["retained_evidence"]["feature_count"] == 2
        assert kwargs["changed_surfaces"] == ["src/app.py", "tests/test_app.py"]
        assert kwargs["deterministic_metric_scores"]
        functional = next(
            score
            for score in kwargs["deterministic_metric_scores"]
            if score.metric_id == "plan-to-code-functional-v2"
        )
        assert functional.passed is True
        assert "execution_outputs" not in kwargs
        return MetricScore(
            metric_id="plan-to-code-plan-adherence-v2",
            score=1.0,
            passed=True,
            evidence="judge passed",
        )

    monkeypatch.setattr("raidar.scorers.plan_to_code.evaluate_llm_as_judge_metric", fake_judge)
    scores = {
        score.metric_id: score
        for score in PlanToCodeV2()
        .collect_evidence(
            replace(
                _v2_context(_workspace(tmp_path)),
                workspace_changes={
                    "changed_files": ["src/app.py", "tests/test_app.py"],
                    "changed_file_count": 2,
                    "error": None,
                },
            )
        )
        .metric_scores
    }

    assert scores["plan-to-code-planned-scope-coverage-v2"].passed
    assert scores["plan-to-code-acceptance-evidence-completeness-v2"].passed
    assert "direct:" in str(scores["plan-to-code-planned-scope-coverage-v2"].evidence)


def test_plan_to_code_judge_receives_retained_changed_files(monkeypatch, tmp_path: Path) -> None:
    _write_authoritative_plan(
        tmp_path,
        "## Feature Dashboard\n\n"
        "| Feature | Status | Evidence Passed | Changed Surfaces |\n"
        "| --- | --- | --- | --- |\n"
        "| F1 | passed | 1 | src/app.py |\n\n"
        "## Acceptance Tracker\n\n"
        "| AC | Status | Passing Evidence |\n"
        "| --- | --- | --- |\n"
        "| AC1 | passed | pytest |\n",
    )
    observed = {}

    def fake_judge(**kwargs):
        observed["changed_surfaces"] = kwargs["changed_surfaces"]
        return MetricScore(
            metric_id="plan-to-code-plan-adherence-v2",
            score=1.0,
            passed=True,
            evidence="judge passed",
        )

    monkeypatch.setattr("raidar.scorers.plan_to_code.evaluate_llm_as_judge_metric", fake_judge)
    PlanToCodeV2().collect_evidence(
        replace(
            _v2_context(_workspace(tmp_path)),
            workspace_changes={
                "changed_files": ["src/app.py"],
                "changed_file_count": 1,
                "error": None,
            },
        )
    )

    assert observed["changed_surfaces"] == ["src/app.py"]


def test_plan_to_code_rejects_status_only_retained_plan_packet(monkeypatch, tmp_path: Path) -> None:
    _write_authoritative_plan(
        tmp_path,
        "## Feature Dashboard\n\n"
        "| Feature | Status |\n"
        "| --- | --- |\n"
        "| F1 | passed |\n\n"
        "## Acceptance Tracker\n\n"
        "| AC | Status |\n"
        "| --- | --- |\n"
        "| AC1 | passed |\n",
    )

    monkeypatch.setattr(
        "raidar.scorers.plan_to_code.evaluate_llm_as_judge_metric",
        lambda **_kwargs: MetricScore(
            metric_id="plan-to-code-plan-adherence-v2",
            score=0.0,
            passed=False,
            evidence="judge failed",
        ),
    )

    scores = {
        score.metric_id: score
        for score in PlanToCodeV2()
        .collect_evidence(_v2_context(_workspace(tmp_path)))
        .metric_scores
    }

    assert scores["plan-to-code-planned-scope-coverage-v2"].score == 0.0
    assert not scores["plan-to-code-planned-scope-coverage-v2"].passed
    assert scores["plan-to-code-acceptance-evidence-completeness-v2"].score == 0.0
    assert not scores["plan-to-code-acceptance-evidence-completeness-v2"].passed


def test_coverage_lift_caps_no_baseline_proxy_at_point_eight() -> None:
    score = coverage_lift_metric_score(SimpleNamespace(threshold=0.8, measured=1.0, passed=True))

    assert score.score == 0.8
    assert not score.passed
    assert "proxy:" in str(score.evidence)


def test_coverage_lift_handles_malformed_coverage_values() -> None:
    score = coverage_lift_metric_score(
        SimpleNamespace(threshold="0.8", measured="1.0", passed=True)
    )

    assert score.score == 0.0
    assert not score.passed
    assert score.missing_patterns == ["coverage baseline or final measurement"]


def test_assertion_strength_handles_malformed_test_file_bytes(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_bad.py").write_bytes(b"def test_bad():\n    assert b'\\xff'\n\xff")

    score = assertion_strength_metric_score(tmp_path)

    assert score.score > 0
    assert "proxy:" in str(score.evidence)


def test_requirement_mapping_handles_malformed_requirement_shape(tmp_path: Path) -> None:
    _workspace(tmp_path)
    score = requirement_mapping_metric_score(
        tmp_path,
        SimpleNamespace(requirements=SimpleNamespace(items=[object()])),
    )

    assert score.score == 0.0
    assert not score.passed
    assert "proxy:" in str(score.evidence)


def test_plan_to_code_handles_malformed_retained_plan_packet(monkeypatch, tmp_path: Path) -> None:
    _write_authoritative_plan(
        tmp_path,
        "## Feature Dashboard\n\nnot a markdown status table\n\n"
        "## Acceptance Tracker\n\nalso malformed",
    )

    monkeypatch.setattr(
        "raidar.scorers.plan_to_code.evaluate_llm_as_judge_metric",
        lambda **_kwargs: MetricScore(
            metric_id="plan-to-code-plan-adherence-v2",
            score=0.0,
            passed=False,
            evidence="judge failed",
        ),
    )

    scores = {
        score.metric_id: score
        for score in PlanToCodeV2()
        .collect_evidence(_v2_context(_workspace(tmp_path)))
        .metric_scores
    }

    assert scores["plan-to-code-planned-scope-coverage-v2"].score == 0.0
    assert not scores["plan-to-code-planned-scope-coverage-v2"].passed
    assert not scores["plan-to-code-acceptance-evidence-completeness-v2"].passed


def test_refactor_requires_paired_command_outcomes_for_behavior_preservation(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    inventory_only = {
        score.metric_id: score
        for score in RefactorV2()
        .collect_evidence(
            replace(
                _v2_context(workspace),
                workspace_changes={
                    "changed_files": ["src/app.py"],
                    "changed_file_count": 1,
                    "error": None,
                },
            )
        )
        .metric_scores
    }
    paired = {
        score.metric_id: score
        for score in RefactorV2().collect_evidence(_v2_context(workspace)).metric_scores
    }

    assert inventory_only["refactor-behavior-preservation-v2"].passed
    assert paired["refactor-behavior-preservation-v2"].passed
    assert not paired["refactor-structural-improvement-v2"].passed
    assert not paired["refactor-public-contract-stability-v2"].passed


def _refactor_snapshot_context(
    workspace: Path,
    *,
    baseline_structure: int | None = None,
    final_structure: int | None = None,
    baseline_contract: tuple[str, ...] | None = None,
    final_contract: tuple[str, ...] | None = None,
    declared_public_paths: tuple[str, ...] = ("src/app.py",),
) -> ScorerContext:
    context = _v2_context(workspace, baseline_exit_code=0)
    context.scenario.scorers = [
        SimpleNamespace(
            id="refactor",
            version=2,
            config={
                "refactor-structural-improvement-v2": {"paths": ["src/app.py"]},
                "refactor-public-contract-stability-v2": {"paths": list(declared_public_paths)},
            },
        )
    ]

    def structural_action(action_id: str, code_line_count: int) -> EvidenceActionResult:
        return EvidenceActionResult(
            id=action_id,
            kind="structural_snapshot",
            required=True,
            status="ok",
            duration_sec=0.1,
            result=StructuralSnapshotEvidenceResult(
                digest=f"structural-{code_line_count}",
                files=[
                    StructuralSnapshotFileEvidence(
                        bytes=code_line_count,
                        code_line_count=code_line_count,
                        line_count=code_line_count,
                        nonempty_line_count=code_line_count,
                        path="src/app.py",
                        sha256="a" * 64,
                    )
                ],
            ),
        )

    def public_action(action_id: str, declarations: tuple[str, ...]) -> EvidenceActionResult:
        return EvidenceActionResult(
            id=action_id,
            kind="public_contract_snapshot",
            required=True,
            status="ok",
            duration_sec=0.1,
            result=PublicContractSnapshotEvidenceResult(
                digest=f"public-{'-'.join(declarations)}",
                files=[
                    PublicContractFileEvidence(
                        declarations=list(declarations),
                        path="src/app.py",
                        sha256="b" * 64,
                    )
                ],
            ),
        )

    final_actions = dict(context.verification_evidence.actions)
    final_aliases = dict(context.verification_evidence.aliases)
    baseline_actions = dict(context.baseline_verification_evidence.actions)
    baseline_aliases = dict(context.baseline_verification_evidence.aliases)
    if final_structure is not None:
        action = structural_action(
            "refactor-v2.refactor-structural-improvement-v2", final_structure
        )
        final_actions[action.id] = action
        final_aliases[action.id] = action.id
    if baseline_structure is not None:
        action = structural_action(
            "refactor-v2.refactor-structural-improvement-v2", baseline_structure
        )
        baseline_actions[action.id] = action
        baseline_aliases[action.id] = action.id
    if final_contract is not None:
        action = public_action("refactor-v2.refactor-public-contract-stability-v2", final_contract)
        final_actions[action.id] = action
        final_aliases[action.id] = action.id
    if baseline_contract is not None:
        action = public_action(
            "refactor-v2.refactor-public-contract-stability-v2", baseline_contract
        )
        baseline_actions[action.id] = action
        baseline_aliases[action.id] = action.id
    return replace(
        context,
        verification_evidence=VerificationEvidence(actions=final_actions, aliases=final_aliases),
        baseline_verification_evidence=VerificationEvidence(
            actions=baseline_actions, aliases=baseline_aliases
        ),
    )


@pytest.mark.parametrize(("baseline", "final"), [(3, 3), (3, 4)])
def test_refactor_structural_snapshot_requires_declared_improvement(
    tmp_path: Path, baseline: int, final: int
) -> None:
    scores = {
        score.metric_id: score
        for score in RefactorV2()
        .collect_evidence(
            _refactor_snapshot_context(
                _workspace(tmp_path),
                baseline_structure=baseline,
                final_structure=final,
                baseline_contract=("add(left, right)",),
                final_contract=("add(left, right)",),
            )
        )
        .metric_scores
    }

    assert not scores["refactor-structural-improvement-v2"].passed


def test_refactor_structural_snapshot_accepts_declared_improvement(tmp_path: Path) -> None:
    scores = {
        score.metric_id: score
        for score in RefactorV2()
        .collect_evidence(
            _refactor_snapshot_context(
                _workspace(tmp_path),
                baseline_structure=4,
                final_structure=3,
                baseline_contract=("add(left, right)",),
                final_contract=("add(left, right)",),
            )
        )
        .metric_scores
    }

    assert scores["refactor-structural-improvement-v2"].passed


def test_refactor_public_snapshot_rejects_undeclared_contract_drift(tmp_path: Path) -> None:
    scores = {
        score.metric_id: score
        for score in RefactorV2()
        .collect_evidence(
            _refactor_snapshot_context(
                _workspace(tmp_path),
                baseline_structure=4,
                final_structure=3,
                baseline_contract=("add(left, right)",),
                final_contract=("add(left, right)", "subtract(left, right)"),
            )
        )
        .metric_scores
    }

    assert not scores["refactor-public-contract-stability-v2"].passed


def test_refactor_public_snapshot_accepts_identical_declared_contract(tmp_path: Path) -> None:
    scores = {
        score.metric_id: score
        for score in RefactorV2()
        .collect_evidence(
            _refactor_snapshot_context(
                _workspace(tmp_path),
                baseline_structure=4,
                final_structure=3,
                baseline_contract=("add(left, right)",),
                final_contract=("add(left, right)",),
            )
        )
        .metric_scores
    }

    assert scores["refactor-public-contract-stability-v2"].passed


def test_refactor_snapshot_metrics_fail_closed_when_snapshot_is_missing(tmp_path: Path) -> None:
    scores = {
        score.metric_id: score
        for score in RefactorV2()
        .collect_evidence(
            _refactor_snapshot_context(
                _workspace(tmp_path),
                baseline_structure=4,
                baseline_contract=("add(left, right)",),
            )
        )
        .metric_scores
    }

    assert not scores["refactor-structural-improvement-v2"].passed
    assert not scores["refactor-public-contract-stability-v2"].passed


def test_test_generation_coverage_lift_requires_matching_paired_artifacts(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    final_only = {
        score.metric_id: score
        for score in TestGenerationV2().collect_evidence(_v2_context(workspace)).metric_scores
    }
    lifted = {
        score.metric_id: score
        for score in TestGenerationV2()
        .collect_evidence(_v2_context(workspace, baseline_coverage=0.5))
        .metric_scores
    }

    assert not final_only["test-generation-coverage-lift-v2"].passed
    assert lifted["test-generation-coverage-lift-v2"].passed
    assert lifted["test-generation-coverage-lift-v2"].score == 1.0


def test_plan_to_code_uses_only_content_addressed_scenario_plan(
    monkeypatch, tmp_path: Path
) -> None:
    scenario_dir = tmp_path / "scenario"
    workspace = _workspace(tmp_path / "workspace")
    content = (
        "## Feature Dashboard\n\n"
        "| Feature | Status | Evidence Passed | Changed Surfaces |\n"
        "| --- | --- | --- | --- |\n"
        "| F1 | passed | 1 | src/app.py |\n\n"
        "## Acceptance Tracker\n\n"
        "| AC | Status | Passing Evidence |\n"
        "| --- | --- | --- |\n"
        "| AC1 | passed | pytest |\n"
    )
    authoritative = _write_authoritative_plan(scenario_dir, content)
    _write_authoritative_plan(
        workspace,
        content.replace("src/app.py", "src/workspace_replacement.py"),
    )
    observed = {}

    def fake_judge(**kwargs):
        observed.update(kwargs["retained_evidence"])
        return MetricScore(
            metric_id="plan-to-code-plan-adherence-v2",
            score=1.0,
            passed=True,
            evidence="judge",
        )

    monkeypatch.setattr("raidar.scorers.plan_to_code.evaluate_llm_as_judge_metric", fake_judge)
    context = replace(_v2_context(workspace), scenario_dir=scenario_dir)
    scores = {
        score.metric_id: score for score in PlanToCodeV2().collect_evidence(context).metric_scores
    }

    assert observed["sha256"] == sha256(content.encode()).hexdigest()
    assert observed["path"] == str(authoritative.relative_to(scenario_dir))
    assert scores["plan-to-code-planned-scope-coverage-v2"].passed


def test_plan_to_code_rejects_absent_or_invalid_authoritative_plan(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "raidar.scorers.plan_to_code.evaluate_llm_as_judge_metric",
        lambda **_kwargs: MetricScore(
            metric_id="plan-to-code-plan-adherence-v2",
            score=0.0,
            passed=False,
            evidence="judge",
        ),
    )
    absent = {
        score.metric_id: score
        for score in PlanToCodeV2()
        .collect_evidence(_v2_context(_workspace(tmp_path / "absent")))
        .metric_scores
    }
    invalid_root = tmp_path / "invalid"
    workspace = _workspace(invalid_root)
    invalid_path = invalid_root / ".enaible" / "intent-plan" / ("0" * 64) / "intentplan.md"
    invalid_path.parent.mkdir(parents=True)
    invalid_path.write_text("## Feature Dashboard", encoding="utf-8")
    invalid = {
        score.metric_id: score
        for score in PlanToCodeV2().collect_evidence(_v2_context(workspace)).metric_scores
    }

    for scores in (absent, invalid):
        assert not scores["plan-to-code-planned-scope-coverage-v2"].passed
        assert scores["plan-to-code-planned-scope-coverage-v2"].missing_patterns == [
            "authoritative plan feature evidence"
        ]
        assert not scores["plan-to-code-acceptance-evidence-completeness-v2"].passed
