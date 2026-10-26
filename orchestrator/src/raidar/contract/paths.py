"""Repository layout: tracked ``lab/`` definitions and the generated ``var/`` root.

``lab/`` holds the run-time data the code loads, each tree at one explicit path below: the
experiments, the problem pins, the ledger, the agent definitions (``lab/agents/``), the workflow
definitions (``lab/workflows/``) with their release register (``lab/releases.yaml``), the
runtime-pack recipes (``lab/environments/``) and the judge policies (``lab/judge/``).

``var/`` holds everything Raidar generates (jobs, Assessment stores, analysis output,
imported problem content and the scratch directory); it is git-ignored. ``RAIDAR_VAR``
relocates it; unset, it is ``<repo>/var``.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
ORCHESTRATOR_ROOT = REPO_ROOT / "orchestrator"
VAR_ENV = "RAIDAR_VAR"
LAB_DIR = "lab"
VAR_DIR = "var"


def var_root(repo_root: Path = REPO_ROOT) -> Path:
    """The generated-data root: ``$RAIDAR_VAR`` when set, else ``<repo_root>/var``."""
    override = os.environ.get(VAR_ENV)
    return Path(override).resolve() if override else repo_root / VAR_DIR


def jobs_root() -> Path:
    return var_root() / "jobs"


def tmp_root() -> Path:
    return var_root() / "tmp"


def lab_root(repo_root: Path = REPO_ROOT) -> Path:
    return repo_root / LAB_DIR


def agents_root(repo_root: Path = REPO_ROOT) -> Path:
    """Agent definitions: ``lab/agents/<name>.yaml``."""
    return lab_root(repo_root) / "agents"


def workflows_root(repo_root: Path = REPO_ROOT) -> Path:
    """Workflow definitions: ``lab/workflows/<name>/workflow.yaml``."""
    return lab_root(repo_root) / "workflows"


def releases_file(repo_root: Path = REPO_ROOT) -> Path:
    """The release register of agents and workflows (``name@N`` -> content hash)."""
    return lab_root(repo_root) / "releases.yaml"


def problems_root(repo_root: Path = REPO_ROOT) -> Path:
    """Committed problem pins and manifests: ``lab/problems/``."""
    return lab_root(repo_root) / "problems"


def environments_root(repo_root: Path = REPO_ROOT) -> Path:
    """Run-time data of the runtime packs: ``lab/environments/<runtime>/``."""
    return lab_root(repo_root) / "environments"


def judge_root(repo_root: Path = REPO_ROOT) -> Path:
    """Judge policies and question sets: ``lab/judge/``."""
    return lab_root(repo_root) / "judge"
