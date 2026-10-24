"""The N1 fixture experiment (plan D4): real Harbor trials, no LLM, then ``raidar perf check``.

A pack-bound two-checkpoint problem (the real WS-B importer over the fake SCB
dataset, graded by the Docker oracle) x {baseline, guided} x 5 attempts, run
with Harbor's no-LLM ``oracle`` agent at concurrency 1, so every budgeted
per-trial phase has 10 samples and every per-step phase 20. Needs Docker with
the Python pack image, so it runs only when opted in with
``RAIDAR_EXECUTION_DOCKER=1``::

    make perf-fixture [PERF_FIXTURE_DIR=<dir>] [PERF_FIXTURE_CONCURRENCY=3]

``RAIDAR_PERF_FIXTURE_CONCURRENCY`` (default 1) runs the same fixture with more
trials at once, to measure the phases under host contention. ``make perf-gate``
runs it at concurrency 1 and 3 and is the enforced overhead gate: the check
here is ``--strict`` against the accepted budgets, unchanged. The strict JSON
report is written to ``<fixture dir>/perf-check.json``.

Opted in, a missing prerequisite fails the test instead of skipping it, so a
CI job or ``make perf-fixture`` can never pass without running the fixture.
The Docker target is the one ``TARGET=`` / ``RAIDAR_TARGET`` / the targets file selects
(else the current Docker context); its configured data check must pass.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.packs.python.image import image_tag
from raidar.packs.python.models import load_pack_spec
from raidar.problems.store import ROOT_ENV
from raidar.targets.config import Target, TargetError
from raidar.targets.health import ensure_target
from tests.component_trial_support import docker_target
from tests.execution_support import imported_pack_fixture, locked_experiment

OPT_IN = "RAIDAR_EXECUTION_DOCKER"
# The fixture is the ENFORCED N1 gate (make perf-gate): it checks strictly, unlike the
# report-only default that ``make perf-check`` applies to real experiment runs.
STRICT_CHECK = ("perf", "check", "--strict")
CONCURRENCY_ENV = "RAIDAR_PERF_FIXTURE_CONCURRENCY"
pytestmark = pytest.mark.skipif(
    os.environ.get(OPT_IN) != "1",
    reason=f"the perf fixture runs real Harbor trials: set {OPT_IN}=1 (make perf-fixture)",
)


def _require_target() -> Target:
    """Opted in: every prerequisite must hold, or the fixture fails (never skips).

    The target is the one ``RAIDAR_TARGET`` / the targets file / the current Docker
    context selects; its reachability and configured data check must pass.
    """
    try:
        target = docker_target()
        ensure_target(target)
    except TargetError as error:
        pytest.fail(str(error))
    tag = image_tag(load_pack_spec())
    found = subprocess.run(
        ["docker", "--context", target.docker_context, "image", "inspect", tag],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if found.returncode != 0:
        pytest.fail(
            f"pack image {tag} not present on docker context {target.docker_context!r}; "
            "make pack-build"
        )
    return target


def test_fixture_experiment_runs_and_holds_the_n1_budgets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _require_target()
    context = target.docker_context
    root = Path(os.environ.get("RAIDAR_PERF_FIXTURE_DIR") or tmp_path)
    concurrency = int(os.environ.get(CONCURRENCY_ENV) or 1)
    monkeypatch.setenv("DOCKER_CONTEXT", context)
    fixture = imported_pack_fixture(root / "fixture", context)
    monkeypatch.setenv(ROOT_ENV, str(fixture.store.root))
    (root / "exp" / "components").mkdir(parents=True, exist_ok=True)
    (root / "exp" / "components" / "ruff.toml").write_text("line-length = 100\n")
    loaded, _, _ = locked_experiment(
        root / "exp",
        fixture.ref,
        conditions=[
            {"id": "baseline", "components": []},
            {
                "id": "guided",
                "components": [{"use": "lint-config@1", "files": ["components/ruff.toml"]}],
            },
        ],
        analysis={
            "baseline_arm": "baseline",
            "attempts": 5,
            "seed": 20260930,
            "concurrency": concurrency,
        },
    )
    runner = CliRunner()
    runs = root / "jobs"
    ran = runner.invoke(
        main,
        [
            "run",
            str(loaded.path),
            "--agent-override",
            "oracle",
            "--allow-unsmoked",  # the no-LLM fixture is its own check, not a measured experiment
            "--jobs-dir",
            str(runs),
            "--job-id",
            "perf-fixture",
        ],
    )
    assert ran.exit_code == 0, ran.output
    job_dir = runs / loaded.spec.id / "perf-fixture"
    records = [
        json.loads(p.read_text()) for p in sorted((job_dir / "trial-records").glob("*.json"))
    ]
    assert len(records) == 10
    for record in records:
        assert all(check["passed"] for check in record["validity"]["checks"]), record["validity"]
        assert [a["checkpoint_state"] for a in record["step_results"]] == ["solved", "solved"]
    checked = runner.invoke(main, [*STRICT_CHECK, str(job_dir)])
    print(checked.output)
    report = runner.invoke(main, [*STRICT_CHECK, "--json", str(job_dir)])
    (root / "perf-check.json").write_text(report.output)
    assert checked.exit_code == 0, checked.output
