"""The canary: the package's known-good ``done`` fixture, run in the trial's own container.

Before the agent starts, the fixture runs through the same call path as a real stop call. Its
stdout and output digests must equal the passing ``stage-check`` record for exactly this package,
pack recipe and primitive identities; otherwise the trial is invalid. When a real stop call later
fails, the canary is run again to attribute the failure (public contract: canary attribution):

- canary passes: the failure is the agent's;
- canary fails with agent-writable disk or memory below its recorded floor: the agent's
  (``environment_exhausted``);
- canary fails with resources above the floors: the instrument's, trial invalid.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import JsonValue

from raidar.subject.primitive.calls import (
    ResourceFloors,
    TransportError,
    make_state_dir,
    resource_floors,
    root_command,
    tree_manifest,
    upload_state,
)
from raidar.subject.primitive.site import PackageSite, TrialInvalid

DONE_CASE = "done"
CANARY_ROOT = "/var/lib/raidar-calls/canary"
ENVIRONMENT_EXHAUSTED = "environment_exhausted"
AGENT_STATE = "agent_state"
WRITABLE_PATHS = ("/app", "/tmp")
"""Where an agent can fill disk; the recorded disk floor applies to the least free of them."""


@dataclass(frozen=True)
class StageCheckRecord:
    """What ``stage-check`` recorded for the package's passing ``done`` fixture."""

    package: str
    pack: str
    primitives: Mapping[str, str]
    stdout_sha256: str
    outputs: Mapping[str, str]
    disk_free: int
    memory_available: int | None

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> StageCheckRecord:
        """The record from its stored JSON (keys as in ``as_json``)."""
        floors = data["floors"]
        memory = floors.get("memory_available")
        return cls(
            package=str(data["package"]),
            pack=str(data["pack"]),
            primitives={str(key): str(value) for key, value in data["primitives"].items()},
            stdout_sha256=str(data["stdout_sha256"]),
            outputs={str(key): str(value) for key, value in data["outputs"].items()},
            disk_free=int(floors["disk_free"]),
            memory_available=None if memory is None else int(memory),
        )

    def as_json(self) -> dict[str, object]:
        """The stored JSON form."""
        return {
            "package": self.package,
            "pack": self.pack,
            "primitives": dict(self.primitives),
            "stdout_sha256": self.stdout_sha256,
            "outputs": dict(self.outputs),
            "floors": {"disk_free": self.disk_free, "memory_available": self.memory_available},
        }

    def identity_error(self, package: str, pack: str, primitives: Mapping[str, str]) -> str | None:
        """Why this record is not for these identities, or ``None``.

        The record's primitives must each equal the workflow's (the workflow may carry more).
        """
        if self.package != package:
            return f"stage-check record is for package {self.package}, not {package}"
        if self.pack != pack:
            return f"stage-check record is for pack {self.pack}, not {pack}"
        wrong = sorted(
            key for key, value in self.primitives.items() if primitives.get(key) != value
        )
        if wrong:
            return f"stage-check record is for different primitive identities: {', '.join(wrong)}"
        return None

    def below_floor(self, now: ResourceFloors) -> bool:
        """Whether agent-writable disk or memory is under what this record needed."""
        disk = min((free for free in now.disk_free.values() if free is not None), default=None)
        if disk is not None and disk < self.disk_free:
            return True
        return (
            now.memory_available is not None
            and self.memory_available is not None
            and now.memory_available < self.memory_available
        )


@dataclass(frozen=True)
class FixtureRun:
    """One fixture call in a throwaway state, and the digests that identify its result."""

    record: dict[str, object]
    usable: bool
    stdout_sha256: str
    outputs: dict[str, str | None]


def _fixture_json(path: Path) -> dict[str, JsonValue]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TrialInvalid("instrument", f"{path} is not a JSON object")
    return data


async def run_fixture(site: PackageSite, case: str, label: str) -> FixtureRun:
    """Run fixture ``case`` through the stop call path in a fresh state and output folder.

    The fixture's context supplies task, params, round and carry; state and outputs point at
    per-run folders, so the agent's state is never touched. Fixture files are read as data only.
    """
    stop = site.package.manifest.on_stop
    if stop is None:
        raise TrialInvalid("instrument", f"{site.reference} declares no stop to run a fixture for")
    fixture = site.package.fixture_dir(case)
    context = _fixture_json(fixture / "context.json")
    root = f"{CANARY_ROOT}/{site.scope}-{site.evidence_dir.name}/{label}"
    context["state"] = f"{root}/state"
    paths = _output_paths(site, root)
    context["outputs"] = dict(paths)
    try:
        await root_command(site.environment, f"rm -rf {shlex.quote(root)}")
        await _stage_state(site, fixture / "state", root)
        result = await site.runner.run(
            site.reference,
            "stop/1",
            stop.run,
            context,
            timeout_s=stop.timeout_s,
            path=site.installed.path,
            installed=[site.installed],
            state=f"{root}/state",
        )
        digests = {name: await _digest(site, path) for name, path in paths.items()}
    except TransportError as error:
        raise TrialInvalid("integrity", f"canary transport failed: {error}") from error
    if result.integrity_error is not None:
        raise TrialInvalid("integrity", result.integrity_error, result.record)
    return FixtureRun(
        record=result.record,
        usable=result.usable,
        stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
        outputs=digests,
    )


def _output_paths(site: PackageSite, root: str) -> dict[str, str]:
    """Where each declared output lands in the canary's own state: beside the state it came from."""
    found: dict[str, str] = {}
    for name, output in site.package.manifest.outputs.items():
        path = PurePosixPath(output.path)
        if site.watch is not None and path.is_relative_to(site.watch):
            found[name] = f"{root}/state/{path.relative_to(site.watch)}"
        else:
            found[name] = f"{root}/outputs/{name}"
    return found


async def _digest(site: PackageSite, path: str) -> str | None:
    """The content digest of the one regular file at ``path`` (``None``: absent or not a file)."""
    written = await tree_manifest(site.environment, path)
    if written.violation is not None or len(written.entries) != 1:
        return None
    return written.entries[0].sha256


async def _stage_state(site: PackageSite, host_state: Path, root: str) -> None:
    """Put the fixture's state and an empty outputs folder in the container, owned by UID 1000."""
    if host_state.is_dir():
        await upload_state(site.environment, host_state, f"{root}/state")
    else:
        await make_state_dir(site.environment, f"{root}/state")
    await make_state_dir(site.environment, f"{root}/outputs")


class Canary:
    """A package's control run against its ``stage-check`` record."""

    def __init__(self, site: PackageSite, expected: StageCheckRecord) -> None:
        self.site = site
        self.expected = expected
        self.runs: list[dict[str, object]] = []

    async def _passes(self, label: str) -> bool:
        run = await run_fixture(self.site, DONE_CASE, f"{label}-{len(self.runs)}")
        same = run.stdout_sha256 == self.expected.stdout_sha256
        same = same and run.outputs == dict(self.expected.outputs)
        passed = run.usable and same
        self.runs.append(
            {
                "label": label,
                "passed": passed,
                "expected": {
                    "stdout_sha256": self.expected.stdout_sha256,
                    "outputs": dict(self.expected.outputs),
                },
                "actual": {"stdout_sha256": run.stdout_sha256, "outputs": run.outputs},
                "call": run.record,
            }
        )
        return passed

    async def require(self, package: str, pack: str, primitives: Mapping[str, str]) -> None:
        """Before the agent: the record is for these identities and the fixture reproduces it."""
        mismatch = self.expected.identity_error(package, pack, primitives)
        if mismatch is not None:
            raise TrialInvalid("instrument", mismatch)
        if not await self._passes("initial"):
            raise TrialInvalid(
                "instrument", "canary failed before the agent started", self.runs[-1]
            )

    async def attribute_failure(self) -> str:
        """Attribute a failed stop call: ``agent_state`` or ``environment_exhausted``.

        Raises ``TrialInvalid`` for integrity failures regardless of resource floors,
        or when the canary fails with resources above their floors.
        """
        if await self._passes("recheck"):
            return AGENT_STATE
        if await self._exhausted():
            return ENVIRONMENT_EXHAUSTED
        raise TrialInvalid(
            "instrument", "stop call and canary both failed above resource floors", self.runs[-1]
        )

    async def _exhausted(self) -> bool:
        now = await resource_floors(self.site.environment, WRITABLE_PATHS)
        return self.expected.below_floor(now)
