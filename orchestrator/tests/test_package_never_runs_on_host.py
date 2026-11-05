"""V4: package code never runs on the host on the static data paths that touch a package tree.

The ``marker-tool`` fixture's ``__init__`` writes ``RAIDAR_IMPORT_MARKER`` when imported. The
package is copied into the stage tree; static loading, the lock check and a container-less
``stage-check`` must leave the marker absent and the module unimported. The run-directory paths
(health audit, assessment) are the slow-tier twins in ``test_package_lock_slow``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import uv
from click.testing import CliRunner

from raidar.cli import main, stage_fixtures, stage_lock, stage_package
from raidar.subject.package.registry import load_stage_package
from raidar.subject.workflow import stage_check

FIXTURE = Path(__file__).parent / "fixtures" / "stages" / "acme" / "marker-tool"
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
    package_root = _plant(root / "acme" / "marker-tool")
    monkeypatch.setattr(stage_package, "stages_root", lambda: root)
    monkeypatch.setattr(stage_fixtures, "_pack", lambda target: ("image", "context", "pack"))
    monkeypatch.setattr(stage_check, "PackContainer", NoDocker)

    locked_uv = uv.find_uv_bin()

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv[0] != locked_uv:
            return subprocess.CompletedProcess(argv, 1, "", "host uv must not lock packages")
        project = Path(argv[argv.index("--project") + 1])
        (project / "uv.lock").write_text((package_root / "tool" / "uv.lock").read_text())
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(stage_lock.subprocess, "run", run)

    package = load_stage_package(package_root)
    problems = stage_lock.lock_problems(package_root)
    locked = CliRunner().invoke(main, ["stage", "lock", "acme/marker-tool"])
    info = CliRunner().invoke(main, ["stage", "info", "acme/marker-tool"])
    listed = CliRunner().invoke(main, ["stage", "list"])
    checked = CliRunner().invoke(main, ["stage", "check", "acme/marker-tool"])

    assert package.name == "marker-tool"
    assert problems == [] or all("marker" not in item for item in problems)
    assert locked.exit_code == 0, locked.output
    assert info.exit_code == 0
    assert listed.exit_code == 0
    assert checked.exit_code != 0  # no container: it stops, it does not fall back to the host
    _assert_untouched(marker)
