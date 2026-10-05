"""The gate's ``tests`` check: the workspace's own tests must exist, pass and cover it.

Like a repository's commit check, the gate runs the contributor's own tests
(never the hidden verifier's: the gate only ever reads its ``--root``, and a
root under a verifier path is a gate error). It runs in a throwaway copy of the
workspace, so nothing the tests write reaches the workspace (a shadow gate
stays invisible).

- **Which tests.** Test files are the scope's (``test_*.py``, ``*_test.py``,
  files under ``tests/``...; ``conftest.py`` is pytest's, not a test file).
  ``unittest`` loads every test file, always; pytest runs the files with
  pytest-style tests (module-level ``test_*`` functions, ``Test*`` classes that
  are not ``TestCase``, a ``pytest`` import, or a ``conftest.py`` in the
  workspace) with its unittest plugin off, so no test runs twice.
- **Under coverage.** Both runners run in one ``coverage run`` with the
  project's interpreter, Python subprocesses included (``patch = subprocess``
  and the ``guardrails_site`` start-up hook), over the workspace copy.
- **Findings** (all block):
  - ``TEST.missing``: the change touches production code and no test ran (no
    test file, or test files without a test). "Touches" is git's view when the
    root is a git work tree with a commit (``git diff HEAD`` and untracked
    files, Python files only); otherwise every production file counts as
    changed. Git is consulted only when the root is the top of its own work
    tree, never a folder of an outer repository. A change with no Python
    source change and no tests passes;
  - ``TEST.failed``: a test failed or errored (one finding per failing test,
    capped), or the run did not finish within ``TESTS_TIMEOUT_SEC``;
  - ``TEST.coverage-minimum``: the owned line coverage (statements of the
    production files only; test files are not the measured slice) is below
    ``COVERAGE_MINIMUM_PERCENT`` (85, the original rule catalogue's
    ``TEST.coverage-minimum`` threshold), evaluated when at least one test ran.
- The outcome (``tests`` in the JSON report) records the counts per runner, the
  failing tests, the coverage and why the check was or was not required.

A failure of the runner itself (coverage cannot start, the interpreter is
missing) raises ``GateError``: a gate error, never a refusal or a pass.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from guardrails.diagnostics import Diagnostic
from guardrails.scope import EXCLUDED_DIRS, SourceFile

COVERAGE_MINIMUM_PERCENT = 85.0
TESTS_TIMEOUT_SEC = 180
GIT_TIMEOUT_SEC = 30
FAILED_FINDINGS_CAP = 20
SOURCE = "tests"
VERIFIER_ROOTS = ("/tests", "/logs/verifier")
CONFTEST = "conftest.py"


class TestsGateError(RuntimeError):
    """The test run itself could not run (a gate error, never a verdict)."""


@dataclass
class TestsOutcome:
    """What the tests check found, for the JSON report."""

    required: bool
    reason: str
    test_files: int = 0
    pytest_files: int = 0
    runners: dict[str, Any] = field(default_factory=dict)
    tests_ran: int = 0
    failed: int = 0
    coverage_percent: float | None = None
    coverage_minimum: float = COVERAGE_MINIMUM_PERCENT
    owned_files: int = 0
    timed_out: bool = False
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return dict(vars(self))


# ---- what changed ---------------------------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        return subprocess.run(
            [git, "-c", "safe.directory=*", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _own_work_tree(root: Path) -> bool:
    """Whether ``root`` is the top of its own git work tree (not a folder of an outer one)."""
    top = _git(root, "rev-parse", "--show-toplevel")
    if top is None or top.returncode != 0 or not top.stdout.strip():
        return False
    return Path(top.stdout.strip()).resolve() == root.resolve()


def _changed_python(root: Path) -> list[str] | None:
    """Python files changed since HEAD or untracked (None: git could not list them)."""
    changed = _git(root, "diff", "--name-only", "HEAD", "--", "*.py")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "--", "*.py")
    runs = (changed, untracked)
    if any(run is None or run.returncode for run in runs):
        return None
    return [line for run in runs if run for line in run.stdout.splitlines() if line.strip()]


def source_change(root: Path) -> tuple[bool, str]:
    """Whether any Python file changed since the last commit, and how that was decided."""
    if not _own_work_tree(root):
        return True, "not its own git work tree: every Python file counts as changed"
    head = _git(root, "rev-parse", "-q", "--verify", "HEAD")
    if head is None or head.returncode != 0:
        return True, "no commit to compare with: every Python file counts as changed"
    names = _changed_python(root)
    if names is None:
        return True, "git could not list the changes: every Python file counts as changed"
    if names:
        return True, f"{len(names)} Python file(s) changed since the last commit"
    return False, "no Python file changed since the last commit"


# ---- which tests ----------------------------------------------------------------------


def _is_test_case_base(base: ast.expr) -> bool:
    name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
    return str(name).endswith("TestCase")


def _pytest_node(node: ast.stmt) -> bool:
    """A module-level statement that only pytest would run or need."""
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return node.name.startswith("test")
    if isinstance(node, ast.ClassDef):
        return node.name.startswith("Test") and not any(map(_is_test_case_base, node.bases))
    if isinstance(node, ast.Import):
        return any(alias.name.split(".")[0] == "pytest" for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").split(".")[0] == "pytest"
    return False


def _pytest_style(tree: ast.Module) -> bool:
    return any(_pytest_node(node) for node in tree.body)


def pytest_files(tests: Sequence[SourceFile], has_conftest: bool) -> list[str]:
    """Test files with pytest-style tests (every test file when a conftest.py exists)."""
    found = []
    for source in tests:
        try:
            tree = ast.parse(source.path.read_bytes())
        except (SyntaxError, ValueError, OSError):
            found.append(source.relpath)  # pytest reports the broken file as an error
            continue
        if has_conftest or _pytest_style(tree):
            found.append(source.relpath)
    return found


# ---- running them ---------------------------------------------------------------------


def _copy_workspace(root: Path, target: Path) -> None:
    def ignored(folder: str, names: list[str]) -> list[str]:
        del folder
        return [name for name in names if name in EXCLUDED_DIRS or name.endswith(".egg-info")]

    shutil.copytree(root, target, symlinks=True, ignore=ignored)


def _tool_paths(home: Path) -> list[str]:
    """The gate's library, its start-up hook and its site-packages (pytest, coverage)."""
    site = sorted((home / "venv" / "lib").glob("python*/site-packages"))
    if not site:
        raise TestsGateError(f"no site-packages under {home / 'venv' / 'lib'}")
    return [str(home / "lib" / "guardrails_site"), str(home / "lib"), str(site[0])]


def _rcfile(work: Path, copy: Path) -> Path:
    """Measure the whole copy; the owned slice is picked from the report afterwards."""
    rc = work / "coveragerc"
    lines = [
        "[run]",
        f"source = {copy}",
        f"data_file = {work / 'coverage-data' / '.coverage'}",
        "parallel = true",
        "patch = subprocess",
        "",
    ]
    rc.write_text("\n".join(lines), encoding="utf-8")
    (work / "coverage-data").mkdir()
    return rc


@dataclass(frozen=True)
class _Run:
    python: str
    home: Path
    work: Path
    copy: Path
    production: tuple[SourceFile, ...]


# A caller's own coverage measurement (``COVERAGE_FILE``, pytest-cov's subprocess hook)
# never reaches the gate's test run or its coverage report.
CALLER_COVERAGE_PREFIXES = ("COVERAGE_", "COV_CORE_")


def _clean_environment() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith(CALLER_COVERAGE_PREFIXES)}


def _environment(tool_paths: Sequence[str]) -> dict[str, str]:
    env = _clean_environment()
    env["PYTHONPATH"] = os.pathsep.join(tool_paths)
    env["GUARDRAILS_TOOL_PATHS"] = os.pathsep.join(tool_paths[1:])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONHOME", None)
    return env


def _execute(run: _Run, spec: dict[str, Any], rc: Path) -> tuple[dict[str, Any] | None, bool]:
    """Run the tests; (result, timed out). A runner failure raises TestsGateError."""
    spec_path = run.work / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    argv = [run.python, "-m", "coverage", "run", f"--rcfile={rc}", "-m", "guardrails.testrun"]
    try:
        completed = subprocess.run(
            [*argv, str(spec_path)],
            cwd=run.copy,
            capture_output=True,
            text=True,
            check=False,
            timeout=TESTS_TIMEOUT_SEC,
            env=_environment(spec["tool_paths"]),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return None, True
    except OSError as exc:
        raise TestsGateError(f"the test run could not start: {exc}") from exc
    out = Path(spec["out"])
    if completed.returncode != 0 or not out.is_file():
        tail = (completed.stderr or completed.stdout).strip()[-400:]
        raise TestsGateError(f"the test runner failed (exit {completed.returncode}): {tail}")
    return json.loads(out.read_text(encoding="utf-8")), False


def _coverage_tool(run: _Run, rc: Path, verb: str, *extra: str) -> None:
    """Run one ``coverage`` subcommand on the test run's data (a failure is a gate error)."""
    tool = str(run.home / "venv" / "bin" / "coverage")
    done = subprocess.run(
        [tool, verb, f"--rcfile={rc}", "-q", *extra],
        cwd=run.copy,
        capture_output=True,
        text=True,
        check=False,
        timeout=TESTS_TIMEOUT_SEC,
        env=_clean_environment(),
    )
    if done.returncode not in (0, 1):
        raise TestsGateError(f"coverage {verb} failed: {done.stderr.strip()[-400:]}")


def _measured_files(run: _Run, report: Path) -> dict[str, dict[str, Any]]:
    """The coverage report's per-file data, keyed by workspace-relative path."""
    files = json.loads(report.read_text(encoding="utf-8")).get("files", {})
    copy = run.copy.resolve()
    resolved = {(run.copy / name).resolve(): data for name, data in files.items()}
    return {
        path.relative_to(copy).as_posix(): data
        for path, data in resolved.items()
        if path.is_relative_to(copy)
    }


def _coverage(run: _Run, rc: Path, production: Sequence[SourceFile]) -> tuple[float, int]:
    """Owned line coverage (percent) over the production files, and how many were measured."""
    report = run.work / "coverage.json"
    _coverage_tool(run, rc, "combine")
    _coverage_tool(run, rc, "json", "-o", str(report), "--ignore-errors")
    if not report.is_file():
        raise TestsGateError("coverage json wrote no report")
    measured = _measured_files(run, report)
    owned = [measured[s.relpath]["summary"] for s in production if s.relpath in measured]
    statements = sum(int(item["num_statements"]) for item in owned)
    covered = sum(int(item["covered_lines"]) for item in owned)
    percent = 100.0 if statements == 0 else round(100.0 * covered / statements, 2)
    return percent, len(owned)


# ---- findings -------------------------------------------------------------------------


def _last_line(text: object, copy: Path) -> str:
    """The error's last line, with the throwaway copy's path made workspace-relative."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    line = lines[-1] if lines else "(no message)"
    for prefix in {str(copy), str(copy.resolve())}:
        line = line.replace(f"{prefix}/", "")
    return line[-200:]


def _failed_findings(result: dict[str, Any], copy: Path) -> list[Diagnostic]:
    failing = [item for runner in result.values() for item in runner.get("failed_tests", [])]
    found = [
        Diagnostic(
            "TEST.failed",
            str(item.get("file") or "."),
            1,
            f"test failed: {item.get('id')}: {_last_line(item.get('error'), copy)}",
            source=SOURCE,
        )
        for item in failing[:FAILED_FINDINGS_CAP]
    ]
    total = sum(int(r.get("failed", 0)) + int(r.get("errors", 0)) for r in result.values())
    if total and not found:
        found.append(Diagnostic("TEST.failed", ".", 1, f"{total} test(s) failed", source=SOURCE))
    return found


def _missing(outcome: TestsOutcome) -> list[Diagnostic]:
    if not outcome.required:
        return []
    what = "test files without a test" if outcome.test_files else "no test files"
    message = (
        f"no tests ran ({what}): the change touches production code, so it needs tests "
        "(test_*.py or *_test.py files, or files under tests/; unittest or pytest style)"
    )
    return [Diagnostic("TEST.missing", ".", 1, message, source=SOURCE)]


def _coverage_finding(outcome: TestsOutcome) -> list[Diagnostic]:
    percent = outcome.coverage_percent
    if percent is None or percent >= COVERAGE_MINIMUM_PERCENT:
        return []
    message = (
        f"owned line coverage {percent:.1f}% < {COVERAGE_MINIMUM_PERCENT:.0f}% over "
        f"{outcome.owned_files} production file(s) (test files are not counted)"
    )
    return [Diagnostic("TEST.coverage-minimum", ".", 1, message, source=SOURCE)]


def _check_root(root: Path) -> None:
    resolved = root.resolve().as_posix()
    for hidden in VERIFIER_ROOTS:
        if resolved == hidden or resolved.startswith(f"{hidden}/"):
            raise TestsGateError(f"refusing to run tests under a verifier path ({resolved})")


def _run_suite(
    root: Path, run: _Run, outcome: TestsOutcome, files: tuple[list[str], list[str]]
) -> list[Diagnostic]:
    tests, pytest_style = files
    _copy_workspace(root, run.copy)
    rc = _rcfile(run.work, run.copy)
    spec = {
        "root": str(run.copy),
        "unittest_files": tests,
        "pytest_files": pytest_style,
        "tool_paths": _tool_paths(run.home),
        "out": str(run.work / "result.json"),
    }
    result, timed_out = _execute(run, spec, rc)
    if timed_out or result is None:
        outcome.timed_out = True
        message = f"the test run did not finish within {TESTS_TIMEOUT_SEC} s"
        return [Diagnostic("TEST.failed", ".", 1, message, source=SOURCE)]
    outcome.runners = result
    outcome.tests_ran = sum(int(r.get("ran", 0)) for r in result.values())
    outcome.failed = sum(int(r.get("failed", 0)) + int(r.get("errors", 0)) for r in result.values())
    found = _failed_findings(result, run.copy)
    if outcome.tests_ran == 0:
        return found + _missing(outcome)
    outcome.coverage_percent, outcome.owned_files = _coverage(run, rc, run.production)
    return found + _coverage_finding(outcome)


def _is_test_file(source: SourceFile) -> bool:
    return source.is_test and Path(source.relpath).name != CONFTEST


def _outcome(root: Path, sources: Sequence[SourceFile]) -> TestsOutcome:
    """Whether tests are required (a production Python change) and how many test files exist."""
    production = any(not source.is_test for source in sources)
    changed, why = source_change(root) if production else (False, "no production Python file")
    return TestsOutcome(required=changed, reason=why, test_files=sum(map(_is_test_file, sources)))


def tests_findings(
    root: Path, sources: Sequence[SourceFile], home: Path, python: str
) -> tuple[list[Diagnostic], TestsOutcome]:
    """The tests check's findings and outcome for one gate run."""
    started = time.monotonic()
    _check_root(root)
    outcome = _outcome(root, sources)
    tests = [source for source in sources if _is_test_file(source)]
    if not tests:
        found = _missing(outcome)
    else:
        has_conftest = any(Path(s.relpath).name == CONFTEST for s in sources)
        style = pytest_files(tests, has_conftest)
        outcome.pytest_files = len(style)
        production = tuple(source for source in sources if not source.is_test)
        with tempfile.TemporaryDirectory(prefix="guardrails-tests-") as tmp:
            run = _Run(python, home, Path(tmp), Path(tmp) / "workspace", production)
            found = _run_suite(root, run, outcome, ([s.relpath for s in tests], style))
    outcome.seconds = round(time.monotonic() - started, 3)
    return found, outcome
