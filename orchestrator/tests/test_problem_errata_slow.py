"""Slow-tier behaviours split from :mod:`tests.test_problem_errata`."""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.design.experiment.lock import LockError
from raidar.design.experiment.validation import semantic_issues
from raidar.engine.job.assessment import lock_errata, lock_rescore_config
from raidar.engine.trial.config import harbor_trial_config
from raidar.engine.trial.plan import PlanError, PlanOptions, build_plan
from raidar.grading.errata import ErrataOutcome, ErrataRef
from raidar.grading.records import load_step_record, step_record_dir
from raidar.subject.environment.python.pack_definition import load_pack_spec
from tests.execution_support import ImportedFixture, locked_experiment
from tests.test_problem_errata import (
    EXCLUDED as EXCLUDED,
)
from tests.test_problem_errata import (
    fixture_fixture as fixture_fixture,
)
from tests.test_problem_errata import (
    write_errata as write_errata,
)
from tests.test_verifier_and_regrade import make_verifier, place_step_artifact
from tests.verifier_support import FakeAgentEnvironment

pytestmark = pytest.mark.slow


def test_the_lock_pins_the_errata_in_the_execution_identity(
    fixture: ImportedFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The trial config attaches host codex auth; this test is about errata, not the host.
    monkeypatch.setattr("raidar.engine.trial.config.with_host_auth", lambda config, harness: config)
    _, plain, _ = locked_experiment(
        tmp_path / "plain", fixture.ref, environment={"compose_init": False}
    )
    write_errata(fixture.store, fixture.ref)
    loaded, pinned, _ = locked_experiment(
        tmp_path / "pinned", fixture.ref, environment={"compose_init": False}
    )
    metadata = pinned["execution"]["references"]["problem"][fixture.ref]["metadata"]
    assert metadata["errata"]["exclude_from_solved"] == [EXCLUDED]
    assert pinned["lock_sha256"] != plain["lock_sha256"]
    assert lock_errata(pinned) == {
        fixture.ref: ErrataRef(sha256=metadata["errata"]["sha256"], exclude_from_solved=(EXCLUDED,))
    }
    assert lock_errata(plain) == {}

    plan = build_plan(
        loaded, pinned, store=fixture.store, pack=load_pack_spec(), options=PlanOptions()
    )
    assert plan.problems[0].errata == lock_errata(pinned)[fixture.ref].model_dump(mode="json")
    trial = plan.trials[0]
    config = harbor_trial_config(
        plan,
        trial,
        tmp_path / "task",
        trials_dir=tmp_path / "trials",
        bundle_root=tmp_path / "bundle",
        docker_context="orbstack",
    )
    assert config.verifier.kwargs["pinned"]["errata"] == {
        "sha256": metadata["errata"]["sha256"],
        "exclude_from_solved": [EXCLUDED],
    }

    # Changing the committed errata after locking refuses the plan: relock first.
    write_errata(fixture.store, fixture.ref, registered="2026-10-03")
    with pytest.raises(PlanError, match="errata changed since the lock"):
        build_plan(
            loaded, pinned, store=fixture.store, pack=load_pack_spec(), options=PlanOptions()
        )
    write_errata(fixture.store, fixture.ref, manifest_sha256="0" * 64)
    with pytest.raises(LockError, match="for manifest"):
        locked_experiment(tmp_path / "stale", fixture.ref, environment={"compose_init": False})


def test_errata_reach_correctness_config_from_the_lock_only(
    fixture: ImportedFixture, tmp_path: Path
) -> None:
    write_errata(fixture.store, fixture.ref)
    loaded, lock, _ = locked_experiment(
        tmp_path / "exp", fixture.ref, environment={"compose_init": False}
    )
    config = lock_rescore_config(lock).scorers["correctness@10"]
    assert config["errata"] == {
        fixture.ref: {
            "sha256": lock_errata(lock)[fixture.ref].sha256,
            "exclude_from_solved": [EXCLUDED],
        }
    }
    assert config["progression"] == "continue"
    explicit = loaded.spec.model_copy(
        update={
            "assessment": loaded.spec.assessment.model_copy(
                update={"scorer_config": {"correctness@10": {"errata": {}}}}
            )
        }
    )
    issues = [issue.render() for issue in semantic_issues(explicit)]
    assert any(
        issue.startswith("assessment.scorer_config.correctness@10.errata:") for issue in issues
    )


async def test_the_verifier_records_raw_and_errata_applied_verdicts(tmp_path: Path) -> None:
    place_step_artifact(tmp_path, "checkpoint_2", mul=False)
    errata = {"sha256": "e" * 64, "exclude_from_solved": [EXCLUDED]}
    verifier = await make_verifier(
        tmp_path,
        FakeAgentEnvironment(),
        step="checkpoint_2",
        pinned={"errata": errata},
    )
    result = await verifier.verify()
    record = load_step_record(step_record_dir(tmp_path / "trial", "checkpoint_2"))
    # The reward and the grade stay the raw SCB verdict ...
    assert result.rewards is not None
    assert result.rewards["solved"] == 0.0
    assert not record.grade.solved
    # ... and the errata-applied one is recorded beside it.
    assert record.errata == ErrataOutcome(
        sha256="e" * 64,
        excluded_cases=(EXCLUDED,),
        excluded_failing=(EXCLUDED,),
        solved=True,
    )
