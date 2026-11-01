"""experiment.yaml schema, CLI override precedence and release/kind validation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.design.experiment.agent import AgentResolutionError, entry_issues, resolve_agent
from raidar.design.experiment.arm import ArmResolutionError, resolve_arm
from raidar.design.experiment.loader import UNSET, CliOverrides, load_experiment, parse_assignment
from raidar.design.experiment.schema import AgentEntry, ExperimentSpec
from raidar.design.experiment.validation import SpecValidationError, kind_issues
from raidar.subject.releases import released_agent
from tests.experiment_spec_support import experiment_document, write_experiment


def _issues(path: Path, overrides: CliOverrides | None = None) -> dict[str, str]:
    with pytest.raises(SpecValidationError) as caught:
        load_experiment(path, overrides)
    return {issue.loc: issue.message for issue in caught.value.issues}


def test_invalid_file_reports_every_error_at_its_field(tmp_path: Path) -> None:
    document = experiment_document(unknown_key=1)
    document["agents"][0]["harness"] = "not-a-harness"
    document["agents"][0]["release"] = "codex"
    document["analysis"]["attempts"] = 0
    document["problems"] = [{"ref": "forge"}, {"path": "."}]
    document["protocol"] = {"progression": "halt_on_failure"}

    issues = _issues(write_experiment(tmp_path, document))

    assert set(issues) >= {
        "unknown_key",
        "agents.0.harness",
        "agents.0.release",
        "analysis.attempts",
        "problems.0.ref",
        "problems.1.path",
        "protocol",
    }
    assert "halt_on_failure" in issues["protocol"]


def test_cross_reference_errors_are_located_on_the_referring_field(tmp_path: Path) -> None:
    document = experiment_document()
    document["arms"][1]["agent"] = "ghost"
    document["arms"].append({"id": "guided", "agent": "luna"})
    document["analysis"]["baseline_arm"] = "control"
    document["assessment"]["metric"] = "scb-canonical@9"

    issues = _issues(write_experiment(tmp_path, document))

    assert set(issues) == {
        "arms.1.agent",
        "arms.2.id",
        "analysis.baseline_arm",
        "assessment.metric",
    }


def test_duplicate_yaml_keys_are_rejected(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())
    path.write_text(path.read_text() + "id: second\n", encoding="utf-8")

    issues = _issues(path)

    assert "duplicate key 'id'" in issues[""]


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        ("loop: &loop [*loop]\n", "cyclic YAML aliases"),
        ("loop: &loop {child: *loop}\n", "recursive node"),
        ("loop: &loop [{child: *loop}]\n", "cyclic YAML aliases"),
        ("? [a, b]\n: value\n", "unhashable mapping key"),
        ("? {a: b}\n: value\n", "unhashable mapping key"),
    ],
)
def test_invalid_yaml_graphs_have_readable_validation_errors(
    tmp_path: Path, yaml_text: str, message: str
) -> None:
    path = tmp_path / "experiment.yaml"
    path.write_text(yaml_text)

    assert message in _issues(path)[""]
    result = CliRunner().invoke(main, ["validate", str(path)])
    assert result.exit_code != 0
    assert message in result.output
    assert not isinstance(result.exception, (RecursionError, TypeError))


def test_cli_overrides_follow_scb_precedence(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())

    from_file = load_experiment(path).spec.analysis
    from_flag = load_experiment(path, CliOverrides(flags={"attempts": 3, "seed": None})).spec
    from_set = load_experiment(
        path,
        CliOverrides(
            flags={"attempts": 3}, assignments=("analysis.attempts=9", "arms.1.id=treated")
        ),
    ).spec

    assert (from_file.attempts, from_file.seed, from_file.concurrency) == (5, 7, 1)
    assert from_flag.analysis.attempts == 3
    assert from_flag.analysis.seed == 7  # unset flag leaves the file value
    assert from_set.analysis.attempts == 9  # key=value beats the flag
    assert from_set.arms[1].id == "treated"
    assert from_set.protocol.progression == "continue"  # built-in default


def test_invalid_overrides_are_rejected_with_their_location(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())

    issues = _issues(
        path,
        CliOverrides(
            flags={"retries": 2},
            assignments=("attempts", "arms.7.id=x", "analysis.attempts.deep=1"),
        ),
    )

    assert set(issues) == {
        "--retries",
        "--set attempts",
        "--set arms.7.id=x",
        "--set analysis.attempts.deep=1",
    }


def test_override_values_are_typed_like_scb() -> None:
    assert parse_assignment("a.b=3") == ("a.b", 3)
    assert parse_assignment("a=0.5") == ("a", 0.5)
    assert parse_assignment("a=true") == ("a", True)
    assert parse_assignment("a=gpt-6-luna") == ("a", "gpt-6-luna")
    assert parse_assignment("a=2026-09-29") == ("a", "2026-09-29")
    # An empty value unsets the key; quoted empty is the empty string (Raidar addition).
    assert parse_assignment("a=") == ("a", UNSET)
    assert parse_assignment("a=''") == ("a", "")
    assert parse_assignment("a=1e3") == ("a", 1000.0)
    assert parse_assignment("a=FALSE") == ("a", False)
    # SCB infers only bool/int/float; YAML-only spellings stay strings.
    for text in ("yes", "off", "null", "~", "0x1f", "[1, 2]"):
        assert parse_assignment(f"a={text}") == ("a", text)
    # Quoting keeps a numeric-looking value a string (Raidar addition).
    assert parse_assignment("a='1.10'") == ("a", "1.10")


def test_validate_command_exits_nonzero_with_field_errors(tmp_path: Path) -> None:
    document = experiment_document()
    document["analysis"]["concurrency"] = -1

    result = CliRunner().invoke(main, ["validate", str(write_experiment(tmp_path, document))])

    assert result.exit_code == 1
    assert "analysis.concurrency" in result.output


@pytest.mark.parametrize(
    ("change", "loc"),
    [
        ({"problems": [{"ref": "scb/forge@abc123", "path": "tasks/forge"}]}, "problems.0"),
        ({"problems": [{"ref": "scb/forge@abc123"}] * 2}, "problems.1"),
        ({"protocol": {"min_reward": 0.5}}, "protocol"),
        ({"assessment": {"scorers": ["correctness@10", "correctness@10"]}}, "assessment.scorers.1"),
    ],
    ids=["problem-two-sources", "duplicate-problem", "min-reward-in-continue", "dup-scorer"],
)
def test_structural_rules_are_field_located(tmp_path: Path, change: dict, loc: str) -> None:
    issues = _issues(write_experiment(tmp_path, experiment_document(**change)))

    assert loc in issues


def test_non_mapping_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "experiment.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")

    assert "must be a YAML mapping" in _issues(path)[""]


def test_set_override_can_replace_a_list_item(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())

    spec = load_experiment(
        path, CliOverrides(assignments=("assessment.scorers.0=code-quality@9",))
    ).spec

    assert spec.assessment.scorers == ("code-quality@9",)


def test_verdict_thresholds_are_pinned_in_the_analysis_block(tmp_path: Path) -> None:
    document = experiment_document()
    document["analysis"].update(
        min_pairs=1, alpha=1.5, max_exclusion_fraction=-0.1, min_effects={"nope": 0.1}
    )
    issues = _issues(write_experiment(tmp_path, document))
    assert {"analysis.min_pairs", "analysis.alpha", "analysis.max_exclusion_fraction"} <= set(
        issues
    )

    document["analysis"].update(min_pairs=6, alpha=0.1, max_exclusion_fraction=0.25)
    issues = _issues(write_experiment(tmp_path, document))
    assert set(issues) == {"analysis.min_effects.nope"}
    assert "unknown outcome metric 'nope'" in issues["analysis.min_effects.nope"]

    document["analysis"]["min_effects"] = {"solve_rate": 0.2}
    analysis = load_experiment(write_experiment(tmp_path, document)).spec.analysis
    assert (analysis.min_pairs, analysis.alpha, dict(analysis.min_effects)) == (
        6,
        0.1,
        {"solve_rate": 0.2},
    )
    # The pre-registered material exclusion-rate difference has no default.
    assert analysis.max_exclusion_difference is None
    document["analysis"]["max_exclusion_difference"] = 1.5
    assert "analysis.max_exclusion_difference" in _issues(write_experiment(tmp_path, document))
    document["analysis"]["max_exclusion_difference"] = 0.15
    pinned = load_experiment(write_experiment(tmp_path, document)).spec.analysis
    assert pinned.max_exclusion_difference == 0.15


def test_set_overrides_change_only_the_addressed_yaml_alias(tmp_path: Path) -> None:
    from raidar.design.experiment.loader import _read_document, apply_assignment

    path = tmp_path / "aliased.yaml"
    path.write_text("arms:\n  - {id: a, with: &w {rounds: 2}}\n  - {id: b, with: *w}\n")
    document, _ = _read_document(path)

    apply_assignment(document, "arms.1.with.rounds", 3)

    assert [arm["with"]["rounds"] for arm in document["arms"]] == [2, 3]


@pytest.mark.parametrize(
    ("agent_change", "field"),
    [
        ({"model": "claude-opus-4-7"}, "agents.0.model"),
        ({"reasoning": "xhigh"}, "agents.0.reasoning"),
        ({"reasoning": None}, "agents.0.reasoning"),
        ({"release": "codex@999"}, "agents.0.release"),
        ({"release": "ghost@1"}, "agents.0.release"),
    ],
    ids=["model", "reasoning", "reasoning-required", "unregistered-version", "unknown-agent"],
)
def test_agent_entries_are_checked_against_their_release(
    tmp_path: Path, agent_change: dict, field: str
) -> None:
    document = experiment_document()
    document["agents"][0].update(agent_change)

    assert set(_issues(write_experiment(tmp_path, document))) == {field}


def test_an_arm_workflow_must_be_a_released_workflow(tmp_path: Path) -> None:
    document = experiment_document()
    document["arms"][1]["workflow"] = "acme/ghost@1"

    assert set(_issues(write_experiment(tmp_path, document))) == {"arms.1.workflow"}


def test_a_single_arm_smoke_is_accepted(tmp_path: Path) -> None:
    document = experiment_document(kind="smoke", arms=[experiment_document()["arms"][0]])

    assert load_experiment(write_experiment(tmp_path, document)).spec.kind == "smoke"


def test_a_benchmark_may_not_compare_workflows() -> None:
    document = experiment_document(kind="benchmark")
    document["arms"][1]["workflow"] = "acme/other@1"

    assert [issue.loc for issue in kind_issues(ExperimentSpec.model_validate(document))] == ["arms"]
    document["arms"][1]["workflow"] = "raidar/one-shot@2"
    assert kind_issues(ExperimentSpec.model_validate(document)) == []


@pytest.mark.parametrize(
    ("flags", "named"),
    [
        (["--ignore-host-python"], "--ignore-host-python"),
        (["--draft", "--check"], "--check"),
        (["--draft", "--output", "lock.json"], "--output"),
    ],
)
def test_lock_options_that_contradict_each_other_are_usage_errors(
    tmp_path: Path, flags: list[str], named: str
) -> None:
    path = write_experiment(tmp_path, experiment_document())

    result = CliRunner().invoke(main, ["lock", str(path), *flags])

    assert result.exit_code == 2
    assert named in result.output
    assert not list(tmp_path.glob("*.lock.json"))


def _agent_entry(**changes: str | None) -> AgentEntry:
    fields = {"id": "luna", "release": "codex@3", "model": "gpt-6-luna", "reasoning": "low"}
    return AgentEntry.model_validate({**fields, **changes})


def test_a_resolved_agent_runs_with_its_release_pin_and_the_entrys_choices() -> None:
    definition = released_agent("codex@3").definition

    agent = resolve_agent(_agent_entry())

    assert agent.version == definition.tool.version
    assert agent.model == definition.models["gpt-6-luna"].canonical
    assert agent.reasoning == "low"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"release": "ghost@1"}, "agent 'luna'"),
        ({"model": "nope"}, "offers models"),
        ({"reasoning": "xhigh"}, "allows reasoning"),
        ({"reasoning": None}, "needs one of"),
    ],
)
def test_an_agent_that_does_not_fit_its_release_cannot_be_resolved(
    changes: dict[str, str | None], message: str
) -> None:
    with pytest.raises(AgentResolutionError, match=message):
        resolve_agent(_agent_entry(**changes))


def test_a_model_without_reasoning_levels_takes_no_reasoning() -> None:
    released = released_agent("codex@3")
    plain = released.definition.models["gpt-6-luna"].model_copy(update={"reasoning": ()})
    definition = released.definition.model_copy(update={"models": {"gpt-6-luna": plain}})
    fitted = replace(released, definition=definition)

    assert entry_issues(_agent_entry(reasoning=None), fitted) == []
    assert [field for field, _ in entry_issues(_agent_entry(reasoning="low"), fitted)] == [
        "reasoning"
    ]


def test_an_arm_resolves_its_agent_and_workflow_releases() -> None:
    spec = ExperimentSpec.model_validate(experiment_document())

    arm = resolve_arm(spec, "guided")

    assert arm.arm_id == "guided"
    assert arm.agent.entry == spec.agent("luna")


def test_an_arm_whose_agent_or_workflow_is_not_released_cannot_be_resolved() -> None:
    bad_agent = experiment_document()
    bad_agent["agents"][0]["release"] = "ghost@1"
    bad_workflow = experiment_document()
    bad_workflow["arms"][1]["workflow"] = "acme/ghost@1"

    with pytest.raises(ArmResolutionError, match="agent 'luna'"):
        resolve_arm(ExperimentSpec.model_validate(bad_agent), "baseline")
    with pytest.raises(ArmResolutionError, match="arm 'guided'"):
        resolve_arm(ExperimentSpec.model_validate(bad_workflow), "guided")
