"""Runner seams that need the real file system or a real ``python3``: the session store's
``ctime`` and the work-tree reading scripts the host sends into the container."""

from __future__ import annotations

import asyncio
import shlex
import time
from pathlib import Path

import pytest

from raidar.subject.workflow.acceptance_reply import PRESENT
from raidar.subject.workflow.judgeloop import SOURCE_SCRIPT
from raidar.subject.workflow.runner import SessionWatch

pytestmark = pytest.mark.slow


async def _python(*words: str) -> tuple[int, str]:
    """The exit code and stdout of ``python3 <words>`` run by a real shell."""
    process = await asyncio.create_subprocess_shell(
        shlex.join(["python3", *words]),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await process.communicate()
    assert process.returncode is not None
    return process.returncode, out.decode()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A work tree with one test file, a link out of it and a file beside it."""
    root = tmp_path / "app"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_app.py").write_text("def test_a():\n    assert 1\n")
    (tmp_path / "outside.py").write_text("def test_o(): ...\n")
    (root / "escape.py").symlink_to(tmp_path / "outside.py")
    return root


async def test_the_source_script_reads_a_file_in_the_tree_and_refuses_any_that_leaves_it(
    tree: Path,
) -> None:
    command = ("-I", "-B", "-c", SOURCE_SCRIPT, str(tree))

    assert await _python(*command, "tests/test_app.py") == (0, "def test_a():\n    assert 1\n")
    for refused in ("../outside.py", "escape.py", "tests", "tests/absent.py"):
        assert await _python(*command, refused) == (3, "")


async def test_the_present_script_names_only_the_files_that_are_files_inside_the_tree(
    tree: Path,
) -> None:
    names = ("tests/test_app.py", "../outside.py", "escape.py", "tests", "absent.py")

    code, out = await _python("-c", PRESENT, str(tree), *names)

    assert (code, out.splitlines()) == (0, ["tests/test_app.py"])


def test_a_session_log_only_appended_to_is_clean_and_a_write_then_restore_is_seen(
    tmp_path: Path,
) -> None:
    source, staged = tmp_path / "source", tmp_path / "staged"
    (source / "sessions").mkdir(parents=True)
    (staged / "sessions").mkdir(parents=True)
    original = b'{"a": 1}\n'
    watch = SessionWatch()

    def remote() -> dict[str, int]:  # what the container reports for the file's ctime
        return {"sessions/r.jsonl": (source / "sessions" / "r.jsonl").stat().st_ctime_ns}

    def keep(text: bytes, *, grew: bool = True) -> list[dict[str, str]]:
        (source / "sessions" / "r.jsonl").write_bytes(text)
        (staged / "sessions" / "r.jsonl").write_bytes(text)
        return watch.observe(staged, remote(), ("sessions",), grew_expected=grew)

    assert keep(original) == []
    assert keep(original + b'{"a": 2}\n') == []  # appended
    appended = original + b'{"a": 2}\n'
    time.sleep(0.05)  # a file system's ctime is coarse
    (source / "sessions" / "r.jsonl").write_bytes(b"tampered\n")  # a write ...
    (source / "sessions" / "r.jsonl").write_bytes(appended)  # ... and a restore
    (staged / "sessions" / "r.jsonl").write_bytes(appended)
    found = watch.observe(staged, remote(), ("sessions",), grew_expected=True)
    assert [item["name"] for item in found] == ["session_log_touched"]
    (staged / "sessions" / "r.jsonl").unlink()
    removed = watch.observe(staged, remote(), ("sessions",), grew_expected=True)
    assert [item["name"] for item in removed] == ["session_log_removed"]
