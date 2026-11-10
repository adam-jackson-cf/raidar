"""Real-process canaries for repository gate exit codes and path diagnostics."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.quality

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("returncode", [0, 17])
def test_dispatcher_preserves_child_exit_status(tmp_path: Path, returncode: int) -> None:
    script = tmp_path / "scripts/checks/run-repository-gate.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/checks/run-repository-gate.py", script)
    gate_directory = tmp_path / ".omp"
    gate_directory.mkdir()
    (gate_directory / "gate.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "checks": {"commit": [sys.executable, "-c", f"raise SystemExit({returncode})"]},
            }
        )
    )

    result = subprocess.run(
        [sys.executable, str(script), "commit"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == returncode, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_dispatcher_hook_keeps_linked_worktree_repository_non_bare(tmp_path: Path) -> None:
    environment = os.environ.copy()
    for variable in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_PREFIX",
    ):
        environment.pop(variable, None)

    def git(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    repository = tmp_path / "main"
    repository.mkdir()
    assert git("init", "-q", cwd=repository).returncode == 0
    for key, value in (
        ("user.name", "Repository Gate Canary"),
        ("user.email", "gate@example.invalid"),
        ("commit.gpgsign", "false"),
        ("core.hooksPath", str(repository / ".git/hooks")),
    ):
        assert git("config", key, value, cwd=repository).returncode == 0
    initial = git("commit", "--allow-empty", "--no-verify", "-m", "Initial", cwd=repository)
    assert initial.returncode == 0, initial.stderr
    worktree = tmp_path / "linked"
    added = git("worktree", "add", "-b", "linked", str(worktree), cwd=repository)
    assert added.returncode == 0, added.stderr

    dispatcher = worktree / "scripts/checks/run-repository-gate.py"
    dispatcher.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/checks/run-repository-gate.py", dispatcher)
    gate_directory = worktree / ".omp"
    gate_directory.mkdir()
    scratch_init = (
        "import os, subprocess, tempfile\n"
        "with tempfile.TemporaryDirectory() as scratch:\n"
        "    os.chdir(scratch)\n"
        "    subprocess.run(['git', 'init', '-q', '--bare'], check=True, timeout=10)\n"
    )
    (gate_directory / "gate.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "checks": {"commit": [sys.executable, "-c", scratch_init]},
            }
        )
    )
    hook = repository / ".git/hooks/pre-commit"
    hook.write_text(
        "#!/bin/sh\nexec " + shlex.join([sys.executable, str(dispatcher), "commit"]) + "\n"
    )
    hook.chmod(0o755)
    (worktree / "tracked.txt").write_text("Worktree commit exercises the gate hook.\n")
    assert git("add", "tracked.txt", cwd=worktree).returncode == 0

    committed = git("commit", "-m", "Run repository gate", cwd=worktree)
    bare = git(
        "--git-dir", str(repository / ".git"), "config", "--get", "core.bare", cwd=repository
    )

    assert bare.returncode == 0, bare.stderr
    assert bare.stdout.strip() == "false", committed.stderr
    assert committed.returncode == 0, committed.stderr


def test_path_check_accepts_a_clean_owned_path() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/checks/check-repository-paths.py"), "README.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_path_check_refuses_a_misplaced_file_with_one_actionable_line() -> None:
    # This canary must be at the root to exercise real folder-placement policy.
    with tempfile.NamedTemporaryFile(
        dir=ROOT, prefix="gate-canary-", suffix=".txt", mode="w", encoding="utf-8"
    ) as candidate:
        candidate.write("misplaced file\n")
        candidate.flush()
        relative = Path(candidate.name).relative_to(ROOT).as_posix()
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/checks/check-repository-paths.py"), relative],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    assert result.returncode == 1, result.stderr
    assert result.stdout == ""
    lines = result.stderr.splitlines()
    assert len(lines) == 1
    assert re.fullmatch(
        rf"{re.escape(relative)}: STRUCT\.folder-placement: [^\r\n;]+; [^\r\n]+", lines[0]
    )
