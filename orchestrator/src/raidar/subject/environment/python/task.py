"""Bind a Harbor task environment to the prebuilt Python pack image.

Importers (plan B1) call ``bind_task_environment`` on a task's
``environment/`` directory and merge ``task_environment_fields`` into the
task's ``[environment]`` table. Harbor then starts the prebuilt image instead
of building one, and applies the compose override, which carries the accepted
N1 ``init: true`` decision.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from raidar.subject.environment.python.image import image_tag
from raidar.subject.environment.python.models import PackSpec

COMPOSE_FILE_NAME = "docker-compose.yaml"
MAIN_SERVICE = "main"


def task_environment_fields(spec: PackSpec) -> dict[str, str]:
    """``[environment]`` fields that select the prebuilt pack image in Harbor."""
    return {"docker_image": image_tag(spec)}


def compose_override(existing: dict[str, Any] | None, spec: PackSpec) -> dict[str, Any]:
    """Merge the pack's main-service settings into a task compose document.

    Pack keys win for the main service, because they are accepted overhead
    decisions; every other service and key in the task's document is kept.
    """
    document = dict(existing or {})
    services = dict(document.get("services") or {})
    main = dict(services.get(MAIN_SERVICE) or {})
    main.update(spec.task_environment.compose_main_service)
    services[MAIN_SERVICE] = main
    document["services"] = services
    return document


def bind_task_environment(environment_dir: Path, spec: PackSpec) -> dict[str, str]:
    """Write the compose override into ``environment_dir`` and return the env fields.

    A directory that carries a ``Dockerfile`` is rejected: with both present,
    Harbor picks the prebuilt image or a rebuild depending on ``force_build``,
    so the agent image would no longer be fixed by the pack lock.
    """
    if (environment_dir / "Dockerfile").exists():
        raise ValueError(
            f"{environment_dir} has a Dockerfile; a pack-bound environment uses the "
            "prebuilt pack image only"
        )
    environment_dir.mkdir(parents=True, exist_ok=True)
    compose_path = environment_dir / COMPOSE_FILE_NAME
    existing = yaml.safe_load(compose_path.read_text()) if compose_path.exists() else None
    if existing is not None and not isinstance(existing, dict):
        raise ValueError(f"{compose_path} must contain a mapping")
    document = compose_override(existing, spec)
    compose_path.write_text(yaml.safe_dump(document, sort_keys=False))
    return task_environment_fields(spec)
