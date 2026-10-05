"""Run the workspace's own tests under coverage (python-guardrails@3 tests check).

    python -m coverage run --rcfile RC -m guardrails.testrun SPEC

Run by ``guardrails.tests_check`` with the *project's* interpreter (the
workspace's ``.venv/bin/python`` when it has one, else ``python3``), in a
throwaway copy of the workspace, so nothing the tests write reaches the
workspace. SPEC is a JSON file:

- ``root``: the copy's root (the working directory);
- ``unittest_files``: every test file, loaded with ``unittest`` (discovery
  always runs: a ``unittest.TestCase`` anywhere in a test file is run);
- ``pytest_files``: the test files with pytest-style tests (module-level
  ``test_*`` functions, non-``TestCase`` ``Test*`` classes, a ``pytest``
  import or a ``conftest.py``), run by pytest with its unittest plugin off,
  so no test runs twice;
- ``tool_paths``: the gate's own ``sys.path`` entries (its site-packages and
  library), moved to the end of ``sys.path`` so the project's packages win;
- ``out``: where the JSON result goes.

The result counts, per runner, the tests that ran, failed, errored and were
skipped, and names the failing tests with their files. Any exception here
(other than a test's own) is printed and exits 3: the caller reports it as a
gate error, never as a pass.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

FAILED_IDS_CAP = 50


def _demote_tool_paths(tool_paths: list[str]) -> None:
    tools = {os.path.realpath(path) for path in tool_paths}
    kept = [path for path in sys.path if os.path.realpath(path or ".") not in tools]
    sys.path[:] = kept + [path for path in tool_paths if path]


def _module_name(root: Path, path: Path) -> str | None:
    """The dotted name when every directory between root and the file is a package."""
    relative = path.relative_to(root)
    folders = relative.parent.parts
    for depth in range(len(folders)):
        if not (root.joinpath(*folders[: depth + 1]) / "__init__.py").is_file():
            return None
    return ".".join((*folders, path.stem))


def _load(root: Path, relpath: str, index: int) -> ModuleType:
    path = root / relpath
    dotted = _module_name(root, path)
    if dotted is not None:
        return importlib.import_module(dotted)
    folder = str(path.parent)
    if folder not in sys.path:
        sys.path.insert(1, folder)
    name = f"_guardrails_tests_{index}_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {relpath}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _test_file(test: unittest.TestCase, files: dict[str, str]) -> str:
    return files.get(type(test).__module__, "")


def run_unittest(root: Path, relpaths: list[str]) -> dict[str, Any]:
    """Load every test file and run its ``TestCase`` tests."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    files: dict[str, str] = {}
    import_errors: list[dict[str, str]] = []
    for index, relpath in enumerate(relpaths):
        try:
            module = _load(root, relpath, index)
        except BaseException as exc:  # noqa: BLE001 - a broken test file is a failing test
            import_errors.append({"id": relpath, "file": relpath, "error": repr(exc)[:300]})
            continue
        files[module.__name__] = relpath
        suite.addTests(loader.loadTestsFromModule(module))
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
    failed = [
        {"id": test.id(), "file": _test_file(test, files), "error": text.strip()[-300:]}
        for test, text in [*result.failures, *result.errors]
        if isinstance(test, unittest.TestCase)
    ]
    return {
        "ran": result.testsRun,
        "failed": len(result.failures) + len(result.unexpectedSuccesses),
        "errors": len(result.errors) + len(import_errors),
        "skipped": len(result.skipped),
        "failed_tests": (failed + import_errors)[:FAILED_IDS_CAP],
    }


class _Counter:
    """A pytest plugin that counts the outcome of every test it ran."""

    def __init__(self) -> None:
        self.ran = self.failed = self.errors = self.skipped = 0
        self.failed_tests: list[dict[str, str]] = []

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.skipped:
            self.skipped += 1
        elif report.when == "call":
            self.ran += 1
            if report.failed:
                self.failed += 1
                self._note(report)
        elif report.failed:  # setup or teardown
            self.errors += 1
            self._note(report)

    def pytest_collectreport(self, report: Any) -> None:
        if report.failed:
            self.errors += 1
            self._note(report)

    def _note(self, report: Any) -> None:
        nodeid = str(getattr(report, "nodeid", "") or "")
        text = str(getattr(report, "longreprtext", "") or "")
        self.failed_tests.append(
            {"id": nodeid, "file": nodeid.split("::", 1)[0], "error": text.strip()[-300:]}
        )


def run_pytest(root: Path, relpaths: list[str]) -> dict[str, Any]:
    """Run the pytest-style test files with pytest (its unittest plugin off)."""
    import pytest

    counter = _Counter()
    code = pytest.main(
        [
            *relpaths,
            "-p",
            "no:cacheprovider",
            "-p",
            "no:unittest",
            "-q",
            "-o",
            "addopts=",
        ],
        plugins=[counter],
    )
    return {
        "ran": counter.ran,
        "failed": counter.failed,
        "errors": counter.errors,
        "skipped": counter.skipped,
        "exit_code": int(code),
        "failed_tests": counter.failed_tests[:FAILED_IDS_CAP],
    }


def main(argv: list[str]) -> int:
    spec = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    root = Path(spec["root"])
    _demote_tool_paths(list(spec["tool_paths"]))
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    results: dict[str, Any] = {"unittest": run_unittest(root, list(spec["unittest_files"]))}
    if spec["pytest_files"]:
        results["pytest"] = run_pytest(root, list(spec["pytest_files"]))
    Path(spec["out"]).write_text(json.dumps(results), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except BaseException as exc:  # the runner's own failure: a gate error, never a verdict
        print(f"guardrails.testrun: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(3) from None
