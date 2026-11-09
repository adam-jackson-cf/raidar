"""The trusted payload's supervisor run for real: real children, timeouts, descendants, pipes.

Its decisions (refusals, outcome decoding, collection, kill rounds) are proven with injected
pipes, clock and wait status in ``test_primitive_payload_boundaries.py``; these tests keep only
what needs a forked child: exit/signal/stream capture, the real deadline kill, a flood, a
descendant holding the pipes, an unstartable command, a tool changed by the running child, and
the procfs view of adopted children.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from raidar.subject.primitive.payload import supervise, walk
from tests.primitive_support import lay as _lay
from tests.primitive_support import sha as _sha

pytestmark = pytest.mark.slow

PY = sys.executable


@pytest.fixture(autouse=True)
def _only_test_owned_children(monkeypatch: pytest.MonkeyPatch) -> None:
    """The supervisor kills and reaps every child of its process; spare the test runner's."""
    real = supervise._children
    spared = set(real(os.getpid()))

    def owned(me: int) -> list[int]:
        return [pid for pid in real(me) if pid not in spared]

    def reap() -> None:
        for pid in owned(os.getpid()):
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                continue

    monkeypatch.setattr(supervise, "_children", owned)
    monkeypatch.setattr(supervise, "_reap_all", reap)


def _spec(tmp_path: Path, code: str, **over: Any) -> dict[str, Any]:
    spec = supervise.build_spec(
        call_dir=str(tmp_path / "call"),
        argv=[PY, "-c", code],
        env={"PATH": supervise.BASE_PATH},
        cwd=str(tmp_path),
        timeout_s=20,
        uid=None,
        gid=None,
        seccomp=False,
        trees=[],
    )
    spec.update(over)
    return spec


def _tree_check(root: Path) -> dict[str, Any]:
    limits = {"links": True, "owner": None}
    digest = walk.scan(str(root), walk.Limits.from_spec(limits), entries=False)["digest"]
    return {"root": str(root), "limits": limits, "digest": digest}


def test_a_run_reports_exit_code_signal_streams_and_digests(tmp_path: Path) -> None:
    code = "import sys; sys.stdout.write('out'); sys.stderr.write('err'); sys.exit(3)"

    result = supervise.supervise(_spec(tmp_path, code))

    assert result["ran"]
    assert result["isolation_error"] is None
    assert (result["exit_code"], result["signal"], result["timed_out"]) == (3, None, False)
    assert result["stdout_sha256"] == _sha(b"out")
    assert result["stderr_bytes"] == 3

    killed = supervise.supervise(
        _spec(tmp_path, "import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
    )
    assert (killed["exit_code"], killed["signal"]) == (None, 9)


def test_a_child_that_outlives_its_timeout_is_killed(tmp_path: Path) -> None:
    started = time.monotonic()

    result = supervise.supervise(_spec(tmp_path, "import time; time.sleep(60)", timeout_s=0.5))

    assert result["timed_out"]
    assert result["signal"] == 9
    assert time.monotonic() - started < 15


def test_a_flood_on_stdout_is_cut_at_the_cap_and_flagged(tmp_path: Path) -> None:
    result = supervise.supervise(
        _spec(tmp_path, "import sys\nwhile True: sys.stdout.write('x' * 4096)", stdout_cap=1000)
    )

    assert result["overflow"]
    assert not result["timed_out"]
    assert len(supervise.base64.b64decode(result["stdout_b64"])) == 1000
    assert result["stdout_bytes"] > 1000


def _is_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:
        return ") Z" in Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False


def test_a_backgrounded_descendant_holding_the_pipes_does_not_outlive_the_call(
    tmp_path: Path,
) -> None:
    pidfile = tmp_path / "pid"
    code = (
        "import subprocess, sys\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
    )
    started = time.monotonic()

    result = supervise.supervise(_spec(tmp_path, code))

    assert result["exit_code"] == 0
    assert not result["timed_out"]
    assert time.monotonic() - started < 15
    pid = int(pidfile.read_text())
    for _ in range(100):  # init may take a moment to reap the orphan
        if _is_gone(pid):
            break
        time.sleep(0.05)
    else:
        pytest.fail("the descendant survived the call")


@pytest.mark.parametrize(
    "over",
    [
        {"argv": ["no-such-tool-anywhere"]},
        {"argv": [PY, "-c", "pass"], "cwd": "/nonexistent/dir"},
    ],
)
def test_a_child_that_cannot_be_started_is_reported_as_not_run(
    tmp_path: Path, over: dict[str, Any]
) -> None:
    result = supervise.supervise(_spec(tmp_path, "pass", **over))

    assert result["ran"] is False
    assert result["isolation_error"]
    assert result["exit_code"] == 127


def test_a_tool_tree_changed_by_the_running_child_is_flagged_after(tmp_path: Path) -> None:
    tool = _lay(tmp_path / "tool")
    check = _tree_check(tool)
    mutate = f"open({str(tool / 'a.txt')!r}, 'w').write('changed')"

    after = supervise.supervise(_spec(tmp_path, mutate, verify=[check]))

    assert after["ran"]
    assert [t["ok"] for t in after["tool_before"]] == [True]
    assert [t["ok"] for t in after["tool_after"]] == [False]


@pytest.mark.skipif(not os.path.isdir("/proc/self"), reason="needs procfs")
def test_adopted_children_are_found_through_procfs() -> None:
    child = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"])  # noqa: S603
    try:
        assert child.pid in supervise._children(os.getpid())
        assert child.pid not in supervise._children(child.pid)
    finally:
        child.kill()
        child.wait(timeout=120)
