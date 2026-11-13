"""Fast behavioural proofs for repository hygiene policy."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]


class Checker(Protocol):
    def subprocess_findings(self, root: Path, inventory: Sequence[str]) -> list[str]: ...


@pytest.fixture
def checker() -> Checker:
    spec = importlib.util.spec_from_file_location(
        "timeout_hygiene_proof", ROOT / "scripts/checks/check-test-hygiene.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("Checker", module)


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\nsubprocess.run(['tool'], timeout=10)\n",
        "from subprocess import check_output as capture\ncapture(['tool'], timeout=2)\n",
        "import subprocess as sp\np = sp.Popen(['tool'])\np.communicate(timeout=2)\n",
        "from subprocess import Popen\np = Popen(['tool'])\np.wait(timeout=2)\n",
        "import asyncio\nasync def probe():\n"
        "    p = await asyncio.create_subprocess_exec('tool')\n"
        "    await asyncio.wait_for(p.wait(), timeout=2)\n",
        "class Event:\n    def wait(self): pass\nEvent().wait()\n",
    ],
)
def test_bounded_process_waits_and_unrelated_methods_are_accepted(
    tmp_path: Path, checker: Checker, source: str
) -> None:
    path = tmp_path / "orchestrator/tests/test_probe.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    assert checker.subprocess_findings(tmp_path, [path.relative_to(tmp_path).as_posix()]) == []


@pytest.mark.parametrize("root", ["orchestrator/tests", "lab/stages/acme/probe/tests"])
@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\nsubprocess.run(['tool'])\n",
        "from subprocess import call as invoke\ninvoke(['tool'], timeout=None)\n",
        "import subprocess\nsubprocess.check_call(['tool'], timeout=0)\n",
        "import subprocess as sp\np = sp.Popen(['tool'])\np.wait()\n",
        "from subprocess import Popen\np = Popen(['tool'])\np.communicate()\n",
        "import subprocess\np = subprocess.Popen(['tool'], timeout=2)\np.wait()\n",
        "import asyncio\nasync def probe():\n"
        "    p = await asyncio.create_subprocess_exec('tool')\n"
        "    await p.wait()\n",
    ],
)
def test_unbounded_process_waits_are_rejected_in_both_test_roots(
    tmp_path: Path, checker: Checker, root: str, source: str
) -> None:
    path = tmp_path / root / "test_probe.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    found = checker.subprocess_findings(tmp_path, [path.relative_to(tmp_path).as_posix()])
    assert found
    assert all("TEST.subprocess-timeouts" in line for line in found)
    assert any(f"{root}/test_probe.py" in line for line in found)


def test_application_processes_are_not_test_timeout_findings(
    tmp_path: Path, checker: Checker
) -> None:
    path = tmp_path / "orchestrator/src/raidar/probe.py"
    path.parent.mkdir(parents=True)
    path.write_text("import subprocess\nsubprocess.run(['tool'])\n", encoding="utf-8")
    assert checker.subprocess_findings(tmp_path, [path.relative_to(tmp_path).as_posix()]) == []
