"""Repository layout: tracked ``lab/`` definitions and the generated ``var/`` root.

``var/`` holds everything Raidar generates (jobs, Assessment stores, analysis output,
imported problem content and the scratch directory); it is git-ignored. ``RAIDAR_VAR``
relocates it; unset, it is ``<repo>/var``.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
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
