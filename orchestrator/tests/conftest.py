"""Shared test fixtures for agentic evaluation system."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_eval.schemas.events import GateEvent
from agentic_eval.schemas.scorecard import (
    ComplianceCheck,
    ComplianceScore,
    EfficiencyScore,
    EvalConfig,
    EvalRun,
    FunctionalScore,
    Scorecard,
    VisualScore,
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
        task_name="test-task",
        agent="codex-cli",
        model="openai/gpt-4o",
        rules_variant="strict",
        duration_sec=120.5,
        functional=FunctionalScore(
            passed=True,
            tests_passed=8,
            tests_total=10,
            build_succeeded=True,
            gates_passed=3,
            gates_total=3,
        ),
        compliance=ComplianceScore(
            checks=[
                ComplianceCheck(rule="Use React", type="deterministic", passed=True),
                ComplianceCheck(rule="No console.log", type="deterministic", passed=True),
                ComplianceCheck(rule="Code quality", type="llm_judge", passed=True),
            ]
        ),
        visual=VisualScore(similarity=0.95),
        efficiency=EfficiencyScore(
            total_gate_failures=1,
            unique_failure_categories=1,
            repeat_failures=0,
        ),
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
            rules_variant="strict",
            task_name="test-task",
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
