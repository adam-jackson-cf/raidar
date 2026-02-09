"""Tests for scorecard computed fields."""

from agentic_eval.schemas.scorecard import (
    ComplianceCheck,
    ComplianceScore,
    EfficiencyScore,
    FunctionalScore,
    OptimizationScore,
    QualificationCheck,
    QualificationScore,
    Scorecard,
    VisualScore,
)


class TestFunctionalScore:
    """Test FunctionalScore computed fields."""

    def test_score_zero_when_build_fails(self):
        """Score should be 0 when build fails."""
        score = FunctionalScore(build_succeeded=False, tests_passed=5, tests_total=5)
        assert score.score == 0.0

    def test_score_one_when_all_tests_pass(self):
        """Score should be 1 when all tests pass."""
        score = FunctionalScore(
            build_succeeded=True,
            tests_passed=10,
            tests_total=10,
            passed=True,
        )
        assert score.score == 1.0

    def test_score_partial_when_some_tests_fail(self):
        """Score should be partial when some tests fail."""
        score = FunctionalScore(
            build_succeeded=True,
            tests_passed=7,
            tests_total=10,
        )
        assert score.score == 0.7

    def test_score_one_when_no_tests(self):
        """Score should be 1 when no tests exist but passed."""
        score = FunctionalScore(
            build_succeeded=True,
            tests_passed=0,
            tests_total=0,
            passed=True,
        )
        assert score.score == 1.0


class TestComplianceScore:
    """Test ComplianceScore computed fields."""

    def test_score_one_when_no_checks(self):
        """Score should be 1 when no checks configured."""
        score = ComplianceScore(checks=[])
        assert score.score == 1.0

    def test_score_one_when_all_pass(self):
        """Score should be 1 when all checks pass."""
        checks = [
            ComplianceCheck(rule="Rule 1", type="deterministic", passed=True),
            ComplianceCheck(rule="Rule 2", type="deterministic", passed=True),
        ]
        score = ComplianceScore(checks=checks)
        assert score.score == 1.0

    def test_score_zero_when_all_fail(self):
        """Score should be 0 when all checks fail."""
        checks = [
            ComplianceCheck(rule="Rule 1", type="deterministic", passed=False),
            ComplianceCheck(rule="Rule 2", type="deterministic", passed=False),
        ]
        score = ComplianceScore(checks=checks)
        assert score.score == 0.0

    def test_score_partial_when_some_fail(self):
        """Score should be partial when some checks fail."""
        checks = [
            ComplianceCheck(rule="Rule 1", type="deterministic", passed=True),
            ComplianceCheck(rule="Rule 2", type="deterministic", passed=False),
        ]
        score = ComplianceScore(checks=checks)
        assert score.score == 0.5


class TestEfficiencyScore:
    """Test EfficiencyScore computed fields."""

    def test_score_one_when_no_failures(self):
        """Score should be 1 when no gate failures."""
        score = EfficiencyScore(total_gate_failures=0, repeat_failures=0)
        assert score.score == 1.0

    def test_score_decreases_with_failures(self):
        """Score should decrease with gate failures."""
        score = EfficiencyScore(total_gate_failures=2, repeat_failures=0)
        assert score.score < 1.0
        assert score.score > 0.0

    def test_score_decreases_more_with_repeats(self):
        """Repeat failures should decrease score more."""
        no_repeat = EfficiencyScore(total_gate_failures=2, repeat_failures=0)
        with_repeat = EfficiencyScore(total_gate_failures=2, repeat_failures=1)
        assert with_repeat.score < no_repeat.score

    def test_score_clamped_to_zero(self):
        """Score should not go below 0."""
        score = EfficiencyScore(total_gate_failures=100, repeat_failures=100)
        assert score.score == 0.0


class TestVisualScore:
    """Test VisualScore computed fields."""

    def test_score_equals_similarity(self):
        """Score should equal similarity."""
        score = VisualScore(similarity=0.85)
        assert score.score == 0.85


class TestScorecardComposite:
    """Test Scorecard composite score calculation."""

    def test_composite_with_visual(self):
        """Composite should use all weights when visual present."""
        scorecard = Scorecard(
            functional=FunctionalScore(
                passed=True, build_succeeded=True, tests_passed=10, tests_total=10
            ),
            compliance=ComplianceScore(),
            visual=VisualScore(similarity=1.0),
            efficiency=EfficiencyScore(),
        )
        # All scores are 1.0, so composite should be 1.0
        assert abs(scorecard.composite_score - 1.0) < 0.001

    def test_composite_without_visual(self):
        """Composite should redistribute visual weight when visual None."""
        scorecard = Scorecard(
            functional=FunctionalScore(
                passed=True, build_succeeded=True, tests_passed=10, tests_total=10
            ),
            compliance=ComplianceScore(),
            visual=None,
            efficiency=EfficiencyScore(),
        )
        # All scores are 1.0, so composite should still be 1.0
        assert abs(scorecard.composite_score - 1.0) < 0.001

    def test_composite_with_mixed_scores(self):
        """Quality score should weight quality dimensions correctly."""
        scorecard = Scorecard(
            functional=FunctionalScore(
                passed=True, build_succeeded=True, tests_passed=5, tests_total=10
            ),  # 0.5
            compliance=ComplianceScore(),  # 1.0
            visual=VisualScore(similarity=0.8),  # 0.8
            efficiency=EfficiencyScore(),  # 1.0
        )
        # 0.5*0.4 + 1.0*0.25 + 0.8*0.2 + 1.0*0.15 = 0.2 + 0.25 + 0.16 + 0.15 = 0.76
        assert abs(scorecard.quality_score - 0.76) < 0.001

    def test_composite_zero_when_unqualified(self):
        """Composite score must be 0 when qualification checks fail."""
        scorecard = Scorecard(
            qualification=QualificationScore(
                checks=[
                    QualificationCheck(
                        name="quality_gates_passed",
                        passed=False,
                        evidence="lint failed",
                    )
                ]
            )
        )
        assert scorecard.composite_score == 0.0

    def test_composite_uses_optimization_when_qualified(self):
        """Composite score should use optimization score after qualification."""
        scorecard = Scorecard(
            qualification=QualificationScore(
                checks=[
                    QualificationCheck(
                        name="quality_gates_passed",
                        passed=True,
                        evidence="all gates passed",
                    )
                ]
            ),
            optimization=OptimizationScore(
                uncached_input_tokens=150000,
                output_tokens=2000,
                command_count=8,
                failed_command_count=1,
                verification_rounds=1,
                repeated_verification_failures=0,
            ),
        )
        assert scorecard.composite_score == scorecard.optimization.score

    def test_composite_zero_when_voided(self):
        """Composite score must be 0 when run is voided."""
        scorecard = Scorecard(
            voided=True,
            void_reasons=["provider_rate_limit"],
            qualification=QualificationScore(
                checks=[
                    QualificationCheck(
                        name="quality_gates_passed",
                        passed=True,
                        evidence="all gates passed",
                    )
                ]
            ),
            optimization=OptimizationScore(
                uncached_input_tokens=100,
                output_tokens=20,
                command_count=2,
                failed_command_count=0,
                verification_rounds=1,
                repeated_verification_failures=0,
            ),
        )
        assert scorecard.composite_score == 0.0

    def test_diagnostic_score_available_when_unqualified(self):
        """Diagnostic score should remain available for failed runs."""
        scorecard = Scorecard(
            qualification=QualificationScore(
                checks=[
                    QualificationCheck(
                        name="no_requirement_test_gaps",
                        passed=False,
                        evidence="mapped=2/4",
                    )
                ]
            )
        )
        assert scorecard.composite_score == 0.0
        assert scorecard.diagnostic_score > 0.0
