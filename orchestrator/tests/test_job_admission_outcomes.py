"""Job admission outcomes: run checks, runner provenance, recipe identities, assessment pins.

Decided from in-memory plans and injected collaborators (credential preflight, problem store,
git state, digests): no run directory, process or network is touched.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml

from raidar.design.experiment.schema import AssessmentSpec, ProtocolSpec
from raidar.design.problem.store import ProblemStoreError
from raidar.engine.job import provenance, recipe, run_checks
from raidar.engine.job.assessment import (
    ASSESSMENT_CONFIG_FILE,
    lock_errata,
    lock_rescore_config,
    lock_solvable,
    write_assessment_config,
)
from raidar.engine.job.credentials import CredentialPreflightError
from raidar.subject.environment import load_runtime_pack
from raidar.subject.environment.python.image import image_lock_hash

# ---- run checks ------------------------------------------------------------------------


def _lock(kind: str) -> dict[str, Any]:
    return {"execution": {"design": {"kind": kind}}}


def _target() -> Any:
    return SimpleNamespace(
        manifest_fields=lambda concurrency: {
            "execution_target": {"name": "local", "docker": "27", "concurrency": concurrency}
        }
    )


def _prepare(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    kind: str,
    allow_unsmoked: bool = False,
    credentials_error: Exception | None = None,
    problems_error: Exception | None = None,
) -> Any:
    def preflight(_plan: object) -> None:
        if credentials_error is not None:
            raise credentials_error

    def load(_plan: object, _store: object) -> dict[int, Any]:
        if problems_error is not None:
            raise problems_error
        return {0: "problem"}

    monkeypatch.setattr(run_checks, "preflight_credentials", preflight)
    monkeypatch.setattr(run_checks, "load_problems", load)
    monkeypatch.setattr(run_checks, "runner_provenance", dict)
    monkeypatch.setattr(run_checks, "recipe_digests", lambda _plan, _problems: {})
    plan = SimpleNamespace(lock=_lock(kind), concurrency=2)
    return run_checks.prepare_run(
        cast(Any, plan),
        cast(Any, object()),
        _target(),
        tmp_path,
        allow_unsmoked=allow_unsmoked,
    )


def test_a_smoke_experiment_is_admitted_without_a_prior_smoke(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    problems, admission = _prepare(monkeypatch, tmp_path, kind="smoke")

    assert problems == {0: "problem"}
    assert admission == {"required": False, "kind": "smoke"}


def test_a_measured_run_without_a_clean_smoke_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(run_checks.RunRefusedError, match="no clean smoke job matches"):
        _prepare(monkeypatch, tmp_path, kind="ab")


def test_allow_unsmoked_records_the_override_instead_of_refusing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _problems, admission = _prepare(monkeypatch, tmp_path, kind="ab", allow_unsmoked=True)

    assert admission["required"] is True
    assert admission["overridden"] is True
    assert admission["smoke_job"] is None


def test_a_missing_credential_refuses_the_run_with_the_preflight_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(run_checks.RunRefusedError, match="no key for agents.0"):
        _prepare(
            monkeypatch,
            tmp_path,
            kind="smoke",
            credentials_error=CredentialPreflightError("no key for agents.0"),
        )


def test_an_unusable_problem_store_refuses_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(run_checks.RunRefusedError, match="manifest mismatch"):
        _prepare(
            monkeypatch,
            tmp_path,
            kind="smoke",
            problems_error=ProblemStoreError("manifest mismatch"),
        )


# ---- runner provenance and effective limits --------------------------------------------


def _runner_in_checkout_with_changes(
    monkeypatch: pytest.MonkeyPatch, changed: list[str]
) -> dict[str, Any]:
    """``runner_provenance`` where git reports ``changed`` paths, honouring the excludes."""

    def git(_cwd: Path, *, exclude: tuple[str, ...] = ()) -> dict[str, Any]:
        counted = [path for path in changed if not path.startswith(tuple(exclude))]
        return {"commit": "abc123", "dirty": bool(counted), "error": None}

    monkeypatch.setattr(provenance, "git_state", git)
    monkeypatch.setattr(provenance, "spec_source_digest", lambda: "spec-digest")
    monkeypatch.setattr(provenance, "execution_source_digest", lambda: "exec-digest")
    return provenance.runner_provenance()


def test_notes_under_docs_do_not_make_the_runner_dirty(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _runner_in_checkout_with_changes(monkeypatch, ["docs/notes.md"])

    assert record["dirty"] is False
    assert record["commit"] == "abc123"
    assert record["toolchain_sha256"] == "spec-digest"
    assert record["execution_sha256"] == "exec-digest"


def test_a_changed_source_file_makes_the_runner_dirty(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _runner_in_checkout_with_changes(monkeypatch, ["docs/notes.md", "orchestrator/x.py"])

    assert record["dirty"] is True


def test_an_explicit_zero_setup_override_is_kept_not_replaced_by_the_default() -> None:
    assert provenance.setup_limit(45) == 45.0
    assert provenance.setup_limit(0) == 0.0
    assert provenance.setup_limit(None) != provenance.setup_limit(0)


def _limits_plan(protocol: ProtocolSpec) -> Any:
    def agent(timeout: int | None, setup: int | None) -> Any:
        return SimpleNamespace(
            agent_config=SimpleNamespace(
                override_timeout_sec=timeout, override_setup_timeout_sec=setup
            )
        )

    return SimpleNamespace(
        arms={"tuned": agent(600, 30), "plain": agent(None, None)},
        loaded=SimpleNamespace(spec=SimpleNamespace(protocol=protocol)),
    )


def test_effective_limits_record_each_arms_configured_timeouts_before_multipliers() -> None:
    limits = provenance.effective_limits(_limits_plan(ProtocolSpec()))

    assert limits["agent_s"] == {"tuned": 600, "plain": None}
    assert limits["agent_setup_s"] == {
        "tuned": 30.0,
        "plain": provenance.setup_limit(None),
    }
    assert set(limits["multipliers"]) == {
        "timeout_multiplier",
        "agent_timeout_multiplier",
        "verifier_timeout_multiplier",
        "agent_setup_timeout_multiplier",
        "environment_build_timeout_multiplier",
    }
    assert limits["environment_build_s"] > 0


def test_the_verifier_limit_counts_the_coverage_run_only_when_it_runs_in_the_trial() -> None:
    in_trial = provenance.effective_limits(_limits_plan(ProtocolSpec()))["verifier_s"]
    offline = provenance.effective_limits(_limits_plan(ProtocolSpec(verifier_coverage="offline")))[
        "verifier_s"
    ]

    assert in_trial - offline == ProtocolSpec().verifier_timeout_sec


# ---- recipe identities -----------------------------------------------------------------


def _plan_of(*indexes: int) -> Any:
    return SimpleNamespace(
        problems=[SimpleNamespace(ref=f"p/{index}", index=index) for index in indexes],
        loaded=SimpleNamespace(spec=SimpleNamespace(runtime="python")),
    )


def test_a_grader_recipe_change_changes_only_that_problems_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The real tag hashes the grader Dockerfile; the Dockerfile text is the bundle's recipe.
    monkeypatch.setattr("raidar.grading.grader_image.grader_dockerfile", lambda bundle: bundle)

    def digests(first: str, second: str) -> dict[str, Any]:
        problems = {0: SimpleNamespace(bundle=first), 1: SimpleNamespace(bundle=second)}
        return recipe.recipe_digests(_plan_of(0, 1), cast(Any, problems))

    before = digests("FROM a", "FROM b")
    after = digests("FROM a", "FROM b2")

    assert before["pack"] == after["pack"]
    assert before["graders"]["p/0"] == after["graders"]["p/0"]
    assert before["graders"]["p/1"] != after["graders"]["p/1"]
    assert before["graders"]["p/0"] != before["graders"]["p/1"]


def test_only_loaded_problems_have_a_grader_digest_and_the_pack_is_the_real_recipe() -> None:
    digests = recipe.recipe_digests(_plan_of(5), {})

    assert digests["graders"] == {}
    assert digests["pack"] == image_lock_hash(load_runtime_pack("python"))


# ---- assessment pins -------------------------------------------------------------------

_ERRATA = {"sha256": "e" * 8, "exclude_from_solved": ["case-1"]}
_SOLVABLE = {"sha256": "s" * 8, "steps": {"step-1": ["case-2"]}}


def _pinning_lock(**problems: dict[str, Any]) -> dict[str, Any]:
    return {
        "spec": {"assessment": {"scorers": ["correctness@10"]}},
        "execution": {
            "references": {
                "problem": {ref: {"metadata": metadata} for ref, metadata in problems.items()}
            }
        },
    }


def test_a_lock_pins_errata_and_solvable_sets_per_problem_and_skips_unpinned_ones() -> None:
    lock = _pinning_lock(
        pinned={"errata": _ERRATA, "solvable": _SOLVABLE}, plain={}, only_errata={"errata": _ERRATA}
    )

    errata = lock_errata(lock)
    solvable = lock_solvable(lock)

    assert sorted(errata) == ["only_errata", "pinned"]
    assert errata["pinned"].exclude_from_solved == ("case-1",)
    assert sorted(solvable) == ["pinned"]
    assert solvable["pinned"].steps == {"step-1": ("case-2",)}


def test_a_lock_without_references_pins_nothing() -> None:
    assert lock_errata({}) == {}
    assert lock_solvable({"execution": {"references": None}}) == {}


def test_the_rescore_config_hands_the_pinned_sets_to_scorers_that_take_them() -> None:
    lock = _pinning_lock(pinned={"errata": _ERRATA, "solvable": _SOLVABLE})

    config = lock_rescore_config(lock)

    scorer = config.scorers["correctness@10"]
    assert scorer["errata"]["pinned"]["sha256"] == _ERRATA["sha256"]
    assert scorer["solvable"]["pinned"]["steps"] == {"step-1": ["case-2"]}
    assert scorer["progression"] == "continue"


def test_a_lock_without_a_spec_cannot_yield_assessor_settings() -> None:
    with pytest.raises(ValueError, match="the lock has no spec"):
        lock_rescore_config({"spec": None})


def test_the_assessment_config_file_round_trips_the_pinned_settings(tmp_path: Path) -> None:
    lock = _pinning_lock(pinned={"errata": _ERRATA})
    assessment = AssessmentSpec.model_validate(lock["spec"]["assessment"])

    path = write_assessment_config(
        assessment,
        ProtocolSpec(),
        tmp_path,
        errata=lock_errata(lock),
        solvable=lock_solvable(lock),
    )

    assert path == tmp_path / ASSESSMENT_CONFIG_FILE
    written = yaml.safe_load(path.read_text())
    assert written["scorers"]["correctness@10"]["errata"]["pinned"]["sha256"] == _ERRATA["sha256"]
