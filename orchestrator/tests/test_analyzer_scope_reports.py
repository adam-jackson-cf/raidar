"""Reports preserve operation identities and fail closed outside baseline debt."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.quality

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "orchestrator/src/raidar/scoring/comparison.py"


class Checker(Protocol):
    def main(self) -> int: ...

    def analyzer_findings(self, root: Path, paths: list[str]) -> list[str]: ...


@pytest.fixture(scope="module")
def checker() -> Checker:
    spec = importlib.util.spec_from_file_location(
        "analyzer_scope_report_tests", ROOT / "scripts/checks/check-analyzer-scope.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast("Checker", module)


def _workspace(root: Path, source: str) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=10)
    (root / "CODEOWNERS").write_text("* @owner\n", encoding="utf-8")
    path = root / SOURCE
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")


def _argv(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["check-analyzer-scope.py", "--repo-root", str(root), "--format", "findings-json"],
    )


def test_report_preserves_each_environment_operation_for_the_shared_ratchet(
    checker: Checker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _workspace(
        tmp_path,
        "import os\ndef compare():\n    return os.environ.get('OLD'), os.environ.get('NEW')\n",
    )
    _argv(monkeypatch, tmp_path)
    assert checker.main() == 0
    findings = json.loads(capsys.readouterr().out)
    assert findings == [
        {"path": SOURCE, "code": "PYS252", "symbol": "compare", "source_line": operation}
        for operation in ("os.environ.get('OLD')", "os.environ.get('NEW')")
    ]


@pytest.mark.parametrize("blocker", ["ownership", "syntax"])
def test_invalid_scope_cannot_be_serialized_as_baselineable_io_debt(
    checker: Checker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    blocker: str,
) -> None:
    _workspace(tmp_path, "def broken(:\n" if blocker == "syntax" else "result = 1\n")
    if blocker == "ownership":
        (tmp_path / "CODEOWNERS").write_text("", encoding="utf-8")
    _argv(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as error:
        checker.main()
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert ("PYS000" if blocker == "syntax" else "PYS253") in captured.err


def test_codeowners_single_star_does_not_cross_directory_separators(
    checker: Checker, tmp_path: Path
) -> None:
    (tmp_path / "CODEOWNERS").write_text("/orchestrator/*/ @owner\n", encoding="utf-8")
    # This owns src/, so all three roots inherit it, even though '*' cannot
    # directly consume the deeper hierarchy. An ownerless exact-depth override
    # must not accidentally erase ownership several levels below it.
    with (tmp_path / "CODEOWNERS").open("a", encoding="utf-8") as stream:
        stream.write("/orchestrator/*/health/\n")
    assert checker.analyzer_findings(tmp_path, []) == []


def test_codeowners_recursive_glob_can_remove_one_subsystem_owner(
    checker: Checker, tmp_path: Path
) -> None:
    (tmp_path / "CODEOWNERS").write_text("* @owner\n/orchestrator/**/health/\n", encoding="utf-8")
    findings = checker.analyzer_findings(tmp_path, [])
    assert len(findings) == 1
    assert "engine/health/ PYS253" in findings[0]
