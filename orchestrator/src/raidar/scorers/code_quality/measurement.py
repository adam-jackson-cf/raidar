"""Run the pinned measurement tools for one snapshot (host side of G3).

The measurement program in ``snapshot_tool`` and Ruff run under the
interpreter of the Python pack's ``scb-python-quality`` tool, which the WS-I
lazy provisioner installs into the host cache keyed by ``tools.lock.yaml``
(first use is timed separately and never charged to an arm). Every process
is argv-only, isolated (``python -I``) and bounded by a deadline; a timeout
or a missing interpreter is ``measurement_environment_mismatch``, as in the
fork. Nothing is written into the measured snapshot (Ruff's cache is off).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.packs.python.models import load_tool_lock
from raidar.packs.python.provisioner import ProvisioningTiming, ToolProvisioner

QUALITY_TOOL = "scb-python-quality"
DEFAULT_DEADLINE_S = 300.0
_TOOL_ROOT = Path(__file__).resolve().parent / "tool_env"
_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    "from snapshot_tool.cli import main; raise SystemExit(main(sys.argv[1:]))"
)


# Measurement must never write into a stored snapshot: Ruff runs with the snapshot as its
# working directory and would otherwise create ``.ruff_cache`` there.
_READ_ONLY_ENV = {"RUFF_NO_CACHE": "true", "PYTHONDONTWRITEBYTECODE": "1"}


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
    """Run one argv-only measurement process and collect its UTF-8 streams."""
    try:
        completed = subprocess.run(  # noqa: S603 - argv is built from pinned tool paths
            list(argv),
            cwd=cwd,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=deadline_s,
            check=False,
            env={**os.environ, **_READ_ONLY_ENV},
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
        return self.run(["-I", "-B", "-c", _BOOTSTRAP, str(_TOOL_ROOT), *args], stdin=stdin)

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


def provisioned_environment(provisioner: ToolProvisioner | None = None) -> ToolEnvironment:
    """The ``scb-python-quality`` interpreter, provisioned on first use."""
    tool = (provisioner or ToolProvisioner(load_tool_lock())).ensure(QUALITY_TOOL)
    return ToolEnvironment(python=tool.python, provisioning=tool.first_use)
