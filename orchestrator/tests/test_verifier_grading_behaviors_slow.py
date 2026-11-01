"""Slow-tier behaviours split from :mod:`tests.test_verifier_grading_behaviors`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raidar.grading.grader import BUDGET_MARGIN_S, grade_step
from raidar.grading.models import (
    CaseGroup,
    CaseStatus,
)
from raidar.grading.parity import fork_regression_run_evidence
from raidar.grading.sandbox import SandboxResult, SandboxRun
from tests.test_verifier_grading_behaviors import (
    FORGER as FORGER,
)
from tests.test_verifier_grading_behaviors import (
    SHADOW as SHADOW,
)
from tests.test_verifier_grading_behaviors import (
    SLOW_TESTS as SLOW_TESTS,
)
from tests.test_verifier_grading_behaviors import (
    bundle_fixture as bundle_fixture,
)
from tests.test_verifier_grading_behaviors import (
    sandbox_fixture as sandbox_fixture,
)
from tests.verifier_support import LocalProcessSandbox, make_bundle, write_app

pytestmark = pytest.mark.slow


# ------------------------------------------------------- real pytest runs
async def test_reference_solution_solves_and_prior_solution_fails_next_checkpoint(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    v1 = write_app(tmp_path / "v1")
    v2 = write_app(tmp_path / "v2", mul=True)

    first = await grade_step(bundle, "checkpoint_1", v1, sandbox, tmp_path / "w1", coverage=False)
    counts = first.grade.group_counts()
    assert first.grade.solved
    assert counts[CaseGroup.CORE].total == 1
    assert counts[CaseGroup.FUNCTIONALITY].total == 1
    assert counts[CaseGroup.ERROR].total == 1
    assert any(c.status is CaseStatus.SKIPPED for c in first.grade.cases)

    stale = await grade_step(bundle, "checkpoint_2", v1, sandbox, tmp_path / "w2", coverage=False)
    assert not stale.grade.solved
    assert stale.grade.group_counts()[CaseGroup.REGRESSION].passed == 3

    solved = await grade_step(bundle, "checkpoint_2", v2, sandbox, tmp_path / "w3", coverage=False)
    assert solved.grade.solved


async def test_submission_modules_cannot_replace_the_grader_tools(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    app = write_app(tmp_path / "app")  # checkpoint_2 is unsolved by this app
    for name in ("pytest.py", "coverage.py", "json.py"):
        (app / name).write_text(SHADOW)

    result = await grade_step(bundle, "checkpoint_2", app, sandbox, tmp_path / "w", coverage=True)

    assert result.grade.infra_failure is None
    assert not result.grade.solved
    assert any(c.id.endswith("test_multiplies") for c in result.grade.cases)
    assert result.parity is not None
    assert result.parity.eligible, result.parity


async def test_submission_cannot_forge_the_grading_ledger(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.py").write_text(FORGER.lstrip())

    result = await grade_step(bundle, "checkpoint_2", app, sandbox, tmp_path / "w", coverage=False)

    assert not result.grade.solved
    ledger = json.loads((tmp_path / "w" / "grading" / "out" / "ledger.json").read_text())
    assert "leaked" not in ledger  # the plugin's own ledger replaced the forgery
    assert json.loads((sandbox.root / "leaked.json").read_text()) == []
    assert ledger["plugin_id"] == "raidar-oracle-plugin@1"
    assert {c.status for c in result.grade.cases} >= {CaseStatus.FAILED}


@pytest.mark.parametrize(
    "ledger_text",
    ["{not json", '{"nodes": [{"node_id": "t.py::a"}], "exit_status": 0}'],
    ids=["truncated", "node_without_status"],
)
async def test_a_malformed_ledger_is_an_infra_failure_not_a_crash(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox, ledger_text: str
) -> None:
    class BrokenLedger(LocalProcessSandbox):
        async def run(self, run: SandboxRun) -> SandboxResult:
            result = await super().run(run)
            (run.out_dir / "ledger.json").write_text(ledger_text)
            return result

    app = write_app(tmp_path / "app")
    result = await grade_step(
        bundle, "checkpoint_2", app, BrokenLedger(sandbox.root), tmp_path / "w", coverage=False
    )

    assert result.grade.infra_failure is not None
    assert result.grade.infra_failure.code == "ledger_unreadable"
    assert not result.grade.solved


async def test_cases_left_when_the_grading_budget_runs_out_fail_rather_than_infra(
    tmp_path: Path, sandbox: LocalProcessSandbox
) -> None:
    fixture = make_bundle(tmp_path / "slow")
    (fixture.tests_dir / "test_checkpoint_1.py").write_text(SLOW_TESTS.lstrip())
    app = write_app(tmp_path / "app")
    # Budget = hard timeout - per-test timeout (none) - margin: 1 s of case starts.
    timeout_s = BUDGET_MARGIN_S + 1.0

    result = await grade_step(
        fixture, "checkpoint_1", app, sandbox, tmp_path / "w", coverage=False, timeout_s=timeout_s
    )

    assert result.grade.infra_failure is None
    assert not result.grade.solved
    statuses = {c.id.split("::")[1]: c.status for c in result.grade.cases}
    assert statuses["test_second"] is CaseStatus.ERROR
    assert "test_checkpoint_1.py::test_second" in result.grading.timing_sensitive
    assert result.grade.rewards()["infrastructure_failure"] == 0.0


async def test_regrading_the_same_snapshot_gives_identical_results(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    app = write_app(tmp_path / "app")
    first = await grade_step(bundle, "checkpoint_2", app, sandbox, tmp_path / "a", coverage=True)
    second = await grade_step(bundle, "checkpoint_2", app, sandbox, tmp_path / "b", coverage=True)
    assert first.grade == second.grade
    assert first.parity == second.parity
    assert first.grading.evidence == second.grading.evidence


async def test_grading_never_modifies_the_snapshot(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    app = write_app(tmp_path / "app", mul=True)
    before = (app / "app.py").read_bytes()
    await grade_step(bundle, "checkpoint_2", app, sandbox, tmp_path / "w", coverage=True)
    assert sorted(p.name for p in app.iterdir()) == ["app.py"]
    assert (app / "app.py").read_bytes() == before


async def test_coverage_pass_attributes_lines_per_test_with_parity(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    app = write_app(tmp_path / "app", mul=True)
    result = await grade_step(bundle, "checkpoint_2", app, sandbox, tmp_path / "w", coverage=True)

    assert result.parity is not None
    assert result.parity.eligible, result.parity
    assert result.coverage is not None
    assert result.coverage.evidence is not None
    contexts = {c.node_id: c for c in result.coverage.evidence.coverage}
    assert "test_checkpoint_2.py::test_multiplies" in contexts
    assert contexts["test_checkpoint_2.py::test_multiplies"].path == "app.py"
    assert result.grading.evidence is not None
    assert result.grading.evidence.coverage == ()
    assert result.coverage.evidence.measurement_prefix[:3] == ("-m", "coverage", "run")
    assert result.grading.evidence.invocation == result.coverage.evidence.invocation


async def test_verdict_flipped_by_instrumentation_makes_coverage_ineligible(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    app = write_app(tmp_path / "app", mul=True, coverage_sensitive=True)
    result = await grade_step(bundle, "checkpoint_2", app, sandbox, tmp_path / "w", coverage=True)

    assert result.grade.solved  # the uninstrumented grading run decides the verdict
    assert result.parity is not None
    assert not result.parity.eligible
    assert "outcome_mismatch" in result.parity.reasons
    assert any("test_adds" in note for note in result.parity_differences)


async def test_grading_infra_failure_skips_coverage_and_is_ineligible(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    (bundle.tests_dir / "conftest.py").write_text("raise RuntimeError('broken corpus')\n")
    app = write_app(tmp_path / "app")
    result = await grade_step(bundle, "checkpoint_1", app, sandbox, tmp_path / "w", coverage=True)

    assert result.grade.infra_failure is not None
    assert not result.grade.solved
    assert result.coverage is None
    assert result.parity is not None
    assert not result.parity.eligible
    assert result.parity.reasons[0].startswith("grading_infra_failure:")


async def test_fork_projection_carries_the_g_kernel_input_shape(
    tmp_path: Path, bundle, sandbox: LocalProcessSandbox
) -> None:
    app = write_app(tmp_path / "app", mul=True)
    result = await grade_step(bundle, "checkpoint_2", app, sandbox, tmp_path / "w", coverage=True)
    assert result.grading.evidence is not None
    assert result.coverage is not None
    assert result.coverage.evidence is not None

    baseline = fork_regression_run_evidence(result.grading.evidence, result.grading.cases)
    produced = fork_regression_run_evidence(result.coverage.evidence, result.coverage.cases)

    assert set(baseline) == {
        "environment",
        "corpus",
        "invocation",
        "outcomes",
        "ledger",
        "coverage",
        "process_events",
    }
    for key in ("environment", "corpus", "invocation"):
        assert baseline[key] == produced[key]
    assert all(len(value) == 64 for value in baseline["environment"].values())
    groups = {row["node_id"]: row["group_type"] for row in baseline["outcomes"]}
    assert groups["test_checkpoint_1.py::test_adds"] == "Regression"
    assert groups["test_checkpoint_2.py::test_multiplies"] == "Core"
    assert json.dumps(produced)  # serialisable as the fork's JSON input
