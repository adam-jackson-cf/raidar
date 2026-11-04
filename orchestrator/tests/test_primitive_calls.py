"""Seam tests for the call transport: the real payload process behind a local environment.

Decisions (evidence integrity, outcomes, bounds, manifest verification) are made on the real
code in ``test_primitive_calls_shape.py`` with a scripted container; here the trusted payload
and supervisor run for real, once per seam: environment and context hygiene, the process-tree
kill, installed-tree and watched-state enforcement, snapshots and the root helpers. The
Linux-only isolation (UID drop, seccomp) is proven in a real container by the acceptance suite.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from raidar.subject.primitive import calls
from tests.primitive_support import UNCONFINED, LocalEnvironment, Tree, relocate

pytestmark = pytest.mark.slow

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
    assert {"PATH", "LANG", "HOME", "TMPDIR", "RAIDAR_STAGE_CONTEXT"} <= set(seen["env"])
    assert "RAIDAR_TEST_SECRET" not in seen["env"]
    assert seen["argv"][0] == "--context"
    assert seen["named"] == seen["argv"][1]
    assert json.loads(seen["context"]) == CONTEXT
    assert json.loads(second.stdout)["home"] != seen["home"]
    assert json.loads(first.record_path.read_text())["integrity_error"] is None


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


# ------------------------------------------------------------- root helpers and floors
async def test_root_files_flags_and_resource_floors(container: Path) -> None:
    environment = LocalEnvironment()
    flag = str(container / "container" / "var" / "calls" / "closed" / "pkg")

    previous = os.umask(0)
    try:
        assert not await calls.root_file_exists(environment, flag)
        await calls.put_root_file(environment, flag, b"closed")
        assert await calls.root_file_exists(environment, flag)
    finally:
        os.umask(previous)
    for directory in (Path(flag).parent, Path(flag).parent.parent, Path(flag).parent.parent.parent):
        assert directory.stat().st_mode & 0o777 == 0o755
    assert Path(flag).stat().st_mode & 0o777 == 0o644

    floors = await calls.resource_floors(environment, [str(container)])
    assert (floors.disk_free[str(container)] or 0) > 0


async def test_state_can_be_uploaded_regular_only(container: Path) -> None:
    environment = LocalEnvironment()
    source = _tree(container / "fixture")
    target = str(container / "container" / "var" / "calls" / "canary" / "state")

    empty = container / "container" / "empty" / "ns" / "state"
    previous = os.umask(0)
    try:
        await calls.upload_state(environment, source, target)
        await calls.make_state_dir(environment, str(empty))
    finally:
        os.umask(previous)
    for root in (Path(target), empty):
        for directory in (root, root.parent, root.parent.parent):
            assert directory.stat().st_mode & 0o777 == 0o755
    assert (Path(target) / "a.txt").read_text() == "alpha"
    with pytest.raises(calls.TransportError):
        await calls.upload_state(environment, source, target)  # never over an existing tree
