"""Tests for promotion-ready proposed scorer evidence contracts."""

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
from raidar.scorers.code_task.bugfix import Bugfix
from raidar.scorers.code_task.refactor import Refactor
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
)
from raidar.scorers.plan_to_code import PlanToCode
from raidar.scorers.test_generation import TestGeneration
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
        id="test-generation.coverage.python",
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
            "test-generation.coverage.python": coverage_result.id,
            "test-generation.coverage.typescript": coverage_result.id,
        }
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
    )


def _workspace(tmp_path: Path) -> Path:
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
    assert not scores["defect-resolution"].passed
    assert scores["defect-resolution"].missing_patterns == ["defect-linked requirement evidence"]
    assert not scores["regression-protection"].passed
    assert "proxy:" in str(scores["regression-protection"].evidence)
    assert "proxy:" in str(scores["change-containment"].evidence)
    assert not scores["change-containment"].passed


def test_bugfix_defect_resolution_requires_defect_linked_requirement_evidence(
    tmp_path: Path,
) -> None:
    context = _context(
        _workspace(tmp_path),
        requirements=RequirementsCoverageScore(total_requirements=1, satisfied_requirements=1),
    )

    scores = {score.metric_id: score for score in Bugfix().collect_evidence(context).metric_scores}

    assert scores["defect-resolution"].passed
    assert scores["defect-resolution"].score == 1.0


def test_bugfix_defect_resolution_handles_malformed_requirement_evidence(
    tmp_path: Path,
) -> None:
    context = _context(_workspace(tmp_path), requirements=SimpleNamespace(total_requirements="bad"))

    scores = {score.metric_id: score for score in Bugfix().collect_evidence(context).metric_scores}

    assert not scores["defect-resolution"].passed
    assert scores["defect-resolution"].score == 0.0


def test_bugfix_defect_evidence_requires_explicit_retained_fields(tmp_path: Path) -> None:
    context = _context(
        _workspace(tmp_path),
        requirements=RequirementsCoverageScore(total_requirements=1, satisfied_requirements=1),
        workspace_changes={
            "changed_files": ["src/app.py", "tests/test_app.py"],
            "changed_file_count": 2,
            "error": None,
        },
        retained_evidence={
            "reproduction_note": "Observed failing defect before fix.",
            "regression_tests": ["tests/test_app.py::test_req_add"],
            "verification_evidence": "pytest passed",
        },
    )

    scores = {score.metric_id: score for score in Bugfix().collect_evidence(context).metric_scores}

    assert scores["defect-evidence-completeness"].passed
    assert scores["defect-evidence-completeness"].score == 1.0


def test_bugfix_defect_evidence_does_not_pass_from_source_inventory(tmp_path: Path) -> None:
    scores = {
        score.metric_id: score
        for score in Bugfix().collect_evidence(_context(_workspace(tmp_path))).metric_scores
    }

    assert not scores["defect-evidence-completeness"].passed
    assert "reproduction note" in scores["defect-evidence-completeness"].missing_patterns


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
            metric_id="plan-adherence",
            score=1.0,
            passed=True,
            evidence="judge passed",
        )

    monkeypatch.setattr("raidar.scorers.plan_to_code.evaluate_llm_as_judge_metric", fake_judge)
    context = _context(
        _workspace(tmp_path),
        functional=FunctionalScore(
            passed=False,
            tests_passed=0,
            tests_total=2,
            build_succeeded=True,
        ),
    )
    scores = {
        score.metric_id: score for score in PlanToCode().collect_evidence(context).metric_scores
    }

    assert scores["plan-adherence"].score == 0.0
    assert scores["plan-adherence"].passed is False
    assert "deterministic functional execution failed" in str(scores["plan-adherence"].evidence)
    assert scores["planned-scope-coverage"].passed is False
    assert "proxy:" in str(scores["planned-scope-coverage"].evidence)


def test_plan_to_code_scores_retained_plan_packet(monkeypatch, tmp_path: Path) -> None:
    plan_dir = tmp_path / ".enaible" / "intent-plan" / "run"
    plan_dir.mkdir(parents=True)
    (plan_dir / "intentplan.md").write_text(
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
        encoding="utf-8",
    )

    def fake_judge(**kwargs):
        assert kwargs["retained_evidence"]["feature_count"] == 2
        assert kwargs["changed_surfaces"] == ["src/app.py", "tests/test_app.py"]
        assert kwargs["deterministic_metric_scores"]
        functional = next(
            score
            for score in kwargs["deterministic_metric_scores"]
            if score.metric_id == "functional"
        )
        assert functional.passed is True
        assert "execution_outputs" not in kwargs
        return MetricScore(
            metric_id="plan-adherence",
            score=1.0,
            passed=True,
            evidence="judge passed",
        )

    monkeypatch.setattr("raidar.scorers.plan_to_code.evaluate_llm_as_judge_metric", fake_judge)
    scores = {
        score.metric_id: score
        for score in PlanToCode()
        .collect_evidence(
            _context(
                _workspace(tmp_path),
                workspace_changes={
                    "changed_files": ["src/app.py", "tests/test_app.py"],
                    "changed_file_count": 2,
                    "error": None,
                },
            )
        )
        .metric_scores
    }

    assert scores["planned-scope-coverage"].passed
    assert scores["acceptance-evidence-completeness"].passed
    assert "direct:" in str(scores["planned-scope-coverage"].evidence)


def test_plan_to_code_judge_receives_retained_changed_files(monkeypatch, tmp_path: Path) -> None:
    plan_dir = tmp_path / ".enaible" / "intent-plan" / "run"
    plan_dir.mkdir(parents=True)
    (plan_dir / "intentplan.md").write_text(
        "## Feature Dashboard\n\n"
        "| Feature | Status | Evidence Passed | Changed Surfaces |\n"
        "| --- | --- | --- | --- |\n"
        "| F1 | passed | 1 | src/app.py |\n\n"
        "## Acceptance Tracker\n\n"
        "| AC | Status | Passing Evidence |\n"
        "| --- | --- | --- |\n"
        "| AC1 | passed | pytest |\n",
        encoding="utf-8",
    )
    observed = {}

    def fake_judge(**kwargs):
        observed["changed_surfaces"] = kwargs["changed_surfaces"]
        return MetricScore(
            metric_id="plan-adherence",
            score=1.0,
            passed=True,
            evidence="judge passed",
        )

    monkeypatch.setattr("raidar.scorers.plan_to_code.evaluate_llm_as_judge_metric", fake_judge)
    PlanToCode().collect_evidence(
        _context(
            _workspace(tmp_path),
            workspace_changes={
                "changed_files": ["src/app.py"],
                "changed_file_count": 1,
                "error": None,
            },
        )
    )

    assert observed["changed_surfaces"] == ["src/app.py"]


def test_plan_to_code_rejects_status_only_retained_plan_packet(monkeypatch, tmp_path: Path) -> None:
    plan_dir = tmp_path / ".enaible" / "intent-plan" / "run"
    plan_dir.mkdir(parents=True)
    (plan_dir / "intentplan.md").write_text(
        "## Feature Dashboard\n\n"
        "| Feature | Status |\n"
        "| --- | --- |\n"
        "| F1 | passed |\n\n"
        "## Acceptance Tracker\n\n"
        "| AC | Status |\n"
        "| --- | --- |\n"
        "| AC1 | passed |\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "raidar.scorers.plan_to_code.evaluate_llm_as_judge_metric",
        lambda **_kwargs: MetricScore(
            metric_id="plan-adherence",
            score=0.0,
            passed=False,
            evidence="judge failed",
        ),
    )

    scores = {
        score.metric_id: score
        for score in PlanToCode().collect_evidence(_context(_workspace(tmp_path))).metric_scores
    }

    assert scores["planned-scope-coverage"].score == 0.0
    assert not scores["planned-scope-coverage"].passed
    assert scores["acceptance-evidence-completeness"].score == 0.0
    assert not scores["acceptance-evidence-completeness"].passed


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
    plan_dir = tmp_path / ".enaible" / "intent-plan" / "run"
    plan_dir.mkdir(parents=True)
    (plan_dir / "intentplan.md").write_text(
        "## Feature Dashboard\n\nnot a markdown status table\n\n"
        "## Acceptance Tracker\n\nalso malformed",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "raidar.scorers.plan_to_code.evaluate_llm_as_judge_metric",
        lambda **_kwargs: MetricScore(
            metric_id="plan-adherence",
            score=0.0,
            passed=False,
            evidence="judge failed",
        ),
    )

    scores = {
        score.metric_id: score
        for score in PlanToCode().collect_evidence(_context(_workspace(tmp_path))).metric_scores
    }

    assert scores["planned-scope-coverage"].score == 0.0
    assert not scores["planned-scope-coverage"].passed
    assert scores["acceptance-evidence-completeness"].score == 0.0
    assert not scores["acceptance-evidence-completeness"].passed
