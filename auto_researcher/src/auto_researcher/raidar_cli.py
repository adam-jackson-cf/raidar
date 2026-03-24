"""Public CLI wrapper around the Raidar evaluator boundary."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .storage import WorkspaceLayout


class RaidarClient(Protocol):
    """Evaluator boundary required by the autoresearch engine."""

    def scenario_init(
        self,
        *,
        path: Path,
        name: str,
        scenario_revision: str,
        starter_root: str,
        prompt_entry: str,
        difficulty: str,
        category: str,
        timeout_sec: int,
    ) -> dict[str, Any]: ...

    def scenario_clone_revision(
        self,
        *,
        path: Path,
        from_revision: str,
        to_revision: str | None = None,
    ) -> dict[str, Any]: ...

    def scenario_validate(self, *, scenario_yaml: Path) -> None: ...

    def experiment_run(
        self,
        *,
        scenario_yaml: Path,
        harness: str,
        model: str,
        timeout_sec: int,
        repeats: int,
        repeat_parallel: int,
        experiment_kind: str,
        experiments_root: Path | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class RaidarCli:
    """Call Raidar only through its machine-readable CLI surface."""

    layout: WorkspaceLayout
    command: tuple[str, ...] = ("uv", "run", "--project", "orchestrator", "raidar")

    def _run(self, *args: str) -> str:
        env = dict(os.environ)
        env.pop("VIRTUAL_ENV", None)
        result = subprocess.run(
            [*self.command, *args],
            cwd=self.layout.repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(message)
        return result.stdout

    def _run_json(self, *args: str) -> dict[str, Any]:
        output = self._run(*args)
        payload = json.loads(output)
        if not isinstance(payload, dict):
            raise RuntimeError("Expected JSON object from Raidar CLI.")
        return payload

    def scenario_init(
        self,
        *,
        path: Path,
        name: str,
        scenario_revision: str,
        starter_root: str,
        prompt_entry: str,
        difficulty: str,
        category: str,
        timeout_sec: int,
    ) -> dict[str, Any]:
        return self._run_json(
            "scenario",
            "init",
            "--path",
            str(path),
            "--name",
            name,
            "--scenario-revision",
            scenario_revision,
            "--starter-root",
            starter_root,
            "--prompt-entry",
            prompt_entry,
            "--difficulty",
            difficulty,
            "--category",
            category,
            "--timeout",
            str(timeout_sec),
            "--json",
        )

    def scenario_clone_revision(
        self,
        *,
        path: Path,
        from_revision: str,
        to_revision: str | None = None,
    ) -> dict[str, Any]:
        args = [
            "scenario",
            "clone-revision",
            "--path",
            str(path),
            "--from-revision",
            from_revision,
            "--json",
        ]
        if to_revision is not None:
            args.extend(["--to-revision", to_revision])
        return self._run_json(*args)

    def scenario_validate(self, *, scenario_yaml: Path) -> None:
        self._run("scenario", "validate", "--scenario", str(scenario_yaml))

    def experiment_run(
        self,
        *,
        scenario_yaml: Path,
        harness: str,
        model: str,
        timeout_sec: int,
        repeats: int,
        repeat_parallel: int,
        experiment_kind: str,
        experiments_root: Path | None = None,
    ) -> dict[str, Any]:
        args = [
            "experiment",
            "run",
            "--scenario",
            str(scenario_yaml),
            "--harness",
            harness,
            "--model",
            model,
            "--timeout",
            str(timeout_sec),
            "--repeats",
            str(repeats),
            "--repeat-parallel",
            str(repeat_parallel),
            "--rerun-unscored",
            "1",
            "--experiment-kind",
            experiment_kind,
            "--json",
        ]
        if experiments_root is not None:
            args.extend(["--experiments-root", str(experiments_root)])
        return self._run_json(*args)
