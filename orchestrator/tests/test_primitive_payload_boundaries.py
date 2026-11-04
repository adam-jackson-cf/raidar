"""Safety boundaries of the trusted payload, exercised in-process.

``test_primitive_calls.py`` drives the payload through a subprocess; these tests call it
directly so refusals, cleanup and fail-closed isolation are observed on the real helpers.
Only the external edges are replaced: ``uv``, the kernel's seccomp/prctl and privilege calls.
Nothing authored runs as root; the supervisor children are this file's own interpreter code.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import io
import json
import os
import signal
import stat
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from raidar.subject.primitive.payload import install_tree, isolate, main, supervise, walk
from tests.primitive_support import lay as _lay
from tests.primitive_support import sha as _sha

# ------------------------------------------------------------------------------------ walk


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


def test_an_unreadable_file_is_a_violation_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _lay(tmp_path / "tree")

    def unreadable(*args: Any) -> int:
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(walk.os, "open", unreadable)
    found = walk.scan(str(root), walk.Limits())

    assert found["exists"]
    assert found["violation"].startswith("unreadable: a.txt")


def test_a_directory_listing_failure_is_recorded_as_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreadable(path: str) -> list[str]:
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(walk.os, "listdir", unreadable)
    found = walk.scan(str(tmp_path), walk.Limits())

    assert found["exists"]
    assert found["violation"] == "unreadable: Permission denied"


def test_a_file_that_ends_before_its_declared_size_refuses_a_further_archive_read() -> None:
    reader = walk._Exact(io.BytesIO(b"ab"), 3)

    assert reader.read(2) == b"ab"
    with pytest.raises(walk.Violation, match="changed while archived"):
        reader.read()


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
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hostile = ["python", "-c", "x; touch /tmp/pwn $(id)"]
    spec = _stage(
        tmp_path,
        {"libexec/run": (b"#!/bin/sh\n", True), "data/x.txt": (b"data", False)},
        commands={"tool": hostile},
        root=str(tmp_path / "stages" / "ns" / "name"),
    )

    stages = tmp_path / "stages"
    stages.mkdir(mode=0o700)
    spec_path = tmp_path / "install.json"
    spec_path.write_text(json.dumps(spec))
    previous = os.umask(0)
    try:
        assert main.main(["install", str(spec_path)]) == 0
    finally:
        os.umask(previous)
    done = json.loads(capsys.readouterr().out)

    root = Path(spec["root"])
    assert done == {"ok": True, "commands": ["tool"]}
    assert stat.S_IMODE(stages.stat().st_mode) == 0o700
    for directory in (root.parent, root, root / "bin"):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o755
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


def test_memory_without_an_available_counter_falls_back_to_the_container_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counters = {
        main.MEMINFO: "MemTotal: 9000 kB\nMemFree: 100 kB\n",
        f"{main.CGROUP}/memory.max": "2048\n",
        f"{main.CGROUP}/memory.current": "512\n",
    }
    monkeypatch.setattr("builtins.open", lambda path, **kw: io.StringIO(counters[path]))

    assert main.probe({"paths": []}) == {"disk_free": {}, "memory_available": 1536}


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


def test_an_installed_filter_still_refuses_execution_when_network_denial_is_unproven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(isolate, "_architecture", lambda: "aarch64")
    monkeypatch.setattr(isolate.ctypes, "CDLL", lambda *a, **kw: _Libc())
    _socket_module(monkeypatch, lambda *a: SimpleNamespace(close=lambda: None))

    with pytest.raises(isolate.IsolationError, match="socket\\(\\) was not refused"):
        isolate.install_seccomp()


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


def test_a_setup_that_never_reports_is_a_failure_not_a_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read, write = os.pipe()
    try:
        monkeypatch.setattr(supervise, "STATUS_WAIT_S", 0)  # the wait is already over
        assert supervise._read_status(read) == "child setup did not finish"
        monkeypatch.setattr(supervise, "STATUS_WAIT_S", 10)
    finally:
        os.close(write)
    assert supervise._read_status(read) is None
    os.close(read)


def test_a_setup_failure_is_reported_as_its_text() -> None:
    read, write = os.pipe()
    os.write(write, b"OSError: no such directory")
    os.close(write)

    assert supervise._read_status(read) == "OSError: no such directory"
    os.close(read)


# ------------------------------------------------------------------ supervisor decisions
# The supervisor's decisions run on real pipes with an injected clock, launcher and wait
# status; nothing forks and nothing sleeps. ``test_primitive_payload_supervisor_slow.py``
# keeps the seams that need a real child process.
class _Done(BaseException):  # noqa: N818 - stands in for a process exit
    def __init__(self, code: int) -> None:
        self.code = code


class _Clock:
    def __init__(self, tick: float = 0.1) -> None:
        self.now, self.tick = 0.0, tick

    def monotonic(self) -> float:
        self.now += self.tick
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(
        supervise, "time", SimpleNamespace(monotonic=fake.monotonic, sleep=fake.sleep)
    )
    monkeypatch.setattr(supervise, "POLL_S", 0)
    return fake


class _Pipes:
    """The supervisor's end of a launched child: stdout/stderr pipes the test feeds."""

    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", *, close: bool = True) -> None:
        self.out_r, self.out_w = os.pipe()
        self.err_r, self.err_w = os.pipe()
        os.write(self.out_w, stdout)
        os.write(self.err_w, stderr)
        if close:
            self.close_writers()

    def close_writers(self) -> None:
        for descriptor in (self.out_w, self.err_w):
            with contextlib.suppress(OSError):
                os.close(descriptor)

    def launched(self, pid: int = 4321, error: str | None = None) -> supervise.Launched:
        return supervise.Launched(pid, self.out_r, self.err_r, error)

    def release(self) -> None:
        self.close_writers()
        for descriptor in (self.out_r, self.err_r):
            with contextlib.suppress(OSError):
                os.close(descriptor)


def _polls(monkeypatch: pytest.MonkeyPatch, *statuses: int | None) -> None:
    pending = list(statuses)
    monkeypatch.setattr(
        supervise, "_poll_main", lambda pid: pending.pop(0) if len(pending) > 1 else pending[0]
    )


def _call_spec(tmp_path: Path, **over: Any) -> dict[str, Any]:
    spec = supervise.build_spec(
        call_dir=str(tmp_path / "call"),
        argv=["tool"],
        env={"PATH": supervise.BASE_PATH},
        cwd=str(tmp_path),
        timeout_s=1,
        uid=None,
        gid=None,
        seccomp=False,
        trees=[],
    )
    spec.update(over)
    return spec


def test_output_is_pumped_until_the_child_and_its_pipes_are_done(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    pipes = _Pipes(b"out", b"err")
    out, err = supervise.Capture(100, tail=False), supervise.Capture(100, tail=True)
    _polls(monkeypatch, None, 3 << 8)
    try:
        outcome = supervise.collect(pipes.launched(), {"timeout_s": 5}, out, err)
    finally:
        pipes.release()

    assert (outcome.status, outcome.timed_out) == (3 << 8, False)
    assert (out.data, err.data) == (b"out", b"err")


def test_a_child_that_outlives_its_timeout_is_cut_off_at_the_deadline(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    pipes = _Pipes(close=False)
    _polls(monkeypatch, None)
    try:
        outcome = supervise.collect(
            pipes.launched(),
            {"timeout_s": 0.35},
            supervise.Capture(9, tail=False),
            supervise.Capture(9, tail=True),
        )
    finally:
        pipes.release()

    assert (outcome.status, outcome.timed_out) == (None, True)
    assert clock.now < 1


def test_a_descendant_holding_the_pipes_open_is_waited_for_only_for_the_grace(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    pipes = _Pipes(close=False)
    _polls(monkeypatch, 0)
    try:
        outcome = supervise.collect(
            pipes.launched(),
            {"timeout_s": 60},
            supervise.Capture(9, tail=False),
            supervise.Capture(9, tail=True),
        )
    finally:
        pipes.release()

    assert (outcome.status, outcome.timed_out) == (0, False)
    assert supervise.GRACE_S <= clock.now < supervise.GRACE_S + 1


def test_a_flood_stops_collection_at_the_cap(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    pipes = _Pipes(b"x" * 50, close=False)
    out = supervise.Capture(10, tail=False)
    _polls(monkeypatch, None)
    try:
        outcome = supervise.collect(
            pipes.launched(), {"timeout_s": 60}, out, supervise.Capture(9, tail=True)
        )
    finally:
        pipes.release()

    assert out.overflow
    assert (outcome.status, outcome.timed_out) == (None, False)
    assert (out.data, out.total) == (b"x" * 10, 50)


def test_a_main_child_still_running_with_closed_pipes_is_polled_not_spun(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    pipes = _Pipes()
    _polls(monkeypatch, None, None, 0)
    try:
        outcome = supervise.collect(
            pipes.launched(),
            {"timeout_s": 60},
            supervise.Capture(9, tail=False),
            supervise.Capture(9, tail=True),
        )
    finally:
        pipes.release()

    assert outcome.status == 0


def test_what_a_killed_childs_pipes_still_buffer_is_read_without_blocking() -> None:
    pipes = _Pipes(b"tail", b"", close=False)
    out, err = supervise.Capture(100, tail=False), supervise.Capture(100, tail=True)
    try:
        supervise.flush(pipes.launched(), out, err)
    finally:
        pipes.release()

    assert (out.data, err.data) == (b"tail", b"")


def test_a_closed_pipe_ends_the_flush() -> None:
    pipes = _Pipes(b"a", b"b")
    out, err = supervise.Capture(100, tail=False), supervise.Capture(100, tail=True)
    try:
        supervise.flush(pipes.launched(), out, err)
    finally:
        pipes.release()

    assert (out.data, err.data) == (b"a", b"b")


def test_poll_reports_a_status_only_for_a_finished_child(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([(0, 0), (77, 512)])
    monkeypatch.setattr(os, "waitpid", lambda pid, flags: next(answers))

    assert supervise._poll_main(77) is None
    assert supervise._poll_main(77) == 512


@pytest.mark.parametrize(
    ("status", "expected"),
    [(None, (None, None)), (3 << 8, (3, None)), (9, (None, 9)), (0, (0, None))],
)
def test_a_wait_status_decodes_to_an_exit_code_or_a_signal(
    status: int | None, expected: tuple[int | None, int | None]
) -> None:
    assert supervise._decode(status) == expected


def test_the_childs_group_is_killed_and_a_known_status_is_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append((pid, sig)))

    assert supervise.finish_main(55, supervise.Outcome(7, False)) == 7
    assert killed == [(55, signal.SIGKILL)]


@pytest.mark.parametrize(
    ("wait", "expected"),
    [(lambda pid, flags: (pid, 9), 9), (None, None)],
)
def test_an_unreaped_child_is_waited_for_after_its_group_is_killed(
    monkeypatch: pytest.MonkeyPatch, wait: Any, expected: int | None
) -> None:
    def gone(pid: int, sig: int) -> None:
        raise ProcessLookupError

    def nothing(pid: int, flags: int) -> None:
        raise ChildProcessError

    monkeypatch.setattr(os, "killpg", gone)
    monkeypatch.setattr(os, "waitpid", wait or nothing)

    assert supervise.finish_main(55, supervise.Outcome(None, True)) == expected


def test_stray_adopted_processes_are_killed_and_counted(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    pending = [[11, 12], [13], []]
    signalled: list[int] = []
    reaped: list[bool] = []

    def kill(pid: int, sig: int) -> None:
        if pid == 12:
            raise ProcessLookupError
        signalled.append(pid)

    monkeypatch.setattr(supervise, "_children", lambda me: pending.pop(0))
    monkeypatch.setattr(supervise, "_reap_all", lambda: reaped.append(True))
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: (_ for _ in ()).throw(PermissionError))
    monkeypatch.setattr(os, "kill", kill)

    assert supervise.kill_descendants(2**22) == 2
    assert signalled == [11, 13]
    assert len(reaped) == 2


def test_killing_descendants_gives_up_after_its_rounds(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    monkeypatch.setattr(supervise, "KILL_ROUNDS", 3)
    monkeypatch.setattr(supervise, "_children", lambda me: [9])
    monkeypatch.setattr(supervise, "_reap_all", lambda: None)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: None)
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    assert supervise.kill_descendants(2**22) == 3


def test_reaping_stops_when_nothing_is_left_to_wait_for(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: list[Any] = [(5, 0), (6, 0), (0, 0)]

    def wait(pid: int, flags: int) -> tuple[int, int]:
        return answers.pop(0)

    monkeypatch.setattr(os, "waitpid", wait)
    supervise._reap_all()
    assert answers == []

    monkeypatch.setattr(os, "waitpid", lambda pid, flags: (_ for _ in ()).throw(ChildProcessError))
    supervise._reap_all()


def test_adopted_children_are_read_from_the_process_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tables = {
        "/proc/12/stat": "12 (a b) S 99 1 2",
        "/proc/14/stat": "14 (c) S 7 1 2",
        "/proc/15/stat": "15 (d)",
    }

    def read(path: str, **kwargs: Any) -> io.StringIO:
        if path not in tables:
            raise OSError(path)
        return io.StringIO(tables[path])

    with monkeypatch.context() as patch:
        patch.setattr(os, "listdir", lambda path: ["12", "self", "13", "14", "15"])
        patch.setattr(supervise, "open", read, raising=False)
        assert supervise._children(99) == [12]
        assert supervise._children(7) == [14]


def test_a_host_without_a_process_table_has_no_adopted_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(path: str) -> list[str]:
        raise FileNotFoundError(path)

    with monkeypatch.context() as patch:
        patch.setattr(os, "listdir", missing)
        assert supervise._children(1) == []


def test_the_child_redirects_its_stdio_and_closes_every_other_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, tuple[int, ...]]] = []
    with monkeypatch.context() as patch:
        patch.setattr(os, "open", lambda path, flags: 99)
        patch.setattr(os, "dup2", lambda a, b: seen.append(("dup2", (a, b))))
        patch.setattr(os, "closerange", lambda a, b: seen.append(("close", (a, b))))
        patch.setattr(os, "sysconf", lambda name: 4096)
        supervise._prepare_stdio(7, 8, 9)

    assert seen == [
        ("dup2", (99, 0)),
        ("dup2", (7, 1)),
        ("dup2", (8, 2)),
        ("close", (3, 9)),
        ("close", (10, 4096)),
    ]


@pytest.mark.parametrize(
    ("over", "expected"),
    [
        ({}, ["chdir"]),
        ({"uid": 1000, "gid": 1001}, ["drop 1000 1001", "chdir"]),
        ({"seccomp": True}, ["seccomp", "chdir"]),
        ({"uid": 1, "gid": 2, "seccomp": True}, ["drop 1 2", "seccomp", "chdir"]),
    ],
)
def test_the_child_drops_privileges_then_installs_the_filter_then_enters_its_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, over: dict[str, Any], expected: list[str]
) -> None:
    order: list[str] = []
    monkeypatch.setattr(
        isolate, "drop_privileges", lambda uid, gid: order.append(f"drop {uid} {gid}")
    )
    monkeypatch.setattr(isolate, "install_seccomp", lambda: order.append("seccomp"))
    monkeypatch.setattr(os, "chdir", lambda path: order.append("chdir"))

    supervise._become_child(_call_spec(tmp_path, **over))

    assert order == expected


def _child_setup(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    steps: list[Any] = []

    def leave(code: int) -> None:
        raise _Done(code)

    monkeypatch.setattr(os, "setsid", lambda: steps.append("setsid"))
    monkeypatch.setattr(os, "umask", lambda mask: 0)
    monkeypatch.setattr(os, "execve", lambda *a: steps.append(("execve", a)))
    monkeypatch.setattr(os, "_exit", leave)
    monkeypatch.setattr(supervise, "_prepare_stdio", lambda *a: steps.append(("stdio", a)))
    return steps


def test_a_child_that_cannot_be_prepared_reports_why_and_exits_127(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _child_setup(monkeypatch)
    monkeypatch.setattr(
        supervise, "_become_child", lambda spec: (_ for _ in ()).throw(OSError("no such dir"))
    )
    read, write = os.pipe()

    with pytest.raises(_Done) as left:
        supervise._child(_call_spec(tmp_path), 1, 2, write)

    os.close(write)
    assert left.value.code == 127
    assert os.read(read, 100) == b"OSError: no such dir"
    os.close(read)


def test_a_command_that_is_not_on_the_path_never_reaches_exec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steps = _child_setup(monkeypatch)
    monkeypatch.setattr(supervise, "_become_child", lambda spec: None)
    read, write = os.pipe()

    with pytest.raises(_Done):
        supervise._child(
            _call_spec(tmp_path, argv=["no-such-tool"], env={"PATH": str(tmp_path)}), 1, 2, write
        )

    os.close(write)
    assert b"FileNotFoundError" in os.read(read, 200)
    os.close(read)
    assert not any(step[0] == "execve" for step in steps if isinstance(step, tuple))


def test_a_prepared_child_executes_the_resolved_argv_with_the_clean_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steps = _child_setup(monkeypatch)
    monkeypatch.setattr(supervise, "_become_child", lambda spec: steps.append("become"))
    tool = tmp_path / "tool"
    tool.write_text("")
    tool.chmod(0o755)
    spec = _call_spec(tmp_path, argv=["tool", "--x"], env={"PATH": str(tmp_path), "A": "1"})
    read, write = os.pipe()

    with pytest.raises(_Done):  # the faked exec returns, so the child leaves with 127
        supervise._child(spec, 1, 2, write)

    os.close(write)
    assert os.read(read, 10) == b""
    os.close(read)
    assert steps[:2] == ["setsid", ("stdio", (1, 2, write))]
    assert steps[2] == "become"
    assert steps[3] == ("execve", (str(tool), ["tool", "--x"], {"PATH": str(tmp_path), "A": "1"}))


def test_the_parent_of_a_launched_child_gets_its_pid_pipes_and_setup_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(os, "fork", lambda: 4321)

    launched = supervise.launch(_call_spec(tmp_path))

    assert (launched.pid, launched.setup_error) == (4321, None)
    os.close(launched.out_fd)
    os.close(launched.err_fd)


def test_a_launched_child_runs_the_child_side_and_never_returns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    given: list[tuple[Any, ...]] = []

    def child(spec: dict[str, Any], out_w: int, err_w: int, status_w: int) -> None:
        given.append((spec["argv"], out_w, err_w, status_w))
        raise _Done(127)

    monkeypatch.setattr(os, "fork", lambda: 0)
    monkeypatch.setattr(supervise, "_child", child)

    with pytest.raises(_Done):
        supervise.launch(_call_spec(tmp_path))

    assert given[0][0] == ["tool"]
    for descriptor in given[0][1:]:
        os.close(descriptor)


def _launch_with(
    monkeypatch: pytest.MonkeyPatch, pipes: _Pipes, *, status: int | None, error: str | None = None
) -> None:
    _polls(monkeypatch, status)
    monkeypatch.setattr(supervise, "launch", lambda spec: pipes.launched(error=error))
    monkeypatch.setattr(supervise, "kill_descendants", lambda pgid: 2)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: None)


def _run_child(tmp_path: Path, **over: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    supervise._run_child(_call_spec(tmp_path, **over), result)
    return result


def test_a_run_reports_exit_code_streams_digests_and_stray_kills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clock: _Clock
) -> None:
    pipes = _Pipes(b"out", b"err")
    _launch_with(monkeypatch, pipes, status=3 << 8)

    result = _run_child(tmp_path)

    assert result["ran"]
    assert result["isolation_error"] is None
    assert (result["exit_code"], result["signal"], result["timed_out"], result["overflow"]) == (
        3,
        None,
        False,
        False,
    )
    assert result["stdout_sha256"] == _sha(b"out")
    assert (result["stdout_bytes"], result["stderr_bytes"]) == (3, 3)
    assert result["descendants_killed"] == 2
    assert result["duration_s"] > 0


def test_a_child_killed_by_a_signal_reports_the_signal_not_an_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clock: _Clock
) -> None:
    _launch_with(monkeypatch, _Pipes(), status=9)

    result = _run_child(tmp_path)

    assert (result["exit_code"], result["signal"]) == (None, 9)


def test_a_timed_out_child_is_reaped_and_flagged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clock: _Clock
) -> None:
    pipes = _Pipes(b"partial", close=False)
    _launch_with(monkeypatch, pipes, status=None)
    monkeypatch.setattr(os, "waitpid", lambda pid, flags: (pid, 9))

    result = _run_child(tmp_path, timeout_s=0.35)

    assert result["timed_out"]
    assert (result["exit_code"], result["signal"]) == (None, 9)
    assert supervise.base64.b64decode(result["stdout_b64"]) == b"partial"
    pipes.release()


def test_a_flood_is_cut_at_the_cap_and_flagged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clock: _Clock
) -> None:
    pipes = _Pipes(b"x" * 4096, close=False)
    _launch_with(monkeypatch, pipes, status=None)
    monkeypatch.setattr(os, "waitpid", lambda pid, flags: (pid, 9))

    result = _run_child(tmp_path, stdout_cap=1000)

    assert result["overflow"]
    assert not result["timed_out"]
    assert len(supervise.base64.b64decode(result["stdout_b64"])) == 1000
    assert result["stdout_bytes"] >= 1000
    pipes.release()


def test_a_child_that_cannot_be_started_is_reported_as_not_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clock: _Clock
) -> None:
    _launch_with(monkeypatch, _Pipes(), status=127 << 8, error="FileNotFoundError: tool")

    result = _run_child(tmp_path)

    assert result["ran"] is False
    assert result["isolation_error"] == "FileNotFoundError: tool"
    assert result["exit_code"] == 127


# -------------------------------------------------------------- the supervisor's refusals
def _supervised(
    monkeypatch: pytest.MonkeyPatch, spec: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started: list[dict[str, Any]] = []

    def run(given: dict[str, Any], result: dict[str, Any]) -> None:
        started.append(given)
        result["ran"] = True

    monkeypatch.setattr(supervise, "_run_child", run)
    return supervise.supervise(spec), started


def _tree_check(root: Path) -> dict[str, Any]:
    limits = {"links": True, "owner": None}
    digest = walk.scan(str(root), walk.Limits.from_spec(limits), entries=False)["digest"]
    return {"root": str(root), "limits": limits, "digest": digest}


def test_a_changed_tool_tree_is_refused_before_it_runs_and_flagged_after(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tool = _lay(tmp_path / "tool")
    check = _tree_check(tool)
    (tool / "a.txt").write_bytes(b"tampered")

    refused, started = _supervised(monkeypatch, _call_spec(tmp_path, verify=[check]))

    assert refused["ran"] is False
    assert started == []
    assert [t["ok"] for t in refused["tool_before"]] == [False]
    assert "tool_after" not in refused

    (tool / "a.txt").write_bytes(b"alpha")
    clean, started = _supervised(monkeypatch, _call_spec(tmp_path, verify=[check]))
    assert clean["ran"]
    assert [t["ok"] for t in clean["tool_before"]] == [True]
    assert [t["ok"] for t in clean["tool_after"]] == [True]
    assert len(started) == 1


def test_an_unsafe_or_oversized_watched_state_refuses_the_run_and_missing_state_does_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _lay(tmp_path / "state")
    (state / "l").symlink_to(state)

    unsafe, started = _supervised(monkeypatch, _call_spec(tmp_path, state=str(state)))
    (state / "l").unlink()
    limits = {"max_bytes": 1, "max_files": 10, "max_depth": 5}
    oversized, _ = _supervised(
        monkeypatch, _call_spec(tmp_path, state=str(state), state_limits=limits)
    )
    absent, started_absent = _supervised(
        monkeypatch, _call_spec(tmp_path, state=str(tmp_path / "absent"))
    )
    unwatched, _ = _supervised(monkeypatch, _call_spec(tmp_path))

    assert unsafe["ran"] is False
    assert unsafe["bounds_violation"] == "symlink: l"
    assert started == []
    assert oversized["ran"] is False
    assert "too many bytes" in oversized["bounds_violation"]
    assert absent["ran"]
    assert absent["bounds_violation"] is None
    assert unwatched["bounds_violation"] is None
    assert len(started_absent) == 1


def _libc(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    def cdll(*args: Any, **kwargs: Any) -> Any:
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ctypes, "CDLL", cdll)


@pytest.mark.parametrize(
    "libc",
    [SimpleNamespace(prctl=lambda *args: -1), OSError("no libc"), SimpleNamespace()],
)
def test_a_supervisor_that_cannot_become_a_subreaper_refuses_to_run_the_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, libc: Any
) -> None:
    _libc(monkeypatch, libc)

    result, started = _supervised(monkeypatch, _call_spec(tmp_path, seccomp=True))

    assert result["ran"] is False
    assert result["isolation_error"] == "could not become a subreaper"
    assert started == []
    assert "tool_after" not in result


def test_a_subreaper_is_only_needed_when_the_filter_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[int, ...]] = []

    def prctl(*args: int) -> int:
        seen.append(args)
        return 0

    _libc(monkeypatch, SimpleNamespace(prctl=prctl))

    assert supervise._subreaper({"seccomp": False}) is None
    assert seen == []
    assert supervise._subreaper({"seccomp": True}) is None
    assert seen == [(supervise.PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)]


def test_call_scratch_directories_are_private_to_the_call(tmp_path: Path) -> None:
    spec = _call_spec(tmp_path)

    supervise._prepare_dirs(spec)

    for name in ("home", "tmp"):
        assert stat.S_IMODE((tmp_path / "call" / name).stat().st_mode) == 0o700


def test_scratch_goes_to_the_childs_user_and_a_root_supervisor_locks_the_call_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    chowned: list[tuple[str, int, int]] = []
    chmodded: list[tuple[str, int]] = []
    (tmp_path / "call").mkdir()
    (tmp_path / "call" / "context.json").write_text("{}")
    monkeypatch.setattr(
        os, "chown", lambda path, uid, gid: chowned.append((Path(path).name, uid, gid))
    )
    monkeypatch.setattr(os, "chmod", lambda path, mode: chmodded.append((Path(path).name, mode)))
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    supervise._prepare_dirs(_call_spec(tmp_path, uid=1000, gid=1001))

    assert chowned == [
        ("home", 1000, 1001),
        ("tmp", 1000, 1001),
        ("call", 0, 0),
        ("context.json", 0, 0),
    ]
    assert chmodded == [("call", 0o755)]


def test_the_payload_entry_reports_unsafe_state_without_running_and_destroys_call_scratch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "loop").symlink_to(state)
    spec = _call_spec(tmp_path, state=str(state))
    (tmp_path / "call").mkdir()
    (tmp_path / "call" / "leftover").write_text("x")
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))

    assert main.main(["supervise", str(path)]) == 0

    lines = capsys.readouterr().out.splitlines()
    (record,) = [json.loads(line) for line in lines]
    assert record["ran"] is False
    assert record["bounds_violation"] == "symlink: loop"
    assert record["isolation_error"] is None
    assert not (tmp_path / "call").exists()


def test_a_quiet_poll_while_setup_runs_keeps_waiting_for_its_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = supervise.selectors.DefaultSelector

    class Quiet:
        def __init__(self) -> None:
            self.inner, self.quiet = real(), 1

        def register(self, *args: Any) -> Any:
            return self.inner.register(*args)

        def select(self, timeout: float) -> list[Any]:
            if self.quiet:
                self.quiet -= 1
                return []
            return self.inner.select(0)

    monkeypatch.setattr(supervise.selectors, "DefaultSelector", Quiet)
    read, write = os.pipe()
    os.write(write, b"late")
    os.close(write)

    assert supervise._read_status(read) == "late"
    os.close(read)


def test_a_root_supervisor_locks_the_call_directory_even_before_a_context_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    chowned: list[str] = []
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: chowned.append(Path(path).name))
    monkeypatch.setattr(os, "chmod", lambda path, mode: None)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    (tmp_path / "call").mkdir()

    supervise._prepare_dirs(_call_spec(tmp_path))

    assert chowned == ["call"]


def test_install_mode_normalisation_does_not_chmod_a_symlink_target(tmp_path: Path) -> None:
    root = tmp_path / "install"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("not part of the install")
    outside.chmod(0o600)
    (root / "python").symlink_to(outside)
    regular = root / "module.py"
    regular.write_text("value = 1\n")
    regular.chmod(0o600)

    install_tree._normalise_modes(root)

    assert stat.S_IMODE(outside.stat().st_mode) == 0o600
    assert (root / "python").is_symlink()
    assert stat.S_IMODE(regular.stat().st_mode) == 0o644
