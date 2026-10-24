"""experiment.yaml schema, CLI override precedence and factor guard."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.design.experiment.factor_guard import check_factors
from raidar.design.experiment.loader import UNSET, CliOverrides, load_experiment, parse_assignment
from raidar.design.experiment.validation import SpecValidationError
from tests.experiment_spec_support import (
    AGENTS_BENCHMARK,
    MECHANICS_SMOKE,
    SHARED_FRAMING,
    SMOKE_EXPERIMENT,
    experiment_document,
    write_experiment,
)


def _issues(path: Path, overrides: CliOverrides | None = None) -> dict[str, str]:
    with pytest.raises(SpecValidationError) as caught:
        load_experiment(path, overrides)
    return {issue.loc: issue.message for issue in caught.value.issues}


@pytest.mark.parametrize("committed", [MECHANICS_SMOKE, SMOKE_EXPERIMENT, AGENTS_BENCHMARK])
def test_validate_command_accepts_committed_experiments(committed: Path) -> None:
    result = CliRunner().invoke(main, ["validate", str(committed)])
    scorers = len(load_experiment(committed).spec.assessment.scorers)

    assert result.exit_code == 0, result.output
    assert "Experiment validation passed." in result.output
    assert f"scorer references: {scorers} (resolver available)" in result.output


def test_invalid_file_reports_every_error_at_its_field(tmp_path: Path) -> None:
    document = experiment_document(unknown_key=1)
    document["agents"][0]["harness"] = "not-a-harness"
    document["agents"][0]["version"] = "latest"
    document["analysis"]["attempts"] = 0
    document["problems"] = [{"ref": "forge"}, {"path": "."}]
    document["conditions"][1]["components"][0]["files"] = ["/etc/passwd", "../../etc/hosts"]
    document["conditions"][1]["components"][0]["with"] = {"depth": {"limit": float("nan")}}
    document["protocol"] = {"progression": "halt_on_failure"}

    issues = _issues(write_experiment(tmp_path, document))

    assert set(issues) >= {
        "unknown_key",
        "agents.0.harness",
        "agents.0.version",
        "analysis.attempts",
        "problems.0.ref",
        "problems.1.path",
        "conditions.1.components.0.files.0",
        "conditions.1.components.0.files.1",
        "conditions.1.components.0.with",
        "protocol",
    }
    assert "halt_on_failure" in issues["protocol"]
    assert "depth.limit" in issues["conditions.1.components.0.with"]


@pytest.mark.parametrize(
    ("change", "loc"),
    [
        ({"provider": "anthropic"}, "agents.0.provider"),
        ({"model": "claude-opus-4-7"}, "agents.0.model"),
        ({"reasoning_effort": "xhigh"}, "agents.0.reasoning_effort"),
        (
            {"name": "gemini-cli", "provider": "google", "model": "gemini-3-pro-preview"},
            "agents.0",
        ),
        ({"name": "cursor-cli", "provider": "acme", "model": "any"}, "agents.0.provider"),
        ({"name": "cursor-cli", "provider": "openai", "model": "any"}, None),
    ],
    ids=[
        "provider",
        "model",
        "effort-outside-profile",
        "gemini-effort",
        "external-provider",
        "external-accepted",
    ],
)
def test_agents_are_checked_against_their_harness_model_support(
    tmp_path: Path, change: dict, loc: str | None
) -> None:
    document = experiment_document()
    document["agents"][0].update(change)
    path = write_experiment(tmp_path, document)

    if loc is None:
        load_experiment(path)
        return
    issues = _issues(path)
    assert len(issues) == 1
    assert next(iter(issues)).startswith(loc)


def test_component_files_may_not_overlap_a_local_task_directory(tmp_path: Path) -> None:
    document = experiment_document(problems=[{"path": "tasks/forge"}])
    document["conditions"][1]["components"][0]["files"] = [
        "tasks/forge/tests/test_hidden.py",
        "components/AGENTS.md",
        "tasks/forge-notes.md",
    ]
    document["conditions"].append(
        {"id": "wide", "components": [{"use": "rules-file@1", "files": ["./tasks"]}]}
    )

    issues = _issues(write_experiment(tmp_path, document))

    assert "conditions.1.components.0.files.0" in issues
    assert "hidden test" in issues["conditions.1.components.0.files.0"]
    assert "conditions.1.components.0.files.1" not in issues
    assert "conditions.1.components.0.files.2" not in issues
    assert "conditions.2.components.0.files.0" in issues


def test_cross_reference_errors_are_located_on_the_referring_field(tmp_path: Path) -> None:
    document = experiment_document()
    document["arms"][1]["agent"] = "ghost"
    document["arms"].append({"id": "guided", "condition": "nowhere", "agent": "luna"})
    document["analysis"]["baseline_arm"] = "control"
    document["assessment"]["metric"] = "scb-canonical@9"

    issues = _issues(write_experiment(tmp_path, document))

    assert set(issues) == {
        "arms.1.agent",
        "arms.2.id",
        "arms.2.condition",
        "analysis.baseline_arm",
        "assessment.metric",
    }


def test_duplicate_yaml_keys_are_rejected(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())
    path.write_text(path.read_text() + "id: second\n", encoding="utf-8")

    issues = _issues(path)

    assert "duplicate key 'id'" in issues[""]


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


def test_a_bad_override_value_reports_only_its_own_field(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())

    issues = _issues(path, CliOverrides(assignments=("agents.0.version=1.10",)))
    quoted = load_experiment(path, CliOverrides(assignments=("agents.0.version='1.10'",)))

    assert set(issues) == {"agents.0.version"}
    assert quoted.spec.agents[0].version == "1.10"


def test_factor_guard_rejects_arms_differing_in_an_undeclared_noun(tmp_path: Path) -> None:
    document = experiment_document()
    document["agents"].append({**document["agents"][0], "id": "mini", "model": "gpt-5.4-mini"})
    document["arms"][1]["agent"] = "mini"

    issues = _issues(write_experiment(tmp_path, document))

    assert issues == {
        "arms.1": "arms 'baseline' and 'guided' differ in undeclared noun 'agent.model' "
        "(declared factors: condition)"
    }


def test_factor_guard_accepts_declared_parent_noun_and_ignores_ids(tmp_path: Path) -> None:
    document = experiment_document(factors=["condition", "agent"])
    document["agents"].append({**document["agents"][0], "id": "mini", "model": "gpt-5.4-mini"})
    document["agents"].append({**document["agents"][0], "id": "luna-copy"})
    document["conditions"].append({"id": "baseline-copy", "components": []})
    document["arms"] = [
        {"id": "baseline", "condition": "baseline", "agent": "luna"},
        {"id": "renamed", "condition": "baseline-copy", "agent": "luna-copy"},
        {"id": "mini", "condition": "guided", "agent": "mini"},
    ]

    spec = load_experiment(write_experiment(tmp_path, document)).spec

    assert check_factors(spec) == []


def test_factor_guard_flags_component_differences_when_condition_is_undeclared(
    tmp_path: Path,
) -> None:
    document = experiment_document(kind="smoke", factors=["agent.model"])

    issues = _issues(write_experiment(tmp_path, document))

    assert list(issues) == ["arms.1"]
    assert "undeclared noun 'condition'" in issues["arms.1"]


def test_shared_components_lead_every_condition_and_never_split_the_arms(
    tmp_path: Path,
) -> None:
    """Pilot v2: framing both arms get is not a factor, so the guard ignores it."""
    document = experiment_document(
        kind="smoke", factors=["agent.model"], shared_components=[SHARED_FRAMING]
    )
    document["conditions"][1]["components"] = []

    spec = load_experiment(write_experiment(tmp_path, document)).spec

    assert check_factors(spec) == []
    for condition in ("baseline", "guided"):
        assert [use.use for use in spec.condition(condition).components] == ["prompt-wrapper@1"]
    assert spec.references()["component"] == ("prompt-wrapper@1",)
    assert spec.to_json()["shared_components"] == [{**SHARED_FRAMING, "with": {}}]

    guided = experiment_document(shared_components=[SHARED_FRAMING])
    spec = load_experiment(write_experiment(tmp_path / "guided", guided)).spec
    assert [use.use for use in spec.condition("guided").components] == [
        "prompt-wrapper@1",
        "rules-file@1",
    ]
    document = experiment_document(
        kind="smoke", factors=["agent.model"], shared_components=[SHARED_FRAMING]
    )
    issues = _issues(write_experiment(tmp_path / "split", document))
    assert list(issues) == ["arms.1"]
    assert "undeclared noun 'condition'" in issues["arms.1"]


def test_a_condition_may_not_repeat_a_shared_component(tmp_path: Path) -> None:
    document = experiment_document(shared_components=[SHARED_FRAMING])
    document["conditions"][1]["components"].append(SHARED_FRAMING)

    issues = _issues(write_experiment(tmp_path, document))

    assert issues == {
        "conditions.1.components.1.use": "prompt-wrapper@1 is already in shared_components, "
        "which every condition gets"
    }


def test_specs_without_shared_components_serialise_as_before(tmp_path: Path) -> None:
    spec = load_experiment(write_experiment(tmp_path, experiment_document())).spec

    assert "shared_components" not in spec.to_json()
    assert spec.condition("guided") is spec.conditions[1]


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
        ({"assessment": {"scorers": ["correctness@1", "correctness@1"]}}, "assessment.scorers.1"),
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
        path, CliOverrides(assignments=("assessment.scorers.0=code-quality@3",))
    ).spec

    assert spec.assessment.scorers == ("code-quality@3",)


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


def test_set_overrides_change_only_the_addressed_yaml_alias(tmp_path: Path) -> None:
    from raidar.design.experiment.loader import _read_document, apply_assignment

    path = tmp_path / "aliased.yaml"
    path.write_text(
        "conditions:\n"
        "  - {id: a, components: [{use: review-loop@2, with: &w {max_review_rounds: 2}}]}\n"
        "  - {id: b, components: [{use: review-loop@2, with: *w}]}\n"
    )
    document, _ = _read_document(path)

    apply_assignment(document, "conditions.1.components.0.with.max_review_rounds", 3)

    rounds = [c["components"][0]["with"]["max_review_rounds"] for c in document["conditions"]]
    assert rounds == [2, 3]


def test_concurrency_is_not_a_lock_or_validate_override(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())
    for command in ("lock", "validate"):
        result = CliRunner().invoke(main, [command, str(path), "--concurrency", "3"])
        assert result.exit_code == 2
        assert "No such option: --concurrency" in result.output
