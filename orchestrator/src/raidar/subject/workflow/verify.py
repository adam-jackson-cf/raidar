"""The ``verify`` stage type (design sections 5.1-5.2): a gate the agent commits through.

factory-x-loops' ``one-shot-verify`` mechanics (``engine/workflow/gated.py``, ``verified.py``,
``capability/verify``), inside Raidar's container and the checkpoint's one primary session.
The gate itself is ``raidar.subject.primitive.gate_tool``, uploaded beside its tools.

**Privilege.** The stage's *recorder* never runs agent-written code with more privilege than the
agent has. Root runs only the trusted installation (the tools environment from the pinned pack
interpreter, the gate's files, ownership and modes of ``/opt`` and of the state directories) and
hashes the tool directory with that same interpreter (``-I -B``); it never reads the workspace.
Everything else that touches the agent's work runs as the agent's own user: the workspace's git
setup, the setup check, the baseline, the hook's decision, the seal (which runs the agent's
pytest, conftest and project configuration), the witness snapshots and the reaping of the
agent's leftover processes. Each record the gate prints names the uid it ran as (``process``).
The tools stay root-owned and read-only to the agent (G3 hashes them).

**The hook.** The agent's own ``git commit`` runs the root-installed ``hooks/pre-commit``, which
runs the gate's decision as the agent's user (``verify_gate.py hook``) and appends its record to
the hook log, which the agent's user owns. There is no privileged broker: the log is a reading
(the agent can forge or rewrite it), never validity, and the seal below is the authoritative run.
The record's tree is the commit's only when it equals the raw work tree that was checked.

**Where evidence lives.** The runner keeps every record host-side outside the mounted
``/logs/agent`` (``Checkpoint.logs_dir`` is a staging directory) and publishes them after the
agent's processes were reaped. The seal and the baseline print their record on standard output
and the stage writes it; nothing the lab later trusts is read from a path the agent can write.
What the agent's user can still write (native session logs, the workspace, a watched workflow
state directory such as define's) is *draft* evidence, watched rather than trusted: host copies
after every turn, and before and after each seal (``witness_findings``) for the watched
directories a workflow prerequisite names; an earlier copy rewritten or touched is an
``integrity`` entry, a named violation. A seal that finds the work tree changed by its own
checks says so (``workspace_changed_during_gate``) and records the final tree it left: nothing
is put back.

**Before the agent starts** (``prepare``):

- the gate's tools environment (``PATHS.tools``): a virtualenv on the pack's own interpreter
  (``PATHS.system_python``, not the workspace's ``.venv``) holding every check's pinned tool,
  built once per trial and root-owned outside the workspace; its resolved lock
  (``uv pip freeze``) is recorded with its sha256;
- the gate (``verify_gate.py``), its configuration (``gate.json``), the rules record and the
  hooks directory (``hooks/pre-commit``) and the empty hooks directory the gate's git calls use
  beside it, root-owned; the hook log's directory and the gate's scratch directory (the agent's,
  freshly created);
- ``/app`` as a git repository, set up as the agent user: initialised and the seeded tree
  committed as the base when it is not one; ``core.hooksPath`` pointing at the hooks directory;
  ``.git/info/exclude`` leaving out the workspace caches and the workflow's own files
  (``SNAPSHOT_EXCLUDES``, and the rules file when the stage created it); a rules file the task
  already had gets a git clean filter that strips the stage's block, so no agent commit holds
  it. The checkpoint's base is ``HEAD``;
- the rules (``rules_file``): the checks and what blocks, appended between fixed markers to
  the task's own file, or written as a new file;
- the **setup check** (``verify_gate.py setup-check``): no agent code runs. Interpreter and
  tool versions and hashes, tool ownership, the hook, the gate's own hash, the rules block and
  the user (not root) are recorded in ``verify.setup_check``; one failed row makes the trial
  invalid (an instrument failure, never an agent outcome) and stops the checkpoint before the
  agent starts;
- the sha256 of the whole tool directory (gate, configuration, hooks, tools).

**Prompt fragment**: the stage's ``prompt`` with its tokens filled, at ``%%%VERIFY%%%``.

**Checks when the agent stops** (``check``): the agent's leftover processes are reaped, the
hook's run log is copied to the turn's logs and checked for rewrites, then the seal
(``verify_gate.py seal``, as the agent's user) *always* reruns every check over the work tree
exactly as the agent left it and records the verdict and the tree and workspace digest it judged.
It never commits, restores or changes the agent's work. Passed ends the stage (``gate_passed``);
refused resumes the session with factory-x-loops' resumed fix prompt and the gate's output at most
``max_fix_rounds`` times (default 0: no pushback, the agent must verify itself), then ends
``gate_refused`` and the lab grades the workspace as the agent left it; a gate that could not run
ends ``gate_error`` and never starts a fix round.

**Outputs** (``close``): nothing is restored, on a cut (``deadline``, ``turn_error``) or
otherwise: the graded tree is always what the agent left, never something the lab produced. The
leftover processes are reaped, the final hook log is copied, the rules block is stripped from the
workspace (the file removed when the stage created it and nothing else is in it), so the graded
snapshot never holds it, and the tool directory is hashed again. Records under the step's agent
logs: ``workflow/verify/gate-<n>.json`` per seal (who ran it and as which uid, verdict, exits,
per-check results with category, duration, output, the tree, the per-file digests and the
repository evidence), ``workflow/<turn>/verify-hook-runs.jsonl`` after every turn and
``workflow/verify/hook-runs.jsonl`` at the end, ``workflow/verify/rules.md`` (the block the agent
was shown), ``workflow/verify/setup-check.json``, and the stage record in ``workflow.json`` with
every digest.

**Judge** (``verify.judge``, design D3-D4): after every accepted seal, and after a refusal that
used its fix rounds, the engine judges each criterion of the feature definition on the sealed
tree (``raidar.subject.workflow.judging``): Jev is asked, host-side and never from the sandbox
(``raidar.subject.workflow.asking``), one question per ``Aspect:`` over the mapped tests' source
(read from the work tree, checked against the seal's digest, and kept under ``workflow/sources``).
The tests that pass on the base, run
once before the agent starts, close ``Kind: regression`` criteria. Criteria still open come back
to the builder as a ``rework`` turn (the pushback and the gate's result), then the work is sealed
and judged again, up to ``max_passes``. Endings: ``verified``, ``pass_limit``, ``no_progress``,
``budget_exhausted`` (``fit_rework``: a turn starts only when the last turn of its kind, the last
gate and the last judge pass fit the time left; the tree is then gated and judged), ``deadline``
(the phase was cut, or no time was left to judge), ``gate_refused`` (the fix rounds ran out; that
tree is judged, not reworked) and ``gate_error``. ``gate_ending`` is the last seal's own verdict.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from raidar.contract.snapshot import SNAPSHOT_EXCLUDES
from raidar.subject.primitive import calls, gate_tool
from raidar.subject.workflow import judging
from raidar.subject.workflow.acceptance import Acceptance
from raidar.subject.workflow.definition import VERIFY_TOKEN, VerifySettings
from raidar.subject.workflow.judgeloop import JudgeLoop
from raidar.subject.workflow.stage import (
    DEADLINE,
    GATE_FIX,
    REWORK,
    Checkpoint,
    Resume,
    Stage,
)

GATE_PASSED = "gate_passed"
GATE_REFUSED = "gate_refused"
GATE_ERROR = "gate_error"
BUDGET_EXHAUSTED = "budget_exhausted"
BASELINE_RECORD = "base-tests.json"
SETUP_RECORD = "setup-check.json"
CLIP = 20_000
CTIME_SCRIPT = "import os, sys; print(os.stat(sys.argv[1]).st_ctime_ns)"
RECORD_DIR = "workflow/verify"
HOOK_LOG = "verify-hook-runs.jsonl"
RULES_RECORD = "rules.md"
GATE_CONFIG = "gate.json"
PRE_COMMIT = "pre-commit"
PAYLOAD = Path(gate_tool.__file__)
PROMPT_LIMIT = 120_000
TRUNCATED = "\n... gate output truncated to fit the prompt limit ...\n"
FIX_PROMPT = """\
When you stopped, the repository refused your commit: its pre-commit hook `verify-gate` \
failed with exit status {exit}. Its output follows.

Fix what it reports in the working tree, keeping the work the task asks for.
The gate runs again when you stop.

The gate's output:

{output}
"""
"""factory-x-loops ``capability/verify/prompts.py`` ``RESUMED_FIX_PROMPT``, its event filled."""
RULES_HEAD = """\
## Verify gate rules

The repository's pre-commit hook `verify-gate` runs these checks over the whole work tree
(every Python file outside virtualenvs, caches and `.git`) on every `git commit`, and the same
checks run again, and are recorded, when you stop. A commit is refused while a blocking check
fails; a warning check only reports.
"""
HASH_SCRIPT = """\
import hashlib, os, sys
root = sys.argv[1]
digest = hashlib.sha256()
for current, dirs, files in sorted(os.walk(root)):
    for name in sorted(dirs + files):
        path = os.path.join(current, name)
        info = os.lstat(path)
        relative = os.path.relpath(path, root)
        digest.update(f"{relative}\\0{info.st_mode:o}\\0{info.st_uid}\\0".encode())
        if os.path.islink(path):
            digest.update(os.readlink(path).encode())
        elif os.path.isfile(path):
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
print(digest.hexdigest())
"""
"""The tool directory's digest: every entry's path, mode, owner and content or link target."""


@dataclass(frozen=True)
class VerifyPaths:
    """Where the stage works in the container (the workspace, its tools and its state)."""

    workspace: str = "/app"
    home: str = "/opt/raidar-verify"
    state: str = "/var/lib/raidar-verify"
    system_python: str = "/usr/local/bin/python3"
    """The trusted pack interpreter root runs and builds the tools environment from."""
    reap: bool = True
    """Kill the agent user's leftover processes (off only where the agent's user is the test's)."""

    @property
    def tools(self) -> str:
        return f"{self.home}/tools"

    @property
    def python(self) -> str:
        return f"{self.tools}/bin/python"

    @property
    def gate(self) -> str:
        return f"{self.home}/verify_gate.py"

    @property
    def gate_command(self) -> list[str]:
        """The gate on the tools' interpreter, writing no bytecode into the tool directory."""
        return [self.python, "-B", self.gate]

    @property
    def config(self) -> str:
        return f"{self.home}/{GATE_CONFIG}"

    @property
    def hooks(self) -> str:
        return f"{self.home}/hooks"

    @property
    def rules_record(self) -> str:
        return f"{self.home}/{RULES_RECORD}"

    @property
    def hook_state(self) -> str:
        return f"{self.state}/hook"

    @property
    def scratch(self) -> str:
        return f"{self.state}/scratch"

    @property
    def empty_hooks(self) -> str:
        return f"{self.home}/nohooks"


PATHS = VerifyPaths()


class VerifySetupError(RuntimeError):
    """The gate could not be set up before the agent started."""


def tools_install_command(paths: VerifyPaths, pins: list[str]) -> str:
    """Build the tools environment once per trial, on the pack's own interpreter.

    Run as root, so it reads nothing the agent can write: not the workspace's ``.venv`` or
    ``pyproject.toml`` (a different working directory and no ``uv`` configuration).
    """
    tools = shlex.quote(paths.tools)
    return "\n".join(
        [
            "set -eu",
            "cd /",
            f"[ -x {tools}/bin/python ] && exit 0",
            "export UV_NO_CONFIG=1 UV_PYTHON_DOWNLOADS=never",
            f"uv venv -q --python {shlex.quote(paths.system_python)} {tools}",
            f"uv pip install -q --python {tools}/bin/python {shlex.join(pins)}",
        ]
    )


def rules_block(settings: VerifySettings) -> str:
    """The rules the agent reads in ``rules_file``, between the gate's markers."""
    lines = [gate_tool.RULES_BEGIN, RULES_HEAD]
    for check in settings.checks:
        kind = "blocking" if check.blocking else "warning"
        what = (
            "pytest: at least one test must be collected and none may fail; a test running "
            f"longer than {settings.test_timeout} s fails"
            if check.category == "tests"
            else f"`{check.run}`"
        )
        lines.append(f"- `{check.name}` ({check.category}, {kind}, {check.tool}): {what}")
    lines += ["", gate_tool.RULES_END, ""]
    return "\n".join(lines)


def gate_config(settings: VerifySettings, paths: VerifyPaths) -> dict[str, Any]:
    return {
        "root": paths.workspace,
        "home": paths.home,
        "tools": paths.tools,
        "excludes": list(SNAPSHOT_EXCLUDES),
        "test_timeout": settings.test_timeout,
        "checks": [
            {"name": c.name, "category": c.category, "argv": c.argv, "blocking": c.blocking}
            for c in settings.checks
        ],
        "hook_state": paths.hook_state,
        "scratch": paths.scratch,
        "empty_hooks": paths.empty_hooks,
        "rules_file": settings.rules_file,
        "hooks": paths.hooks,
        "rules_record": paths.rules_record,
        "strip_command": shlex.join([*paths.gate_command, "strip-rules"]),
        "session_paths": [],
    }


def fix_prompt(exit_code: int, output: str) -> str:
    """The resumed session's fix prompt, the gate's output cut only to fit the prompt limit."""
    budget = PROMPT_LIMIT - len(FIX_PROMPT.format(exit=exit_code, output=""))
    text = output or "(no output)"
    if len(text) > budget:
        text = text[: max(budget - len(TRUNCATED), 0)] + TRUNCATED
    return FIX_PROMPT.format(exit=exit_code, output=text)


def _clip(text: str, limit: int = CLIP) -> str:
    """``text`` cut to ``limit`` characters, saying so."""
    return text if len(text) <= limit else text[: max(limit - len(TRUNCATED), 0)] + TRUNCATED


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _last_json(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


@dataclass
class Standing:
    """How the checkpoint stands: fix rounds, the last seal's verdict and the ending."""

    ending: str | None = None
    gate_ending: str | None = None
    fix_rounds: int = 0
    """The fix turns this pass has used."""
    fixes: int = 0
    """Every fix turn of the checkpoint."""


@dataclass
class GateEvidence:
    """Host-owned records and the witnesses used to detect tampering."""

    setup_check: dict[str, Any] | None = None
    integrity: list[dict[str, str]] = field(default_factory=list)
    reaps: list[dict[str, Any]] = field(default_factory=list)
    hook_seen: str = ""
    hook_ctime: str | None = None
    hook_integrity_seen: int = 0
    errors: list[str] = field(default_factory=list)


class Verify(Stage):
    """The verify stage with one workflow's settings, for one checkpoint (module docstring)."""

    name = "verify"
    token = VERIFY_TOKEN

    def __init__(
        self,
        settings: VerifySettings,
        *,
        prerequisites: list[dict[str, Any]] | None = None,
        acceptance: Acceptance | None = None,
    ) -> None:
        self.settings = settings
        self.paths = PATHS
        self.instructions = [f"{RECORD_DIR}/{RULES_RECORD}"]
        self.owner: str | None = None
        self.base: str | None = None
        self.facts: dict[str, Any] = {"prerequisites": prerequisites or []}
        self.tools: dict[str, Any] = {"pins": settings.pins}
        self.gates: list[dict[str, Any]] = []
        self.standing = Standing()
        self.evidence = GateEvidence()
        self.loop = (
            None
            if settings.judge is None or settings.rework is None
            else JudgeLoop(
                settings.judge,
                settings.max_passes,
                settings.rework,
                acceptance or Acceptance(),
                self.paths.workspace,
            )
        )

    # before the agent starts ----------------------------------------------------------------

    async def prepare(self, checkpoint: Checkpoint) -> None:
        records = checkpoint.logs_dir / RECORD_DIR
        records.mkdir(parents=True, exist_ok=True)
        self.owner = (await self._exec(checkpoint, "id -un", root=False)).strip()
        started = time.monotonic()
        await self._exec(checkpoint, tools_install_command(self.paths, self.settings.pins))
        self.tools["install_seconds"] = round(time.monotonic() - started, 3)
        lock = await self._exec(
            checkpoint, f"cd / && UV_NO_CONFIG=1 uv pip freeze -q --python {q(self.paths.python)}"
        )
        self.tools["lock"] = lock.splitlines()
        self.tools["lock_sha256"] = _sha256(lock)
        rules = q(f"{self.paths.workspace}/{self.settings.rules_file}")
        owned = await self._exec(
            checkpoint, f"if [ -e {rules} ]; then echo 0; else echo 1; fi", root=False
        )
        self.facts["rules_owned"] = owned.strip() == "1"
        await self._install_gate(checkpoint, records)
        setup = shlex.join(
            [*self.paths.gate_command, "workspace-setup", "--config", self.paths.config]
        )
        found = _last_json(await self._exec(checkpoint, setup, root=False))
        if found is None or "base" not in found:
            raise VerifySetupError(f"the workspace setup produced no base commit: {found}")
        self.base = found["base"]
        self.facts["repo_created"] = bool(found.get("repo_created"))
        await self._setup_check(checkpoint, records)
        self.tools["sha256_before"] = await self._tools_digest(checkpoint)
        if self.loop is not None:
            await self._baseline(checkpoint, records, self.loop)
            self.loop.start(checkpoint)

    async def _setup_check(self, checkpoint: Checkpoint, records: Path) -> None:
        """Deterministic setup facts, no agent code run; a failed row stops the checkpoint."""
        gate = [*self.paths.gate_command, "setup-check", "--config", self.paths.config]
        command = shlex.join(gate)
        result = await calls.trusted_exec(checkpoint.environment, command, user=None)
        found = _last_json(result.stdout or "") or {
            "status": "error",
            "checks": [
                {
                    "name": "setup_check",
                    "status": "error",
                    "detail": f"exit {result.return_code}: {(result.stderr or '').strip()[-500:]}",
                }
            ],
        }
        found["record"] = f"{RECORD_DIR}/{SETUP_RECORD}"
        (records / SETUP_RECORD).write_text(
            json.dumps(found, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.evidence.setup_check = found
        if found["status"] != gate_tool.PASSED:
            failed = [c for c in found["checks"] if c.get("status") != gate_tool.PASSED]
            names = ", ".join(f"{c['name']}: {c.get('detail', '')[:200]}" for c in failed)
            raise VerifySetupError(f"the setup check failed: {names}")

    async def _baseline(self, checkpoint: Checkpoint, records: Path, loop: JudgeLoop) -> None:
        """Run the tests check over the base: the tests that pass before the agent starts.

        A ``Kind: regression`` criterion closes only when a test mapped to it is among them
        (``judge.regression_needs_baseline``); a base the check could not run leaves none. It
        runs as the agent's user, like the seal; the stage writes the printed record.
        """
        paths = self.paths
        command = shlex.join([*paths.gate_command, "baseline", "--config", paths.config])
        result = await calls.trusted_exec(checkpoint.environment, command, user=None)
        found = _last_json(result.stdout or "") or {
            "status": "error",
            "output": f"exit {result.return_code}: {(result.stderr or '').strip()[-500:]}",
        }
        (records / BASELINE_RECORD).write_text(
            json.dumps(found, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        tests = found.get("tests") if found.get("status") != gate_tool.ERROR else None
        loop.baseline_passed = sorted(judging.passed_tests(tests))
        loop.baseline = {
            "record": f"{RECORD_DIR}/{BASELINE_RECORD}",
            "status": found.get("status"),
            "exit": found.get("exit"),
            "seconds": found.get("seconds"),
            "collected": len((tests or {}).get("collected", [])),
            "process": found.get("process"),
            "tree": found.get("tree"),
            "worktree_sha256": found.get("worktree_sha256"),
        }

    async def _install_gate(self, checkpoint: Checkpoint, records: Path) -> None:
        """Root's whole job: install the trusted files, set modes and make fresh state dirs."""
        paths = self.paths
        hook = f"#!/bin/sh\nexec {shlex.join(paths.gate_command)} hook --config {q(paths.config)}\n"
        block = rules_block(self.settings)
        configuration = gate_config(self.settings, paths)
        configuration.update(
            prerequisites=self.facts["prerequisites"],
            rules_owned=self.facts["rules_owned"],
            hook_sha256=_sha256(hook),
            payload_sha256=hashlib.sha256(PAYLOAD.read_bytes()).hexdigest(),
            trusted_uid=int((await self._exec(checkpoint, "id -u")).strip()),
            session_paths=list(checkpoint.session_paths),
        )
        config = json.dumps(configuration, indent=2, sort_keys=True) + "\n"
        for name, text in ((GATE_CONFIG, config), (PRE_COMMIT, hook), (RULES_RECORD, block)):
            (records / name).write_text(text, encoding="utf-8")
        self.facts.update(
            gate_config_sha256=_sha256(config),
            payload_sha256=configuration["payload_sha256"],
            rules_sha256=_sha256(block),
        )
        await self._exec(checkpoint, f"mkdir -p {q(paths.hooks)} {q(paths.empty_hooks)}")
        environment = checkpoint.environment
        await calls.upload_file(environment, PAYLOAD, paths.gate)
        await calls.upload_file(environment, records / GATE_CONFIG, paths.config)
        await calls.upload_file(environment, records / PRE_COMMIT, f"{paths.hooks}/{PRE_COMMIT}")
        await calls.upload_file(environment, records / RULES_RECORD, paths.rules_record)
        owner = q(f"{self.owner}:")
        hook_path = q(f"{paths.hooks}/{PRE_COMMIT}")
        runs = q(f"{paths.hook_state}/{gate_tool.HOOK_RUNS}")
        await self._exec(
            checkpoint,
            "\n".join(
                [
                    "set -eu",
                    f'if [ "$(id -u)" = 0 ]; then chown -R root:root {q(paths.home)}; fi',
                    f"chmod -R go-w {q(paths.home)}",
                    f"chmod 755 {q(paths.home)} {q(paths.hooks)} "
                    f"{q(paths.empty_hooks)} {hook_path}",
                    f"chmod 644 {q(paths.gate)} {q(paths.config)} {q(paths.rules_record)}",
                    # fresh directories each checkpoint: nothing the agent left is ever chowned
                    f"rm -rf {q(paths.hook_state)} {q(paths.scratch)}",
                    f"mkdir -p {q(paths.hook_state)} {q(paths.scratch)}",
                    f"chown {owner} {q(paths.scratch)} {q(paths.hook_state)}",
                    f"chmod 755 {q(paths.state)} {q(paths.hook_state)}",
                    # the hook log is the agent's: its user appends to it, so it is a reading
                    f": > {runs}",
                    f"chown {owner} {runs}",
                    f"chmod 644 {runs}",
                ]
            ),
        )

    # prompt fragment --------------------------------------------------------------------------

    def fragment(self) -> str:
        return self.settings.render()

    # checks when the agent stops -------------------------------------------------------------

    async def check(self, checkpoint: Checkpoint, after: str) -> Resume | None:
        await self._reap(checkpoint)
        await self._copy_hook_log(checkpoint, f"{after}/{HOOK_LOG}", after)
        gate = await self._seal(checkpoint, after)
        # the seal runs the agent's pytest and conftest: the log must not have been touched
        await self._copy_hook_log(checkpoint, f"{after}/{HOOK_LOG}", f"{after} (after the seal)")
        loop, standing = self.loop, self.standing
        if gate["verdict"] == gate_tool.PASSED:
            standing.gate_ending = GATE_PASSED
        elif gate["verdict"] != gate_tool.REFUSED:
            standing.gate_ending = standing.ending = GATE_ERROR
            return None
        elif standing.fix_rounds >= self.settings.max_fix_rounds:
            standing.gate_ending = GATE_REFUSED
        elif loop is not None and not loop.fits(checkpoint, GATE_FIX, self.gates):
            standing.gate_ending, standing.ending = GATE_REFUSED, BUDGET_EXHAUSTED
            await loop.judge_if_fits(checkpoint, self.gates, gate)
            return None
        else:
            standing.fix_rounds += 1
            standing.fixes += 1
            return Resume(fix_prompt(int(gate["exit"]), str(gate.get("output") or "")), GATE_FIX)
        if loop is None:
            standing.ending = standing.gate_ending
            return None
        return await self._judged(checkpoint, gate, loop)

    async def _judged(
        self, checkpoint: Checkpoint, gate: dict[str, Any], loop: JudgeLoop
    ) -> Resume | None:
        """Judge the sealed tree; end the loop or send the open criteria back (``rework``)."""
        standing = self.standing
        left = checkpoint.remaining()
        if left is not None and left <= 0:
            standing.ending = DEADLINE
            return None
        result, changed = await loop.judge_pass(checkpoint, self.gates, gate)
        if standing.gate_ending == GATE_REFUSED:
            standing.ending = GATE_REFUSED
            return None
        ended = loop.stop(changed)
        if ended is None and not loop.fits(checkpoint, REWORK, self.gates):
            ended = BUDGET_EXHAUSTED
        if ended is not None:
            standing.ending = ended
            return None
        standing.fix_rounds = 0  # ``max_fix_rounds`` bounds each pass's fix turns
        return loop.rework(result, _clip(str(gate.get("output") or "(no output)")))

    async def _seal(self, checkpoint: Checkpoint, after: str) -> dict[str, Any]:
        """Run the seal as the agent's user and write the record it printed (host side)."""
        paths, run = self.paths, len(self.gates) + 1
        args = [
            *paths.gate_command, "seal", "--config", paths.config, "--run", str(run),
            "--after", after, "--base", str(self.base),
        ]  # fmt: skip
        watches = gate_tool.watches(self.facts["prerequisites"])
        snaps_before = [await self._snapshot(checkpoint, d) for _, d, _ in watches]
        result = await calls.trusted_exec(checkpoint.environment, shlex.join(args), user=None)
        snaps_after = [await self._snapshot(checkpoint, d) for _, d, _ in watches]
        gate = _last_json(result.stdout or "") or {
            "who": "seal", "run": run, "after": after, "verdict": gate_tool.ERROR,
            "exit": None, "tree": None, "checks": [], "output": result.stdout or "",
            "error": f"the seal printed no record (exit {result.return_code}): "
            f"{(result.stderr or '').strip()[-500:]}",
        }  # fmt: skip
        gate["record"] = f"{RECORD_DIR}/gate-{run}.json"
        gate["turn_kind"] = checkpoint.turns[-1].kind if checkpoint.turns else None
        if gate.get("changed_during_gate"):
            self.evidence.integrity.append(
                {
                    "name": "workspace_changed_during_gate",
                    "after": after,
                    "detail": "the checks changed the work tree: "
                    + ", ".join(gate["changed_during_gate"][:10]),
                }
            )
        witnessed: list[dict[str, Any]] = []
        for (label, _, allowed), was, now in zip(watches, snaps_before, snaps_after, strict=True):
            found = gate_tool.witness_findings(label, was, now, allowed, after)
            self.evidence.integrity += found
            witnessed.append({"label": label, "findings": found})
            kept = checkpoint.logs_dir / RECORD_DIR / f"witness-{run}-{label}.json"
            document = {"before": was, "after": now, "findings": found}
            kept.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        gate["witness"] = witnessed
        path = checkpoint.logs_dir / gate["record"]
        path.write_text(json.dumps(gate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.gates.append(gate)
        return gate

    async def _snapshot(self, checkpoint: Checkpoint, directory: str) -> dict[str, Any]:
        command = shlex.join([*self.paths.gate_command, "snapshot", "--dir", directory])
        result = await calls.trusted_exec(checkpoint.environment, command, user=None)
        return _last_json(result.stdout or "") or {
            "dir": directory, "present": False, "files": {},
            "error": f"no snapshot (exit {result.return_code})",
        }  # fmt: skip

    async def _reap(self, checkpoint: Checkpoint) -> None:
        """Kill the agent user's leftover processes, as the agent's user (a root process is
        never needed to stop what the agent started), so nothing writes while the work is read."""
        if not self.paths.reap:
            return
        command = shlex.join([*self.paths.gate_command, "reap"])
        result = await calls.trusted_exec(checkpoint.environment, command, user=None)
        found = _last_json(result.stdout or "") or {"error": (result.stderr or "")[-300:]}
        self.evidence.reaps.append(found)

    # outputs ----------------------------------------------------------------------------------

    async def close(self, checkpoint: Checkpoint, cut: str | None) -> None:
        if self.base is None:
            self.standing.ending = GATE_ERROR
            self.evidence.errors.append("the gate was not set up before the agent started")
            return
        if cut is not None and self.standing.ending is None:
            self.standing.ending = cut
        try:
            await self._reap(checkpoint)
            await self._copy_hook_log(checkpoint, f"{RECORD_DIR}/{gate_tool.HOOK_RUNS}", "close")
            rules = q(f"{self.paths.workspace}/{self.settings.rules_file}")
            remove = " --remove-empty" if self.facts.get("rules_owned") else ""
            stripped = await self._exec(
                checkpoint,
                f"{shlex.join(self.paths.gate_command)} strip-rules --file {rules}{remove}",
                root=False,
            )
            self.facts["rules_stripped"] = _last_json(stripped)
            self.tools["sha256_after"] = await self._tools_digest(checkpoint)
        except Exception as exc:  # noqa: BLE001 - recorded; the trial's records still get written
            self.evidence.errors.append(f"{type(exc).__name__}: {exc}")

    async def _copy_hook_log(self, checkpoint: Checkpoint, relative: str, after: str) -> None:
        """Copy the hook's run log host-side and note whether it was only ever appended to.

        The agent's user owns the log, so it can rewrite, remove or forge a line: what this
        notes (lines changed or removed, or a write that restored the same bytes: a later
        ``ctime`` on equal content) is a reading, never a validity finding.
        """
        runs = q(f"{self.paths.hook_state}/{gate_tool.HOOK_RUNS}")
        environment = checkpoint.environment
        result = await calls.trusted_exec(environment, f"cat {runs} 2>/dev/null || true", user=None)
        text = result.stdout or ""
        stat = await calls.trusted_exec(
            environment, f"python3 -I -B -c {q(CTIME_SCRIPT)} {runs} 2>/dev/null || true", user=None
        )
        ctime = (stat.stdout or "").strip() or None
        self._observe_hook_log(text, ctime, after)
        path = checkpoint.logs_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.facts["hook_log"] = relative
        self.facts["hook_log_sha256"] = _sha256(text)

    def _observe_hook_log(self, text: str, ctime: str | None, after: str) -> None:
        if self.evidence.hook_seen and not text.startswith(self.evidence.hook_seen):
            self.evidence.integrity.append(
                {
                    "name": "hook_log_rewritten",
                    "after": after,
                    "detail": "the hook's run log no longer begins with the lines the stage "
                    f"copied earlier ({len(self.evidence.hook_seen)} bytes, now {len(text)})",
                }
            )
        elif (
            text == self.evidence.hook_seen
            and self.evidence.hook_ctime
            and ctime
            and ctime != self.evidence.hook_ctime
        ):
            self.evidence.integrity.append(
                {
                    "name": "hook_log_touched",
                    "after": after,
                    "detail": "the hook's run log was written without changing its content",
                }
            )
        elif len(text) >= len(self.evidence.hook_seen):
            self.evidence.hook_seen = text
        self.evidence.hook_ctime = ctime
        self._ingest_hook_integrity(text, after)

    def _ingest_hook_integrity(self, text: str, after: str) -> None:
        """Findings the hook runs themselves recorded (their decision watched the workflow's state
        directories and the session store before and after the checks). The agent's user wrote the
        log, so a missing finding proves nothing; the seal's own witnesses stay authoritative."""
        lines = [line for line in text.splitlines() if line.strip()]
        for line in lines[self.evidence.hook_integrity_seen :]:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            for item in record.get("integrity", []) if isinstance(record, dict) else []:
                self.evidence.integrity.append(
                    {**item, "after": f"{item.get('after', '')} ({after})"}
                )
        self.evidence.hook_integrity_seen = len(lines)

    async def _tools_digest(self, checkpoint: Checkpoint) -> str:
        script = q(HASH_SCRIPT)
        python = q(self.paths.system_python)
        command = f"cd / && {python} -I -B -c {script} {q(self.paths.home)}"
        return (await self._exec(checkpoint, command)).strip()

    async def _exec(self, checkpoint: Checkpoint, command: str, *, root: bool = True) -> str:
        """Run ``command`` (as root only for the trusted installation, else as the agent's
        user; under ``umask 022``: the container's exec umask is 0) and return its output."""
        command = f"umask 022\n{command}"
        result = await calls.trusted_exec(
            checkpoint.environment, command, user="root" if root else None
        )
        if result.return_code != 0:
            detail = (result.stderr or result.stdout or "").strip()[-1000:]
            raise VerifySetupError(f"exit {result.return_code}: {command[:120]!r}: {detail}")
        return result.stdout or ""

    def record(self) -> dict[str, Any]:
        loop = self.loop
        return {
            "ending": self.standing.ending,
            "gate_ending": self.standing.gate_ending,
            "fix_rounds": self.standing.fixes,
            "max_fix_rounds": self.settings.max_fix_rounds,
            "base": self.base,
            "owner": self.owner,
            "hooks_path": self.paths.hooks,
            "rules_file": self.settings.rules_file,
            "rules_record": f"{RECORD_DIR}/{RULES_RECORD}",
            **self.facts,
            "tools": self.tools,
            "setup_check": self.evidence.setup_check,
            "integrity": self.evidence.integrity,
            "reaps": self.evidence.reaps,
            "gates": [
                {
                    **{k: v for k, v in gate.items() if k not in ("output", "files")},
                    "checks": [
                        {k: v for k, v in check.items() if k != "tests"}
                        for check in gate.get("checks", [])
                    ],
                }
                for gate in self.gates
            ],
            "errors": self.evidence.errors,
            **({} if loop is None else loop.record()),
        }


def q(value: str) -> str:
    return shlex.quote(value)
