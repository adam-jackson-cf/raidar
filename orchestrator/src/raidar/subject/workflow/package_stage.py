"""Run a ``schema: 2`` workflow's packages through the runner's one stage loop.

``PackageStage`` is the runner's single stage for every package workflow: it holds no branch
for a named package. Before the agent starts it holds each package to the lock
(``package_trial``: bytes, installed tree and check record; a difference makes the trial invalid,
never an agent outcome), places package skills under the agent's skills placement, and lets
``PackageCheckpoint`` (setup, frame, canary, gate, stop, outputs) do the rest.

A workflow may also opt into two Raidar-owned primitives that no package implements:
``acceptance-reply/1`` (``build.reply``: the builder's structured reply, read after every turn)
and ``jev-judge/1`` (``judge``: the host-side aspect judge of the sealed gate). After the packages'
own check, ``check`` reads the reply and then judges the seal; the first resume wins.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from raidar_contract.package.manifest import StagePackage
from raidar_contract.workflow import PackageWorkflowDefinition, StageUse

from raidar.subject.workflow.acceptance import Acceptance
from raidar.subject.workflow.acceptance_reply import AcceptanceReply
from raidar.subject.workflow.identity import package_primitives
from raidar.subject.workflow.package import CheckpointPlan, PackageCheckpoint
from raidar.subject.workflow.package_judge import JevJudge
from raidar.subject.workflow.package_member import PackageSpec
from raidar.subject.workflow.package_trial import (
    install_locked,
    load_locked_package,
    locked_check_record,
    place_skills,
    verify_installed,
)
from raidar.subject.workflow.stage import Checkpoint, Resume

WORKSPACE = "/app"
REPLY_RECORD = "acceptance-reply/1"
JUDGE_RECORD = "jev-judge/1"


class PackageStage:
    """The enabled packages of one checkpoint (module docstring)."""

    def __init__(
        self,
        definition: PackageWorkflowDefinition,
        *,
        locked: Mapping[str, Mapping[str, Any]],
        pack: str,
        skills_placement: str,
        repo_root: Path | None = None,
    ) -> None:
        self.definition = definition
        self.locked = locked
        self.pack = pack
        self.skills_placement = skills_placement
        self.repo_root = repo_root
        self.components: list[dict[str, str]] = []
        self.checkpoint: PackageCheckpoint | None = None
        self._prompt: str | None = None
        self.loaded: list[tuple[StageUse, StagePackage]] = []
        self.reply: AcceptanceReply | None = None
        self.judge: JevJudge | None = None

    @property
    def prompt(self) -> str:
        """The checkpoint's one framed prompt (available once ``prepare`` has run)."""
        assert self._prompt is not None
        return self._prompt

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

    def decorate(self, kind: str, prompt: str) -> str:
        """The turn's prompt with ``acceptance-reply/1``'s shape appended where it applies."""
        return prompt if self.reply is None else self.reply.decorate(kind, prompt)

    async def _spec(
        self, checkpoint: Checkpoint, use: StageUse, package: StagePackage
    ) -> PackageSpec:
        release, locked = use.release, self.locked[use.release.name]
        installed = await install_locked(
            checkpoint.environment, checkpoint.logs_dir, release, package, locked
        )
        self.components.extend(
            await place_skills(
                checkpoint.environment, checkpoint.logs_dir, package, self.skills_placement
            )
        )
        expected = locked_check_record(release, package, locked)
        return PackageSpec(
            release.name, package, installed, package.effective_params(use.params), expected
        )

    async def prepare(self, checkpoint: Checkpoint) -> None:
        acceptance = Acceptance()
        self.reply = None
        self.judge = None
        if self.definition.build.reply is not None:
            self.reply = AcceptanceReply(acceptance, WORKSPACE)
        if self.definition.judge is not None:
            self.judge = JevJudge(self.definition.judge, acceptance, WORKSPACE)
            self.judge.start(checkpoint)  # an unusable judge is invalid before any tool runs
        if not self.loaded:
            self.load()
        specs = [await self._spec(checkpoint, use, package) for use, package in self.loaded]
        judge = self.judge
        plan = CheckpointPlan(
            build=self.definition.build.prompt,
            pack=self.pack,
            primitives=package_primitives(package for _, package in self.loaded),
            max_fix_rounds=self.definition.gate.max_fix_rounds,
            baseline=judge is not None and judge.config.regression_needs_baseline,
            reserve_seconds=None if judge is None else lambda: judge.seconds,
        )
        self.checkpoint = PackageCheckpoint(checkpoint, specs, plan)
        frame = await self.checkpoint.prepare()
        self._prompt = frame.prompt
        if judge is not None:
            judge.take_baseline(self.checkpoint.gate)

    async def check(self, checkpoint: Checkpoint, after: str) -> Resume | None:
        assert self.checkpoint is not None
        resume = await self.checkpoint.check(after)
        if self.judge is not None:
            self.judge.load_criteria(self.checkpoint)  # known before the reply is checked
        if resume is not None:
            return resume
        if self.reply is not None:
            resume = await self.reply.check(checkpoint, after)
            if resume is not None:
                return resume
        if self.judge is not None:
            return await self.judge.after(checkpoint, self.checkpoint)
        return None

    async def close(self, checkpoint: Checkpoint, cut: str | None) -> None:
        del checkpoint
        if self.checkpoint is None:
            return
        await self.checkpoint.close(cut)
        if self.judge is not None:
            self.judge.close(cut)
        await verify_installed([member.spec.installed for member in self.checkpoint.members])

    def package_record(self) -> dict[str, dict[str, Any]]:
        """Host-written evidence keyed by primitive contract id."""
        record = {} if self.checkpoint is None else self.checkpoint.record()
        if self.reply is not None:
            record[REPLY_RECORD] = self.reply.record()
        if self.judge is not None:
            record[JUDGE_RECORD] = self.judge.record()
        return record
