"""The trusted payload's supervisor run for real: real children, timeouts, descendants, pipes."""

from __future__ import annotations

import ctypes
import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
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


def test_a_changed_tool_tree_is_refused_before_it_runs_and_flagged_after(
    tmp_path: Path,
) -> None:
    tool = _lay(tmp_path / "tool")
    marker = tmp_path / "ran"
    check = _tree_check(tool)
    (tool / "a.txt").write_bytes(b"tampered")

    refused = supervise.supervise(_spec(tmp_path, f"open({str(marker)!r}, 'w')", verify=[check]))

    assert refused["ran"] is False
    assert not marker.exists()
    assert [t["ok"] for t in refused["tool_before"]] == [False]
    assert "tool_after" not in refused

    (tool / "a.txt").write_bytes(b"alpha")
    mutate = f"open({str(tool / 'a.txt')!r}, 'w').write('changed')"
    after = supervise.supervise(_spec(tmp_path, mutate, verify=[check]))
    assert after["ran"]
    assert [t["ok"] for t in after["tool_before"]] == [True]
    assert [t["ok"] for t in after["tool_after"]] == [False]


def test_unsafe_or_oversized_watched_state_refuses_the_run_and_missing_state_does_not(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "ran"
    code = f"open({str(marker)!r}, 'w')"
    state = _lay(tmp_path / "state")
    (state / "l").symlink_to(state)

    unsafe = supervise.supervise(_spec(tmp_path, code, state=str(state)))
    (state / "l").unlink()
    oversized = supervise.supervise(
        _spec(
            tmp_path,
            code,
            state=str(state),
            state_limits={"max_bytes": 1, "max_files": 10, "max_depth": 5},
        )
    )
    absent = supervise.supervise(_spec(tmp_path, "pass", state=str(tmp_path / "absent")))

    assert unsafe["ran"] is False
    assert unsafe["bounds_violation"] == "symlink: l"
    assert oversized["ran"] is False
    assert "too many bytes" in oversized["bounds_violation"]
    assert not marker.exists()
    assert absent["ran"]
    assert absent["bounds_violation"] is None


def test_a_supervisor_that_cannot_become_a_subreaper_refuses_to_run_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "ran"
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: SimpleNamespace(prctl=lambda *args: -1))

    result = supervise.supervise(_spec(tmp_path, f"open({str(marker)!r}, 'w')", seccomp=True))

    assert result["ran"] is False
    assert result["isolation_error"] == "could not become a subreaper"
    assert not marker.exists()
    assert "tool_after" not in result

    def unavailable(*a: Any, **k: Any) -> None:
        raise OSError("no libc")

    monkeypatch.setattr(ctypes, "CDLL", unavailable)
    assert supervise._subreaper({"seccomp": True}) == "could not become a subreaper"
    assert supervise._subreaper({"seccomp": False}) is None


def test_call_scratch_directories_are_private_to_the_call(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "pass")

    supervise._prepare_dirs(spec)

    for name in ("home", "tmp"):
        assert stat.S_IMODE((tmp_path / "call" / name).stat().st_mode) == 0o700


def test_the_supervisor_entry_prints_one_result_and_destroys_the_call_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = _spec(tmp_path, "print('hi')")
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))

    assert supervise.main([str(path)]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["exit_code"] == 0
    assert not (tmp_path / "call").exists()


def test_stray_adopted_processes_are_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    stray = subprocess.Popen([PY, "-c", "import time; time.sleep(60)"])  # noqa: S603
    pending = [[stray.pid], []]
    monkeypatch.setattr(supervise, "_children", lambda me: pending.pop(0))
    monkeypatch.setattr(supervise, "_reap_all", lambda: None)  # the Popen below reaps its own

    killed = supervise.kill_descendants(2**22)  # no such process group

    assert killed == 1
    assert stray.wait(timeout=10) == -signal.SIGKILL


@pytest.mark.skipif(not os.path.isdir("/proc/self"), reason="needs procfs")
def test_adopted_children_are_found_through_procfs() -> None:
    child = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"])  # noqa: S603
    try:
        assert child.pid in supervise._children(os.getpid())
        assert child.pid not in supervise._children(child.pid)
    finally:
        child.kill()
        child.wait()
