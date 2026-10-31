"""Run a ``schema: 2`` workflow's packages through the runner's one stage loop.

``PackageStage`` is the runner's single ``Stage`` for every package workflow: it holds no branch
for a named package. Before the agent starts it holds each package to the lock
(``package_trial``: bytes, installed tree and check record; a difference makes the trial invalid,
never an agent outcome), places package skills under the agent's skills placement, and lets
``PackageCheckpoint`` (setup, frame, canary, gate, stop, outputs) do the rest.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from raidar_contract.package.manifest import StagePackage
from raidar_contract.package.tokens import Frame
from raidar_contract.workflow import PackageWorkflowDefinition, StageUse

from raidar.subject.primitive import calls
from raidar.subject.primitive.install import InstalledPackage
from raidar.subject.primitive.site import TrialInvalid
from raidar.subject.workflow.package import PackageCheckpoint
from raidar.subject.workflow.package_member import PackageSpec
from raidar.subject.workflow.package_trial import (
    install_locked,
    load_locked_package,
    locked_check_record,
)
from raidar.subject.workflow.stage import Checkpoint, Resume, Stage

SKILLS_PREFIX = "skills/"
WORKSPACE = "/app"


class PackageStage(Stage):
    """The enabled packages of one checkpoint (module docstring)."""

    name = "build"
    token = None

    def __init__(
        self,
        definition: PackageWorkflowDefinition,
        *,
        locked: Mapping[str, Mapping[str, Any]],
        primitives: Mapping[str, str],
        pack: str,
        skills_placement: str,
        repo_root: Path | None = None,
    ) -> None:
        self.definition = definition
        self.locked = locked
        self.primitives = dict(primitives)
        self.pack = pack
        self.skills_placement = skills_placement
        self.repo_root = repo_root
        self.instructions = []
        self.installed: list[InstalledPackage] = []
        self.components: list[dict[str, str]] = []
        self.checkpoint: PackageCheckpoint | None = None
        self.frame: Frame | None = None
        self.loaded: list[tuple[StageUse, StagePackage]] = []

    def fragment(self) -> str:
        return self.definition.build.prompt

    @property
    def prompt(self) -> str:
        """The checkpoint's one framed prompt (available once ``prepare`` has run)."""
        assert self.frame is not None
        return self.frame.prompt

    def load(self) -> None:
        """Read every package from the tree and hold it to the lock (``TrialInvalid`` otherwise)."""
        self.loaded = [
            (use, load_locked_package(use.release, self.locked, self.repo_root))
            for use in self.definition.stages
        ]

    @property
    def instructions_text(self) -> str | None:
        """The workflow's instructions: each package's ``instructions.md`` in workflow order."""
        texts = [pkg.instructions.strip("\n") for _, pkg in self.loaded if pkg.instructions]
        joined = "\n\n".join(text for text in texts if text)
        return joined or None

    async def _place_skills(self, checkpoint: Checkpoint, package: StagePackage) -> None:
        for item in package.files:
            if not item.path.startswith(SKILLS_PREFIX):
                continue
            relative = f"{self.skills_placement}/{item.path.removeprefix(SKILLS_PREFIX)}"
            record = PurePosixPath("workflow", "components", package.name, relative)
            path = checkpoint.logs_dir / record
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item.data)
            await calls.upload_file(checkpoint.environment, path, f"{WORKSPACE}/{relative}")
            self.components.append(
                {"path": relative, "sha256": item.sha256, "record": record.as_posix()}
            )

    async def _spec(
        self, checkpoint: Checkpoint, use: StageUse, package: StagePackage
    ) -> PackageSpec:
        release, locked = use.release, self.locked[use.release.name]
        installed = await install_locked(
            checkpoint.environment, checkpoint.logs_dir, release, package, locked
        )
        self.installed.append(installed)
        await self._place_skills(checkpoint, package)
        expected = locked_check_record(release, package, locked)
        return PackageSpec(
            release.name, package, installed, package.effective_params(use.params), expected
        )

    async def prepare(self, checkpoint: Checkpoint) -> None:
        if not self.loaded:
            self.load()
        specs = [await self._spec(checkpoint, use, package) for use, package in self.loaded]
        self.checkpoint = PackageCheckpoint(
            checkpoint,
            specs,
            build=self.definition.build.prompt,
            pack=self.pack,
            primitives=self.primitives,
            max_fix_rounds=self.definition.gate.max_fix_rounds,
        )
        self.frame = await self.checkpoint.prepare()

    async def check(self, checkpoint: Checkpoint, after: str) -> Resume | None:
        del checkpoint
        assert self.checkpoint is not None
        return await self.checkpoint.check(after)

    async def close(self, checkpoint: Checkpoint, cut: str | None) -> None:
        del checkpoint
        if self.checkpoint is None:
            return
        await self.checkpoint.close(cut)
        for installed in self.installed:
            found = await installed.verify()
            if not found.ok:
                raise TrialInvalid(
                    "integrity",
                    f"{installed.reference}: the installed tree changed during the trial "
                    f"({found.violation or found.digest[:12]})",
                )

    def package_record(self) -> dict[str, dict[str, Any]]:
        """Host-written evidence keyed by primitive contract id."""
        return {} if self.checkpoint is None else self.checkpoint.record()
