"""Slow-tier behaviours split from :mod:`tests.test_verifier_bundle_and_isolation`."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from raidar.grading.sandbox import (
    RUNTIME_DIR,
    SandboxLayout,
    sandbox_env,
)
from tests.verifier_support import make_bundle

pytestmark = pytest.mark.slow


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv on PATH")
@pytest.mark.parametrize("with_lock", [False, True])
def test_uv_entrypoint_runs_offline_when_the_submission_declares_a_dev_group(
    tmp_path: Path, with_lock: bool
) -> None:
    # Pilot v2 forge-01 (aborted): a submission with `dev = ["pytest>=8"]` failed
    # every case because `uv run --project /app` tried to download pytest offline.
    app = tmp_path / "app"
    app.mkdir()
    (app / "pyproject.toml").write_text(
        '[project]\nname = "sub"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n'
        'dependencies = []\n\n[dependency-groups]\ndev = ["pytest>=8"]\n'
    )
    if with_lock:
        (app / "uv.lock").write_text(
            'version = 1\nrequires-python = ">=3.10"\n\n[[package]]\nname = "sub"\n'
            'version = "0.1.0"\nsource = { virtual = "." }\n'
        )
    (app / "prog.py").write_text("print('ran')\n")
    layout = SandboxLayout(
        app=str(app),
        tests=str(tmp_path / "tests"),
        runtime=str(RUNTIME_DIR),
        out=str(tmp_path / "out"),
        scratch=str(tmp_path / "scratch"),
        python=sys.executable,
    )
    env = sandbox_env(make_bundle(tmp_path), layout, coverage=False)
    assert env["UV_OFFLINE"] == "1"
    host = {k: v for k, v in os.environ.items() if not k.startswith("UV_") and k != "VIRTUAL_ENV"}
    result = subprocess.run(
        ["uv", "run", "--project", str(app), str(app / "prog.py")],
        cwd=app,
        env={**host, **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ran"
