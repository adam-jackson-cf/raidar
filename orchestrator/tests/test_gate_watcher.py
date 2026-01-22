"""Tests for gate watcher functionality."""

from agentic_eval.schemas.events import GateEvent
from agentic_eval.watcher.gate_watcher import (
    GateWatcher,
    categorize_failure,
    truncate_output,
)


class TestCategorizeFailure:
    """Test failure categorization."""

    def test_categorizes_type_error(self):
        """Should categorize TypeScript errors."""
        category = categorize_failure("", "TS2345: Argument not assignable")
        assert category == "type_error"

    def test_categorizes_lint_unused(self):
        """Should categorize unused variable lint errors."""
        category = categorize_failure("no-unused-vars: 'x' is defined but never used", "")
        assert category == "lint_unused"

    def test_categorizes_test_assertion(self):
        """Should categorize test assertion errors."""
        category = categorize_failure("", "AssertionError: expected 5 to equal 6")
        assert category == "test_assertion"

    def test_categorizes_build_module(self):
        """Should categorize missing module errors."""
        category = categorize_failure("Cannot find module 'react'", "")
        assert category == "build_module"

    def test_returns_unknown_for_unmatched(self):
        """Should return unknown for unmatched errors."""
        category = categorize_failure("Some random error", "output")
        assert category == "unknown"

    def test_returns_none_for_empty(self):
        """Should return None for empty output."""
        category = categorize_failure("", "")
        assert category is None


class TestTruncateOutput:
    """Test output truncation."""

    def test_no_truncation_when_under_limit(self):
        """Should not truncate when under limit."""
        output = "short output"
        result = truncate_output(output, max_length=100)
        assert result == output

    def test_truncates_when_over_limit(self):
        """Should truncate when over limit."""
        output = "x" * 100
        result = truncate_output(output, max_length=50)
        assert len(result) < len(output)
        assert "truncated" in result

    def test_includes_total_length_in_message(self):
        """Should include total length in truncation message."""
        output = "x" * 100
        result = truncate_output(output, max_length=50)
        assert "100" in result


class TestGateWatcher:
    """Test GateWatcher class."""

    def test_initial_state(self):
        """Should start with empty state."""
        watcher = GateWatcher()
        assert watcher.total_failures == 0
        assert len(watcher.events) == 0
        assert len(watcher.failure_categories_seen) == 0

    def test_custom_max_failures(self):
        """Should accept custom max failures."""
        watcher = GateWatcher(max_failures=5)
        assert watcher.max_failures == 5

    def test_should_terminate_when_max_failures_reached(self):
        """Should terminate when max failures reached."""
        watcher = GateWatcher(max_failures=2)
        watcher.total_failures = 2
        assert watcher.should_terminate()

    def test_should_not_terminate_under_max(self):
        """Should not terminate under max failures."""
        watcher = GateWatcher(max_failures=3)
        watcher.total_failures = 2
        assert not watcher.should_terminate()

    def test_get_summary(self, sample_gate_event: GateEvent, failed_gate_event: GateEvent):
        """Should return correct summary."""
        watcher = GateWatcher()
        watcher.events = [sample_gate_event, failed_gate_event]
        watcher.total_failures = 1
        watcher.failure_categories_seen = {"type_error"}

        summary = watcher.get_summary()
        assert summary["total_gates"] == 2
        assert summary["passed"] == 1
        assert summary["failed"] == 1
        assert summary["unique_failure_categories"] == 1

    def test_tracks_repeat_failures(self):
        """Should track repeat failure categories."""
        watcher = GateWatcher()
        watcher.failure_categories_seen.add("type_error")

        # Create a repeat event
        event = GateEvent(
            timestamp="2024-01-01T00:00:00Z",
            gate_name="test",
            command="test",
            exit_code=1,
            stdout="",
            stderr="TS2345: error",
            failure_category="type_error",
            is_repeat=True,
        )
        watcher.events.append(event)

        summary = watcher.get_summary()
        assert summary["repeat_failures"] == 1
