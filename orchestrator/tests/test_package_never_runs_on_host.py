"""V4: package code never runs on the host, on any data path that touches a package tree.

The ``marker-tool`` fixture's ``__init__`` writes ``RAIDAR_IMPORT_MARKER`` when imported. The
package is copied wherever each path might read one (the stage tree, a trial's evidence, a job
being assessed); static loading, the lock check, a container-less ``stage-check``, the health
audit and an assessment run must all leave the marker absent and the module unimported.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from raidar.cli import main, stage_fixtures, stage_lock, stage_package
from raidar.scoring.framework.rescore import rescore_path
from raidar.scoring.framework.store import AssessmentStore
from raidar.subject.package.registry import load_stage_package
from raidar.subject.workflow import stage_check
from tests.health_support import healthy_trial
from tests.scorer_support import fake_registry, two_step_trial
from tests.test_trial_health_audit import audit

FIXTURE = Path(__file__).parent / "fixtures" / "stages" / "marker-tool"
MODULE = "marker_tool"


class NoDocker:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> Any:
        raise RuntimeError("no docker in this test")

    async def __aexit__(self, *exc: Any) -> None:
        return None


@pytest.fixture
def marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "imported.marker"
    monkeypatch.setenv("RAIDAR_IMPORT_MARKER", str(path))
    sys.modules.pop(MODULE, None)
    return path


def _plant(destination: Path) -> Path:
    shutil.copytree(FIXTURE, destination)
    return destination


def _assert_untouched(marker: Path) -> None:
    assert not marker.exists(), "package code ran on the host"
    assert MODULE not in sys.modules


def test_static_loading_the_lock_check_and_a_containerless_check_never_import_the_tool(
    tmp_path: Path, marker: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "stages"
    package_root = _plant(root / "marker-tool")
    monkeypatch.setattr(stage_package, "stages_root", lambda: root)
    monkeypatch.setattr(stage_fixtures, "_pack", lambda target: ("image", "context", "pack"))
    monkeypatch.setattr(stage_check, "PackContainer", NoDocker)

    def uv(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
        (project / "uv.lock").write_text((package_root / "tool" / "uv.lock").read_text())
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(stage_lock, "_uv", uv)

    package = load_stage_package(package_root)
    problems = stage_lock.lock_problems(package_root)
    locked = CliRunner().invoke(main, ["stage", "lock", "marker-tool"])
    info = CliRunner().invoke(main, ["stage", "info", "marker-tool"])
    listed = CliRunner().invoke(main, ["stage", "list"])
    checked = CliRunner().invoke(main, ["stage", "check", "marker-tool"])

    assert package.name == "marker-tool"
    assert problems == [] or all("marker" not in item for item in problems)
    assert locked.exit_code == 0, locked.output
    assert info.exit_code == 0
    assert listed.exit_code == 0
    assert checked.exit_code != 0  # no container: it stops, it does not fall back to the host
    _assert_untouched(marker)


def test_the_health_audit_never_imports_a_package_tree_in_a_trial(
    tmp_path: Path, marker: Path
) -> None:
    t = healthy_trial(tmp_path)
    t.seal()
    _plant(t.trial_dir / "stages" / "marker-tool")
    _plant(t.trial_dir / "steps" / "checkpoint_1" / "agent" / "stages" / "marker-tool")

    checks = audit(t)

    assert checks
    _assert_untouched(marker)


def test_an_assessment_never_imports_a_package_tree_in_a_trial(
    tmp_path: Path, marker: Path
) -> None:
    trial = two_step_trial(tmp_path / "jobs" / "trial")
    _plant(trial / "stages" / "marker-tool")

    records = rescore_path(
        trial, ["fake-quality@1"], AssessmentStore(tmp_path / "s"), fake_registry()
    )

    assert records
    _assert_untouched(marker)
