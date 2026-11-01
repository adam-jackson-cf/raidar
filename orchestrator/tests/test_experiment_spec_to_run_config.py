"""How an experiment file becomes run configuration: lock paths and assessment settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from raidar.design.experiment.lock import default_lock_path
from raidar.design.experiment.schema import AssessmentSpec, ExperimentSpec, ProtocolSpec
from raidar.design.experiment.validation import semantic_issues
from raidar.engine.job.assessment import rescore_config
from tests.experiment_spec_support import experiment_document


@pytest.mark.parametrize(
    ("experiment", "lock"),
    [
        ("lab/experiment.yaml", "lab/experiment.lock.json"),
        ("x/oneshot.experiment.yaml", "x/oneshot.experiment.lock.json"),
        ("a/b.yml", "a/b.lock.json"),
        ("a/plain", "a/plain.lock.json"),
    ],
)
def test_each_experiment_file_defaults_to_the_lock_keyed_by_its_name(
    experiment: str, lock: str
) -> None:
    assert default_lock_path(Path(experiment)) == Path(lock)


def test_experiments_sharing_a_directory_never_share_a_lock() -> None:
    first, second = Path("d/experiment.yaml"), Path("d/oneshot.experiment.yaml")

    assert default_lock_path(first) != default_lock_path(second)


def test_assessment_settings_become_the_rescore_config() -> None:
    assessment = AssessmentSpec.model_validate(
        {
            "scorers": ["correctness@10", "code-quality@9"],
            "inventory_policy": {"generated_globs": ["gen/**"], "test_globs": ["tests/**"]},
            "scorer_config": {"code-quality@9": {"sensitivity": True}},
        }
    )
    config = rescore_config(assessment, ProtocolSpec())
    assert config.inventory_policy.generated_globs == ("gen/**",)
    assert config.inventory_policy.test_globs == ("tests/**",)
    assert config.scorers == {
        "correctness@10": {"progression": "continue"},
        "code-quality@9": {"sensitivity": True},
    }
    only_quality = AssessmentSpec(scorers=("code-quality@9",))
    assert rescore_config(only_quality, ProtocolSpec()).scorers == {}


def test_protocol_progression_reaches_the_scorers_that_read_it() -> None:
    halt = ProtocolSpec(progression="halt_on_failure", min_reward=1.0)
    assessment = AssessmentSpec.model_validate(
        {
            "scorers": ["correctness@10", "resource-usage@8"],
            "scorer_config": {"correctness@10": {"progression": "halt_on_failure"}},
        }
    )
    assert rescore_config(assessment, halt).scorers == {
        "correctness@10": {"progression": "halt_on_failure"}
    }


def _document(*scorers: str, **scorer_config: dict[str, Any]) -> dict[str, Any]:
    document = experiment_document()
    document["assessment"] = {"scorers": list(scorers), "scorer_config": scorer_config}
    return document


def test_a_scorer_config_contradicting_the_protocol_is_a_field_error() -> None:
    raw = _document("correctness@10", **{"correctness@10": {"progression": "halt_on_failure"}})
    issues = semantic_issues(ExperimentSpec.model_validate(raw))
    assert [issue.loc for issue in issues] == [
        "assessment.scorer_config.correctness@10.progression"
    ]
    raw["protocol"] = {"progression": "halt_on_failure", "min_reward": 1.0}
    assert semantic_issues(ExperimentSpec.model_validate(raw)) == []


def test_only_scorers_that_take_a_protocol_key_are_held_to_it() -> None:
    # resource-usage@8 does not take progression from the protocol: its own key stands.
    raw = _document(
        "correctness@10",
        "resource-usage@8",
        **{"resource-usage@8": {"progression": "halt_on_failure"}},
    )
    spec = ExperimentSpec.model_validate(raw)
    assert semantic_issues(spec) == []
    # The registry's protocol keys decide, whatever their name.
    issues = semantic_issues(spec, protocol_config={"resource-usage@8": ("progression",)})
    assert [issue.loc for issue in issues] == [
        "assessment.scorer_config.resource-usage@8.progression"
    ]


def test_scorer_config_must_name_a_declared_scorer() -> None:
    with pytest.raises(ValidationError, match="not in 'scorers'"):
        AssessmentSpec.model_validate(
            {"scorers": ["correctness@10"], "scorer_config": {"code-quality@9": {}}}
        )
