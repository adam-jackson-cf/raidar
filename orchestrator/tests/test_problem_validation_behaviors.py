"""Problem validation (B2) and one-shot support (B3) over the fixture store."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from harbor.models.task.task import Task

from raidar.cli import main
from raidar.oracle.sandbox import SandboxResult, SandboxRun
from raidar.problems import direct, harbor_engine, validator
from raidar.problems.models import ProblemId
from raidar.problems.native import grade_from_evaluation, stage_catalog
from raidar.problems.rewrite import ORACLE_VERIFIER_IMPORT_PATH
from raidar.problems.store import ProblemStore
from raidar.problems.validation import (
    NEGATIVE,
    POSITIVE,
    Check,
    CheckStatus,
    Grade,
    ValidationRuns,
    Verdict,
    check_determinism,
    check_expectation,
    check_parity,
    policy_preview,
    read_validation_record,
    run_checks,
    verdict,
)
from raidar.problems.validator import ValidateOptions, validate_problem, write_report
from tests.oracle_support import LocalProcessSandbox
from tests.problems_support import (
    NEGATIVE_REF,
    ONESHOT_REF,
    POSITIVE_REF,
    build_fixture_store,
)


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
        ValidateOptions(repeats=repeats, concurrency=1),  # one local sandbox root
        sandbox=LocalProcessSandbox(tmp_path / "sandbox"),
        work_root=tmp_path,
    )


async def test_reference_trajectory_validates_with_oracle_grading(
    store: ProblemStore, tmp_path: Path
) -> None:
    report = await _validate(store, POSITIVE_REF, tmp_path)
    statuses = {check.name: check.status for check in report.checks}
    assert statuses == {
        "reference_k_passes_1..k": CheckStatus.PASS,
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
    assert [check.name for check in failed] == ["reference_k-1_fails_k"]
    assert all("checkpoint_2" in line for line in failed[0].detail)
    assert report.verdict is Verdict.EXCLUDED
    path = write_report(report, tmp_path / "report.json")
    written = json.loads(path.read_text())
    assert written["verdict"] == "excluded"
    assert written["manifest_sha256"] == store.load_manifest(ProblemId.parse(NEGATIVE_REF)).digest()
    assert written["grades"]["oracle"]["negative"][0]["checkpoint_2"]["all_pass"] is True


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
    assert policy["eligible"] is False  # V/E/H come from WS-G scorers, never zero-filled
    multi: Any = (await _validate(store, POSITIVE_REF, tmp_path, repeats=1)).to_json()
    assert multi["headline_policy"]["policy"] == "scb-canonical@1"


def test_expectation_and_determinism_checks_report_what_differs() -> None:
    passing = {"s1": _grade("s1", {"c1::a": "passed", "c1::b": "passed", "c1::s": "skipped"})}
    flaky = {"s1": _grade("s1", {"c1::a": "passed", "c1::b": "failed"})}
    infra = {"s1": _grade("s1", {}, infra="collection_error")}

    assert check_expectation("pos", [passing], expect_pass=True).status is CheckStatus.PASS
    # A real miss outranks missing evidence; both are reported.
    missed = check_expectation("pos", [passing, flaky, infra], expect_pass=True)
    assert missed.status is CheckStatus.FAIL
    assert missed.detail == (
        "repeat 1 s1: 1/2 (expected all pass)",
        "repeat 2 s1: no verdict (collection_error)",
    )
    # An infrastructure failure is neither a pass nor an (expected) failure.
    for expect_pass in (True, False):
        unknown = check_expectation("x", [infra], expect_pass=expect_pass)
        assert unknown.status is CheckStatus.INFRA
        assert unknown.detail == ("repeat 0 s1: no verdict (collection_error)",)

    drift = check_determinism("rep", [passing, flaky])
    assert drift.status is CheckStatus.FAIL
    assert "c1::b: oracle=pass oracle=fail" in drift.detail[0]
    assert check_determinism("rep", [passing]).status is CheckStatus.NOT_RUN
    assert check_determinism("rep", [passing, infra]).status is CheckStatus.INFRA
    assert check_determinism("rep", [infra, passing]).status is CheckStatus.INFRA


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

    ungraded = {**graded, "s2": _grade("s2", {}, infra="no_oracle_record")}
    preview = policy_preview(ungraded, 2)
    assert preview["reference_correctness"] is None
    assert preview["components"]["C"] == "ineligible"
    assert preview["eligible"] is False
    assert any("oracle_infra_failure: s2" in r["detail"] for r in preview["reasons"])


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


async def test_direct_engine_refuses_references_that_need_installed_requirements(
    store: ProblemStore, tmp_path: Path
) -> None:
    problem = store.load(ProblemId.parse(POSITIVE_REF))
    (problem.reference("checkpoint_2") / "requirements.txt").write_text("# comment\nrequests\n")
    with pytest.raises(direct.DirectEngineError, match="requests"):
        await direct.grade_references(problem, LocalProcessSandbox(tmp_path / "s"), repeats=1)
    with pytest.raises(ValueError, match="repeats"):
        await direct.grade_references(problem, LocalProcessSandbox(tmp_path / "s"), repeats=0)


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
    assert config.verifier.import_path == ORACLE_VERIFIER_IMPORT_PATH
    assert config.verifier.kwargs["oracle_bundle"] == str(problem.bundle.root)
    assert config.verifier.kwargs["snapshot_source"] == "artifact"
    grades = harbor_engine.read_grades(tmp_path / "trials" / "negative-0", ["checkpoint_1"])
    assert grades["checkpoint_1"].infra_failure == "no_oracle_record"
    # Harbor records (does not raise) trial exceptions; the type survives as the reason.
    crashed = harbor_engine.read_grades(
        tmp_path / "trials" / "negative-0", ["checkpoint_1"], "DockerException"
    )
    assert crashed["checkpoint_1"].infra_failure == "no_oracle_record:DockerException"
    assert [step.name for step in Task(task).config.steps or []] == ["checkpoint_1", "checkpoint_2"]


def test_validate_command_exit_codes_follow_the_verdict(
    store: ProblemStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    assert "[FAIL] reference_k-1_fails_k" in excluded.output
    assert "EXCLUDED" in excluded.output
    written = store.validation_path(ProblemId.parse(NEGATIVE_REF))
    assert json.loads(written.read_text())["environment"]["sandbox"] == "local"

    report = tmp_path / "positive.json"
    passed = runner.invoke(main, [*base, POSITIVE_REF, "--report", str(report)])
    assert passed.exit_code == 0, passed.output
    assert json.loads(report.read_text())["verdict"] == "validated_partial"
    assert "VALIDATED_PARTIAL (not run: native_parity_positive" in passed.output

    # Docker down: no verdict, exit 2, and the committed (excluded) report is kept.
    committed = written.read_bytes()

    async def down(problem: Any, docker_context: str) -> tuple[Any, dict[str, object]]:
        return DownSandbox(tmp_path / "down"), {"sandbox": "down"}

    monkeypatch.setattr(validator, "_docker_sandbox", down)
    outage = runner.invoke(main, [*base, NEGATIVE_REF])
    assert outage.exit_code == 2, outage.output
    assert "[INFRA] reference_k_passes_1..k" in outage.output
    assert "INFRA_FAILURE" in outage.output
    assert written.read_bytes() == committed
    attempt = json.loads(store.validation_attempt_path(ProblemId.parse(NEGATIVE_REF)).read_text())
    assert attempt["verdict"] == "infra_failure"
    assert attempt["headline_policy"]["reference_correctness"] is None

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


FAKE_SLOP_CODE = """#!/usr/bin/env python3
import json, sys
from pathlib import Path
args = sys.argv[1:]
out = Path(args[args.index("-o") + 1])
out.mkdir(parents=True)
checkpoint = args[args.index("-c") + 1]
Path(out.parent / "calls.txt").open("a").write(checkpoint + "\\n")
evaluation = {
    "pass_counts": {"Core": 1},
    "total_counts": {"Core": 1},
    "tests": {f"checkpoint_{checkpoint}-Core": {"passed": ["test_native_only"]}},
}
(out / "evaluation.json").write_text(json.dumps(evaluation))
"""


async def test_validator_wires_the_harbor_engine_and_the_native_arm(
    store: ProblemStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner_root = tmp_path / "fork"
    script = runner_root / ".venv" / "bin" / "slop-code"
    script.parent.mkdir(parents=True)
    script.write_text(FAKE_SLOP_CODE)
    script.chmod(0o755)
    problem = store.load(ProblemId.parse(POSITIVE_REF))
    harbor_calls: list[tuple[str, int]] = []

    async def fake_variant(
        imported: Any, variant: str, payloads: Any, work: Path, *, repeats: int, docker_context: str
    ) -> list[Mapping[str, Grade]]:
        harbor_calls.append((variant, repeats))
        grades = await direct.grade_references(
            imported, LocalProcessSandbox(tmp_path / "sandbox"), repeats=repeats, concurrency=1
        )
        return grades[variant]

    class Checkout:
        root = tmp_path / "checkout"

    (Checkout.root / "adder").mkdir(parents=True)
    monkeypatch.setattr(harbor_engine, "run_variant", fake_variant)
    monkeypatch.setattr(validator, "resolve_checkout", lambda *args: Checkout())
    monkeypatch.setattr(validator, "stage_catalog", lambda source, catalog: catalog / source.name)
    monkeypatch.setattr("raidar.problems.native._docker_host", lambda context: "unix:///fake")
    report = await validate_problem(
        store,
        ProblemId.parse(POSITIVE_REF),
        ValidateOptions(engine="harbor", repeats=1, native_runner=runner_root),
        work_root=tmp_path,
    )
    assert harbor_calls == [(POSITIVE, 1), (NEGATIVE, 1)]
    assert report.environment["native_runner"] == {"commit": None}
    assert report.environment["grader_image"].startswith("raidar-oracle-grader:")
    assert report.environment["pack_image"] == problem.manifest.pack.image
    statuses = {check.name: check.status for check in report.checks}
    assert statuses["native_parity_positive"] is CheckStatus.FAIL
    assert report.runs.native is not None
    native = report.runs.native[POSITIVE]["checkpoint_2"]
    assert native.outcomes == {"checkpoint_2::test_native_only": "passed"}
    assert report.verdict is Verdict.EXCLUDED
    assert problem.manifest == store.load(ProblemId.parse(POSITIVE_REF)).manifest
    with pytest.raises(ValueError, match="engine"):
        await validate_problem(store, ProblemId.parse(POSITIVE_REF), ValidateOptions(engine="x"))


def test_lock_metadata_ignores_run_specific_report_bytes(tmp_path: Path) -> None:
    base = {"verdict": "validated", "manifest_sha256": "a" * 64, "not_run": []}
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    first.write_text(json.dumps({**base, "environment": {"docker_context": "orbstack"}}))
    second.write_text(
        json.dumps({**base, "environment": {"docker_context": "other"}, "repeats": 5})
    )

    one, two = read_validation_record(first), read_validation_record(second)

    assert one.report_sha256 != two.report_sha256
    assert one.lock_metadata() == two.lock_metadata() == {"verdict": "validated", "not_run": []}


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
