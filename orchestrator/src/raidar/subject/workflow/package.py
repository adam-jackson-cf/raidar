"""Run stage packages inside one checkpoint's primary session (public contract: stage/1).

``PackageCheckpoint`` is the one runtime for every enabled package, whatever its method. It runs
the primitives in the contract's order and holds no branch for a named package:

1. ``prepare`` (before the agent): each package's ``setup/1`` in workflow order, the ``frame/1``
   prompt with its tokens, the ``gate/1`` start-up and the package canary;
2. ``check`` (after each agent turn): the gate seal, then each package's ``stop/1`` in workflow
   order; the first resume wins and resumes the same primary session;
3. ``close`` (once the checkpoint ends, cut or not): the ``outputs/1`` copy;
4. ``record``: host-written evidence grouped by primitive contract id, never by package name.

Packages are installed by the caller (``install/1``); this module takes their ``InstalledPackage``.
The invalidity of the instrument, in any primitive, surfaces as ``TrialInvalid``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from raidar.subject.primitive.calls import CallRunner, TransportError
from raidar.subject.primitive.frame import Frame, FramePart, render_frame
from raidar.subject.primitive.gate import (
    Attribution,
    GateInvalid,
    GateOutcome,
    GatePackage,
    GateRun,
    GateSettings,
)
from raidar.subject.primitive.outputs import collect_outputs
from raidar.subject.primitive.setup import run_setup
from raidar.subject.primitive.site import PackageSite, TrialInvalid
from raidar.subject.primitive.stop import closed_flag_path, final_decision_record, run_stop
from raidar.subject.workflow.package_member import PackageMember, PackageSpec
from raidar.subject.workflow.stage import DEADLINE, GATE_FIX, REPAIR, REWORK, Checkpoint, Resume


@contextmanager
def _invalidity() -> Iterator[None]:
    """Container transport failure and the gate's invalidity are ``TrialInvalid`` here."""
    try:
        yield
    except TransportError as error:
        raise TrialInvalid("integrity", f"container transport failed: {error}") from error
    except GateInvalid as error:
        raise TrialInvalid(error.category, str(error)) from error


class PackageCheckpoint:
    """The enabled packages of one checkpoint, in workflow order."""

    def __init__(
        self,
        checkpoint: Checkpoint,
        specs: Sequence[PackageSpec],
        *,
        build: str,
        pack: str,
        primitives: Mapping[str, str],
        max_fix_rounds: int = 0,
        runner: CallRunner | None = None,
    ) -> None:
        self.checkpoint = checkpoint
        self.build = build
        self.pack = pack
        self.primitives = dict(primitives)
        self.max_fix_rounds = max_fix_rounds
        runner = runner or CallRunner(checkpoint.environment, checkpoint.logs_dir)
        self.members = [
            PackageMember(
                PackageSite(
                    checkpoint.environment,
                    runner,
                    checkpoint.logs_dir,
                    spec.package,
                    spec.reference,
                    spec.installed,
                    checkpoint.task,
                    dict(spec.params),
                ),
                spec,
            )
            for spec in specs
        ]
        self.gate: GateRun | None = None
        self.fix_rounds = 0
        self.endings: dict[str, str] = {}
        self.frame: Frame | None = None

    # before the agent starts ---------------------------------------------------------------

    async def prepare(self) -> Frame:
        """Set every package up, frame the prompt, start the gate and run the canaries.

        Returns the framed prompt and instruction text for the checkpoint's first turn.
        Raises ``TrialInvalid`` for a failed setup, canary, gate start-up or tool integrity.
        """
        with _invalidity():
            for member in self.members:
                member.setup = await run_setup(member.site)
            self.frame = render_frame(self.build, self.checkpoint.task, self._parts())
            for member in self.members:
                if member.canary is not None:
                    await member.canary.require(
                        member.spec.package.digest, self.pack, self.primitives
                    )
            await self._start_gate()
        return self.frame

    def _parts(self) -> list[FramePart]:
        return [
            FramePart(
                name=member.name,
                fragment=member.spec.package.prompt,
                params=member.spec.params,
                tokens=member.setup.tokens,
                instructions=member.spec.package.instructions,
            )
            for member in self.members
        ]

    async def _start_gate(self) -> None:
        gated = [
            GatePackage(
                member.name,
                member.spec.installed,
                member.spec.package.manifest.gate,
                member.site.context(round_=0, carry={}),
                closed_flag_path(member.site),
                member.spec.package.manifest.outputs,
            )
            for member in self.members
            if member.spec.package.manifest.gate
        ]
        if not gated:
            return
        settings = GateSettings(self.max_fix_rounds)
        self.gate = GateRun(self.checkpoint.environment, self.checkpoint.logs_dir, gated, settings)
        await self.gate.prepare(self.checkpoint.session_paths)

    # after each agent turn -----------------------------------------------------------------

    async def check(self, after: str) -> Resume | None:
        """Check the agent's work after the turn logged at ``after``.

        The gate seal first, then each package's stop in workflow order. The first request to
        resume wins; later packages are checked after the next turn. ``None`` lets the
        checkpoint end as far as packages are concerned.
        """
        with _invalidity():
            if self.gate is not None:
                resume = await self._gate_fix(self.gate, await self.gate.seal(after))
                if resume is not None:
                    return resume
            for member in self.members:
                resume = await self._stop(member)
                if resume is not None:
                    return resume
                if member.name in self.endings:
                    break  # a deadline cut ends the checkpoint: later packages are not asked
        return None

    async def _gate_fix(self, gate: GateRun, outcome: GateOutcome) -> Resume | None:
        if outcome.attribution in (Attribution.INSTRUMENT, Attribution.INTEGRITY):
            raise TrialInvalid(
                "integrity" if outcome.attribution is Attribution.INTEGRITY else "instrument",
                outcome.reason,
                outcome.record,
            )
        if not outcome.resumable or self.fix_rounds >= self.max_fix_rounds:
            return None
        if not self._fits(GATE_FIX):
            return None
        self.fix_rounds += 1
        await gate.retain_prefix(self.fix_rounds)
        failed = [
            str(check.get("name"))
            for check in outcome.record.get("checks", [])
            if check.get("status") not in (None, "passed")
        ]
        detail = f" ({', '.join(failed)})" if failed else ""
        message = f"The checks did not pass{detail}: {outcome.reason}. Fix your work, then finish."
        return Resume(message, GATE_FIX)

    async def _stop(self, member: PackageMember) -> Resume | None:
        if member.canary is None:
            return None
        decision = await run_stop(member.site, member.stop, member.canary)
        if decision.action in ("done", "closed"):
            return None
        kind = REWORK if decision.action == "resume" else REPAIR
        if not self._fits(kind):
            self.endings[member.name] = DEADLINE
            return None
        return Resume(decision.message, kind)

    def _fits(self, kind: str) -> bool:
        """A resume turn starts only if the last turn of its kind and the gate run that follows
        it (the last gate's seconds) fit in the time left."""
        left = self.checkpoint.remaining()
        if left is None:
            return True
        return left > 0 and left >= self.checkpoint.last_seconds(kind) + self._gate_seconds()

    def _gate_seconds(self) -> float:
        if self.gate is None:
            return 0.0
        gates = self.gate.record()["gates"]
        return float(gates[-1].get("seconds") or 0.0) if gates else 0.0

    # when the checkpoint ends --------------------------------------------------------------

    async def close(self, cut: str | None) -> None:
        """Copy every declared output host-side (``cut`` names why the checkpoint was cut)."""
        with _invalidity():
            for member in self.members:
                if member.spec.package.manifest.outputs:
                    result = await collect_outputs(member.site, "close")
                    member.outputs = {**result.record(), "cut": cut}
            if self.gate is not None:
                await self.gate.close()

    def record(self) -> dict[str, dict[str, Any]]:
        """Host-written evidence of this checkpoint, keyed by primitive contract id."""
        members = self.members
        record: dict[str, dict[str, Any]] = {
            "frame/1": dict(self.frame.fragments) if self.frame is not None else {},
            "install/1": {m.name: m.spec.installed.record for m in members},
            "setup/1": self._setup_record(),
            "stop/1": self._stop_record(),
            "outputs/1": {m.name: m.outputs for m in members if m.outputs},
        }
        if self.gate is not None:
            record["gate/1"] = self.gate.record()
        if self.endings:
            record["endings"] = dict(self.endings)
        return record

    def _setup_record(self) -> dict[str, Any]:
        return {m.name: m.setup.record for m in self.members if m.spec.package.manifest.setup}

    def _stop_record(self) -> dict[str, Any]:
        return {
            m.name: {**final_decision_record(m.stop), "canary": m.canary.runs}
            for m in self.members
            if m.canary is not None
        }
