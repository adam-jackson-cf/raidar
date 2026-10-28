"""Run one authored command as the agent's user under a trusted supervisor.

Root-owned spec in, one JSON result out. The supervisor (root) verifies the installed tool
trees and the watched state, forks the child, drops it to the agent's user with a clean
environment, no inherited descriptors and the seccomp network filter, captures its output
through pipes (stdout kept to a cap, stderr's tail, full hashes), enforces the deadline, kills
the child's whole process group and every adopted orphan (it is a subreaper), verifies the
tool trees again and prints the result. The child never writes the result. Isolation that
cannot be installed means the child is never executed.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import selectors
import shutil
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any

from . import isolate, walk

GRACE_S = 0.3
POLL_S = 0.05
KILL_ROUNDS = 100
PR_SET_CHILD_SUBREAPER = 36
BASE_PATH = "/usr/local/bin:/usr/bin:/bin"
STDOUT_CAP = 1 << 20
STDERR_TAIL = 8 << 10
MAX_TIMEOUT_S = 300
STATE_LIMITS: dict[str, Any] = {"max_bytes": 64 << 20, "max_files": 10_000, "max_depth": 32}
TREE_LIMITS: dict[str, Any] = {"links": True, "owner": 0}
"""Limits of an installed (root-owned, unbounded) tree: links are recorded, owners checked."""
STATUS_WAIT_S = 10.0


class Capture:
    """One captured stream: a bounded copy plus the digest and size of everything seen."""

    def __init__(self, cap: int, *, tail: bool) -> None:
        self.cap, self.tail = cap, tail
        self.data = b""
        self.total = 0
        self.digest = hashlib.sha256()

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        self.digest.update(chunk)
        if self.tail:
            self.data = (self.data + chunk)[-self.cap :]
        elif len(self.data) < self.cap:
            self.data += chunk[: self.cap - len(self.data)]

    @property
    def overflow(self) -> bool:
        return not self.tail and self.total > self.cap

    def record(self, name: str) -> dict[str, Any]:
        return {
            f"{name}_b64": base64.b64encode(self.data).decode(),
            f"{name}_sha256": self.digest.hexdigest(),
            f"{name}_bytes": self.total,
        }


@dataclass
class Launched:
    pid: int
    out_fd: int
    err_fd: int
    setup_error: str | None


def _resolve(argv0: str, path: str) -> str:
    if "/" in argv0:
        return argv0
    for directory in path.split(":"):
        candidate = os.path.join(directory, argv0)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise FileNotFoundError(f"{argv0}: not found on PATH")


def _prepare_stdio(out_w: int, err_w: int, keep: int) -> None:
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.dup2(out_w, 1)
    os.dup2(err_w, 2)
    limit = min(os.sysconf("SC_OPEN_MAX"), 1 << 20)
    os.closerange(3, keep)
    os.closerange(keep + 1, limit)


def _become_child(spec: dict[str, Any]) -> None:
    if spec["uid"] is not None:
        isolate.drop_privileges(spec["uid"], spec["gid"])
    if spec["seccomp"]:
        isolate.install_seccomp()
    os.chdir(spec["cwd"])


def _child(spec: dict[str, Any], out_w: int, err_w: int, status_w: int) -> None:
    try:
        os.setsid()
        _prepare_stdio(out_w, err_w, status_w)
        os.umask(0o022)
        _become_child(spec)
        argv, env = spec["argv"], spec["env"]
        os.execve(_resolve(argv[0], env.get("PATH", "")), argv, env)
    except BaseException as error:  # noqa: BLE001 - reported to the supervisor, then _exit
        with contextlib.suppress(OSError):
            os.write(status_w, f"{type(error).__name__}: {error}".encode())
    os._exit(127)


def _read_status(status_r: int) -> str | None:
    selector = selectors.DefaultSelector()
    selector.register(status_r, selectors.EVENT_READ)
    text = b""
    deadline = time.monotonic() + STATUS_WAIT_S
    while time.monotonic() < deadline:
        if not selector.select(0.1):
            continue
        chunk = os.read(status_r, 4096)
        if not chunk:
            break
        text += chunk
    else:
        return "child setup did not finish"
    return text.decode(errors="replace") or None


def launch(spec: dict[str, Any]) -> Launched:
    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()
    status_r, status_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        _child(spec, out_w, err_w, status_w)
    for descriptor in (out_w, err_w, status_w):
        os.close(descriptor)
    problem = _read_status(status_r)
    os.close(status_r)
    return Launched(pid, out_r, err_r, problem)


def _children(me: int) -> list[int]:
    found: list[int] = []
    try:
        names = os.listdir("/proc")
    except OSError:
        return found
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", encoding="utf-8", errors="replace") as handle:
                fields = handle.read().rpartition(")")[2].split()
        except OSError:
            continue
        if len(fields) > 1 and int(fields[1]) == me:
            found.append(int(name))
    return found


def _reap_all() -> None:
    while True:
        try:
            if os.waitpid(-1, os.WNOHANG)[0] == 0:
                return
        except ChildProcessError:
            return


def kill_descendants(pgid: int) -> int:
    """Kill the process group and every adopted orphan; the number of strays killed."""
    killed = 0
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)
    for _ in range(KILL_ROUNDS):
        strays = _children(os.getpid())
        if not strays:
            break
        for pid in strays:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
                killed += 1
        time.sleep(0.01)
        _reap_all()
    return killed


@dataclass
class Outcome:
    status: int | None
    timed_out: bool


def _poll_main(pid: int) -> int | None:
    done, status = os.waitpid(pid, os.WNOHANG)
    return status if done else None


def _drain(selector: selectors.BaseSelector, sinks: dict[int, Capture]) -> None:
    for key, _ in selector.select(POLL_S):
        chunk = os.read(key.fd, 65536)
        if chunk:
            sinks[key.fd].feed(chunk)
        else:
            selector.unregister(key.fd)


def collect(launched: Launched, spec: dict[str, Any], stdout: Capture, stderr: Capture) -> Outcome:
    """Pump the pipes until the child ends (its pipes close or the grace passes) or time is up."""
    sinks = {launched.out_fd: stdout, launched.err_fd: stderr}
    selector = selectors.DefaultSelector()
    for descriptor in sinks:
        selector.register(descriptor, selectors.EVENT_READ)
    deadline = time.monotonic() + spec["timeout_s"]
    status: int | None = None
    grace_end: float | None = None
    timed_out = False
    while not stdout.overflow:
        now = time.monotonic()
        if now >= deadline:
            timed_out = True
            break
        if status is None:
            status = _poll_main(launched.pid)
        if status is not None:
            grace_end = grace_end if grace_end is not None else now + GRACE_S
            if not selector.get_map() or now >= grace_end:
                break
        if selector.get_map():
            _drain(selector, sinks)
        else:
            time.sleep(POLL_S)
    selector.close()
    return Outcome(status, timed_out)


def flush(launched: Launched, stdout: Capture, stderr: Capture) -> None:
    """Read what the killed child's pipes still buffer."""
    for descriptor, sink in ((launched.out_fd, stdout), (launched.err_fd, stderr)):
        os.set_blocking(descriptor, False)
        while True:
            try:
                chunk = os.read(descriptor, 65536)
            except BlockingIOError:
                break
            if not chunk:
                break
            sink.feed(chunk)


def finish_main(pid: int, outcome: Outcome) -> int | None:
    """Kill what is left of the child's group and reap the main child; its wait status."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)
    if outcome.status is not None:
        return outcome.status
    with contextlib.suppress(ChildProcessError):
        return os.waitpid(pid, 0)[1]
    return None


def _decode(status: int | None) -> tuple[int | None, int | None]:
    if status is None:
        return None, None
    if os.WIFSIGNALED(status):
        return None, os.WTERMSIG(status)
    return os.WEXITSTATUS(status), None


def _subreaper(spec: dict[str, Any]) -> str | None:
    if not spec["seccomp"]:
        return None
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        done = libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
    except (AttributeError, OSError):
        return "could not become a subreaper"
    return None if done == 0 else "could not become a subreaper"


def _check_trees(spec: dict[str, Any]) -> list[dict[str, Any]]:
    checks = []
    for tree in spec["verify"]:
        found = walk.scan(tree["root"], walk.Limits.from_spec(tree["limits"]), entries=False)
        ok = found["violation"] is None and found["digest"] == tree["digest"]
        checks.append({"root": tree["root"], "ok": ok, "digest": found["digest"]})
    return checks


def _check_state(spec: dict[str, Any]) -> str | None:
    if not spec["state"]:
        return None
    found = walk.scan(spec["state"], walk.Limits.from_spec(spec["state_limits"]), entries=False)
    return None if found["violation"] in (None, "missing") else found["violation"]


def _prepare_dirs(spec: dict[str, Any]) -> None:
    for name in ("home", "tmp"):
        path = os.path.join(spec["call_dir"], name)
        os.makedirs(path, mode=0o700, exist_ok=True)
        if spec["uid"] is not None:
            os.chown(path, spec["uid"], spec["gid"])
    if os.geteuid() == 0:
        for path in (spec["call_dir"], os.path.join(spec["call_dir"], "context.json")):
            if os.path.exists(path):
                os.chown(path, 0, 0)
        os.chmod(spec["call_dir"], 0o755)


def _run_child(spec: dict[str, Any], result: dict[str, Any]) -> None:
    stdout = Capture(spec["stdout_cap"], tail=False)
    stderr = Capture(spec["stderr_tail"], tail=True)
    started = time.monotonic()
    launched = launch(spec)
    result["isolation_error"] = launched.setup_error
    outcome = collect(launched, spec, stdout, stderr)
    status = finish_main(launched.pid, outcome)
    result["descendants_killed"] = kill_descendants(launched.pid)
    flush(launched, stdout, stderr)
    for descriptor in (launched.out_fd, launched.err_fd):
        os.close(descriptor)
    exit_code, signal_number = _decode(status)
    result.update(
        ran=launched.setup_error is None,
        exit_code=exit_code,
        signal=signal_number,
        timed_out=outcome.timed_out,
        overflow=stdout.overflow,
        duration_s=round(time.monotonic() - started, 3),
        **stdout.record("stdout"),
        **stderr.record("stderr"),
    )


def supervise(spec: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"schema": "call-result/1", "ran": False, "isolation_error": None}
    _prepare_dirs(spec)
    result["tool_before"] = _check_trees(spec)
    result["bounds_violation"] = _check_state(spec)
    result["isolation_error"] = _subreaper(spec)
    refused = result["bounds_violation"] or result["isolation_error"]
    if refused or not all(item["ok"] for item in result["tool_before"]):
        return result
    _run_child(spec, result)
    result["tool_after"] = _check_trees(spec)
    return result


def build_spec(
    *,
    call_dir: str,
    argv: list[str],
    env: dict[str, str],
    cwd: str,
    timeout_s: float,
    uid: int | None,
    gid: int | None,
    seccomp: bool,
    trees: list[dict[str, Any]],
    state: str | None = None,
    state_limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The supervisor's spec; the one definition used by the host runner and the in-container gate.

    ``uid``/``gid`` ``None`` means the caller already is the agent's user (no drop); the
    seccomp filter, clean environment, caps, bounds and hashing are identical either way.
    """
    return {
        "call_dir": call_dir,
        "argv": argv,
        "env": env,
        "cwd": cwd,
        "timeout_s": timeout_s,
        "uid": uid,
        "gid": gid,
        "seccomp": seccomp,
        "stdout_cap": STDOUT_CAP,
        "stderr_tail": STDERR_TAIL,
        "verify": trees,
        "state": state,
        "state_limits": dict(state_limits or STATE_LIMITS),
    }


def main(arguments: list[str]) -> int:
    with open(arguments[0], encoding="utf-8") as handle:
        spec = json.load(handle)
    result = supervise(spec)
    shutil.rmtree(spec["call_dir"], ignore_errors=True)
    sys.stdout.write(json.dumps(result) + "\n")
    return 0
