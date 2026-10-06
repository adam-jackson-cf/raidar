"""``analysis.primary_outcome``: the pre-registered primary the report, markdown and review label.

Unset, the primary is the progression's default (``solve_rate`` under
``continue``), exactly as before. Set, it must be a known trial outcome; it is
analysis configuration (the lock's ``analysis`` section), so ``lock_sha256``
does not move; and an errata-applied primary is always reported with its raw
counterpart beside it. The gate-effort version the lock declares is read too
(``@2`` before ``@1``).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from raidar.analysis.assessed import GATE_EFFORT_REFS, gate_effort_ref
from raidar.analysis.effects import Settings
from raidar.analysis.markdown import render_markdown
from raidar.analysis.outcomes import (
    PRIMARY_FROM_LOCK,
    RAW_COUNTERPART,
    SOLVE_RATE,
    SOLVE_RATE_ERRATA,
    primary_metric,
    primary_source,
)
from raidar.analysis.report import analyse
from raidar.spec.loader import load_experiment
from raidar.spec.lock import build_lock, render_lock, stale_sections
from raidar.spec.metrics import MetricResolver
from raidar.spec.references import ResolverSet
from raidar.spec.validation import SpecValidationError
from tests.analysis_support import inputs, paired_arms
from tests.experiment_spec_support import (
    experiment_document,
    fixture_resolvers,
    write_experiment,
)

SETTINGS = Settings(min_pairs=5, max_exclusion_fraction=0.2, bootstrap_samples=2000, seed=7)


def _outcomes() -> list[Any]:
    raw = paired_arms(SOLVE_RATE, [0.25] * 6, [0.5] * 6)
    errata = paired_arms(SOLVE_RATE_ERRATA, [0.5] * 6, [0.5, 0.625, 0.5, 0.625, 0.5, 0.625])
    merged = []
    for left, right in zip(raw, errata, strict=True):
        merged.append(left.model_copy(update={"metrics": {**left.metrics, **right.metrics}}))
    return merged


# ---- the experiment schema -------------------------------------------------------------


def test_primary_outcome_is_validated_against_known_trial_outcomes(tmp_path: Path) -> None:
    document = experiment_document()
    document["analysis"]["primary_outcome"] = SOLVE_RATE_ERRATA
    spec = load_experiment(write_experiment(tmp_path / "ok", document)).spec
    assert spec.analysis.primary_outcome == SOLVE_RATE_ERRATA
    assert spec.to_json()["analysis"]["primary_outcome"] == SOLVE_RATE_ERRATA

    plain = load_experiment(write_experiment(tmp_path / "plain", experiment_document())).spec
    assert plain.analysis.primary_outcome is None
    assert "primary_outcome" not in plain.to_json()["analysis"]

    for bad in ("vibes", "gate.first_pass_clean_rate"):
        document["analysis"]["primary_outcome"] = bad
        with pytest.raises(SpecValidationError) as caught:
            load_experiment(write_experiment(tmp_path / bad, document))
        issues = {issue.loc: issue.message for issue in caught.value.issues}
        assert f"unknown primary outcome {bad!r}" in issues["analysis.primary_outcome"]


def test_setting_the_primary_relocks_the_analysis_section_only(tmp_path: Path) -> None:
    resolvers = ResolverSet([*fixture_resolvers(), MetricResolver()], entry_point_group=None)

    def locked(document: dict[str, Any], name: str) -> dict[str, Any]:
        path = write_experiment(tmp_path / name, document)
        loaded = load_experiment(path)
        lock_path = path.with_name("experiment.lock.json")
        return json.loads(render_lock(build_lock(loaded, lock_path=lock_path, resolvers=resolvers)))

    base = locked(experiment_document(), "base")
    document = experiment_document()
    document["analysis"]["primary_outcome"] = SOLVE_RATE_ERRATA
    primary = locked(document, "primary")

    assert primary["lock_sha256"] == base["lock_sha256"]
    assert "analysis" in stale_sections(base, primary)
    assert "execution" not in stale_sections(base, primary)
    assert primary["analysis"]["primary_outcome"] == SOLVE_RATE_ERRATA
    assert "primary_outcome" not in base["analysis"]


# ---- the analysis report ---------------------------------------------------------------


def test_the_default_primary_is_unchanged() -> None:
    report = analyse(inputs(_outcomes(), 6), SETTINGS)

    assert (report.primary_metric, report.primary_source) == (
        SOLVE_RATE,
        "default for continue progression",
    )
    (comparison,) = report.comparisons
    assert comparison.primary.metric == SOLVE_RATE
    assert comparison.primary_raw is None
    markdown = render_markdown(report)
    assert "**Primary outcome** `solve_rate`" in markdown
    assert "Raw (beside the primary)" not in markdown
    assert primary_metric("halt_on_failure") == "reached"


def test_a_preregistered_errata_primary_is_labelled_with_raw_beside_it() -> None:
    report = analyse(
        dataclasses.replace(inputs(_outcomes(), 6), primary_outcome=SOLVE_RATE_ERRATA), SETTINGS
    )

    assert (report.primary_metric, report.primary_source) == (SOLVE_RATE_ERRATA, PRIMARY_FROM_LOCK)
    (comparison,) = report.comparisons
    assert comparison.primary.metric == SOLVE_RATE_ERRATA
    assert comparison.primary_raw is not None
    assert comparison.primary_raw.metric == SOLVE_RATE
    assert comparison.primary_raw.mean_delta == pytest.approx(0.25)
    markdown = render_markdown(report)
    assert "**Primary outcome** `solve_rate_errata`" in markdown
    assert "**Raw (beside the primary)** `solve_rate`" in markdown
    assert "- Primary outcome: `solve_rate_errata` (lock (analysis.primary_outcome)" in markdown
    assert "| Outcome | Problem | Both | Baseline only |" in markdown
    assert "| final_solved | toy@1 |" in markdown
    assert "| final_solved_errata | toy@1 |" in markdown
    assert RAW_COUNTERPART[SOLVE_RATE_ERRATA] == SOLVE_RATE
    assert primary_source("continue", SOLVE_RATE_ERRATA) == PRIMARY_FROM_LOCK


def test_an_errata_primary_without_errata_still_has_a_primary_effect() -> None:
    outcomes = paired_arms(SOLVE_RATE, [0.25] * 5, [0.5] * 5)

    report = analyse(
        dataclasses.replace(inputs(outcomes, 5), primary_outcome=SOLVE_RATE_ERRATA), SETTINGS
    )

    (comparison,) = report.comparisons
    assert comparison.primary.metric == SOLVE_RATE_ERRATA
    assert comparison.primary.verdict.value == "insufficient"
    assert comparison.primary_raw is not None
    assert comparison.primary_raw.metric == SOLVE_RATE


def test_the_lock_declared_gate_effort_version_is_read_newest_first() -> None:
    assert GATE_EFFORT_REFS == (
        "gate-effort@5",
        "gate-effort@4",
        "gate-effort@3",
        "gate-effort@2",
        "gate-effort@1",
    )
    assert gate_effort_ref(["gate-effort@4", "gate-effort@3"]) == "gate-effort@4"
    assert gate_effort_ref(["correctness@1", "gate-effort@1"]) == "gate-effort@1"
    assert gate_effort_ref(["gate-effort@2", "gate-effort@1"]) == "gate-effort@2"
    assert gate_effort_ref(["gate-effort@3", "correctness@1"]) == "gate-effort@3"
    assert gate_effort_ref(["correctness@1"]) is None
