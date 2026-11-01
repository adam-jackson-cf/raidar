"""Slow-tier behaviours split from :mod:`tests.test_problem_validation_behaviors`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.design.problem import validator
from raidar.design.problem.models import ProblemId, SourcePin
from raidar.design.problem.native import stage_catalog
from raidar.design.problem.source import PinnedCheckout, SourceError
from raidar.design.problem.store import ProblemStore
from raidar.design.problem.validation import (
    NEGATIVE,
    POSITIVE,
    Check,
    CheckStatus,
    ValidationRecordError,
    Verdict,
    read_validation_record,
    solvable_sets,
)
from raidar.design.problem.validator import ValidateOptions, write_report
from tests.problems_support import (
    NEGATIVE_REF,
    ONESHOT_REF,
    POSITIVE_REF,
    build_fake_scb_repo,
)
from tests.test_problem_validation_behaviors import (
    DownSandbox as DownSandbox,
)
from tests.test_problem_validation_behaviors import (
    _validate as _validate,
)
from tests.test_problem_validation_behaviors import (
    store_fixture as store_fixture,
)
from tests.verifier_support import LocalProcessSandbox

pytestmark = pytest.mark.slow


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
    observations = report.to_json()["reference_observations"]
    assert observations["positive"]["checkpoint_2"]["observation"] == (
        "no variation observed in 2 runs for this reference and environment"
    )
    assert observations["positive"]["checkpoint_2"]["status"] == "measured"


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


def test_native_catalog_stages_a_locked_evaluator_project_with_real_uv(tmp_path: Path) -> None:
    problem = tmp_path / "adder"
    problem.mkdir()
    (problem / "config.yaml").write_text("test_dependencies: [pyyaml==6.0.2]\n")
    staged = stage_catalog(problem, tmp_path / "catalog")
    pyproject = (staged / "pyproject.toml").read_text()
    assert '"pytest==8.4.1"' in pyproject
    assert '"pyyaml==6.0.2"' in pyproject
    assert (staged / "uv.lock").is_file()
    assert (problem / "config.yaml").read_text() == "test_dependencies: [pyyaml==6.0.2]\n"


def test_validate_command_exit_codes_follow_the_verdict(
    store: ProblemStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    docker_target_offline: None,
) -> None:
    async def local_sandbox(problem: Any, docker_context: str) -> tuple[Any, dict[str, object]]:
        return LocalProcessSandbox(tmp_path / "sandbox"), {"sandbox": "local"}

    monkeypatch.setattr(validator.direct, "prepare_grader_sandbox", local_sandbox)
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

    monkeypatch.setattr(validator.direct, "prepare_grader_sandbox", down)
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
