"""``verify-gate``: the verify stage's gate, run inside the trial container.

The verify stage (``raidar.subject.workflow.verify``) uploads this file beside its tools
environment and runs it on that environment's interpreter, so it imports only the standard
library (and ``pytest`` from the tools environment, in ``pytest-run``). Its configuration is
one JSON file the stage writes (``root``, ``tools``, ``excludes``, ``test_timeout``,
``checks``, ``hook_state``, ``seal_state``). Subcommands:

- ``hook``: the workspace's ``pre-commit`` hook, run by the agent's own ``git commit`` as the
  agent user. It decides the work tree, appends one JSON line (``who: hook``, the verdict, the
  gate's exit, the index tree, checked workspace digest and per-check results) to
  ``hook-runs.jsonl`` in the hook's state directory, prints what it found and exits 0 (passed),
  1 (refused) or 126
  (the gate could not run). It decides nothing for the runner: the seal does.
- ``seal``: the runner's own commit after a turn, as root (the authoritative verdict). It
  stages everything (``git add -A``, the workspace's excludes left out), writes the staged
  tree, reuses a previous verdict only for the same tree and checked workspace, else runs the
  checks; a pass is committed (the gated tree itself, so what is committed is what was
  checked), a refusal leaves the work staged and ``HEAD`` where it was. It prints one JSON
  record, with the repository's evidence for the bypass check: ``core.hooksPath`` and every
  commit since the checkpoint's base.
- ``restore``: after a cut, stops a running seal and the agent user's processes and puts the
  work tree back to a tree a seal decided.
- ``strip-rules``: removes the stage's rules block from a file (or from standard input, as a
  git clean filter), so the block never reaches a commit or the graded workspace.
- ``pytest-run``: the tests check's pytest, on the tools environment with the workspace's own
  site-packages after its own, every test stopped after ``test_timeout`` seconds.

A check whose tool cannot be started, is killed or exceeds ``CHECK_TIMEOUT_S`` makes the gate
an error (126): never a pass and never a refusal. Any other non-zero exit is the check failing
on the work. Every root-run command leaves ``root`` owned by the agent user.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import signal
import site
import stat
import subprocess
import sys
import time
from collections.abc import Generator, Iterator, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PASSED, REFUSED, ERROR = "passed", "refused", "error"
EXITS = {PASSED: 0, REFUSED: 1, ERROR: 126}
HOOK_RUNS = "hook-runs.jsonl"
SEAL_PID = "seal.pid"
CHECK_TIMEOUT_S = 600
OUTPUT_CHARS = 20_000
RULES_BEGIN = "<!-- raidar verify rules: begin -->"
RULES_END = "<!-- raidar verify rules: end -->"
SEAL_AUTHOR = {
    "GIT_AUTHOR_NAME": "raidar-verify-seal",
    "GIT_AUTHOR_EMAIL": "seal@raidar.invalid",
    "GIT_COMMITTER_NAME": "raidar-verify-seal",
    "GIT_COMMITTER_EMAIL": "seal@raidar.invalid",
}
KEPT_ENV = ("HOME", "LANG", "LC_ALL", "TMPDIR", "USER", "LOGNAME")


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


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    """Run git in ``root`` (any owner) and return its stripped output; raise on failure."""
    done = subprocess.run(
        ["git", "-c", "safe.directory=*", *args],
        cwd=root,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        check=False,
    )
    if done.returncode != 0:
        raise GateError(f"git {args[0]}: {(done.stderr or done.stdout).strip()}")
    return done.stdout.strip()


def _git_or_none(root: Path, *args: str) -> str | None:
    try:
        return _git(root, *args)
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


def _entry_identity(path: Path) -> tuple[bytes, bytes]:
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode):
        return b"link", os.fsencode(os.readlink(path))
    if not stat.S_ISREG(mode):
        raise GateError(f"workspace entry is not a regular file or link: {path}")
    with path.open("rb") as stream:
        content = hashlib.file_digest(stream, "sha256").digest()
    return f"file:{mode & 0o111:o}".encode(), content


def _worktree_digest(root: Path, excludes: Sequence[str]) -> str:
    """Checks inspect ignored and unstaged files too; the Git tree alone is not their input."""
    digest = hashlib.sha256()
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in excludes)
        base = Path(current)
        links = [name for name in dirs if (base / name).is_symlink()]
        for name in sorted(names + links):
            path = base / name
            kind, content = _entry_identity(path)
            digest.update(os.fsencode(path.relative_to(root)) + b"\0" + kind + b"\0")
            digest.update(content + b"\0")
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


def _command(config: dict[str, Any], check: dict[str, Any], files: list[str]) -> list[str]:
    root, tools = Path(config["root"]), Path(config["tools"])
    if check["category"] == "tests":
        sites = [arg for path in _project_sites(root) for arg in ("--site", path)]
        return [
            str(tools / "bin" / "python"),
            str(Path(__file__).resolve()),
            "pytest-run",
            "--timeout",
            str(config["test_timeout"]),
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


def _run_check(
    config: dict[str, Any], check: dict[str, Any], files: list[str], cache: Path
) -> CheckResult:
    started = time.monotonic()

    def result(status: str, code: int | None, output: str) -> CheckResult:
        seconds = round(time.monotonic() - started, 3)
        return CheckResult(
            check["name"], check["category"], check["blocking"], status, code, seconds,
            output[-OUTPUT_CHARS:],
        )  # fmt: skip

    if check["category"] != "tests" and not files:
        return result(PASSED, 0, "no Python files to check")
    command = _command(config, check, files)
    if not os.access(command[0], os.X_OK):
        return result(ERROR, None, f"tool not installed: {command[0]}")
    try:
        done = subprocess.run(
            command,
            cwd=config["root"],
            env=_tool_env(config, cache),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=CHECK_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return result(ERROR, None, f"killed after {CHECK_TIMEOUT_S} s")
    except OSError as exc:
        return result(ERROR, None, f"could not start: {exc}")
    output = done.stdout.decode("utf-8", errors="replace")
    if done.returncode < 0 or done.returncode in (126, 127):
        return result(ERROR, done.returncode, output)
    return result(PASSED if done.returncode == 0 else "failed", done.returncode, output)


def prerequisites(config: dict[str, Any]) -> list[CheckResult]:
    """Workflow preconditions run before any commit, including a reused tree verdict."""
    checks: list[CheckResult] = []
    for requirement in config.get("prerequisites", []):
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


def decide(
    config: dict[str, Any], state: Path, required: list[CheckResult] | None = None
) -> Decision:
    """Run every check over the work tree; the verdict is the worst of them."""
    started = time.monotonic()
    if required is None:
        required = prerequisites(config)
    if any(check.status != PASSED for check in required):
        return _verdict(required, round(time.monotonic() - started, 3))
    cache = state / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    files = python_files(Path(config["root"]), config["excludes"])
    checks = required + [_run_check(config, check, files, cache) for check in config["checks"]]
    return _verdict(checks, round(time.monotonic() - started, 3))


def _verdict(checks: list[CheckResult], seconds: float) -> Decision:
    """Error when a check could not run; refused when a blocking check failed; else passed."""
    errors = [c for c in checks if c.status == ERROR]
    if errors:
        error = "; ".join(f"{c.name}: {c.output.strip()[-300:]}" for c in errors)
        return Decision(ERROR, seconds, checks, error)
    refused = any(c.status == "failed" and c.blocking for c in checks)
    return Decision(REFUSED if refused else PASSED, seconds, checks)


def _append_line(path: Path, record: dict[str, Any]) -> None:
    """Append one JSON line; a link planted in the log's place is left alone."""
    if path.is_symlink():
        return
    with suppress(OSError), path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def hook(config: dict[str, Any]) -> int:
    root, state = Path(config["root"]), Path(config["hook_state"])
    tree = _git_or_none(root, "write-tree")
    worktree = None
    try:
        worktree = _worktree_digest(root, config["excludes"])
        decision = decide(config, state)
    except Exception as exc:  # noqa: BLE001 - a gate that cannot run is an error, never a pass
        decision = Decision(ERROR, 0.0, error=f"{type(exc).__name__}: {exc}")
    sys.stdout.write(decision.text())
    record = {
        "who": "hook",
        "at": _now(),
        "verdict": decision.verdict,
        "exit": EXITS[decision.verdict],
        "tree": tree,
        "worktree_sha256": worktree,
        "seconds": decision.seconds,
        "checks": decision.summary(),
        "error": decision.error,
    }
    _append_line(state / HOOK_RUNS, record)
    return EXITS[decision.verdict]


def _pathspec_excludes(excludes: Sequence[str]) -> list[str]:
    return [f":(exclude,glob)**/{name}/**" for name in excludes]


def _commits(root: Path, base: str) -> tuple[list[dict[str, str]], bool]:
    """Every commit from ``base`` (exclusive) to ``HEAD``, oldest first; and whether ``base``
    is still an ancestor of ``HEAD`` (history the agent rewrote cannot be read)."""
    ancestor = (
        subprocess.run(
            ["git", "-c", "safe.directory=*", "merge-base", "--is-ancestor", base, "HEAD"],
            cwd=root,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )
    if not ancestor:
        return [], False
    log = _git(root, "log", "--reverse", "--format=%H %T %s", f"{base}..HEAD")
    rows = [line.split(" ", 2) for line in log.splitlines() if line]
    return [
        {"commit": row[0], "tree": row[1], "subject": row[2] if len(row) > 2 else ""}
        for row in rows
    ], True


def _owned(root: Path, owner: str) -> str | None:
    """Give ``root`` back to the agent user; the failure, if any (recorded, never raised)."""
    done = subprocess.run(
        ["chown", "-R", f"{owner}:", str(root)], capture_output=True, text=True, check=False
    )
    return (done.stderr.strip() or f"exit {done.returncode}") if done.returncode else None


def seal(config: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    root, state = Path(config["root"]), Path(config["seal_state"])
    state.mkdir(parents=True, exist_ok=True)
    with suppress(PermissionError):  # a session leader (docker exec) already leads its group
        os.setpgrp()
    if os.getpgrp() == os.getpid():  # only a group this seal leads is ever killed
        (state / SEAL_PID).write_text(str(os.getpid()))
    started = time.monotonic()
    record: dict[str, Any] = {
        "who": "seal", "run": args.run, "after": args.after, "at": _now(),
        "verdict": ERROR, "exit": EXITS[ERROR], "tree": None, "commit": None,
        "reused_from": None, "checks": [], "output": "", "error": None,
    }  # fmt: skip
    try:
        record["head_before"] = _git(root, "rev-parse", "HEAD")
        record["hooks_path"] = _git_or_none(root, "config", "--get", "core.hooksPath")
        record["commits"], record["base_is_ancestor"] = _commits(root, args.base)
        _git(root, "add", "-A", "--", ".", *_pathspec_excludes(config["excludes"]))
        tree = record["tree"] = _git(root, "write-tree")
        record["worktree_sha256"] = _worktree_digest(root, config["excludes"])
        verdict = _decide_tree(config, state, args, record)
        if verdict == PASSED:
            record["commit"] = _commit(root, tree, record["head_before"], args)
        record["head_after"] = _git(root, "rev-parse", "HEAD")
        record["verdict"], record["exit"] = verdict, EXITS[verdict]
    except Exception as exc:  # noqa: BLE001 - recorded as a gate that could not run
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["output"] += f"verify-gate: ERROR, the gate could not run: {record['error']}\n"
    finally:
        record["chown_error"] = _owned(root, args.owner)
        (state / SEAL_PID).unlink(missing_ok=True)
    record["seconds"] = round(time.monotonic() - started, 3)
    return record


def _decide_tree(
    config: dict[str, Any], state: Path, args: argparse.Namespace, record: dict[str, Any]
) -> str:
    required = prerequisites(config)
    ready = all(check.status == PASSED for check in required)
    reusable = args.previous_verdict == PASSED or not config.get("prerequisites")
    if not ready:
        decision = _verdict(required, sum(check.seconds for check in required))
    elif (
        record["tree"] == args.previous_tree
        and record["worktree_sha256"] == args.previous_worktree
        and args.previous_verdict in (PASSED, REFUSED)
        and reusable
    ):
        record["reused_from"] = args.previous_run
        record["output"] = f"verify-gate: gate run {args.previous_run} decided this tree\n"
        record["checks"] = Decision(PASSED, 0.0, required).summary()
        return str(args.previous_verdict)
    else:
        decision = decide(config, state, required)
    record["checks"] = decision.summary()
    record["output"], record["error"] = decision.text(), decision.error
    return decision.verdict


def _commit(root: Path, tree: str, head: str, args: argparse.Namespace) -> str | None:
    """Commit the gated tree on ``HEAD``; ``None`` when ``HEAD`` already holds it (the agent's
    own commit is the work, and the seal adds none)."""
    if _git(root, "rev-parse", "HEAD^{tree}") == tree:
        return None
    message = f"verify-gate: seal after {args.after} (gate run {args.run})"
    commit = _git(root, "commit-tree", tree, "-p", head, "-m", message, env=SEAL_AUTHOR)
    _git(root, "update-ref", "HEAD", commit, head)
    return commit


def _agent_pids(owner: str) -> Iterator[int]:
    uid = pwd.getpwnam(owner).pw_uid
    for entry in Path("/proc").glob("[0-9]*"):
        with suppress(OSError):
            if entry.stat().st_uid == uid:
                yield int(entry.name)


def _stop_writers(config: dict[str, Any], owner: str) -> list[str]:
    """Stop a running seal and every agent-user process: nothing writes ``root`` after this."""
    stopped: list[str] = []
    pid_file = Path(config["seal_state"]) / SEAL_PID
    with suppress(OSError, ValueError, ProcessLookupError, PermissionError):
        os.killpg(int(pid_file.read_text()), signal.SIGKILL)
        stopped.append("seal")
    if pwd.getpwnam(owner).pw_uid == os.getuid():
        return stopped  # never this process's own user (a host-side test run)
    for _ in range(3):
        for pid in _agent_pids(owner):
            with suppress(OSError):
                os.kill(pid, signal.SIGKILL)
                stopped.append(str(pid))
        time.sleep(0.2)
    return stopped


def restore(config: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    root = Path(config["root"])
    record: dict[str, Any] = {"tree": args.tree, "restored": False, "error": None}
    try:
        record["stopped"] = _stop_writers(config, args.owner)
        (root / ".git" / "index.lock").unlink(missing_ok=True)
        _git(root, "read-tree", "--reset", "-u", args.tree)
        excludes = [arg for name in config["excludes"] for arg in ("-e", name)]
        _git(root, "clean", "-fdq", *excludes)
        record["restored"] = _git(root, "write-tree") == args.tree
    except Exception as exc:  # noqa: BLE001 - recorded: the runner reports a failed restore
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        record["chown_error"] = _owned(root, args.owner)
    return record


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


def pytest_run(timeout: int, sites: Sequence[str], args: Sequence[str]) -> int:
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

    return int(pytest.main(list(args), plugins=[StopSlowTests()]))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="verify-gate")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("hook", "seal", "restore"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        if name != "hook":
            command.add_argument("--owner", required=True)
    sealing = commands.choices["seal"]
    sealing.add_argument("--run", type=int, required=True)
    sealing.add_argument("--after", required=True)
    sealing.add_argument("--base", required=True)
    sealing.add_argument("--previous-run", type=int)
    sealing.add_argument("--previous-tree")
    sealing.add_argument("--previous-worktree")
    sealing.add_argument("--previous-verdict")
    commands.choices["restore"].add_argument("--tree", required=True)
    stripping = commands.add_parser("strip-rules")
    stripping.add_argument("--file", type=Path)
    stripping.add_argument("--remove-empty", action="store_true")
    testing = commands.add_parser("pytest-run")
    testing.add_argument("--timeout", type=int, required=True)
    testing.add_argument("--site", action="append", default=[])
    testing.add_argument("args", nargs="*")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    os.umask(0o022)  # the container's exec umask is 0: nothing the gate writes is world-writable
    if args.command == "pytest-run":
        return pytest_run(args.timeout, args.site, args.args)
    if args.command == "strip-rules":
        if args.file is None:
            sys.stdout.write(strip_rules(sys.stdin.read()))
        else:
            print(json.dumps(strip_file(args.file, args.remove_empty)))
        return 0
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.command == "hook":
        return hook(config)
    run = seal if args.command == "seal" else restore
    print(json.dumps(run(config, args), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
