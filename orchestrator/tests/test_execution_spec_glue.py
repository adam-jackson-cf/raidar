"""Spec glue for execution: per-experiment lock paths, assessment settings feeding scorers."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from raidar.cli import main
from raidar.execution.assessment import rescore_config
from raidar.problems.store import ROOT_ENV
from raidar.spec.lock import default_lock_path
from raidar.spec.schema import AssessmentSpec
from tests.experiment_spec_support import PILOT_EXPERIMENT, PILOT_ONESHOT


def test_each_experiment_file_defaults_to_its_own_lock() -> None:
    assert default_lock_path(PILOT_EXPERIMENT).name == "experiment.lock.json"
    assert default_lock_path(PILOT_ONESHOT).name == "oneshot.experiment.lock.json"
    assert default_lock_path(Path("a/b.yml")) == Path("a/b.lock.json")
    assert len({default_lock_path(PILOT_EXPERIMENT), default_lock_path(PILOT_ONESHOT)}) == 2


def test_lock_command_writes_the_keyed_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(PILOT_EXPERIMENT.parents[2] / "problems"))
    experiment = tmp_path / "oneshot.experiment.yaml"
    experiment.write_text(PILOT_ONESHOT.read_text())
    (tmp_path / "components" / "guided").mkdir(parents=True)
    (tmp_path / "components" / "guided" / "AGENTS.md").write_text("rules\n")
    result = CliRunner().invoke(main, ["lock", str(experiment)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "oneshot.experiment.lock.json").is_file()
    assert not (tmp_path / "experiment.lock.json").exists()


def test_assessment_settings_become_the_rescore_config() -> None:
    assessment = AssessmentSpec.model_validate(
        {
            "scorers": ["correctness@1", "code-quality@1"],
            "inventory_policy": {"generated_globs": ["gen/**"], "test_globs": ["tests/**"]},
            "scorer_config": {"code-quality@1": {"sensitivity": True}},
        }
    )
    config = rescore_config(assessment)
    assert config.inventory_policy.generated_globs == ("gen/**",)
    assert config.inventory_policy.test_globs == ("tests/**",)
    assert config.scorers == {"code-quality@1": {"sensitivity": True}}
    assert rescore_config(AssessmentSpec(scorers=("correctness@1",))).scorers == {}


def test_scorer_config_must_name_a_declared_scorer() -> None:
    with pytest.raises(ValidationError, match="not in 'scorers'"):
        AssessmentSpec.model_validate(
            {"scorers": ["correctness@1"], "scorer_config": {"code-quality@1": {}}}
        )
