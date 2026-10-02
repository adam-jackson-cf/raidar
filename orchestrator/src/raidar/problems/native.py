"""SCB native-runner parity arm (``slop-code eval-snapshot`` from the SCB fork).

The fork's runner needs a locked evaluator project in each problem directory,
which upstream problems do not ship. As in the Phase 0 harness, a copy of the
problem is staged into a private catalog with an evaluator ``pyproject.toml``
pinned to the fork's documented tool versions plus the problem's
``test_dependencies``; the problem sources are not modified.

This is a one-off porting check (oracle-validation.md §6.5), not a per-run step.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from raidar.problems.validation import Grade
from raidar.verifier.models import CaseGroup

# The fork's evaluator pins (docs/evaluation/configuration.md in the SCB fork).
NATIVE_EVALUATOR_DEPS = (
    "coverage==7.15.4",
    "pytest==8.4.1",
    "pytest-json-ctrf==0.3.5",
    "pytest-json-report==1.5.0",
    "pytest-timeout==2.4.0",
    "ruff==0.8.6",
)
NATIVE_ENV_CONFIG = "configs/environments/docker-python3.12-uv.yaml"
NATIVE_TIMEOUT_S = 3600
_STATUSES = ("passed", "failed", "skipped")


class NativeRunnerError(RuntimeError):
    """The native runner or its evaluator project could not be prepared."""


def stage_catalog(problem_dir: Path, catalog: Path) -> Path:
    """Copy one problem into ``catalog`` with a locked evaluator project."""
    dest = catalog / problem_dir.name
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(problem_dir, dest)
    config = yaml.safe_load((dest / "config.yaml").read_text()) or {}
    deps = [*NATIVE_EVALUATOR_DEPS, *(config.get("test_dependencies") or [])]
    dep_lines = "".join(f'    "{dep}",\n' for dep in deps)
    (dest / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "{problem_dir.name.replace("_", "-")}-evaluator"\n'
        'version = "0.0.0"\n'
        'requires-python = ">=3.12"\n'
        f"dependencies = [\n{dep_lines}]\n"
    )
    proc = subprocess.run(
        ["uv", "lock", "-q"], cwd=dest, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise NativeRunnerError(f"uv lock failed for the native evaluator: {proc.stderr[-2000:]}")
    return dest


def grade_from_evaluation(data: dict[str, Any], step: str) -> Grade:
    """Reduce the fork's ``evaluation.json`` to a comparable ``Grade``."""
    groups = {
        group.value: (
            int(data["pass_counts"].get(group.value.capitalize(), 0)),
            int(data["total_counts"].get(group.value.capitalize(), 0)),
        )
        for group in CaseGroup
    }
    outcomes: dict[str, str] = {}
    for key, bucket in data["tests"].items():
        origin = key.rsplit("-", 1)[0]
        for status in _STATUSES:
            for test_id in bucket.get(status, []):
                outcomes[f"{origin}::{test_id}"] = status
    infra = "native_infrastructure_failure" if data.get("infrastructure_failure") else None
    return Grade("native", step, groups, outcomes, infra)


def _docker_host(context: str) -> str:
    proc = subprocess.run(
        ["docker", "context", "inspect", context, "--format", "{{.Endpoints.docker.Host}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    host = proc.stdout.strip()
    if proc.returncode != 0 or not host:
        raise NativeRunnerError(f"docker context {context!r} is not available")
    return host


@dataclass(frozen=True)
class NativeRunner:
    """An SCB fork checkout with its ``.venv`` (``slop-code`` CLI) installed."""

    root: Path
    docker_context: str = "orbstack"

    def revision(self) -> dict[str, object]:
        """The fork's git commit and whether it has local changes (recorded, N3).

        ``commit`` is None unless ``root`` is itself a git work tree root, so an
        enclosing repository is never mistaken for the fork.
        """

        def git(*args: str) -> str | None:
            proc = subprocess.run(
                ["git", "-C", str(self.root), *args], capture_output=True, text=True, check=False
            )
            return proc.stdout.strip() if proc.returncode == 0 else None

        top = git("rev-parse", "--show-toplevel")
        if top is None or Path(top).resolve() != self.root.resolve():
            return {"commit": None}
        return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}

    def grade(
        self, catalog: Path, problem: str, snapshot: Path, checkpoint_index: int, out: Path
    ) -> Grade:
        step = f"checkpoint_{checkpoint_index}"
        shutil.rmtree(out, ignore_errors=True)
        staged = out / "snapshot"
        shutil.copytree(snapshot, staged)
        env = {k: v for k, v in os.environ.items() if k != "DOCKER_CONTEXT"}
        env["DOCKER_HOST"] = _docker_host(self.docker_context)  # docker-py reads DOCKER_HOST
        env["SCBENCH_PROBLEMS_PATH"] = str(catalog)
        argv = [
            str(self.root / ".venv" / "bin" / "slop-code"),
            "--quiet",
            "eval-snapshot",
            str(staged),
            "-o",
            str(out / "eval"),
            "-p",
            problem,
            "-c",
            str(checkpoint_index),
            "-e",
            str(self.root / NATIVE_ENV_CONFIG),
            "--json",
        ]
        proc = subprocess.run(
            argv,
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=NATIVE_TIMEOUT_S,
        )
        (out / "native.log").write_text(proc.stdout + "\n--- stderr\n" + proc.stderr)
        evaluation = out / "eval" / "evaluation.json"
        if not evaluation.is_file():
            return Grade("native", step, {}, {}, "native_no_evaluation")
        return grade_from_evaluation(json.loads(evaluation.read_text()), step)
