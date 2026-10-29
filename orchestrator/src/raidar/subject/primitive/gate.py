"""``gate/1``: package-contributed checks the agent commits through, sealed record-only.

A package's ``gate`` entries become checks of one gate per checkpoint: free names (a conventional
command runs with its argv unmodified; the reserved ``pytest`` kind records every collected test's
outcome and passes only with at least one collected test and none failed), ``before``
prerequisites that run first and prevent every other check while they refuse, and a ``watch``
directory of stage state the gate proves it left alone. The checks run in the trusted gate payload
(:mod:`raidar.subject.primitive.gate_tool`) as the agent's user, never root: from the agent's own
``git commit`` (the hook; the agent can write its log, so what it records is a reading) and at the
seal, the authoritative run after
each turn. The seal only records the verdict, the work tree it judged and the workspace digest: it
never stages, commits, restores or repairs the agent's work. With ``max_fix_rounds`` above zero the
workspace is snapshotted host-side before each fix turn (the pre-fix tree).

**Attribution.** A gate error is judged against the gate's own run over the starting workspace
(the *canary*): a canary that cannot run invalidates the trial (instrument). After a passing
canary, an agent's check that exits 126 or 127, is killed or exceeds its timeout is the agent's
outcome (``agent_error``), never invalidity; a check whose tool cannot start, a changed installed
tool tree, a missing record or a failed container exec is an integrity failure.

Every container command goes through :mod:`raidar.subject.primitive.calls`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shlex
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from raidar.contract.snapshot import SNAPSHOT_EXCLUDES, tree_digest
from raidar.subject.package.manifest import GateCheck, Output
from raidar.subject.primitive import calls, gate_tool
from raidar.subject.primitive.gate_outputs import CheckOutputs

if TYPE_CHECKING:
    from harbor.environments.base import BaseEnvironment

    from raidar.subject.primitive.install import InstalledPackage

PASSED, REFUSED, ERROR = gate_tool.PASSED, gate_tool.REFUSED, gate_tool.ERROR
RECORD_DIR = "gate"
OUTPUTS_DIR = "check-outputs"
PAYLOAD = Path(gate_tool.__file__)
CTIME_SCRIPT = "import os, sys; print(os.stat(sys.argv[1]).st_ctime_ns)"
CONTEXT_SUBDIR = "context"
INSTRUMENT_CAUSES = frozenset({"unrunnable", "isolation", "tool_changed"})
"""Check causes that are never the agent's: the tool or its confinement, not its work."""


class Attribution(StrEnum):
    """Who a sealed gate record is attributed to."""

    PASSED = "passed"
    REFUSED = "refused"
    AGENT_ERROR = "agent_error"
    """A check the agent's code broke (exit 126/127, killed, timeout) after a passing canary."""
    INSTRUMENT = "instrument_invalid"
    INTEGRITY = "integrity_invalid"


class GateInvalid(RuntimeError):
    """The trial cannot be scored: the instrument failed or its integrity is in doubt."""

    def __init__(self, category: Literal["instrument", "integrity"], message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class GatePaths:
    """Where the gate lives in the container (root-owned) and keeps its state."""

    workspace: str = "/app"
    home: str = "/opt/raidar/gate"
    state: str = "/var/lib/raidar-gate"
    system_python: str = "/usr/local/bin/python3"
    """The pack interpreter that runs the payload (standard library only)."""
    reap: bool = True
    """Kill the agent user's leftover processes before a seal (off where that user is a test's)."""
    trusted_root: str = calls.TRUSTED_ROOT
    """Where the root-owned call supervisor lives (installed by ``calls.ensure_trusted``)."""
    confine: bool = True
    """Seccomp (no sockets, io_uring) for every check; off only where a test host cannot."""
    tool_owner: int = 0
    """The uid that must own installed tool trees (root; a test host's own user in tests)."""

    @property
    def payload(self) -> str:
        return f"{self.home}/{PAYLOAD.name}"

    @property
    def config(self) -> str:
        return f"{self.home}/gate.json"

    @property
    def hooks(self) -> str:
        return f"{self.home}/hooks"

    @property
    def empty_hooks(self) -> str:
        return f"{self.home}/nohooks"

    @property
    def contexts(self) -> str:
        return f"{self.home}/{CONTEXT_SUBDIR}"

    @property
    def hook_state(self) -> str:
        return f"{self.state}/hook"

    @property
    def scratch(self) -> str:
        return f"{self.state}/scratch"

    @property
    def command(self) -> list[str]:
        """The payload as the agent's user runs it."""
        return [self.system_python, "-B", self.payload]


@dataclass(frozen=True)
class GatePackage:
    """One enabled package's gate: its checks, installed tool and the context they read."""

    name: str
    """The package's leaf name."""
    installed: InstalledPackage
    checks: Sequence[GateCheck]
    context: Mapping[str, Any] = field(default_factory=dict)
    """The JSON the checks read through ``RAIDAR_STAGE_CONTEXT`` (written root-owned)."""
    closed_flag: str | None = None
    """Root-owned flag Raidar writes when the package's stop closes: its ``before`` checks fail."""
    outputs: Mapping[str, Output] = field(default_factory=dict)
    """The package's declared outputs: each check's run is followed by a capture of them."""


@dataclass(frozen=True)
class GateSettings:
    max_fix_rounds: int = 0


@dataclass(frozen=True)
class AgentFacts:
    """Who the gate's user is and who owns the trusted files (read in the container)."""

    owner: str
    trusted_uid: int


@dataclass(frozen=True)
class GateOutcome:
    """A sealed gate record and who it is attributed to."""

    attribution: Attribution
    record: dict[str, Any]
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.attribution is Attribution.PASSED

    @property
    def resumable(self) -> bool:
        """The agent's tree did not pass: a fix turn may follow."""
        return self.attribution in (Attribution.REFUSED, Attribution.AGENT_ERROR)


def check_argv(package: GatePackage, check: GateCheck) -> list[str]:
    """A check's argv: ``run`` unmodified, its first word expanded when it names a tool command."""
    run = list(check.run or ())
    commands = package.installed.commands
    if check.kind != "pytest" and run and run[0] in commands:
        return [*commands[run[0]], *run[1:]]
    return run


def context_path(paths: GatePaths, package: GatePackage) -> str:
    return f"{paths.contexts}/{package.name}.json"


def _check_entries(paths: GatePaths, packages: Sequence[GatePackage]) -> list[dict[str, Any]]:
    return [
        {
            "name": f"{package.name}.{check.name}",
            "kind": check.kind,
            "argv": check_argv(package, check),
            "before": check.before,
            "closed_flag": package.closed_flag,
            "timeout_s": check.timeout_s,
            "path": package.installed.path,
            "python": package.installed.python,
            "context": context_path(paths, package),
            "verify": [
                {
                    "root": package.installed.root,
                    "digest": package.installed.digest,
                    "limits": {**calls.TREE_LIMITS, "owner": paths.tool_owner},
                }
            ],
            "state": check.watch,
            "outputs": {name: output.path for name, output in package.outputs.items()},
            "state_limits": dict(calls.STATE_LIMITS),
        }
        for package in packages
        for check in package.checks
    ]


def watched_state(packages: Sequence[GatePackage]) -> list[dict[str, Any]]:
    """State directories the gate watches, as payload ``prerequisites`` that run nothing."""
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for package in packages:
        for check in package.checks:
            if check.watch:
                found[package.name, check.watch] = {
                    "name": f"{package.name}.watch", "argv": [], "watch": check.watch,
                    "watch_only": True,
                }  # fmt: skip
    return list(found.values())


def gate_config(
    paths: GatePaths,
    packages: Sequence[GatePackage],
    facts: AgentFacts,
    *,
    hook_sha256: str,
    session_paths: Sequence[str] = (),
) -> dict[str, Any]:
    """The payload's one configuration file."""
    return {
        "root": paths.workspace,
        "trusted_root": paths.trusted_root,
        "confine": paths.confine,
        "home": paths.home,
        "python": paths.system_python,
        "excludes": list(SNAPSHOT_EXCLUDES),
        "package_checks": _check_entries(paths, packages),
        "prerequisites": watched_state(packages),
        "hook_state": paths.hook_state,
        "scratch": paths.scratch,
        "empty_hooks": paths.empty_hooks,
        "hooks": paths.hooks,
        "session_paths": list(session_paths),
        "hook_sha256": hook_sha256,
        "payload_sha256": hashlib.sha256(PAYLOAD.read_bytes()).hexdigest(),
        "payload_name": PAYLOAD.name,
        "trusted_uid": facts.trusted_uid,
    }


def last_json(text: str) -> dict[str, Any] | None:
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _errored_checks(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [check for check in record.get("checks") or [] if check.get("status") == ERROR]


def check_canary(record: Mapping[str, Any]) -> None:
    """The gate must be able to decide on the starting workspace (a refusal is not an error)."""
    if record.get("verdict") in (PASSED, REFUSED):
        return
    errored = _errored_checks(record)
    detail = "; ".join(f"{c.get('name')}: {c.get('cause')}" for c in errored)
    reason = detail or str(record.get("error") or "the canary printed no verdict")
    raise GateInvalid("instrument", f"the gate's canary failed on the starting workspace: {reason}")


def _errored_attribution(record: Mapping[str, Any]) -> tuple[Attribution, str]:
    """A record that neither passed nor refused: who the errors belong to."""
    errored = _errored_checks(record)
    if not errored:
        return Attribution.INTEGRITY, str(record.get("error") or "the gate printed no record")
    broken = [c for c in errored if c.get("cause") in INSTRUMENT_CAUSES]
    if broken:
        names = ", ".join(f"{c.get('name')} ({c.get('cause')})" for c in broken)
        return Attribution.INTEGRITY, f"a check could not be trusted or started: {names}"
    return Attribution.AGENT_ERROR, "; ".join(f"{c.get('name')}: {c.get('cause')}" for c in errored)


def attribute(record: Mapping[str, Any], *, tools_intact: bool = True) -> tuple[Attribution, str]:
    """Attribute a sealed record, after a passing canary.

    Errors of the agent's code (exit 126/127, a signal, a timeout) are the agent's; a check whose
    tool cannot start, changed tool bytes or no usable record are integrity failures.
    """
    verdict = record.get("verdict")
    if not tools_intact:
        return Attribution.INTEGRITY, "the installed tool tree changed"
    if verdict == PASSED:
        return Attribution.PASSED, ""
    if verdict == REFUSED:
        return Attribution.REFUSED, ""
    return _errored_attribution(record)


@dataclass
class HookLog:
    """The hook's run log as copied host-side. The agent's user writes it: what it holds, and
    what its findings say, are readings."""

    seen: str = ""
    ctime: str | None = None
    integrity_seen: int = 0

    def observe(self, text: str, ctime: str | None, after: str) -> list[dict[str, str]]:
        """Readings for a rewritten or silently touched log, and the hook's own findings."""
        found: list[dict[str, str]] = []
        if self.seen and not text.startswith(self.seen):
            found.append(
                {
                    "name": "hook_log_rewritten", "after": after,
                    "detail": "the hook's run log no longer begins with the lines copied earlier",
                }
            )  # fmt: skip
        elif text == self.seen and self.ctime and ctime and ctime != self.ctime:
            found.append(
                {
                    "name": "hook_log_touched", "after": after,
                    "detail": "the hook's run log was written without changing its content",
                }
            )  # fmt: skip
        elif len(text) >= len(self.seen):
            self.seen = text
        self.ctime = ctime
        return found + self._hook_findings(text, after)

    def _hook_findings(self, text: str, after: str) -> list[dict[str, str]]:
        lines = [line for line in text.splitlines() if line.strip()]
        found: list[dict[str, str]] = []
        for line in lines[self.integrity_seen :]:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            readings = record.get("integrity", []) if isinstance(record, dict) else []
            found.extend(
                {**item, "after": f"{item.get('after', '')} ({after})"} for item in readings
            )
        self.integrity_seen = len(lines)
        return found


@dataclass
class GateEvidence:
    """What one checkpoint's gate observed, host-side (the body of ``GateRun.record``)."""

    canary: dict[str, Any] | None = None
    setup_check: dict[str, Any] | None = None
    gates: list[dict[str, Any]] = field(default_factory=list)
    pre_fix: list[dict[str, Any]] = field(default_factory=list)
    integrity: list[dict[str, str]] = field(default_factory=list)
    reaps: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _q(value: str) -> str:
    return shlex.quote(value)


class GateRun:
    """One checkpoint's gate: ``prepare`` before the agent, ``seal`` after each turn, ``close``."""

    def __init__(
        self,
        environment: BaseEnvironment,
        logs_dir: Path,
        packages: Sequence[GatePackage],
        settings: GateSettings | None = None,
        paths: GatePaths | None = None,
    ) -> None:
        self.environment = environment
        self.records = logs_dir / RECORD_DIR
        self.packages = list(packages)
        self.settings = settings or GateSettings()
        self.paths = paths or GatePaths()
        self.base: str | None = None
        self.evidence = GateEvidence()
        self.hook_log = HookLog()
        declared = {f"{p.name}.{c.name}": p.outputs for p in self.packages for c in p.checks}
        self.outputs = CheckOutputs(
            environment, self.paths.scratch, self.records / OUTPUTS_DIR, declared
        )

    # before the agent starts ---------------------------------------------------------------

    async def prepare(self, session_paths: Sequence[str] = ()) -> None:
        """Install the gate, check it and run the canary (``GateInvalid``)."""
        self.records.mkdir(parents=True, exist_ok=True)
        if self.paths.trusted_root == calls.TRUSTED_ROOT:
            await calls.ensure_trusted(self.environment)  # the supervisor every check runs under
        facts = await self._facts()
        command = shlex.join(self.paths.command)
        hook = f"#!/bin/sh\nexec {command} hook --config {self.paths.config}\n"
        configuration = gate_config(
            self.paths, self.packages, facts, hook_sha256=_sha256(hook), session_paths=session_paths
        )
        await self._install(facts, hook, configuration)
        found = last_json(await self._agent(shlex.join(self._argv("workspace-setup"))))
        if found is None or "base" not in found:
            raise GateInvalid("integrity", f"no base commit from the workspace setup: {found}")
        self.base = str(found["base"])
        await self._check_setup()
        await self._run_canary()
        await self._verify_tools("before the agent started")

    async def _facts(self) -> AgentFacts:
        owner = (await self._agent("id -un")).strip()
        trusted = int((await self._root("id -u")).strip())
        return AgentFacts(owner, trusted)

    async def _install(self, facts: AgentFacts, hook: str, configuration: dict[str, Any]) -> None:
        """Root's whole job: put the trusted files in place, set modes, make fresh state."""
        paths, environment = self.paths, self.environment
        text = json.dumps(configuration, indent=2, sort_keys=True) + "\n"
        staged = {"gate.json": text, "pre-commit": hook}
        for name, content in staged.items():
            (self.records / name).write_text(content, encoding="utf-8")
        await self._root(f"mkdir -p {_q(paths.hooks)} {_q(paths.empty_hooks)} {_q(paths.contexts)}")
        await calls.upload_file(environment, PAYLOAD, paths.payload)
        await calls.upload_file(environment, self.records / "gate.json", paths.config)
        hook_target = f"{paths.hooks}/pre-commit"
        await calls.upload_file(environment, self.records / "pre-commit", hook_target)
        for package in self.packages:
            kept = self.records / CONTEXT_SUBDIR / f"{package.name}.json"
            kept.parent.mkdir(parents=True, exist_ok=True)
            kept.write_text(json.dumps(package.context, sort_keys=True), encoding="utf-8")
            await calls.upload_file(environment, kept, context_path(paths, package))
        runs = _q(f"{paths.hook_state}/{gate_tool.HOOK_RUNS}")
        await self._root(
            "\n".join(
                [
                    "set -eu",
                    f'if [ "$(id -u)" = 0 ]; then chown -R root:root {_q(paths.home)}; fi',
                    f"chmod -R go-w {_q(paths.home)}",
                    f"chmod 755 {_q(paths.home)} {_q(paths.hooks)} {_q(paths.empty_hooks)} "
                    f"{_q(paths.contexts)} {_q(paths.hooks)}/pre-commit",
                    f"chmod 644 {_q(paths.payload)} {_q(paths.config)}",
                    f"chmod 644 {_q(paths.contexts)}/*.json 2>/dev/null || true",
                    # fresh directories each checkpoint: nothing the agent left is ever chowned
                    f"rm -rf {_q(paths.hook_state)} {_q(paths.scratch)}",
                    f"mkdir -p {_q(paths.hook_state)} {_q(paths.scratch)}",
                    f"chown {_q(facts.owner + ':')} {_q(paths.scratch)} {_q(paths.hook_state)}",
                    f"chmod 755 {_q(paths.state)} {_q(paths.hook_state)}",
                    # the hook log is the agent's: its user appends to it, so it is a reading
                    f": > {runs}",
                    f"chown {_q(facts.owner + ':')} {runs}",
                    f"chmod 644 {runs}",
                ]
            )
        )

    def _argv(self, command: str, *extra: str) -> list[str]:
        return [*self.paths.command, command, "--config", self.paths.config, *extra]

    async def _check_setup(self) -> None:
        """Deterministic facts, no agent code run: a failed row is an instrument failure."""
        result = await calls.trusted_exec(self.environment, shlex.join(self._argv("setup-check")))
        found = last_json(result.stdout or "") or {
            "status": ERROR,
            "checks": [
                {
                    "name": "setup_check", "status": ERROR,
                    "detail": f"exit {result.return_code}: {(result.stderr or '').strip()[-500:]}",
                }
            ],
        }  # fmt: skip
        self.evidence.setup_check = found
        self._write("setup-check.json", found)
        if found["status"] != PASSED:
            failed = [c for c in found["checks"] if c.get("status") != PASSED]
            names = ", ".join(f"{c['name']}: {c.get('detail', '')[:200]}" for c in failed)
            raise GateInvalid("instrument", f"the gate's setup check failed: {names}")

    async def _run_canary(self) -> None:
        """The gate over the starting workspace: it must be able to decide."""
        result = await calls.trusted_exec(self.environment, shlex.join(self._argv("canary")))
        found = last_json(result.stdout or "") or {
            "verdict": ERROR, "checks": [],
            "error": f"the canary printed no record (exit {result.return_code})",
        }  # fmt: skip
        self.evidence.canary = found
        self._write("canary.json", found)
        check_canary(found)

    # after each turn -------------------------------------------------------------------------

    async def seal(self, after: str) -> GateOutcome:
        """Reap leftovers, run the seal as the agent's user and attribute its record."""
        await self._reap()
        await self._copy_hook_log(f"{after}/hook-runs.jsonl", after)
        intact = await self._tools_intact(f"before the seal after {after}")
        run = len(self.evidence.gates) + 1
        args = self._argv("seal", "--run", str(run), "--after", after, "--base", str(self.base))
        watched = gate_tool.watches(watched_state(self.packages))
        before = [await self._snapshot(directory) for _, directory, _ in watched]
        result = await calls.trusted_exec(self.environment, shlex.join(args))
        after_snaps = [await self._snapshot(directory) for _, directory, _ in watched]
        record = last_json(result.stdout or "") or self._missing_record(run, after, result)
        record["record"] = f"{RECORD_DIR}/gate-{run}.json"
        self._witness(record, watched, before, after_snaps, after)
        await self._kept(self.outputs.keep(record.get("checks"), f"seal-{run}"))
        intact = await self._tools_intact(f"after the seal after {after}") and intact
        await self._copy_hook_log(f"{after}/hook-runs.jsonl", f"{after} (after the seal)")
        attribution, reason = attribute(record, tools_intact=intact)
        record["attribution"], record["attribution_reason"] = attribution.value, reason
        self._write(f"gate-{run}.json", record)
        self.evidence.gates.append(record)
        return GateOutcome(attribution, record, reason)

    @staticmethod
    def _missing_record(run: int, after: str, result: Any) -> dict[str, Any]:
        return {
            "who": "seal", "run": run, "after": after, "verdict": ERROR, "exit": None,
            "tree": None, "checks": [], "output": result.stdout or "",
            "error": f"the seal printed no record (exit {result.return_code}): "
            f"{(result.stderr or '').strip()[-500:]}",
        }  # fmt: skip

    def _witness(
        self,
        record: dict[str, Any],
        watched: Sequence[tuple[str, str, set[str]]],
        before: Sequence[dict[str, Any]],
        after_snaps: Sequence[dict[str, Any]],
        after: str,
    ) -> None:
        """Compare each watched state directory around the seal; changes are integrity findings."""
        if record.get("changed_during_gate"):
            moved = ", ".join(record["changed_during_gate"][:10])
            self.evidence.integrity.append(
                {
                    "name": "workspace_changed_during_gate", "after": after,
                    "detail": f"the checks changed the work tree: {moved}",
                }
            )  # fmt: skip
        witnessed: list[dict[str, Any]] = []
        for (label, _, allowed), was, now in zip(watched, before, after_snaps, strict=True):
            found = gate_tool.witness_findings(label, was, now, allowed, after)
            self.evidence.integrity += found
            witnessed.append({"label": label, "findings": found})
        record["witness"] = witnessed

    async def _snapshot(self, directory: str) -> dict[str, Any]:
        command = shlex.join([*self.paths.command, "snapshot", "--dir", directory])
        result = await calls.trusted_exec(self.environment, command)
        return last_json(result.stdout or "") or {
            "dir": directory, "present": False, "files": {},
            "error": f"no snapshot (exit {result.return_code})",
        }  # fmt: skip

    async def retain_prefix(self, fix_round: int) -> dict[str, Any] | None:
        """Snapshot the workspace a fix turn starts from (only when fix rounds are allowed)."""
        if self.settings.max_fix_rounds <= 0:
            return None
        target = self.records / f"pre_fix_{fix_round}" / "app"
        await calls.download_dir_excluding(
            self.environment, self.paths.workspace, target, list(SNAPSHOT_EXCLUDES)
        )
        digest = await asyncio.to_thread(tree_digest, target)
        entry = {
            "round": fix_round,
            "path": f"{RECORD_DIR}/pre_fix_{fix_round}/app",
            "tree_digest": digest,
        }
        self.evidence.pre_fix.append(entry)
        return entry

    async def _reap(self) -> None:
        """Kill the agent user's leftover processes, as that user, so nothing writes when sealed."""
        if not self.paths.reap:
            return
        command = shlex.join([*self.paths.command, "reap"])
        result = await calls.trusted_exec(self.environment, command)
        self.evidence.reaps.append(
            last_json(result.stdout or "") or {"error": (result.stderr or "")[-300:]}
        )

    async def _copy_hook_log(self, relative: str, after: str) -> None:
        """Copy the hook's log host-side and note whether it was only ever appended to."""
        runs = _q(f"{self.paths.hook_state}/{gate_tool.HOOK_RUNS}")
        environment = self.environment
        read = await calls.trusted_exec(environment, f"cat {runs} 2>/dev/null || true")
        text = read.stdout or ""
        stat = await calls.trusted_exec(
            environment,
            f"{_q(self.paths.system_python)} -I -B -c {_q(CTIME_SCRIPT)} {runs} "
            "2>/dev/null || true",
        )
        found = self.hook_log.observe(text, (stat.stdout or "").strip() or None, after)
        self.evidence.integrity += found
        path = self.records.parent / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        await self._kept(self.outputs.keep_hook(text))

    async def _kept(self, copying: Awaitable[None]) -> None:
        """Per-check output copies; a failed container call is an integrity failure."""
        try:
            await copying
        except calls.TransportError as error:
            raise GateInvalid("integrity", f"a check's output copy failed: {error}") from error

    async def _tools_intact(self, when: str) -> bool:
        """Every installed tool tree still has the digest recorded at install."""
        ok = True
        for package in self.packages:
            check = await package.installed.verify()
            if not check.ok:
                ok = False
                self.evidence.integrity.append(
                    {
                        "name": "tool_tree_changed", "after": when,
                        "detail": f"{package.name}: {check.digest} is not {check.expected}",
                    }
                )  # fmt: skip
        return ok

    async def _verify_tools(self, when: str) -> None:
        if not await self._tools_intact(when):
            raise GateInvalid("integrity", f"an installed tool tree changed {when}")

    # end of the checkpoint -------------------------------------------------------------------

    async def close(self) -> None:
        """Reap, copy the log one last time; failures are recorded."""
        if self.base is None:
            self.evidence.errors.append("the gate was not set up before the agent started")
            return
        try:
            await self._reap()
            await self._copy_hook_log(f"{RECORD_DIR}/hook-runs.jsonl", "close")
            await self._tools_intact("at close")
        except (calls.TransportError, GateInvalid) as exc:
            self.evidence.errors.append(f"{type(exc).__name__}: {exc}")

    def record(self) -> dict[str, Any]:
        """Host-written evidence of this gate (no check output or per-test records)."""
        ev = self.evidence
        return {
            "max_fix_rounds": self.settings.max_fix_rounds,
            "base": self.base,
            "hooks_path": self.paths.hooks,
            "canary": ev.canary and {k: v for k, v in ev.canary.items() if k != "output"},
            "setup_check": ev.setup_check,
            "integrity": ev.integrity,
            "reaps": ev.reaps,
            "pre_fix_snapshots": ev.pre_fix,
            "hook_outputs": self.outputs.hook_runs,
            "gates": [
                {
                    **{k: v for k, v in gate.items() if k not in ("output", "files")},
                    "checks": [
                        {k: v for k, v in check.items() if k != "tests"}
                        for check in gate.get("checks", [])
                    ],
                }
                for gate in ev.gates
            ],
            "errors": ev.errors,
        }

    # container commands ------------------------------------------------------------------------

    async def _root(self, command: str) -> str:
        """A trusted installation step, as root: its failure is an integrity failure."""
        return await self._run(command, "root")

    async def _agent(self, command: str) -> str:
        """A step as the agent's user (never root): its failure is an integrity failure."""
        return await self._run(command, None)

    async def _run(self, command: str, user: str | None) -> str:
        result = await calls.trusted_exec(self.environment, f"umask 022\n{command}", user=user)
        if result.return_code != 0:
            detail = (result.stderr or result.stdout or "").strip()[-1000:]
            message = f"exit {result.return_code}: {command[:120]!r}: {detail}"
            raise GateInvalid("integrity", message)
        return result.stdout or ""

    def _write(self, name: str, document: Mapping[str, Any]) -> None:
        text = json.dumps(document, indent=2, sort_keys=True) + "\n"
        (self.records / name).write_text(text, encoding="utf-8")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
