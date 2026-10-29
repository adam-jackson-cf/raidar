"""What the lifecycle primitives (``setup/1``, ``stop/1``, ``outputs/1``, the canary) share.

A ``PackageSite`` is one package inside one checkpoint's container: the installed tool, the task,
the effective params and where host-side evidence goes. ``invoke`` is the one way these primitives
call a package: it snapshots the watched state before and after for replay, refuses state outside
Raidar's bounds before the tool is called, and turns container-transport failure and changed tool
bytes into an integrity ``TrialInvalid``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import JsonValue

from raidar.subject.package.manifest import StagePackage
from raidar.subject.primitive.calls import (
    CallResult,
    CallRunner,
    TransportError,
    TreeManifest,
    snapshot_state,
)
from raidar.subject.primitive.install import InstalledPackage

STAGES_LOG = "stages"
MISSING = "missing"
"""A watched state directory that does not exist yet is a state, not a bounds violation."""

Invalidity = Literal["instrument", "integrity"]


class TrialInvalid(RuntimeError):
    """The trial's instrument failed: ``instrument`` (the tool or its control run) or ``integrity``.

    Neither is an agent outcome, so the runner marks the trial invalid (the canary rule).
    """

    def __init__(self, kind: Invalidity, detail: str, record: Mapping[str, object] | None = None):
        super().__init__(f"{kind}: {detail}")
        self.kind: Invalidity = kind
        self.detail = detail
        self.record: dict[str, object] = dict(record or {})


@dataclass(frozen=True)
class PackageSite:
    """One package in one checkpoint's container."""

    environment: Any
    runner: CallRunner
    logs_dir: Path
    package: StagePackage
    reference: str
    """The package's ``<ns>/<name>`` as released."""
    installed: InstalledPackage
    task: str
    params: Mapping[str, JsonValue] = field(default_factory=dict)

    @property
    def outputs(self) -> dict[str, str]:
        """The declared outputs' container paths by name."""
        return {name: output.path for name, output in self.package.manifest.outputs.items()}

    @property
    def watch(self) -> str | None:
        """The package's watched state directory: the stop's, else the first gate check's."""
        manifest = self.package.manifest
        if manifest.on_stop is not None and manifest.on_stop.watch:
            return manifest.on_stop.watch
        return next((check.watch for check in manifest.gate if check.watch), None)

    @property
    def evidence_dir(self) -> Path:
        """Host directory holding this package's snapshots and copied outputs."""
        return self.logs_dir / STAGES_LOG / re.sub(r"[^A-Za-z0-9._-]+", "_", self.reference)

    @property
    def scope(self) -> str:
        """A short key of this checkpoint's logs folder: container state of one checkpoint's
        calls never collides with another checkpoint's in a reused container."""
        return hashlib.sha256(str(self.logs_dir).encode()).hexdigest()[:12]

    def context(self, *, round_: int, carry: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        """The call context file's content (public contract: call protocol)."""
        return {
            "task": self.task,
            "params": dict(self.params),
            "round": round_,
            "carry": dict(carry),
            "state": self.watch,
            "outputs": self.outputs,
        }


@dataclass(frozen=True)
class Invocation:
    """One host-recorded package call, or the bounds violation that stopped it before the call."""

    result: CallResult | None
    bounds_violation: str | None
    snapshots: Mapping[str, str | None] = field(default_factory=dict)
    """``before`` / ``after`` state manifest digests (``None``: no watched state)."""

    def record(self) -> dict[str, object]:
        """Host-written summary for ``workflow.json``."""
        return {
            "call": None if self.result is None else self.result.record,
            "bounds_violation": self.bounds_violation,
            "state": dict(self.snapshots),
        }


async def snapshot(site: PackageSite, label: str) -> TreeManifest | None:
    """Copy the watched state (bounded, no-follow) for replay; ``None`` without a watch."""
    if site.watch is None:
        return None
    return await snapshot_state(site.environment, site.watch, site.evidence_dir / label)


async def invoke(
    site: PackageSite,
    primitive: str,
    argv: Sequence[str],
    timeout_s: float,
    *,
    round_: int,
    carry: Mapping[str, JsonValue],
    label: str,
    append_context: bool = True,
) -> Invocation:
    """Call the package once as ``primitive`` (contract id) with the state snapshotted around it.

    State outside the bounds is returned as ``bounds_violation`` without calling the tool. A
    transport failure or a changed tool tree raises ``TrialInvalid("integrity", ...)``.
    """
    context = site.context(round_=round_, carry=carry)
    site.evidence_dir.mkdir(parents=True, exist_ok=True)
    (site.evidence_dir / f"{label}-context.json").write_text(
        json.dumps(context, sort_keys=True), encoding="utf-8"
    )
    before = await snapshot(site, f"{label}-before")
    if before is not None and before.violation not in (None, MISSING):
        return Invocation(None, before.violation, {"before": before.digest, "after": None})
    bounded = before is not None and before.violation is None
    try:
        result = await site.runner.run(
            site.reference,
            primitive,
            argv,
            context,
            timeout_s=timeout_s,
            path=site.installed.path,
            installed=[site.installed],
            state=site.watch if bounded else None,
            append_context=append_context,
        )
    except TransportError as error:
        raise TrialInvalid("integrity", f"{primitive} call transport failed: {error}") from error
    if result.integrity_error is not None:
        raise TrialInvalid("integrity", result.integrity_error, result.record)
    after = await snapshot(site, f"{label}-after")
    return Invocation(
        result,
        result.bounds_violation,
        {
            "before": None if before is None else before.digest,
            "after": None if after is None else after.digest,
        },
    )
