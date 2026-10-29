"""Host copies of the outputs each ``gate/1`` check captured right after it ran.

The gate payload snapshots a package's declared outputs after every check (trusted code, bounded,
no-follow) and records the manifest and archive path on the check. Here the host downloads each
archive, verifies it against its manifest and keeps one immutable copy per call, check and output:
``<label>/<check>/<output>/``. A capture that is absent, refused or does not verify is that output's
``error`` (evidence, never invalidity: only a ``done`` stop owes outputs). A failed container call
raises ``calls.TransportError`` for the caller to turn into an integrity failure.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from raidar.subject.package.manifest import Output
from raidar.subject.primitive import calls
from raidar.subject.primitive.outputs import CopiedOutput, finish_copy

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class CheckOutputs:
    """The copies of one gate's checks' outputs; ``declared`` maps ``package.check`` to outputs."""

    def __init__(
        self,
        environment: Any,
        scratch: str,
        folder: Path,
        declared: Mapping[str, Mapping[str, Output]],
    ) -> None:
        self.environment = environment
        self.archives = f"{scratch}/outputs/"
        self.folder = folder
        self.declared = declared
        self.hook_runs: list[dict[str, Any]] = []
        self._hook_lines = 0

    async def keep(self, checks: object, label: str) -> None:
        """Replace each check's capture with its host copy (``label`` names the call)."""
        for check in checks if isinstance(checks, list) else []:
            captured = check.get("outputs") if isinstance(check, dict) else None
            if not isinstance(captured, dict):
                continue
            name = str(check.get("name"))
            where = self.folder / label / _UNSAFE.sub("_", name)
            check["outputs"] = {
                out: (await self._copy(out, output, captured.get(out), where / out)).record()
                for out, output in self.declared.get(name, {}).items()
            }

    async def keep_hook(self, log: str) -> None:
        """Copies for each commit-hook run in the hook's log not seen before.

        The agent's user writes that log, so a run is named by its line number, never by a field
        a line claims.
        """
        lines = [line for line in log.splitlines() if line.strip()]
        for number, line in enumerate(lines[self._hook_lines :], self._hook_lines + 1):
            run = _json_object(line)
            checks = run.get("checks")
            if not isinstance(checks, list):
                continue
            await self.keep(checks, f"hook-{number}")
            kept = [
                {"name": check.get("name"), "outputs": check["outputs"]}
                for check in checks
                if isinstance(check, dict) and "outputs" in check
            ]
            if kept:
                self.hook_runs.append({"seq": number, "checks": kept})
        self._hook_lines = len(lines)

    async def _copy(self, name: str, output: Output, captured: object, dest: Path) -> CopiedOutput:
        def failed(why: object) -> CopiedOutput:
            return CopiedOutput(name, output.path, output.contract, error=str(why))

        if not isinstance(captured, dict):
            return failed("the check captured nothing")
        if "entries" not in captured:
            return failed(captured.get("violation") or "malformed capture")
        manifest = calls.record_manifest(captured)
        tar = str(captured.get("tar", ""))
        if posixpath.normpath(tar) != tar or not tar.startswith(self.archives):
            return failed(manifest.violation or "capture archive outside the gate's scratch")
        copied = await calls.copy_snapshot(self.environment, manifest, tar, dest)
        return finish_copy(name, output, copied, dest)


def _json_object(line: str) -> dict[str, Any]:
    try:
        found = json.loads(line)
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}
