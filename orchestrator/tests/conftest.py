"""Shared test fixtures for AgentSpec evaluation."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from raidar.schemas.events import GateEvent
from raidar.schemas.scorecard import (
    EvalConfig,
    EvalRun,
    MetricScore,
    Scorecard,
    ScorerMetricContribution,
    ScorerResult,
)


@pytest.fixture
def sample_gate_event() -> GateEvent:
    """Create a sample passing gate event."""
    return GateEvent(
        timestamp=datetime.now(UTC).isoformat(),
        gate_name="build",
        command="bun run build",
        exit_code=0,
        stdout="Build succeeded",
        stderr="",
        failure_category=None,
        is_repeat=False,
    )


@pytest.fixture
def failed_gate_event() -> GateEvent:
    """Create a sample failing gate event."""
    return GateEvent(
        timestamp=datetime.now(UTC).isoformat(),
        gate_name="typecheck",
        command="bun run typecheck",
        exit_code=1,
        stdout="",
        stderr="TS2345: Argument of type 'string' is not assignable",
        failure_category="type_error",
        is_repeat=False,
    )


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Create a minimal workspace with package.json."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Create package.json
    package_json = {
        "name": "test-project",
        "version": "1.0.0",
        "scripts": {
            "build": "echo build",
            "test": "echo test",
            "typecheck": "echo typecheck",
        },
    }
    (workspace / "package.json").write_text(json.dumps(package_json, indent=2))

    # Create src directory with sample file
    src_dir = workspace / "src"
    src_dir.mkdir()
    (src_dir / "index.tsx").write_text(
        """import React from 'react';
export const App = () => <div>Hello</div>;
"""
    )

    return workspace


@pytest.fixture
def sample_scorecard() -> Scorecard:
    """Create a scorecard with known scores."""
    return Scorecard(
        run_id="test-run-001",
        scenario_name="test-task",
        scenario_revision="v001",
        harness="codex-cli",
        model="openai/gpt-4o",
        starter_root="starter",
        duration_sec=120.5,
        metadata={
            "starter": {
                "scenario": "test-task",
                "scenario_revision": "v001",
                "root": "starter",
                "fingerprint": "abc123",
            }
        },
        metric_scores=[
            MetricScore(metric_id="functional", score=0.8, passed=True),
            MetricScore(metric_id="visual-regression", score=0.95, passed=True),
            MetricScore(metric_id="verification-stability", score=0.75, passed=True),
        ],
        scorer_results=[
            ScorerResult(
                scorer_id="typescript-code-task",
                version=1,
                category="quality",
                weight=1.0,
                score=0.825,
                metric_contributions=[
                    ScorerMetricContribution(
                        metric_id="functional",
                        weight=0.5,
                        score=0.8,
                        weighted_score=0.4,
                    ),
                    ScorerMetricContribution(
                        metric_id="visual-regression",
                        weight=0.25,
                        score=0.95,
                        weighted_score=0.2375,
                    ),
                    ScorerMetricContribution(
                        metric_id="verification-stability",
                        weight=0.25,
                        score=0.75,
                        weighted_score=0.1875,
                    ),
                ],
            )
        ],
    )


@pytest.fixture
def sample_eval_run(sample_scorecard: Scorecard) -> EvalRun:
    """Create a sample evaluation run."""
    return EvalRun(
        id="run-001",
        timestamp=datetime.now(UTC).isoformat(),
        config=EvalConfig(
            model="openai/gpt-4o",
            harness="codex-cli",
            scenario_name="test-task",
            scenario_revision="v001",
            starter_root="starter",
            evaluation_profile=(
                "functional+requirements-coverage+verification-stability+"
                "execution-validity+resource-efficiency"
            ),
        ),
        duration_sec=120.5,
        terminated_early=False,
        scores=sample_scorecard,
    )


@pytest.fixture
def tmp_results_dir(tmp_path: Path) -> Path:
    """Create a temporary results directory."""
    results = tmp_path / "results"
    results.mkdir()
    return results
