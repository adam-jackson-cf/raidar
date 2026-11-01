"""Problem records: manifest validation, resolution metadata, store refusals, errata drafts, audit.

Every record here is built in memory or as a few tiny files; git, the converter and the grader
are replaced by injected stand-ins, so nothing builds a store or starts a process.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from raidar.contract.references import RefKind, RegistryRef, RegistryReferenceError
from raidar.design.problem import audit, importer
from raidar.design.problem.audit import (
    REFERENCE,
    DynamicEvidence,
    StepGradeSummary,
    audit_report,
    grade_audit_variants,
    spec_texts,
)
from raidar.design.problem.content import content_digest
from raidar.design.problem.dependencies import ProblemDependencies, resolve_dependencies
from raidar.design.problem.determinacy import AssertionKind, AuditedAssertion, Determination
from raidar.design.problem.errata import (
    ErrataError,
    draft_errata,
    errata_ref_from_metadata,
    load_errata,
)
from raidar.design.problem.models import (
    ProblemId,
    ProblemManifest,
    dependency_content_key,
)
from raidar.design.problem.resolver import ProblemResolver
from raidar.design.problem.store import ImportedProblem, ProblemStore, ProblemStoreError
from raidar.design.problem.validation import solvable_digest
from raidar.design.problem.variant_shim import NEUTRAL_ERROR_TEXT
from raidar.grading.dependency_pins import (
    DependencyLocks,
    DependencyPins,
    DependencyRefusal,
    DependencySet,
    Unprovided,
    lock_sha256,
)
from raidar.grading.models import (
    CaseGroup,
    CaseResult,
    CaseStatus,
    InfraFailure,
    VerifierGrade,
)
from raidar.grading.records import grading_execution_digest
from tests.problems_support import FakeResolver

COMMIT = "ab" * 20
VERSION = COMMIT[:12]
POSITIVE_REF = f"fixture/adder@{VERSION}"
ONESHOT_REF = f"fixture/adder.checkpoint_1@{VERSION}"
ADDS = "test_checkpoint_1.py::test_adds"
MULTIPLIES = "test_checkpoint_2.py::test_multiplies"
IMPLEMENTATION = "a" * 64
ENVIRONMENT_ID = "sha256:" + "f" * 64
CUTOFF = "2026-10-01T00:00:00Z"
LOCK_TEXT = 'lock-version = "1.0"\n'
SET_KEY = "1" * 64


def _pins(exclude_newer: str = CUTOFF) -> DependencyPins:
    locked = lock_sha256(LOCK_TEXT)
    return DependencyPins(
        policy="declared-wheels/1",
        resolver="fake-uv@0",
        index="https://pypi.org/simple",
        python="3.12",
        platforms=("amd64", "arm64"),
        exclude_newer=exclude_newer,
        sets={SET_KEY: DependencySet(requirements=(), amd64=locked, arm64=locked)},
        steps={"checkpoint_1": SET_KEY, "checkpoint_2": SET_KEY},
    )


def _write_locks(store: ProblemStore, problem: ProblemId, pins: DependencyPins) -> None:
    locks = DependencyLocks(store.deps_dir(problem))
    for key, arch in pins.lock_keys():
        locks.write(key, arch, LOCK_TEXT)


def _manifest_json(*, one_shot: bool = False) -> dict[str, Any]:
    names = ["checkpoint_1"] if one_shot else ["checkpoint_1", "checkpoint_2"]
    return {
        "ref": ONESHOT_REF if one_shot else POSITIVE_REF,
        "mode": "one_shot" if one_shot else "multi_checkpoint",
        "checkpoints": len(names),
        "steps": [
            {
                "name": name,
                "checkpoint": name,
                "instruction_sha256": "1" * 64,
                "test_files": [f"test_{name}.py"],
            }
            for name in names
        ],
        "source": {
            "dataset": "fixture",
            "url": "fixture://adder",
            "commit": COMMIT,
            "problem": "adder",
            "problem_tree": "t",
            "converter_tree": "c",
            "converted_task_sha256": "x",
        },
        "pack": {"id": "python", "version": 1, "image": "pack:image"},
        "task_sha256": "2" * 64,
        "bundle_sha256": "3" * 64,
        "references_sha256": "4" * 64,
        "dependencies": _pins().model_dump(mode="json"),
    }


def _manifest(*, one_shot: bool = False, **changes: Any) -> ProblemManifest:
    return ProblemManifest.model_validate({**_manifest_json(one_shot=one_shot), **changes})


@pytest.fixture(name="store")
def store_fixture(tmp_path: Path) -> ProblemStore:
    """A store holding only the committed manifest of ``fixture/adder``."""
    store = ProblemStore(tmp_path / "problems")
    store.write_manifest(_manifest())
    return store


# ---- manifest consistency ----------------------------------------------------------------


def test_a_manifest_refuses_records_that_contradict_each_other() -> None:
    one_shot = _manifest_json(one_shot=True)
    refusal = DependencyRefusal(detail="no wheel", exclude_newer=CUTOFF)

    with pytest.raises(ValidationError, match="3 checkpoints but 2 steps"):
        _manifest(checkpoints=3)
    with pytest.raises(ValidationError, match="implies mode multi_checkpoint"):
        _manifest(mode="one_shot")
    with pytest.raises(ValidationError, match="exactly one checkpoint"):
        _manifest(one_shot=True, checkpoints=2, steps=one_shot["steps"] * 2)
    with pytest.raises(ValidationError, match="!= source commit"):
        _manifest(source={**_manifest_json()["source"], "commit": "cd" * 20})
    with pytest.raises(ValidationError, match="dependencies or a dependency refusal, not both"):
        _manifest(dependency_refusal=refusal.model_dump(mode="json"))


def test_manifest_lock_metadata_records_only_the_optional_pins_it_has() -> None:
    plain = _manifest()
    metadata = plain.lock_metadata()
    assert plain.dependencies is not None
    assert metadata["dependencies_sha256"] == plain.dependencies.digest()
    assert "entry_file" not in metadata
    assert "entry_command" not in metadata

    entered = _manifest(entry_file="app.py", entry_command="uv run --project /app app.py")
    assert entered.lock_metadata()["entry_file"] == "app.py"
    assert entered.lock_metadata()["entry_command"] == "uv run --project /app app.py"
    assert entered.digest() != plain.digest()

    refused = _manifest(
        dependencies=None, dependency_refusal={"detail": "x", "exclude_newer": CUTOFF}
    )
    assert "dependencies_sha256" not in refused.lock_metadata()
    assert "dependencies" not in refused.payload()
    assert refused.content_key() != "unpinned"


def test_content_keys_separate_unpinned_pinned_and_refused_problems() -> None:
    refusal = DependencyRefusal(detail="no wheel", exclude_newer=CUTOFF)
    keys = {
        dependency_content_key(None),
        dependency_content_key(_pins()),
        dependency_content_key(refusal),
    }
    assert dependency_content_key(None) == "unpinned"
    assert len(keys) == 3


# ---- resolution --------------------------------------------------------------------------


def _write_validation(
    store: ProblemStore,
    ref: str = POSITIVE_REF,
    *,
    verdict: str = "validated",
    not_run: tuple[str, ...] = (),
    manifest_sha256: str | None = None,
    grader: bool = True,
) -> None:
    problem = ProblemId.parse(ref)
    cases = [ADDS]
    report: dict[str, Any] = {
        "verdict": verdict,
        "manifest_sha256": manifest_sha256 or store.load_manifest(problem).digest(),
        "not_run": list(not_run),
        "solvable": {
            "checkpoint_1": {
                "verdict": "validated",
                "solvable": cases,
                "digest": solvable_digest(cases),
                "nondeterministic_count": 0,
            }
        },
    }
    if grader:
        report["environment"] = {
            "grader_image": "grader:fixture",
            "grader_environment_id": ENVIRONMENT_ID,
            "grader_implementation_sha256": IMPLEMENTATION,
        }
    store.validation_path(problem).write_text(json.dumps(report))


def _resolve(store: ProblemStore, ref: str, tmp_path: Path) -> Any:
    return ProblemResolver(store.root).resolve(
        RegistryRef.parse(RefKind.PROBLEM, ref), base_dir=tmp_path
    )


def _write_errata(store: ProblemStore, ref: str, manifest_sha256: str) -> Path:
    case = {
        "id": MULTIPLIES,
        "checkpoint_origin": "checkpoint_2",
        "disposition": "exclude-from-solved",
        "rule": "wording-unstated",
        "assertion": "assert x == '6'",
        "reason": "the specification does not state the wording",
        "evidence": ["audit: wording-unstated"],
    }
    path = store.errata_path(ProblemId.parse(ref))
    path.write_text(
        yaml.safe_dump(
            {
                "kind": "raidar-problem-errata",
                "schema_version": 1,
                "problem": ref,
                "manifest_sha256": manifest_sha256,
                "registered": "2026-10-02",
                "basis": "audit v1",
                "cases": [case, {**case, "id": ADDS, "disposition": "informational"}],
            }
        )
    )
    return path


def test_a_validated_problem_resolves_with_verdict_grader_and_errata(
    store: ProblemStore, tmp_path: Path
) -> None:
    manifest = store.load_manifest(ProblemId.parse(POSITIVE_REF))
    _write_validation(store)

    plain = _resolve(store, POSITIVE_REF, tmp_path)
    assert plain.digest == manifest.digest()
    assert plain.binding["validated"] is True
    assert plain.binding["runtime"] == "python"
    assert plain.binding["grader"] == {
        "image": "grader:fixture",
        "environment_id": ENVIRONMENT_ID,
        "implementation_sha256": IMPLEMENTATION,
    }
    assert plain.binding["grader_implementation_sha256"] == grading_execution_digest()
    assert plain.metadata["validation"]["verdict"] == "validated"
    assert plain.metadata["solvable"]["steps"] == {"checkpoint_1": [ADDS]}
    assert "errata" not in plain.metadata

    path = _write_errata(store, POSITIVE_REF, manifest.digest())
    with_errata = _resolve(store, POSITIVE_REF, tmp_path)
    assert with_errata.metadata["errata"]["exclude_from_solved"] == [MULTIPLIES]
    assert with_errata.metadata["errata"]["informational"] == [ADDS]
    # The lock pins the file's bytes, so any edit moves the pinned digest.
    before = with_errata.metadata["errata"]["sha256"]
    path.write_text(path.read_text() + "# amended\n")
    assert _resolve(store, POSITIVE_REF, tmp_path).metadata["errata"]["sha256"] != before


def test_a_validation_without_a_grader_identity_binds_no_grader(
    store: ProblemStore, tmp_path: Path
) -> None:
    _write_validation(store, grader=False)
    assert _resolve(store, POSITIVE_REF, tmp_path).binding["grader"] is None


@pytest.mark.parametrize(
    ("changes", "issue"),
    [
        ({"verdict": "excluded"}, "validation verdict excluded"),
        ({"verdict": "infra_failure"}, "validation verdict infra_failure"),
        (
            {"verdict": "validated_partial", "not_run": ("repeat_identical",)},
            "required checks not run: repeat_identical",
        ),
        ({"manifest_sha256": "0" * 64}, "re-validate it"),
    ],
)
def test_an_unusable_or_stale_validation_reaches_the_gate_unvalidated(
    store: ProblemStore, tmp_path: Path, changes: dict[str, Any], issue: str
) -> None:
    _write_validation(store, **changes)
    resolved = _resolve(store, POSITIVE_REF, tmp_path)
    assert resolved.binding["validated"] is False
    assert issue in resolved.binding["validation_issue"]
    assert resolved.binding["runtime"] == "python"
    assert "validation" not in resolved.metadata


def test_a_problem_that_is_not_imported_or_has_foreign_errata_cannot_resolve(
    store: ProblemStore, tmp_path: Path
) -> None:
    with pytest.raises(RegistryReferenceError, match="is not imported"):
        _resolve(store, "fixture/absent@fefefefefefe", tmp_path)
    _write_validation(store)
    _write_errata(store, POSITIVE_REF, "0" * 64)
    with pytest.raises(RegistryReferenceError, match="re-audit the problem"):
        _resolve(store, POSITIVE_REF, tmp_path)


# ---- store -------------------------------------------------------------------------------


def test_the_store_names_where_a_refused_validation_attempt_is_kept(store: ProblemStore) -> None:
    problem = ProblemId.parse(POSITIVE_REF)
    attempt = store.validation_attempt_path(problem)
    assert attempt == store.cache_dir() / "validation" / "fixture/adder" / f"{VERSION}.json"
    assert attempt != store.validation_path(problem)


def test_loading_refuses_a_bundle_whose_dependencies_disagree_with_the_manifest_pin(
    store: ProblemStore,
) -> None:
    problem = ProblemId.parse(POSITIVE_REF)
    pins = _pins()
    manifest = _manifest()

    def check(manifest: ProblemManifest, carried: object) -> None:
        bundle: Any = SimpleNamespace(dependencies=carried)
        store._check_dependencies(problem, manifest, bundle)

    unpinned = _manifest(dependencies=None)
    check(unpinned, None)
    with pytest.raises(ProblemStoreError, match="bundle carries unpinned dependencies"):
        check(unpinned, SimpleNamespace(pins=pins))
    with pytest.raises(ProblemStoreError, match="bundle dependencies differ from its pin"):
        check(manifest, None)
    repinned = pins.model_copy(update={"exclude_newer": "2026-10-02T00:00:00Z"})
    with pytest.raises(ProblemStoreError, match="bundle dependencies differ from its pin"):
        check(manifest, SimpleNamespace(pins=repinned))

    with pytest.raises(ProblemStoreError, match=r"problem fixture/adder@\w+: missing"):
        check(manifest, SimpleNamespace(pins=pins))
    _write_locks(store, problem, pins)
    check(manifest, SimpleNamespace(pins=pins))
    store.deps_dir(problem).joinpath(pins.lock_filename(SET_KEY, "arm64")).write_text("tampered")
    with pytest.raises(ProblemStoreError, match="differs from its pin"):
        check(manifest, SimpleNamespace(pins=pins))


# ---- content -----------------------------------------------------------------------------


def test_content_digest_tells_a_symlink_from_a_file_with_the_same_text(tmp_path: Path) -> None:
    linked, copied = tmp_path / "linked", tmp_path / "copied"
    for root in (linked, copied):
        root.mkdir()
        (root / "target.txt").write_text("hello")
    (linked / "alias.txt").symlink_to("target.txt")
    (copied / "alias.txt").write_text("target.txt")
    (linked / "dir").mkdir()
    (linked / "dirlink").symlink_to("dir")

    assert content_digest(linked) != content_digest(copied)
    before = content_digest(linked)
    (linked / "dirlink").unlink()
    (linked / "dirlink").symlink_to("elsewhere")
    assert content_digest(linked) != before


# ---- dependencies ------------------------------------------------------------------------


def _references(tmp_path: Path) -> Path:
    references = tmp_path / "references"
    (references / "checkpoint_1").mkdir(parents=True)
    (references / "checkpoint_1" / "requirements.txt").write_text("pkg\n")
    return references


def test_a_recorded_manifest_without_any_dependency_record_resolves_at_the_import_time(
    store: ProblemStore, tmp_path: Path
) -> None:
    problem = ProblemId.parse(POSITIVE_REF)
    resolver = FakeResolver()
    result = resolve_dependencies(
        store,
        problem,
        _manifest(dependencies=None),
        _references(tmp_path),
        ["checkpoint_1"],
        (),
        resolver,
    )
    assert result.pins is not None
    assert result.pins.exclude_newer == resolver.cutoffs[0]
    assert result.pins.exclude_newer.endswith("Z")
    assert result.pins.exclude_newer != CUTOFF


def test_a_refused_problem_keeps_its_cutoff_and_records_what_cannot_be_provided(
    store: ProblemStore, tmp_path: Path
) -> None:
    problem = ProblemId.parse(POSITIVE_REF)
    unprovided = (Unprovided(requirement="sdist-only", reason="no-wheel"),)
    recorded = _manifest(
        dependencies=None,
        dependency_refusal={"detail": "earlier", "exclude_newer": "2026-09-01T00:00:00Z"},
    )
    resolver = FakeResolver(unprovided)
    result = resolve_dependencies(
        store, problem, recorded, _references(tmp_path), ["checkpoint_1"], (), resolver
    )
    assert result.pins is None
    assert result.locks is None
    assert result.refusal is not None
    assert result.refusal.exclude_newer == "2026-09-01T00:00:00Z"
    assert result.refusal.unprovided == unprovided
    assert set(resolver.cutoffs) == {"2026-09-01T00:00:00Z"}


# ---- importer ----------------------------------------------------------------------------


class _Converted:
    """Stand-ins for the conversion, bundle and rendering steps of one import."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, *, leak: bool, dependencies: ProblemDependencies
    ) -> None:
        self.bundle = SimpleNamespace(
            manifest=SimpleNamespace(grader=SimpleNamespace(test_dependencies=())),
            hidden_file_hashes=dict,
        )

        def convert(_checkout: object, _problem: str, out: Path) -> Path:
            out.mkdir(parents=True)
            return out

        def render(_converted: Path, out: Path, **_kw: object) -> None:
            (out / "tests").mkdir(parents=True)
            if leak:
                (out / "tests" / "test_checkpoint_1.py").write_text("assert True\n")

        def references(_converted: Path, out: Path, _steps: list[str]) -> None:
            out.mkdir(parents=True)

        for name, value in {
            "convert_problem": convert,
            "converted_steps": lambda _converted: ["checkpoint_1"],
            "problem_entry_file": lambda *_a: None,
            "build_bundle": lambda *_a, **_kw: self.bundle,
            "grader_entry_command": lambda _bundle: None,
            "checkpoint_specs": lambda *_a, **_kw: {},
            "render_task": render,
            "copy_references": references,
            "resolve_dependencies": lambda *_a: dependencies,
        }.items():
            monkeypatch.setattr(importer, name, value)

    def materialise(self, store: ProblemStore, staging: Path) -> importer._Materialised:
        checkout: Any = SimpleNamespace(root=staging, pin=SimpleNamespace(commit=COMMIT))
        return importer._materialise(
            store,
            checkout,
            ProblemId.parse(POSITIVE_REF),
            staging,
            None,  # type: ignore[arg-type]
            recorded=None,
            resolver=None,
        )


def test_import_refuses_a_task_that_repeats_hidden_verifier_content(
    store: ProblemStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    converted = _Converted(monkeypatch, leak=True, dependencies=ProblemDependencies())
    with pytest.raises(ProblemStoreError, match=r"hidden verifier content.*test_checkpoint_1\.py"):
        converted.materialise(store, tmp_path)


# ---- errata ------------------------------------------------------------------------------


def test_errata_metadata_in_a_lock_yields_the_pinned_exclusions() -> None:
    pinned = errata_ref_from_metadata(
        {"errata": {"sha256": "ab" * 32, "exclude_from_solved": [MULTIPLIES]}}
    )
    assert pinned is not None
    assert pinned.sha256 == "ab" * 32
    assert pinned.exclude_from_solved == (MULTIPLIES,)
    only_hash = errata_ref_from_metadata({"errata": {"sha256": "cd" * 32}})
    assert only_hash is not None
    assert only_hash.exclude_from_solved == ()
    assert errata_ref_from_metadata({"errata": "not-a-mapping"}) is None


def _flagged_audit() -> dict[str, object]:
    return {
        "audit_version": 1,
        "flagged": [
            {
                "case": MULTIPLIES + "[3]",
                "checkpoint_origin": "checkpoint_2",
                "variants": [NEUTRAL_ERROR_TEXT],
                "assertions": [
                    {
                        "rule": "wording-unstated",
                        "line": 7,
                        "why": "wording not in the spec",
                        "assertion": "assert error == 'bad'",
                        "checkpoint_origin": "checkpoint_2",
                    },
                    "not-an-assertion",
                ],
            },
            {"case": "test_x.py::bare"},
            "not-a-case",
        ],
    }


def test_an_errata_draft_lists_each_flagged_case_but_is_refused_until_reasons_are_written(
    tmp_path: Path,
) -> None:
    digest = "9" * 64
    text = draft_errata(
        "fixture/adder@fefefefefefe", digest, _flagged_audit(), registered="2026-10-02"
    )
    document = yaml.safe_load(text)
    assert document["manifest_sha256"] == digest
    assert "audit v1" in document["basis"]
    first, bare = document["cases"]
    assert first["disposition"] == "exclude-from-solved"
    assert first["rule"] == "wording-unstated"
    assert first["assertion"] == "assert error == 'bad'"
    assert first["evidence"][0] == "audit: wording-unstated: line 7: wording not in the spec"
    assert NEUTRAL_ERROR_TEXT in first["evidence"][1]
    assert bare["rule"] == "unknown"
    assert bare["checkpoint_origin"] is None
    assert len(bare["evidence"]) == 1
    assert len(document["cases"]) == 2

    path = tmp_path / "errata.yaml"
    document["cases"] = [first]
    path.write_text(yaml.safe_dump(document))
    with pytest.raises(ErrataError, match="drafted placeholder reason"):
        load_errata(path, problem="fixture/adder@fefefefefefe", manifest_sha256=digest)

    first["reason"] = "the specification never states the wording"
    path.write_text(yaml.safe_dump(document))
    loaded = load_errata(path, problem="fixture/adder@fefefefefefe", manifest_sha256=digest)
    assert loaded is not None
    assert loaded.ref().exclude_from_solved == (MULTIPLIES + "[3]",)


def test_an_errata_draft_of_an_audit_without_flags_has_no_cases() -> None:
    document = yaml.safe_load(draft_errata("p", "0" * 64, {}, registered="2026-10-02"))
    assert document["cases"] == []
    assert "vNone" in document["basis"]


# ---- audit -------------------------------------------------------------------------------


def _assertion(function: str, kind: AssertionKind, *, expected: str = "bad") -> AuditedAssertion:
    return AuditedAssertion(
        test_file="test_a.py",
        function=function,
        line=3,
        kind=kind,
        source=f"assert {function}",
        expected=expected,
        literal=expected,
        input_derived=False,
        category="validation",
    )


UNDETERMINED = Determination(False, "wording-unstated", "wording is not in the spec")
DETERMINED = Determination(True, "literal-in-spec", "the spec states it")


def _grade(step: str, statuses: dict[str, str], *, infra: str | None = None) -> StepGradeSummary:
    cases = tuple(
        CaseResult(id=case, group=CaseGroup.CORE, checkpoint_origin=step, status=CaseStatus(status))
        for case, status in statuses.items()
    )
    infra_failure = InfraFailure(code=infra) if infra else None
    return StepGradeSummary.from_grade(
        VerifierGrade(step=step, checkpoint=step, cases=cases, infra_failure=infra_failure)
    )


def test_a_step_grade_counts_unskipped_cases_and_keeps_only_its_own_origins() -> None:
    grade = VerifierGrade(
        step="checkpoint_2",
        checkpoint="checkpoint_2",
        cases=(
            CaseResult(
                id="a",
                group=CaseGroup.CORE,
                checkpoint_origin="checkpoint_2",
                status=CaseStatus.PASSED,
            ),
            CaseResult(
                id="b",
                group=CaseGroup.ERROR,
                checkpoint_origin="checkpoint_2",
                status=CaseStatus.FAILED,
            ),
            CaseResult(
                id="c",
                group=CaseGroup.CORE,
                checkpoint_origin="checkpoint_2",
                status=CaseStatus.SKIPPED,
            ),
            CaseResult(
                id="d",
                group=CaseGroup.CORE,
                checkpoint_origin="checkpoint_1",
                status=CaseStatus.PASSED,
            ),
        ),
        infra_failure=InfraFailure(code="sandbox_timeout"),
    )
    summary = StepGradeSummary.from_grade(grade)
    assert (summary.passed, summary.total) == (2, 3)
    assert summary.infra_failure == "sandbox_timeout"
    assert summary.origin_cases == {"a": "passed", "b": "failed", "c": "skipped"}


@pytest.fixture(name="evidence")
def evidence_fixture() -> DynamicEvidence:
    passed, failed = "passed", "failed"
    names = ("err[x]", "fmt", "other", "det", "plain", "mixed", "skip")
    everything = {f"test_a.py::test_{name}": passed for name in names}
    neutral = {**everything}
    for name in ("err[x]", "det", "other", "mixed"):
        neutral[f"test_a.py::test_{name}"] = failed
    neutral["test_a.py::test_skip"] = "skipped"
    return DynamicEvidence(
        grades={
            REFERENCE: {
                "checkpoint_1": _grade("checkpoint_1", everything),
                "checkpoint_2": _grade("checkpoint_2", {"test_b.py::test_x": passed}),
            },
            NEUTRAL_ERROR_TEXT: {
                "checkpoint_1": _grade("checkpoint_1", neutral),
                "checkpoint_2": _grade(
                    "checkpoint_2", {"test_b.py::test_x": failed}, infra="sandbox_crash"
                ),
            },
        }
    )


FINDINGS = [
    (_assertion("test_err", AssertionKind.ERROR_TEXT_EQUALS), "checkpoint_1", UNDETERMINED),
    (_assertion("test_fmt", AssertionKind.OUTPUT_BYTES), "checkpoint_1", UNDETERMINED),
    (_assertion("test_det", AssertionKind.ERROR_TEXT_CONTAINS), "checkpoint_1", DETERMINED),
    (_assertion("test_mixed", AssertionKind.OUTPUT_BYTES), "checkpoint_1", UNDETERMINED),
]


def _imported(root: Path) -> ImportedProblem:
    """A problem record over tiny files: instructions, reference apps and an empty tests dir."""
    for step in ("checkpoint_1", "checkpoint_2"):
        (root / "task" / "steps" / step).mkdir(parents=True)
        (root / "task" / "steps" / step / "instruction.md").write_text("Implement it.\n")
        (root / "references" / step).mkdir(parents=True)
        (root / "references" / step / "app.py").write_text("print(1)\n")
    (root / "tests").mkdir()
    bundle: Any = SimpleNamespace(tests_dir=root / "tests")
    return ImportedProblem(_manifest(), root / "task", bundle, root / "references")


@pytest.fixture(name="problem")
def audit_problem_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(audit, "static_findings", lambda _problem: FINDINGS)
    monkeypatch.setattr(audit, "assertion_coverage", lambda *_args: {"audited": 4, "total": 6})
    return _imported(tmp_path)


def test_the_audit_buckets_each_case_by_what_the_static_and_dynamic_halves_agree_on(
    problem: Any, evidence: DynamicEvidence
) -> None:
    report = audit_report(problem, evidence)

    assert [entry["case"] for entry in report["flagged"]] == ["test_a.py::test_err[x]"]
    assert report["flagged"][0]["variants"] == [NEUTRAL_ERROR_TEXT]
    assert report["flagged"][0]["assertions"][0]["rule"] == "wording-unstated"
    # undetermined, but no variant changed the outcome
    assert report["unconfirmed"] == [
        {"case": "test_a.py::test_fmt", "reason": "no variant changed its outcome"}
    ]
    # a determined assertion explains the sensitivity; no matching assertion does not
    assert report["sensitive_determined"] == [
        {"case": "test_a.py::test_det", "variants": [NEUTRAL_ERROR_TEXT]}
    ]
    assert {entry["case"] for entry in report["sensitive_unexplained"]} == {
        "test_a.py::test_mixed",
        "test_a.py::test_other",
    }
    summary = report["summary"]
    assert summary["status"] == "measured"
    assert summary["flagged"] == 1
    assert summary["flagged_by_checkpoint"] == {"checkpoint_1": 1}
    assert summary["flagged_by_rule"] == {"wording-unstated": 1}
    assert report["assertions"]["by_rule"] == {"literal-in-spec": 1, "wording-unstated": 3}
    assert [entry["function"] for entry in report["static_undetermined"]] == [
        "test_a.py::test_err",
        "test_a.py::test_fmt",
        "test_a.py::test_mixed",
    ]


def test_a_step_with_an_infrastructure_failure_has_no_sensitive_cases(
    problem: Any, evidence: DynamicEvidence
) -> None:
    steps = audit_report(problem, evidence)["steps"]
    assert steps["checkpoint_2"]["variants"][NEUTRAL_ERROR_TEXT] == {
        "passed": 0,
        "total": 1,
        "infra_failure": "sandbox_crash",
        "sensitive": [],
    }
    assert steps["checkpoint_1"]["variants"][NEUTRAL_ERROR_TEXT]["sensitive"] == [
        "test_a.py::test_det",
        "test_a.py::test_err[x]",
        "test_a.py::test_mixed",
        "test_a.py::test_other",
    ]
    assert "sensitive" not in steps["checkpoint_1"][REFERENCE]


def test_an_audit_whose_dynamic_half_did_not_run_is_unmeasured_and_flags_nothing(
    problem: Any,
) -> None:
    report = audit_report(problem, DynamicEvidence({}, not_run="no entry_file"))
    assert report["dynamic"]["status"] == "not_run"
    assert report["dynamic"]["reason"] == "no entry_file"
    assert report["flagged"] == []
    assert report["summary"]["status"] == "unmeasured"
    assert report["summary"]["flagged"] is None
    assert report["steps"] == {}


def test_the_spec_of_a_step_falls_back_to_the_tasks_single_instruction(
    tmp_path: Path,
) -> None:
    loaded = _imported(tmp_path / "bare")
    (tmp_path / "instruction.md").write_text("# One\n\nThe program is compact.\n")
    texts = spec_texts(dataclasses.replace(loaded, task_dir=tmp_path))
    assert set(texts) == {"checkpoint_1", "checkpoint_2"}
    assert texts["checkpoint_1"].names_format() == "compact"
    assert len(texts["checkpoint_2"].paragraphs) == 2 * len(texts["checkpoint_1"].paragraphs)


def _fake_grader(calls: list[tuple[str, str]]) -> Any:
    async def grade(
        bundle: Any,
        step: str,
        snapshot: Path,
        sandbox: Any,
        run: Path,
        *,
        coverage: bool,
        timeout_s: float,
    ) -> Any:
        variant = REFERENCE if snapshot.parent.name == "references" else snapshot.parent.name
        calls.append((variant, step))
        status = CaseStatus.FAILED if variant == NEUTRAL_ERROR_TEXT else CaseStatus.PASSED
        case = CaseResult(
            id=f"test_{step}.py::test_it",
            group=CaseGroup.CORE,
            checkpoint_origin=step,
            status=status,
        )
        return SimpleNamespace(grade=VerifierGrade(step=step, checkpoint=step, cases=(case,)))

    return grade


def test_variants_are_graded_per_step_and_only_the_applicable_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = _imported(tmp_path / "imported")
    with_entry = dataclasses.replace(
        loaded, manifest=loaded.manifest.model_copy(update={"entry_file": "app.py"})
    )
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(audit, "grade_step", _fake_grader(calls))

    evidence = asyncio.run(
        grade_audit_variants(with_entry, object(), concurrency=2, work_root=tmp_path)  # type: ignore[arg-type]
    )
    assert evidence.not_run is None
    assert evidence.grades[REFERENCE]["checkpoint_1"].passed == 1
    assert evidence.grades[NEUTRAL_ERROR_TEXT]["checkpoint_2"].passed == 0
    assert not list(tmp_path.glob("raidar-problem-audit-*"))

    # A specification that names the output format makes the reformatting variant meaningless.
    steps = tmp_path / "task" / "steps"
    for step in ("checkpoint_1", "checkpoint_2"):
        (steps / step).mkdir(parents=True)
        (steps / step / "instruction.md").write_text("Print compact output.\n")
    named = dataclasses.replace(with_entry, task_dir=tmp_path / "task")
    calls.clear()
    narrowed = asyncio.run(
        grade_audit_variants(named, object(), concurrency=1, work_root=tmp_path)  # type: ignore[arg-type]
    )
    assert set(narrowed.grades) == {REFERENCE, NEUTRAL_ERROR_TEXT}


def test_variants_are_not_graded_for_a_problem_without_an_entry_file(tmp_path: Path) -> None:
    loaded = _imported(tmp_path)
    evidence = asyncio.run(grade_audit_variants(loaded, object(), concurrency=1))  # type: ignore[arg-type]
    assert evidence.grades == {}
    assert evidence.not_run == "the problem records no entry_file to wrap"
