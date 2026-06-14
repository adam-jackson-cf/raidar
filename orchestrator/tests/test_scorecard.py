"""Tests for scorecard computed fields."""

from raidar.schemas.scorecard import (
    ExecutionValidityScore,
    FunctionalScore,
    GateCheck,
    ResourceEfficiencyScore,
    Scorecard,
    ScorerMetricContribution,
    ScorerResult,
    VerificationStabilityScore,
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


class TestVerificationStabilityScore:
    """Test VerificationStabilityScore computed fields."""

    def test_score_one_when_no_failures(self):
        """Score should be 1 when no gate failures."""
        score = VerificationStabilityScore(total_gate_failures=0, repeat_failures=0)
        assert score.score == 1.0

    def test_score_decreases_with_failures(self):
        """Score should decrease with gate failures."""
        score = VerificationStabilityScore(total_gate_failures=2, repeat_failures=0)
        assert score.score < 1.0
        assert score.score > 0.0

    def test_score_decreases_more_with_repeats(self):
        """Repeat failures should decrease score more."""
        no_repeat = VerificationStabilityScore(total_gate_failures=2, repeat_failures=0)
        with_repeat = VerificationStabilityScore(total_gate_failures=2, repeat_failures=1)
        assert with_repeat.score < no_repeat.score

    def test_score_clamped_to_zero(self):
        """Score should not go below 0."""
        score = VerificationStabilityScore(total_gate_failures=100, repeat_failures=100)
        assert score.score == 0.0


class TestVisualScore:
    """Test VisualScore computed fields."""

    def test_score_equals_similarity(self):
        """Score should equal similarity."""
        score = VisualScore(similarity=0.85)
        assert score.score == 0.85


class TestScorecardComposite:
    """Test Scorecard composite score calculation."""

    def test_composite_zero_without_quality_scorer_results(self):
        """Composite should not be lifted by resource efficiency alone."""
        scorecard = Scorecard(resource_efficiency=ResourceEfficiencyScore(command_count=1))
        assert scorecard.quality_score == 0.0
        assert scorecard.resource_efficiency.score > 0.0
        assert scorecard.composite_score == 0.0

    def test_quality_score_zero_without_quality_scorer_results(self):
        """Quality score should not fall back to legacy metric weights."""
        scorecard = Scorecard(
            functional=FunctionalScore(
                passed=True, build_succeeded=True, tests_passed=10, tests_total=10
            ),
            visual=None,
            verification_stability=VerificationStabilityScore(),
        )
        assert scorecard.quality_score == 0.0

    def test_composite_zero_when_invalid(self):
        """Composite score must be 0 when run validity checks fail."""
        scorecard = Scorecard(
            execution_validity=ExecutionValidityScore(
                checks=[
                    GateCheck(
                        name="quality_gates_passed",
                        passed=False,
                        evidence="lint failed",
                    )
                ]
            )
        )
        assert scorecard.composite_score == 0.0

    def test_composite_does_not_use_resource_efficiency_without_quality_when_valid(self):
        """Composite should still require delivered quality after run validity."""
        scorecard = Scorecard(
            execution_validity=ExecutionValidityScore(
                checks=[
                    GateCheck(
                        name="quality_gates_passed",
                        passed=True,
                        evidence="all gates passed",
                    )
                ]
            ),
            resource_efficiency=ResourceEfficiencyScore(
                uncached_input_tokens=150000,
                output_tokens=2000,
                command_count=8,
                failed_command_count=1,
                verification_rounds=1,
                repeated_verification_failures=0,
            ),
        )
        assert scorecard.resource_efficiency.score > 0.0
        assert scorecard.composite_score == 0.0

    def test_composite_uses_weighted_scorer_results_when_present(self):
        """Composite score should follow scenario-level scorer weights."""
        scorecard = Scorecard(
            scorer_results=[
                ScorerResult(
                    scorer_id="typescript-code-task",
                    version=1,
                    category="quality",
                    weight=0.8,
                    score=0.75,
                    metric_contributions=[
                        ScorerMetricContribution(
                            metric_id="functional",
                            weight=1.0,
                            score=0.75,
                            weighted_score=0.75,
                        )
                    ],
                ),
                ScorerResult(
                    scorer_id="resource-efficiency",
                    version=1,
                    category="efficiency",
                    weight=0.2,
                    score=0.5,
                ),
            ],
        )

        assert scorecard.quality_score == 0.75
        assert scorecard.composite_score == 0.7

    def test_composite_capped_by_weak_quality_with_high_efficiency(self):
        """Resource efficiency cannot lift composite above weak delivered quality."""
        scorecard = Scorecard(
            scorer_results=[
                ScorerResult(
                    scorer_id="python-code-task",
                    version=1,
                    category="quality",
                    weight=0.2,
                    score=0.25,
                    metric_contributions=[
                        ScorerMetricContribution(
                            metric_id="functional",
                            weight=1.0,
                            score=0.25,
                            weighted_score=0.25,
                        )
                    ],
                ),
                ScorerResult(
                    scorer_id="resource-efficiency",
                    version=1,
                    category="efficiency",
                    weight=0.8,
                    score=1.0,
                ),
            ],
        )

        assert scorecard.quality_score == 0.25
        assert scorecard.composite_score == 0.25

    def test_composite_zero_when_unscored(self):
        """Composite score must be 0 when run is unscored."""
        scorecard = Scorecard(
            unscored=True,
            unscored_reasons=["provider_rate_limit"],
            execution_validity=ExecutionValidityScore(
                checks=[
                    GateCheck(
                        name="quality_gates_passed",
                        passed=True,
                        evidence="all gates passed",
                    )
                ]
            ),
            resource_efficiency=ResourceEfficiencyScore(
                uncached_input_tokens=100,
                output_tokens=20,
                command_count=2,
                failed_command_count=0,
                verification_rounds=1,
                repeated_verification_failures=0,
            ),
        )
        assert scorecard.composite_score == 0.0

    def test_diagnostic_score_available_when_invalid(self):
        """Diagnostic score should remain available for failed runs."""
        scorecard = Scorecard(
            execution_validity=ExecutionValidityScore(
                checks=[
                    GateCheck(
                        name="requirement_test_gaps",
                        passed=False,
                        evidence="mapped=2/4",
                    )
                ]
            )
        )
        assert scorecard.composite_score == 0.0
        assert scorecard.diagnostic_score > 0.0
