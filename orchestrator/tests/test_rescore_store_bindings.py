"""`raidar assess --rescore` binds store-aware scorers, raw payloads and
problem-derived config (plan F2, G2, G5, H2)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from raidar.cli import main
from raidar.design.experiment.schema import ProtocolSpec
from raidar.engine.job.assessment import ASSESSMENT_CONFIG_FILE
from raidar.grading.models import CaseStatus
from raidar.scoring.catalog.builtin import default_scorer_registry
from raidar.scoring.framework.context import AssessContext, statused_result
from raidar.scoring.framework.registry import ScorerRegistration, ScorerRegistry
from raidar.scoring.framework.rescore import RescoreOutcome, rescore_path
from raidar.scoring.framework.result import AssessResult, AssessStatus
from raidar.scoring.framework.store import AssessmentConflictError, AssessmentStore
from tests.scorer_support import (
    APP_V1,
    FakeScorer,
    StepFixture,
    fake_manifest,
    two_step_trial,
    write_trial,
)
from tests.trace_trial_support import (
    TracedStep,
    agent_step,
    codex_exec,
    codex_patch,
    trajectory,
    write_traced_trial,
)

DETERMINISTIC = "process-compliance@6"
FAKE = "fake-quality@1"


def _ambiguous_trial(trial_dir: Path) -> Path:
    """One step whose shell edit after the test run leaves both checks undetermined."""
    trace = trajectory(
        agent_step(1, message="Plan:\n1. edit main\n2. run tests"),
        agent_step(2, codex_patch("e1", "/app/app/main.py")),
        agent_step(3, codex_exec("t1", "pytest -q")),
        agent_step(4, codex_exec("s1", "sed -i 's/a/b/' app/main.py")),
    )
    return write_traced_trial(trial_dir, [TracedStep("checkpoint_1", trace=trace)])


class Awaiting(FakeScorer):
    """A fake scorer whose external decision is answered only once ``answered`` is set."""

    answered = False
    runs = 0

    def awaiting_answers(self) -> bool:
        return not type(self).answered

    def assess(self, ctx: AssessContext) -> AssessResult:
        type(self).runs += 1
        reason = "answered" if type(self).answered else "awaiting"
        return statused_result(self.manifest, ctx, AssessStatus.UNAVAILABLE, reason)


def awaiting_scorer() -> Awaiting:
    return Awaiting(fake_manifest("1"))


def test_a_provisional_assessment_is_rerun_and_superseded_once_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Awaiting, "answered", False)
    monkeypatch.setattr(Awaiting, "runs", 0)
    trial = two_step_trial(tmp_path / "jobs" / "trial")
    store = AssessmentStore(tmp_path / "store")
    registry = _registry(implementation="tests.test_rescore_store_bindings:awaiting_scorer")

    (first,) = rescore_path(trial, [FAKE], store, registry)
    provisional = store.path_for(first.key).read_bytes()
    assert first.outcome is RescoreOutcome.ADDED
    assert b'"provisional": true' in provisional

    # Still unanswered: rerun (never reused), same bytes, nothing replaced.
    (again,) = rescore_path(trial, [FAKE], store, registry)
    assert (again.outcome, Awaiting.runs) == (RescoreOutcome.EXISTING, 2)
    assert store.path_for(first.key).read_bytes() == provisional

    monkeypatch.setattr(Awaiting, "answered", True)
    (answered,) = rescore_path(trial, [FAKE], store, registry)
    assert answered.outcome is RescoreOutcome.SUPERSEDED
    assert answered.key == first.key, "analysis finds the complete Assessment under the same key"
    complete = store.get(first.key)
    assert complete is not None
    assert complete.provisional is False
    assert complete.result.reason == "answered"
    assert store.superseded_path(first.key, provisional).read_bytes() == provisional

    # A complete Assessment is reused without running, and never replaced.
    (reused,) = rescore_path(trial, [FAKE], store, registry)
    assert (reused.outcome, Awaiting.runs) == (RescoreOutcome.EXISTING, 3)
    monkeypatch.setattr(Awaiting, "answered", False)
    with pytest.raises(AssessmentConflictError, match="different bytes"):
        rescore_path(trial, [FAKE], store, registry, replay=True)
    assert store.get(first.key) == complete


class RawScorer(FakeScorer):
    """A fake scorer that also returns raw tool output."""

    raw: Mapping[str, bytes] = {"ruff.json": b'{"findings": []}\n'}

    def assess_with_raw(self, ctx: AssessContext) -> tuple[AssessResult, Mapping[str, bytes]]:
        return self.assess(ctx), dict(self.raw)


def raw_scorer() -> RawScorer:
    return RawScorer(fake_manifest("1"))


def _registry(**registration: object) -> ScorerRegistry:
    return ScorerRegistry([ScorerRegistration(fake_manifest("1"), **registration)])  # type: ignore[arg-type]


def test_raw_payloads_are_stored_content_addressed_and_proven_on_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trial = two_step_trial(tmp_path / "jobs" / "trial")
    store = AssessmentStore(tmp_path / "store")
    registry = _registry(implementation="tests.test_rescore_store_bindings:raw_scorer")

    (added,) = rescore_path(trial, [FAKE], store, registry)
    assessment = store.get(added.key)
    assert assessment is not None
    digest = assessment.raw_payloads["ruff.json"]
    assert store.get_raw(digest) == RawScorer.raw["ruff.json"]

    (replayed,) = rescore_path(trial, [FAKE], store, registry, replay=True)
    assert replayed.outcome is RescoreOutcome.REPLAYED_IDENTICAL

    monkeypatch.setattr(RawScorer, "raw", {"ruff.json": b'{"findings": [1]}\n'})
    with pytest.raises(AssessmentConflictError, match="different bytes"):
        rescore_path(trial, [FAKE], store, registry, replay=True)
    assert store.get_raw(digest) == b'{"findings": []}\n', "stored raw bytes are never replaced"

    plain = AssessmentStore(tmp_path / "plain")
    (without,) = rescore_path(trial, [FAKE], plain, _registry(implementation=_FAKE_V1))
    assert b"raw_payloads" not in plain.path_for(without.key).read_bytes()


_FAKE_V1 = "tests.scorer_support:fake_scorer_v1"


class ConfigEcho(FakeScorer):
    """Reports the config it was run with as every observation's reason."""

    def assess(self, ctx: AssessContext) -> AssessResult:
        reason = json.dumps(dict(ctx.config), sort_keys=True)
        return statused_result(self.manifest, ctx, AssessStatus.UNAVAILABLE, reason)


def config_echo() -> ConfigEcho:
    return ConfigEcho(fake_manifest("1"))


def _seen_config(store: AssessmentStore, key: str) -> dict[str, object]:
    assessment = store.get(key)
    assert assessment is not None
    return json.loads(assessment.result.observations[0].reason or "{}")


def test_problem_facts_default_the_scorer_config_and_explicit_config_wins(
    tmp_path: Path,
) -> None:
    steps = [StepFixture("checkpoint_1", APP_V1, (CaseStatus.PASSED,))]
    trial = write_trial(tmp_path / "jobs" / "trial", steps, entry_file="app/main.py")
    registry = _registry(
        implementation="tests.test_rescore_store_bindings:config_echo",
        problem_config=("entry_file",),
    )
    store = AssessmentStore(tmp_path / "store")

    (default,) = rescore_path(trial, [FAKE], store, registry)
    assert _seen_config(store, default.key) == {"entry_file": "app/main.py"}
    stored = store.get(default.key)
    assert stored is not None
    assert stored.config == {}, "the key hashes explicit config; the default is an input"

    explicit = {FAKE: {"entry_file": "other.py", "strict": True}}
    (overridden,) = rescore_path(trial, [FAKE], store, registry, config=explicit)
    assert _seen_config(store, overridden.key) == {"entry_file": "other.py", "strict": True}
    assert overridden.key != default.key

    # A scorer that does not declare the key never sees the problem's value.
    undeclared = _registry(implementation="tests.test_rescore_store_bindings:config_echo")
    (plain,) = rescore_path(trial, [FAKE], AssessmentStore(tmp_path / "s2"), undeclared)
    assert _seen_config(AssessmentStore(tmp_path / "s2"), plain.key) == {}

    # Problems without an entrypoint pass nothing, so the scorer's own default applies.
    bare = write_trial(tmp_path / "jobs" / "bare", steps)
    (none,) = rescore_path(bare, [FAKE], AssessmentStore(tmp_path / "s3"), registry)
    assert _seen_config(AssessmentStore(tmp_path / "s3"), none.key) == {}


def test_builtin_registrations_declare_their_store_protocol_and_problem_bindings(
    tmp_path: Path,
) -> None:
    registry = default_scorer_registry()
    bound = registry.get(DETERMINISTIC).load_for_store(tmp_path / "store")
    assert bound.manifest == registry.get(DETERMINISTIC).manifest
    assert registry.get("code-quality@3").problem_config == ("entry_file",)
    assert registry.get("correctness@1").protocol_config == ("progression",)
    declared = {name for ref in registry.refs() for name in registry.get(ref).protocol_config}
    assert declared <= set(ProtocolSpec.model_fields), "protocol keys name ProtocolSpec fields"
    with pytest.raises(ValueError, match="needs an implementation"):
        ScorerRegistration(fake_manifest("1"), store_implementation="a:b")


def test_a_plain_rescore_uses_the_runs_locked_assessment_config(tmp_path: Path) -> None:
    run = tmp_path / "run"
    trial = two_step_trial(run / "trials" / "trial")
    locked = {"scorers": {"correctness@1": {"progression": "continue"}}}
    config = run / ASSESSMENT_CONFIG_FILE
    config.write_text(yaml.safe_dump(locked))

    def rescore(target: Path, scorer: str, *extra: str) -> dict[str, object]:
        store = tmp_path / f"store-{len(list(tmp_path.glob('store-*')))}"
        args = ["assess", "--rescore", str(target), "--scorer", scorer, "--store", str(store)]
        result = CliRunner().invoke(main, [*args, *extra])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    explicit = rescore(trial, "correctness@1", "--config", str(config))
    for target in (trial, trial.parent):  # a trial dir, and its job dir
        default = rescore(target, "correctness@1")
        assert default["config"] == str(config.resolve())
        assert default["assessments"][0]["key"] == explicit["assessments"][0]["key"]  # type: ignore[index]

    # Scorers not being run are left out of the run's config, not refused.
    other = rescore(trial, "resource-usage@1")
    assert other["counts"]["added"] == 1  # type: ignore[index]

    # A trial outside any run falls back to an empty config.
    loose = two_step_trial(tmp_path / "loose" / "trial")
    assert rescore(loose, "correctness@1")["config"] is None


def test_rescore_records_run_qualified_trial_ids_so_shared_trial_ids_stay_apart(
    tmp_path: Path,
) -> None:
    """Gate packet F10: two runs of one experiment share bare trial ids."""
    store = AssessmentStore(tmp_path / "store")
    registry = default_scorer_registry()
    for job_id in ("run-a", "run-b"):
        run = tmp_path / "jobs" / "pilot" / job_id
        _ambiguous_trial(run / "trials" / "b01-p0-guided")
        (run / "job.json").write_text(json.dumps({"experiment": "pilot", "job_id": job_id}))
        (record,) = rescore_path(run / "trials", [DETERMINISTIC], store, registry)
        assert record.trial == f"pilot.{job_id}.b01-p0-guided"
    # A trial outside any run keeps its directory name.
    loose = _ambiguous_trial(tmp_path / "loose" / "b01-p0-guided")
    (record,) = rescore_path(loose, [DETERMINISTIC], AssessmentStore(tmp_path / "s2"), registry)
    assert record.trial == "b01-p0-guided"
