"""Raidar oracle pytest plugin. Runs inside the grader sandbox, never on the host.

Loaded with ``-p raidar_oracle_plugin`` from a read-only runtime directory on
``PYTHONPATH``. It depends only on the standard library, pytest and (for the
coverage pass) coverage.py, because it executes in the grader image.

Every run records, per collected node:

- the SCB group markers (``error``/``functionality``/``regression``);
- the pytest outcome category of each phase (``pytest_report_teststatus``);
- the per-phase ledger (collection/setup/call/teardown -> passed/failed/skipped);
- process-creation audit events (observation only; the submission is never
  instrumented in the grading run).

With ``RAIDAR_ORACLE_BUDGET_S`` set, a case that has not started when the
grading budget is spent fails in setup and is listed under
``budget_exhausted``. Slow cases therefore end as failed cases (agent-caused)
with a complete ledger, instead of the whole sandbox run timing out as an
infrastructure failure.

With ``RAIDAR_ORACLE_COVERAGE=1`` (the separate, non-grading coverage pass) it
also switches coverage contexts to ``<nodeid>|<phase>`` and injects subprocess
coverage into black-box submission processes. This part is a port of the SCB
fork's ``slop_code/evaluation/coverage_plugin.py`` so the G kernel receives the
same evidence shape.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import pty
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

PLUGIN_ID = "raidar-oracle-plugin@1"
ENV_LEDGER = "RAIDAR_ORACLE_LEDGER"
ENV_ROOT = "RAIDAR_ORACLE_SUBMISSION_ROOT"
ENV_SCRATCH = "RAIDAR_ORACLE_SCRATCH"
ENV_RUNTIME = "RAIDAR_ORACLE_RUNTIME"
ENV_COVERAGE = "RAIDAR_ORACLE_COVERAGE"
ENV_CONTEXT = "RAIDAR_ORACLE_COVERAGE_CONTEXT"
ENV_BUDGET = "RAIDAR_ORACLE_BUDGET_S"
GROUP_MARKERS = ("error", "functionality", "regression")
PHASES = ("collection", "setup", "call", "teardown")
_STARTUP = "import coverage; coverage.process_startup()\n"


def _sha256_file(path: str | None) -> str:
    if not path:
        return ""
    try:
        return hashlib.sha256(Path(path).resolve().read_bytes()).hexdigest()
    except OSError:
        return ""


def _environment_probe() -> dict[str, Any]:
    tools = sorted(
        f"{dist.metadata['Name'].lower()}=={dist.version}"
        for dist in importlib.metadata.distributions()
    )
    submission_python = shutil.which("python3")
    return {
        "grader_python": sys.executable,
        "grader_python_version": sys.version,
        "grader_python_sha256": _sha256_file(sys.executable),
        "submission_python": submission_python or "",
        "submission_python_sha256": _sha256_file(submission_python),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python_implementation": platform.python_implementation(),
        },
        "grader_tools": tools,
    }


def _node_status(categories: list[str]) -> str:
    """Collapse per-phase pytest categories into one case status."""
    seen = {category for category in categories if category}
    if "error" in seen:
        return "error"
    if "failed" in seen:
        return "failed"
    if seen & {"passed", "xpassed"}:
        return "passed"
    if seen & {"skipped", "xfailed"}:
        return "skipped"
    return "error"


class RunState:
    """Everything the plugin observes during one pytest session."""

    def __init__(self) -> None:
        self.markers: dict[str, list[str]] = {}
        self.categories: dict[str, list[str]] = {}
        self.durations: dict[str, float] = {}
        self.phases: dict[str, dict[str, str]] = {}
        self.collect_errors: list[str] = []
        self.process_events: list[dict[str, str]] = []
        self.budget_exhausted: list[str] = []
        self.invalid_coverage_paths = 0
        self.exit_status: int | None = None


class OraclePlugin:
    """Record outcomes, group markers, ledger, process events and coverage."""

    def __init__(
        self,
        *,
        ledger: Path,
        root: Path,
        scratch: Path,
        coverage: bool,
        budget_s: float | None = None,
        runtime: str = "",
    ) -> None:
        self._ledger_path = ledger
        self._runtime = runtime
        self._deadline = None if budget_s is None else time.monotonic() + budget_s
        self._root = root
        self._scratch = scratch
        self._coverage = coverage
        self._config: Any = None
        self._run = RunState()
        self._active: tuple[str, str] | None = None
        self._originals: list[tuple[object, str, Any]] = []

    # ------------------------------------------------------------ lifecycle
    def pytest_configure(self, config: Any) -> None:
        self._config = config
        _strip_runtime_from_pythonpath(self._runtime)
        # --basetemp and HOME live under the scratch dir; pytest will not create parents.
        (self._scratch / "home").mkdir(parents=True, exist_ok=True)
        if self._coverage:
            self._prepare_subprocess_coverage()
        self._install_process_audit()
        self._switch_context("collection")

    def pytest_sessionfinish(self, session: Any, exitstatus: int) -> None:
        self._run.exit_status = int(exitstatus)

    def pytest_unconfigure(self, config: Any) -> None:
        self._restore_process_audit()
        coverage_rows: list[dict[str, object]] = []
        if self._coverage:
            self._combine_subprocess_coverage()
            coverage_rows = self._coverage_contexts()
        payload = {
            "plugin_id": PLUGIN_ID,
            "coverage_enabled": self._coverage,
            "exit_status": self._run.exit_status,
            "collect_errors": sorted(self._run.collect_errors),
            "nodes": [
                {
                    "node_id": node_id,
                    "markers": self._run.markers.get(node_id, []),
                    "status": _node_status(self._run.categories.get(node_id, [])),
                    "ran": node_id in self._run.categories,
                    "duration_s": round(self._run.durations.get(node_id, 0.0), 6),
                }
                for node_id in sorted(self._run.markers)
            ],
            "ledger": [
                {"node_id": node_id, "phase": phase, "outcome": phases[phase]}
                for node_id, phases in sorted(self._run.phases.items())
                for phase in PHASES
                if phase in phases
            ],
            "coverage": coverage_rows,
            "process_events": self._run.process_events,
            "budget_exhausted": sorted(self._run.budget_exhausted),
            "invalid_coverage_paths": self._run.invalid_coverage_paths,
            "environment": _environment_probe(),
        }
        _write_ledger(self._ledger_path, json.dumps(payload, sort_keys=True, separators=(",", ":")))

    # ----------------------------------------------------------- collection
    def pytest_collection_modifyitems(self, items: list[Any]) -> None:
        for item in items:
            names = {marker.name for marker in item.iter_markers()}
            self._run.markers[item.nodeid] = sorted(names & set(GROUP_MARKERS))
            self._run.phases.setdefault(item.nodeid, {})["collection"] = "passed"

    def pytest_collectstart(self, collector: Any) -> None:
        self._activate(collector.nodeid or "<session>", "collection")

    def pytest_collectreport(self, report: Any) -> None:
        if report.failed:
            self._run.collect_errors.append(report.nodeid or "<session>")
            self._run.phases.setdefault(report.nodeid or "<session>", {})["collection"] = "failed"
        self._active = None
        self._switch_context("collection")

    # -------------------------------------------------------------- running
    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_setup(self, item: Any) -> None:
        self._activate(item.nodeid, "setup")
        if self._deadline is not None and time.monotonic() >= self._deadline:
            self._run.budget_exhausted.append(item.nodeid)
            pytest.fail("raidar-oracle: grading budget exhausted before this case started")

    def pytest_runtest_call(self, item: Any) -> None:
        self._activate(item.nodeid, "call")

    def pytest_runtest_teardown(self, item: Any) -> None:
        self._activate(item.nodeid, "teardown")

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.when not in {"setup", "call", "teardown"}:
            return
        category = self._config.hook.pytest_report_teststatus(report=report, config=self._config)[0]
        self._run.categories.setdefault(report.nodeid, []).append(str(category))
        self._run.durations[report.nodeid] = self._run.durations.get(report.nodeid, 0.0) + float(
            getattr(report, "duration", 0.0) or 0.0
        )
        self._run.phases.setdefault(report.nodeid, {})[report.when] = report.outcome

    # ------------------------------------------------------------- coverage
    def _activate(self, node_id: str, phase: str) -> None:
        self._active = (node_id, phase)
        self._switch_context(f"{node_id}|{phase}")

    def _switch_context(self, context: str) -> None:
        current = _current_coverage() if self._coverage else None
        if current is not None:
            current.switch_context(context)

    def _startup_dir(self) -> Path:
        return self._scratch / "coverage-startup"

    def _rcfile(self) -> Path:
        return self._scratch / "subprocess.coveragerc"

    def _data_file(self) -> Path:
        return self._scratch / "coverage-data" / "coverage"

    def _prepare_subprocess_coverage(self) -> None:
        startup = self._startup_dir()
        startup.mkdir(parents=True, exist_ok=True)
        module = importlib.import_module("coverage")
        shutil.copytree(
            Path(str(module.__file__)).resolve().parent, startup / "coverage", dirs_exist_ok=True
        )
        (startup / "sitecustomize.py").write_text(_STARTUP)
        self._data_file().parent.mkdir(parents=True, exist_ok=True)
        self._rcfile().write_text(
            "[run]\n"
            "branch = true\n"
            "parallel = true\n"
            "sigterm = true\n"
            f"source = {self._root}\n"
            f"context = ${ENV_CONTEXT}\n"
        )

    def _combine_subprocess_coverage(self) -> None:
        current = _current_coverage()
        if current is not None:
            current.combine(data_paths=[str(self._data_file().parent)])

    def _coverage_contexts(self) -> list[dict[str, object]]:
        current = _current_coverage()
        if current is None:
            return []
        data = current.get_data()
        root = self._root.resolve()
        rows: list[dict[str, object]] = []
        for measured in data.measured_files():
            relative = self._relative_submission_path(Path(measured), root)
            if relative is None:
                continue
            for line, contexts in data.contexts_by_lineno(measured).items():
                rows.extend(_context_rows(relative, line, contexts))
        return sorted(
            rows,
            key=lambda row: (row["node_id"], row["phase"], row["path"], row["executed_line"]),
        )

    def _relative_submission_path(self, candidate: Path, root: Path) -> str | None:
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        try:
            return candidate.resolve().relative_to(root).as_posix()
        except ValueError:
            # Inside the submission but resolving outside it: a symlink escape.
            self._run.invalid_coverage_paths += 1
            return None

    # -------------------------------------------------------- process audit
    def _subprocess_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if not self._coverage or self._active is None:
            return kwargs
        supplied = kwargs.get("env")
        environment = dict(os.environ if supplied is None else supplied)
        startup = str(self._startup_dir().resolve())
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = f"{startup}:{existing}" if existing else startup
        environment["COVERAGE_FILE"] = str(self._data_file().resolve())
        environment["COVERAGE_PROCESS_START"] = str(self._rcfile().resolve())
        environment[ENV_CONTEXT] = f"{self._active[0]}|{self._active[1]}"
        return {**kwargs, "env": environment}

    def _install_process_audit(self) -> None:
        self._patch(subprocess, "Popen", "subprocess.Popen")
        self._patch(os, "system", "os.system")
        if hasattr(os, "posix_spawn"):
            self._patch(os, "posix_spawn", "os.posix_spawn")
        if hasattr(os, "fork"):
            self._patch(os, "fork", "os.fork")
        self._patch(pty, "spawn", "pty.spawn")

    def _patch(self, owner: object, attribute: str, api: str) -> None:
        original = getattr(owner, attribute)

        def audited(*args: Any, **kwargs: Any) -> Any:
            if self._active is not None:
                node_id, phase = self._active
                self._run.process_events.append({"event": api, "node_id": node_id, "phase": phase})
            if api == "subprocess.Popen":
                kwargs = self._subprocess_kwargs(kwargs)
            return original(*args, **kwargs)

        self._originals.append((owner, attribute, original))
        setattr(owner, attribute, audited)

    def _restore_process_audit(self) -> None:
        for owner, attribute, original in reversed(self._originals):
            setattr(owner, attribute, original)
        self._originals.clear()


def _context_rows(relative: str, line: object, contexts: Any) -> list[dict[str, object]]:
    if not isinstance(line, int):
        return []
    rows: list[dict[str, object]] = []
    for context in contexts:
        node_id, separator, phase = str(context).rpartition("|")
        if separator and phase in {"setup", "call", "teardown"}:
            rows.append(
                {"node_id": node_id, "phase": phase, "path": relative, "executed_line": line}
            )
    return rows


def _write_ledger(path: Path, text: str) -> None:
    """Write the ledger by atomic replace, never in place.

    The submission runs as the same uid and may have planted a (read-only)
    forged ledger at the well-known path. A temp file plus ``os.replace`` in
    the writable out dir supersedes any such file instead of failing on it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    temporary.write_text(text)
    os.replace(temporary, path)


def _take_oracle_environment() -> dict[str, str]:
    """Read and remove every ``RAIDAR_ORACLE_*`` variable from ``os.environ``.

    The plugin keeps the values; submission processes the hidden tests start
    must not learn where the verdict channel lives.
    """
    taken = {key: value for key, value in os.environ.items() if key.startswith("RAIDAR_ORACLE_")}
    for key in taken:
        os.environ.pop(key, None)
    return taken


def _current_coverage() -> Any:
    try:
        module = importlib.import_module("coverage")
    except ModuleNotFoundError:
        return None
    return module.Coverage.current()


def _strip_runtime_from_pythonpath(runtime: str) -> None:
    """Keep the plugin's runtime dir off the submission's import path."""
    entries = [entry for entry in os.environ.get("PYTHONPATH", "").split(":") if entry]
    kept = [entry for entry in entries if entry != runtime]
    if kept:
        os.environ["PYTHONPATH"] = ":".join(kept)
    else:
        os.environ.pop("PYTHONPATH", None)


def pytest_configure(config: Any) -> None:
    """Register the plugin when loaded with ``-p raidar_oracle_plugin``."""
    environment = _take_oracle_environment()
    plugin = OraclePlugin(
        ledger=Path(environment[ENV_LEDGER]),
        root=Path(environment[ENV_ROOT]),
        scratch=Path(environment[ENV_SCRATCH]),
        coverage=environment.get(ENV_COVERAGE) == "1",
        budget_s=float(environment[ENV_BUDGET]) if environment.get(ENV_BUDGET) else None,
        runtime=environment.get(ENV_RUNTIME, ""),
    )
    # pytest_configure is historic: pluggy replays it for the new plugin.
    config.pluginmanager.register(plugin, "raidar-oracle")
