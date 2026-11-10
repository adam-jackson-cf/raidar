"""Fast proofs of the adopted behavioral analyzer boundary, not nominal inheritance."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCOPE_ROOTS = (
    "orchestrator/src/raidar/scoring",
    "orchestrator/src/raidar/evaluation/analysis",
    "orchestrator/src/raidar/engine/health",
)


class AnalyzerChecker(Protocol):
    def analyzer_findings(self, root: Path, paths: list[str]) -> list[str]: ...


@pytest.fixture(scope="module")
def checker() -> AnalyzerChecker:
    script = ROOT / "scripts/checks/check-analyzer-scope.py"
    assert script.is_file(), "Missing PYS251/PYS252/PYS253 analyzer checker"
    spec = importlib.util.spec_from_file_location("analyzer_scope_proofs", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("AnalyzerChecker", module)


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _owned(root: Path) -> None:
    _write(root, "CODEOWNERS", "* @owner\n")


def _assert_finding(findings: list[str], relative: str, rule: str) -> None:
    assert len(findings) == 1, findings
    assert relative in findings[0]
    assert rule in findings[0]
    assert "\n" not in findings[0]
    assert ";" in findings[0], "A finding must include an actionable fix"


@pytest.mark.parametrize("scope", SCOPE_ROOTS)
def test_pure_analysis_accepts_injected_inputs(
    checker: AnalyzerChecker, tmp_path: Path, scope: str
) -> None:
    _owned(tmp_path)
    relative = f"{scope}/comparison.py"
    _write(tmp_path, relative, "def compare(values):\n    return sum(values)\n")
    assert checker.analyzer_findings(tmp_path, [relative]) == []


@pytest.mark.parametrize("scope", SCOPE_ROOTS)
@pytest.mark.parametrize(
    "operation",
    [
        "open('input.json')",
        "Path('input.json').open()",
        "Path('input.json').read_text()",
        "Path('input.json').read_bytes()",
        "Path('output.json').write_text('result')",
        "Path('output.json').write_bytes(b'result')",
        "os.open('input.json', os.O_RDONLY)",
        "candidate.read_text()",
        "candidate.open()",
    ],
)
def test_pys251_rejects_filesystem_access_in_every_scope(
    checker: AnalyzerChecker, tmp_path: Path, scope: str, operation: str
) -> None:
    _owned(tmp_path)
    relative = f"{scope}/comparison.py"
    _write(
        tmp_path,
        relative,
        "import os\nfrom pathlib import Path\n\n"
        "def compare():\n    candidate = Path('input.json')\n"
        f"    return {operation}\n",
    )
    _assert_finding(checker.analyzer_findings(tmp_path, [relative]), relative, "PYS251")


@pytest.mark.parametrize("scope", SCOPE_ROOTS)
@pytest.mark.parametrize(
    "operation",
    [
        "os.environ",
        "os.environ['TOKEN']",
        "os.environ.get('TOKEN')",
        "os.environ.items()",
        "os.environ.copy()",
        "os.getenv('TOKEN')",
        "subprocess.run(['tool'])",
        "subprocess.Popen(['tool'])",
        "subprocess.call(['tool'])",
        "subprocess.check_call(['tool'])",
        "subprocess.check_output(['tool'])",
        "subprocess.getoutput('tool')",
        "subprocess.getstatusoutput('tool')",
    ],
)
def test_pys252_rejects_environment_loads_and_all_process_calls(
    checker: AnalyzerChecker, tmp_path: Path, scope: str, operation: str
) -> None:
    _owned(tmp_path)
    relative = f"{scope}/comparison.py"
    _write(
        tmp_path,
        relative,
        f"import os\nimport subprocess\n\ndef compare():\n    return {operation}\n",
    )
    _assert_finding(checker.analyzer_findings(tmp_path, [relative]), relative, "PYS252")


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        ("from os import environ as environment\nresult = environment.items()\n", "PYS252"),
        ("import subprocess as processes\nresult = processes.check_output(['tool'])\n", "PYS252"),
        ("from subprocess import call as execute\nexecute(['tool'])\n", "PYS252"),
        (
            "from pathlib import Path as FilePath\nresult = FilePath('input').read_text()\n",
            "PYS251",
        ),
        ("from os import open as open_file\nresult = open_file('input', 0)\n", "PYS251"),
    ],
)
def test_import_aliases_do_not_bypass_the_boundary(
    checker: AnalyzerChecker, tmp_path: Path, source: str, rule: str
) -> None:
    _owned(tmp_path)
    relative = f"{SCOPE_ROOTS[0]}/comparison.py"
    _write(tmp_path, relative, source)
    _assert_finding(checker.analyzer_findings(tmp_path, [relative]), relative, rule)


def test_scope_is_not_a_similarly_named_sibling_package(
    checker: AnalyzerChecker, tmp_path: Path
) -> None:
    _owned(tmp_path)
    paths = [
        "orchestrator/src/raidar/scoring_tools/comparison.py",
        "orchestrator/src/raidar/evaluation/analysis_tools/comparison.py",
        "orchestrator/src/raidar/engine/health_tools/comparison.py",
    ]
    for relative in paths:
        _write(tmp_path, relative, "import os\nresult = os.environ.items()\n")
    assert checker.analyzer_findings(tmp_path, paths) == []


@pytest.mark.parametrize(
    ("relative", "source"),
    [
        (
            f"{SCOPE_ROOTS[0]}/code_quality/measurement.py",
            "import os\nimport subprocess\n\ndef run_process():\n"
            "    environment = dict(os.environ.items())\n"
            "    return subprocess.run(['tool'], env=environment)\n",
        ),
        (f"{SCOPE_ROOTS[0]}/framework/store.py", "payload = open('input.json').read()\n"),
        (
            f"{SCOPE_ROOTS[0]}/framework/trial_inputs.py",
            "from pathlib import Path\npayload = Path('input.json').read_bytes()\n",
        ),
        (f"{SCOPE_ROOTS[1]}/sources.py", "payload = open('input.json').read()\n"),
        (
            f"{SCOPE_ROOTS[2]}/base.py",
            "class Audit:\n    @classmethod\n    def from_disk(cls):\n"
            "        return open('input.json').read()\n\n"
            "def read_ledger():\n    return open('ledger.json').read()\n",
        ),
    ],
)
def test_the_five_adopted_io_seams_are_accepted(
    checker: AnalyzerChecker, tmp_path: Path, relative: str, source: str
) -> None:
    _owned(tmp_path)
    _write(tmp_path, relative, source)
    assert checker.analyzer_findings(tmp_path, [relative]) == []


def test_process_seam_does_not_exempt_the_entire_measurement_module(
    checker: AnalyzerChecker, tmp_path: Path
) -> None:
    _owned(tmp_path)
    relative = f"{SCOPE_ROOTS[0]}/code_quality/measurement.py"
    _write(
        tmp_path,
        relative,
        "import subprocess\n\ndef run_process():\n    return subprocess.run(['tool'])\n"
        "\ndef compare():\n    return subprocess.check_output(['other-tool'])\n",
    )
    _assert_finding(checker.analyzer_findings(tmp_path, [relative]), relative, "PYS252")


@pytest.mark.parametrize(
    "relative",
    [
        f"{SCOPE_ROOTS[0]}/framework/store.py",
        f"{SCOPE_ROOTS[0]}/framework/trial_inputs.py",
        f"{SCOPE_ROOTS[1]}/sources.py",
        f"{SCOPE_ROOTS[2]}/base.py",
    ],
)
def test_loading_seams_do_not_approve_process_launches(
    checker: AnalyzerChecker, tmp_path: Path, relative: str
) -> None:
    _owned(tmp_path)
    _write(tmp_path, relative, "import subprocess\nsubprocess.call(['tool'])\n")
    _assert_finding(checker.analyzer_findings(tmp_path, [relative]), relative, "PYS252")


def test_new_io_in_a_previously_noncompliant_file_is_not_hidden(
    checker: AnalyzerChecker, tmp_path: Path
) -> None:
    _owned(tmp_path)
    relative = f"{SCOPE_ROOTS[0]}/snapshot_tool/graph.py"
    _write(
        tmp_path,
        relative,
        "from pathlib import Path\n\ndef load():\n    return Path('old').read_text()\n",
    )
    old_findings = checker.analyzer_findings(tmp_path, [relative])
    _assert_finding(old_findings, relative, "PYS251")
    _write(
        tmp_path,
        relative,
        "from pathlib import Path\n\ndef load():\n    return Path('old').read_text()\n"
        "\ndef new_load():\n    return Path('new').read_text()\n",
    )
    current_findings = checker.analyzer_findings(tmp_path, [relative])
    assert len(current_findings) == 2
    assert len(set(current_findings)) == 2
    assert all(relative in finding and "PYS251" in finding for finding in current_findings)


@pytest.mark.parametrize(
    "relative",
    [
        f"{SCOPE_ROOTS[0]}/snapshot_tool/graph.py",
        f"{SCOPE_ROOTS[0]}/coverage_inputs/scorer.py",
    ],
)
def test_existing_io_modules_are_findings_not_blanket_exemptions(
    checker: AnalyzerChecker, tmp_path: Path, relative: str
) -> None:
    _owned(tmp_path)
    _write(tmp_path, relative, "from pathlib import Path\nresult = Path('input').read_bytes()\n")
    _assert_finding(checker.analyzer_findings(tmp_path, [relative]), relative, "PYS251")


@pytest.mark.parametrize("scope", SCOPE_ROOTS)
def test_pys253_requires_coverage_for_every_scope_root(
    checker: AnalyzerChecker, tmp_path: Path, scope: str
) -> None:
    owners = "".join(f"/{other}/ @owner\n" for other in SCOPE_ROOTS if other != scope)
    _write(tmp_path, "CODEOWNERS", owners)
    _assert_finding(checker.analyzer_findings(tmp_path, []), scope, "PYS253")


@pytest.mark.parametrize("owners", ["* @owner\n", "/orchestrator/ @owner\n"])
def test_pys253_accepts_applicable_inherited_ownership(
    checker: AnalyzerChecker, tmp_path: Path, owners: str
) -> None:
    _write(tmp_path, "CODEOWNERS", owners)
    assert checker.analyzer_findings(tmp_path, []) == []


def test_pys253_later_ownerless_pattern_removes_inherited_ownership(
    checker: AnalyzerChecker, tmp_path: Path
) -> None:
    _write(tmp_path, "CODEOWNERS", f"* @owner\n/{SCOPE_ROOTS[1]}/\n")
    _assert_finding(checker.analyzer_findings(tmp_path, []), SCOPE_ROOTS[1], "PYS253")


def test_pyq210_and_pyq211_do_not_invent_nominal_analyzer_contracts(
    checker: AnalyzerChecker, tmp_path: Path
) -> None:
    _owned(tmp_path)
    relative = f"{SCOPE_ROOTS[0]}/comparison.py"
    _write(
        tmp_path,
        relative,
        "from abc import ABC, abstractmethod\nfrom typing import Protocol\n\n"
        "class Scorer(Protocol):\n    def assess(self, context): ...\n\n"
        "class StructuralAnalyzer:\n    def assess(self, context):\n        return 1\n\n"
        "class CoverageScorer(ABC):\n"
        "    def assess(self, context):\n        return self.read(context)\n"
        "    @abstractmethod\n    def read(self, context): ...\n\n"
        "class CoverageAnalyzer(CoverageScorer):\n"
        "    def read(self, context):\n        return 1\n",
    )
    assert checker.analyzer_findings(tmp_path, [relative]) == []


def test_nominally_unrelated_scorer_still_has_behavioral_purity_obligations(
    checker: AnalyzerChecker, tmp_path: Path
) -> None:
    _owned(tmp_path)
    relative = f"{SCOPE_ROOTS[0]}/comparison.py"
    _write(
        tmp_path,
        relative,
        "import os\n\nclass StructuralScorer:\n"
        "    def assess(self, context):\n        return os.environ.items()\n",
    )
    _assert_finding(checker.analyzer_findings(tmp_path, [relative]), relative, "PYS252")
