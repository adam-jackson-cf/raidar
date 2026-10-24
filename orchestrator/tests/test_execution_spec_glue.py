"""Spec glue for execution: per-experiment lock paths, assessment settings feeding scorers."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from pydantic import ValidationError

from raidar.cli import main
from raidar.design.experiment.lock import default_lock_path
from raidar.design.experiment.schema import AssessmentSpec, ExperimentSpec, ProtocolSpec
from raidar.design.experiment.validation import semantic_issues
from raidar.design.problem.store import ROOT_ENV
from raidar.engine.job.assessment import rescore_config
from tests.experiment_spec_support import MECHANICS_SMOKE, SMOKE_EXPERIMENT


def test_each_experiment_file_defaults_to_its_own_lock() -> None:
    assert default_lock_path(MECHANICS_SMOKE).name == "experiment.lock.json"
    assert default_lock_path(Path("x/oneshot.experiment.yaml")).name == (
        "oneshot.experiment.lock.json"
    )
    assert default_lock_path(Path("a/b.yml")) == Path("a/b.lock.json")
    assert len({default_lock_path(MECHANICS_SMOKE), default_lock_path(SMOKE_EXPERIMENT)}) == 2


def test_lock_command_writes_the_keyed_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(SMOKE_EXPERIMENT.parents[3] / "problems"))
    experiment = tmp_path / "oneshot.experiment.yaml"
    experiment.write_text(SMOKE_EXPERIMENT.read_text())
    result = CliRunner().invoke(main, ["lock", str(experiment)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "oneshot.experiment.lock.json").is_file()
    assert not (tmp_path / "experiment.lock.json").exists()


def test_assessment_settings_become_the_rescore_config() -> None:
    assessment = AssessmentSpec.model_validate(
        {
            "scorers": ["correctness@1", "code-quality@3"],
            "inventory_policy": {"generated_globs": ["gen/**"], "test_globs": ["tests/**"]},
            "scorer_config": {"code-quality@3": {"sensitivity": True}},
        }
    )
    config = rescore_config(assessment, ProtocolSpec())
    assert config.inventory_policy.generated_globs == ("gen/**",)
    assert config.inventory_policy.test_globs == ("tests/**",)
    assert config.scorers == {
        "correctness@1": {"progression": "continue"},
        "code-quality@3": {"sensitivity": True},
    }
    only_quality = AssessmentSpec(scorers=("code-quality@3",))
    assert rescore_config(only_quality, ProtocolSpec()).scorers == {}


def test_protocol_progression_reaches_the_scorers_that_read_it() -> None:
    halt = ProtocolSpec(progression="halt_on_failure", min_reward=1.0)
    assessment = AssessmentSpec.model_validate(
        {
            "scorers": ["correctness@1", "resource-usage@1"],
            "scorer_config": {"correctness@1": {"progression": "halt_on_failure"}},
        }
    )
    assert rescore_config(assessment, halt).scorers == {
        "correctness@1": {"progression": "halt_on_failure"}
    }


def test_a_scorer_config_contradicting_the_protocol_is_a_field_error() -> None:
    raw = yaml.safe_load(MECHANICS_SMOKE.read_text())
    raw["assessment"]["scorer_config"] = {"correctness@1": {"progression": "halt_on_failure"}}
    issues = semantic_issues(ExperimentSpec.model_validate(raw))
    assert [issue.loc for issue in issues] == ["assessment.scorer_config.correctness@1.progression"]
    raw["protocol"] = {"progression": "halt_on_failure", "min_reward": 1.0}
    assert semantic_issues(ExperimentSpec.model_validate(raw)) == []


def test_only_scorers_that_take_a_protocol_key_are_held_to_it() -> None:
    raw = yaml.safe_load(MECHANICS_SMOKE.read_text())
    # resource-usage@1 does not take progression from the protocol: its own key stands.
    raw["assessment"]["scorer_config"] = {"resource-usage@1": {"progression": "halt_on_failure"}}
    spec = ExperimentSpec.model_validate(raw)
    assert semantic_issues(spec) == []
    # The registry's protocol keys decide, whatever their name.
    issues = semantic_issues(spec, protocol_config={"resource-usage@1": ("progression",)})
    assert [issue.loc for issue in issues] == [
        "assessment.scorer_config.resource-usage@1.progression"
    ]


def test_scorer_config_must_name_a_declared_scorer() -> None:
    with pytest.raises(ValidationError, match="not in 'scorers'"):
        AssessmentSpec.model_validate(
            {"scorers": ["correctness@1"], "scorer_config": {"code-quality@3": {}}}
        )
