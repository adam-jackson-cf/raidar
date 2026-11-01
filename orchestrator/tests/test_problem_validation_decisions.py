"""Problem validation decisions: checks, report, committed-report policy and engine conversions.

Everything is in memory or in tiny temporary trees; graders, Harbor trials, the native runner and
the shim's reference process are fakes.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from raidar.design.problem import direct, harbor_engine, validator, variant_shim
from raidar.design.problem.audit import DynamicEvidence
from raidar.design.problem.models import ProblemId, ProblemMode
from raidar.design.problem.validation import (
    DEPENDENCY_CHECK,
    NEGATIVE,
    POSITIVE,
    Check,
    CheckStatus,
    Grade,
    ValidationRecordError,
    ValidationReport,
    ValidationRuns,
    Verdict,
    check_dependencies,
    check_determinism,
    check_parity,
    policy_preview,
    read_validation_record,
    solvable_digest,
)
from raidar.grading.models import (
    CaseGroup,
    CaseResult,
    CaseStatus,
    InfraFailure,
    VerifierGrade,
)
from raidar.grading.records import step_record_dir

PID = ProblemId.parse("scb/adder@abc123456789")
PASSED, FAILED = "passed", "failed"


def grade(step: str, outcomes: dict[str, str], infra: str | None = None) -> Grade:
    counted = [status for status in outcomes.values() if status != "skipped"]
    groups = {"core": (counted.count(PASSED), len(counted))} if counted else {}
    return Grade("oracle", step, groups, outcomes, infra)


def run(*per_repeat: dict[str, Grade]) -> list[dict[str, Grade]]:
    return list(per_repeat)


# ---- Grade and checks ----------------------------------------------------------------------


def test_grade_from_verifier_reduces_cases_groups_and_infra() -> None:
    cases = (
        CaseResult(
            id="t::a", group=CaseGroup.CORE, checkpoint_origin="s", status=CaseStatus.PASSED
        ),
        CaseResult(
            id="t::b", group=CaseGroup.ERROR, checkpoint_origin="s", status=CaseStatus.FAILED
        ),
        CaseResult(
            id="t::c", group=CaseGroup.CORE, checkpoint_origin="s", status=CaseStatus.SKIPPED
        ),
    )
    ok = Grade.from_verifier(VerifierGrade(step="s", checkpoint="s", cases=cases))
    assert (ok.passed, ok.total, ok.infra_failure) == (1, 2, None)
    assert ok.compact()["groups"] == {
        "core": [1, 1],
        "error": [0, 1],
        "functionality": [0, 0],
        "regression": [0, 0],
    }
    assert not ok.all_pass

    crashed = Grade.from_verifier(
        VerifierGrade(
            step="s", checkpoint="s", cases=(), infra_failure=InfraFailure(code="sandbox_timeout")
        ),
        source="native",
    )
    assert crashed.infra_failure == "sandbox_timeout"
    assert crashed.source == "native"
    assert not crashed.has_verdict


def test_negative_repeats_differing_only_in_varying_cases_are_quarantined_not_failed() -> None:
    runs = ValidationRuns(
        oracle={
            POSITIVE: run({"s": grade("s", {"a": PASSED})}),
            NEGATIVE: run(
                {"s": grade("s", {"a": PASSED, "b": FAILED})},
                {"s": grade("s", {"a": FAILED, "b": FAILED})},
            ),
        }
    )
    assert check_determinism("repeat_identical_negative", runs).status is CheckStatus.PASS


def test_negative_repeats_with_different_group_counts_fail_the_determinism_check() -> None:
    first = Grade("oracle", "s", {"core": (0, 1)}, {"a": FAILED})
    second = Grade("oracle", "s", {"core": (0, 2)}, {"a": FAILED})
    runs = ValidationRuns(
        oracle={POSITIVE: run({"s": first}), NEGATIVE: run({"s": first}, {"s": second})}
    )
    check = check_determinism("repeat_identical_negative", runs)
    assert check.status is CheckStatus.FAIL
    assert check.detail == ("repeat 1 vs 0 s: group counts differ",)


def test_parity_reports_infrastructure_for_ungraded_oracle_and_native() -> None:
    oracle = {"s1": grade("s1", {}, "sandbox_timeout"), "s2": grade("s2", {"a": PASSED})}
    check = check_parity("native_parity_positive", oracle, {"s2": grade("s2", {}, "boom")})
    assert check.status is CheckStatus.INFRA
    assert check.detail == (
        "s1 oracle: no verdict (sandbox_timeout)",
        "s2 native: no verdict (boom)",
    )
    assert check_parity("p", oracle, None).status is CheckStatus.NOT_RUN


def test_dependency_refusal_is_a_failed_check_naming_each_unprovided_requirement() -> None:
    refusal = SimpleNamespace(
        detail="no wheel",
        unprovided=[SimpleNamespace(requirement="numpy==9", reason="not found")],
    )
    check = check_dependencies(refusal)  # type: ignore[arg-type]
    assert check.name == DEPENDENCY_CHECK
    assert check.status is CheckStatus.FAIL
    assert check.detail[-1] == "numpy==9: not found"


def test_policy_preview_makes_ungradable_and_empty_references_ineligible() -> None:
    empty = {"s": Grade("oracle", "s", {}, {"t::a": PASSED})}
    preview = policy_preview(empty, 1)
    assert preview["reference_correctness"] is None
    assert preview["eligible"] is False

    ungraded = {"s": grade("s", {}, "sandbox_timeout")}
    assert policy_preview(ungraded, 1)["reference_correctness"] is None


def _report_runs() -> ValidationRuns:
    return ValidationRuns(
        oracle={
            POSITIVE: run(
                {
                    "s1": grade("s1", {"a": PASSED, "b": PASSED}),
                    "s2": grade("s2", {"c": PASSED, "d": PASSED}),
                },
                {
                    "s1": grade("s1", {"a": PASSED, "b": FAILED}),
                    "s2": grade("s2", {"c": PASSED, "d": PASSED}),
                },
            ),
            NEGATIVE: run(
                {"s1": grade("s1", {"a": FAILED}), "s2": grade("s2", {"c": FAILED})},
                {"s1": grade("s1", {}, "sandbox_timeout"), "s2": grade("s2", {"c": FAILED})},
            ),
        },
        native={POSITIVE: {"s1": grade("s1", {"a": PASSED})}},
        references={POSITIVE: {"s1": "r1", "s2": "r2"}},
    )


def test_report_json_records_grades_solvable_sets_observations_and_verdict() -> None:
    runs = _report_runs()
    report = ValidationReport(
        problem=str(PID),
        manifest_sha256="m",
        mode="multi_checkpoint",
        steps=("s1", "s2"),
        engine="direct",
        repeats=2,
        checks=[Check("native_parity_negative", CheckStatus.NOT_RUN)],
        runs=runs,
        spec_determinacy={"flagged": []},
    )
    data = report.to_json()
    assert report.verdict is Verdict.VALIDATED_PARTIAL
    assert data["not_run"] == ["native_parity_negative"]
    assert data["spec_determinacy"] == {"flagged": []}
    assert data["grades"]["native"] == {  # type: ignore[index]
        "positive": {"s1": grade("s1", {"a": PASSED}).compact()}
    }
    assert data["solvable"]["s1"]["solvable"] == ["a"]  # type: ignore[index]
    assert data["solvable"]["s1"]["nondeterministic"] == ["b"]  # type: ignore[index]
    assert data["reference_digests"] == {POSITIVE: {"s1": "r1", "s2": "r2"}}
    observed = data["reference_observations"]
    assert observed[POSITIVE]["s1"]["observation"].startswith("variation observed")  # type: ignore[index]
    assert observed[POSITIVE]["s2"]["observation"].startswith("no variation observed")  # type: ignore[index]
    assert observed[NEGATIVE]["s1"]["status"] == "unmeasured"  # type: ignore[index]
    assert observed[NEGATIVE]["s1"]["reference_sha256"] is None  # type: ignore[index]
    assert data["headline_policy"]["checkpoints"] == 2  # type: ignore[index]


def test_report_without_runs_omits_grade_sections() -> None:
    report = ValidationReport(
        problem=str(PID),
        manifest_sha256="m",
        mode="one_shot",
        steps=("s",),
        engine="direct",
        repeats=1,
        checks=[check_dependencies(SimpleNamespace(detail="d", unprovided=[]))],  # type: ignore[arg-type]
        runs=None,
    )
    data = report.to_json()
    assert data["verdict"] == "excluded"
    assert "grades" not in data
    assert "spec_determinacy" not in data


# ---- committed records ---------------------------------------------------------------------


def _record_json(**over: Any) -> dict[str, Any]:
    def step(cases: list[str], nondeterministic: int) -> dict[str, Any]:
        return {
            "verdict": "validated",
            "exclusion": None,
            "solvable": cases,
            "digest": solvable_digest(cases),
            "nondeterministic_count": nondeterministic,
        }

    base: dict[str, Any] = {
        "verdict": "validated",
        "manifest_sha256": "m" * 64,
        "not_run": ["native_parity_positive"],
        "solvable": {"s1": step(["b", "a"], 2), "s2": step([], 0)},
    }
    return base | over


def test_record_exposes_solvable_sets_and_lock_metadata(tmp_path: Path) -> None:
    path = tmp_path / "v.validation.json"
    path.write_text(json.dumps(_record_json()))
    record = read_validation_record(path)
    assert record.usable
    assert record.solvable_cases("s1") == ("a", "b")
    assert record.solvable_cases("s2") is None
    assert record.solvable_cases("missing") is None
    assert (record.solvable_size("s1"), record.solvable_size("missing")) == (2, 0)
    assert (record.nondeterministic_count("s1"), record.nondeterministic_count("missing")) == (2, 0)
    lock = record.solvable_lock()
    assert lock["steps"] == {"s1": ["a", "b"], "s2": []}
    assert record.lock_metadata()["grader"] is None


def test_record_with_a_tampered_digest_or_missing_file_is_refused(tmp_path: Path) -> None:
    tampered = _record_json()
    tampered["solvable"]["s1"]["digest"] = "0" * 64
    path = tmp_path / "t.json"
    path.write_text(json.dumps(tampered))
    with pytest.raises(ValidationRecordError, match="does not match its cases"):
        read_validation_record(path)
    with pytest.raises(ValidationRecordError, match="no validation report"):
        read_validation_record(tmp_path / "absent.json")
    path.write_text("not json")
    with pytest.raises(ValidationRecordError, match="unreadable"):
        read_validation_record(path)


# ---- validator: store, problem and engine fakes --------------------------------------------


class FakeManifest:
    def __init__(self, mode: ProblemMode = ProblemMode.MULTI_CHECKPOINT, refusal: Any = None):
        self.problem_id = PID
        self.mode = mode
        self.steps = [SimpleNamespace(name="checkpoint_1"), SimpleNamespace(name="checkpoint_2")]
        self.pack = SimpleNamespace(image="pack:1")
        self.dependency_refusal = refusal
        self.source = SimpleNamespace(problem="adder")

    def digest(self) -> str:
        return "d" * 64


class FakeProblem:
    def __init__(self, root: Path, manifest: FakeManifest) -> None:
        self.manifest = manifest
        self.bundle = SimpleNamespace(root=root / "bundle")
        self.task_dir = root / "task"
        self.task_dir.mkdir(parents=True)
        (self.task_dir / "task.toml").write_text("x")
        self._refs = {}
        for step in ("checkpoint_1", "checkpoint_2"):
            ref = root / "refs" / step
            ref.mkdir(parents=True)
            (ref / "main.py").write_text(step)
            self._refs[step] = ref

    def reference(self, step: str) -> Path:
        return self._refs[step]


class FakeStore:
    def __init__(self, root: Path, manifest: FakeManifest | None = None) -> None:
        self.root = root
        self.manifest = manifest or FakeManifest()
        self.problem = FakeProblem(root, self.manifest)

    def load(self, _: ProblemId) -> FakeProblem:
        return self.problem

    def load_manifest(self, _: ProblemId) -> FakeManifest:
        return self.manifest

    def validation_path(self, _: ProblemId) -> Path:
        return self.root / "committed.validation.json"

    def validation_attempt_path(self, _: ProblemId) -> Path:
        return self.root / "attempt.json"


def _oracle(positive: bool = True) -> dict[str, list[dict[str, Grade]]]:
    good = {
        "checkpoint_1": grade("checkpoint_1", {"a": PASSED}),
        "checkpoint_2": grade("checkpoint_2", {"b": PASSED}),
    }
    bad = {
        "checkpoint_1": grade("checkpoint_1", {"a": FAILED}),
        "checkpoint_2": grade("checkpoint_2", {"b": FAILED if positive else PASSED}),
    }
    return {POSITIVE: [good, good], NEGATIVE: [bad, bad]}


def _options(**over: Any) -> validator.ValidateOptions:
    defaults: dict[str, Any] = {"docker_context": "ctx", "repeats": 2, "audit": False}
    return validator.ValidateOptions(**(defaults | over))


def _validate(store: FakeStore, options: validator.ValidateOptions, **kw: Any) -> ValidationReport:
    return asyncio.run(validator.validate_problem(store, PID, options, **kw))  # type: ignore[arg-type]


def test_unknown_engine_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="engine must be one of"):
        _validate(FakeStore(tmp_path), _options(engine="vm"))


def test_unresolvable_dependencies_exclude_without_grading(tmp_path: Path) -> None:
    refusal = SimpleNamespace(detail="no wheel", exclude_newer="2026-01-01", unprovided=[])
    store = FakeStore(tmp_path, FakeManifest(refusal=refusal))
    report = _validate(store, _options())
    assert report.verdict is Verdict.EXCLUDED
    assert report.runs is None
    assert report.environment == {"engine": "direct", "dependency_exclude_newer": "2026-01-01"}
    assert report.steps == ("checkpoint_1", "checkpoint_2")


def test_direct_validation_prepares_a_sandbox_and_runs_the_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = object()

    async def prepare(problem: Any, context: str) -> tuple[object, dict[str, object]]:
        return sandbox, {"grader_image": "img"}

    async def grade_references(problem: Any, box: object, **kw: Any) -> dict[str, Any]:
        return _oracle()

    async def audit_variants(problem: Any, box: object, **kw: Any) -> Any:
        return SimpleNamespace(grades={}, not_run={"x": "why"}, environment={"dyn": 1})

    def audit_report(problem: Any, evidence: DynamicEvidence) -> dict[str, object]:
        return {"environment": dict(evidence.environment or {}), "not_run": dict(evidence.not_run)}

    monkeypatch.setattr(direct, "prepare_grader_sandbox", prepare)
    monkeypatch.setattr(direct, "grade_references", grade_references)
    monkeypatch.setattr(validator, "grade_audit_variants", audit_variants)
    monkeypatch.setattr(validator, "audit_report", audit_report)

    report = _validate(FakeStore(tmp_path), _options(audit=True))
    assert report.verdict is Verdict.VALIDATED_PARTIAL  # native parity not run
    assert report.environment["grader_image"] == "img"
    assert report.spec_determinacy == {"environment": {"dyn": 1}, "not_run": {"x": "why"}}
    assert report.runs is not None
    assert set(report.runs.references[POSITIVE]) == {"checkpoint_1", "checkpoint_2"}


def test_given_sandbox_is_used_without_preparing_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden(*_: Any, **__: Any) -> Any:
        raise AssertionError("no sandbox may be prepared")

    async def grade_references(problem: Any, box: object, **kw: Any) -> dict[str, Any]:
        return _oracle()

    monkeypatch.setattr(direct, "prepare_grader_sandbox", forbidden)
    monkeypatch.setattr(direct, "grade_references", grade_references)
    report = _validate(FakeStore(tmp_path), _options(), sandbox=object())  # type: ignore[arg-type]
    assert report.spec_determinacy is None
    assert report.verdict is Verdict.VALIDATED_PARTIAL
    assert report.runs is not None


def test_audit_alone_prepares_a_sandbox_when_none_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def prepare(problem: Any, context: str) -> tuple[object, dict[str, object]]:
        return "box", {"grader_image": "img"}

    async def audit_variants(problem: Any, box: object, **kw: Any) -> Any:
        return SimpleNamespace(grades={}, not_run={}, environment=None)

    monkeypatch.setattr(direct, "prepare_grader_sandbox", prepare)
    monkeypatch.setattr(validator, "grade_audit_variants", audit_variants)
    monkeypatch.setattr(validator, "audit_report", lambda p, e: dict(e.environment or {}))
    result = asyncio.run(
        validator.audit_problem(
            FakeStore(tmp_path),  # type: ignore[arg-type]
            PID,
            _options(),
            work_root=tmp_path,
        )
    )
    assert result == {"grader_image": "img"}


def test_harbor_validation_pins_the_grader_identity_it_prepared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pins: list[dict[str, str]] = []
    negative_paths: set[Path] = set()

    async def prepare(problem: Any, context: str) -> tuple[object, dict[str, object]]:
        return object(), {
            "grader_image": "img",
            "grader_environment_id": "sha256:" + "a" * 64,
            "grader_implementation_sha256": "b" * 64,
        }

    async def run_variant(problem: Any, variant: str, payloads: Any, work: Path, **kw: Any):
        pins.append(kw["grader"])
        if variant == NEGATIVE:
            negative_paths.update(payloads.values())
        return _oracle()[variant]

    monkeypatch.setattr(direct, "prepare_grader_sandbox", prepare)
    monkeypatch.setattr(harbor_engine, "run_variant", run_variant)
    store = FakeStore(tmp_path, FakeManifest(ProblemMode.ONE_SHOT))
    report = _validate(store, _options(engine="harbor"))
    assert pins[0] == {
        "image": "img",
        "environment_id": "sha256:" + "a" * 64,
        "implementation_sha256": "b" * 64,
    }
    # One-shot: the negative variant grades the empty starter at every step.
    assert len(negative_paths) == 1
    assert report.engine == "harbor"
    assert report.environment["grader_image"] == "img"


def test_native_runner_grades_the_same_snapshots_and_records_its_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Checkout:
        root = tmp_path / "checkout"

        def verify_clean(self, path: str) -> None:
            return None

    class Runner:
        def __init__(self, root: Path, docker_context: str) -> None:
            assert docker_context == "ctx"

        def revision(self) -> dict[str, object]:
            return {"commit": "c0ffee", "dirty": False}

        def grade(self, catalog: Path, problem: str, snapshot: Path, index: int, out: Path):
            ok = index == 1
            return grade(f"checkpoint_{index}", {"a" if ok else "b": PASSED})

    async def prepare(problem: Any, context: str) -> tuple[object, dict[str, object]]:
        return object(), {}

    async def grade_references(problem: Any, box: object, **kw: Any) -> dict[str, Any]:
        return _oracle()

    monkeypatch.setattr(direct, "prepare_grader_sandbox", prepare)
    monkeypatch.setattr(direct, "grade_references", grade_references)
    monkeypatch.setattr(validator, "resolve_checkout", lambda store, pid, given: Checkout())
    monkeypatch.setattr(validator, "stage_catalog", lambda src, dest: dest)
    monkeypatch.setattr(validator, "NativeRunner", Runner)

    report = _validate(
        FakeStore(tmp_path), _options(native_runner=tmp_path / "fork", source_checkout=tmp_path)
    )
    assert report.environment["native_runner"] == {"commit": "c0ffee", "dirty": False}
    by_name = {check.name: check for check in report.checks}
    # Native agrees with the passing positive oracle but not with the failing negative one.
    assert by_name["native_parity_positive"].status is CheckStatus.PASS
    assert by_name["native_parity_negative"].status is CheckStatus.FAIL
    assert report.verdict is Verdict.EXCLUDED


# ---- committed report policy ---------------------------------------------------------------


def _report(*checks: Check, manifest: str = "d" * 64) -> ValidationReport:
    return ValidationReport(
        problem=str(PID),
        manifest_sha256=manifest,
        mode="multi_checkpoint",
        steps=("s",),
        engine="direct",
        repeats=1,
        checks=list(checks),
        runs=None,
    )


def test_default_report_path_never_weakens_the_committed_report(tmp_path: Path) -> None:
    store = FakeStore(tmp_path)
    committed, attempt = store.validation_path(PID), store.validation_attempt_path(PID)
    passing = Check("repeat_identical_negative", CheckStatus.PASS)
    skipped = Check("native_parity_positive", CheckStatus.NOT_RUN)

    infra = _report(Check("x", CheckStatus.INFRA))
    assert validator.default_report_path(store, infra) == attempt  # type: ignore[arg-type]
    excluded = _report(Check("x", CheckStatus.FAIL))
    assert validator.default_report_path(store, excluded) == committed  # type: ignore[arg-type]
    # No committed record yet: the first report is committed.
    assert validator.default_report_path(store, _report(passing, skipped)) == committed  # type: ignore[arg-type]

    committed.write_text(json.dumps({"verdict": "validated", "manifest_sha256": "d" * 64}))
    weaker = _report(passing, skipped)
    assert validator.default_report_path(store, weaker) == attempt  # type: ignore[arg-type]
    other_manifest = _report(passing, skipped, manifest="e" * 64)
    assert validator.default_report_path(store, other_manifest) == committed  # type: ignore[arg-type]
    assert validator.default_report_path(store, _report(passing)) == committed  # type: ignore[arg-type]


def test_write_report_creates_parents_and_readable_json(tmp_path: Path) -> None:
    report = _report(Check("c", CheckStatus.PASS))
    path = validator.write_report(report, tmp_path / "deep" / "dir" / "r.json")
    data = json.loads(path.read_text())
    assert data["verdict"] == "validated"


def test_merge_audit_updates_only_the_report_for_the_current_manifest(tmp_path: Path) -> None:
    store = FakeStore(tmp_path)
    path = store.validation_path(PID)
    path.write_text(json.dumps({"verdict": "validated", "manifest_sha256": "d" * 64, "k": 1}))
    assert validator.merge_audit(store, PID, {"flagged": []}) == path  # type: ignore[arg-type]
    merged = json.loads(path.read_text())
    assert merged == {
        "verdict": "validated",
        "manifest_sha256": "d" * 64,
        "k": 1,
        "spec_determinacy": {"flagged": []},
    }

    path.write_text(json.dumps({"verdict": "validated", "manifest_sha256": "e" * 64}))
    with pytest.raises(ValidationRecordError, match="run a full validation instead"):
        validator.merge_audit(store, PID, {})  # type: ignore[arg-type]
    assert "spec_determinacy" not in json.loads(path.read_text())


# ---- direct engine -------------------------------------------------------------------------


def test_variant_snapshots_use_previous_reference_or_empty_starter(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    multi = FakeProblem(tmp_path / "m", FakeManifest())
    one_shot = FakeProblem(tmp_path / "o", FakeManifest(ProblemMode.ONE_SHOT))
    snaps = direct.variant_snapshots(multi, empty)  # type: ignore[arg-type]
    assert snaps[NEGATIVE] == {
        "checkpoint_1": empty,
        "checkpoint_2": multi.reference("checkpoint_1"),
    }
    assert direct.variant_snapshots(one_shot, empty)[NEGATIVE] == {  # type: ignore[arg-type]
        "checkpoint_1": empty,
        "checkpoint_2": empty,
    }


def test_grade_references_groups_grades_by_variant_and_repeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    problem = FakeProblem(tmp_path, FakeManifest())

    async def grade_step(
        bundle: Any, step: str, snapshot: Path, sandbox: Any, out: Path, **kw: Any
    ):
        passed = snapshot == problem.reference(step)
        status = CaseStatus.PASSED if passed else CaseStatus.FAILED
        case = CaseResult(
            id=f"{step}::t", group=CaseGroup.CORE, checkpoint_origin=step, status=status
        )
        return SimpleNamespace(grade=VerifierGrade(step=step, checkpoint=step, cases=(case,)))

    monkeypatch.setattr(direct, "grade_step", grade_step)
    runs = asyncio.run(
        direct.grade_references(problem, object(), repeats=2, concurrency=2)  # type: ignore[arg-type]
    )
    assert all(len(runs[v]) == 2 for v in (POSITIVE, NEGATIVE))
    assert runs[POSITIVE][1]["checkpoint_2"].all_pass
    assert not runs[NEGATIVE][0]["checkpoint_2"].all_pass


def test_grade_references_refuses_zero_repeats(tmp_path: Path) -> None:
    problem = FakeProblem(tmp_path, FakeManifest())
    with pytest.raises(ValueError, match="repeats must be >= 1"):
        asyncio.run(direct.grade_references(problem, object(), repeats=0))  # type: ignore[arg-type]


def test_prepare_grader_sandbox_records_the_image_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ensure(bundle: Any, *, docker_context: str) -> Any:
        return SimpleNamespace(tag=f"grader:{docker_context}")

    class Sandbox:
        def __init__(self, image: str, docker_context: str) -> None:
            self.image = image

        async def environment_id(self) -> str:
            return "sha256:env"

    monkeypatch.setattr(direct, "ensure_grader_image", ensure)
    monkeypatch.setattr(direct, "DockerCopySandbox", Sandbox)
    monkeypatch.setattr(direct, "grading_execution_digest", lambda: "digest")
    box, details = asyncio.run(
        direct.prepare_grader_sandbox(FakeProblem(tmp_path, FakeManifest()), "ctx")  # type: ignore[arg-type]
    )
    assert box.image == "grader:ctx"  # type: ignore[attr-defined]
    assert details == {
        "grader_image": "grader:ctx",
        "grader_environment_id": "sha256:env",
        "grader_implementation_sha256": "digest",
        "docker_context": "ctx",
    }


# ---- harbor engine -------------------------------------------------------------------------


def test_variant_task_installs_each_payload_as_an_executable_solution(tmp_path: Path) -> None:
    problem = FakeProblem(tmp_path, FakeManifest())
    payloads = {"checkpoint_1": problem.reference("checkpoint_1")}
    dest = tmp_path / "out"
    (dest).mkdir()
    (dest / "stale").write_text("old")
    task = harbor_engine.variant_task(problem, payloads, dest)  # type: ignore[arg-type]
    solution = task / "steps" / "checkpoint_1" / "solution"
    assert not (task / "stale").exists()
    assert (solution / "solve.sh").stat().st_mode & 0o111 == 0o111


def _write_record(trial_dir: Path, step: str) -> VerifierGrade:
    record_dir = step_record_dir(trial_dir, step)
    record_dir.mkdir(parents=True)
    (record_dir / "record.json").write_text("{}")
    case = CaseResult(
        id="t::a", group=CaseGroup.CORE, checkpoint_origin=step, status=CaseStatus.PASSED
    )
    return VerifierGrade(step=step, checkpoint=step, cases=(case,))


def test_read_grades_marks_steps_without_a_record_as_infrastructure_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded = _write_record(tmp_path, "s1")
    monkeypatch.setattr(
        harbor_engine, "load_step_record", lambda d: SimpleNamespace(grade=recorded)
    )
    grades = harbor_engine.read_grades(tmp_path, ["s1", "s2"], "EnvironmentStartTimeoutError")
    assert grades["s1"].all_pass
    assert grades["s2"].infra_failure == "no_verifier_record:EnvironmentStartTimeoutError"
    assert harbor_engine.read_grades(tmp_path, ["s2"])["s2"].infra_failure == "no_verifier_record"


def test_run_variant_runs_each_repeat_and_reads_its_grades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    problem = FakeProblem(tmp_path, FakeManifest())
    payloads = {"checkpoint_1": problem.reference("checkpoint_1")}
    recorded = _write_record(tmp_path / "trial-0", "checkpoint_1")

    async def run_trial(config: Any, bundle_root: Path) -> tuple[Path, str | None]:
        index = int(config.trial_name.rsplit("-", 1)[1])
        return tmp_path / f"trial-{index}", (None if index == 0 else "AgentTimeoutError")

    monkeypatch.setattr(harbor_engine, "_run_trial", run_trial)
    monkeypatch.setattr(
        harbor_engine, "load_step_record", lambda d: SimpleNamespace(grade=recorded)
    )
    result = asyncio.run(
        harbor_engine.run_variant(
            problem,  # type: ignore[arg-type]
            POSITIVE,
            payloads,
            tmp_path / "work",
            repeats=2,
            docker_context="ctx",
            concurrency=1,
            grader={"image": "img"},
        )
    )
    assert result[0]["checkpoint_1"].all_pass
    assert result[1]["checkpoint_1"].infra_failure == "no_verifier_record:AgentTimeoutError"


def test_run_variant_refuses_zero_concurrency(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="concurrency must be >= 1"):
        asyncio.run(
            harbor_engine.run_variant(
                FakeProblem(tmp_path, FakeManifest()),  # type: ignore[arg-type]
                POSITIVE,
                {},
                tmp_path,
                repeats=1,
                docker_context="ctx",
                concurrency=0,
                grader={},
            )
        )


def test_trial_outcome_reports_the_trial_dir_and_exception_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeTrial:
        def __init__(self, error: str | None) -> None:
            self.paths = SimpleNamespace(trial_dir=tmp_path / "t")
            self.error = error

        async def run(self) -> Any:
            info = SimpleNamespace(exception_type=self.error) if self.error else None
            return SimpleNamespace(exception_info=info)

    async def create(config: Any) -> FakeTrial:
        return FakeTrial(config)

    monkeypatch.setattr(harbor_engine.Trial, "create", create)
    monkeypatch.setattr(harbor_engine, "install_leak_guard", lambda trial, root: None)
    assert asyncio.run(harbor_engine._run_trial(None, tmp_path)) == (tmp_path / "t", None)  # type: ignore[arg-type]
    assert asyncio.run(harbor_engine._run_trial("Boom", tmp_path)) == (tmp_path / "t", "Boom")  # type: ignore[arg-type]


# ---- variant shim --------------------------------------------------------------------------


def test_neutralising_applies_inside_json_lists() -> None:
    text = json.dumps([{"error": "e", "detail": "secret"}, 3])
    assert json.loads(variant_shim.transform(text, "neutral_error_text")) == [
        {"error": "e", "detail": variant_shim.NEUTRAL_TEXT},
        3,
    ]


def test_shim_main_runs_the_reference_and_rewrites_only_its_stdout(
    monkeypatch: pytest.MonkeyPatch, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    def fake_run(command: list[str], **kw: Any) -> subprocess.CompletedProcess[bytes]:
        body = json.dumps({"error": "bad", "detail": "secret"}).encode()
        return subprocess.CompletedProcess(command, 3, stdout=body)

    monkeypatch.setattr(variant_shim.subprocess, "run", fake_run)
    code = variant_shim.main("ref.py", "neutral_error_text", ["--x", "1"])
    assert code == 3
    out = capsysbinary.readouterr().out
    assert json.loads(out) == {"error": "bad", "detail": variant_shim.NEUTRAL_TEXT}


def test_shim_main_rejects_an_unknown_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        variant_shim.subprocess,
        "run",
        lambda command, **kw: subprocess.CompletedProcess(command, 0, stdout=b"{}"),
    )
    with pytest.raises(ValueError, match="unknown variant"):
        variant_shim.main("ref.py", "mystery", [])
