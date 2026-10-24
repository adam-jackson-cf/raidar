"""Lazy host-side provisioning of Python pack measurement tools.

Tools are installed on first use into a host cache keyed by the tool lock hash:

    <cache_root>/<runtime>/<lock_hash>/<tool>/

Nothing is installed when the provisioner is created, and only the requested
tool is installed on ``ensure``. A tool directory is published by writing its
marker file last, under a per-tool file lock, so a partial install is never
reused. First-use provisioning time is returned as its own timing record that
is flagged as excluded from arm efficiency metrics (design memo §4.1).

The lock pins Python only to a minor version and the tools' top-level
packages only, so the marker records what an install resolved: the interpreter
patch version and path, and every installed distribution. Each reuse re-probes
the environment, imports the declared modules, and reinstalls when anything
drifted (for example a uv-managed CPython patch upgrade under the venv).

Only ``host-parse`` tools are provisioned here. ``eval-sandbox`` tools build or
execute candidate code and run in the eval container, so asking for one fails.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from raidar.subject.environment.python.pack_definition import (
    REASON_DRIFT,
    REASON_FIRST_USE,
    ProvisioningTiming,
    ToolExecution,
    ToolInstaller,
    ToolLock,
    ToolSpec,
    pinned_distribution,
)

MARKER_NAME = ".raidar-provisioned.json"
TOOL_CACHE_ENV = "RAIDAR_TOOL_CACHE"

# Imports the declared modules, then reports what the environment resolved to.
_ENV_PROBE = """
import importlib, importlib.metadata, json, os, platform, sys
for module in sys.argv[1:]:
    importlib.import_module(module)
print(json.dumps({
    "python_version": platform.python_version(),
    "python_executable": os.path.realpath(sys.executable),
    "distributions": sorted(
        f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()
    ),
}))
"""

CommandRunner = Callable[[Sequence[str], Mapping[str, str]], subprocess.CompletedProcess[str]]


def run_command(argv: Sequence[str], env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv built from the tool lock
        list(argv), capture_output=True, text=True, env=dict(env), check=False
    )


def default_cache_root() -> Path:
    explicit = os.environ.get(TOOL_CACHE_ENV)
    if explicit:
        return Path(explicit)
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "raidar" / "tools"


class ToolProvisioningError(RuntimeError):
    """A tool could not be provisioned; its partial directory was removed."""


class EvalSandboxToolError(ValueError):
    """An eval-sandbox tool was requested for host provisioning."""


@dataclass(frozen=True)
class ProvisionedTool:
    name: str
    lock_hash: str
    root: Path
    bin_dir: Path
    python: Path
    first_use: ProvisioningTiming | None

    def executable(self, name: str) -> Path:
        path = self.bin_dir / name
        if not path.exists():
            raise FileNotFoundError(f"{self.name} provides no executable {name!r} at {path}")
        return path


@dataclass(frozen=True)
class _Layout:
    root: Path
    bin_dir: Path
    python: Path


def _layout(root: Path, spec: ToolSpec) -> _Layout:
    if spec.installer is ToolInstaller.UV_ENV:
        bin_dir = root / "venv" / "bin"
        return _Layout(root=root, bin_dir=bin_dir, python=bin_dir / "python")
    # uv tool install names the tool's own environment after its first package.
    tool_env = root / "tools" / pinned_distribution(spec.packages[0])
    return _Layout(root=root, bin_dir=root / "bin", python=tool_env / "bin" / "python")


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class ToolProvisioner:
    """Provision host-parse tools from one pack tool lock, lazily and once."""

    def __init__(
        self,
        lock: ToolLock,
        *,
        cache_root: Path | None = None,
        runner: CommandRunner | None = None,
        uv_executable: str | None = None,
    ) -> None:
        self._lock = lock
        self._lock_hash = lock.lock_hash()
        self._cache_root = cache_root or default_cache_root()
        self._runner = runner or run_command
        self._uv_executable = uv_executable

    @property
    def lock_dir(self) -> Path:
        return self._cache_root / self._lock.runtime / self._lock_hash

    def is_provisioned(self, name: str) -> bool:
        self._host_spec(name)
        return (self.lock_dir / name / MARKER_NAME).is_file()

    def ensure(self, name: str) -> ProvisionedTool:
        """Return the tool, installing it first if it is missing or has drifted."""
        spec = self._host_spec(name)
        layout = _layout(self.lock_dir / name, spec)
        marker = layout.root / MARKER_NAME
        with _file_lock(self.lock_dir / f"{name}.lock"):
            timing = None
            if not marker.is_file():
                timing = self._install(name, spec, layout, REASON_FIRST_USE)
            elif not self._still_matches(spec, layout, marker):
                timing = self._install(name, spec, layout, REASON_DRIFT)
        return ProvisionedTool(
            name=name,
            lock_hash=self._lock_hash,
            root=layout.root,
            bin_dir=layout.bin_dir,
            python=layout.python,
            first_use=timing,
        )

    def _host_spec(self, name: str) -> ToolSpec:
        spec = self._lock.tool(name)
        if spec.execution is not ToolExecution.HOST_PARSE:
            raise EvalSandboxToolError(
                f"{name} is an {spec.execution.value} tool: it runs in the eval container "
                "and is never provisioned on the host"
            )
        return spec

    def _uv(self) -> str:
        uv = self._uv_executable or shutil.which("uv")
        if not uv:
            raise ToolProvisioningError("uv is required to provision Python pack tools")
        return uv

    def _install(
        self, name: str, spec: ToolSpec, layout: _Layout, reason: str
    ) -> ProvisioningTiming:
        shutil.rmtree(layout.root, ignore_errors=True)
        layout.root.mkdir(parents=True)
        started = time.perf_counter()
        try:
            for argv, env in self._install_commands(spec, layout):
                self._run(name, argv, env)
            resolved = self._verify(name, spec, layout)
        except BaseException:
            shutil.rmtree(layout.root, ignore_errors=True)
            raise
        seconds = time.perf_counter() - started
        timing = ProvisioningTiming(
            tool=name, lock_hash=self._lock_hash, seconds=seconds, reason=reason
        )
        marker = {
            **timing.as_record(),
            "packages": list(spec.packages),
            "installer": spec.installer.value,
            "exclude_newer": self._lock.exclude_newer,
            "python": self._lock.python,
            "resolved": resolved,
            "provisioned_at": datetime.now(UTC).isoformat(),
        }
        staged = layout.root / f"{MARKER_NAME}.tmp"
        staged.write_text(json.dumps(marker, indent=2), encoding="utf-8")
        staged.replace(layout.root / MARKER_NAME)
        return timing

    def _install_commands(
        self, spec: ToolSpec, layout: _Layout
    ) -> list[tuple[list[str], dict[str, str]]]:
        uv = self._uv()
        env = dict(os.environ)
        pins = ["--no-config", "--exclude-newer", self._lock.exclude_newer]
        if spec.installer is ToolInstaller.UV_ENV:
            venv = layout.root / "venv"
            python = str(venv / "bin" / "python")
            return [
                ([uv, "venv", "--no-config", "--python", self._lock.python, str(venv)], env),
                ([uv, "pip", "install", *pins, "--python", python, *spec.packages], env),
            ]
        tool_env = {
            **env,
            "UV_TOOL_DIR": str(layout.root / "tools"),
            "UV_TOOL_BIN_DIR": str(layout.bin_dir),
        }
        first, *extra = spec.packages
        with_args = [arg for package in extra for arg in ("--with", package)]
        argv = [uv, "tool", "install", *pins, "--python", self._lock.python, first, *with_args]
        return [(argv, tool_env)]

    def _run(self, name: str, argv: Sequence[str], env: Mapping[str, str]) -> str:
        completed = self._runner(argv, env)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[-2000:]
            raise ToolProvisioningError(f"provisioning {name} failed: {argv[1:3]}: {detail}")
        return completed.stdout

    def _verify(self, name: str, spec: ToolSpec, layout: _Layout) -> dict[str, object]:
        """Check executables, import the declared modules, and return the resolved env."""
        missing = [exe for exe in spec.executables if not (layout.bin_dir / exe).exists()]
        if missing:
            raise ToolProvisioningError(f"{name} installed without executables {missing}")
        if not layout.python.exists():
            raise ToolProvisioningError(f"{name} installed without its interpreter {layout.python}")
        argv = [str(layout.python), "-c", _ENV_PROBE, *spec.modules]
        output = self._run(name, argv, dict(os.environ))
        try:
            resolved = json.loads(output)
        except ValueError as error:
            raise ToolProvisioningError(f"{name} environment probe gave no JSON") from error
        if not isinstance(resolved, dict):
            raise ToolProvisioningError(f"{name} environment probe gave {resolved!r}")
        return resolved

    def _still_matches(self, spec: ToolSpec, layout: _Layout, marker: Path) -> bool:
        """Re-probe a reused install; any drift from its marker means reinstall."""
        try:
            recorded = json.loads(marker.read_text(encoding="utf-8")).get("resolved")
            return self._verify(layout.root.name, spec, layout) == recorded
        except (ToolProvisioningError, ValueError, AttributeError):
            return False
