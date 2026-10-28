"""Contract tests for the call transport: confinement, bounds, evidence and integrity.

The trusted payload and supervisor run for real against a local stand-in environment; the
Linux-only isolation (UID drop, seccomp) is proven in a real container by the acceptance
suite, so here it is checked to fail closed and its filter program is checked for shape.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import struct
import sys
import tarfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from raidar.subject.primitive import calls
from raidar.subject.primitive.payload import isolate, supervise
from tests.primitive_support import UNCONFINED, LocalEnvironment, Tree, relocate

PY = sys.executable
CONTEXT = {"task": "write a note", "round": 0}


@pytest.fixture
def container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    relocate(monkeypatch, tmp_path / "container")
    return tmp_path


def _runner(root: Path, environment: LocalEnvironment | None = None) -> calls.CallRunner:
    return calls.CallRunner(environment or LocalEnvironment(), root / "logs", policy=UNCONFINED)


async def _python(
    runner: calls.CallRunner, root: Path, code: str, **options: Any
) -> calls.CallResult:
    return await runner.run(
        "ns/pkg", "setup/1", [PY, "-c", code], CONTEXT, cwd=str(root), **options
    )


async def _gone(pid: int) -> bool:
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        await asyncio.sleep(0.05)
    return False


# --------------------------------------------------------------------------- the call
async def test_a_call_gets_a_clean_environment_and_a_root_context(
    container: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIDAR_TEST_SECRET", "s3cret")
    code = (
        "import json, os, sys\n"
        "print(json.dumps({'env': sorted(os.environ), 'argv': sys.argv[1:],\n"
        "  'context': open(sys.argv[2]).read(), 'named': os.environ['RAIDAR_STAGE_CONTEXT'],\n"
        "  'home': os.environ['HOME']}))\n"
    )
    runner = _runner(container)

    first = await _python(runner, container, code)
    second = await _python(runner, container, code)

    seen = json.loads(first.stdout)
    assert first.usable
    assert first.exit_code == 0
    assert {"PATH", "LANG", "HOME", "TMPDIR", "RAIDAR_STAGE_CONTEXT"} <= set(seen["env"])
    assert "RAIDAR_TEST_SECRET" not in seen["env"]
    assert seen["argv"][0] == "--context"
    assert seen["named"] == seen["argv"][1]
    assert json.loads(seen["context"]) == CONTEXT
    assert json.loads(second.stdout)["home"] != seen["home"]
    record = json.loads(first.record_path.read_text())
    assert record["env_names"] == sorted(["PATH", "LANG", "HOME", "TMPDIR", "RAIDAR_STAGE_CONTEXT"])
    assert record["package"] == "ns/pkg"
    assert record["primitive"] == "setup/1"
    assert record["stdout_sha256"]
    assert record["integrity_error"] is None


async def test_a_gate_call_keeps_its_argv_and_gets_the_context_only_through_the_environment(
    container: Path,
) -> None:
    code = "import os, sys; print(sys.argv[1:], 'RAIDAR_STAGE_CONTEXT' in os.environ)"
    result = await _python(_runner(container), container, code, append_context=False)

    assert result.stdout.decode().strip() == "[] True"


async def test_child_exit_statuses_are_distinct_from_transport_and_integrity(
    container: Path,
) -> None:
    runner = _runner(container)

    failed = await _python(runner, container, "raise SystemExit(3)")
    killed = await _python(runner, container, "import os, signal; os.kill(os.getpid(), 9)")

    assert (failed.exit_code, failed.signal, failed.usable) == (3, None, False)
    assert (killed.exit_code, killed.signal, killed.usable) == (None, 9, False)
    assert failed.integrity_error is None
    assert killed.integrity_error is None


async def test_a_timeout_kills_the_whole_process_tree(container: Path) -> None:
    code = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen(['sleep', '60'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    result = await _python(_runner(container), container, code, timeout_s=1)

    assert result.timed_out
    assert result.exit_code is None
    assert not result.usable
    assert time.monotonic() - started < 20
    assert await _gone(int(result.stdout.split()[0]))


async def test_stdout_overflow_is_capped_and_unusable(container: Path) -> None:
    result = await _python(
        _runner(container), container, "import sys; sys.stdout.write('x' * 3_000_000)"
    )

    assert result.overflow
    assert not result.usable
    assert len(result.stdout) == supervise.STDOUT_CAP
    assert result.record["stdout_bytes"] >= supervise.STDOUT_CAP


async def test_only_the_tail_of_stderr_is_kept_with_its_full_digest(container: Path) -> None:
    result = await _python(
        _runner(container), container, "import sys; sys.stderr.write('a' * 20_000 + 'END')"
    )

    assert len(result.stderr) == supervise.STDERR_TAIL
    assert result.stderr.endswith(b"END")
    assert result.record["stderr_bytes"] == 20_003
    assert result.usable


async def test_missing_supervisor_evidence_is_an_integrity_failure(container: Path) -> None:
    class Silent(LocalEnvironment):
        async def exec(self, command: str, *args: Any, **kwargs: Any) -> Any:
            result = await super().exec(command, *args, **kwargs)
            if "supervise" in command:
                result.stdout = ""
            return result

    result = await _python(_runner(container, Silent()), container, "print(1)")

    assert result.integrity_error
    assert "missing call evidence" in result.integrity_error
    assert not result.usable


async def test_a_container_transport_failure_raises_instead_of_looking_like_a_child_failure(
    container: Path,
) -> None:
    class Broken(LocalEnvironment):
        async def exec(self, command: str, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("docker daemon gone")

    with pytest.raises(calls.TransportError, match="docker daemon gone"):
        await _python(_runner(container, Broken()), container, "print(1)")


@pytest.mark.parametrize("timeout", [0, -1, calls.MAX_TIMEOUT_S + 1])
async def test_timeouts_outside_the_contract_are_refused(container: Path, timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout_s"):
        await _python(_runner(container), container, "print(1)", timeout_s=timeout)


@pytest.mark.skipif(os.geteuid() == 0, reason="a root test host really drops to UID 1000")
async def test_unavailable_isolation_fails_closed_and_the_child_never_runs(
    container: Path,
) -> None:
    marker = container / "ran"
    runner = calls.CallRunner(LocalEnvironment(), container / "logs")  # the default policy

    result = await _python(runner, container, f"open({str(marker)!r}, 'w').close()")

    assert not marker.exists()
    assert not result.usable
    assert result.integrity_error


# ----------------------------------------------------------------- installed tool trees
async def test_installed_tool_bytes_are_verified_before_and_after_the_call(
    container: Path,
) -> None:
    environment = LocalEnvironment()
    runner = _runner(container, environment)
    tool = container / "tool"
    tool.mkdir()
    (tool / "module.py").write_text("x = 1\n")
    recorded = await calls.tree_manifest(
        environment, str(tool), bounds=False, links=True, entries=False
    )
    tree = Tree("ns/pkg", str(tool), recorded.digest)

    clean = await _python(runner, container, "print(1)", installed=[tree])
    tampered = await _python(
        runner,
        container,
        f"open({str(tool / 'module.py')!r}, 'a').write('x = 2')",
        installed=[tree],
    )
    stale = await _python(
        runner, container, "print(1)", installed=[Tree("ns/pkg", str(tool), "0" * 64)]
    )

    assert clean.usable
    assert tampered.exit_code == 0
    assert not tampered.usable
    assert tampered.integrity_error
    assert "during the call" in tampered.integrity_error
    assert not stale.ran
    assert stale.integrity_error
    assert "before the call" in stale.integrity_error


# ------------------------------------------------------------------------ watched state
async def test_unsafe_state_is_refused_before_the_tool_runs(container: Path) -> None:
    marker = container / "ran"
    code = f"open({str(marker)!r}, 'w').close()"
    state = container / "state"
    state.mkdir()
    (state / "link").symlink_to(container)
    runner = _runner(container)

    linked = await _python(runner, container, code, state=str(state))
    (state / "link").unlink()
    os.mkfifo(state / "pipe")
    piped = await _python(runner, container, code, state=str(state))

    assert linked.bounds_violation
    assert linked.bounds_violation.startswith("symlink")
    assert piped.bounds_violation
    assert piped.bounds_violation.startswith("special file")
    assert not marker.exists()
    assert linked.integrity_error is None
    assert not linked.ran
    assert not linked.usable


async def test_missing_state_is_a_state_the_tool_still_sees(container: Path) -> None:
    result = await _python(
        _runner(container), container, "print(1)", state=str(container / "absent")
    )

    assert result.bounds_violation is None
    assert result.ran


# -------------------------------------------------------------------- manifests, snapshots
def _tree(root: Path) -> Path:
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("alpha")
    (root / "sub" / "b.bin").write_bytes(b"\x00\xff")
    (root / "empty").write_bytes(b"")
    return root


async def test_a_manifest_lists_files_with_digests_and_is_stable(container: Path) -> None:
    environment = LocalEnvironment()
    root = _tree(container / "state")

    first = await calls.tree_manifest(environment, str(root))
    second = await calls.tree_manifest(environment, str(root))

    assert first.ok
    assert first.digest == second.digest
    assert first.files == 3
    assert {entry.path for entry in first.entries} == {"a.txt", "sub", "sub/b.bin", "empty"}
    assert next(e for e in first.entries if e.path == "empty").size == 0


@pytest.mark.parametrize(
    ("make", "expected"),
    [
        (lambda root: (root / "l").symlink_to(root), "symlink"),
        (lambda root: os.mkfifo(root / "p"), "special file"),
    ],
)
async def test_unsafe_entries_are_violations_never_read(
    container: Path, make: Callable[[Path], object], expected: str
) -> None:
    root = _tree(container / "state")
    make(root)

    found = await calls.tree_manifest(LocalEnvironment(), str(root))

    assert found.violation
    assert found.violation.startswith(expected)


async def test_state_beyond_the_bounds_is_a_violation(
    container: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    limits = {"max_bytes": 1 << 20, "max_files": 2, "max_depth": 32}
    monkeypatch.setattr(calls, "STATE_LIMITS", limits)
    root = _tree(container / "state")

    found = await calls.tree_manifest(LocalEnvironment(), str(root))

    assert found.violation
    assert "too many files" in found.violation


async def test_a_missing_path_is_reported_as_absent(container: Path) -> None:
    found = await calls.tree_manifest(LocalEnvironment(), str(container / "nope"))

    assert not found.exists
    assert found.violation == "missing"


async def test_an_unbounded_manifest_records_links_instead_of_following_them(
    container: Path,
) -> None:
    root = _tree(container / "tool")
    (root / "link").symlink_to("a.txt")

    found = await calls.tree_manifest(
        LocalEnvironment(), str(root), bounds=False, links=True, entries=True
    )

    link = next(entry for entry in found.entries if entry.path == "link")
    assert found.ok
    assert link.kind == "link"
    assert link.target == "a.txt"


async def test_a_snapshot_copies_regular_files_verified_host_side(container: Path) -> None:
    root = _tree(container / "state")
    destination = container / "copy"

    found = await calls.snapshot_state(LocalEnvironment(), str(root), destination)

    assert found.ok
    assert (destination / "a.txt").read_text() == "alpha"
    assert (destination / "sub" / "b.bin").read_bytes() == b"\x00\xff"
    single = container / "single"
    await calls.snapshot_state(LocalEnvironment(), str(root / "a.txt"), single)
    assert (single / "a.txt").read_text() == "alpha"


async def test_a_snapshot_of_unsafe_state_copies_nothing(container: Path) -> None:
    root = _tree(container / "state")
    (root / "l").symlink_to(root)
    destination = container / "copy"

    found = await calls.snapshot_state(LocalEnvironment(), str(root), destination)

    assert found.violation
    assert not destination.exists()


# ---------------------------------------- a manifest from a record is not a trusted answer
def _row(path: str, data: bytes = b"x", **fields: Any) -> dict[str, Any]:
    row = {
        "path": path, "type": "file", "size": len(data), "mode": 420,
        "sha256": hashlib.sha256(data).hexdigest(),
    }  # fmt: skip
    return {**row, **fields}


def _record(rows: list[dict[str, Any]]) -> calls.TreeManifest:
    document = {
        "path": "x", "exists": True, "violation": None, "files": len(rows),
        "bytes": sum(row["size"] for row in rows), "entries": rows,
        "digest": calls._entry_digest(rows),
    }  # fmt: skip
    return calls.record_manifest(document)


def _archive(path: Path, members: dict[str, bytes]) -> str:
    with tarfile.open(path, "w") as bundle:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return str(path)


class _Counting(LocalEnvironment):
    downloads = 0

    async def download_file(self, source: str, target: Path | str) -> None:
        type(self).downloads += 1
        await super().download_file(source, target)


@pytest.mark.parametrize(
    "name", ["../../escaped", "/tmp/escaped", "a/../../escaped", "a//b", "./a", "a/", ""]
)
async def test_a_manifest_name_that_leaves_the_destination_copies_nothing(
    container: Path, name: str
) -> None:
    archive = _archive(container / "state.tar", {name.lstrip("/") or "x": b"x"})
    manifest = _record([_row(name)])
    destination = container / "copy"

    found = await calls.copy_snapshot(LocalEnvironment(), manifest, archive, destination)

    assert found.violation
    assert not destination.exists()
    assert not (container / "escaped").exists()


@pytest.mark.parametrize(
    "rows",
    [
        [_row("a"), _row("a")],
        [_row("l", type="link", target="a")],
        [_row("a", size=calls.STATE_LIMITS["max_bytes"] + 1)],
        [_row("a", sha256="not-a-digest")],
        [_row("/".join(["d"] * 40))],
    ],
)
async def test_a_manifest_with_repeats_links_or_numbers_past_the_bounds_is_refused(
    container: Path, rows: list[dict[str, Any]]
) -> None:
    archive = _archive(container / "state.tar", {"a": b"x"})

    found = await calls.copy_snapshot(_Counting(), _record(rows), archive, container / "copy")

    assert found.violation
    assert _Counting.downloads == 0  # nothing was fetched for a manifest that cannot be unpacked
    assert not (container / "copy").exists()


async def test_long_unicode_paths_fit_the_snapshot_archive_bound(container: Path) -> None:
    folder = "/".join(["é" * 60] * 3)
    members = {f"{folder}/{number}.txt": b"x" for number in range(100)}
    archive = _archive(container / "state.tar", members)
    manifest = _record([_row(name, data) for name, data in members.items()])
    destination = container / "copy"

    found = await calls.copy_snapshot(LocalEnvironment(), manifest, archive, destination)

    assert found.violation is None
    assert {
        path.relative_to(destination).as_posix(): path.read_bytes()
        for path in destination.rglob("*.txt")
    } == members


async def test_an_archive_bigger_than_its_manifest_is_never_downloaded(container: Path) -> None:
    archive = _archive(container / "state.tar", {"a": b"x" * 1_000_000})
    _Counting.downloads = 0

    found = await calls.copy_snapshot(_Counting(), _record([_row("a")]), archive, container / "c")

    assert found.violation == "the archive is larger than its manifest allows"
    assert _Counting.downloads == 0


@pytest.mark.parametrize("data", [b"yy", b"zz"])
async def test_a_member_that_is_not_what_its_manifest_says_leaves_nothing(
    container: Path, data: bytes
) -> None:
    archive = _archive(container / "state.tar", {"a": data})
    manifest = _record([_row("a", b"x")])  # one byte, another digest

    found = await calls.copy_snapshot(LocalEnvironment(), manifest, archive, container / "copy")

    assert found.violation
    assert not (container / "copy").exists()


async def test_garbage_where_an_archive_should_be_is_a_violation_not_an_error(
    container: Path,
) -> None:
    (container / "state.tar").write_bytes(b"\x00not a tar" * 100)

    found = await calls.copy_snapshot(
        LocalEnvironment(), _record([_row("a")]), str(container / "state.tar"), container / "c"
    )

    assert found.violation
    assert not (container / "c").exists()


def test_a_malformed_record_manifest_is_a_violation_not_a_raise() -> None:
    for document in ({"path": "x"}, {"path": "x", "entries": 5}, {"entries": [{"path": 1}]}):
        assert calls.record_manifest(document).violation.startswith("malformed manifest")


# ------------------------------------------------------------- root helpers and floors
async def test_root_files_flags_and_resource_floors(container: Path) -> None:
    environment = LocalEnvironment()
    flag = str(container / "container" / "var" / "calls" / "closed" / "pkg")

    assert not await calls.root_file_exists(environment, flag)
    await calls.put_root_file(environment, flag, b"closed")
    assert await calls.root_file_exists(environment, flag)

    floors = await calls.resource_floors(environment, [str(container)])
    assert (floors.disk_free[str(container)] or 0) > 0


async def test_state_can_be_uploaded_regular_only(container: Path) -> None:
    environment = LocalEnvironment()
    source = _tree(container / "fixture")
    target = str(container / "container" / "var" / "calls" / "canary" / "state")

    await calls.upload_state(environment, source, target)
    assert (Path(target) / "a.txt").read_text() == "alpha"
    with pytest.raises(calls.TransportError):
        await calls.upload_state(environment, source, target)  # never over an existing tree
    (source / "link").symlink_to(source)
    with pytest.raises(ValueError, match="regular"):
        await calls.upload_state(environment, source, target + "2")


# ------------------------------------------------------------------ the network filter
@pytest.mark.parametrize("arch", ["x86_64", "aarch64"])
def test_the_seccomp_program_is_well_formed_and_names_the_denied_calls(arch: str) -> None:
    program = isolate.build_filter(arch)
    instructions = list(struct.iter_unpack("HBBI", program))

    for position, (code, jump_true, _, _) in enumerate(instructions):
        if code in (0x15, 0x35):
            assert position + 1 + jump_true < len(instructions)
    assert instructions[-1][0] == 0x06
    numbers = {k for code, _, _, k in instructions if code == 0x15}
    assert numbers >= {41 if arch == "x86_64" else 198, 425, 426, 427, 438}
