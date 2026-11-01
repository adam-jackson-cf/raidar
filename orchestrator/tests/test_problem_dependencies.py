"""Import records the ``declared-wheels/1`` pin: locks beside the manifest, identity, exclusion."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from raidar.contract.wheels import DependencyInfraError
from raidar.design.problem.importer import ProblemDriftError, import_problem
from raidar.design.problem.models import ProblemId
from raidar.design.problem.store import ProblemStore, ProblemStoreError
from raidar.design.problem.validation import DEPENDENCY_CHECK, Verdict
from raidar.design.problem.validator import ValidateOptions, validate_problem
from raidar.grading.dependency_pins import REFUSAL_REASON, DependencyLocks, Unprovided
from raidar.grading.resolver import Resolution
from tests.problems_support import FakeResolver, build_fake_scb_repo, fake_store

pytestmark = pytest.mark.slow


class Outage:
    identity = "outage"

    def __call__(self, requirements: Sequence[str], *, arch: str, exclude_newer: str) -> Resolution:
        raise DependencyInfraError("index unreachable")


@pytest.fixture
def scb(tmp_path: Path) -> tuple[ProblemStore, Path, ProblemId]:
    repo, commit = build_fake_scb_repo(tmp_path / "src")
    problem = ProblemId.parse(f"scb/adder@{commit[:12]}")
    return fake_store(tmp_path / "problems", commit), repo, problem


def test_import_pins_dependencies_and_writes_locks_beside_the_manifest(scb) -> None:
    store, repo, problem = scb
    result = import_problem(store, problem, source_checkout=repo, resolver=FakeResolver())
    pins = result.manifest.dependencies
    assert pins is not None
    assert result.manifest.dependency_refusal is None
    assert pins.policy == "declared-wheels/1"
    assert pins.exclude_newer.endswith("Z")
    locks = DependencyLocks(store.deps_dir(problem))
    assert locks.read_pins(pins)  # every recorded digest matches the committed file
    assert result.manifest.lock_metadata()["dependencies_sha256"] == pins.digest()
    assert json.loads(store.manifest_path(problem).read_text())["dependencies"]["policy"]
    assert store.load(problem).manifest.dependencies == pins


def test_reimport_keeps_the_cutoff_and_reuses_the_committed_locks(scb) -> None:
    store, repo, problem = scb
    first = import_problem(store, problem, source_checkout=repo, resolver=FakeResolver())
    resolver = FakeResolver()
    again = import_problem(store, problem, source_checkout=repo, resolver=resolver)
    assert not again.changed
    assert again.manifest == first.manifest
    assert resolver.cutoffs == []


def test_a_changed_pin_moves_the_manifest_digest(scb) -> None:
    store, repo, problem = scb
    first = import_problem(store, problem, source_checkout=repo, resolver=FakeResolver())
    manifest = first.manifest
    other = manifest.model_copy(
        update={
            "dependencies": manifest.dependencies.model_copy(
                update={"exclude_newer": "2020-01-01T00:00:00Z"}
            )
        }
    )
    assert other.digest() != manifest.digest()
    assert (
        other.lock_metadata()["dependencies_sha256"]
        != manifest.lock_metadata()["dependencies_sha256"]
    )
    store.write_manifest(other)
    with pytest.raises(ProblemDriftError):
        import_problem(store, problem, source_checkout=repo, resolver=FakeResolver())


def test_tampered_lock_is_refused_on_load(scb) -> None:
    store, repo, problem = scb
    pins = import_problem(store, problem, source_checkout=repo, resolver=FakeResolver()).manifest
    path = next(store.deps_dir(problem).glob("*.toml"))
    path.write_text("tampered\n")
    with pytest.raises(ProblemStoreError, match="differs from its pin"):
        store.load(problem)
    assert pins.dependencies is not None


def test_unresolvable_set_excludes_the_problem(scb) -> None:
    store, repo, problem = scb
    resolver = FakeResolver((Unprovided(requirement="sdist-only", reason="no-wheel"),))
    result = import_problem(store, problem, source_checkout=repo, resolver=resolver)
    refusal = result.manifest.dependency_refusal
    assert refusal is not None
    assert refusal.reason == REFUSAL_REASON
    assert result.manifest.dependencies is None
    assert not store.deps_dir(problem).exists()
    report = asyncio.run(validate_problem(store, problem, ValidateOptions(docker_context="unused")))
    assert report.verdict is Verdict.EXCLUDED
    assert [check.name for check in report.checks] == [DEPENDENCY_CHECK]
    assert "grades" not in report.to_json()
    # A refused re-import keeps its cutoff.
    retry = FakeResolver()
    import_problem(store, problem, source_checkout=repo, update=True, resolver=retry)
    assert retry.cutoffs
    assert set(retry.cutoffs) == {refusal.exclude_newer}


def test_infra_outage_is_not_an_exclusion(scb) -> None:
    store, repo, problem = scb

    with pytest.raises(DependencyInfraError):
        import_problem(store, problem, source_checkout=repo, resolver=Outage())
    assert not store.manifest_path(problem).exists()


def test_a_manifest_without_a_pin_is_not_usable(scb) -> None:
    store, repo, problem = scb
    manifest = import_problem(
        store, problem, source_checkout=repo, resolver=FakeResolver()
    ).manifest
    unpinned = manifest.model_copy(update={"dependencies": None})
    with pytest.raises(ProblemStoreError, match="validation|dependency pin"):
        store.require_validated(problem, unpinned)
