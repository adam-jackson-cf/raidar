"""Runtime workspace layout and preparation orchestration."""

from __future__ import annotations

import os
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from raidar.agents.config import Harness
from raidar.agents.rules import inject_rules
from raidar.runtime.models import (
    RunLayout,
    RunRequest,
    WorkspaceContext,
)
from raidar.runtime.task_bundle import (
    _baseline_workspace_for_request,
    _copy_baseline_workspace,
    _injected_rules_path,
)
from raidar.schemas.scenario import ScenarioDefinition


def load_scenario(scenario_path: Path) -> ScenarioDefinition:
    """Load scenario definition from YAML file."""
    with open(scenario_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return ScenarioDefinition.model_validate(data)


def _slug_fragment(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "unknown"


def _harness_value(harness: Harness | Any) -> str:
    return str(getattr(harness, "value", harness))


def _run_label(repeat_index: int) -> str:
    return f"run-{repeat_index:02d}"


def _repeat_workspace_dir(request: RunRequest) -> Path:
    return request.execution_dir / "runs" / _run_label(request.repeat_index) / "workspace"


def _workspace_runtime_env(
    workspace: Path, base_env: dict[str, str] | None = None
) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    tmp_dir = workspace / ".tmp"
    cache_dir = workspace / ".cache"
    uv_cache_dir = cache_dir / "uv"
    bun_cache_dir = cache_dir / "bun"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    uv_cache_dir.mkdir(parents=True, exist_ok=True)
    bun_cache_dir.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "TMPDIR": str(tmp_dir),
            "TMP": str(tmp_dir),
            "TEMP": str(tmp_dir),
            "XDG_CACHE_HOME": str(cache_dir),
            "UV_CACHE_DIR": str(uv_cache_dir),
            "BUN_INSTALL_CACHE_DIR": str(bun_cache_dir),
        }
    )
    return env


def scenario_evaluation_profile(scenario: ScenarioDefinition) -> str:
    """Derive deterministic evaluation-profile identifier for a scenario."""
    return "+".join(scenario_metrics(scenario))


def scenario_metrics(scenario: ScenarioDefinition) -> list[str]:
    """Return deterministic ordered metric ids for a scenario."""
    return scenario.metric_ids()


def prepare_workspace(
    *,
    starter_dir: Path,
    target_dir: Path,
    scenario_dir: Path,
    harness: Harness,
) -> tuple[Path, Path | None]:
    """Prepare workspace by copying the starter and injecting rules.

    Args:
        starter_dir: Path to resolved starter template/version
        target_dir: Path to create workspace
        scenario_dir: Path to scenario directory (contains rules/)
        harness: Harness id for rule file selection
    Returns:
        Tuple of workspace path and injected rules file (if any)
    """
    # Copy the starter into the run workspace.
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(
        starter_dir,
        target_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("node_modules", ".next", "jobs"),
    )

    # Inject rules
    injected_rules: Path | None = None
    rules_dir = scenario_dir / "rules"
    if rules_dir.exists():
        injected_rules = inject_rules(rules_dir, target_dir, harness)

    return target_dir, injected_rules


def initialize_run(request: RunRequest) -> RunLayout:
    """Create run ids and canonical output directories."""
    run_id = str(uuid.uuid4())[:8]
    start_time = datetime.now(UTC)
    runs_root = request.execution_dir / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    run_label = _run_label(request.repeat_index)
    root_dir = runs_root / run_label
    if root_dir.exists():
        shutil.rmtree(root_dir)
    workspace_dir = root_dir / "workspace"
    verifier_dir = root_dir / "verifier"
    harness_dir = root_dir / "harness"
    harbor_dir = root_dir / "harbor"
    for path in (workspace_dir, verifier_dir, harness_dir, harbor_dir):
        path.mkdir(parents=True, exist_ok=True)
    return RunLayout(
        run_id=run_id,
        start_time=start_time,
        run_label=run_label,
        root_dir=root_dir,
        workspace_dir=workspace_dir,
        verifier_dir=verifier_dir,
        harness_dir=harness_dir,
        harbor_dir=harbor_dir,
        run_json_path=root_dir / "run.json",
        report_path=root_dir / "report.md",
    )


def prepare_run_context(request: RunRequest) -> WorkspaceContext:
    """Resolve starter source, workspace, and metadata."""
    from raidar.starter import record_starter_metadata, resolve_starter_source

    starter_source = resolve_starter_source(
        request.scenario_dir,
        request.scenario.starter.root,
        scenario_name=request.scenario.name,
        scenario_revision=request.scenario.scenario_revision,
    )

    baseline_cache_key, baseline_workspace_dir, baseline_cache = _baseline_workspace_for_request(
        request, starter_source
    )

    workspace_dir = _repeat_workspace_dir(request)
    _copy_baseline_workspace(baseline_workspace_dir, workspace_dir)
    injected_rules = _injected_rules_path(workspace_dir, request.config.harness)
    metadata_path = record_starter_metadata(workspace_dir, starter_source)

    return WorkspaceContext(
        starter_source=starter_source,
        baseline_workspace=baseline_workspace_dir,
        baseline_cache_key=baseline_cache_key,
        baseline_cache_status=baseline_cache.status,
        baseline_cache_hit=baseline_cache.hit,
        baseline_metadata_path=baseline_cache.metadata_path,
        baseline_fingerprint=baseline_cache.baseline_fingerprint,
        workspace=workspace_dir,
        injected_rules=injected_rules,
        metadata_path=metadata_path,
    )
