"""The command blocks on every finding; there is no baseline of waived findings."""

from __future__ import annotations

import importlib.util
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
        ["check-analyzer-scope.py", "--repo-root", str(root)],
    )


def test_every_environment_operation_blocks_without_a_baseline(
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
    assert not (tmp_path / "scripts/checks/baselines").exists()
    assert checker.main() == 1
    lines = capsys.readouterr().out.splitlines()
    assert [line.split("; ")[0] for line in lines] == [
        f"{SOURCE}:3:12 PYS252 direct os.environ.get('OLD')",
        f"{SOURCE}:3:35 PYS252 direct os.environ.get('NEW')",
    ]


def test_a_clean_scope_passes(
    checker: Checker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _workspace(tmp_path, "def compare(left, right):\n    return left == right\n")
    _argv(monkeypatch, tmp_path)
    assert checker.main() == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("blocker", ["ownership", "syntax"])
def test_invalid_scope_blocks(
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
    assert checker.main() == 1
    assert ("PYS000" if blocker == "syntax" else "PYS253") in capsys.readouterr().out


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
