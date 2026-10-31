"""``outputs/1``: copy and hash a package's declared outputs host-side, and validate their contract.

Outputs are method evidence, never graded solution files. Each declared output is copied with the
bounded, no-follow state copy (a symlink, special file or oversized file is refused, never opened)
and its bytes are re-read on the host. An output declared ``criteria/1`` is validated as data by
``contract.criteria``; no package code runs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from raidar_contract.criteria import validate_criteria
from raidar_contract.package.manifest import Output

from raidar.subject.primitive.calls import TransportError, TreeManifest, snapshot_state
from raidar.subject.primitive.site import PackageSite, TrialInvalid

OUTPUTS_LOG = "outputs"
FILE_KIND = "file"


@dataclass(frozen=True)
class CopiedOutput:
    """One declared output after a copy attempt: ``error`` is ``None`` for a valid, present one."""

    name: str
    path: str
    contract: str | None
    sha256: str | None = None
    size: int | None = None
    host_path: str | None = None
    error: str | None = None

    def record(self) -> dict[str, object]:
        """Host-written summary."""
        return {
            "path": self.path,
            "contract": self.contract,
            "sha256": self.sha256,
            "bytes": self.size,
            "host_path": self.host_path,
            "error": self.error,
        }


@dataclass(frozen=True)
class OutputsResult:
    """Every declared output after one collection."""

    outputs: dict[str, CopiedOutput] = field(default_factory=dict)

    @property
    def problems(self) -> dict[str, str]:
        """Declared outputs that are missing or invalid, with why."""
        return {name: out.error for name, out in self.outputs.items() if out.error is not None}

    def digests(self) -> dict[str, str | None]:
        """Content digest of each output (``None`` when absent)."""
        return {name: out.sha256 for name, out in self.outputs.items()}

    def record(self) -> dict[str, object]:
        """Host-written summary keyed by output name."""
        return {name: out.record() for name, out in self.outputs.items()}


def _validated(data: bytes, contract: str | None) -> str | None:
    """Why ``data`` breaks its output contract, or ``None`` (``criteria/1`` is the only one)."""
    if contract is None:
        return None
    try:
        validate_criteria(json.loads(data.decode("utf-8")))
    except ValueError as error:
        return f"{contract}: {error}"
    return None


async def _copy_one(site: PackageSite, name: str, label: str) -> CopiedOutput:
    output = site.package.manifest.outputs[name]
    dest = site.evidence_dir / OUTPUTS_LOG / label / name
    copied = await snapshot_state(site.environment, output.path, dest)
    return finish_copy(name, output, copied, dest)


def finish_copy(name: str, output: Output, copied: TreeManifest, dest: Path) -> CopiedOutput:
    """A host-side copy of one output (however it was taken) re-read, re-hashed and validated."""
    if copied.violation is not None:
        return CopiedOutput(name, output.path, output.contract, error=copied.violation)
    entries = copied.entries
    if len(entries) != 1 or entries[0].kind != FILE_KIND:
        return CopiedOutput(name, output.path, output.contract, error="not a single regular file")
    host_file = dest / PurePosixPath(output.path).name
    data = host_file.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    if sha256 != entries[0].sha256:
        return CopiedOutput(name, output.path, output.contract, error="bytes changed during copy")
    return CopiedOutput(
        name,
        output.path,
        output.contract,
        sha256=sha256,
        size=len(data),
        host_path=str(host_file),
        error=_validated(data, output.contract),
    )


async def collect_outputs(site: PackageSite, label: str) -> OutputsResult:
    """Copy and validate every declared output; ``label`` names this collection's host folder."""
    try:
        found = {name: await _copy_one(site, name, label) for name in site.package.manifest.outputs}
    except TransportError as error:
        raise TrialInvalid("integrity", f"output transport failed: {error}") from error
    return OutputsResult(found)
