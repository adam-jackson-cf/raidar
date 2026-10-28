"""``verify-gate``: the verify stage's gate, run inside the trial container as the agent's user.

The verify stage (``raidar.subject.workflow.verify``) uploads this file beside its tools
environment and runs it on that environment's interpreter, so it imports only the standard
library (and ``pytest`` from the tools environment, in ``pytest-run``). Its configuration is
one JSON file the stage writes (``root``, ``tools``, ``excludes``, ``test_timeout``,
``checks``, ``hook_state``, ``scratch``, ...). **Every subcommand runs as the agent's own user,
never as root**: the checks execute the agent's pytest, conftest and project configuration, and
no byte the agent wrote is ever run with more privilege than the agent has. Each record a
subcommand prints says which user ran it (``process``: uid, gid, pid). The gate trusts nothing it
reads from the agent's repository configuration and never changes the agent's work (index, HEAD,
work tree). Subcommands:

- ``hook``: the workspace's ``pre-commit`` hook, run by the agent's own ``git commit``. It is a
  client of the root-owned broker (``broker``): it names no verdict or tree, prints what the
  broker relays and exits as the broker's record says (0 passed, 1 refused, 126 the gate could
  not run). ``hook-decide`` is the decision, run by the broker as the agent's user: it checks the
  work tree and returns its record (verdict, checked workspace digest, index and raw trees, per-
  check results) on a pipe; the broker is the only writer of the hash-chained, root-owned hook
  log. The hook decides nothing for the runner: the seal does.
- ``seal``: the runner's record of the work after a turn (the authoritative verdict). It always
  runs the checks over the work tree as the agent left it and prints one JSON record: the
  verdict, the tree it judged (a Git tree built from the work tree with raw bytes in a private
  Git directory: the agent's ``.git/config``, hooks, attributes and filters are never read, and
  the agent's index, objects and ``HEAD`` are not touched), the workspace digest and per-file
  digests, and the repository's evidence for the bypass check: ``core.hooksPath`` and every
  commit since the checkpoint's base. It never commits, restores or edits anything of the agent's.
- ``baseline``: runs the tests check once over the work tree before the agent starts and prints
  its per-test outcomes (the tests that pass on the base, for ``Kind: regression`` criteria).
- ``setup-check``: a deterministic check that runs no agent code: interpreter and tool versions
  and hashes, the hook, who owns the tools, the rules block and the process's own user.
- ``reap``: kills every other process of this user (the agent's leftovers), so nothing writes
  the work tree while it is sealed or graded.
- ``strip-rules``: removes the stage's rules block from a file (or from standard input), so the
  block never reaches the graded workspace.
- ``pytest-run``: the tests check's pytest, on the tools environment with the workspace's own
  site-packages after its own, every test stopped after ``test_timeout`` seconds. It never runs as
  root: pytest is read from the pinned tool environment as the agent's user only.
- ``canary``: ``decide`` over the work tree before the agent starts, with no hook and no seal's
  workspace evidence (the gate run whose pass makes later gate errors the agent's).

A check whose tool cannot be started, is killed or exceeds its timeout makes the gate an error
(126): never a pass and never a refusal; each such result names its ``cause`` (``timeout``,
``crashed`` or ``unrunnable``) so the host can attribute it. Any other non-zero exit is the check
failing on the work. Package-contributed checks (``package_checks`` in the configuration: free
names, argv run unmodified with ``RAIDAR_STAGE_CONTEXT`` in a clean environment, the reserved
``pytest`` kind) run beside the spike's own, ``before`` checks first.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import os
import shutil
import signal
import site
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Generator, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

PASSED, REFUSED, ERROR = "passed", "refused", "error"
EXITS = {PASSED: 0, REFUSED: 1, ERROR: 126}
HOOK_RUNS = "hook-runs.jsonl"
CHECK_TIMEOUT_S = 600
BROKER_TIMEOUT_S = CHECK_TIMEOUT_S * 6
BROKER_REQUEST_LIMIT = 65536
BROKER_PID = "broker.pid"
TEST_OUTCOMES = "tests.json"
OUTPUT_CHARS = 20_000
RULES_BEGIN = "<!-- raidar verify rules: begin -->"
RULES_END = "<!-- raidar verify rules: end -->"
KEPT_ENV = ("HOME", "LANG", "LC_ALL", "TMPDIR", "USER", "LOGNAME")
GIT_KEPT = ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE")
"""What a hook inherits from the agent's own ``git commit``: where that commit's index is."""
GATE_HOOKS = ["/dev/null"]
"""The gate's own empty hooks directory (``configure`` sets it from the configuration)."""


def git_options() -> list[str]:
    """Options for every git call: nothing in a repository configuration is run (the agent's own
    ``.git/config`` is read only where a call reads the repository's history or one of its
    settings, with ``--local``)."""
    return [
        "-c", "core.fsmonitor=", "-c", f"core.hooksPath={GATE_HOOKS[0]}",
        "-c", "core.attributesFile=/dev/null", "-c", "core.excludesFile=/dev/null",
        "-c", "core.pager=cat", "-c", "core.untrackedCache=false", "-c", "diff.external=",
    ]  # fmt: skip


FILE_MODES = {"file": "100644", "exec": "100755", "link": "120000"}
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class GateError(RuntimeError):
    """The gate could not decide."""


@dataclass
class CheckResult:
    name: str
    category: str
    blocking: bool
    status: str
    exit: int | None
    seconds: float
    output: str = ""
    input_sha256: str | None = None
    tests: dict[str, Any] | None = None
    cause: str | None = None
    call: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None
    """Declared outputs captured right after the check: each one's ``walk.snapshot`` manifest and
    ``tar`` (a root-trusted, bounded archive the host downloads and verifies)."""


@dataclass
class Decision:
    verdict: str
    seconds: float
    checks: list[CheckResult] = field(default_factory=list)
    error: str | None = None

    def summary(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in asdict(c).items() if k != "output"} for c in self.checks]

    def text(self) -> str:
        lines = [f"verify-gate: {self.verdict}"]
        if self.error:
            lines.append(f"verify-gate: ERROR, the gate could not run: {self.error}")
        for check in self.checks:
            kind = "blocking" if check.blocking else "warning"
            lines.append(
                f"- {check.name} ({check.category}, {kind}): {check.status}, exit {check.exit}"
            )
            if check.status != PASSED and check.output.strip():
                lines.append(check.output.rstrip())
        return "\n".join(lines) + "\n"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def process_facts() -> dict[str, Any]:
    """Who runs this command: the proof that no gate step has root's privileges."""
    return {
        "uid": os.getuid(), "euid": os.geteuid(), "gid": os.getgid(), "egid": os.getegid(),
        "pid": os.getpid(), "ppid": os.getppid(),
    }  # fmt: skip


def _git_env(*, keep: Sequence[str] = (), extra: dict[str, str] | None = None) -> dict[str, str]:
    """A git environment with no system or global configuration and nothing inherited."""
    env = {k: os.environ[k] for k in (*KEPT_ENV, "PATH") if k in os.environ}
    env.update({k: os.environ[k] for k in keep if k in os.environ})
    env.update(
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_ATTR_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GIT_PAGER="cat", GIT_EXTERNAL_DIFF="",
    )  # fmt: skip
    return {**env, **(extra or {})}


def _git(
    cwd: Path,
    *args: str,
    env: dict[str, str] | None = None,
    stdin: bytes | None = None,
    options: Sequence[str] | None = None,
) -> str:
    """Run git in ``cwd`` and return its stripped output; raise on failure."""
    done = subprocess.run(
        ["git", *(git_options() if options is None else options), *args],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        env=env if env is not None else _git_env(),
        check=False,
    )
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).decode("utf-8", errors="replace").strip()
        raise GateError(f"git {args[0]}: {detail}")
    return done.stdout.decode("utf-8", errors="replace").strip()


def _repo_git(root: Path, *args: str) -> str:
    """Read the agent's repository (history and settings only; nothing it configures is run)."""
    return _git(root, "--git-dir", str(root / ".git"), *args)


def _repo_git_or_none(root: Path, *args: str) -> str | None:
    try:
        return _repo_git(root, *args)
    except GateError:
        return None


def python_files(root: Path, excludes: Sequence[str]) -> list[str]:
    found: list[str] = []
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in excludes)
        base = Path(current)
        found += [
            (base / name).relative_to(root).as_posix() for name in names if name.endswith(".py")
        ]
    return sorted(found)


def _entry_identity(path: Path) -> tuple[str, bytes]:
    """The entry's kind (``file``, ``exec`` or ``link``) and a digest of its content."""
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode):
        return "link", hashlib.sha256(os.fsencode(os.readlink(path))).digest()
    if not stat.S_ISREG(mode):
        raise GateError(f"workspace entry is not a regular file or link: {path}")
    with path.open("rb") as stream:
        content = hashlib.file_digest(stream, "sha256").digest()
    return ("exec" if mode & 0o111 else "file"), content


def worktree_manifest(root: Path, excludes: Sequence[str]) -> dict[str, str]:
    """Every entry the checks can see (ignored and unstaged files too), by path: kind and sha256."""
    found: dict[str, str] = {}
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in excludes)
        base = Path(current)
        links = [name for name in dirs if (base / name).is_symlink()]
        for name in sorted(names + links):
            path = base / name
            kind, content = _entry_identity(path)
            found[path.relative_to(root).as_posix()] = f"{kind}:{content.hex()}"
    return found


def manifest_digest(manifest: dict[str, str]) -> str:
    """One digest of a manifest (the checked workspace the hook and the seal agree on)."""
    digest = hashlib.sha256()
    for path, identity in sorted(manifest.items()):
        digest.update(os.fsencode(path) + b"\0" + identity.encode() + b"\0")
    return digest.hexdigest()


def _tool_env(config: dict[str, Any], cache: Path) -> dict[str, str]:
    kept = {key: os.environ[key] for key in KEPT_ENV if key in os.environ}
    return {
        **kept,
        "PATH": f"{config['tools']}/bin:/usr/local/bin:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "RUFF_CACHE_DIR": str(cache / "ruff"),
        "MYPY_CACHE_DIR": str(cache / "mypy"),
    }


def _project_sites(root: Path) -> list[str]:
    return sorted(str(path) for path in root.glob(".venv/lib/python*/site-packages"))


def _read_outcomes(path: Path, category: str) -> dict[str, Any] | None:
    """The tests check's per-test record (``collected`` and ``outcomes``), if it wrote one."""
    if category != "tests" or not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def _command(
    config: dict[str, Any], check: dict[str, Any], files: list[str], outcomes: Path
) -> list[str]:
    root, tools = Path(config["root"]), Path(config["tools"])
    if check["category"] == "tests":
        sites = [arg for path in _project_sites(root) for arg in ("--site", path)]
        return [
            str(tools / "bin" / "python"),
            str(Path(__file__).resolve()),
            "pytest-run",
            "--timeout",
            str(config["test_timeout"]),
            "--outcomes",
            str(outcomes),
            *sites,
            "--",
            "-q",
            "-p",
            "no:cacheprovider",
        ]
    binary, *rest = check["argv"]
    extra: list[str] = []
    project_python = root / ".venv" / "bin" / "python"
    if check["category"] == "types" and project_python.exists():
        extra = ["--python-executable", str(project_python)]
    return [str(tools / "bin" / binary), *rest, *extra, *files]


def _spawn(
    command: list[str], cwd: str, env: dict[str, str], timeout: float
) -> tuple[str, int | None, str, str | None]:
    """Run one check command: its status, exit code, output and, for an error, its cause."""
    try:
        done = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ERROR, None, f"killed after {timeout} s", "timeout"
    except OSError as exc:
        return ERROR, None, f"could not start: {exc}", "unrunnable"
    output = done.stdout.decode("utf-8", errors="replace")
    if done.returncode < 0 or done.returncode in (126, 127):
        return ERROR, done.returncode, output, "crashed"
    return (PASSED if done.returncode == 0 else "failed"), done.returncode, output, None


def _run_check(
    config: dict[str, Any], check: dict[str, Any], files: list[str], cache: Path
) -> CheckResult:
    started = time.monotonic()
    outcomes = cache.parent / TEST_OUTCOMES

    def result(status: str, code: int | None, output: str, cause: str | None = None) -> CheckResult:
        seconds = round(time.monotonic() - started, 3)
        return CheckResult(
            check["name"], check["category"], check["blocking"], status, code, seconds,
            output[-OUTPUT_CHARS:], tests=_read_outcomes(outcomes, check["category"]),
            cause=cause,
        )  # fmt: skip

    if check["category"] != "tests" and not files:
        return result(PASSED, 0, "no Python files to check")
    outcomes.unlink(missing_ok=True)
    command = _command(config, check, files, outcomes)
    if not os.access(command[0], os.X_OK):
        return result(ERROR, None, f"tool not installed: {command[0]}", "unrunnable")
    run = _spawn(command, config["root"], _tool_env(config, cache), CHECK_TIMEOUT_S)
    return result(*run)


def _package_command(config: dict[str, Any], check: dict[str, Any], outcomes: Path) -> list[str]:
    """A package check's argv, run unmodified; the ``pytest`` kind is this payload's
    ``pytest-run`` on the package's pinned tool interpreter (``argv``: pytest arguments)."""
    if check["kind"] != "pytest":
        return list(check["argv"])
    sites = [arg for path in _project_sites(Path(config["root"])) for arg in ("--site", path)]
    per_test = max(1, int(check["timeout_s"]) // 2)
    return [
        check["python"], str(Path(__file__).resolve()), "pytest-run",
        "--timeout", str(per_test), "--outcomes", str(outcomes), "--agent-user", *sites,
        "--", "-q", "-p", "no:cacheprovider", *check["argv"],
    ]  # fmt: skip


def _package_env(check: dict[str, Any], work: Path) -> dict[str, str]:
    """The whole environment of a package check: nothing inherited, a fresh HOME and TMPDIR."""
    (work / "home").mkdir()
    (work / "tmp").mkdir()
    env = {
        "PATH": check["path"],
        "LANG": "C.UTF-8",
        "HOME": str(work / "home"),
        "TMPDIR": str(work / "tmp"),
        "RAIDAR_STAGE_CONTEXT": check["context"],
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if check["kind"] == "pytest":
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env


def _pytest_status(status: str, tests: dict[str, Any] | None) -> str:
    """A passing pytest run counts only with at least one collected test and no failed one."""
    if status != PASSED:
        return status
    outcomes = (tests or {}).get("outcomes") or {}
    if not tests or not tests.get("collected") or "failed" in outcomes.values():
        return "failed"
    return PASSED


def _supervisor(config: dict[str, Any]) -> Any:
    """The trusted call supervisor (root-owned ``payload`` package): the one launcher of calls."""
    root = config["trusted_root"]
    if root not in sys.path:
        sys.path.insert(0, root)
    from payload import supervise  # noqa: PLC0415 - Raidar's own root-owned code, on demand

    return supervise


def _confined_spec(
    supervisor: Any,
    config: dict[str, Any],
    check: dict[str, Any],
    argv: list[str],
    env: dict[str, str],
    work: Path,
) -> dict[str, Any]:
    """The supervisor's own spec builder: this process already is the agent's user (no drop)."""
    return supervisor.build_spec(
        call_dir=str(work), argv=argv, env=env, cwd=config["root"],
        timeout_s=float(check["timeout_s"]), uid=None, gid=None,
        seccomp=bool(config.get("confine", True)),
        trees=check.get("verify", []), state=check.get("state"),
        state_limits=check.get("state_limits"),
    )  # fmt: skip


def _refusal(result: dict[str, Any]) -> tuple[str, str] | None:
    """Why the supervisor did not or could not trust the call: its cause and detail."""
    if result.get("bounds_violation"):
        return "bounds", f"watched state outside Raidar's bounds: {result['bounds_violation']}"
    if result.get("isolation_error"):
        return "isolation", str(result["isolation_error"])
    trees = [*result.get("tool_before", []), *result.get("tool_after", [])]
    changed = [tree for tree in trees if not tree["ok"]]
    if changed:
        return "tool_changed", f"installed tool tree changed: {changed[0]['root']}"
    return None


def _supervised(
    config: dict[str, Any], check: dict[str, Any], argv: list[str], env: dict[str, str], work: Path
) -> tuple[str, int | None, str, str | None, dict[str, Any]]:
    """One call through the supervisor: status, exit, output, cause and the call's own record."""
    supervisor = _supervisor(config)
    result = supervisor.supervise(_confined_spec(supervisor, config, check, argv, env, work))
    call = {
        **result, "argv": argv, "uid": os.getuid(), "env_names": sorted(env),
        "env_sha256": hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest(),
        "timeout_s": check["timeout_s"],
    }  # fmt: skip
    streams = base64.b64decode(result.get("stdout_b64", ""))
    streams += base64.b64decode(result.get("stderr_b64", ""))
    output, code = streams.decode("utf-8", errors="replace"), result.get("exit_code")
    refused = _refusal(result)
    if refused is not None:
        return ERROR, None, refused[1], refused[0], call
    if result.get("timed_out"):
        return ERROR, None, f"killed after {check['timeout_s']} s", "timeout", call
    if code is None or code in (126, 127):
        return ERROR, code, output, "crashed", call
    return (PASSED if code == 0 else "failed"), code, output, None, call


def _capture_outputs(config: dict[str, Any], check: dict[str, Any]) -> dict[str, Any] | None:
    """Snapshot each declared output once the check has finished, before anything else can run.

    The trusted payload's own bounded no-follow walk; a refused or absent output is recorded as
    its violation, never raised. One archive folder per check run keeps every capture distinct.
    """
    declared: dict[str, str] = check.get("outputs") or {}
    if not declared:
        return None
    _supervisor(config)  # puts the trusted payload on the path
    from payload import walk  # noqa: PLC0415 - Raidar's own root-owned code, on demand

    root = Path(config["scratch"]) / "outputs"
    root.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=f"{check['name']}-", dir=root))
    limits = walk.Limits.from_spec(check.get("state_limits"))
    found: dict[str, Any] = {}
    for name, path in declared.items():
        tar = str(folder / f"{name}.tar")
        try:
            found[name] = {**walk.snapshot(path, tar, limits), "tar": tar}
        except OSError as error:
            found[name] = {"path": path, "violation": f"snapshot failed: {error}"}
    return found


def _run_package_check(config: dict[str, Any], check: dict[str, Any], scratch: Path) -> CheckResult:
    flag = check.get("closed_flag")
    if check["before"] and flag and os.path.lexists(flag):
        # Raidar's root-owned flag: the package's stop closed this checkpoint and it never reopens
        output = "the package's stop closed this checkpoint; its prerequisite no longer passes"
        return CheckResult(
            check["name"], check["kind"], True, "failed", None, 0.0, output, cause="closed"
        )
    started = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix="call-", dir=scratch))
    outcomes, pytest_kind = work / TEST_OUTCOMES, check["kind"] == "pytest"
    call: dict[str, Any] | None = None
    cause: str | None = None
    try:
        command, env = _package_command(config, check, outcomes), _package_env(check, work)
        found = shutil.which(command[0], path=env["PATH"])
        if found is None:
            status, code, cause = ERROR, None, "unrunnable"
            output = f"tool not installed: {command[0]}"
        else:
            argv = [found, *command[1:]]
            status, code, output, cause, call = _supervised(config, check, argv, env, work)
        tests = _read_outcomes(outcomes, "tests") if pytest_kind else None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if pytest_kind:
        status = _pytest_status(status, tests)
    outputs = _capture_outputs(config, check)
    seconds = round(time.monotonic() - started, 3)
    return CheckResult(
        check["name"], check["kind"], True, status, code, seconds, output[-OUTPUT_CHARS:],
        tests=tests, cause=cause, call=call, outputs=outputs,
    )  # fmt: skip


def package_checks(config: dict[str, Any], scratch: Path, *, before: bool) -> list[CheckResult]:
    """The package-contributed checks of one stage of the run (prerequisites or ordinary)."""
    scratch.mkdir(parents=True, exist_ok=True)
    return [
        _run_package_check(config, check, scratch)
        for check in config.get("package_checks", [])
        if bool(check["before"]) == before
    ]


def prerequisites(config: dict[str, Any]) -> list[CheckResult]:
    """Workflow preconditions run before the checks (as this user, like the checks)."""
    checks: list[CheckResult] = []
    for requirement in config.get("prerequisites", []):
        if requirement.get("watch_only"):
            continue  # a package check's state directory, watched here; it runs as a package check
        started = time.monotonic()
        try:
            done = subprocess.run(
                requirement["argv"],
                capture_output=True,
                text=True,
                timeout=CHECK_TIMEOUT_S,
                check=False,
            )
            status = PASSED if done.returncode == 0 else "failed"
            if done.returncode < 0 or done.returncode in (126, 127):
                status = ERROR
            code, output = done.returncode, done.stdout + done.stderr
        except (OSError, subprocess.TimeoutExpired) as exc:
            status, code, output = ERROR, None, str(exc)
        checks.append(
            CheckResult(
                requirement["name"],
                "prerequisite",
                True,
                status,
                code,
                round(time.monotonic() - started, 3),
                output[-OUTPUT_CHARS:],
                hashlib.sha256(output.encode()).hexdigest(),
            )
        )
    return checks


def decide(config: dict[str, Any], scratch: Path) -> Decision:
    """Run every check over the work tree; the verdict is the worst of them.

    Prerequisites (``before`` checks) come first and a refusal prevents every other check.
    """
    started = time.monotonic()
    required = prerequisites(config)
    if all(check.status == PASSED for check in required):
        required += package_checks(config, scratch / "before", before=True)
    if any(check.status != PASSED for check in required):
        return _verdict(required, round(time.monotonic() - started, 3))
    cache = scratch / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    files = python_files(Path(config["root"]), config["excludes"])
    checks = required + [
        _run_check(config, check, files, cache) for check in config.get("checks", [])
    ]
    checks += package_checks(config, scratch / "after", before=False)
    return _verdict(checks, round(time.monotonic() - started, 3))


def _verdict(checks: list[CheckResult], seconds: float) -> Decision:
    """Error when a check could not run; refused when a blocking check failed; else passed."""
    errors = [c for c in checks if c.status == ERROR]
    if errors:
        error = "; ".join(f"{c.name}: {c.output.strip()[-300:]}" for c in errors)
        return Decision(ERROR, seconds, checks, error)
    refused = any(c.status == "failed" and c.blocking for c in checks)
    return Decision(REFUSED if refused else PASSED, seconds, checks)


def _chain_of(previous: str, record: dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k != "chain"}
    return hashlib.sha256((previous + json.dumps(body, sort_keys=True)).encode()).hexdigest()


def chain_problem(text: str) -> str | None:
    """Why a hook log is not the log the broker wrote: a line that does not parse, is out of order
    or does not chain to the one before it (``None``: it chains). The log is root-owned and the
    broker its only writer, so the agent's user cannot rewrite it; the stage still checks it, as
    defence in depth where the file's permissions are the test's own."""
    previous = ""
    for number, line in enumerate(filter(str.strip, text.splitlines()), 1):
        try:
            record = json.loads(line)
        except ValueError:
            return f"line {number} is not JSON"
        if not isinstance(record, dict) or record.get("seq") != number:
            return f"line {number} is out of sequence"
        if record.get("chain") != _chain_of(previous, record):
            return f"line {number} does not chain to the line before it"
        previous = str(record["chain"])
    return None


def _append_line(path: Path, record: dict[str, Any]) -> None:
    """Append one chained JSON line (the broker, the log's only writer)."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    previous = ""
    if lines:
        with suppress(ValueError):
            previous = str(json.loads(lines[-1]).get("chain", ""))
    record = {**record, "seq": len(lines) + 1}
    record["chain"] = _chain_of(previous, record)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def hook(config: dict[str, Any]) -> int:
    """The pre-commit hook, run by the agent's own ``git commit``: a client of the broker.

    It asks the root-owned broker to run the gate's decision (as the agent's user), prints what the
    broker relays and exits as the broker's record says. It writes nothing itself: the hook log
    is the broker's, so the agent's user can neither forge nor rewrite it.
    """
    request = {"env": {k: os.environ[k] for k in GIT_KEPT if k in os.environ}}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(BROKER_TIMEOUT_S)
            socket_path = Path(config["broker_socket"])
            os.chdir(socket_path.parent)  # a unix socket's path is short: connect by name
            conn.connect(socket_path.name)
            conn.sendall(json.dumps(request).encode() + b"\n")
            conn.shutdown(socket.SHUT_WR)
            data = b"".join(iter(lambda: conn.recv(65536), b""))
        reply = json.loads(data)
    except (OSError, ValueError) as exc:
        sys.stdout.write(f"verify-gate: ERROR, the hook broker could not be reached: {exc}\n")
        return EXITS[ERROR]
    sys.stdout.write(str(reply.get("output", "")))
    return int(reply.get("exit", EXITS[ERROR]))


def _raw_tree(config: dict[str, Any], scratch: Path) -> str:
    """``_tree_of`` leaving no private Git directory behind."""
    try:
        return _tree_of(config, scratch)
    finally:
        for private in scratch.glob("seal-*"):
            shutil.rmtree(private, ignore_errors=True)


def hook_decide(config: dict[str, Any], record_fd: int) -> int:
    """The hook's decision, run by the broker as the agent's user (never root).

    The index tree is the commit's own (inherited ``GIT_INDEX_FILE``); the checks judge the work
    tree, so the record's ``tree`` is the commit's only when it is exactly the raw work tree that
    was checked (a commit of anything else was not gated). The record goes to the broker's pipe,
    out of reach of the checks' processes.
    """
    root, scratch = Path(config["root"]), Path(config["scratch"])
    scratch.mkdir(parents=True, exist_ok=True)
    index_tree = raw_tree = tree = worktree = None
    integrity: list[dict[str, str]] = []
    with suppress(GateError):
        index_tree = _git(root, "write-tree", env=_git_env(keep=GIT_KEPT))
    try:
        worktree = manifest_digest(worktree_manifest(root, config["excludes"]))
        raw_tree = _raw_tree(config, scratch)
        tree = index_tree if index_tree == raw_tree else None
        watched = watches(config.get("prerequisites", []))
        before = [snapshot(Path(directory)) for _, directory, _ in watched]
        sessions = [snapshot(Path(directory)) for directory in config.get("session_paths", [])]
        decision = decide(config, scratch / "hook")
        after = [snapshot(Path(directory)) for _, directory, _ in watched]
        integrity += [
            finding
            for was in sessions
            for finding in session_findings(was, snapshot(Path(was["dir"])), "the hook run")
        ]
        for (label, _, allowed), was, now in zip(watched, before, after, strict=True):
            integrity += witness_findings(label, was, now, allowed, "the hook run")
    except Exception as exc:  # noqa: BLE001 - a gate that cannot run is an error, never a pass
        decision = Decision(ERROR, 0.0, error=f"{type(exc).__name__}: {exc}")
    sys.stdout.write(decision.text())
    record = {
        "verdict": decision.verdict,
        "tree": tree,
        "index_tree": index_tree,
        "worktree_tree": raw_tree,
        "worktree_sha256": worktree,
        "seconds": decision.seconds,
        "checks": decision.summary(),
        "error": decision.error,
        "integrity": integrity,
        "process": process_facts(),
    }
    with os.fdopen(record_fd, "w", encoding="utf-8") as pipe:
        pipe.write(json.dumps(record))
    return EXITS[decision.verdict]


def _peer_uid(conn: socket.socket) -> int | None:
    with suppress(OSError, AttributeError):
        peercred = getattr(socket, "SO_PEERCRED", None)
        if peercred is None:
            return None
        creds = conn.getsockopt(socket.SOL_SOCKET, peercred, struct.calcsize("3i"))
        return int(struct.unpack("3i", creds)[1])
    return None


def _read_all(descriptor: int) -> bytes:
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read()


def _hook_child(config: dict[str, Any], config_path: Path, env: dict[str, str]) -> tuple[str, Any]:
    """Run ``hook-decide`` as the agent's user; its printed text and its record."""
    uid, gid = int(config["agent_uid"]), int(config["agent_gid"])
    drop: dict[str, Any] = {}
    if os.getuid() == 0:
        drop = {"user": uid, "group": gid, "extra_groups": []}
    reader, writer = os.pipe()
    child_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": config["agent_home"],
        "USER": config["agent_name"], "LOGNAME": config["agent_name"], "LANG": "C.UTF-8",
        **env,
    }  # fmt: skip
    command = [
        sys.executable, "-B", str(Path(__file__).resolve()), "hook-decide",
        "--config", str(config_path), "--record-fd", str(writer),
    ]  # fmt: skip
    collected: list[bytes] = []
    proc = subprocess.Popen(
        command, cwd=config["root"], env=child_env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, pass_fds=(writer,), **drop,
    )  # fmt: skip
    os.close(writer)
    thread = threading.Thread(target=lambda: collected.append(_read_all(reader)))
    thread.start()
    try:
        out, _ = proc.communicate(timeout=BROKER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    thread.join(timeout=5)
    text = out.decode("utf-8", errors="replace")
    try:
        record = json.loads(b"".join(collected))
    except ValueError:
        record = None
    return text, record


def _handle(conn: socket.socket, config: dict[str, Any], config_path: Path, lock: Any) -> None:
    with conn:
        conn.settimeout(30)
        raw = b""
        with suppress(OSError):
            while not raw.endswith(b"\n") and len(raw) < BROKER_REQUEST_LIMIT:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                raw += chunk
        try:
            asked = json.loads(raw)
            env = {k: v for k, v in asked["env"].items() if k in GIT_KEPT and isinstance(v, str)}
        except (ValueError, KeyError, AttributeError, TypeError):
            conn.sendall(json.dumps({"error": "malformed request", "exit": 126}).encode())
            return
        text, record = _hook_child(config, config_path, env)
        verdict = record.get("verdict") if isinstance(record, dict) else None
        if verdict not in EXITS:
            record = {"verdict": ERROR, "error": "the hook decision produced no record"}
            verdict = ERROR
        record.update(who="hook", at=_now(), exit=EXITS[verdict], peer_uid=_peer_uid(conn))
        record["broker"] = process_facts()
        with lock:
            _append_line(Path(config["hook_state"]) / HOOK_RUNS, record)
        with suppress(OSError):
            conn.sendall(json.dumps({"output": text, "exit": EXITS[verdict]}).encode())


def broker(config: dict[str, Any], config_path: Path) -> dict[str, Any]:
    """Start the hook broker as a daemon and return once it listens.

    Started by the stage as root, from root-owned files: it runs no agent code itself. For each
    request it runs the gate's decision as the agent's user (uid and gid dropped before exec) and
    is the only writer of the hook log, which stays root-owned and read-only to the agent.
    """
    socket_path = Path(config["broker_socket"])
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    pid = os.fork()
    if pid:
        for _ in range(100):
            if socket_path.exists():
                return {"pid": pid, "socket": str(socket_path), "process": process_facts()}
            time.sleep(0.1)
        return {"error": "the broker did not start", "process": process_facts()}
    os.setsid()
    null = os.open(os.devnull, os.O_RDWR)
    for descriptor in (0, 1, 2):
        os.dup2(null, descriptor)
    (socket_path.parent / BROKER_PID).write_text(str(os.getpid()))
    lock = threading.Lock()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    os.chdir(socket_path.parent)
    server.bind(socket_path.name)
    socket_path.chmod(0o666)  # any user may ask; only the broker writes the log
    server.listen(8)
    while True:
        conn, _ = server.accept()
        threading.Thread(target=_handle, args=(conn, config, config_path, lock)).start()


def broker_stop(config: dict[str, Any]) -> dict[str, Any]:
    pid_file = Path(config["broker_socket"]).parent / BROKER_PID
    stopped = False
    with suppress(OSError, ValueError, ProcessLookupError):
        os.kill(int(pid_file.read_text()), signal.SIGKILL)
        stopped = True
    Path(config["broker_socket"]).unlink(missing_ok=True)
    return {"stopped": stopped, "process": process_facts()}


def snapshot(directory: Path) -> dict[str, Any]:
    """Each file under ``directory`` (links not followed): its digest, size and ``ctime``.

    The stage takes it before and after a seal, as the agent's user, and keeps both host-side: a
    change, or a bumped ``ctime`` on unchanged bytes (a write and restore), is a violation.
    """
    found: dict[str, dict[str, Any]] = {}
    for current, _, names in os.walk(directory):
        for name in sorted(names):
            path = Path(current) / name
            info = path.lstat()
            entry: dict[str, Any] = {"ctime_ns": info.st_ctime_ns, "size": info.st_size}
            if stat.S_ISREG(info.st_mode):
                entry["sha256"] = _sha256_file(path)
            found[path.relative_to(directory).as_posix()] = entry
    return {"dir": str(directory), "present": directory.is_dir(), "files": found}


def watches(prerequisites: Sequence[dict[str, Any]]) -> list[tuple[str, str, set[str]]]:
    """The directories workflow prerequisites keep state in: a prerequisite's name's first part,
    its ``watch`` directory and the basenames it legitimately writes during a gate run."""
    return [
        (str(item["name"]).split(".")[0], str(item["watch"]), set(item.get("writes", [])))
        for item in prerequisites
        if item.get("watch")
    ]


def _prefix_sha256(path: Path, size: int) -> str | None:
    try:
        with path.open("rb") as handle:
            return hashlib.sha256(handle.read(size)).hexdigest()
    except OSError:
        return None


def session_findings(
    before: dict[str, Any], after: dict[str, Any], where: str
) -> list[dict[str, str]]:
    """What a gate run did to the native session store: a file removed, or whose first bytes (at
    the size first seen) changed, or written without changing (a later ``ctime`` on identical
    bytes and size). Appending is what a session legitimately does."""
    found: list[dict[str, str]] = []
    now = after.get("files") or {}
    for name, was in sorted((before.get("files") or {}).items()):
        if "sha256" not in was:
            continue
        current = now.get(name)
        path = Path(before["dir"]) / name
        if current is None:
            kind, detail = "removed", f"{name} was removed during the gate"
        elif _prefix_sha256(path, int(was["size"])) != was["sha256"]:
            kind, detail = "rewritten", f"{name} no longer begins with the bytes first seen"
        elif current.get("size") == was["size"] and current.get("ctime_ns") != was["ctime_ns"]:
            kind, detail = "touched", f"{name} was written, with the same bytes, during the gate"
        else:
            continue
        found.append({"name": f"session_log_{kind}", "after": where, "detail": detail})
    return found


def witness_findings(
    label: str,
    before: dict[str, Any],
    after: dict[str, Any],
    allowed: set[str],
    where: str,
) -> list[dict[str, str]]:
    """What a gate run (the agent's pytest and conftest ran in it) did to a watched state
    directory: a file added, removed or changed (``<label>_state_rewritten``) or written without
    changing its bytes, which a write and restore leaves as a later ``ctime``
    (``<label>_state_touched``). ``allowed`` basenames are the prerequisite's own writes."""
    was, now = before.get("files") or {}, after.get("files") or {}
    found: list[dict[str, str]] = []
    for name in sorted({*was, *now}):
        if Path(name).name in allowed:
            continue
        old, new = was.get(name), now.get(name)
        if old is None or new is None or old.get("sha256") != new.get("sha256"):
            kind, detail = "rewritten", f"{name} was added, removed or changed during the gate"
        elif old.get("ctime_ns") != new.get("ctime_ns"):
            kind, detail = "touched", f"{name} was written, with the same bytes, during the gate"
        else:
            continue
        found.append({"name": f"{label}_state_{kind}", "after": where, "detail": detail})
    return found


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def workspace_setup(config: dict[str, Any]) -> dict[str, Any]:
    """The workspace as a git repository with the rules in place, as the agent's user, before the
    agent starts. Every git call is the gate's sanitized one (no system or global configuration,
    ``core.fsmonitor=``, the gate-owned empty hooks directory) and the base commit is a raw tree
    (no clean, smudge or process filter is run), so nothing the repository configures executes."""
    root, scratch = Path(config["root"]), _scratch(config)
    git = root / ".git"
    if git.exists() and not git.is_dir():
        raise GateError("the workspace's .git is not a repository directory")
    created = not git.is_dir()
    if created:
        _git(root, "init", "-q", "--template=")

    def local(*args: str) -> str:
        return _repo_git(root, "config", "--local", *args)

    local("core.hooksPath", config["hooks"])
    for key, value in (("user.name", "raidar-agent"), ("user.email", "agent@raidar.invalid")):
        if _repo_git_or_none(root, "config", "--local", "--get", key) is None:
            local(key, value)
    _workspace_rules(config, root, git)
    if _repo_git_or_none(root, "rev-parse", "-q", "--verify", "HEAD") is None:
        _base_commit(config, root, git, scratch)
    return {"base": _repo_git(root, "rev-parse", "HEAD"), "repo_created": created}


def _workspace_rules(config: dict[str, Any], root: Path, git: Path) -> None:
    if not config.get("rules_file"):  # a gate of package checks keeps no rules block
        _write_text(git / "info" / "exclude", "".join(f"{n}/\n" for n in config["excludes"]))
        return
    rules = root / config["rules_file"]
    if rules.is_symlink():
        raise GateError(f"{config['rules_file']} is a symlink")
    if rules.is_file():
        strip_file(rules, False)
    else:
        _write_text(rules, "")
    block = Path(config["rules_record"]).read_text(encoding="utf-8")
    with rules.open("a", encoding="utf-8") as handle:
        handle.write("\n" + block)
    owned = bool(config["rules_owned"])
    exclude = [f"{name}/" for name in config["excludes"]]
    if owned:
        exclude.append(f"/{config['rules_file']}")
    _write_text(git / "info" / "exclude", "\n".join(exclude) + "\n")
    if not owned:
        _write_text(git / "info" / "attributes", f"/{config['rules_file']} filter=raidar-rules\n")
        _repo_git(root, "config", "--local", "filter.raidar-rules.clean", config["strip_command"])


def _base_commit(config: dict[str, Any], root: Path, git: Path, scratch: Path) -> None:
    """Commit the workspace before the agent as a raw tree built in the repository's own object
    store through a temporary index, never ``git add`` (which would run configured filters)."""
    index = scratch / "setup-index"
    index.unlink(missing_ok=True)
    env = _git_env(
        extra={"GIT_DIR": str(git), "GIT_WORK_TREE": str(root), "GIT_INDEX_FILE": str(index)}
    )
    tree = _tree_of(config, scratch, env)
    author = {
        "GIT_AUTHOR_NAME": "raidar-agent", "GIT_AUTHOR_EMAIL": "agent@raidar.invalid",
        "GIT_COMMITTER_NAME": "raidar-agent", "GIT_COMMITTER_EMAIL": "agent@raidar.invalid",
    }  # fmt: skip
    message = "raidar: the workspace before the agent"
    _git(root, "hash-object", "-t", "tree", "-w", "--stdin", stdin=b"", env=env)  # the empty tree
    commit = _git(root, "commit-tree", tree, "-m", message, env={**env, **author})
    _git(root, "update-ref", "HEAD", commit, env=env)
    index.unlink(missing_ok=True)


def _commits(root: Path, base: str) -> tuple[list[dict[str, str]], bool]:
    """Every commit from ``base`` (exclusive) to ``HEAD``, oldest first; and whether ``base``
    is still an ancestor of ``HEAD`` (history the agent rewrote cannot be read)."""
    if _repo_git_or_none(root, "merge-base", "--is-ancestor", base, "HEAD") is None:
        return [], False
    log = _repo_git(
        root, "log", "--no-ext-diff", "--no-textconv", "--reverse", "--format=%H %T %s",
        f"{base}..HEAD",
    )  # fmt: skip
    rows = [line.split(" ", 2) for line in log.splitlines() if line]
    return [
        {"commit": row[0], "tree": row[1], "subject": row[2] if len(row) > 2 else ""}
        for row in rows
    ], True


def _private_git(config: dict[str, Any], scratch: Path) -> tuple[Path, dict[str, str]]:
    """A Git directory of the gate's own, reading the agent's objects and nothing else of it."""
    root = Path(config["root"])
    git_dir = Path(tempfile.mkdtemp(prefix="seal-", dir=scratch))
    _git(scratch, "init", "-q", "--bare", "--template=", str(git_dir), env=_git_env())
    objects = root / ".git" / "objects"
    if (root / ".git").is_symlink() or not objects.is_dir():
        raise GateError("the workspace's .git is not a repository directory")
    (git_dir / "objects" / "info").mkdir(parents=True, exist_ok=True)
    (git_dir / "objects" / "info" / "alternates").write_text(f"{objects}\n", encoding="utf-8")
    env = _git_env(
        extra={
            "GIT_DIR": str(git_dir), "GIT_WORK_TREE": str(root),
            "GIT_INDEX_FILE": str(git_dir / "index"),
        }
    )  # fmt: skip
    return git_dir, env


def _untracked_names(config: dict[str, Any], root: Path, env: dict[str, str]) -> list[str]:
    """Every file of the work tree the Git ignore rules, the exclusions and the owned rules file
    leave in, sorted."""
    excludes = [f"--exclude={name}/" for name in config["excludes"]]
    rules = config.get("rules_file")
    if rules and config.get("rules_owned"):
        excludes.append(f"--exclude=/{rules}")
    listed = _git(
        root, "ls-files", "-z", "--others", "--exclude-standard", *excludes, env=env
    ).split("\0")
    return sorted(item for item in listed if item and not item.endswith("/"))


def _is_special(name: str, kind: str, rules: str | None) -> bool:
    """Whether a file is hashed on its own rather than through ``--stdin-paths``."""
    return kind == "link" or "\n" in name or name.startswith('"') or name == rules


def _special_bytes(config: dict[str, Any], path: Path, name: str, kind: str) -> bytes:
    """The raw bytes of a link target or of the rules file (stripped when the task owned it)."""
    if kind == "link":
        return os.fsencode(os.readlink(path))
    data = path.read_bytes()
    if name == config.get("rules_file") and not config.get("rules_owned"):
        data = strip_rules(data.decode("utf-8", errors="replace")).encode()
    return data


def _hash_blobs(
    config: dict[str, Any], root: Path, env: dict[str, str], names: list[str], kinds: dict[str, str]
) -> dict[str, str]:
    """The blob of every file, written raw into the private Git directory."""
    rules = config.get("rules_file")
    special = {name for name in names if _is_special(name, kinds[name], rules)}
    blobs: dict[str, str] = {}
    batch = [name for name in names if name not in special]
    if batch:  # one git process for every plain file
        hashed = _git(
            root, "hash-object", "-w", "--no-filters", "--stdin-paths",
            stdin="\n".join(batch).encode() + b"\n", env=env,
        ).splitlines()  # fmt: skip
        blobs.update(zip(batch, hashed, strict=True))
    for name in sorted(special):
        data = _special_bytes(config, root / name, name, kinds[name])
        blobs[name] = _git(
            root, "hash-object", "-w", "--no-filters", "--stdin", stdin=data, env=env
        )
    return blobs


def _tree_of(config: dict[str, Any], scratch: Path, env: dict[str, str] | None = None) -> str:
    """The Git tree of the work tree as it is: raw bytes, no filter, attribute or hook of the
    agent's repository involved, built in a private Git directory (the stage's rules block is
    taken out of the rules file the task already had)."""
    root = Path(config["root"])
    if env is None:
        _, env = _private_git(config, scratch)
    names = _untracked_names(config, root, env)
    kinds = {name: _entry_identity(root / name)[0] for name in names}
    blobs = _hash_blobs(config, root, env, names, kinds)
    entries = [f"{FILE_MODES[kinds[name]]} {blobs[name]}\t{name}" for name in names]
    if not entries:
        return EMPTY_TREE
    index = "\0".join(entries) + "\0"
    _git(root, "update-index", "--add", "-z", "--index-info", stdin=index.encode(), env=env)
    return _git(root, "write-tree", env=env)


def seal(config: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """The authoritative record of the work after a turn; changes nothing of the agent's."""
    root, scratch = Path(config["root"]), Path(config["scratch"])
    scratch.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    record: dict[str, Any] = {
        "who": "seal", "run": args.run, "after": args.after, "at": _now(),
        "verdict": ERROR, "exit": EXITS[ERROR], "tree": None, "checks": [], "output": "",
        "error": None, "process": process_facts(),
    }  # fmt: skip
    try:
        record["head"] = _repo_git(root, "rev-parse", "HEAD")
        record["hooks_path"] = _repo_git_or_none(
            root, "config", "--local", "--get", "core.hooksPath"
        )
        record["commits"], record["base_is_ancestor"] = _commits(root, args.base)
        before = worktree_manifest(root, config["excludes"])
        record["worktree_sha256"] = manifest_digest(before)
        record["tree"] = _raw_tree(config, scratch)
        decision = decide(config, scratch / "seal")
        record["checks"] = decision.summary()
        record["output"], record["error"] = decision.text(), decision.error
        record["verdict"], record["exit"] = decision.verdict, EXITS[decision.verdict]
        # the checks run the agent's code: the work tree the agent leaves is read again, and is
        # what is graded; nothing it changed is put back
        after = worktree_manifest(root, config["excludes"])
        record["files"] = after
        record["final_worktree_sha256"] = manifest_digest(after)
        record["final_tree"] = _raw_tree(config, scratch)
        record["changed_during_gate"] = sorted(
            p for p in {*before, *after} if before.get(p) != after.get(p)
        )
    except Exception as exc:  # noqa: BLE001 - recorded as a gate that could not run
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["output"] += f"verify-gate: ERROR, the gate could not run: {record['error']}\n"
    record["seconds"] = round(time.monotonic() - started, 3)
    return record


def _ancestors() -> set[int]:
    found, pid = set(), os.getpid()
    while pid > 1 and pid not in found:
        found.add(pid)
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            pid = int(fields[1])
        except (OSError, ValueError, IndexError):
            break
    return found


def reap() -> dict[str, Any]:
    """Kill every other process of this user: the agent's leftovers (never as root)."""
    record: dict[str, Any] = {"process": process_facts(), "killed": [], "remaining": []}
    me = os.getuid()
    if me == 0:
        record["error"] = "refused: reaping is the agent user's own processes, never root's"
        return record
    keep = _ancestors()

    def mine() -> list[int]:
        found = []
        for entry in Path("/proc").glob("[0-9]*"):
            with suppress(OSError):
                if entry.stat().st_uid == me and int(entry.name) not in keep:
                    found.append(int(entry.name))
        return found

    for _ in range(5):
        pids = mine()
        if not pids:
            break
        for pid in pids:
            with suppress(OSError):
                os.kill(pid, signal.SIGKILL)
                record["killed"].append(pid)
        time.sleep(0.2)
    record["remaining"] = mine()
    return record


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _version(command: list[str], cwd: Path) -> str:
    done = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, timeout=60, check=False,
        stdin=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin", "HOME": str(cwd)},
    )  # fmt: skip
    if done.returncode != 0:
        raise GateError(f"exit {done.returncode}: {(done.stderr or done.stdout).strip()[-200:]}")
    return (done.stdout or done.stderr).strip()


def _untrusted_entries(top: Path, trusted_uid: int) -> list[str]:
    """Entries under ``top`` not owned by the trusted user or writable by anyone else."""
    bad: list[str] = []
    for current, dirs, names in os.walk(top):
        for name in [*dirs, *names]:
            path = Path(current) / name
            info = path.lstat()
            if info.st_uid != trusted_uid or (
                not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022
            ):
                bad.append(str(path))
    return bad[:5]


def _scratch(config: dict[str, Any]) -> Path:
    path = Path(config["scratch"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def _check_not_root(config: dict[str, Any]) -> str:
    if os.getuid() == 0 or os.geteuid() == 0:
        raise GateError(f"the gate runs as uid {os.getuid()}: the agent's user is not root")
    return f"uid {os.getuid()} gid {os.getgid()}"


def _check_interpreter(config: dict[str, Any]) -> str:
    python = Path(config["python"] if "python" in config else f"{config['tools']}/bin/python")
    if not os.access(python, os.X_OK):
        raise GateError(f"no tools interpreter at {python}")
    real = python.resolve()
    version = _version([str(python), "-I", "-B", "--version"], _scratch(config))
    return f"{version} {real} {_sha256_file(real)}"


def _check_package_tool(config: dict[str, Any], check: dict[str, Any]) -> str:
    """A package check's command resolves on its own PATH (the installed tool, not run here)."""
    command = _package_command(config, check, _scratch(config) / TEST_OUTCOMES)[0]
    found = shutil.which(command, path=check["path"])
    if found is None:
        raise GateError(f"tool not installed: {command}")
    return f"{found} {_sha256_file(Path(found).resolve())}"


def _check_tool(config: dict[str, Any], check: dict[str, Any]) -> str:
    tools, scratch = Path(config["tools"]), _scratch(config)
    if check["category"] == "tests":
        code = "import pytest; print(pytest.__version__)"
        version = _version([str(tools / "bin" / "python"), "-I", "-B", "-c", code], scratch)
        return f"pytest {version}"
    binary = tools / "bin" / check["argv"][0]
    if not os.access(binary, os.X_OK):
        raise GateError(f"tool not installed: {binary}")
    return f"{_version([str(binary), '--version'], scratch)} {_sha256_file(binary.resolve())}"


def _check_ownership(config: dict[str, Any]) -> str:
    home, trusted = Path(config["home"]), int(config["trusted_uid"])
    bad = _untrusted_entries(home, trusted)
    if bad:
        raise GateError(f"not owned by uid {trusted} or writable by others: {bad}")
    return f"{home} owned by uid {trusted}, writable by no one else"


def _check_payload(config: dict[str, Any]) -> str:
    name = config.get("payload_name", "verify_gate.py")
    digest = _sha256_file(Path(config["home"]) / name)
    if digest != config["payload_sha256"]:
        wanted = config["payload_sha256"]
        raise GateError(f"the installed gate is {digest[:12]}, not {wanted[:12]}")
    return f"{name} {digest[:12]}"


def _check_hook(config: dict[str, Any]) -> str:
    hooks = Path(config["home"]) / "hooks"
    script = hooks / "pre-commit"
    if not os.access(script, os.X_OK) or _sha256_file(script) != config["hook_sha256"]:
        raise GateError(f"{script} is missing, not executable or not the stage's hook")
    repository = Path(config["root"])
    configured = _repo_git(repository, "config", "--local", "--get", "core.hooksPath")
    if configured != str(hooks):
        raise GateError(f"core.hooksPath is {configured!r}")
    return f"core.hooksPath {configured}, hook {config['hook_sha256'][:12]}"


def _check_rules(config: dict[str, Any]) -> str:
    if not config.get("rules_file"):
        return "no rules file"
    text = (Path(config["root"]) / config["rules_file"]).read_text(encoding="utf-8")
    if RULES_BEGIN not in text or RULES_END not in text:
        raise GateError(f"{config['rules_file']} holds no rules block")
    return f"{config['rules_file']} holds the rules block"


def _check_scratch(config: dict[str, Any]) -> str:
    probe = _scratch(config) / ".probe"
    probe.write_text("x", encoding="utf-8")
    probe.unlink()
    return "scratch writable"


def _check_broker(config: dict[str, Any]) -> str:
    socket_path, trusted = Path(config["broker_socket"]), int(config["trusted_uid"])
    log = Path(config["hook_state"]) / HOOK_RUNS
    if not socket_path.is_socket():
        raise GateError(f"no hook broker listens at {socket_path}")
    info = log.stat()
    if info.st_uid != trusted or info.st_mode & 0o022:
        raise GateError(f"{log} is not the broker's: uid {info.st_uid}, mode {info.st_mode:o}")
    return f"hook broker at {socket_path}; hook log owned by uid {trusted}, no one else writes it"


def _check_git(config: dict[str, Any]) -> str:
    return _version(["git", "--version"], _scratch(config))


SetupCheck = Callable[[dict[str, Any]], str]


def _setup_step(name: str, action: SetupCheck, config: dict[str, Any]) -> dict[str, str]:
    try:
        return {"name": name, "status": PASSED, "detail": str(action(config))}
    except Exception as exc:  # noqa: BLE001 - one named failure, the rest still run
        detail = f"{type(exc).__name__}: {exc}"
        return {"name": name, "status": "failed", "detail": detail}


SETUP_HEAD: tuple[tuple[str, SetupCheck], ...] = (
    ("agent_user_not_root", _check_not_root),
    ("interpreter", _check_interpreter),
)
SETUP_TAIL: tuple[tuple[str, SetupCheck], ...] = (
    ("tool_ownership", _check_ownership),
    ("gate_payload", _check_payload),
    ("hook_installed", _check_hook),
    ("rules_block", _check_rules),
    ("state_writable", _check_scratch),
    ("hook_broker", _check_broker),
    ("git", _check_git),
)


def setup_check(config: dict[str, Any]) -> dict[str, Any]:
    """Deterministic setup facts, from root-owned installs only: no workspace code is imported or
    run (no workspace configuration, conftest or interpreter) and nothing is written."""
    tools: list[tuple[str, SetupCheck]] = [
        (f"tool:{check['name']}", partial(_check_tool, check=check))
        for check in config.get("checks", [])
    ]
    tools += [
        (f"tool:{check['name']}", partial(_check_package_tool, check=check))
        for check in config.get("package_checks", [])
    ]
    steps: list[tuple[str, SetupCheck]] = [*SETUP_HEAD, *tools, *SETUP_TAIL]
    results = [_setup_step(name, action, config) for name, action in steps]
    failed = any(item["status"] != PASSED for item in results)
    return {"status": "failed" if failed else PASSED, "checks": results, "process": process_facts()}


def strip_rules(text: str) -> str:
    """``text`` without the rules block and the one newline the stage put before it.

    The stage appends ``"\\n" + block`` to a file (to an empty text when it creates the file),
    so stripping gives back the file's own bytes.
    """
    start = text.find(RULES_BEGIN)
    end = text.find(RULES_END, start)
    if start < 0 or end < 0:
        return text
    end += len(RULES_END)
    end += text[end : end + 1] == "\n"
    start -= start > 0 and text[start - 1] == "\n"
    return text[:start] + text[end:]


def strip_file(path: Path, remove_empty: bool) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        return {"path": str(path), "present": False}
    stripped = strip_rules(path.read_text(encoding="utf-8", errors="replace"))
    if remove_empty and not stripped.strip():
        path.unlink()
        return {"path": str(path), "present": False, "removed": True}
    path.write_text(stripped, encoding="utf-8")
    return {"path": str(path), "present": True, "removed": False}


class TestOutcomes:
    """A pytest plugin: each collected node id and its result (the worst of its phases)."""

    __test__ = False  # not a pytest test class

    def __init__(self, target: Path | None) -> None:
        self.target = target
        self.collected: list[str] = []
        self.results: dict[str, str] = {}

    def pytest_collection_modifyitems(self, items: Sequence[Any]) -> None:
        self.collected = [item.nodeid for item in items]

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.when != "call" and report.outcome == PASSED:
            return
        if self.results.get(report.nodeid) != "failed":
            self.results[report.nodeid] = "skipped" if report.skipped else report.outcome

    def pytest_sessionfinish(self) -> None:
        if self.target is not None:
            document = {
                "collected": self.collected,
                "outcomes": self.results,
                "uid": os.getuid(),
            }
            self.target.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")


def pytest_run(
    timeout: int,
    sites: Sequence[str],
    args: Sequence[str],
    outcomes: Path | None = None,
    *,
    agent_user: bool = False,
) -> int:
    if agent_user and os.getuid() == 0:
        sys.stderr.write("pytest-run: the pinned pytest runs as the agent's user, not root\n")
        return EXITS[ERROR]
    for path in sites:
        site.addsitedir(path)
    sys.path[0] = os.getcwd()
    import pytest  # noqa: PLC0415 - from the tools environment, only in this subcommand

    class StopSlowTests:
        @pytest.hookimpl(wrapper=True)
        def pytest_runtest_call(self) -> Generator[None, object, object]:
            def expire(signum: int, frame: object) -> None:
                pytest.fail(f"the test ran longer than {timeout} s", pytrace=False)

            previous = signal.signal(signal.SIGALRM, expire)
            signal.alarm(timeout)
            try:
                return (yield)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, previous)

    return int(pytest.main(list(args), plugins=[StopSlowTests(), TestOutcomes(outcomes)]))


def baseline(config: dict[str, Any]) -> dict[str, Any]:
    """Run the tests check once over the work tree, as the gate does, and report it.

    The verify stage runs it before the agent starts: the tests that pass on the base are what a
    ``Kind: regression`` criterion's mapped tests are compared with.
    """
    check = next(item for item in config["checks"] if item["category"] == "tests")
    cache = Path(config["scratch"]) / "baseline" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    result = asdict(_run_check(config, check, [], cache))
    root = Path(config["root"])
    manifest = worktree_manifest(root, config["excludes"])  # the base the tests ran over
    return {
        **result,
        "worktree_sha256": manifest_digest(manifest),
        "tree": _raw_tree(config, Path(config["scratch"])),
        "process": process_facts(),
    }


def canary(config: dict[str, Any]) -> dict[str, Any]:
    """The gate's own run over the starting workspace: its verdict and every check's result."""
    scratch = _scratch(config)
    decision = decide(config, scratch / "canary")
    manifest = worktree_manifest(Path(config["root"]), config["excludes"])
    return {
        "verdict": decision.verdict,
        "exit": EXITS[decision.verdict],
        "checks": decision.summary(),
        "output": decision.text(),
        "error": decision.error,
        "worktree_sha256": manifest_digest(manifest),
        "tree": _raw_tree(config, scratch),
        "process": process_facts(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="verify-gate")
    commands = parser.add_subparsers(dest="command", required=True)
    names = ("hook", "seal", "baseline", "canary", "setup-check", "workspace-setup", "reap")
    for name in (*names, "broker", "broker-stop"):
        command = commands.add_parser(name)
        if name != "reap":
            command.add_argument("--config", type=Path, required=True)
    deciding = commands.add_parser("hook-decide")
    deciding.add_argument("--config", type=Path, required=True)
    deciding.add_argument("--record-fd", type=int, required=True)
    snapshotting = commands.add_parser("snapshot")
    snapshotting.add_argument("--dir", type=Path, required=True)
    sealing = commands.choices["seal"]
    sealing.add_argument("--run", type=int, required=True)
    sealing.add_argument("--after", required=True)
    sealing.add_argument("--base", required=True)
    stripping = commands.add_parser("strip-rules")
    stripping.add_argument("--file", type=Path)
    stripping.add_argument("--remove-empty", action="store_true")
    testing = commands.add_parser("pytest-run")
    testing.add_argument("--timeout", type=int, required=True)
    testing.add_argument("--site", action="append", default=[])
    testing.add_argument("--outcomes", type=Path)
    testing.add_argument("--agent-user", action="store_true")
    testing.add_argument("args", nargs="*")
    return parser


def _emit(value: dict[str, Any]) -> int:
    print(json.dumps(value, sort_keys=True))
    return 0


def _strip(args: argparse.Namespace) -> int:
    if args.file is None:
        sys.stdout.write(strip_rules(sys.stdin.read()))
        return 0
    return _emit(strip_file(args.file, args.remove_empty))


STANDALONE: dict[str, Callable[[argparse.Namespace], int]] = {
    "pytest-run": lambda a: pytest_run(
        a.timeout, a.site, a.args, a.outcomes, agent_user=a.agent_user
    ),
    "strip-rules": _strip,
    "reap": lambda a: _emit(reap()),
    "snapshot": lambda a: _emit(snapshot(a.dir)),
}
"""Subcommands that read no gate configuration."""
CONFIGURED: dict[str, Callable[[dict[str, Any], argparse.Namespace], int]] = {
    "hook": lambda c, a: hook(c),
    "hook-decide": lambda c, a: hook_decide(c, a.record_fd),
    "broker": lambda c, a: _emit(broker(c, a.config)),
    "broker-stop": lambda c, a: _emit(broker_stop(c)),
    "baseline": lambda c, a: _emit(baseline(c)),
    "canary": lambda c, a: _emit(canary(c)),
    "setup-check": lambda c, a: _emit(setup_check(c)),
    "workspace-setup": lambda c, a: _emit(workspace_setup(c)),
    "seal": lambda c, a: _emit(seal(c, a)),
}
"""Subcommands run on the gate's configuration file."""


def _protect_record_channel() -> None:
    """A check must not reopen the trusted parent's stdout or record pipe through procfs.

    Linux resets dumpability on exec, so every gate process sets it before spawning checks.
    The hook's pipe is additionally created and owned by the root broker.
    """
    if sys.platform != "linux":
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE
        raise OSError(ctypes.get_errno(), "cannot protect the gate record channel")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    os.umask(0o022)  # the container's exec umask is 0: nothing the gate writes is world-writable
    _protect_record_channel()
    if args.command in STANDALONE:
        return STANDALONE[args.command](args)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    GATE_HOOKS[0] = config.get("empty_hooks", GATE_HOOKS[0])
    return CONFIGURED[args.command](config, args)


if __name__ == "__main__":
    sys.exit(main())
