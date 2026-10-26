"""``define`` as a shell runs it from the uploaded package: the heredoc, the isolation, the exit
codes."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import raidar.subject.workflow.define_tool as payload
from raidar.subject.workflow.define_tool import tool

ISSUE = (
    "# Greeting\n\nThe greeting is printed when the program runs.\n\n"
    "The greeting names the caller.\n"
)
PACKAGE = Path(payload.__file__).parent
# What the container's ``define`` command is: the pinned interpreter, isolated, on the package
# uploaded to a directory of its own.
LAUNCHER = """\
#!/bin/sh
exec "{python}" -I -c 'import sys
sys.path.insert(0, "{root}")
from define_tool import tool
raise SystemExit(tool.main())' "$@"
"""


def shell(command: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", "-c", command],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@pytest.fixture
def home(tmp_path: Path) -> tuple[dict[str, str], Path]:
    root = tmp_path / "opt"
    shutil.copytree(PACKAGE, root / "define_tool", ignore=shutil.ignore_patterns("__pycache__"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launcher = bin_dir / "define"
    launcher.write_text(LAUNCHER.format(python=sys.executable, root=root))
    launcher.chmod(0o755)
    state = tmp_path / "state"
    tool.start(state, tool.Context(ISSUE, (), "", (), str(tmp_path), 2))
    env = {
        "PATH": os.pathsep.join([str(bin_dir), os.environ["PATH"]]),
        tool.STATE_ENV: str(state),
    }
    work = tmp_path / "work"
    work.mkdir()
    return env, work


def test_a_quoted_heredoc_passes_every_byte_of_a_value_through(
    home: tuple[dict[str, str], Path],
) -> None:
    env, work = home
    awkward = '{"code": 1, "msg": "a: b"} ```py $HOME \\n \\"q\\" EOF `x -> y`'
    script = f"define put <<'EOF'\nfeature F1: f\nac F1.AC1: {awkward}\nEOF\ndefine show\n"

    done = shell(script, env, work)

    assert done.returncode == 0
    assert f"ac F1.AC1: {awkward}\n" in done.stdout


def test_the_tool_exits_with_its_findings_and_prints_them(
    home: tuple[dict[str, str], Path],
) -> None:
    env, work = home

    wrong = shell("define put <<'EOF'\ncolour: red\nEOF\n", env, work)
    unknown = shell("define frob", env, work)

    assert (wrong.returncode, unknown.returncode) == (tool.FAULT, tool.USAGE_ERROR)
    assert "- line 1: unknown key `colour`" in wrong.stdout
    assert unknown.stdout == tool.USAGE + "\n"


def test_a_state_that_is_not_there_is_the_tool_failing(
    home: tuple[dict[str, str], Path], tmp_path: Path
) -> None:
    env, work = home
    elsewhere = {**env, tool.STATE_ENV: str(tmp_path / "nowhere")}

    done = shell("define show", elsewhere, work)

    assert done.returncode == tool.BROKEN
    assert done.stdout.startswith("define: ERROR, the state cannot be used (")
    assert "Traceback" not in done.stderr


def test_a_package_planted_in_the_workspace_is_not_imported(
    home: tuple[dict[str, str], Path],
) -> None:
    # `-I`: no PYTHONPATH and no current directory on the import path, so the
    # workspace cannot stand in for the uploaded code.
    env, work = home
    planted = work / "define_tool"
    planted.mkdir()
    (planted / "__init__.py").write_text("raise SystemExit('planted')\n")
    poisoned = {**env, "PYTHONPATH": str(work)}

    done = shell("define show", poisoned, work)

    assert (done.returncode, done.stdout) == (0, "")
    assert "planted" not in done.stderr


def test_calls_run_at_once_by_a_shell_each_land_and_none_crashes(
    home: tuple[dict[str, str], Path],
) -> None:
    env, work = home
    count = 12
    puts = "".join(
        f"define put <<'EOF' >out-{n}.txt 2>&1 &\nac F1.AC{n}: c\nsource: U1\nEOF\n"
        for n in range(1, count + 1)
    )
    script = f"define put <<'EOF' >/dev/null\nfeature F1: f\nEOF\n{puts}wait\ndefine show\n"

    done = shell(script, env, work)

    assert done.stdout.count("\nac F1.AC") + done.stdout.startswith("ac F1.AC") == count
    assert "Traceback" not in done.stderr + "".join(
        path.read_text() for path in work.glob("out-*.txt")
    )


@pytest.mark.parametrize("number", ["²", "9" * 5000])
def test_a_decision_number_python_cannot_read_is_a_finding_not_a_traceback(
    home: tuple[dict[str, str], Path], number: str
) -> None:
    env, work = home

    done = shell(f"define put <<'EOF'\ndecision {number}: text\nrung: 1\nEOF\n", env, work)

    assert done.returncode == tool.FAULT
    assert "is not a number: write `decision 1: <text>`" in done.stdout
    assert "Traceback" not in done.stderr


def test_a_state_directory_the_tool_cannot_write_is_the_tool_failing_not_a_traceback(
    home: tuple[dict[str, str], Path],
) -> None:
    env, work = home
    state = Path(env[tool.STATE_ENV])
    state.chmod(0o555)
    try:
        done = shell("define put <<'EOF'\ntitle: t\nEOF\n", env, work)
    finally:
        state.chmod(0o755)

    assert done.returncode == tool.BROKEN
    assert done.stdout.startswith("define: ERROR, the state cannot be used (")
    assert "Traceback" not in done.stderr


def test_the_status_command_reports_a_sealed_definition_and_writes_it(
    home: tuple[dict[str, str], Path], tmp_path: Path
) -> None:
    env, work = home
    output = tmp_path / "out" / "feature-definition.md"
    script = (
        "define put <<'EOF'\n"
        "title: Greeting\nobjective: Print a greeting politely.\n"
        "feature F1: Greeting\noutcome: The greeting is printed.\n"
        "ac F1.AC1: The greeting is printed.\nsource: U1 U2\n"
        "aspect: the program runs -> the greeting is printed\n"
        "EOF\n"
        "define done ready\n"
    )
    sealed = shell(script, {**env, tool.OUTPUT_ENV: str(output)}, work)
    status = subprocess.run(
        [sys.executable, "-I", "-c", STATUS.format(root=tmp_path / "opt"), "--write"],
        env={**env, tool.OUTPUT_ENV: str(output)},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert sealed.returncode == 0, sealed.stdout
    assert output.read_text().startswith("# Greeting\n")
    assert status.returncode == 0, status.stdout
    assert '"state": "ready"' in status.stdout


STATUS = (
    'import sys; sys.path.insert(0, "{root}"); '
    "from define_tool import status; raise SystemExit(status.main())"
)
