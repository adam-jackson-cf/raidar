"""Harness process metrics assembly services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from raidar.harness import harness_definition
from raidar.harness.command_records import _command_records_for_harness
from raidar.harness.usage_metrics import _usage_tuple_for_harness
from raidar.runtime.models import CommandRecord, ProcessMetrics
from raidar.runtime.verification_metrics import (
    _count_executed_required,
    _count_failed_commands,
    _count_process_failed_commands,
    _count_repeated_failures,
    _failure_category_counts,
    _first_pass_counts,
    _first_pass_status,
    _git_commit_bypass_commands,
    _verification_attempts,
    _verification_command_strings,
)
from raidar.schemas.scenario import ScenarioDefinition


@dataclass(frozen=True, slots=True)
class ProcessMetricsBuildInput:
    """Parsed process evidence used to assemble resource metrics."""

    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    records: list[CommandRecord]
    git_commit_records: list[CommandRecord]
    verification_patterns: list[str]
    attempts_by_pattern: dict[str, int]
    failures_by_pattern: dict[str, int]
    first_pass_status: dict[str, str]
    failure_categories: dict[str, int]


class ProcessMetricsError(RuntimeError):
    """Typed process metrics extraction failure."""

    def __init__(self, message: str, *, failure_code: str) -> None:
        super().__init__(message)
        self.failure_code = failure_code


def _empty_process_metrics() -> ProcessMetrics:
    return ProcessMetrics(
        uncached_input_tokens=0,
        output_tokens=0,
        command_count=0,
        failed_command_count=0,
        process_failed_command_count=0,
        verification_rounds=0,
        repeated_verification_failures=0,
        required_verification_commands=0,
        executed_required_verification_commands=0,
    )


def collect_process_metrics(
    scenario: ScenarioDefinition,
    trial_dir: Path | None,
    *,
    harness: str,
) -> ProcessMetrics:
    """Collect resource-efficiency metrics from harness execution logs."""
    if not trial_dir:
        return _empty_process_metrics()

    definition = harness_definition(harness)
    usage_tuple = _usage_tuple_for_harness(trial_dir, harness)
    if usage_tuple is None:
        if definition.usage_policy.required:
            raise ProcessMetricsError(
                f"Missing token usage metrics for harness `{harness}` in trial `{trial_dir}`.",
                failure_code="missing_token_usage",
            )
        usage_tuple = (0, 0, 0)
    input_tokens, cached_input_tokens, output_tokens = usage_tuple

    verification_patterns = _verification_command_strings(scenario)
    verification_pattern_tuple = tuple(verification_patterns)
    records = _command_records_for_harness(
        trial_dir,
        harness,
        verification_patterns=verification_pattern_tuple,
    )
    git_commit_records = _command_records_for_harness(
        trial_dir,
        harness,
        include_git_commit=True,
        verification_patterns=verification_pattern_tuple,
    )
    attempts_by_pattern, failures_by_pattern = _verification_attempts(
        records, verification_patterns
    )
    first_pass_status = _first_pass_status(records, verification_patterns)
    failure_categories = _failure_category_counts(records)
    return _process_metrics_from_records(
        ProcessMetricsBuildInput(
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            records=records,
            git_commit_records=git_commit_records,
            verification_patterns=verification_patterns,
            attempts_by_pattern=attempts_by_pattern,
            failures_by_pattern=failures_by_pattern,
            first_pass_status=first_pass_status,
            failure_categories=failure_categories,
        )
    )


def _process_metrics_from_records(request: ProcessMetricsBuildInput) -> ProcessMetrics:
    uncached_input_tokens = max(0, request.input_tokens - request.cached_input_tokens)
    command_count = len(request.records)
    failed_command_count = _count_failed_commands(request.records)
    process_failed_command_count = _count_process_failed_commands(request.failure_categories)
    verification_rounds = max(request.attempts_by_pattern.values(), default=0)
    repeated_failures = _count_repeated_failures(request.failures_by_pattern)
    executed_required = _count_executed_required(request.attempts_by_pattern)
    first_pass_successes, first_pass_failures, missing_required = _first_pass_counts(
        request.first_pass_status
    )
    return ProcessMetrics(
        uncached_input_tokens=uncached_input_tokens,
        output_tokens=request.output_tokens,
        command_count=command_count,
        failed_command_count=failed_command_count,
        process_failed_command_count=process_failed_command_count,
        verification_rounds=verification_rounds,
        repeated_verification_failures=repeated_failures,
        required_verification_commands=len(request.verification_patterns),
        executed_required_verification_commands=executed_required,
        failed_command_categories=request.failure_categories,
        required_verification_first_pass=request.first_pass_status,
        first_pass_verification_successes=first_pass_successes,
        first_pass_verification_failures=first_pass_failures,
        missing_required_verification_commands=missing_required,
        git_commit_verification_bypass_commands=_git_commit_bypass_commands(
            request.git_commit_records
        ),
    )
