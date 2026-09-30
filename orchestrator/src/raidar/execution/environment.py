"""Harbor's Docker environment with a run-scoped Compose project name.

Harbor names a trial's Compose project ``<trial_name>__env``. Raidar's trial
names are plan trial ids (``b00-p0-baseline``), which repeat across runs and
experiments, so two runs on one Docker host (two pilot legs, or a replacement
run beside the original) shared a Compose project: the second ``compose up``
recreated the first run's container, which exited 143 and was recorded as an
infra error. The scope suffix makes the project unique per run directory while
the trial directory stays ``trials/<trial id>`` and no plan hash changes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from harbor.environments.docker.docker import DockerEnvironment

RUN_SCOPED_DOCKER_IMPORT_PATH = "raidar.execution.environment:RunScopedDockerEnvironment"
SCOPE_KWARG = "run_scope"


def run_scope(trials_dir: Path) -> str:
    """A short, stable token for one run directory."""
    return hashlib.sha256(str(trials_dir.resolve()).encode()).hexdigest()[:10]


class RunScopedDockerEnvironment(DockerEnvironment):
    def __init__(self, *args: Any, session_id: str, run_scope: str, **kwargs: Any) -> None:
        super().__init__(*args, session_id=f"{session_id}-{run_scope}", **kwargs)
