"""Tests for compliance scoring and judge parsing."""

from agentic_eval.scoring.compliance import (
    JudgeResult,
    parse_judge_response,
)


class TestParseJudgeResponse:
    """Test LLM judge response parsing."""

    def test_parses_structured_pass(self):
        """Should parse structured PASS response."""
        response = "VERDICT: PASS\nEVIDENCE: Code follows all guidelines."
        result = parse_judge_response(response)

        assert result.passed is True
        assert "follows all guidelines" in result.evidence
        assert result.raw_response == response

    def test_parses_structured_fail(self):
        """Should parse structured FAIL response."""
        response = "VERDICT: FAIL\nEVIDENCE: Missing required imports."
        result = parse_judge_response(response)

        assert result.passed is False
        assert "Missing required" in result.evidence

    def test_parses_first_line_pass(self):
        """Should parse PASS in first line."""
        response = "PASS - the implementation looks good\nSome more details here."
        result = parse_judge_response(response)

        assert result.passed is True

    def test_parses_first_line_fail(self):
        """Should parse FAIL in first line."""
        response = "FAIL\nThe code does not meet requirements."
        result = parse_judge_response(response)

        assert result.passed is False

    def test_fails_conservatively_when_unparseable(self):
        """Should fail conservatively when response is unparseable."""
        response = "The code quality is acceptable overall."
        result = parse_judge_response(response)

        assert result.passed is False
        assert "Could not parse" in result.evidence

    def test_handles_case_insensitive_verdict(self):
        """Should handle case-insensitive VERDICT."""
        response = "verdict: pass\nevidence: Good code"
        result = parse_judge_response(response)

        assert result.passed is True

    def test_handles_multiline_evidence(self):
        """Should handle multiline evidence."""
        response = """VERDICT: PASS
EVIDENCE: The code follows best practices.
It uses proper typing and has good structure."""
        result = parse_judge_response(response)

        assert result.passed is True
        assert "follows best practices" in result.evidence

    def test_handles_pass_and_fail_in_same_line(self):
        """Should fail when both PASS and FAIL in first line (ambiguous)."""
        response = "PASS or FAIL depends on interpretation"
        result = parse_judge_response(response)

        # When both appear, FAIL takes precedence (conservative)
        assert result.passed is False

    def test_preserves_raw_response(self):
        """Should preserve raw response in result."""
        response = "VERDICT: PASS\nEVIDENCE: All good"
        result = parse_judge_response(response)

        assert result.raw_response == response

    def test_handles_whitespace(self):
        """Should handle leading/trailing whitespace."""
        response = "  \n  VERDICT: PASS  \n  EVIDENCE: Good  \n  "
        result = parse_judge_response(response)

        assert result.passed is True


class TestJudgeResult:
    """Test JudgeResult dataclass."""

    def test_dataclass_creation(self):
        """Should create JudgeResult correctly."""
        result = JudgeResult(
            passed=True,
            evidence="Test evidence",
            raw_response="raw",
        )

        assert result.passed is True
        assert result.evidence == "Test evidence"
        assert result.raw_response == "raw"
