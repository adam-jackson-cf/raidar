"""Real-filesystem and stored-trial seams of the scoring framework.

Special-file evidence is refused without hanging a run; health's dependency readings, the H5
setup limit, the trial-level usage layout and the rescore CLI are proved over stored trials.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest
import yaml
from click.testing import CliRunner

from raidar.cli import main
from raidar.design.experiment.schema import ProtocolSpec
from raidar.engine.health.audit import audit_trial
from raidar.engine.health.dependencies import GRADER_LOG
from raidar.engine.health.model import HealthCheck
from raidar.engine.job.assessment import ASSESSMENT_CONFIG_FILE
from raidar.engine.job.provenance import effective_limits
from raidar.grading.records import load_step_record, step_record_dir, write_step_record
from raidar.grading.submission_deps import SubmissionDependencies
from raidar.scoring.catalog.builtin import default_scorer_registry
from raidar.scoring.framework import result as result_module
from raidar.scoring.framework.registry import ScorerRegistration
from raidar.scoring.framework.rescore import RescoreError, _loaded_trials, rescore_path
from raidar.scoring.framework.store import AssessmentStore
from raidar.scoring.framework.trial_inputs import load_trial_context
from raidar.scoring.framework.validation import validate_result
from raidar.scoring.resource_usage import resource_usage_scorer
from tests.health_support import STEP, healthy_trial
from tests.scorer_support import fake_manifest, two_step_context, two_step_trial
from tests.test_process_compliance import REF, UNDETERMINED, _ambiguous, _by_key
from tests.trace_trial_support import (
    TracedStep,
    agent_step,
    execution,
    trajectory,
    usage,
    write_traced_trial,
)

pytestmark = pytest.mark.slow


def _dependencies(declared: tuple[str, ...], unprovided: list[dict[str, str]]) -> object:
    return SubmissionDependencies.model_validate(
        {
            "declared": declared,
            "set_sha256": "s" * 64,
            "source": "resolved",
            "lock_text": "",
            "lock_sha256": "l" * 64,
            "arch": "arm64",
            "provided": (),
            "unprovided": unprovided,
            "resolve_s": 0.0,
            "build_s": 0.0,
            "image_id": "sha256:x",
        }
    )


def _readings(
    tmp_path: Path, declared: tuple[str, ...], unprovided: list[dict[str, str]], log: str | None
) -> dict[str, HealthCheck]:
    t = healthy_trial(tmp_path)
    record_dir = step_record_dir(t.trial_dir, STEP)
    ledger = load_step_record(record_dir)
    deps = _dependencies(declared, unprovided)
    write_step_record(record_dir, ledger.model_copy(update={"submission_dependencies": deps}))
    if log is not None:
        (record_dir / GRADER_LOG).write_text(log)
    report = audit_trial(t.job_dir, t.trial_dir, t.record, t.job())
    return {c.id: c for c in report.checks}


def test_d1_and_d2_record_structured_evidence_per_step(tmp_path: Path) -> None:
    unprovided = [{"requirement": "sdistonly==1", "reason": "no-wheel"}]
    log = "ModuleNotFoundError: No module named 'polars'\n"
    checks = _readings(tmp_path, ("sdistonly==1",), unprovided, log)
    assert checks["D1"].status == "pass"
    assert checks["D1"].evidence["steps"] == {STEP: unprovided}
    assert checks["D2"].evidence["steps"] == {STEP: [{"module": "polars", "basis": "heuristic"}]}
    assert checks["D2"].evidence["unmeasured"] == []


def test_a_missing_grader_log_is_unmeasured_not_unaffected(tmp_path: Path) -> None:
    checks = _readings(tmp_path, (), [], None)
    assert checks["D1"].evidence["steps"] == {STEP: []}
    assert checks["D2"].status == "unmeasured"
    assert checks["D2"].evidence["unmeasured"] == [STEP]


@pytest.mark.parametrize("setup_override", [None, 120.0])
def test_recorded_setup_limits_make_h5_measured_and_enforced(
    tmp_path: Path, setup_override: float | None
) -> None:
    config = SimpleNamespace(override_timeout_sec=900, override_setup_timeout_sec=setup_override)
    plan = SimpleNamespace(
        arms={"baseline": SimpleNamespace(agent_config=config)},
        loaded=SimpleNamespace(spec=SimpleNamespace(protocol=ProtocolSpec())),
    )
    trial = healthy_trial(tmp_path)
    trial.set_job(effective_limits=effective_limits(plan))  # type: ignore[arg-type]
    report = audit_trial(trial.job_dir, trial.trial_dir, trial.record, trial.job())
    assert next(check for check in report.checks if check.id == "H5").status == "pass"
    trial.patch_result(
        lambda result: result["agent_setup"].update(finished_at="2026-09-30T14:00:00+00:00")
    )
    report = audit_trial(trial.job_dir, trial.trial_dir, trial.record, trial.job())
    assert next(check for check in report.checks if check.id == "H5").status == "violation"


def test_one_shot_trials_read_trial_level_usage_and_timing(tmp_path: Path) -> None:
    """A single-step Harbor trial reports usage and timing at the trial level."""
    trial = write_traced_trial(
        tmp_path / "trial",
        [TracedStep("checkpoint_1", trace=trajectory(agent_step(1)), usage=usage(7, 3, 0.2))],
    )
    result_path = trial / "result.json"
    stored = json.loads(result_path.read_text())
    (step,) = stored.pop("step_results")
    stored["agent_result"] = step["agent_result"]
    stored["agent_execution"] = execution("00:00", "00:12.5")
    result_path.write_text(json.dumps(stored))
    shutil.move(trial / "steps" / "checkpoint_1" / "agent", trial / "agent")
    ctx = load_trial_context(trial)
    scorer = resource_usage_scorer()
    observed = _by_key(validate_result(scorer.manifest, ctx, scorer.assess(ctx)))
    assert observed["total_tokens", "trajectory"].value == 10
    assert observed["agent_wall_s", "checkpoint_1"].value == pytest.approx(12.5)
    assert observed["turns", "checkpoint_1"].value == 1


def test_rescore_writes_no_jev_audit_and_nothing_is_provisional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No key: nothing to answer, so nothing awaits.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    trial = write_traced_trial(
        tmp_path / "jobs" / "trial", [TracedStep("checkpoint_1", trace=_ambiguous())]
    )
    store = tmp_path / "store"
    args = ["assess", "--rescore", str(trial), "--scorer", REF, "--store", str(store)]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    (entry,) = json.loads(result.output)["assessments"]
    stored = AssessmentStore(store).get(entry["key"])
    assert stored is not None
    assert stored.provisional is False
    assert not (store / "jev-audit").exists()
    item = _by_key(stored.result)["plan_written", "checkpoint_1"]
    assert (item.status, item.value) == (UNDETERMINED, None)

    replay = CliRunner().invoke(main, [*args, "--replay"])
    assert replay.exit_code == 0, replay.output
    assert json.loads(replay.output)["counts"]["replayed_identical"] == 1


@pytest.mark.parametrize("filename", ["source.py", "source.data"])
def test_special_files_invalidate_inventory_without_opening_them(
    tmp_path: Path, filename: str
) -> None:
    os.mkfifo(tmp_path / filename)
    # A subprocess gives a broken implementation a hard bound rather than hanging pytest.
    code = (
        "from pathlib import Path; "
        "from raidar.scoring.framework.inventory import build_inventory; "
        f"inventory = build_inventory(Path({str(tmp_path)!r})); "
        "assert not inventory.eligible; "
        "assert len(inventory.invalid) == 1; "
        "assert 'not a regular file' in inventory.invalid[0].detail"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=5)


def test_the_stored_trial_loader_blinds_every_step_of_agent_results(tmp_path: Path) -> None:
    ctx = load_trial_context(two_step_trial(tmp_path / "trial"))
    for step in ctx.steps:
        assert step.agent_result is not None
        assert "arm-model-label" not in json.dumps(step.agent_result)


def test_implementation_digest_covers_raidar_modules_the_scorer_imports() -> None:
    covered = ScorerRegistration(fake_manifest(), "tests.scorer_support:fake_scorer_v1")
    sdk_result = Path(result_module.__file__)
    original = sdk_result.read_bytes()
    baseline = covered.implementation_digest()
    try:
        sdk_result.write_bytes(original + b"\n_TOUCHED = 1\n")
        assert covered.implementation_digest() != baseline
    finally:
        sdk_result.write_bytes(original)


def test_a_plain_rescore_uses_the_runs_locked_assessment_config(tmp_path: Path) -> None:
    run = tmp_path / "run"
    trial = two_step_trial(run / "trials" / "trial")
    locked = {"scorers": {"correctness@10": {"progression": "continue"}}}
    config = run / ASSESSMENT_CONFIG_FILE
    config.write_text(yaml.safe_dump(locked))

    def rescore(target: Path, scorer: str, *extra: str) -> dict[str, object]:
        store = tmp_path / f"store-{len(list(tmp_path.glob('store-*')))}"
        args = ["assess", "--rescore", str(target), "--scorer", scorer, "--store", str(store)]
        result = CliRunner().invoke(main, [*args, *extra])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    explicit = rescore(trial, "correctness@10", "--config", str(config))
    for target in (trial, trial.parent):  # a trial dir, and its job dir
        default = rescore(target, "correctness@10")
        assert default["config"] == str(config.resolve())
        assert default["assessments"][0]["key"] == explicit["assessments"][0]["key"]  # type: ignore[index]

    # Scorers not being run are left out of the run's config, not refused.
    other = rescore(trial, "resource-usage@8")
    assert other["counts"]["added"] == 1  # type: ignore[index]

    # A trial outside any run falls back to an empty config.
    loose = two_step_trial(tmp_path / "loose" / "trial")
    assert rescore(loose, "correctness@10")["config"] is None


def test_rescore_records_run_qualified_trial_ids_so_shared_trial_ids_stay_apart(
    tmp_path: Path,
) -> None:
    """Gate packet F10: two runs of one experiment share bare trial ids."""
    store = AssessmentStore(tmp_path / "store")
    registry = default_scorer_registry()
    steps = [TracedStep("checkpoint_1", trace=_ambiguous())]
    for job_id in ("run-a", "run-b"):
        run = tmp_path / "jobs" / "pilot" / job_id
        write_traced_trial(run / "trials" / "b01-p0-guided", steps)
        (run / "job.json").write_text(json.dumps({"experiment": "pilot", "job_id": job_id}))
        (record,) = rescore_path(run / "trials", [REF], store, registry)
        assert record.trial == f"pilot.{job_id}.b01-p0-guided"
    # A trial outside any run keeps its directory name.
    loose = write_traced_trial(tmp_path / "loose" / "b01-p0-guided", steps)
    (record,) = rescore_path(loose, [REF], AssessmentStore(tmp_path / "s2"), registry)
    assert record.trial == "b01-p0-guided"


def test_a_stored_trial_with_an_unreadable_job_manifest_is_a_rescore_error(
    tmp_path: Path,
) -> None:
    with TemporaryDirectory(dir=tmp_path) as temporary:
        root = Path(temporary)
        trial = root / "job" / "trials" / "t1"
        trial.mkdir(parents=True)
        (root / "job" / "job.json").write_text("{not json")
        ctx = two_step_context(root / "context")
        with pytest.raises(RescoreError, match="cannot read .*job.json"):
            list(_loaded_trials([trial], lambda _: ctx))
