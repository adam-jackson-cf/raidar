"""The ``verify`` stage type (design sections 5.1-5.2): a gate the agent commits through.

factory-x-loops' ``one-shot-verify`` mechanics (``engine/workflow/gated.py``, ``verified.py``,
``capability/verify``), inside Raidar's container and the checkpoint's one primary session.
The gate itself is ``raidar.subject.workflow.verify_gate``, uploaded beside its tools.

**Before the agent starts** (``prepare``, as root unless said otherwise):

- the gate's tools environment (``PATHS.tools``): a virtualenv on the workspace project's
  interpreter holding every check's pinned tool, built once per trial and root-owned outside
  the workspace; its resolved lock (``uv pip freeze``) is recorded with its sha256;
- the gate (``verify_gate.py``), its configuration (``gate.json``) and the hooks directory
  (``hooks/pre-commit``) beside it, root-owned; the hook's state directory, owned by the agent
  user, with an empty run log; the seal's state directory, root's;
- ``/app`` as a git repository: initialised and the seeded tree committed as the base when it
  is not one; ``core.hooksPath`` pointing at the hooks directory; ``.git/info/exclude`` leaving
  out the workspace caches and the workflow's own files (``SNAPSHOT_EXCLUDES``, and the rules
  file when the stage created it); a rules file the task already had gets a git clean filter
  that strips the stage's block, so no commit holds it. The checkpoint's base is ``HEAD``;
- the rules (``rules_file``): the checks and what blocks, appended between fixed markers to
  the task's own file, or written as a new file;
- the sha256 of the whole tool directory (gate, configuration, hooks, tools), read with the
  system interpreter, never the tools' own.

**Prompt fragment**: the stage's ``prompt`` with its tokens filled, at ``%%%VERIFY%%%``.

**Checks when the agent stops** (``check``): the hook's run log is copied to the turn's logs,
then the seal (``verify_gate.py seal``, as root: the authoritative verdict) commits the
leftovers through the same checks. A tree the previous seal decided reuses its verdict.
Passed ends the stage (``gate_passed``); refused resumes the session with factory-x-loops'
resumed fix prompt and the gate's output, at most ``max_fix_rounds`` times, then ends
``gate_refused`` on the last sealed tree; a gate that could not run ends ``gate_error`` and
never starts a fix round.

**Outputs** (``close``): on a cut (``deadline``, ``turn_error``) or a gate error the workspace
goes back to the last tree a seal decided (FXL ``restore_gated_work``: a running seal and the
agent user's processes are stopped first), and the record says which gate it was, or that none
had decided. Then the final hook log is copied, the rules block is stripped from the workspace
(the file removed when the stage created it and nothing else is in it), so the graded snapshot
never holds it, and the tool directory is hashed again. Records under the step's agent logs:
``workflow/verify/gate-<n>.json`` per seal (who ran it, verdict, exits, per-check results with
category, duration, output, the repository evidence), ``workflow/<turn>/verify-hook-runs.jsonl``
after every turn and ``workflow/verify/hook-runs.jsonl`` at the end, ``workflow/verify/rules.md``
(the block the agent was shown), and the stage record in ``workflow.json`` with every digest.

**Judge** (``verify.judge``, design D3-D4): after every accepted seal, and after a refusal that
used its fix rounds, the engine judges each criterion of the feature definition on the sealed
tree (``raidar.subject.workflow.judging``): Jev is asked, host-side and never from the sandbox
(``raidar.subject.workflow.asking``), one question per ``Aspect:`` over the mapped tests' source
(read from the tree and kept under ``workflow/sources``). The tests that pass on the base, run
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.contract.snapshot import SNAPSHOT_EXCLUDES
from raidar.subject.workflow import judgeloop, judging, verify_gate
from raidar.subject.workflow.acceptance import Acceptance
from raidar.subject.workflow.definition import VERIFY_TOKEN, VerifySettings
from raidar.subject.workflow.judgeloop import JudgeLoop
from raidar.subject.workflow.stage import (
    DEADLINE,
    GATE_FIX,
    REWORK,
    TURN_ERROR,
    Checkpoint,
    Resume,
    Stage,
)

GATE_PASSED = "gate_passed"
GATE_REFUSED = "gate_refused"
GATE_ERROR = "gate_error"
RESTORED_ON = (DEADLINE, TURN_ERROR, GATE_ERROR)
BUDGET_EXHAUSTED = "budget_exhausted"
BASELINE_RECORD = "base-tests.json"
CLIP = 20_000
RECORD_DIR = "workflow/verify"
HOOK_LOG = "verify-hook-runs.jsonl"
RULES_RECORD = "rules.md"
GATE_CONFIG = "gate.json"
PRE_COMMIT = "pre-commit"
PAYLOAD = Path(verify_gate.__file__)
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
(every Python file outside virtualenvs, caches and `.git`) on every `git commit`, and again
when you stop. A commit is refused while a blocking check fails; a warning check only reports.
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
    def hook_state(self) -> str:
        return f"{self.state}/hook"

    @property
    def seal_state(self) -> str:
        return f"{self.state}/seal"


PATHS = VerifyPaths()


class VerifySetupError(RuntimeError):
    """The gate could not be set up before the agent started."""


def tools_install_command(paths: VerifyPaths, pins: list[str]) -> str:
    """Build the tools environment once per trial, on the workspace project's interpreter."""
    tools, cfg = shlex.quote(paths.tools), shlex.quote(f"{paths.workspace}/.venv/pyvenv.cfg")
    return "\n".join(
        [
            "set -eu",
            f"[ -x {tools}/bin/python ] && exit 0",
            "py=python3",
            f'if [ -f {cfg} ]; then home=$(sed -n "s/^home = //p" {cfg}); '
            'if [ -x "$home/python3" ]; then py="$home/python3"; fi; fi',
            f'UV_PYTHON_DOWNLOADS=never uv venv -q --python "$py" {tools}',
            f"uv pip install -q --python {tools}/bin/python {shlex.join(pins)}",
        ]
    )


def rules_block(settings: VerifySettings) -> str:
    """The rules the agent reads in ``rules_file``, between the gate's markers."""
    lines = [verify_gate.RULES_BEGIN, RULES_HEAD]
    for check in settings.checks:
        kind = "blocking" if check.blocking else "warning"
        what = (
            "pytest: at least one test must be collected and none may fail; a test running "
            f"longer than {settings.test_timeout} s fails"
            if check.category == "tests"
            else f"`{check.run}`"
        )
        lines.append(f"- `{check.name}` ({check.category}, {kind}, {check.tool}): {what}")
    lines += ["", verify_gate.RULES_END, ""]
    return "\n".join(lines)


def gate_config(settings: VerifySettings, paths: VerifyPaths) -> dict[str, Any]:
    return {
        "root": paths.workspace,
        "tools": paths.tools,
        "excludes": list(SNAPSHOT_EXCLUDES),
        "test_timeout": settings.test_timeout,
        "checks": [
            {"name": c.name, "category": c.category, "argv": c.argv, "blocking": c.blocking}
            for c in settings.checks
        ],
        "hook_state": paths.hook_state,
        "seal_state": paths.seal_state,
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


def _values(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)


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
        self.restored: dict[str, Any] | None = None
        self.errors: list[str] = []
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
        lock = await self._exec(checkpoint, f"uv pip freeze -q --python {q(self.paths.python)}")
        self.tools["lock"] = lock.splitlines()
        self.tools["lock_sha256"] = _sha256(lock)
        await self._install_gate(checkpoint, records)
        values = _values(await self._exec(checkpoint, self._workspace_script()))
        if "base" not in values:
            raise VerifySetupError("the workspace repository has no base commit")
        self.base = values["base"]
        self.facts.update(
            repo_created=values.get("repo_created") == "1",
            rules_owned=values.get("rules_owned") == "1",
        )
        self.tools["sha256_before"] = await self._tools_digest(checkpoint)
        if self.loop is not None:
            await self._baseline(checkpoint, records, self.loop)
            self.loop.start(checkpoint)

    async def _baseline(self, checkpoint: Checkpoint, records: Path, loop: JudgeLoop) -> None:
        """Run the tests check over the base: the tests that pass before the agent starts.

        A ``Kind: regression`` criterion closes only when a test mapped to it is among them
        (``judge.regression_needs_baseline``); a base the check could not run leaves none.
        """
        paths = self.paths
        command = shlex.join([*paths.gate_command, "baseline", "--config", paths.config])
        result = await checkpoint.environment.exec(command, user="root")
        found = _last_json(result.stdout or "") or {
            "status": "error",
            "output": f"exit {result.return_code}: {(result.stderr or '').strip()[-500:]}",
        }
        (records / BASELINE_RECORD).write_text(
            json.dumps(found, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        tests = found.get("tests") if found.get("status") != verify_gate.ERROR else None
        loop.baseline_passed = sorted(judging.passed_tests(tests))
        loop.baseline = {
            "record": f"{RECORD_DIR}/{BASELINE_RECORD}",
            "status": found.get("status"),
            "exit": found.get("exit"),
            "seconds": found.get("seconds"),
            "collected": len((tests or {}).get("collected", [])),
        }
        await self._exec(checkpoint, f"chown -R {q(f'{self.owner}:')} {q(paths.workspace)}")

    async def _install_gate(self, checkpoint: Checkpoint, records: Path) -> None:
        paths = self.paths
        configuration = gate_config(self.settings, paths)
        configuration["prerequisites"] = self.facts["prerequisites"]
        config = json.dumps(configuration, indent=2, sort_keys=True) + "\n"
        hook = f"#!/bin/sh\nexec {shlex.join(paths.gate_command)} hook --config {q(paths.config)}\n"
        block = rules_block(self.settings)
        for name, text in ((GATE_CONFIG, config), (PRE_COMMIT, hook), (RULES_RECORD, block)):
            (records / name).write_text(text, encoding="utf-8")
        self.facts.update(
            gate_config_sha256=_sha256(config),
            payload_sha256=hashlib.sha256(PAYLOAD.read_bytes()).hexdigest(),
            rules_sha256=_sha256(block),
        )
        await self._exec(
            checkpoint,
            f"mkdir -p {q(paths.hooks)} {q(paths.hook_state)} {q(paths.seal_state)}",
        )
        environment = checkpoint.environment
        await environment.upload_file(PAYLOAD, paths.gate)
        await environment.upload_file(records / GATE_CONFIG, paths.config)
        await environment.upload_file(records / PRE_COMMIT, f"{paths.hooks}/{PRE_COMMIT}")
        await environment.upload_file(records / RULES_RECORD, f"{paths.seal_state}/{RULES_RECORD}")
        owner = q(f"{self.owner}:")
        runs = q(f"{paths.hook_state}/{verify_gate.HOOK_RUNS}")
        hook = q(f"{paths.hooks}/{PRE_COMMIT}")
        await self._exec(
            checkpoint,
            "\n".join(
                [
                    "set -eu",
                    f'if [ "$(id -u)" = 0 ]; then chown -R root:root {q(paths.home)}; fi',
                    f"chmod -R go-w {q(paths.home)}",
                    f"chmod 755 {q(paths.home)} {q(paths.hooks)} {hook}",
                    f"chmod 644 {q(paths.gate)} {q(paths.config)}",
                    f"rm -f {runs} && : > {runs}",
                    f"chown -R {owner} {q(paths.hook_state)}",
                    f"chmod 700 {q(paths.seal_state)}",
                ]
            ),
        )

    def _workspace_script(self) -> str:
        paths, rules = self.paths, q(self.settings.rules_file)
        git = "git -c safe.directory=*"
        strip = f"{shlex.join(paths.gate_command)} strip-rules"
        block = q(f"{paths.seal_state}/{RULES_RECORD}")
        excludes = " ".join(q(f"{name}/") for name in SNAPSHOT_EXCLUDES)
        seal = " ".join(f"{key}={q(value)}" for key, value in verify_gate.SEAL_AUTHOR.items())
        identity = (("user.name", "raidar-agent"), ("user.email", "agent@raidar.invalid"))
        return "\n".join(
            [
                "set -eu",
                f"cd {q(paths.workspace)}",
                "created=0",
                "if [ ! -d .git ]; then git init -q; created=1; fi",
                f"{git} config core.hooksPath {q(paths.hooks)}",
                *(
                    f"{git} config {key} >/dev/null || {git} config {key} {value}"
                    for key, value in identity
                ),
                f'if [ -L {rules} ]; then echo "{rules} is a symlink" >&2; exit 1; fi',
                f"owned=0; if [ -f {rules} ]; then {strip} --file {rules} >/dev/null; "
                f"else owned=1; : > {rules}; fi",
                f"printf '\\n' >> {rules}; cat {block} >> {rules}",
                "mkdir -p .git/info",
                f"{{ printf '%s\\n' {excludes}; "
                f"if [ $owned = 1 ]; then printf '/%s\\n' {rules}; fi; }} > .git/info/exclude",
                f"if [ $owned = 0 ]; then printf '/%s filter=raidar-rules\\n' {rules} "
                f"> .git/info/attributes; {git} config filter.raidar-rules.clean {q(strip)}; fi",
                f"if ! {git} rev-parse -q --verify HEAD >/dev/null; then {git} add -A; {seal} "
                f"{git} commit -q --no-verify --allow-empty -m 'raidar: the workspace before the "
                "agent'; fi",
                f'echo "base=$({git} rev-parse HEAD)"',
                'echo "repo_created=$created"',
                'echo "rules_owned=$owned"',
                f"chown -R {q(f'{self.owner}:')} .",
            ]
        )

    # prompt fragment --------------------------------------------------------------------------

    def fragment(self) -> str:
        return self.settings.render()

    # checks when the agent stops -------------------------------------------------------------

    async def check(self, checkpoint: Checkpoint, after: str) -> Resume | None:
        await self._copy_hook_log(checkpoint, f"{after}/{HOOK_LOG}")
        gate = await self._seal(checkpoint, after)
        loop, standing = self.loop, self.standing
        if gate["verdict"] == verify_gate.PASSED:
            standing.gate_ending = GATE_PASSED
        elif gate["verdict"] != verify_gate.REFUSED:
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
            seal = judgeloop.decided(self.gates, gate)
            fix = fix_prompt(int(gate["exit"]), str(seal.get("output") or ""))
            return Resume(fix, GATE_FIX)
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
        seal = judgeloop.decided(self.gates, gate)
        return loop.rework(result, _clip(str(seal.get("output") or "(no output)")))

    def _gate(self, run: object) -> dict[str, Any] | None:
        return next((gate for gate in self.gates if gate["run"] == run), None)

    def _last_decided(self) -> dict[str, Any] | None:
        return next(
            (
                gate
                for gate in reversed(self.gates)
                if gate["verdict"] in (verify_gate.PASSED, verify_gate.REFUSED) and gate.get("tree")
            ),
            None,
        )

    async def _seal(self, checkpoint: Checkpoint, after: str) -> dict[str, Any]:
        paths, run = self.paths, len(self.gates) + 1
        args = [
            *paths.gate_command, "seal", "--config", paths.config, "--owner", str(self.owner),
            "--run", str(run), "--after", after, "--base", str(self.base),
        ]  # fmt: skip
        previous = self._last_decided()
        if previous is not None:
            args += ["--previous-run", str(previous["run"]), "--previous-tree", previous["tree"]]
            args += ["--previous-verdict", previous["verdict"]]
            args += ["--previous-worktree", previous["worktree_sha256"]]
        result = await checkpoint.environment.exec(shlex.join(args), user="root")
        gate = _last_json(result.stdout or "") or {
            "who": "seal", "run": run, "after": after, "verdict": verify_gate.ERROR,
            "exit": None, "tree": None, "commit": None, "reused_from": None, "checks": [],
            "output": result.stdout or "",
            "error": f"the seal printed no record (exit {result.return_code}): "
            f"{(result.stderr or '').strip()[-500:]}",
        }  # fmt: skip
        gate["record"] = f"{RECORD_DIR}/gate-{run}.json"
        gate["turn_kind"] = checkpoint.turns[-1].kind if checkpoint.turns else None
        path = checkpoint.logs_dir / gate["record"]
        path.write_text(json.dumps(gate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.gates.append(gate)
        return gate

    # outputs ----------------------------------------------------------------------------------

    async def close(self, checkpoint: Checkpoint, cut: str | None) -> None:
        if self.base is None:
            self.standing.ending = GATE_ERROR
            self.errors.append("the gate was not set up before the agent started")
            return
        if cut is not None and self.standing.ending is None:
            self.standing.ending = cut
        try:
            if self.standing.ending in RESTORED_ON:
                await self._restore(checkpoint)
            await self._copy_hook_log(checkpoint, f"{RECORD_DIR}/{verify_gate.HOOK_RUNS}")
            rules = q(f"{self.paths.workspace}/{self.settings.rules_file}")
            remove = " --remove-empty" if self.facts.get("rules_owned") else ""
            stripped = await self._exec(
                checkpoint,
                f"{shlex.join(self.paths.gate_command)} strip-rules --file {rules}{remove}",
            )
            self.facts["rules_stripped"] = _last_json(stripped)
            self.tools["sha256_after"] = await self._tools_digest(checkpoint)
        except Exception as exc:  # noqa: BLE001 - recorded; the trial's records still get written
            self.errors.append(f"{type(exc).__name__}: {exc}")

    async def _restore(self, checkpoint: Checkpoint) -> None:
        decided = self._last_decided()
        self.restored = {"reason": self.standing.ending, "gate": None, "tree": None}
        if decided is None:
            self.restored["detail"] = (
                "no seal decided a tree: the workspace is as the agent left it"
            )
            return
        paths = self.paths
        args = [
            *paths.gate_command, "restore", "--config", paths.config,
            "--owner", str(self.owner), "--tree", decided["tree"],
        ]  # fmt: skip
        result = await checkpoint.environment.exec(shlex.join(args), user="root")
        outcome = _last_json(result.stdout or "") or {"error": (result.stderr or "")[-500:]}
        self.restored.update(gate=decided["run"], tree=decided["tree"], outcome=outcome)

    async def _copy_hook_log(self, checkpoint: Checkpoint, relative: str) -> None:
        runs = q(f"{self.paths.hook_state}/{verify_gate.HOOK_RUNS}")
        text = await self._exec(checkpoint, f"cat {runs}")
        path = checkpoint.logs_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.facts["hook_log"] = relative
        self.facts["hook_log_sha256"] = _sha256(text)

    async def _tools_digest(self, checkpoint: Checkpoint) -> str:
        script = q(HASH_SCRIPT)
        return (await self._exec(checkpoint, f"python3 -c {script} {q(self.paths.home)}")).strip()

    async def _exec(self, checkpoint: Checkpoint, command: str, *, root: bool = True) -> str:
        """Run ``command`` (as root, under ``umask 022``: the container's exec umask is 0, which
        would leave the gate's tools writable by the agent user) and return its output."""
        if root:
            command = f"umask 022\n{command}"
        result = await checkpoint.environment.exec(command, user="root" if root else None)
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
            "gates": [
                {
                    **{k: v for k, v in gate.items() if k != "output"},
                    "checks": [
                        {k: v for k, v in check.items() if k != "tests"}
                        for check in gate.get("checks", [])
                    ],
                }
                for gate in self.gates
            ],
            "restored": self.restored,
            "errors": self.errors,
            **({} if loop is None else loop.record()),
        }


def q(value: str) -> str:
    return shlex.quote(value)
