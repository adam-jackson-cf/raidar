"""The workflow runner: the Harbor agent of every real arm (design section 5.5).

Harbor runs one agent phase per checkpoint (one ``run`` per step), so the reaper, the liveness
monitor and the per-step reset see the same phases they saw for the native agent. Inside the
phase the runner drives the native agent (``inner_agent``, built by ``create_inner``) through
the workflow's enabled stages (``raidar.subject.workflow.stage``), host-side, one *turn* at a
time, all in the checkpoint's one primary session:

1. every enabled stage prepares (before the agent starts);
2. one prompt (``compose``: build's frame, each stage's fragment at its token, the task text at
   ``%%%TASK%%%``) runs as the first turn, in a fresh session (``run``);
3. when the agent stops, each enabled stage's checks run in stage order; the first that returns
   a ``Resume`` resumes the same session with its message (``resume``, a turn of that stage and
   of the ``Resume``'s kind: ``gate_fix``, ``rework``, ``repair`` or ``define``; the first prompt
   is ``first_build``) and the checks run again. Each stage bounds its own resumes; when none
   asks, the checkpoint ends. A stage may add to a turn's prompt (``Stage.decorate``: the build
   reply's shape); each finished turn's kind and seconds is on ``Checkpoint.turns``, and
   ``Checkpoint.deadline`` is when the phase is cut (``phase_timeout_s`` from the phase start:
   the arm's agent timeout as Harbor scales it), so a stage can decline a turn it could not finish;
4. every stage closes, also when a turn failed (``turn_error``) or the phase was cancelled
   (``deadline``), and its record goes into ``workflow.json`` under its name.

Each checkpoint starts a fresh session because Harbor empties ``/logs/agent`` before each step.
The native-agent protocol owns isolation, process facts, usage normalization, final replies,
resume storage and session auditing. The runner copies logs without moving the native store,
records normalized per-turn deltas and session totals, and writes ``last-message.txt`` for
stage consumers. Unknown usage remains unknown rather than a partial phase total.

**Evidence stays host-side.** ``/logs/agent`` is mounted and the agent's user can write it, so
the stages' records (``Checkpoint.logs_dir``) are kept in a host staging directory outside it,
each turn's logs are copied by the host (links kept as links) after the turn, and everything is
published into the step's agent logs (replacing whatever the agent put under ``workflow/``) only
after the stages closed and reaped the agent's processes. ``SessionWatch`` records a named
``integrity`` entry when an earlier copy of the native session store was rewritten.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shlex
import shutil
import tempfile
import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from harbor.agents.base import BaseAgent
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from raidar.subject.agent.protocol import ZERO, NativeAgent, Tokens
from raidar.subject.workflow.components import skill_files
from raidar.subject.workflow.definition import StageType, WorkflowDefinition
from raidar.subject.workflow.factory import workflow_stages
from raidar.subject.workflow.inner import InnerAgentSpec, create_inner
from raidar.subject.workflow.stage import (
    DEADLINE,
    FIRST_BUILD,
    TURN_ERROR,
    Checkpoint,
    Resume,
    Spent,
    Stage,
    compose,
)

RUNNER_NAME = "raidar-workflow"
WORKFLOW_DIR = "workflow"
RECORD_FILE = "workflow.json"
RECORD_KIND = "raidar-workflow"
PROMPT_FILE = "prompt.txt"
TRAJECTORY_FILE = "trajectory.json"
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


class WorkflowRunnerOptions(AgentOptions):
    inner_agent: InnerAgentSpec
    workflow: str
    definition: WorkflowDefinition
    skills_placement: str = ".agents/skills"
    phase_timeout_s: float | None = None


@dataclass
class Turn:
    stage: StageType
    kind: str
    index: int
    resumed: bool
    prompt_sha256: str
    started_at: str
    started: float
    finished_at: str | None = None
    seconds: float | None = None
    error: str | None = None
    settle_error: str | None = None
    session_id: str | None = None
    session_total: Tokens | None = None
    tokens: Tokens | None = None
    harness_isolation: dict[str, Any] | None = None
    agent_process: dict[str, str | None] | None = None
    session_audit: dict[str, Any] | None = None
    session_store: tuple[str, ...] = ()
    integrity: list[dict[str, str]] = field(default_factory=list)

    @property
    def log_dir(self) -> str:
        return f"{WORKFLOW_DIR}/{self.stage}-{self.index}"

    def stop(self, error: str | None = None) -> None:
        self.finished_at = _now()
        self.seconds = round(time.monotonic() - self.started, 3)
        self.error = error

    def record(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "kind": self.kind,
            "turn": self.index,
            "log_dir": self.log_dir,
            "session_id": self.session_id,
            "resumed": self.resumed,
            "prompt_sha256": self.prompt_sha256,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "seconds": self.seconds,
            "error": self.error,
            "settle_error": self.settle_error,
            "session_total": self.session_total.record() if self.session_total else None,
            "tokens": self.tokens.record() if self.tokens else None,
            "harness_isolation": self.harness_isolation,
            "agent_process": self.agent_process,
            "session_audit": self.session_audit,
            "session_store": list(self.session_store),
            "integrity": self.integrity,
        }


@dataclass
class _Run:
    turns: list[Turn] = field(default_factory=list)

    def previous(self, turn: Turn) -> Tokens | None:
        """The preceding normalized total of this session, if one was measured."""
        return next(
            (
                item.session_total
                for item in reversed(self.turns)
                if item is not turn and item.session_id == turn.session_id
            ),
            None,
        )

    def usage(self) -> Tokens | None:
        """The phase's tokens (sum of turn deltas); ``None`` when any turn's is unknown."""
        total = ZERO
        for turn in self.turns:
            if turn.tokens is None:
                return None
            total = total.plus(turn.tokens)
        return total if self.turns else None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _session_id(turn_dir: Path) -> str | None:
    path = turn_dir / TRAJECTORY_FILE
    try:
        value = json.loads(path.read_text()).get("session_id")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and value else None


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

    def _inner(self, logs_dir: Path) -> BaseAgent:
        return create_inner(
            self, self.runner_options.inner_agent, logs_dir, model_name=self.model_name
        )

    async def setup(self, environment: BaseEnvironment) -> None:
        await self._inner(self.logs_dir).setup(environment)

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        state = _Run()
        stages = workflow_stages(self.runner_options.definition)
        self.components: list[dict[str, str]] = []
        self.watch = SessionWatch()
        self.integrity: list[dict[str, str]] = []
        self.staging = Path(tempfile.mkdtemp(prefix="raidar-workflow-"))
        timeout = self.runner_options.phase_timeout_s
        deadline = None if timeout is None else time.monotonic() + timeout
        store = cast(NativeAgent, self._inner(self.logs_dir)).session_store()
        paths = tuple(f"{self.environment_logs_dir}/{name}" for name in store)
        checkpoint = Checkpoint(
            environment, self.staging, instruction, deadline, session_paths=paths
        )
        cut: str | None = None
        try:
            await self._work(checkpoint, state, stages, instruction)
        except asyncio.CancelledError:
            cut = DEADLINE
            raise
        except Exception:
            cut = TURN_ERROR
            raise
        finally:
            await asyncio.shield(self._close(checkpoint, stages, cut))
            await asyncio.shield(self._lock_logs(environment))
            self._finish(context, state, stages)

    async def _lock_logs(self, environment: BaseEnvironment) -> None:
        """A non-mounted ``/logs/agent`` is downloaded by Harbor after the run, over what the
        runner published. The agent's processes were reaped, so the records' directory in the
        container is recreated root-owned and empty: nothing the agent left there can overwrite a
        published record."""
        if environment.capabilities.mounted:
            return
        target = shlex.quote(f"{self.environment_logs_dir}/{WORKFLOW_DIR}")
        command = (
            f"rm -rf {target} && mkdir {target} && chown root:root {target} && chmod 755 {target}"  # noqa: E501
        )
        with suppress(Exception):
            await environment.exec(command, user="root")

    async def _work(
        self, checkpoint: Checkpoint, state: _Run, stages: list[Stage], instruction: str
    ) -> None:
        """Prepare every stage, run the one prompt, then resume while a stage's checks ask."""
        await self._place_components(checkpoint)
        for stage in stages:
            await stage.prepare(checkpoint)
        prompt = compose(stages, instruction)
        await self._turn(checkpoint, state, stages, "build", FIRST_BUILD, prompt, resume=False)
        while True:
            followup = await self._checks(checkpoint, stages, state.turns[-1])
            await self._rewatch(checkpoint, state.turns[-1])
            if followup is None:
                break
            stage_type, resume = followup
            await self._turn(
                checkpoint, state, stages, stage_type, resume.kind, resume.message, resume=True
            )

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
            result = await environment.exec(command)
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

    async def _place_components(self, checkpoint: Checkpoint) -> None:
        files = skill_files(self.runner_options.definition, self.runner_options.skills_placement)
        for relative, text in files.items():
            record = f"{WORKFLOW_DIR}/components/{relative}"
            path = checkpoint.logs_dir / record
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            await checkpoint.environment.upload_file(path, f"/app/{relative}")
            self.components.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "record": record,
                }
            )

    @staticmethod
    async def _checks(
        checkpoint: Checkpoint, stages: list[Stage], turn: Turn
    ) -> tuple[StageType, Resume] | None:
        """The first stage, in stage order, whose checks resume the session, and its ``Resume``."""
        for stage in stages:
            resume = await stage.check(checkpoint, turn.log_dir)
            if resume is not None:
                return stage.name, resume
        return None

    @staticmethod
    async def _close(checkpoint: Checkpoint, stages: list[Stage], cut: str | None) -> None:
        for stage in stages:
            await stage.close(checkpoint, cut)

    async def _turn(
        self,
        checkpoint: Checkpoint,
        state: _Run,
        stages: list[Stage],
        stage: StageType,
        kind: str,
        prompt: str,
        *,
        resume: bool,
    ) -> None:
        environment = checkpoint.environment
        for owner in stages:
            prompt = owner.decorate(kind, prompt)
        turn = Turn(
            stage=stage,
            kind=kind,
            index=len(state.turns) + 1,
            resumed=resume,
            prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            started_at=_now(),
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
        state: _Run,
        turn: Turn,
        inner: BaseAgent,
        context: AgentContext,
    ) -> None:
        """Copy the turn's logs into its directory and read its isolation, session and usage."""
        native = cast(NativeAgent, inner)
        turn.harness_isolation = native.harness_isolation
        turn.agent_process = native.process_facts()
        turn.session_store = native.session_store()
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
        turn.session_id = _session_id(turn_dir)
        previous = state.previous(turn)
        if not turn.resumed or previous is not None:
            turn.tokens, turn.session_total = native.accounting(context, previous)
        turn.session_audit = native.session_audit(turn_dir)
        ctimes = await self._remote_ctimes(environment, turn, turn.log_dir)
        seen = self.watch.observe(
            turn_dir, ctimes, turn.session_store, grew_expected=turn.error is None
        )
        turn.integrity += seen
        self.integrity += [{**item, "turn": turn.log_dir} for item in seen]
        text = native.last_message(turn_dir)
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

    def _record(self, state: _Run, stages: list[Stage], usage: Tokens | None) -> dict[str, Any]:
        """``workflow.json``: the stages, the turns, the phase's tokens and each stage's record."""
        record: dict[str, Any] = {
            "kind": RECORD_KIND,
            "workflow": self.runner_options.workflow,
            "stages": [stage.name for stage in stages],
            "instructions": [path for stage in stages for path in stage.instructions],
            "turns": [turn.record() for turn in state.turns],
            "tokens": usage.record() if usage else None,
            "components": self.components,
            "integrity": self.integrity,
        }
        outputs = {stage.name: stage.record() for stage in stages}
        return {**record, **{name: value for name, value in outputs.items() if value is not None}}

    def _finish(self, context: AgentContext, state: _Run, stages: list[Stage]) -> None:
        usage = state.usage()
        if usage is not None:
            context.n_input_tokens = usage.input
            context.n_cache_tokens = usage.cached
            context.n_output_tokens = usage.output
            context.cost_usd = usage.cost_usd
        record = self._record(state, stages, usage)
        context.metadata = {**(context.metadata or {}), "workflow": record}
        workflow_dir = self.staging / WORKFLOW_DIR
        workflow_dir.mkdir(parents=True, exist_ok=True)
        (workflow_dir / RECORD_FILE).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        last = state.turns[-1] if state.turns else None
        trajectory = self.staging / last.log_dir / TRAJECTORY_FILE if last else None
        self._publish(workflow_dir, trajectory)

    def _publish(self, workflow_dir: Path, trajectory: Path | None) -> None:
        """Put the host-kept records in the step's agent logs once the agent's processes were
        reaped (the stages' ``close``): what the agent wrote there during the run is replaced."""
        target = self.logs_dir / WORKFLOW_DIR
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(workflow_dir, target, symlinks=True)
        if trajectory is not None and trajectory.is_file():
            (self.logs_dir / TRAJECTORY_FILE).write_bytes(trajectory.read_bytes())
        shutil.rmtree(self.staging, ignore_errors=True)
