"""Problem validation (B2) and one-shot support (B3) over the fixture store."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from harbor.models.task.task import Task

from raidar.cli import main
from raidar.design.problem import direct, harbor_engine, validator
from raidar.design.problem.models import ProblemId, SourcePin
from raidar.design.problem.native import grade_from_evaluation, stage_catalog
from raidar.design.problem.rewrite import VERIFIER_IMPORT_PATH
from raidar.design.problem.source import PinnedCheckout, SourceError
from raidar.design.problem.store import ProblemStore
from raidar.design.problem.validation import (
    NEGATIVE,
    POSITIVE,
    Check,
    CheckStatus,
    Grade,
    ValidationRecordError,
    ValidationRuns,
    Verdict,
    check_determinism,
    check_parity,
    check_positive_repeats,
    check_reference_reading,
    check_solvable,
    policy_preview,
    read_validation_record,
    reference_tree_digest,
    run_checks,
    solvable_digest,
    solvable_sets,
    verdict,
)
from raidar.design.problem.validator import ValidateOptions, validate_problem, write_report
from raidar.grading.sandbox import SandboxResult, SandboxRun
from tests.problems_support import (
    NEGATIVE_REF,
    ONESHOT_REF,
    POSITIVE_REF,
    build_fake_scb_repo,
    build_fixture_store,
)
from tests.verifier_support import LocalProcessSandbox


class DownSandbox(LocalProcessSandbox):
    """A grader whose container runtime is unreachable."""

    async def run(self, run: SandboxRun) -> SandboxResult:
        return SandboxResult(exit_code=125, timed_out=False, output="Cannot connect to Docker")


@pytest.fixture
def store(tmp_path: Path) -> ProblemStore:
    return build_fixture_store(tmp_path / "problems")


def _grade(step: str, outcomes: dict[str, str], infra: str | None = None) -> Grade:
    passed = sum(1 for status in outcomes.values() if status == "passed")
    total = sum(1 for status in outcomes.values() if status != "skipped")
    return Grade("oracle", step, {"core": (passed, total)}, outcomes, infra)


async def _validate(
    store: ProblemStore, ref: str, tmp_path: Path, repeats: int = 2
) -> validator.ValidationReport:
    return await validate_problem(
        store,
        ProblemId.parse(ref),
        ValidateOptions(
            docker_context="test-ctx", repeats=repeats, concurrency=1
        ),  # one local sandbox root
        sandbox=LocalProcessSandbox(tmp_path / "sandbox"),
        work_root=tmp_path,
    )


async def test_reference_trial_validates_with_oracle_grading(
    store: ProblemStore, tmp_path: Path
) -> None:
    report = await _validate(store, POSITIVE_REF, tmp_path)
    statuses = {check.name: check.status for check in report.checks}
    assert statuses == {
        "solvable_set": CheckStatus.PASS,
        "reference_k-1_fails_k": CheckStatus.PASS,
        "repeat_identical_positive": CheckStatus.PASS,
        "repeat_identical_negative": CheckStatus.PASS,
        "native_parity_positive": CheckStatus.NOT_RUN,
        "native_parity_negative": CheckStatus.NOT_RUN,
    }
    assert report.verdict is Verdict.VALIDATED_PARTIAL
    assert report.to_json()["not_run"] == ["native_parity_positive", "native_parity_negative"]
    positive = report.runs.oracle[POSITIVE][0]
    # Checkpoint 2 re-grades checkpoint 1's tests as regression; the skip leaves the denominator.
    assert positive["checkpoint_2"].groups["regression"] == (3, 3)
    assert positive["checkpoint_2"].total == 4
    assert not report.runs.oracle[NEGATIVE][0]["checkpoint_1"].all_pass  # empty starter


async def test_negative_fixture_is_excluded_and_its_report_is_written(
    store: ProblemStore, tmp_path: Path
) -> None:
    report = await _validate(store, NEGATIVE_REF, tmp_path)
    failed = [check for check in report.checks if check.status is CheckStatus.FAIL]
    assert [check.name for check in failed] == ["solvable_set"]
    assert failed[0].detail == (
        "checkpoint_1: no_new_scoreable_tests",
        "checkpoint_2: no_new_scoreable_tests",
    )
    assert report.verdict is Verdict.EXCLUDED
    path = write_report(report, tmp_path / "report.json")
    written = json.loads(path.read_text())
    assert written["verdict"] == "excluded"
    assert written["manifest_sha256"] == store.load_manifest(ProblemId.parse(NEGATIVE_REF)).digest()
    assert written["grades"]["oracle"]["positive"][0]["checkpoint_2"]["all_pass"] is False
    assert written["solvable"]["checkpoint_2"]["exclusion"] == "no_new_scoreable_tests"


async def test_validation_records_the_reference_digests_it_pools_by(
    store: ProblemStore, tmp_path: Path
) -> None:
    report = await _validate(store, POSITIVE_REF, tmp_path, repeats=1)
    digests: Any = report.to_json()["reference_digests"]
    assert digests[NEGATIVE]["checkpoint_2"] == digests[POSITIVE]["checkpoint_1"]
    assert digests[POSITIVE]["checkpoint_1"] != digests[POSITIVE]["checkpoint_2"]
    reading = next(check for check in report.checks if check.name == "reference_k-1_fails_k")
    assert reading.detail[1].startswith("checkpoint_2: pass")


async def test_one_shot_problem_grades_one_step_under_the_one_shot_policy(
    store: ProblemStore, tmp_path: Path
) -> None:
    report = await _validate(store, ONESHOT_REF, tmp_path, repeats=1)
    assert report.steps == ("checkpoint_1",)
    assert report.verdict is Verdict.VALIDATED_PARTIAL
    # One repeat: the determinism checks did not run either, and the report says so.
    assert report.to_json()["not_run"] == [
        "repeat_identical_positive",
        "repeat_identical_negative",
        "native_parity_positive",
        "native_parity_negative",
    ]
    policy: Any = report.to_json()["headline_policy"]
    assert policy["policy"] == "scb-canonical-oneshot@1"
    assert policy["checkpoints"] == 1
    assert policy["components"] == {
        "C": "used",
        "V": "missing",
        "E": "missing",
        "H": "missing",
        "R": "not_applicable",
        "G": "not_applicable",
    }
    assert policy["eligible"] is False  # V/E/H come from scorers, never zero-filled
    multi: Any = (await _validate(store, POSITIVE_REF, tmp_path, repeats=1)).to_json()
    assert multi["headline_policy"]["policy"] == "scb-canonical@1"


def _runs(
    positive: list[dict[str, Grade]],
    negative: list[dict[str, Grade]] | None = None,
    references: dict[str, dict[str, str]] | None = None,
) -> ValidationRuns:
    return ValidationRuns(
        oracle={POSITIVE: positive, NEGATIVE: positive if negative is None else negative},
        references=references or {},
    )


def test_solvable_sets_keep_cases_passed_in_every_repeat() -> None:
    passing = {"s1": _grade("s1", {"c1::a": "passed", "c1::b": "passed", "c1::s": "skipped"})}
    flaky = {"s1": _grade("s1", {"c1::a": "passed", "c1::b": "failed", "c1::n": "passed"})}
    infra = {"s1": _grade("s1", {}, infra="collection_error")}

    sets = solvable_sets(_runs([passing, flaky]))
    assert sets["s1"].solvable == ("c1::a",)
    assert sets["s1"].nondeterministic == ("c1::b", "c1::n", "c1::s")
    assert sets["s1"].verdict is Verdict.VALIDATED
    assert sets["s1"].digest == solvable_digest(["c1::a"])
    assert solvable_sets(_runs([passing, passing]))["s1"].solvable == ("c1::a", "c1::b")
    # Missing evidence is never an empty set.
    assert solvable_sets(_runs([passing, infra]))["s1"].verdict is Verdict.INFRA_FAILURE
    assert check_solvable("x", _runs([passing])).status is CheckStatus.PASS
    assert check_solvable("x", _runs([passing, infra])).status is CheckStatus.INFRA


def test_a_step_without_new_scoreable_tests_is_excluded_alone() -> None:
    one = _grade("s1", {"c1::a": "passed", "c1::late": "failed"})
    # s2 adds only c2::x, which its reference fails, and regrades s1's cases.
    reused = _grade("s2", {"c1::a": "passed", "c1::late": "failed", "c2::x": "failed"})
    # s3 adds c3::y, which it passes; s1's failed case is not new at s2 or s3.
    later = _grade("s3", {"c1::a": "passed", "c1::late": "passed", "c3::y": "passed"})
    runs = _runs([{"s1": one, "s2": reused, "s3": later}] * 2)

    sets = solvable_sets(runs)
    assert sets["s2"].verdict is Verdict.EXCLUDED
    assert sets["s2"].exclusion == "no_new_scoreable_tests"
    assert sets["s2"].solvable == ()
    assert sets["s1"].verdict is sets["s3"].verdict is Verdict.VALIDATED
    assert sets["s3"].solvable == ("c1::a", "c1::late", "c3::y")
    # Other steps stay usable: the check passes and names the excluded step.
    check = check_solvable("x", runs)
    assert check.status is CheckStatus.PASS
    assert check.detail == ("s2: no_new_scoreable_tests",)
    assert verdict(run_checks(runs)) is Verdict.VALIDATED_PARTIAL


def test_a_new_case_must_pass_in_every_repeat_to_count() -> None:
    first = {"s1": _grade("s1", {"c1::a": "passed"})}
    lucky = {**first, "s2": _grade("s2", {"c1::a": "passed", "c2::x": "passed"})}
    unlucky = {**first, "s2": _grade("s2", {"c1::a": "passed", "c2::x": "failed"})}
    sets = solvable_sets(_runs([lucky, unlucky]))
    assert sets["s2"].exclusion == "no_new_scoreable_tests"
    assert sets["s2"].nondeterministic == ("c2::x",)
    assert sets["s1"].verdict is Verdict.VALIDATED
    # No usable step at all fails the check.
    nothing = {"s1": _grade("s1", {"c1::a": "failed"})}
    assert check_solvable("x", _runs([nothing])).status is CheckStatus.FAIL


def test_repeats_of_identical_reference_code_are_pooled_across_steps_and_variants() -> None:
    # Reference digest A grades s1 (positive) and s2 (negative); B grades s2 (positive).
    refs = {
        POSITIVE: {"s1": "A", "s2": "B", "s3": "C"},
        NEGATIVE: {"s1": "empty", "s2": "A", "s3": "B"},
    }
    cases = {"c1::a": "passed", "c1::x": "passed"}
    positive = [
        {
            "s1": _grade("s1", cases),
            "s2": _grade("s2", {**cases, "c2::n": "passed"}),
            "s3": _grade("s3", {**cases, "c2::n": "passed", "c3::m": "passed"}),
        }
    ] * 3

    def negative(failing: bool) -> dict[str, Grade]:
        flip = {**cases, "c1::x": "failed" if failing else "passed", "c2::n": "failed"}
        return {
            "s1": _grade("s1", dict.fromkeys(cases, "failed")),
            "s2": _grade("s2", flip),
            "s3": _grade("s3", {**cases, "c2::n": "passed", "c3::m": "failed"}),
        }

    stable = [negative(True)] * 3
    pooled = _runs(positive, stable, refs)
    sets = solvable_sets(pooled)
    # c1::x passes in every positive repeat but fails in reference A's negative runs.
    for step in ("s1", "s2", "s3"):
        assert "c1::x" not in sets[step].solvable
        assert "c1::x" in sets[step].nondeterministic
    assert sets["s3"].solvable == ("c1::a", "c2::n", "c3::m")
    assert check_positive_repeats("rep", pooled).detail == (
        "s1: 1 nondeterministic",
        "s2: 1 nondeterministic",
        "s3: 1 nondeterministic",
    )
    # Different code is not pooled: without digests the same grades leave c1::x solvable.
    assert "c1::x" in solvable_sets(_runs(positive, stable))["s1"].solvable
    # Negative repeats that differ only in a quarantined case still agree.
    flipping = _runs(positive, [negative(False), negative(True), negative(False)], refs)
    assert "c1::x" not in solvable_sets(flipping)["s1"].solvable
    assert check_determinism("rep", flipping).status is CheckStatus.PASS


def test_the_reference_reading_never_excludes_and_names_identical_pairs() -> None:
    cases = {"c1::a": "passed", "c2::b": "passed"}
    positive = [{"s1": _grade("s1", {"c1::a": "passed"}), "s2": _grade("s2", cases)}] * 2
    negative = [{"s1": _grade("s1", {"c1::a": "failed"}), "s2": _grade("s2", cases)}] * 2
    same = {POSITIVE: {"s1": "A", "s2": "A"}, NEGATIVE: {"s1": "empty", "s2": "A"}}

    identical = check_reference_reading("r", _runs(positive, negative, same))
    assert identical.status is CheckStatus.PASS
    assert identical.detail == (
        "s1: pass (empty starter reference on s1 tests)",
        "s2: not_informative (s1 and s2 references are identical)",
    )
    different = {POSITIVE: {"s1": "A", "s2": "B"}, NEGATIVE: {"s1": "empty", "s2": "A"}}
    reused = check_reference_reading("r", _runs(positive, negative, different))
    assert reused.status is CheckStatus.PASS  # reference k-1 passes every k case: a reading only
    assert reused.detail[1] == "s2: fail (s1 reference on s2 tests)"
    absent = {"s1": _grade("s1", {}, infra="collection_error")}
    assert check_reference_reading("r", _runs(positive, [absent])).status is CheckStatus.INFRA
    # Neither reading excludes: both problems stay usable.
    assert verdict(run_checks(_runs(positive, negative, same))) is Verdict.VALIDATED_PARTIAL


def test_reference_digest_ignores_pycache_and_sees_content(tmp_path: Path) -> None:
    one, two, three = (tmp_path / name for name in ("one", "two", "three"))
    for root in (one, two, three):
        (root / "pkg").mkdir(parents=True)
        (root / "pkg" / "app.py").write_text("print(1)\n")
    (two / "pkg" / "__pycache__").mkdir()
    (two / "pkg" / "__pycache__" / "app.pyc").write_bytes(b"\0")
    (three / "requirements.txt").write_text("x\n")
    assert reference_tree_digest(one) == reference_tree_digest(two)
    assert reference_tree_digest(one) != reference_tree_digest(three)


def test_repeat_checks_report_what_differs() -> None:
    passing = {"s1": _grade("s1", {"c1::a": "passed", "c1::b": "passed"})}
    flaky = {"s1": _grade("s1", {"c1::a": "passed", "c1::b": "failed"})}
    infra = {"s1": _grade("s1", {}, infra="collection_error")}

    listed = check_positive_repeats("rep", _runs([passing, flaky]))
    assert listed.status is CheckStatus.PASS
    assert listed.detail == ("s1: 1 nondeterministic",)
    assert check_positive_repeats("rep", _runs([passing])).status is CheckStatus.NOT_RUN
    assert check_positive_repeats("rep", _runs([passing, infra])).status is CheckStatus.INFRA

    # A case that varies between negative repeats is quarantined, not a determinism failure.
    assert check_determinism("rep", _runs([passing], [passing, flaky])).status is CheckStatus.PASS
    regrouped = {"s1": Grade("oracle", "s1", {"core": (1, 2)}, passing["s1"].outcomes)}
    drift = check_determinism("rep", _runs([passing], [passing, regrouped]))
    assert drift.status is CheckStatus.FAIL
    assert drift.detail[0].startswith("repeat 1 vs 0 s1:")
    assert check_determinism("rep", _runs([passing])).status is CheckStatus.NOT_RUN
    assert check_determinism("rep", _runs([passing], [passing, infra])).status is CheckStatus.INFRA
    assert check_determinism("rep", _runs([passing], [infra, passing])).status is CheckStatus.INFRA


def test_parity_compares_cases_and_groups_and_verdict_orders_fail_over_not_run() -> None:
    oracle = {"s1": _grade("s1", {"c1::a": "passed", "c1::b": "passed"})}
    same = {"s1": Grade("native", "s1", {"core": (2, 2)}, {"c1::a": "passed", "c1::b": "passed"})}
    other = {"s1": Grade("native", "s1", {"core": (1, 2)}, {"c1::a": "passed", "c1::b": "failed"})}
    broken = {"s1": Grade("native", "s1", {}, {}, "native_no_evaluation")}

    assert check_parity("p", oracle, same).status is CheckStatus.PASS
    mismatch = check_parity("p", oracle, other)
    assert mismatch.status is CheckStatus.FAIL
    assert any("c1::b: oracle=pass native=fail" in line for line in mismatch.detail)
    unavailable = check_parity("p", oracle, broken)
    assert unavailable.status is CheckStatus.INFRA
    assert unavailable.detail == ("s1 native: no verdict (native_no_evaluation)",)
    assert check_parity("p", oracle, {}).status is CheckStatus.INFRA

    empty = {"s1": _grade("s1", {"c1::a": "failed"})}
    runs = ValidationRuns(
        oracle={POSITIVE: [oracle, oracle], NEGATIVE: [empty, empty]},
        native={
            POSITIVE: same,
            NEGATIVE: {"s1": Grade("native", "s1", {"core": (0, 1)}, {"c1::a": "failed"})},
        },
    )
    assert verdict(run_checks(runs)) is Verdict.VALIDATED
    assert verdict(run_checks(ValidationRuns(oracle=runs.oracle))) is Verdict.VALIDATED_PARTIAL


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ({CheckStatus.PASS}, Verdict.VALIDATED),
        ({CheckStatus.PASS, CheckStatus.NOT_RUN}, Verdict.VALIDATED_PARTIAL),
        ({CheckStatus.NOT_RUN, CheckStatus.INFRA}, Verdict.INFRA_FAILURE),
        ({CheckStatus.INFRA, CheckStatus.FAIL, CheckStatus.NOT_RUN}, Verdict.EXCLUDED),
    ],
)
def test_verdict_precedence_is_fail_then_infra_then_not_run(
    statuses: set[CheckStatus], expected: Verdict
) -> None:
    assert verdict([Check(status.value, status) for status in statuses]) is expected


def test_policy_preview_never_scores_an_ungraded_reference_as_zero() -> None:
    graded = {"s1": _grade("s1", {"c1::a": "passed"}), "s2": _grade("s2", {"c2::a": "failed"})}
    preview: Any = policy_preview(graded, 2)
    assert preview["reference_correctness"] == "0.5"
    assert preview["components"]["C"] == "used"

    ungraded = {**graded, "s2": _grade("s2", {}, infra="no_verifier_record")}
    preview = policy_preview(ungraded, 2)
    assert preview["reference_correctness"] is None
    assert preview["components"]["C"] == "ineligible"
    assert preview["eligible"] is False
    assert any("verifier_infra_failure: s2" in r["detail"] for r in preview["reasons"])


def test_native_evaluation_reduces_to_the_same_case_keys_as_the_oracle(tmp_path: Path) -> None:
    grade = grade_from_evaluation(
        {
            "pass_counts": {"Core": 1, "Regression": 1},
            "total_counts": {"Core": 2, "Regression": 1},
            "tests": {
                "checkpoint_2-Core": {"passed": ["test_x[a b]"], "failed": ["test_y"]},
                "checkpoint_1-Regression": {"passed": ["test_z"]},
            },
        },
        "checkpoint_2",
    )
    assert grade.outcomes == {
        "checkpoint_2::test_x[a b]": "passed",
        "checkpoint_2::test_y": "failed",
        "checkpoint_1::test_z": "passed",
    }
    assert grade.groups["core"] == (1, 2)
    assert grade.groups["error"] == (0, 0)
    problem = tmp_path / "adder"
    problem.mkdir()
    (problem / "config.yaml").write_text("test_dependencies: [pyyaml==6.0.2]\n")
    staged = stage_catalog(problem, tmp_path / "catalog")
    pyproject = (staged / "pyproject.toml").read_text()
    assert '"pytest==8.4.1"' in pyproject
    assert '"pyyaml==6.0.2"' in pyproject
    assert (staged / "uv.lock").is_file()
    assert (problem / "config.yaml").read_text() == "test_dependencies: [pyyaml==6.0.2]\n"


def test_harbor_engine_variant_adds_solutions_to_a_copy_only(
    store: ProblemStore, tmp_path: Path
) -> None:
    problem = store.load(ProblemId.parse(POSITIVE_REF))
    empty = tmp_path / "empty"
    empty.mkdir()
    payloads = direct.variant_snapshots(problem, empty)[NEGATIVE]
    assert payloads == {"checkpoint_1": empty, "checkpoint_2": problem.reference("checkpoint_1")}

    task = harbor_engine.variant_task(problem, payloads, tmp_path / "variant")
    solve = task / "steps" / "checkpoint_2" / "solution" / "solve.sh"
    assert solve.stat().st_mode & 0o111
    assert (solve.parent / "payload" / "app.py").read_bytes() == (
        problem.reference("checkpoint_1") / "app.py"
    ).read_bytes()
    assert not list(problem.task_dir.rglob("solution"))
    assert store.load(ProblemId.parse(POSITIVE_REF)).manifest == problem.manifest

    config = harbor_engine.trial_config(
        task, tmp_path / "trials", "negative-0", problem.bundle.root, docker_context="orbstack"
    )
    assert config.agent.name == "oracle"
    assert config.verifier.import_path == VERIFIER_IMPORT_PATH
    assert config.verifier.kwargs["oracle_bundle"] == str(problem.bundle.root)
    # Validation tasks collect no /app artifact, so the verifier downloads the snapshot.
    assert config.verifier.kwargs["snapshot_source"] == "download"
    grades = harbor_engine.read_grades(tmp_path / "trials" / "negative-0", ["checkpoint_1"])
    assert grades["checkpoint_1"].infra_failure == "no_verifier_record"
    # Harbor records (does not raise) trial exceptions; the type survives as the reason.
    crashed = harbor_engine.read_grades(
        tmp_path / "trials" / "negative-0", ["checkpoint_1"], "DockerException"
    )
    assert crashed["checkpoint_1"].infra_failure == "no_verifier_record:DockerException"
    assert [step.name for step in Task(task).config.steps or []] == ["checkpoint_1", "checkpoint_2"]


def test_validate_command_exit_codes_follow_the_verdict(
    store: ProblemStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    docker_target_offline: None,
) -> None:
    async def local_sandbox(problem: Any, docker_context: str) -> tuple[Any, dict[str, object]]:
        return LocalProcessSandbox(tmp_path / "sandbox"), {"sandbox": "local"}

    monkeypatch.setattr(validator, "_docker_sandbox", local_sandbox)
    runner = CliRunner()
    base = [
        "problem",
        "validate",
        "--store",
        str(store.root),
        "--repeats",
        "2",
        "--concurrency",
        "1",
    ]

    excluded = runner.invoke(main, [*base, NEGATIVE_REF])
    assert excluded.exit_code == 1, excluded.output
    assert "[FAIL] solvable_set" in excluded.output
    assert "EXCLUDED" in excluded.output
    written = store.validation_path(ProblemId.parse(NEGATIVE_REF))
    assert json.loads(written.read_text())["environment"]["sandbox"] == "local"

    report = tmp_path / "positive.json"
    passed = runner.invoke(main, [*base, POSITIVE_REF, "--report", str(report)])
    assert passed.exit_code == 0, passed.output
    assert json.loads(report.read_text())["verdict"] == "validated_partial"

    # Docker down: no verdict, exit 2, and the committed (excluded) report is kept.
    committed = written.read_bytes()

    async def down(problem: Any, docker_context: str) -> tuple[Any, dict[str, object]]:
        return DownSandbox(tmp_path / "down"), {"sandbox": "down"}

    monkeypatch.setattr(validator, "_docker_sandbox", down)
    outage = runner.invoke(main, [*base, NEGATIVE_REF])
    assert outage.exit_code == 2, outage.output
    assert written.read_bytes() == committed
    step_result = json.loads(
        store.validation_attempt_path(ProblemId.parse(NEGATIVE_REF)).read_text()
    )
    assert step_result["verdict"] == "infra_failure"
    assert step_result["headline_policy"]["reference_correctness"] is None

    missing = runner.invoke(main, [*base, "fixture/absent@fefefefefefe"])
    assert missing.exit_code == 2
    assert "not imported" in missing.output
    bad = runner.invoke(main, [*base, "no-dataset@1"])
    assert bad.exit_code == 2
    assert "dataset" in bad.output


def test_import_command_reports_manifest_and_drift_errors(tmp_path: Path) -> None:
    from tests.problems_support import build_fake_scb_repo, fake_store  # noqa: PLC0415

    repo, commit = build_fake_scb_repo(tmp_path / "src")
    store = fake_store(tmp_path / "problems", commit)
    runner = CliRunner()
    args = ["problem", "import", f"scb/adder@{commit[:12]}", "--store", str(store.root)]
    first = runner.invoke(main, [*args, "--source-checkout", str(repo)])
    assert first.exit_code == 0, first.output
    assert "imported" in first.output
    second = runner.invoke(main, [*args, "--source-checkout", str(repo)])
    assert "verified (matches manifest)" in second.output
    refused = runner.invoke(
        main, ["problem", "import", "scb/adder@000000000000", "--store", str(store.root)]
    )
    assert refused.exit_code == 1
    assert "does not match" in refused.output


def test_lock_metadata_ignores_run_specific_report_bytes(tmp_path: Path) -> None:
    base = {"verdict": "validated", "manifest_sha256": "a" * 64, "not_run": []}
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    first.write_text(json.dumps({**base, "environment": {"docker_context": "orbstack"}}))
    second.write_text(
        json.dumps({**base, "environment": {"docker_context": "other"}, "repeats": 5})
    )

    one, two = read_validation_record(first), read_validation_record(second)

    assert one.lock_metadata() == two.lock_metadata() == {"verdict": "validated", "not_run": []}


async def test_report_records_solvable_sets_the_lock_pins(
    store: ProblemStore, tmp_path: Path
) -> None:
    report = await _validate(store, POSITIVE_REF, tmp_path)
    path = write_report(report, tmp_path / "report.json")
    record = read_validation_record(path)

    for step, one in solvable_sets(report.runs).items():
        assert record.solvable_cases(step) == one.solvable
        assert record.solvable_size(step) == len(one.solvable) > 0
        assert record.nondeterministic_count(step) == 0
    lock = record.solvable_lock()
    assert lock["steps"] == {s: list(c.cases) for s, c in record.solvable.items()}
    assert record.solvable_cases("absent") is None

    data = json.loads(path.read_text())
    data["solvable"]["checkpoint_1"]["solvable"] = []
    path.write_text(json.dumps(data))
    with pytest.raises(ValidationRecordError):
        read_validation_record(path)


def test_a_validation_that_skipped_the_repeat_checks_is_not_usable(tmp_path: Path) -> None:
    path = tmp_path / "v.json"
    path.write_text(
        json.dumps(
            {
                "verdict": "validated_partial",
                "manifest_sha256": "a" * 64,
                "not_run": ["repeat_identical_positive", "native_parity_positive"],
            }
        )
    )
    record = read_validation_record(path)
    assert not record.usable
    assert record.missing_required == ("repeat_identical_positive",)

    path.write_text(
        json.dumps(
            {
                "verdict": "validated_partial",
                "manifest_sha256": "a" * 64,
                "not_run": ["native_parity_positive"],
            }
        )
    )
    assert read_validation_record(path).usable


async def test_a_weaker_validation_never_replaces_the_committed_report(
    store: ProblemStore, tmp_path: Path
) -> None:
    (tmp_path / "full").mkdir()
    (tmp_path / "single").mkdir()
    full = await _validate(store, ONESHOT_REF, tmp_path / "full", repeats=2)
    committed = validator.default_report_path(store, full)
    write_report(full, committed)

    single = await _validate(store, ONESHOT_REF, tmp_path / "single", repeats=1)

    weaker = validator.default_report_path(store, single)
    assert weaker != committed
    assert weaker == store.validation_attempt_path(ProblemId.parse(ONESHOT_REF))
    assert validator.default_report_path(store, full) == committed


async def test_failed_revalidation_revokes_admission_even_with_skipped_checks(
    store: ProblemStore, tmp_path: Path
) -> None:
    report = await _validate(store, ONESHOT_REF, tmp_path, repeats=2)
    report.checks = [Check(check.name, CheckStatus.PASS) for check in report.checks]
    problem_id = ProblemId.parse(ONESHOT_REF)
    committed = store.validation_path(problem_id)
    write_report(report, committed)
    assert store.require_validated(problem_id, store.load_manifest(problem_id)).usable

    report.checks = [
        Check("solvable_set", CheckStatus.FAIL),
        Check("native_parity_positive", CheckStatus.NOT_RUN),
    ]
    path = validator.default_report_path(store, report)
    assert path == committed
    write_report(report, path)
    with pytest.raises(ValueError, match="not usable.*excluded"):
        store.require_validated(problem_id, store.load_manifest(problem_id))


@pytest.mark.parametrize("limit", [1, 2])
async def test_harbor_validation_respects_concurrency_across_all_repeats(
    store: ProblemStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    problem = store.load(ProblemId.parse(ONESHOT_REF))
    active = 0
    peak = 0
    completed: list[str] = []

    async def run_trial(config: Any, bundle_root: Path) -> tuple[Path, None]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        completed.append(config.trial_name)
        active -= 1
        return tmp_path / config.trial_name, None

    monkeypatch.setattr(harbor_engine, "_run_trial", run_trial)
    monkeypatch.setattr(harbor_engine, "read_grades", lambda *args: {})
    runs = await validator._harbor_runs(
        problem,
        ValidateOptions("test-ctx", engine="harbor", repeats=5, concurrency=limit),
        tmp_path / "harbor",
    )
    assert peak == limit
    assert len(completed) == len(set(completed)) == 10
    assert {variant: len(repeats) for variant, repeats in runs.items()} == {
        POSITIVE: 5,
        NEGATIVE: 5,
    }


@pytest.mark.parametrize("dirty_file", ["config.yaml", "untracked.py"])
def test_native_parity_refuses_modified_problem_sources_before_staging(
    store: ProblemStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dirty_file: str
) -> None:
    checkout_root, commit = build_fake_scb_repo(tmp_path / "source")
    checkout = PinnedCheckout(checkout_root, SourcePin(url="fixture://adder", commit=commit))
    (checkout_root / "adder" / dirty_file).write_text("modified source\n")
    monkeypatch.setattr(validator, "resolve_checkout", lambda *args: checkout)
    staged: list[Path] = []
    monkeypatch.setattr(validator, "stage_catalog", lambda source, catalog: staged.append(source))
    with pytest.raises(SourceError, match="local changes"):
        validator._native_runs(
            store,
            store.load(ProblemId.parse(POSITIVE_REF)),
            ValidateOptions("test-ctx", native_runner=tmp_path / "runner"),
            tmp_path / "native",
        )
    assert not staged
