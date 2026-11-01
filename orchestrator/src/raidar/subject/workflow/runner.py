"""The workflow runner: the Harbor agent of every real arm (design section 5.5).

Harbor runs one agent phase per checkpoint (one ``run`` per step), so the reaper, the liveness
monitor and the per-step reset see the same phases they saw for the native agent. Inside the
phase the runner drives the native agent (``inner_agent``, built by ``create_inner``) through
the workflow's one ``PackageStage`` (``raidar.subject.workflow.package_stage``), host-side, one
*turn* at a time, all in the checkpoint's one primary session:

1. the package runtime prepares (before the agent starts): lock checks, installs, setup calls,
   the framed prompt, the gate and the canaries;
2. the framed prompt (``PackageStage.prompt``) runs as the first turn, in a fresh session
   (``run``);
3. when the agent stops, the runtime's checks run; one that returns a ``Resume`` resumes the same
   session with its message (``resume``, a turn of the ``Resume``'s kind: ``gate_fix``,
   ``rework`` or ``repair``; the first prompt is ``first_build``) and the checks run again. The
   runtime bounds its own resumes; when none asks, the checkpoint ends. The runtime may add to a
   turn's prompt (``PackageStage.decorate``: the build reply's shape); each finished turn's kind
   and seconds is on ``Checkpoint.turns``, and ``Checkpoint.deadline`` is when the phase is cut
   (``phase_timeout_s`` from the phase start: the arm's agent timeout as Harbor scales it), so a
   check can decline a turn it could not finish;
4. the runtime closes, also when a turn failed (``turn_error``) or the phase was cancelled
   (``deadline``), and its per-primitive records go into ``workflow.json`` under their
   contract ids.

Each checkpoint starts a fresh session because Harbor empties ``/logs/agent`` before each step.
The native-agent protocol owns isolation, process facts, usage normalization, final replies,
resume storage and session auditing. The runner copies logs without moving the native store,
records normalized per-turn deltas and session totals, and writes ``last-message.txt`` for
consumers. Unknown usage remains unknown rather than a partial phase total.

**Evidence stays host-side.** ``/logs/agent`` is mounted and the agent's user can write it, so
the runtime's records (``Checkpoint.logs_dir``) are kept in a host staging directory outside it,
each turn's logs are copied by the host (links kept as links) after the turn, and everything is
published into the step's agent logs (replacing whatever the agent put under the published
names) only after the runtime closed and reaped the agent's processes: ``workflow/`` and the
primitives' evidence folders (``EVIDENCE_DIRS``: stage outputs and snapshots, gate records,
call records, install records). A recorded ``host_path`` that names a staging file is rewritten
before the record is kept to its path below the step's agent logs (``stages/...``), so the
published evidence stands on its own and an offline reader needs no host path.
``SessionWatch`` records a named ``integrity`` entry when an earlier copy of the native session
store was rewritten.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import shutil
import tempfile
import time
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from raidar.subject.primitive import calls
from raidar.subject.primitive.site import TrialInvalid
from raidar.subject.workflow.inner import WorkflowRunnerOptions, create_inner
from raidar.subject.workflow.package_stage import PackageStage
from raidar.subject.workflow.stage import (
    DEADLINE,
    FIRST_BUILD,
    TURN_ERROR,
    Checkpoint,
    Spent,
)
from raidar.subject.workflow.turns import (
    TRAJECTORY_FILE,
    WORKFLOW_DIR,
    Run,
    Turn,
    native_account,
    native_audit,
    native_last_message,
    native_observe,
    native_session_store,
    now,
    session_id,
)

RUNNER_NAME = "raidar-workflow"
RECORD_FILE = "workflow.json"
RECORD_KIND = "raidar-workflow"
PROMPT_FILE = "prompt.txt"
CTIME_SCRIPT = """\
import json, os, sys
root, found = sys.argv[1], {}
for name in sys.argv[2:]:
    base = os.path.join(root, name)
    if os.path.isfile(base):
        paths = [base]
    else:
        paths = [os.path.join(d, f) for d, _, files in os.walk(base) for f in files]
    for path in paths:
        if not os.path.islink(path):
            found[os.path.relpath(path, root)] = os.lstat(path).st_ctime_ns
print(json.dumps(found))
"""
LAST_MESSAGE_FILE = "last-message.txt"
STEP_RECORD_PREFIX = "raidar-"
"""Step-level lab records in ``/logs/agent`` (reset report, provision records): not a turn's."""
INVALID = "invalid"
"""Why a checkpoint was cut when a package's instrument failed (``TrialInvalid``)."""
BUILD = "build"
"""The stage name every turn carries: a package workflow is one build frame."""
EVIDENCE_DIRS = ("stages", "gate", "calls", "install")
"""Host evidence the primitives write beside ``workflow/`` (``site``, ``gate``, ``calls``,
``install``): published at the same names in the step's agent logs."""
HOST_PATH = "host_path"
"""The key of a recorded host file (``outputs/1``)."""


class SessionWatch:
    """Honest detection of rewrites of the native session store (the agent's user can write it).

    After each turn the runner keeps a host copy and remembers each file's size, the digest of its
    content and its ``ctime`` as the container reports it (read by the agent's user, whether or not
    ``/logs/agent`` is mounted). At the next turn every earlier file
    must still begin with the bytes it had (append-only), must exist, and must not have been
    *touched* without growing: a ``ctime`` cannot be set back by the agent's user, so a write
    followed by a restore of the same bytes shows as a changed ``ctime`` on an unchanged file.
    What it cannot see: a rewrite that is restored within one turn of a file the native agent was
    also appending to, and any forgery in the part of a file written during the turn itself.
    """

    def __init__(self) -> None:
        self.seen: dict[str, tuple[int, str, int | None]] = {}

    @staticmethod
    def _files(staged: Path, store: Sequence[str]) -> list[Path]:
        found: list[Path] = []
        for name in store:
            base = staged / name
            if base.is_file() and not base.is_symlink():
                found.append(base)
            elif base.is_dir():
                found += sorted(p for p in base.rglob("*") if p.is_file() and not p.is_symlink())
        return found

    def observe(
        self,
        staged: Path,
        ctimes: dict[str, int] | None,
        store: Sequence[str],
        *,
        grew_expected: bool,
    ) -> list[dict[str, str]]:
        """Violations seen in this turn's copy of the session store (and remember its state)."""
        found: list[dict[str, str]] = []
        current: dict[str, tuple[int, str, int | None]] = {}
        for path in self._files(staged, store):
            relative = path.relative_to(staged).as_posix()
            data = path.read_bytes()
            ctime = None if ctimes is None else ctimes.get(relative)
            current[relative] = (len(data), hashlib.sha256(data).hexdigest(), ctime)
            before = self.seen.get(relative)
            if before is None:
                continue
            finding = self._changed(relative, data, ctime, before, grew_expected)
            if finding is not None:
                found.append(finding)
        found += [
            {"name": "session_log_removed", "detail": f"{name}: removed"}
            for name in sorted(set(self.seen) - set(current))
        ]
        self.seen = current
        return found

    @staticmethod
    def _changed(
        relative: str,
        data: bytes,
        ctime: int | None,
        before: tuple[int, str, int | None],
        grew_expected: bool,
    ) -> dict[str, str] | None:
        size, digest, old = before
        if len(data) < size or hashlib.sha256(data[:size]).hexdigest() != digest:
            return {
                "name": "session_log_rewritten",
                "detail": f"{relative}: its earlier {size} bytes were rewritten",
            }
        if len(data) == size and grew_expected and None not in (ctime, old) and ctime != old:
            return {
                "name": "session_log_touched",
                "detail": f"{relative}: written without growing (ctime changed)",
            }
        return None


class WorkflowRunner(BaseAgent):
    """Runs the arm's workflow on its native agent, one turn at a time (module docstring)."""

    options_model = WorkflowRunnerOptions

    @staticmethod
    def name() -> str:
        return RUNNER_NAME

    @property
    def runner_options(self) -> WorkflowRunnerOptions:
        return cast(WorkflowRunnerOptions, self.options)

    def version(self) -> str | None:
        """The native agent's pinned CLI version (what the turns run)."""
        pinned = self.runner_options.inner_agent.kwargs.get("version")
        return str(pinned) if pinned is not None else None

    instructions_text: str | None = None
    package_stage: PackageStage | None = None
    invalid: dict[str, Any] | None = None

    def _invalid(self, invalid: TrialInvalid) -> None:
        """The instrument failed: recorded as an integrity finding (W11), never an agent outcome."""
        name = f"package_{invalid.kind}"
        self.invalid = {"kind": invalid.kind, "detail": invalid.detail, "record": invalid.record}
        self.integrity.append({"name": name, "detail": invalid.detail, "turn": "packages"})

    def _inner(self, logs_dir: Path) -> BaseAgent:
        spec = self.runner_options.inner_agent
        if self.instructions_text:
            spec = spec.model_copy(
                update={"kwargs": {**spec.kwargs, "instructions": self.instructions_text}}
            )
        return create_inner(self, spec, logs_dir, model_name=self.model_name)

    async def setup(self, environment: BaseEnvironment) -> None:
        await self._inner(self.logs_dir).setup(environment)

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        state = Run()
        options = self.runner_options
        self.watch = SessionWatch()
        self.integrity: list[dict[str, str]] = []
        self.invalid = None
        package = PackageStage(
            options.definition,
            locked=options.locked_packages,
            pack=options.pack,
            skills_placement=options.skills_placement,
        )
        self.package_stage = package
        self.staging = Path(tempfile.mkdtemp(prefix="raidar-workflow-"))
        timeout = options.phase_timeout_s
        deadline = None if timeout is None else time.monotonic() + timeout
        cut: str | None = None
        checkpoint: Checkpoint | None = None
        try:
            package.load()
            self.instructions_text = package.instructions_text
            store = native_session_store(self._inner(self.logs_dir))
            paths = tuple(f"{self.environment_logs_dir}/{name}" for name in store)
            checkpoint = Checkpoint(
                environment, self.staging, instruction, deadline, session_paths=paths
            )
            await self._work(checkpoint, state, package)
        except asyncio.CancelledError:
            cut = DEADLINE
            raise
        except TrialInvalid as invalid:
            cut = INVALID
            self._invalid(invalid)
        except Exception:
            cut = TURN_ERROR
            raise
        finally:
            if checkpoint is not None:
                await asyncio.shield(self._close(checkpoint, package, cut))
            await asyncio.shield(self._lock_logs(environment))
            self._finish(context, state, package)

    async def _lock_logs(self, environment: BaseEnvironment) -> None:
        """A non-mounted ``/logs/agent`` is downloaded by Harbor after the run, over what the
        runner published. The agent's processes were reaped, so each published directory in the
        container is recreated root-owned and empty: nothing the agent left there can overwrite a
        published record."""
        if environment.capabilities.mounted:
            return
        names = (WORKFLOW_DIR, *EVIDENCE_DIRS)
        targets = " ".join(shlex.quote(f"{self.environment_logs_dir}/{name}") for name in names)
        command = (
            f"rm -rf {targets} && mkdir {targets} "
            f"&& chown root:root {targets} && chmod 755 {targets}"
        )
        with suppress(Exception):
            await calls.trusted_exec(environment, command, user="root")

    async def _work(self, checkpoint: Checkpoint, state: Run, package: PackageStage) -> None:
        """Prepare the package runtime, run the one prompt, then resume while its checks ask."""
        await package.prepare(checkpoint)
        await self._turn(checkpoint, state, package, FIRST_BUILD, package.prompt, resume=False)
        while True:
            resume = await package.check(checkpoint, state.turns[-1].log_dir)
            await self._rewatch(checkpoint, state.turns[-1])
            if resume is None:
                break
            await self._turn(checkpoint, state, package, resume.kind, resume.message, resume=True)

    async def _rewatch(self, checkpoint: Checkpoint, turn: Turn) -> None:
        """The checks ran the agent's own code after the turn (its pytest and conftest): the
        native session store must still be exactly as the turn left it."""
        staged = self.staging / turn.log_dir
        environment = checkpoint.environment
        try:
            links = await self._copy_turn_logs(environment, turn, staged)
            self._note_links(turn, links, f"{turn.log_dir} (after the checks)")
        except Exception as exc:  # noqa: BLE001 - recorded on the turn
            turn.settle_error = f"{type(exc).__name__}: {exc}"
            return
        ctimes = await self._remote_ctimes(environment, turn, f"{turn.log_dir} (after the checks)")
        found = self.watch.observe(staged, ctimes, turn.session_store, grew_expected=True)
        turn.integrity += found
        self.integrity += [{**item, "turn": f"{turn.log_dir} (after the checks)"} for item in found]

    async def _remote_ctimes(
        self, environment: BaseEnvironment, turn: Turn, where: str
    ) -> dict[str, int] | None:
        """Each session-store file's ``ctime`` in the container, read by the agent's user with the
        standard library only (the agent's user cannot set a ``ctime`` back). A read that fails is
        a named ``session_metadata_missing`` violation: without it a write and restore of the
        session log could not be seen, and that is never silently skipped."""
        store = turn.session_store
        if not store:
            return {}
        command = shlex.join(
            ["python3", "-I", "-B", "-c", CTIME_SCRIPT, str(self.environment_logs_dir), *store]
        )
        try:
            result = await calls.trusted_exec(environment, command)
            found = json.loads(result.stdout or "")
        except (ValueError, OSError):
            found = None
        if not isinstance(found, dict):
            detail = "the session store's ctimes could not be read in the container"
            item = {"name": "session_metadata_missing", "detail": detail}
            turn.integrity.append(item)
            self.integrity.append({**item, "turn": where})
            return None
        return {str(k): int(v) for k, v in found.items()}

    async def _close(self, checkpoint: Checkpoint, package: PackageStage, cut: str | None) -> None:
        try:
            await package.close(checkpoint, cut)
        except TrialInvalid as invalid:
            self._invalid(invalid)

    async def _turn(
        self,
        checkpoint: Checkpoint,
        state: Run,
        package: PackageStage,
        kind: str,
        prompt: str,
        *,
        resume: bool,
    ) -> None:
        environment = checkpoint.environment
        prompt = package.decorate(kind, prompt)
        turn = Turn(
            stage=BUILD,
            kind=kind,
            index=len(state.turns) + 1,
            resumed=resume,
            prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            started_at=now(),
            started=time.monotonic(),
        )
        state.turns.append(turn)
        turn_dir = self.logs_dir / turn.log_dir
        turn_dir.mkdir(parents=True, exist_ok=True)
        staged = self.staging / turn.log_dir
        staged.mkdir(parents=True, exist_ok=True)
        (staged / PROMPT_FILE).write_text(prompt, encoding="utf-8")
        inner = self._inner(turn_dir)
        context = AgentContext()
        send = inner.resume if resume else inner.run
        try:
            await send(prompt, environment, context)
        except asyncio.CancelledError:
            turn.stop("CancelledError: the agent phase was cancelled (agent timeout)")
            await asyncio.shield(self._settle(environment, state, turn, inner, context))
            raise
        except Exception as exc:
            turn.stop(f"{type(exc).__name__}: {exc}")
            await self._settle(environment, state, turn, inner, context)
            raise
        turn.stop()
        checkpoint.turns.append(Spent(kind, turn.seconds or 0.0))
        await self._settle(environment, state, turn, inner, context)

    async def _settle(
        self,
        environment: BaseEnvironment,
        state: Run,
        turn: Turn,
        inner: BaseAgent,
        context: AgentContext,
    ) -> None:
        """Copy the turn's logs into its directory and read its isolation, session and usage."""
        native_observe(turn, inner)
        turn_dir = self.staging / turn.log_dir
        try:
            links = await self._copy_turn_logs(environment, turn, turn_dir)
            self._note_links(turn, links, turn.log_dir)
        except Exception as exc:  # noqa: BLE001 - recorded on the turn; usage stays unknown
            turn.settle_error = f"{type(exc).__name__}: {exc}"
            return
        if context.is_empty():
            original, inner.logs_dir = inner.logs_dir, turn_dir  # parse the sanitized copy only
            try:
                inner.populate_context_post_run(context)
            finally:
                inner.logs_dir = original
        turn.session_id = session_id(turn_dir)
        native_account(turn, state, inner, context)
        native_audit(turn, inner, turn_dir)
        ctimes = await self._remote_ctimes(environment, turn, turn.log_dir)
        seen = self.watch.observe(
            turn_dir, ctimes, turn.session_store, grew_expected=turn.error is None
        )
        turn.integrity += seen
        self.integrity += [{**item, "turn": turn.log_dir} for item in seen]
        text = native_last_message(inner, turn_dir)
        if text is not None:
            (turn_dir / LAST_MESSAGE_FILE).write_text(text, encoding="utf-8")

    def _note_links(self, turn: Turn, links: list[str], where: str) -> None:
        for path in links:
            item = {"name": "source_symlink", "detail": f"{path}: a link in the agent's logs"}
            turn.integrity.append(item)
            self.integrity.append({**item, "turn": where})

    async def _copy_turn_logs(
        self, environment: BaseEnvironment, turn: Turn, staged: Path
    ) -> list[str]:
        """Keep the turn's logs host-side, outside the mounted ``/logs/agent`` the agent writes.

        The copy is made by the host and leaves out every link (an agent can plant one that points
        at a host file, and a parser would read that file): the paths of the links left out are
        returned, for a named ``source_symlink`` violation.
        """
        links: list[str] = []

        def ignore(directory: str, names: list[str]) -> list[str]:
            skipped = [n for n in names if Path(directory, n).is_symlink()]
            links.extend(str(Path(directory, n)) for n in skipped)
            return skipped

        source = self.logs_dir
        if not environment.capabilities.mounted:
            source = Path(tempfile.mkdtemp(prefix="logs-", dir=self.staging))
            await environment.download_dir(str(self.environment_logs_dir), source)
        for entry in sorted(source.iterdir()):
            if entry.name == WORKFLOW_DIR or entry.name.startswith(STEP_RECORD_PREFIX):
                continue
            if entry.is_symlink():
                links.append(str(entry))
            elif entry.is_dir():
                shutil.copytree(entry, staged / entry.name, ignore=ignore, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, staged / entry.name)
        own = self.logs_dir / turn.log_dir  # what the native agent itself wrote, host side
        if own.is_dir() and not own.is_symlink():
            shutil.copytree(own, staged, ignore=ignore, dirs_exist_ok=True)
        return links

    def _record(self, state: Run, package: PackageStage) -> dict[str, Any]:
        """``workflow.json``: the turns, the phase's tokens, the components and each primitive's
        host-written evidence (keyed by contract id), with staging paths made portable."""
        usage = state.usage()
        record: dict[str, Any] = {
            "kind": RECORD_KIND,
            "workflow": self.runner_options.workflow,
            "stages": [BUILD],
            "turns": [turn.record() for turn in state.turns],
            "tokens": usage.record() if usage else None,
            "components": list(package.components),
            "integrity": self.integrity,
            **package.package_record(),
        }
        if self.invalid is not None:
            record["invalid"] = self.invalid
        portable: dict[str, Any] = self._portable(record)
        return portable

    def _portable(self, value: Any) -> Any:
        """``value`` with each ``host_path`` that names a staging file rewritten to its path below
        the step's agent logs: staging is deleted at publication, and a recorded path must lead to
        the published copy, never outside it."""
        if isinstance(value, dict):
            return {
                key: self._published_path(item)
                if key == HOST_PATH and isinstance(item, str)
                else self._portable(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._portable(item) for item in value]
        return value

    def _published_path(self, text: str) -> str:
        normal = Path(os.path.normpath(text))
        staging = Path(os.path.normpath(self.staging))
        if not normal.is_absolute() or normal == staging or not normal.is_relative_to(staging):
            return text
        return normal.relative_to(staging).as_posix()

    def _finish(self, context: AgentContext, state: Run, package: PackageStage) -> None:
        usage = state.usage()
        if usage is not None:
            context.n_input_tokens = usage.input
            context.n_cache_tokens = usage.cached
            context.n_output_tokens = usage.output
            context.cost_usd = usage.cost_usd
        record = self._record(state, package)
        context.metadata = {**(context.metadata or {}), "workflow": record}
        workflow_dir = self.staging / WORKFLOW_DIR
        workflow_dir.mkdir(parents=True, exist_ok=True)
        (workflow_dir / RECORD_FILE).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        last = state.turns[-1] if state.turns else None
        trajectory = self.staging / last.log_dir / TRAJECTORY_FILE if last else None
        self._publish(workflow_dir, trajectory)

    def _publish(self, workflow_dir: Path, trajectory: Path | None) -> None:
        """Put the host-kept records and evidence in the step's agent logs once the agent's
        processes were reaped (the runtime's ``close``): what the agent wrote under the published
        names during the run is replaced."""
        target = self.logs_dir / WORKFLOW_DIR
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(workflow_dir, target, symlinks=True)
        for name in EVIDENCE_DIRS:
            kept = self.staging / name
            published = self.logs_dir / name
            shutil.rmtree(published, ignore_errors=True)
            if kept.is_dir() and not kept.is_symlink():
                shutil.copytree(kept, published, symlinks=True)
        if trajectory is not None and trajectory.is_file():
            (self.logs_dir / TRAJECTORY_FILE).write_bytes(trajectory.read_bytes())
        shutil.rmtree(self.staging, ignore_errors=True)
