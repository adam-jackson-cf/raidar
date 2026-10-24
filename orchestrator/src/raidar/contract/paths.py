"""Repository layout: tracked ``lab/`` definitions and the generated ``var/`` root.

``lab/`` holds the run-time data the code loads, each tree at one explicit path below: the
experiments, the problem pins, the ledger, the component payloads (``lab/treatment/``), the
runtime-pack recipes (``lab/environment/``) and the judge policies (``lab/judge/``).

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


def treatment_root(repo_root: Path = REPO_ROOT) -> Path:
    """Run-time data of the treatment components: ``lab/treatment/<component>/``."""
    return lab_root(repo_root) / "treatment"


def environment_root(repo_root: Path = REPO_ROOT) -> Path:
    """Run-time data of the runtime packs: ``lab/environment/<runtime>/``."""
    return lab_root(repo_root) / "environment"


def judge_root(repo_root: Path = REPO_ROOT) -> Path:
    """Judge policies and question sets: ``lab/judge/``."""
    return lab_root(repo_root) / "judge"
