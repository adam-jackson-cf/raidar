"""Workspace seeding (design decision D2): ``/app`` gets its own project before checkpoint 1.

The grader runs the entry file as ``uv run --project /app``, resolving in the workspace's own
project and nowhere else, and gives the program the problem's declared dependencies only when
the submission declares them. An agent working in an empty ``/app`` sees whatever its session
has on the path, so a package it never declared imports while it works and fails only when
graded: that scores the absence of a ``uv add``, not the code (fault FCR-004). The problem
already says what it needs (its SCB ``test_dependencies``, pinned in the verifier bundle), so
the lab owes the solution the same environment it owes the tests (factory-x-loops
``evaluation/benchmark/scb.py`` ``write_workspace_project`` / ``sync_workspace_project``).

Before checkpoint 1 the lab therefore writes ``/app/pyproject.toml`` naming those dependencies
and runs ``uv sync`` as the agent user, as a per-trial provision at environment start
(``raidar.engine.trial.provision``): outside every step, agent and verifier duration. The
provision records what was seeded (dependencies, the file's sha256, the interpreter) and the
digest of the seeded ``/app``, which is the parent of checkpoint 1's snapshot. Its check, run
before every step's agent, reports the file's current sha256; at checkpoint 1 it must equal
the seeded one.
"""

from __future__ import annotations

import hashlib
import re
import shlex
from collections.abc import Sequence
from pathlib import Path

from raidar.contract.snapshot import APP_SOURCE
from raidar.engine.trial.provision import PayloadFile, TrialProvision, read_provision_record

SEED_SLUG = "workspace-project"
PROJECT_FILE = "pyproject.toml"
SHA_KEY = "pyproject_sha256"
ABSENT = "absent"


def workspace_project(problem: str, dependencies: Sequence[str], python: str) -> str:
    """The seeded ``pyproject.toml``: the problem's declared dependencies, nothing else.

    The project name is the problem reference reduced to a valid package name (letters,
    digits and ``-``), so ``scb/forge@38d627ecf668`` names ``scb-forge-38d627ecf668-workspace``.
    """
    name = re.sub(r"[^A-Za-z0-9]+", "-", problem).strip("-").lower()
    listed = ", ".join(f'"{dependency}"' for dependency in dependencies)
    return (
        f'[project]\nname = "{name}-workspace"\nversion = "0"\n'
        f'requires-python = ">={python}"\ndependencies = [{listed}]\n'
    )


def _install(python: str) -> str:
    project = shlex.quote(f"{APP_SOURCE}/{PROJECT_FILE}")
    sync = f"uv sync --project {shlex.quote(APP_SOURCE)} --python {shlex.quote(python)}"
    return "\n".join(
        [
            "set -eu",
            'user="${RAIDAR_AGENT_USER:?the task names no agent user to own the workspace}"',
            f'install -m 0644 -o "$user" -g "$(id -gn "$user")" '
            f'"$RAIDAR_PAYLOAD_DIR/{PROJECT_FILE}" {project}',
            f'runuser -u "$user" -- env UV_PYTHON_DOWNLOADS=never sh -c '
            f"{shlex.quote(f'cd {APP_SOURCE} && {sync}')} 2>&1",
        ]
    )


def _check() -> str:
    project = shlex.quote(f"{APP_SOURCE}/{PROJECT_FILE}")
    return (
        f"if [ -f {project} ]; then "
        f'echo "{SHA_KEY}=$(sha256sum {project} | cut -d " " -f1)"; '
        f"else echo {SHA_KEY}={ABSENT}; fi"
    )


def workspace_seed(problem: str, dependencies: Sequence[str], python: str) -> TrialProvision:
    """The provision that writes and syncs the workspace project at environment start."""
    content = workspace_project(problem, dependencies, python).encode()
    return TrialProvision(
        slug=SEED_SLUG,
        payload={PROJECT_FILE: PayloadFile(content)},
        install=_install(python),
        check=_check(),
        snapshot=APP_SOURCE,
        facts={
            "dependencies": list(dependencies),
            "python": python,
            SHA_KEY: hashlib.sha256(content).hexdigest(),
        },
    )


def seed_check(agent_dir: Path) -> tuple[bool, str]:
    """At checkpoint 1: the seeded ``pyproject.toml`` was in ``/app`` when the agent started."""
    record = read_provision_record(agent_dir, SEED_SLUG)
    if record is None:
        return False, f"{SEED_SLUG}: no provision record before the agent started"
    seeded = (record.get("facts") or {}).get(SHA_KEY)
    found = ((record.get("check") or {}).get("values") or {}).get(SHA_KEY)
    if not seeded or found != seeded:
        state = found or "unchecked (the install failed)"
        return False, f"{SEED_SLUG}: /app/{PROJECT_FILE} was {state} at the start, seeded {seeded}"
    return True, f"{SEED_SLUG}: seeded {PROJECT_FILE} present"


def seeded_digest(agent_dir: Path) -> str | None:
    """The snapshot digest of the seeded ``/app`` (checkpoint 1's parent), if recorded."""
    record = read_provision_record(agent_dir, SEED_SLUG)
    digest = ((record or {}).get("install") or {}).get("snapshot_digest")
    return digest if isinstance(digest, str) else None
