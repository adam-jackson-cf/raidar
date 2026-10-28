"""Safety boundaries of the trusted payload, exercised in-process.

``test_primitive_calls.py`` drives the payload through a subprocess; these tests call it
directly so refusals, cleanup and fail-closed isolation are observed on the real helpers.
Only the external edges are replaced: ``uv``, the kernel's seccomp/prctl and privilege calls.
Nothing authored runs as root; the supervisor children are this file's own interpreter code.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from raidar.subject.primitive.payload import install_tree, isolate, main, supervise, walk

PY = sys.executable
IS_ROOT = os.geteuid() == 0


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


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------------------------ walk
def _lay(root: Path) -> Path:
    (root / "d").mkdir(parents=True)
    (root / "a.txt").write_bytes(b"alpha")
    (root / "d" / "b.bin").write_bytes(b"beta!")
    return root


def test_a_symlink_is_refused_unless_links_are_recorded_and_never_followed(
    tmp_path: Path,
) -> None:
    root = _lay(tmp_path / "tree")
    outside = tmp_path / "secret"
    outside.write_bytes(b"outside")
    (root / "l").symlink_to(outside)

    refused = walk.scan(str(root), walk.Limits())
    recorded = walk.scan(str(root), walk.Limits(links=True))

    assert refused["violation"] == "symlink: l"
    assert recorded["violation"] is None
    link = next(e for e in recorded["entries"] if e["path"] == "l")
    assert link["type"] == "link"
    assert link["target"] == str(outside)
    assert link["sha256"] == ""
    assert recorded["bytes"] == 10  # the link target's bytes were not read


def test_a_fifo_is_refused_without_being_opened(tmp_path: Path) -> None:
    root = _lay(tmp_path / "tree")
    os.mkfifo(root / "pipe")

    found = walk.scan(str(root), walk.Limits())

    assert found["violation"] == "special file: pipe"


@pytest.mark.parametrize(
    ("limits", "refused"),
    [
        (walk.Limits(max_files=2), False),
        (walk.Limits(max_files=1), True),
        (walk.Limits(max_bytes=10), False),
        (walk.Limits(max_bytes=9), True),
        (walk.Limits(max_depth=2), False),
        (walk.Limits(max_depth=1), True),
    ],
)
def test_bounds_admit_exactly_the_limit_and_refuse_one_beyond(
    tmp_path: Path, limits: walk.Limits, refused: bool
) -> None:
    found = walk.scan(str(_lay(tmp_path / "tree")), limits)

    assert (found["violation"] is not None) is refused


def test_a_foreign_owner_is_refused(tmp_path: Path) -> None:
    root = _lay(tmp_path / "tree")

    mine = walk.scan(str(root), walk.Limits(owner=os.getuid()))
    theirs = walk.scan(str(root), walk.Limits(owner=os.getuid() + 1))

    assert mine["violation"] is None
    assert theirs["violation"].startswith("owner: ")


@pytest.mark.skipif(IS_ROOT, reason="root reads unreadable files")
def test_an_unreadable_file_is_a_violation_not_a_crash(tmp_path: Path) -> None:
    root = _lay(tmp_path / "tree")
    (root / "a.txt").chmod(0)
    try:
        found = walk.scan(str(root), walk.Limits())
    finally:
        (root / "a.txt").chmod(0o600)

    assert found["violation"].startswith("unreadable: a.txt")


def test_a_missing_root_is_reported_absent_and_a_single_file_is_a_manifest_of_one(
    tmp_path: Path,
) -> None:
    gone = walk.scan(str(tmp_path / "gone"), walk.Limits())
    (tmp_path / "one").write_bytes(b"xyz")
    single = walk.scan(str(tmp_path / "one"), walk.Limits(), entries=False)

    assert gone["exists"] is False
    assert gone["violation"] == "missing"
    assert single["single"] is True
    assert single["files"] == 1
    assert single["entries"] == []


def test_the_digest_changes_with_content_and_mode_but_not_between_scans(tmp_path: Path) -> None:
    root = _lay(tmp_path / "tree")
    first = walk.scan(str(root), walk.Limits())["digest"]

    assert walk.scan(str(root), walk.Limits())["digest"] == first
    (root / "a.txt").write_bytes(b"alphb")
    assert walk.scan(str(root), walk.Limits())["digest"] != first
    (root / "a.txt").write_bytes(b"alpha")
    (root / "a.txt").chmod(0o755)
    assert walk.scan(str(root), walk.Limits())["digest"] != first


def test_a_file_replaced_between_listing_and_open_is_refused(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    other = tmp_path / "other"
    victim.write_bytes(b"v")
    other.write_bytes(b"o")
    listed = os.lstat(victim)

    with pytest.raises(walk.Violation, match="changed while read"):
        walk._open_regular(str(other), listed)

    victim.unlink()
    victim.symlink_to(other)
    with pytest.raises(OSError, match="Too many levels|symbolic link"):
        walk._open_regular(str(victim), listed)


def test_a_snapshot_archives_regular_files_privately(tmp_path: Path) -> None:
    root = _lay(tmp_path / "tree")
    tar_path = tmp_path / "out.tar"

    found = walk.snapshot(str(root), str(tar_path), walk.Limits())

    assert found["violation"] is None
    assert stat.S_IMODE(tar_path.stat().st_mode) == 0o600
    with tarfile.open(tar_path) as archive:
        members = {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}  # type: ignore[union-attr]
    assert members == {"a.txt": b"alpha", "d/b.bin": b"beta!"}


def test_a_dirty_tree_is_never_archived(tmp_path: Path) -> None:
    root = _lay(tmp_path / "tree")
    (root / "l").symlink_to(root)
    tar_path = tmp_path / "out.tar"

    found = walk.snapshot(str(root), str(tar_path), walk.Limits())

    assert found["violation"] == "symlink: l"
    assert not tar_path.exists()


def test_a_snapshot_never_overwrites_or_follows_an_existing_destination(
    tmp_path: Path,
) -> None:
    root = _lay(tmp_path / "tree")
    victim = tmp_path / "victim"
    victim.write_bytes(b"precious")
    planted = tmp_path / "planted.tar"
    planted.symlink_to(victim)
    existing = tmp_path / "existing.tar"
    existing.write_bytes(b"old")

    with pytest.raises(FileExistsError):
        walk.snapshot(str(root), str(planted), walk.Limits())
    with pytest.raises(FileExistsError):
        walk.snapshot(str(root), str(existing), walk.Limits())

    assert victim.read_bytes() == b"precious"
    assert existing.read_bytes() == b"old"


def _race_after_scan(monkeypatch: pytest.MonkeyPatch, mutate: Any) -> None:
    real = walk.scan

    def racing(root: str, limits: walk.Limits, **kw: Any) -> dict[str, Any]:
        result = real(root, limits, **kw)
        mutate()
        return result

    monkeypatch.setattr(walk, "scan", racing)


def test_a_file_that_shrinks_during_the_snapshot_leaves_no_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _lay(tmp_path / "tree")
    tar_path = tmp_path / "out.tar"
    _race_after_scan(monkeypatch, lambda: (root / "a.txt").write_bytes(b"al"))

    found = walk.snapshot(str(root), str(tar_path), walk.Limits())

    assert found["violation"].startswith("snapshot failed:")
    assert not tar_path.exists()


def test_a_file_swapped_for_a_link_during_the_snapshot_leaves_no_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _lay(tmp_path / "tree")
    secret = tmp_path / "secret"
    secret.write_bytes(b"alpha")
    tar_path = tmp_path / "out.tar"

    def swap() -> None:
        (root / "a.txt").unlink()
        (root / "a.txt").symlink_to(secret)

    _race_after_scan(monkeypatch, swap)

    found = walk.snapshot(str(root), str(tar_path), walk.Limits())

    assert found["violation"].startswith("snapshot failed:")
    assert not tar_path.exists()


def test_exact_reads_never_return_more_than_the_scanned_size() -> None:
    reader = walk._Exact(io.BytesIO(b"abcdef"), 4)

    assert reader.read() == b"abcd"
    assert reader.read(10) == b""


def test_limits_from_a_spec_default_to_unbounded() -> None:
    assert walk.Limits.from_spec(None) == walk.Limits()
    assert walk.Limits.from_spec({"max_files": 3, "links": True}) == walk.Limits(
        max_files=3, links=True
    )


# ----------------------------------------------------------------------------- install_tree
def _stage(tmp_path: Path, files: dict[str, tuple[bytes, bool]], **over: Any) -> dict[str, Any]:
    stage = tmp_path / "stage"
    for name, (data, _) in files.items():
        target = stage / "files" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o777)
    links = tmp_path / "links"
    links.mkdir(exist_ok=True)
    spec: dict[str, Any] = {
        "stage": str(stage),
        "root": str(tmp_path / "root"),
        "files": [
            {"path": name, "sha256": _sha(data), "exec": run} for name, (data, run) in files.items()
        ],
        "wheels": [],
        "venv": False,
        "commands": {},
        "links": str(links),
    }
    spec.update(over)
    return spec


def test_an_install_copies_bytes_normalises_modes_and_wraps_commands_safely(
    tmp_path: Path,
) -> None:
    hostile = ["python", "-c", "x; touch /tmp/pwn $(id)"]
    spec = _stage(
        tmp_path,
        {"libexec/run": (b"#!/bin/sh\n", True), "data/x.txt": (b"data", False)},
        commands={"tool": hostile},
    )

    done = install_tree.install(spec)

    root = Path(spec["root"])
    assert done == {"ok": True, "commands": ["tool"]}
    assert (root / "data" / "x.txt").read_bytes() == b"data"
    assert stat.S_IMODE((root / "libexec" / "run").stat().st_mode) == 0o755
    assert stat.S_IMODE((root / "data" / "x.txt").stat().st_mode) == 0o644
    assert stat.S_IMODE((root / "data").stat().st_mode) == 0o755
    assert (Path(spec["links"]) / "tool").resolve() == (root / "bin" / "tool").resolve()
    assert not Path(spec["stage"]).exists()


def test_a_tampered_staged_file_or_wheel_stops_the_install_before_anything_is_written(
    tmp_path: Path,
) -> None:
    spec = _stage(tmp_path, {"a": (b"good", False)})
    (Path(spec["stage"]) / "files" / "a").write_bytes(b"evil")

    with pytest.raises(install_tree.InstallError, match="staged file a"):
        install_tree.install(spec)
    assert not Path(spec["root"]).exists()

    (Path(spec["stage"]) / "files" / "a").write_bytes(b"good")
    wheels = Path(spec["stage"]) / "wheels"
    wheels.mkdir()
    (wheels / "w.whl").write_bytes(b"swapped")
    spec["wheels"] = [{"file": "w.whl", "sha256": _sha(b"original")}]
    with pytest.raises(install_tree.InstallError, match="staged wheel w.whl"):
        install_tree.install(spec)
    assert not Path(spec["root"]).exists()


@pytest.mark.parametrize("bad", ["../evil", "-x", "a b", "", ".hidden", "a/b"])
def test_a_bad_command_name_is_refused_and_earlier_links_are_rolled_back(
    tmp_path: Path, bad: str
) -> None:
    spec = _stage(tmp_path, {"a": (b"1", False)}, commands={"a-good": ["python"], bad: ["python"]})
    with pytest.raises(install_tree.InstallError, match="bad command"):
        install_tree.install(spec)

    assert list(Path(spec["links"]).iterdir()) == []


def test_an_empty_argv_is_refused(tmp_path: Path) -> None:
    spec = _stage(tmp_path, {"a": (b"1", False)}, commands={"tool": []})

    with pytest.raises(install_tree.InstallError, match="bad command"):
        install_tree.install(spec)


def test_an_existing_link_is_never_replaced_and_earlier_links_are_removed(
    tmp_path: Path,
) -> None:
    spec = _stage(
        tmp_path, {"a": (b"1", False)}, commands={"first": ["python"], "second": ["python"]}
    )
    taken = Path(spec["links"]) / "second"
    taken.write_text("system tool")

    with pytest.raises(install_tree.InstallError, match="already exists"):
        install_tree.install(spec)

    assert taken.read_text() == "system tool"
    assert not (Path(spec["links"]) / "first").exists()


def _fake_uv(
    monkeypatch: pytest.MonkeyPatch, *, returncode: int = 0, stderr: str = "", sites: int = 1
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append({"argv": argv, **kwargs})
        if returncode == 0 and argv[1] == "venv":
            venv = Path(argv[-1])
            (venv / "bin").mkdir(parents=True)
            (venv / "bin" / "python").write_text("")
            (venv / "bin" / "entry").write_text("")
            for number in range(sites):
                (venv / "lib" / f"python3.{12 + number}" / "site-packages").mkdir(parents=True)
        return SimpleNamespace(returncode=returncode, stderr=stderr)

    monkeypatch.setattr(install_tree.subprocess, "run", run)
    return calls


def test_a_venv_install_runs_uv_with_a_clean_environment_and_writes_the_source_path_as_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIDAR_TEST_SECRET", "s3cret")
    calls = _fake_uv(monkeypatch)
    spec = _stage(
        tmp_path,
        {"tool/src/mod.py": (b"x = 1\n", False)},
        venv=True,
        uv="/usr/bin/uv",
        python="/usr/bin/python3",
        flags=["--offline"],
        commands={"run": ["python", "-m", "mod"], "entry": ["entry"], "other": ["/bin/true"]},
    )
    Path(spec["stage"], "requirements.txt").write_text("")
    wheels = Path(spec["stage"]) / "wheels"
    wheels.mkdir()
    (wheels / "w.whl").write_bytes(b"wheel")
    spec["wheels"] = [{"file": "w.whl", "sha256": _sha(b"wheel")}]

    install_tree.install(spec)

    root = Path(spec["root"])
    assert len(calls) == 2
    for call in calls:
        assert "RAIDAR_TEST_SECRET" not in call["env"]
        assert call["cwd"] == "/"
        assert call["env"]["HOME"].startswith(spec["stage"])
    pth = root / "venv" / "lib" / "python3.12" / "site-packages" / install_tree.PTH_NAME
    assert pth.read_text() == f"{root / 'tool' / 'src'}\n"


def test_a_failed_uv_is_an_install_error_carrying_the_tail_of_its_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_uv(monkeypatch, returncode=2, stderr="x" * 5000 + "no matching wheel")
    spec = _stage(tmp_path, {"a": (b"1", False)}, venv=True, uv="uv", python="python3", flags=[])

    with pytest.raises(install_tree.InstallError, match="exited 2.*no matching wheel") as caught:
        install_tree.install(spec)

    assert len(str(caught.value)) < 1700


def test_a_uv_that_times_out_fails_the_install_and_removes_the_partial_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def hang(argv: list[str], **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(install_tree.subprocess, "run", hang)
    spec = _stage(tmp_path, {"a": (b"1", False)}, venv=True, uv="uv", python="python3", flags=[])

    reply = _run_main(tmp_path, capsys, "install", spec)

    assert reply["ok"] is False
    assert "timed out" in reply["error"]
    assert not Path(spec["root"]).exists()


def test_an_ambiguous_site_packages_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_uv(monkeypatch, sites=2)
    spec = _stage(
        tmp_path,
        {"tool/src/mod.py": (b"", False)},
        venv=True,
        uv="uv",
        python="python3",
        flags=[],
    )

    with pytest.raises(install_tree.InstallError, match="single site-packages"):
        install_tree.install(spec)


# ------------------------------------------------------------------------------------ main
def _run_main(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str, spec: dict[str, Any]
) -> dict[str, Any]:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))
    assert main.main([command, str(path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def test_an_install_into_an_existing_root_is_refused_and_leaves_it_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = _stage(tmp_path, {"a": (b"1", False)})
    Path(spec["root"]).mkdir()
    (Path(spec["root"]) / "keep").write_text("mine")

    reply = _run_main(tmp_path, capsys, "install", spec)

    assert reply["ok"] is False
    assert "already exists" in reply["error"]
    assert (Path(spec["root"]) / "keep").read_text() == "mine"


def test_a_dangling_root_link_counts_as_existing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = _stage(tmp_path, {"a": (b"1", False)})
    Path(spec["root"]).symlink_to(tmp_path / "nowhere")

    reply = _run_main(tmp_path, capsys, "install", spec)

    assert reply["ok"] is False
    assert not (tmp_path / "nowhere").exists()


def test_a_failed_install_removes_its_partial_root_and_reports_the_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = _stage(tmp_path, {"a": (b"1", False)}, commands={"bad name": ["python"]})

    reply = _run_main(tmp_path, capsys, "install", spec)

    assert reply["ok"] is False
    assert "bad command" in reply["error"]
    assert not Path(spec["root"]).exists()


def test_an_oserror_during_install_is_reported_not_raised(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = _stage(tmp_path, {"a": (b"1", False)})
    (Path(spec["stage"]) / "files" / "a").unlink()

    reply = _run_main(tmp_path, capsys, "install", spec)

    assert reply["ok"] is False
    assert "a" in reply["error"]
    assert not Path(spec["root"]).exists()


def test_manifest_and_snapshot_commands_report_violations_instead_of_failing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _lay(tmp_path / "tree")
    (root / "l").symlink_to(root)
    limits = {"max_files": 5}

    manifest = _run_main(
        tmp_path, capsys, "manifest", {"path": str(root), "limits": limits, "entries": False}
    )
    snap = _run_main(
        tmp_path,
        capsys,
        "snapshot",
        {"path": str(root), "tar": str(tmp_path / "o.tar"), "limits": limits},
    )

    assert manifest["violation"] == snap["violation"] == "symlink: l"
    assert manifest["entries"] == []
    assert not (tmp_path / "o.tar").exists()


def test_probe_reports_the_scarcest_memory_and_unknown_disk_as_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 9 kB\nMemAvailable:     100 kB\n")
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    (cgroup / "memory.max").write_text("2000000\n")
    (cgroup / "memory.current").write_text("500000\n")
    monkeypatch.setattr(main, "MEMINFO", str(meminfo))
    monkeypatch.setattr(main, "CGROUP", str(cgroup))
    paths = [str(tmp_path), str(tmp_path / "absent")]

    reply = _run_main(tmp_path, capsys, "probe", {"paths": paths})

    assert reply["memory_available"] == 102400
    assert reply["disk_free"][paths[0]] > 0
    assert reply["disk_free"][paths[1]] is None

    (cgroup / "memory.current").write_text("1900000\n")
    meminfo.write_text("MemAvailable: 99999999 kB\n")
    assert main.probe({"paths": []})["memory_available"] == 100000


@pytest.mark.parametrize("limit", ["max\n", "garbage\n"])
def test_an_unlimited_or_unreadable_memory_source_is_unknown_never_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    (cgroup / "memory.max").write_text(limit)
    (cgroup / "memory.current").write_text("500\n")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable: lots kB\n")
    monkeypatch.setattr(main, "MEMINFO", str(meminfo))
    monkeypatch.setattr(main, "CGROUP", str(cgroup))

    assert main.probe({"paths": []})["memory_available"] is None
    monkeypatch.setattr(main, "MEMINFO", str(tmp_path / "missing"))
    monkeypatch.setattr(main, "CGROUP", str(tmp_path / "missing"))
    assert main.probe({"paths": []})["memory_available"] is None


def test_an_unknown_command_is_an_error_not_a_silent_success(tmp_path: Path) -> None:
    path = tmp_path / "spec.json"
    path.write_text("{}")

    with pytest.raises(KeyError):
        main.main(["rm-rf", str(path)])


def test_a_malformed_spec_file_fails_before_anything_runs(tmp_path: Path) -> None:
    path = tmp_path / "spec.json"
    path.write_text("{not json")

    with pytest.raises(json.JSONDecodeError):
        main.main(["probe", str(path)])


# ------------------------------------------------------------------------------- isolation
def test_isolation_refuses_platforms_without_a_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(isolate.platform, "system", lambda: "Darwin")
    with pytest.raises(isolate.IsolationError, match="no seccomp filter"):
        isolate._architecture()
    monkeypatch.setattr(isolate.platform, "system", lambda: "Linux")
    monkeypatch.setattr(isolate.platform, "machine", lambda: "riscv64")
    with pytest.raises(isolate.IsolationError, match="no seccomp filter"):
        isolate._architecture()
    monkeypatch.setattr(isolate.platform, "machine", lambda: "ARM64")
    assert isolate._architecture() == "aarch64"


class _Libc:
    def __init__(self, prctl: Any = None, syscall: Any = None) -> None:
        self.prctl = prctl or (lambda *a: 0)
        self.syscall = syscall or (lambda *a: -1)


def test_a_failing_prctl_stops_before_a_filter_is_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int] = []

    def prctl(option: Any, *rest: Any) -> int:
        seen.append(option.value)
        ctypes.set_errno(errno.EPERM)
        return -1

    monkeypatch.setattr(isolate, "_architecture", lambda: "x86_64")
    monkeypatch.setattr(isolate.ctypes, "CDLL", lambda *a, **k: _Libc(prctl))

    with pytest.raises(isolate.IsolationError, match="prctl"):
        isolate.install_seccomp()

    assert seen == [isolate.PR_SET_NO_NEW_PRIVS]


def _socket_module(monkeypatch: pytest.MonkeyPatch, behaviour: Any) -> None:
    fake = SimpleNamespace(
        AF_INET=2,
        AF_UNIX=1,
        SOCK_STREAM=1,
        socket=behaviour,
        socketpair=lambda *a: (SimpleNamespace(close=lambda: None),) * 2,
    )
    monkeypatch.setattr(isolate, "socket", fake)


def _refuse(code: int) -> Any:
    def socket(*args: Any) -> None:
        raise OSError(code, os.strerror(code))

    return socket


def test_an_unproven_network_denial_is_an_isolation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def uring_refused(*a: Any) -> int:
        ctypes.set_errno(errno.EPERM)
        return -1

    ok = _Libc(syscall=uring_refused)

    _socket_module(monkeypatch, lambda *a: SimpleNamespace(close=lambda: None))
    with pytest.raises(isolate.IsolationError, match="socket\\(\\) was not refused"):
        isolate._prove_denied(ok)

    _socket_module(monkeypatch, _refuse(errno.EACCES))
    with pytest.raises(isolate.IsolationError, match="not EPERM"):
        isolate._prove_denied(ok)

    _socket_module(monkeypatch, _refuse(errno.EPERM))
    isolate._prove_denied(ok)  # the proven state

    wrong_errno = _Libc(syscall=lambda *a: (ctypes.set_errno(errno.ENOSYS), -1)[1])
    with pytest.raises(isolate.IsolationError, match="io_uring_setup"):
        isolate._prove_denied(wrong_errno)
    allowed = _Libc(syscall=lambda *a: 3)
    with pytest.raises(isolate.IsolationError, match="io_uring_setup"):
        isolate._prove_denied(allowed)


def _patch_ids(monkeypatch: pytest.MonkeyPatch, *, ids: int | None, regain: bool) -> None:
    def setuid(uid: int) -> None:
        if uid == 0 and not regain:
            raise PermissionError

    monkeypatch.setattr(os, "setgroups", lambda groups: None)
    monkeypatch.setattr(os, "setgid", lambda gid: None)
    monkeypatch.setattr(os, "setuid", setuid)
    if ids is not None:
        for name in ("getuid", "geteuid", "getgid", "getegid"):
            monkeypatch.setattr(os, name, lambda: ids)


def test_privileges_that_were_not_dropped_or_can_be_regained_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ids(monkeypatch, ids=None, regain=False)
    with pytest.raises(isolate.IsolationError, match="ids were not dropped"):
        isolate.drop_privileges(54321, 54321)

    _patch_ids(monkeypatch, ids=1000, regain=True)
    with pytest.raises(isolate.IsolationError, match="root was regained"):
        isolate.drop_privileges(1000, 1000)

    _patch_ids(monkeypatch, ids=1000, regain=False)
    isolate.drop_privileges(1000, 1000)


# ---------------------------------------------------------------------------- supervisor
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


def test_capture_keeps_a_bounded_head_or_tail_but_digests_everything() -> None:
    head, tail = supervise.Capture(4, tail=False), supervise.Capture(4, tail=True)
    for chunk in (b"abc", b"def", b"gh"):
        head.feed(chunk)
        tail.feed(chunk)

    assert head.data == b"abcd"
    assert head.overflow
    assert tail.data == b"efgh"
    assert not tail.overflow
    for capture in (head, tail):
        assert capture.total == 8
        assert capture.record("o")["o_sha256"] == _sha(b"abcdefgh")
        assert capture.record("o")["o_bytes"] == 8


def test_argv0_resolution_only_trusts_executables_on_the_given_path(tmp_path: Path) -> None:
    tool = tmp_path / "tool"
    tool.write_text("")
    plain = tmp_path / "plain"
    plain.write_text("")
    tool.chmod(0o755)
    plain.chmod(0o644)

    assert supervise._resolve("tool", f"/nonexistent:{tmp_path}") == str(tool)
    assert supervise._resolve("/abs/tool", "") == "/abs/tool"
    with pytest.raises(FileNotFoundError):
        supervise._resolve("plain", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        supervise._resolve("tool", "")


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


def test_a_setup_that_never_reports_is_a_failure_not_a_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(supervise, "STATUS_WAIT_S", 0.2)
    read, write = os.pipe()
    try:
        assert supervise._read_status(read) == "child setup did not finish"
    finally:
        os.close(write)
    assert supervise._read_status(read) is None
    os.close(read)


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
