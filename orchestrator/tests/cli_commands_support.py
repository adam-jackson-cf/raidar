"""Shared imports for CLI command tests."""

# ruff: noqa: F401

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import click
from click.testing import CliRunner

from raidar.application import execution
from raidar.application.models import ExecutionDispatchRequest, RunCliOptions
from raidar.application.repo_state import (
    assert_no_generated_artifact_changes,
    generated_artifact_paths,
)
from raidar.cli import main
from raidar.commands.experiments import archive_destination as _archive_destination
from raidar.commands.shared import (
    BENCHMARK_EXPERIMENTS_ROOT,
    ORCHESTRATOR_ROOT,
    RESEARCH_LOOP_EXPERIMENTS_ROOT,
    SuiteExecutionResult,
)
from raidar.commands.shared import (
    resolve_experiments_root as _resolve_experiments_root,
)
from raidar.schemas.scenario import ScenarioDefinition
from raidar.schemas.scorecard import EvalConfig, EvalRun, Scorecard

quality_gates = main.commands["quality"].commands["gates"]
