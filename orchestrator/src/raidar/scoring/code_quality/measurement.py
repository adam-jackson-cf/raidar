"""Run the pinned measurement tools for one snapshot (host side of G3).

The measurement program in ``snapshot_tool`` and Ruff run under the
interpreter of the Python pack's ``scb-python-quality`` tool, which the lazy
provisioner installs into the host cache keyed by ``tools.lock.yaml``
(first use is timed separately and never charged to an arm). Every process
is argv-only, isolated (``python -I``) and bounded by a deadline; a timeout
or a missing interpreter is ``measurement_environment_mismatch``, as in the
fork. Nothing is written into the measured snapshot (Ruff's cache is off).
"""

from __future__ import annotations

import importlib
import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from raidar.contract import file_io
from raidar.subject.environment.python.pack_definition import ProvisioningTiming, load_tool_lock


class _ProvisionedTool(Protocol):
    @property
    def python(self) -> Path: ...
    @property
    def first_use(self) -> ProvisioningTiming | None: ...


class _Provisioner(Protocol):
    def ensure(self, name: str) -> _ProvisionedTool: ...


QUALITY_TOOL = "scb-python-quality"
# Loaded by name: provisioning and process mechanics stay outside the scorer's static import
# closure and so outside its implementation digest.
_PROVISIONER = "raidar.subject.environment.python.provisioner"
DEFAULT_DEADLINE_S = 300.0
_TOOL_ROOT = Path(__file__).resolve().parent / "snapshot_tool"
# The program reads files only through the reader its entry is given: the bootstrap loads
# ``raidar.contract.file_io`` (standard library only) from its file and passes it in.
_FILE_IO = Path(file_io.__file__).resolve()
_BOOTSTRAP = (
    "import importlib.util as u, sys; "
    "s = u.spec_from_file_location('snapshot_tool', sys.argv.pop(1) + '/__init__.py'); "
    "m = u.module_from_spec(s); sys.modules['snapshot_tool'] = m; s.loader.exec_module(m); "
    "r = u.spec_from_file_location('file_io', sys.argv.pop(1)); "
    "f = u.module_from_spec(r); r.loader.exec_module(f); "
    "from snapshot_tool.cli import main; raise SystemExit(main(sys.argv[1:], f))"
)


# Measurement must never write into a stored snapshot: Ruff runs with the snapshot as its
# working directory and would otherwise create ``.ruff_cache`` there.
_READ_ONLY_ENV = {"RUFF_NO_CACHE": "true", "PYTHONDONTWRITEBYTECODE": "1"}
# Parsing needs process/platform settings, not the assessor's credentials or Python
# import overrides. Python -I also excludes candidate modules from every tool invocation.
_PROCESS_ENV_NAMES = frozenset(
    {"PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE", "TZ"}
)


class MeasurementError(ValueError):
    """Evidence cannot be measured; the message starts with a fork eligibility code."""


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


ProcessRunner = Callable[[Sequence[str], Path | None, str | None, float], ProcessResult]


def run_process(
    argv: Sequence[str], cwd: Path | None, stdin: str | None, deadline_s: float
) -> ProcessResult:
    """Run one argv-only measurement process and collect its UTF-8 streams.

    The process call and the environment it inherits belong to the provisioner, loaded by name
    like ``provisioned_environment`` does, so this analyzer module runs no process itself.
    """
    tools = importlib.import_module(_PROVISIONER)
    try:
        completed = tools.run_command(
            argv,
            tools.inherited_environment(_PROCESS_ENV_NAMES, _READ_ONLY_ENV),
            cwd=cwd,
            input=stdin,
            timeout=deadline_s,
            encoding="utf-8",
        )
    except subprocess.TimeoutExpired as error:
        raise MeasurementError("measurement_environment_mismatch: measurement timed out") from error
    except OSError as error:
        raise MeasurementError("measurement_environment_mismatch: evaluator unavailable") from error
    return ProcessResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True)
class ToolEnvironment:
    """The pinned evaluator interpreter and how to run processes under it."""

    python: Path
    runner: ProcessRunner = run_process
    deadline_s: float = DEFAULT_DEADLINE_S
    provisioning: ProvisioningTiming | None = None

    def run(
        self, argv: Sequence[str], *, cwd: Path | None = None, stdin: str | None = None
    ) -> ProcessResult:
        return self.runner([str(self.python), *argv], cwd, stdin, self.deadline_s)

    def tool(self, *args: str, stdin: str | None = None) -> ProcessResult:
        return self.run(
            ["-I", "-B", "-c", _BOOTSTRAP, str(_TOOL_ROOT), str(_FILE_IO), *args], stdin=stdin
        )

    def evaluate(self, sources: Mapping[str, bytes]) -> ProcessResult:
        """The evaluator payload (source lines, clone groups, interpreter identity)."""
        files = [{"path": path, "bytes": data.hex()} for path, data in sources.items()]
        return self.tool("evaluate", stdin=json.dumps({"files": files}))

    def measure(self, snapshot: Path, entry_file: str | None) -> dict[str, Any]:
        """File rows, symbol rows and the call graph traversal for one snapshot."""
        args = ["measure", str(snapshot), *([entry_file] if entry_file else [])]
        completed = self.tool(*args)
        if completed.returncode != 0:
            raise MeasurementError(
                "measurement_environment_mismatch: snapshot measurement failed: "
                + (completed.stderr or completed.stdout)[-2000:]
            )
        try:
            measured = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise MeasurementError(
                "score_evidence_invalid: snapshot measurement emitted malformed rows"
            ) from error
        if not isinstance(measured, dict):
            raise MeasurementError("score_evidence_invalid: snapshot measurement is not an object")
        return measured


def provisioned_environment(provisioner: _Provisioner | None = None) -> ToolEnvironment:
    """The ``scb-python-quality`` interpreter, provisioned on first use.

    The provisioner is loaded by name, deliberately outside the scorer's static
    import closure: provisioning mechanics are not part of the scorer's
    implementation digest.
    """
    if provisioner is None:
        module = importlib.import_module(_PROVISIONER)
        provisioner = module.ToolProvisioner(load_tool_lock())
    tool = provisioner.ensure(QUALITY_TOOL)
    return ToolEnvironment(python=tool.python, provisioning=tool.first_use)
