"""Host-side verifier contract and offline regrade (E1, E3)."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from click.testing import CliRunner
from harbor.models.trial.paths import TrialPaths
from harbor.trial.hooks import TrialEvent

from raidar.cli import main
from raidar.oracle import regrade as regrade_module
from raidar.oracle.bundle import BundleError
from raidar.oracle.guard import install_leak_guard
from raidar.oracle.leak import LeakScanError
from raidar.oracle.records import ORACLE_DIR, RECORD_FILE, load_step_record, step_record_dir
from raidar.oracle.regrade import RegradeError, RegradeOptions, regrade_path
from raidar.oracle.sandbox import GradingSandbox
from raidar.oracle.verifier import RaidarOracleVerifier
from tests.oracle_support import FakeAgentEnvironment, LocalProcessSandbox, make_bundle, write_app


class LocalOracleVerifier(RaidarOracleVerifier):
    """The production verifier with the grader container swapped for a host subprocess."""

    sandbox: LocalProcessSandbox

    async def prepare_sandbox(self) -> tuple[GradingSandbox, Any]:
        from raidar.oracle.records import GraderRef

        return self.sandbox, GraderRef(
            image="local",
            environment_id=await self.sandbox.environment_id(),
            image_built=False,
            timeout_s=self.grader_timeout_s,
        )


@dataclass
class FakeTrial:
    """The parts of a Harbor ``Trial`` the leak guard uses."""

    agent_environment: FakeAgentEnvironment
    paths: TrialPaths
    hooks: dict[TrialEvent, list[Any]] = field(default_factory=dict)

    def add_hook(self, event: TrialEvent, hook: Any) -> None:
        self.hooks.setdefault(event, []).append(hook)

    async def start_agent(self, step: str | None) -> None:
        results = [SimpleNamespace(step_name=step)] if step else None
        event = SimpleNamespace(result=SimpleNamespace(step_results=results))
        for hook in self.hooks.get(TrialEvent.AGENT_START, []):
            await hook(event)


async def start_agent_behind_guard(
    tmp_path: Path, env: FakeAgentEnvironment, bundle: Path, step: str | None
) -> None:
    trial = FakeTrial(env, TrialPaths(trial_dir=tmp_path / "trial"))
    install_leak_guard(cast(Any, trial), bundle)
    await trial.start_agent(step)


async def make_verifier(
    tmp_path: Path,
    env: FakeAgentEnvironment,
    *,
    step: str | None = "checkpoint_1",
    bundle_root: Path | None = None,
    guarded: bool = True,
    **kwargs: Any,
) -> LocalOracleVerifier:
    bundle = bundle_root or make_bundle(tmp_path / "fixture").root
    if guarded:
        await start_agent_behind_guard(tmp_path, FakeAgentEnvironment(), bundle, step)
    verifier = LocalOracleVerifier(
        oracle_bundle=str(bundle),
        task=cast(Any, None),
        trial_paths=TrialPaths(trial_dir=tmp_path / "trial"),
        environment=cast(Any, env),
        step_name=step,
        **kwargs,
    )
    verifier.sandbox = LocalProcessSandbox(tmp_path / "sandbox")
    return verifier


def place_step_artifact(tmp_path: Path, step: str, *, mul: bool = False) -> Path:
    return write_app(tmp_path / "trial" / "steps" / step / "artifacts" / "app", mul=mul)


def local_factory(tmp_path: Path):
    async def build(bundle: Any) -> GradingSandbox:
        return LocalProcessSandbox(tmp_path / "regrade-sandbox")

    return build


# ------------------------------------------------------------------ verifier
async def test_verifier_grades_the_artifact_and_never_uploads_to_the_agent_env(
    tmp_path: Path,
) -> None:
    env = FakeAgentEnvironment()
    place_step_artifact(tmp_path, "checkpoint_1")
    verifier = await make_verifier(tmp_path, env)

    result = await verifier.verify()

    assert result.rewards is not None
    assert result.rewards["solved"] == 1.0
    assert result.rewards["oracle_leak"] == 0.0
    assert env.calls == ["exec"]  # the leak assertion only; no upload, no download
    record = load_step_record(step_record_dir(tmp_path / "trial", "checkpoint_1"))
    assert record.snapshot.used == "artifact"
    assert record.snapshot.stored_path == "steps/checkpoint_1/artifacts/app"
    assert record.leak_check is not None
    assert record.leak_check.clean
    assert record.pre_agent_leak_check is not None
    assert record.pre_agent_leak_check.clean
    assert record.coverage.parity is not None
    assert record.coverage.parity.eligible
    # N1 attribution: the stored record carries the final wall time, and result
    # writing (including record.json) is a measured part of plumbing.
    timings = record.timings
    assert {"oracle_run_s", "coverage_run_s", "verifier_total_s"} <= set(timings)
    assert 0 < timings["result_write_s"] <= timings["verifier_plumbing_s"]


async def test_oracle_outcomes_stay_out_of_the_env_mounted_verifier_dir(tmp_path: Path) -> None:
    place_step_artifact(tmp_path, "checkpoint_1")
    verifier = await make_verifier(tmp_path, FakeAgentEnvironment())
    await verifier.verify()

    mounted = [TrialPaths(trial_dir=tmp_path / "trial").verifier_dir, tmp_path / "trial" / "agent"]
    assert all(not d.exists() or not any(d.iterdir()) for d in mounted)
    assert (tmp_path / "trial" / ORACLE_DIR / "steps" / "checkpoint_1" / RECORD_FILE).is_file()


async def test_missing_artifact_falls_back_to_download_and_keeps_a_regradable_copy(
    tmp_path: Path,
) -> None:
    env = FakeAgentEnvironment(app_source=write_app(tmp_path / "live-app"))
    verifier = await make_verifier(tmp_path, env, coverage=False)

    result = await verifier.verify()

    assert result.rewards is not None
    assert result.rewards["solved"] == 1.0
    assert env.calls == ["exec", "download:/app"]
    record = load_step_record(step_record_dir(tmp_path / "trial", "checkpoint_1"))
    assert record.snapshot.used == "download"
    assert record.snapshot.fallback_reason == "artifact_missing"
    assert (tmp_path / "trial" / record.snapshot.stored_path / "app.py").is_file()
    assert record.coverage.parity is None


async def test_a_leaked_hidden_test_forces_a_failing_reward(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path / "fixture")
    digest = next(iter(bundle.hidden_file_hashes()))
    env = FakeAgentEnvironment(leak_stdout=f"{digest}  /app/stolen.py\nRAIDAR_LEAK_SCAN_DONE\n")
    place_step_artifact(tmp_path, "checkpoint_1")
    verifier = await make_verifier(tmp_path, env, bundle_root=bundle.root, coverage=False)

    result = await verifier.verify()

    # No verdict or rate is reported for a leaked step, not even as 0.0.
    assert result.rewards == {"infrastructure_failure": 0.0, "oracle_leak": 1.0}
    record = load_step_record(step_record_dir(tmp_path / "trial", "checkpoint_1"))
    assert record.grade.solved  # the evidence is kept; only the reward is withheld
    assert record.leak_check is not None
    assert not record.leak_check.clean


async def test_hidden_tests_seen_before_the_agent_started_leak_the_step(tmp_path: Path) -> None:
    # Harbor empties /tests before verify() in shared multi-step mode, so only the
    # pre-agent scan can see a /tests copy baked into the agent image.
    bundle = make_bundle(tmp_path / "fixture").root
    baked = FakeAgentEnvironment(leak_stdout="TESTS\t/tests/conftest.py\nRAIDAR_LEAK_SCAN_DONE\n")
    await start_agent_behind_guard(tmp_path, baked, bundle, "checkpoint_1")
    place_step_artifact(tmp_path, "checkpoint_1")
    verifier = await make_verifier(
        tmp_path, FakeAgentEnvironment(), bundle_root=bundle, guarded=False, coverage=False
    )

    result = await verifier.verify()

    assert result.rewards is not None
    assert result.rewards["oracle_leak"] == 1.0
    assert "solved" not in result.rewards
    record = load_step_record(step_record_dir(tmp_path / "trial", "checkpoint_1"))
    assert record.leak_check is not None
    assert record.leak_check.clean  # the verify-time scan alone would have missed it
    assert record.pre_agent_leak_check is not None
    assert record.pre_agent_leak_check.tests_dir_entries == ("/tests/conftest.py",)


async def test_verifier_fails_closed_without_the_pre_agent_check(tmp_path: Path) -> None:
    place_step_artifact(tmp_path, "checkpoint_1")
    verifier = await make_verifier(tmp_path, FakeAgentEnvironment(), guarded=False)
    with pytest.raises(LeakScanError, match="install_leak_guard"):
        await verifier.verify()


async def test_guard_refuses_to_start_the_agent_when_the_scan_cannot_run(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path / "fixture").root
    broken = FakeAgentEnvironment(leak_stdout="RAIDAR_LEAK_SCAN_UNAVAILABLE\n")
    with pytest.raises(LeakScanError):
        await start_agent_behind_guard(tmp_path, broken, bundle, "checkpoint_1")


async def test_verifier_rejects_unknown_snapshot_sources(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="snapshot_source"):
        await make_verifier(tmp_path, FakeAgentEnvironment(), snapshot_source="volume")


async def test_single_step_verification_needs_a_one_step_bundle(tmp_path: Path) -> None:
    verifier = await make_verifier(tmp_path, FakeAgentEnvironment(), step=None, guarded=False)
    with pytest.raises(BundleError, match="one-step"):
        await verifier.verify()


# ------------------------------------------------------------------- regrade
async def test_regrade_over_stored_snapshots_reproduces_the_original_results(
    tmp_path: Path,
) -> None:
    bundle = make_bundle(tmp_path / "fixture")
    place_step_artifact(tmp_path, "checkpoint_1")
    place_step_artifact(tmp_path, "checkpoint_2")  # stale: checkpoint_2 fails
    for step in ("checkpoint_1", "checkpoint_2"):
        verifier = await make_verifier(
            tmp_path, FakeAgentEnvironment(), step=step, bundle_root=bundle.root
        )
        await verifier.verify()
    trial = tmp_path / "trial"
    before = {p: p.read_bytes() for p in trial.rglob("*") if p.is_file()}

    report = await regrade_path(trial, RegradeOptions(sandbox_factory=local_factory(tmp_path)))

    assert report["match"] is True
    steps = report["trials"][0]["steps"]  # type: ignore[index]
    assert [s["step"] for s in steps] == ["checkpoint_1", "checkpoint_2"]
    assert [s["regraded"]["solved"] for s in steps] == [True, False]
    assert {p: p.read_bytes() for p in trial.rglob("*") if p.is_file()} == before


async def test_regrade_reports_a_mismatch_against_a_different_record(tmp_path: Path) -> None:
    place_step_artifact(tmp_path, "checkpoint_1")
    await (await make_verifier(tmp_path, FakeAgentEnvironment(), coverage=False)).verify()
    record_path = step_record_dir(tmp_path / "trial", "checkpoint_1") / RECORD_FILE
    record = json.loads(record_path.read_text())
    record["grade"]["cases"][0]["status"] = "failed"
    record_path.write_text(json.dumps(record))

    report = await regrade_path(
        tmp_path / "trial", RegradeOptions(sandbox_factory=local_factory(tmp_path))
    )

    assert report["match"] is False
    step = report["trials"][0]["steps"][0]  # type: ignore[index]
    assert step["differences"] == [f"{record['grade']['cases'][0]['id']}: failed -> passed"]


async def test_regrade_refuses_a_snapshot_or_bundle_changed_since_grading(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path / "fixture")
    app = place_step_artifact(tmp_path, "checkpoint_1")
    verifier = await make_verifier(
        tmp_path, FakeAgentEnvironment(), bundle_root=bundle.root, coverage=False
    )
    await verifier.verify()
    options = RegradeOptions(sandbox_factory=local_factory(tmp_path))

    (bundle.tests_dir / "conftest.py").write_text("# edited\n")
    with pytest.raises(RegradeError, match="bundle"):
        await regrade_path(tmp_path / "trial", options)

    options = RegradeOptions(
        sandbox_factory=local_factory(tmp_path), bundle_override=make_bundle(tmp_path / "b2").root
    )
    (app / "app.py").write_text("print('changed')\n")
    with pytest.raises(RegradeError):
        await regrade_path(tmp_path / "trial", options)


class OtherImageSandbox(LocalProcessSandbox):
    async def environment_id(self) -> str:
        return "sha256:rebuilt-elsewhere"


async def test_regrade_uses_the_recorded_timeout_and_attributes_a_different_grader(
    tmp_path: Path,
) -> None:
    place_step_artifact(tmp_path, "checkpoint_1")
    verifier = await make_verifier(
        tmp_path, FakeAgentEnvironment(), coverage=False, grader_timeout_s=123.0
    )
    await verifier.verify()
    sandbox = OtherImageSandbox(tmp_path / "other")

    async def other_image(bundle: Any) -> GradingSandbox:
        return sandbox

    report = await regrade_path(tmp_path / "trial", RegradeOptions(sandbox_factory=other_image))

    assert [run.timeout_s for run in sandbox.runs] == [123.0]
    assert report["match"] is False
    step = report["trials"][0]["steps"][0]  # type: ignore[index]
    assert step["differences"] == [
        f"grader_environment: local:{sys.executable} -> sha256:rebuilt-elsewhere"
    ]
    assert step["regraded"] == step["original"]  # same cases; only the environment differs


def test_regrade_needs_oracle_records(tmp_path: Path) -> None:
    (tmp_path / "job" / "trial-a").mkdir(parents=True)
    with pytest.raises(RegradeError, match="records"):
        regrade_module.trial_dirs(tmp_path / "job")


# ----------------------------------------------------------------------- CLI
async def _verify(tmp_path: Path, **kwargs: Any) -> None:
    await (await make_verifier(tmp_path, FakeAgentEnvironment(), **kwargs)).verify()


def test_assess_regrade_cli_exit_code_follows_the_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    place_step_artifact(tmp_path, "checkpoint_1")
    asyncio.run(_verify(tmp_path, coverage=False))
    monkeypatch.setattr(
        regrade_module, "docker_sandbox_factory", lambda _ctx: local_factory(tmp_path)
    )
    out = tmp_path / "report.json"
    runner = CliRunner()

    ok = runner.invoke(main, ["assess", "--regrade", str(tmp_path), "--out", str(out)])
    assert ok.exit_code == 0, ok.output
    assert json.loads(out.read_text())["match"] is True

    record_path = step_record_dir(tmp_path / "trial", "checkpoint_1") / RECORD_FILE
    record = json.loads(record_path.read_text())
    record["grade"]["cases"] = record["grade"]["cases"][1:]
    record_path.write_text(json.dumps(record))
    mismatch = runner.invoke(main, ["assess", "--regrade", str(tmp_path / "trial")])
    assert mismatch.exit_code == 1
    assert '"match": false' in mismatch.output


def test_assess_requires_a_mode_and_reports_regrade_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    usage = runner.invoke(main, ["assess"])
    assert usage.exit_code == 2
    assert "--regrade" in usage.output
    empty = runner.invoke(main, ["assess", "--regrade", str(tmp_path)])
    assert empty.exit_code == 1
    assert "no raidar-oracle/ records" in empty.output
