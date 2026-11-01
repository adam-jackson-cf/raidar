"""code-quality@9 behaviour over stored trials (plan G2/G3 acceptance).

A trial's per-step snapshots and verifier records go in; the Assessment holds
V/E/H per checkpoint and R/G per transition with the fork's values, every
unavailable value explicitly statused (never zero), and evidence that the
store validates against the stored snapshots (file, sha256, line spans).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from raidar.scoring.code_quality import CodeQualityScorer
from raidar.scoring.code_quality.contract import EXPECTED_RUFF_VERSION
from raidar.scoring.code_quality.measurement import QUALITY_TOOL
from raidar.scoring.framework.builtin_manifests import CODE_QUALITY
from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.framework.validation import validate_result
from raidar.subject.environment.python.pack_definition import load_tool_lock
from raidar.subject.environment.python.provisioner import ToolProvisioningError
from tests.code_quality_support import case_trial, golden_case, trial_context


def test_an_unavailable_tool_environment_leaves_every_metric_unavailable(tmp_path: Path) -> None:
    def broken() -> Any:
        raise ToolProvisioningError("provisioning scb-python-quality failed: offline")

    ctx = trial_context(case_trial(tmp_path / "trial", golden_case("synthetic_shop")))
    result = validate_result(CODE_QUALITY, ctx, CodeQualityScorer(tools_factory=broken).assess(ctx))
    assert result.status is AssessStatus.UNAVAILABLE
    assert {item.status for item in result.observations} == {AssessStatus.UNAVAILABLE}
    assert "offline" in (result.reason or "")


def test_the_quality_tool_pins_are_the_scb_contract_and_the_test_env_pins() -> None:
    packages = set(load_tool_lock().tool(QUALITY_TOOL).packages)
    assert f"ruff=={EXPECTED_RUFF_VERSION}" in packages
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    dev = set(project["project"]["optional-dependencies"]["dev"])
    assert packages - {f"ruff=={EXPECTED_RUFF_VERSION}"} <= dev
