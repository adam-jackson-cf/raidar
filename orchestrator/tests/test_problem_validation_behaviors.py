"""Problem validation (B2) and one-shot support (B3) over the fixture store."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner
from harbor.models.task.task import Task
from raidar_contract.canonical import digest_json

from raidar.cli import main
from raidar.contract.paths import VAR_ENV, problems_root
from raidar.contract.references import RegistryReferenceError
from raidar.design.problem import direct, harbor_engine, importer, validator
from raidar.design.problem.catalog import select_problems, source_catalog
from raidar.design.problem.content import content_digest, hidden_content_in_task
from raidar.design.problem.importer import ProblemDriftError
from raidar.design.problem.models import ProblemId, ProblemManifest, ProblemMode, SourcePin
from raidar.design.problem.native import (
    NativeRunner,
    NativeRunnerError,
    grade_from_evaluation,
    stage_catalog,
)
from raidar.design.problem.rewrite import (
    VERIFIER_IMPORT_PATH,
    RewriteError,
    checkpoint_specs,
    converted_steps,
    copy_references,
    grader_entry_command,
    problem_entry_file,
    render_spec,
    select_steps,
)
from raidar.design.problem.source import (
    PinnedCheckout,
    SourceError,
    convert_problem,
    ensure_checkout,
    open_checkout,
)
from raidar.design.problem.store import (
    ROOT_ENV,
    ProblemStore,
    ProblemStoreError,
    open_store,
    store_root,
)
from raidar.design.problem.validation import (
    NEGATIVE,
    POSITIVE,
    Check,
    CheckStatus,
    Grade,
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
from raidar.design.problem.validator import ValidateOptions, validate_problem
from raidar.grading.bundle import VerifierStepSpec
from raidar.grading.sandbox import SandboxResult, SandboxRun
from raidar.subject.environment.python.pack_definition import load_pack_spec
from tests.problems_support import (
    CONVERTED_TASK_TOML,
    NEGATIVE_REF,
    ONESHOT_REF,
    POSITIVE_REF,
    FakeResolver,
    ScriptedSource,
    build_fixture_store,
    fake_store,
    scripted_tree_id,
    write_fake_scb_tree,
)
from tests.verifier_support import LocalProcessSandbox

GRADER = {
    "image": "raidar-oracle-grader:fixture",
    "environment_id": "sha256:" + "f" * 64,
    "implementation_sha256": "a" * 64,
}


class DownSandbox(LocalProcessSandbox):
    """A grader whose container runtime is unreachable."""

    async def run(self, run: SandboxRun) -> SandboxResult:
        return SandboxResult(exit_code=125, timed_out=False, output="Cannot connect to Docker")


@pytest.fixture(name="store")
def store_fixture(tmp_path: Path) -> ProblemStore:
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
        task,
        tmp_path / "trials",
        "negative-0",
        problem.bundle.root,
        docker_context="orbstack",
        grader=GRADER,
    )
    assert config.verifier.kwargs["pinned"] == {"grader": GRADER}
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


def test_lock_metadata_ignores_run_specific_report_bytes(tmp_path: Path) -> None:
    base = {"verdict": "validated", "manifest_sha256": "a" * 64, "not_run": []}
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    first.write_text(json.dumps({**base, "environment": {"docker_context": "orbstack"}}))
    second.write_text(
        json.dumps({**base, "environment": {"docker_context": "other"}, "repeats": 5})
    )

    one, two = read_validation_record(first), read_validation_record(second)

    assert one.lock_metadata() == two.lock_metadata()
    assert one.lock_metadata()["spec_determinacy"]["status"] == "unmeasured"

    second.write_text(
        json.dumps(
            {
                **base,
                "spec_determinacy": {
                    "assertions": {"audited": 1, "total": 3},
                    "flagged": [{"case": "checkpoint_1::wording"}],
                },
            }
        )
    )
    assert read_validation_record(second).lock_metadata() != one.lock_metadata()


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
        yielded = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_soon(yielded.set_result, None)
        await yielded
        completed.append(config.trial_name)
        active -= 1
        return tmp_path / config.trial_name, None

    monkeypatch.setattr(harbor_engine, "_run_trial", run_trial)
    monkeypatch.setattr(harbor_engine, "read_grades", lambda *args: {})
    runs = await validator._harbor_runs(
        problem,
        ValidateOptions("test-ctx", engine="harbor", repeats=5, concurrency=limit),
        tmp_path / "harbor",
        GRADER,
    )
    assert peak == limit
    assert len(completed) == len(set(completed)) == 10
    assert {variant: len(repeats) for variant, repeats in runs.items()} == {
        POSITIVE: 5,
        NEGATIVE: 5,
    }


# ---- import decisions (B1): references, spec rewrite, pinned source, scripted import --------
# Moved from the slow import file: git and converter shells use an injected runner.
# Small source/store fixtures remain on disk; real git/converter seams stay slow.
COMMIT = "ab" * 20


def _cp(code: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, out, err)


def _pin(converter: str | None = None) -> SourcePin:
    extra = {} if converter is None else {"converter": converter}
    return SourcePin(url="file:///nowhere", commit=COMMIT, **extra)


def _ref(name: str = "scb/adder") -> ProblemId:
    return ProblemId.parse(f"{name}@{COMMIT[:12]}")


@pytest.fixture(name="scripted")
def scripted_fixture(tmp_path: Path) -> tuple[ProblemStore, Path, ScriptedSource]:
    repo = write_fake_scb_tree(tmp_path / "src")
    return fake_store(tmp_path / "problems", COMMIT), repo, ScriptedSource(repo, COMMIT)


def _import(
    scripted: tuple[ProblemStore, Path, ScriptedSource], ref: ProblemId | None = None, **kw: Any
) -> importer.ImportResult:
    store, repo, run = scripted
    return importer.import_problem(
        store, ref or _ref(), source_checkout=repo, resolver=FakeResolver(), run=run, **kw
    )


def test_problem_references_name_dataset_problem_and_optional_one_shot_checkpoint() -> None:
    multi = ProblemId.parse("scb/forge@38d627ecf668")
    oneshot = ProblemId.parse("scb/circuit_eval.checkpoint_1@38d627ecf668")
    assert (multi.dataset, multi.problem, multi.checkpoint, multi.mode) == (
        "scb",
        "forge",
        None,
        ProblemMode.MULTI_CHECKPOINT,
    )
    assert (oneshot.problem, oneshot.checkpoint, oneshot.mode) == (
        "circuit_eval",
        "checkpoint_1",
        ProblemMode.ONE_SHOT,
    )
    assert str(oneshot) == "scb/circuit_eval.checkpoint_1@38d627ecf668"
    for bad in ("forge@38d627ecf668", "scb/forge", "scb/a/b@1"):
        with pytest.raises(RegistryReferenceError):
            ProblemId.parse(bad)


def test_a_spec_token_the_problem_does_not_define_is_refused() -> None:
    assert (
        render_spec("run %%%ENTRYPOINT:other%%%", entry_command="c", entry_file="f.py")
        == "run %%%ENTRYPOINT:other%%%"
    )
    with pytest.raises(RewriteError, match="does not define"):
        render_spec("run %%%ENTRYPOINT:entry_file%%%", entry_command="c", entry_file=None)
    with pytest.raises(RewriteError, match="entry_command"):
        render_spec("%%%ENTRYPOINT:entry_command%%%", entry_command=None, entry_file="f")
    assert render_spec("plain", entry_command=None, entry_file=None) == "plain"


def test_spec_tokens_are_replaced_everywhere_and_nothing_else_changes() -> None:
    tokens = "%%%ENTRYPOINT:entry_command%%% ~ %%%ENTRYPOINT:entry_command%%%"
    text = f"<!-- keep -->\n{tokens} %%%ENTRYPOINT:entry_file%%%"
    assert (
        render_spec(text, entry_command="uv run app.py", entry_file="app.py")
        == "<!-- keep -->\nuv run app.py ~ uv run app.py app.py"
    )


def test_a_one_shot_is_the_first_checkpoint_alone() -> None:
    steps = ["checkpoint_1", "checkpoint_2"]
    assert select_steps(steps, None) == steps
    assert select_steps(steps, "checkpoint_1") == ["checkpoint_1"]
    with pytest.raises(RewriteError, match="first checkpoint"):
        select_steps(steps, "checkpoint_2")
    with pytest.raises(RewriteError, match="declares no"):
        select_steps([], None)


def _bundle(*step_args: tuple[str, ...]) -> Any:
    steps = [
        VerifierStepSpec(
            name=f"s{i}", checkpoint=f"s{i}", test_files=("t.py",), pytest_args=("{tests}/t.py", *a)
        )
        for i, a in enumerate(step_args)
    ]
    return SimpleNamespace(manifest=SimpleNamespace(steps=steps))


def test_the_grader_entry_command_is_the_one_every_step_passes() -> None:
    command = ("--entrypoint", "python3 {app}/main.py")
    assert grader_entry_command(_bundle(command, command)) == "python3 /app/main.py"
    assert grader_entry_command(_bundle(("-x",), command)) == "python3 /app/main.py"
    assert grader_entry_command(_bundle(("-x",))) is None
    assert grader_entry_command(_bundle(("--entrypoint",))) is None  # no value follows
    with pytest.raises(RewriteError, match="disagree"):
        grader_entry_command(_bundle(command, ("--entrypoint", "other")))


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ("checkpoints: {}\n", None),
        ("- not\n- a mapping\n", None),
        ("entry_file: app\n", "app.py"),
    ],
)
def test_the_problem_entry_file_is_read_from_its_config(
    tmp_path: Path, config: str, expected: str | None
) -> None:
    (tmp_path / "config.yaml").write_text(config)
    assert problem_entry_file(tmp_path, load_pack_spec()) == expected


@pytest.mark.parametrize(
    ("config", "message"),
    [("entry_file: 3\n", "must be a string"), ("entry_file: ../x\n", "config.yaml")],
)
def test_a_malformed_entry_file_names_the_config_it_came_from(
    tmp_path: Path, config: str, message: str
) -> None:
    (tmp_path / "config.yaml").write_text(config)
    with pytest.raises(RewriteError, match=message):
        problem_entry_file(tmp_path, load_pack_spec())


def test_checkpoint_specs_and_references_come_from_the_pinned_files(tmp_path: Path) -> None:
    problem = tmp_path / "adder"
    problem.mkdir()
    for name in ("checkpoint_1", "checkpoint_2"):
        (problem / f"{name}.md").write_bytes(f"{name}: %%%ENTRYPOINT:entry_file%%%".encode())
    specs = checkpoint_specs(problem, ["checkpoint_2"], entry_command="c", entry_file="app.py")
    assert specs == {"checkpoint_2": "checkpoint_2: app.py"}

    converted = tmp_path / "converted"
    for name in ("checkpoint_1", "checkpoint_2"):
        payload = converted / "steps" / name / "solution" / "payload"
        payload.mkdir(parents=True)
        (payload / "app.py").write_text(name)
    (converted / "task.toml").write_text(CONVERTED_TASK_TOML)
    out = tmp_path / "references"
    copy_references(converted, out, ["checkpoint_1"])
    assert [p.name for p in out.iterdir()] == ["checkpoint_1"]
    assert (out / "checkpoint_1" / "app.py").read_text() == "checkpoint_1"
    assert converted_steps(converted) == ["checkpoint_1", "checkpoint_2"]


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("forge", "forge.py"),
        (" circopt ", "circopt.py"),
        ("my..tool", "my..tool.py"),
        ("", None),
        ("../escape", None),
        ("pkg/../../escape", None),
    ],
)
def test_the_pack_formats_entry_files_as_snapshot_relative_paths(
    configured: str, expected: str | None
) -> None:
    pack = load_pack_spec()
    if expected is not None:
        assert pack.entry_file(configured) == expected
        return
    with pytest.raises(ValueError, match="snapshot-relative"):
        pack.entry_file(configured)


def test_source_selection_requires_one_pin_and_unambiguous_names(tmp_path: Path) -> None:
    repo = write_fake_scb_tree(tmp_path / "source")
    store = fake_store(tmp_path / "store", COMMIT)
    run = ScriptedSource(repo, COMMIT)
    source = f"scb@{COMMIT}"
    _, available = source_catalog(store, source, repo, run=run)
    expected = (_ref(),)
    assert available == expected
    assert select_problems(store, (), "all", source, repo, importing=True, run=run) == expected
    assert select_problems(store, (), "adder,adder", source, repo, run=run) == expected
    assert select_problems(store, (str(expected[0]),), None, None) == expected
    assert select_problems(store, (str(expected[0]),) * 2, None, None) == expected
    for refs, names, pin in (
        ((str(expected[0]),), "all", source),
        ((), None, source),
        ((), "missing", source),
        ((), "adder", "scb@000000000000"),
        ((), "adder", "scb"),
        ((), "adder", "other@1"),
        ((), "adder", None),
    ):
        with pytest.raises(ProblemStoreError):
            select_problems(store, refs, names, pin, repo, importing=True, run=run)


def test_registered_problems_are_selected_by_name_or_all(store: ProblemStore) -> None:
    everything = tuple(store.registered())
    assert len(everything) == 3
    assert select_problems(store, (), "all", None) == everything
    names = select_problems(store, (), "adder, fixture/adder_negative", None)
    assert [str(p) for p in names] == [POSITIVE_REF, NEGATIVE_REF]
    with pytest.raises(ProblemStoreError, match="one pinned problem"):
        select_problems(store, (), "nothing", None)


def test_a_checkout_must_sit_at_its_pin(tmp_path: Path) -> None:
    repo = write_fake_scb_tree(tmp_path)
    assert open_checkout(repo, _pin(), run=ScriptedSource(repo, COMMIT)).root == repo
    with pytest.raises(SourceError, match="expected pinned commit"):
        open_checkout(repo, _pin(), run=ScriptedSource(repo, COMMIT, head="c" * 40))
    with pytest.raises(SourceError, match="boom"):
        open_checkout(repo, _pin(), run=lambda argv, **kw: _cp(1, err="boom"))


def test_a_checkout_reports_tree_ids_and_refuses_local_changes(tmp_path: Path) -> None:
    repo = write_fake_scb_tree(tmp_path)
    checkout = open_checkout(repo, _pin(), run=ScriptedSource(repo, COMMIT))
    assert checkout.converter_dir == "scripts"
    assert checkout.tree_id("adder") == scripted_tree_id("adder")
    assert checkout.optional_tree_id("conversion.toml") is None
    checkout.verify_clean("adder")
    dirty = open_checkout(repo, _pin(), run=ScriptedSource(repo, COMMIT, dirty="?? adder/x.py"))
    with pytest.raises(SourceError, match="local changes under adder"):
        dirty.verify_clean("adder")


def test_the_pinned_clone_is_made_once_and_only_when_absent(tmp_path: Path) -> None:
    repo = write_fake_scb_tree(tmp_path / "src")
    run = ScriptedSource(repo, COMMIT)
    cache = tmp_path / "cache"
    first = ensure_checkout(cache, "scb", _pin(), run=run)
    again = ensure_checkout(cache, "scb", _pin(), run=run)
    assert first.root == again.root == cache / "sources" / f"scb-{COMMIT[:12]}"
    assert [call[1] for call in run.calls if call[1] == "clone"] == ["clone"]
    refused = ScriptedSource(repo, COMMIT, clone_error="no route")
    with pytest.raises(SourceError, match="clone of .* failed: no route"):
        ensure_checkout(tmp_path / "other", "scb", _pin(), run=refused)


def test_the_converter_must_run_inside_the_checkout_and_produce_a_task(tmp_path: Path) -> None:
    repo = write_fake_scb_tree(tmp_path / "src")
    out = tmp_path / "out"
    ok = PinnedCheckout(repo, _pin(), ScriptedSource(repo, COMMIT))
    assert (convert_problem(ok, "adder", out) / "task.toml").is_file()

    failed = PinnedCheckout(repo, _pin(), ScriptedSource(repo, COMMIT, converter_exit=3))
    with pytest.raises(SourceError, match=r"(?s)converter failed for adder \(exit 3\).*said no"):
        convert_problem(failed, "adder", tmp_path / "out2")
    silent = PinnedCheckout(repo, _pin(), lambda argv, **kw: _cp())
    with pytest.raises(SourceError, match="converter failed"):  # exit 0, no task.toml
        convert_problem(silent, "adder", tmp_path / "out3")
    hung = PinnedCheckout(repo, _pin(), ScriptedSource(repo, COMMIT, hang=True))
    with pytest.raises(SourceError, match="timed out"):
        convert_problem(hung, "adder", tmp_path / "out4")
    absent = PinnedCheckout(repo, _pin("scripts/absent.py"), ScriptedSource(repo, COMMIT))
    with pytest.raises(SourceError, match="pinned converter scripts/absent.py not found"):
        convert_problem(absent, "adder", tmp_path / "out5")
    for escaping in ("../x.py", "/abs/x.py", "x.py"):
        with pytest.raises(SourceError, match="inside"):
            _ = PinnedCheckout(repo, _pin(escaping), ScriptedSource(repo, COMMIT)).converter_dir


def test_import_splits_the_converted_task_and_pins_the_rewritten_specs(
    scripted: tuple[ProblemStore, Path, ScriptedSource],
) -> None:
    store, repo, _ = scripted
    result = _import(scripted)
    manifest, content = result.manifest, result.content_dir
    assert result.changed
    assert [step.name for step in manifest.steps] == ["checkpoint_1", "checkpoint_2"]
    assert manifest.source.commit == COMMIT
    assert manifest.source.problem_tree == scripted_tree_id("adder")
    assert manifest.entry_file == "app.py"
    assert manifest.entry_command == "uv run --project /app app.py"
    assert manifest.lock_metadata()["entry_file"] == "app.py"
    assert manifest.lock_metadata()["entry_command"] == manifest.entry_command

    task = Task(content / "task")
    assert task.config.environment.docker_image == manifest.pack.image
    assert task.config.agent.user == "agent"
    assert [step.min_reward for step in task.config.steps or []] == [None, None]
    assert not (content / "task" / "environment" / "Dockerfile").exists()
    assert not list((content / "task").rglob("test_checkpoint_*"))
    assert not list((content / "task").rglob("solution"))

    loaded = store.load(manifest.problem_id)  # digests, dependency pins and hidden content
    bundle = loaded.bundle.manifest
    assert bundle.entry_file == manifest.entry_file
    assert bundle.grader.base_image == manifest.pack.image
    assert [step.test_files for step in bundle.steps] == [
        ("test_checkpoint_1.py",),
        ("test_checkpoint_1.py", "test_checkpoint_2.py"),
    ]
    names = sorted(p.name for p in loaded.references_dir.iterdir())
    assert names == ["checkpoint_1", "checkpoint_2"]
    assert VERIFIER_IMPORT_PATH == "raidar.grading.verifier:RaidarVerifier"

    for step in manifest.steps:
        upstream = (repo / "adder" / f"{step.name}.md").read_bytes().decode()
        expected = upstream.replace("%%%ENTRYPOINT:entry_command%%%", manifest.entry_command or "")
        expected = expected.replace("%%%ENTRYPOINT:entry_file%%%", "app.py")
        rendered = (content / "task" / "steps" / step.name / "instruction.md").read_bytes()
        assert rendered == expected.encode()


def test_reimport_reproduces_the_manifest_and_refuses_drift_unless_updating(
    scripted: tuple[ProblemStore, Path, ScriptedSource],
) -> None:
    store, _, _ = scripted
    problem = _ref()
    first = _import(scripted)
    marker = first.content_dir / "task" / "task.toml"
    inode = marker.stat().st_ino
    again = _import(scripted)
    assert not again.changed
    assert again.manifest == first.manifest
    assert marker.stat().st_ino == inode  # matching content is left in place for live readers

    marker.write_text("tampered\n")
    repaired = _import(scripted)
    assert not repaired.changed
    assert store.load(problem).manifest == first.manifest

    path = store.manifest_path(problem)
    recorded = json.loads(path.read_text())
    recorded["task_sha256"] = "0" * 64
    path.write_text(json.dumps(recorded))
    with pytest.raises(ProblemDriftError, match="task_sha256"):
        _import(scripted)
    updated = _import(scripted, update=True)
    assert updated.changed
    assert updated.manifest == first.manifest


def test_one_shot_import_keeps_only_the_first_checkpoint(
    scripted: tuple[ProblemStore, Path, ScriptedSource],
) -> None:
    store, _, _ = scripted
    result = _import(scripted, _ref("scb/adder.checkpoint_1"))
    assert result.manifest.mode is ProblemMode.ONE_SHOT
    assert result.manifest.checkpoints == 1
    loaded = store.load(result.manifest.problem_id)
    assert [step.name for step in Task(loaded.task_dir).config.steps or []] == ["checkpoint_1"]
    tests = sorted(p.name for p in loaded.bundle.tests_dir.glob("test_*"))
    assert tests == ["test_checkpoint_1.py"]
    with pytest.raises(ValueError, match="first checkpoint"):
        _import(scripted, _ref("scb/adder.checkpoint_2"))


def test_import_refuses_sources_that_are_not_exactly_the_pin(
    scripted: tuple[ProblemStore, Path, ScriptedSource], tmp_path: Path
) -> None:
    store, repo, _ = scripted
    with pytest.raises(ProblemStoreError, match="does not match"):
        _import(scripted, ProblemId.parse("scb/adder@000000000000"))
    with pytest.raises(ProblemStoreError, match="not pinned"):
        _import(scripted, ProblemId.parse("other/adder@000000000000"))
    with pytest.raises(SourceError, match="local changes"):
        importer.import_problem(
            store, _ref(), source_checkout=repo, run=ScriptedSource(repo, COMMIT, dirty="?? x")
        )
    with pytest.raises(SourceError, match="expected pinned commit"):
        importer.import_problem(
            store, _ref(), source_checkout=repo, run=ScriptedSource(repo, COMMIT, head="c" * 40)
        )
    with pytest.raises(ProblemStoreError, match="is not an SCB problem"):
        _import(scripted, _ref("scb/ghost"))


@pytest.mark.parametrize(
    ("converter", "message"),
    [("scripts/absent.py", "pinned converter scripts/absent.py not found"), ("../x.py", "inside")],
)
def test_import_runs_the_converter_the_pin_names(
    scripted: tuple[ProblemStore, Path, ScriptedSource], converter: str, message: str
) -> None:
    store = scripted[0]
    sources = store.root / "sources.yaml"
    sources.write_text(sources.read_text() + f"    converter: {converter}\n")
    with pytest.raises(SourceError, match=message):
        _import(scripted)


def test_import_surfaces_converter_failure_and_timeout(
    scripted: tuple[ProblemStore, Path, ScriptedSource],
) -> None:
    store, repo, _ = scripted
    for run, message in (
        (ScriptedSource(repo, COMMIT, converter_exit=1), "converter failed"),
        (ScriptedSource(repo, COMMIT, hang=True), "timed out"),
    ):
        with pytest.raises(SourceError, match=message):
            importer.import_problem(
                store, _ref(), source_checkout=repo, resolver=FakeResolver(), run=run
            )
    assert not store.manifest_path(_ref()).exists()


def test_default_import_clones_the_pinned_commit_into_the_store_cache(
    scripted: tuple[ProblemStore, Path, ScriptedSource],
) -> None:
    store, _, run = scripted
    result = importer.import_problem(store, _ref(), resolver=FakeResolver(), run=run)
    assert (store.root / "cache" / "sources" / f"scb-{COMMIT[:12]}" / ".git").exists()
    assert result.manifest.source.url == "file:///nowhere"


# ---- the problem store over the fixture store -----------------------------------------------


def test_loading_refuses_missing_or_drifted_content(store: ProblemStore) -> None:
    problem = ProblemId.parse(POSITIVE_REF)
    loaded = store.load(problem)
    (loaded.task_dir / "steps" / "checkpoint_1" / "instruction.md").write_text("tampered\n")
    with pytest.raises(ProblemStoreError, match="task_sha256"):
        store.load(problem)
    shutil.rmtree(store.content_dir(problem, store.load_manifest(problem).content_key()))
    with pytest.raises(ProblemStoreError, match="raidar problem import"):
        store.load(problem)
    with pytest.raises(ProblemStoreError, match="not imported"):
        store.load(ProblemId.parse("fixture/absent@fefefefefefe"))


@pytest.mark.parametrize("area", ["task", "oracle", "references"])
@pytest.mark.parametrize("excluded_dir", ["build", "dist", "__pycache__", ".venv"])
def test_content_digests_cover_directories_a_workspace_snapshot_excludes(
    store: ProblemStore, area: str, excluded_dir: str
) -> None:
    problem = ProblemId.parse(POSITIVE_REF)
    content = store.content_dir(problem, store.load_manifest(problem).content_key())
    extra = content / area / excluded_dir / "extra.txt"
    extra.parent.mkdir(parents=True)
    extra.write_text("not in the manifest\n")
    with pytest.raises(ProblemStoreError, match="drifted"):
        store.load(problem)


def test_loading_refuses_a_task_that_exposes_hidden_tests_even_with_matching_digests(
    store: ProblemStore,
) -> None:
    problem = ProblemId.parse(POSITIVE_REF)
    loaded = store.load(problem)
    leaked = loaded.task_dir / "build" / "test_checkpoint_2.py"
    leaked.parent.mkdir()
    shutil.copy2(loaded.bundle.tests_dir / "test_checkpoint_2.py", leaked)
    manifest = loaded.manifest.model_copy(update={"task_sha256": content_digest(loaded.task_dir)})
    store.write_manifest(manifest)  # a manifest re-recorded over leaked content
    with pytest.raises(ProblemStoreError, match="hidden verifier content"):
        store.load(problem)


def test_hidden_content_check_flags_renamed_copies_and_solutions(store: ProblemStore) -> None:
    loaded = store.load(ProblemId.parse(POSITIVE_REF))
    assert hidden_content_in_task(loaded.task_dir, loaded.bundle) == []
    shutil.copy2(loaded.bundle.tests_dir / "test_checkpoint_2.py", loaded.task_dir / "notes.txt")
    solution = loaded.task_dir / "steps" / "checkpoint_1" / "solution" / "solve.sh"
    solution.parent.mkdir()
    solution.write_text("#!/bin/bash\n")
    assert hidden_content_in_task(loaded.task_dir, loaded.bundle) == [
        "notes.txt",
        "steps/checkpoint_1/solution/solve.sh",
    ]


def test_manifests_without_an_entry_file_keep_their_recorded_bytes_and_digest(
    store: ProblemStore,
) -> None:
    manifests = sorted((store.root / "registry").rglob("*.json"))
    assert manifests
    for path in manifests:
        if path.name.endswith(".validation.json"):
            continue
        recorded = json.loads(path.read_text())
        loaded = ProblemManifest.model_validate(recorded)
        assert loaded.entry_file is None
        assert loaded.digest() == digest_json(recorded), path
        assert "entry_file" not in loaded.lock_metadata()


def test_the_store_refuses_malformed_sources_and_unpinned_datasets(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir()
    (root / "sources.yaml").write_text("sources: [1]\n")
    with pytest.raises(ProblemStoreError, match="invalid"):
        ProblemStore(root).sources()
    (root / "sources.yaml").write_text("sources:\n  scb: {url: u}\n")
    with pytest.raises(ProblemStoreError, match="invalid"):
        ProblemStore(root).sources()
    with pytest.raises(ProblemStoreError, match="invalid"):
        ProblemStore(tmp_path / "absent").sources()
    with pytest.raises(ProblemStoreError, match="is not pinned"):
        fake_store(tmp_path / "ok", COMMIT).pin("zzz")


def test_the_registry_lists_manifests_but_not_reports_and_refuses_strays(
    store: ProblemStore,
) -> None:
    problem = ProblemId.parse(POSITIVE_REF)
    store.validation_path(problem).write_text("{}")
    assert [str(p) for p in store.registered()] == sorted([POSITIVE_REF, NEGATIVE_REF, ONESHOT_REF])
    stray = store.root / "registry" / "BAD" / "p" / "abc.json"
    stray.parent.mkdir(parents=True)
    stray.write_text("{}")
    with pytest.raises(ProblemStoreError, match="unexpected registry entry"):
        store.registered()


def test_a_manifest_must_parse_and_belong_to_its_reference(store: ProblemStore) -> None:
    problem = ProblemId.parse(POSITIVE_REF)
    path = store.manifest_path(problem)
    other = store.manifest_path(ProblemId.parse(NEGATIVE_REF)).read_text()
    path.write_text("{}")
    with pytest.raises(ProblemStoreError, match="invalid manifest"):
        store.load_manifest(problem)
    path.write_text(other)
    with pytest.raises(ProblemStoreError, match="records"):
        store.load_manifest(problem)


def _record(store: ProblemStore, problem: ProblemId, **fields: Any) -> None:
    manifest = fields.pop("manifest", None) or store.load_manifest(problem)
    report = {
        "verdict": "validated_partial",
        "manifest_sha256": manifest.digest(),
        "not_run": ["native_parity_positive"],
        **fields,
    }
    store.validation_path(problem).write_text(json.dumps(report))


def test_only_a_usable_verdict_for_the_current_manifest_admits_a_problem(
    store: ProblemStore,
) -> None:
    problem = ProblemId.parse(POSITIVE_REF)
    manifest = store.load_manifest(problem)
    with pytest.raises(ProblemStoreError, match="not verifier-validated"):
        store.require_validated(problem, manifest)
    _record(store, problem, manifest_sha256="0" * 64)
    with pytest.raises(ProblemStoreError, match="re-validate"):
        store.require_validated(problem, manifest)
    _record(store, problem, not_run=["repeat_identical_positive"])
    with pytest.raises(ProblemStoreError, match="required checks not run: repeat_identical"):
        store.require_validated(problem, manifest)
    _record(store, problem, verdict="excluded", not_run=[])
    with pytest.raises(ProblemStoreError, match="not usable.*excluded"):
        store.require_validated(problem, manifest)
    _record(store, problem)
    assert store.require_validated(problem, manifest).usable
    unpinned = manifest.model_copy(update={"dependencies": None})
    _record(store, problem, manifest=unpinned)
    with pytest.raises(ProblemStoreError, match="declared-wheels"):
        store.require_validated(problem, unpinned)


def test_the_store_root_is_explicit_unless_the_environment_names_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "env"))
    assert store_root() == (tmp_path / "env").resolve()
    assert store_root(tmp_path / "x") == (tmp_path / "x").resolve()
    monkeypatch.delenv(ROOT_ENV)
    assert store_root() == problems_root()
    monkeypatch.delenv(VAR_ENV, raising=False)
    assert open_store(tmp_path / "elsewhere") == ProblemStore((tmp_path / "elsewhere").resolve())
    monkeypatch.setenv(VAR_ENV, str(tmp_path / "relocated"))
    lab = tmp_path / "repo" / "lab" / "problems"
    lab.mkdir(parents=True)
    assert open_store(lab).data == (tmp_path / "relocated" / "problems").resolve()


# ---- native runner over an injected runner -----------------------------------------------


class _Runs:
    """A scripted ``subprocess.run``: queued outcomes (results or exceptions), recorded calls."""

    def __init__(self, *outcomes: subprocess.CompletedProcess[str] | BaseException) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_the_native_evaluator_project_is_staged_beside_an_untouched_problem(
    tmp_path: Path,
) -> None:
    problem = tmp_path / "adder_x"
    problem.mkdir()
    (problem / "config.yaml").write_text("test_dependencies: [pyyaml==6.0.2]\n")
    run = _Runs(_cp())
    staged = stage_catalog(problem, tmp_path / "catalog", run=run)
    pyproject = (staged / "pyproject.toml").read_text()
    assert 'name = "adder-x-evaluator"' in pyproject
    assert '"pytest==8.4.1"' in pyproject
    assert '"pyyaml==6.0.2"' in pyproject
    assert run.calls[0][0] == ["uv", "lock", "-q"]
    assert run.calls[0][1]["cwd"] == staged
    assert (problem / "config.yaml").read_text() == "test_dependencies: [pyyaml==6.0.2]\n"
    assert not (problem / "pyproject.toml").exists()
    (problem / "config.yaml").write_text("{}\n")
    with pytest.raises(NativeRunnerError, match="uv lock failed.*no index"):
        stage_catalog(problem, tmp_path / "catalog", run=_Runs(_cp(1, err="no index")))


def test_native_revision_names_the_fork_only_when_it_is_the_work_tree_root(
    tmp_path: Path,
) -> None:
    def revision(*outcomes: subprocess.CompletedProcess[str]) -> dict[str, object]:
        return NativeRunner(tmp_path, "ctx", _Runs(*outcomes)).revision()

    top = _cp(0, f"{tmp_path}\n")
    assert revision(top, _cp(0, "abc\n"), _cp(0, " M f\n")) == {"commit": "abc", "dirty": True}
    assert revision(top, _cp(0, "abc\n"), _cp(0, "")) == {"commit": "abc", "dirty": False}
    assert revision(_cp(0, f"{tmp_path.parent}\n")) == {"commit": None}
    assert revision(_cp(128, err="not a repo")) == {"commit": None}


def _native(tmp_path: Path, run: Any) -> tuple[NativeRunner, Path, Path]:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "app.py").write_text("x")
    return NativeRunner(tmp_path / "fork", "ctx", run), snapshot, tmp_path / "out"


def _evaluating(content: str | None, *, host: str = "unix:///sock\n") -> Any:
    seen: list[dict[str, Any]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv[0] == "docker":
            return _cp(0, host)
        seen.append(kwargs)
        if content is not None:
            target = Path(argv[argv.index("-o") + 1])
            target.mkdir(parents=True)
            (target / "evaluation.json").write_text(content)
        return _cp(0, "out", "err")

    run.seen = seen  # type: ignore[attr-defined]
    return run


def test_a_native_grade_reduces_the_evaluation_the_fork_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCKER_CONTEXT", "ambient")
    evaluation = {
        "pass_counts": {"Core": 1},
        "total_counts": {"Core": 1},
        "tests": {"checkpoint_1-Core": {"passed": ["t"]}},
    }
    run = _evaluating(json.dumps(evaluation))
    runner, snapshot, out = _native(tmp_path, run)
    grade = runner.grade(tmp_path / "catalog", "adder", snapshot, 1, out)
    assert (grade.step, grade.outcomes, grade.infra_failure) == (
        "checkpoint_1",
        {"checkpoint_1::t": "passed"},
        None,
    )
    env = run.seen[0]["env"]
    assert env["DOCKER_HOST"] == "unix:///sock"
    assert "DOCKER_CONTEXT" not in env
    assert env["SCBENCH_PROBLEMS_PATH"] == str(tmp_path / "catalog")
    assert "--- stderr\nerr" in (out / "native.log").read_text()


@pytest.mark.parametrize(
    ("content", "infra"),
    [
        (None, "native_no_evaluation"),
        ("not json", "native_malformed_evaluation"),
        ("{}", "native_malformed_evaluation"),
        ("[]", "native_malformed_evaluation"),
    ],
)
def test_a_native_run_without_a_usable_evaluation_is_infrastructure(
    tmp_path: Path, content: str | None, infra: str
) -> None:
    runner, snapshot, out = _native(tmp_path, _evaluating(content))
    grade = runner.grade(tmp_path / "catalog", "adder", snapshot, 2, out)
    assert (grade.step, grade.infra_failure, grade.outcomes) == ("checkpoint_2", infra, {})


def test_a_native_timeout_is_infrastructure_and_an_unreachable_docker_is_refused(
    tmp_path: Path,
) -> None:
    timeout = subprocess.TimeoutExpired(["slop-code"], 1)
    runner, snapshot, out = _native(tmp_path, _Runs(_cp(0, "unix:///s\n"), timeout))
    grade = runner.grade(tmp_path / "catalog", "adder", snapshot, 1, out)
    assert grade.infra_failure == "native_timeout"
    assert "timed out" in (out / "native.log").read_text()
    for host in (_cp(1, err="no context"), _cp(0, "\n")):
        runner, snapshot, out = _native(tmp_path / "again", _Runs(host))
        with pytest.raises(NativeRunnerError, match="not available"):
            runner.grade(tmp_path / "catalog", "adder", snapshot, 1, out)


# ---- the public CLI over the fixture store -------------------------------------------------


def _invoke(*args: str) -> Any:
    return CliRunner().invoke(main, ["problem", *args])


def test_list_and_info_report_each_problem_and_its_state(store: ProblemStore) -> None:
    listed = _invoke("list", "--store", str(store.root))
    assert listed.exit_code == 0, listed.output
    for ref in (POSITIVE_REF, NEGATIVE_REF, ONESHOT_REF):
        assert ref in listed.output
    assert "validation: not validated" in listed.output
    assert "errata: none" in listed.output
    assert "store: imported" in listed.output

    info = _invoke("info", POSITIVE_REF, "--store", str(store.root))
    assert info.exit_code == 0, info.output
    assert "checkpoints: 2" in info.output
    assert "steps: checkpoint_1, checkpoint_2" in info.output
    assert "validation: not validated" in info.output
    assert "entry_file" not in info.output
    assert "store: imported" in info.output


def test_a_validation_record_is_shown_as_usable_stale_or_absent(store: ProblemStore) -> None:
    problem = ProblemId.parse(POSITIVE_REF)
    _record(store, problem)
    shown = _invoke("info", POSITIVE_REF, "--store", str(store.root)).output
    assert "validation: validated_partial (usable; not run: native_parity_positive)" in shown
    _record(store, problem, manifest_sha256="0" * 64)
    assert (
        "(validated_partial for another manifest)"
        in _invoke("list", "--store", str(store.root)).output
    )
    _record(store, problem, verdict="excluded", not_run=[])
    info = _invoke("info", POSITIVE_REF, "--store", str(store.root)).output
    assert "excluded (not usable)" in info


def test_list_of_an_empty_store_and_info_of_an_unimported_problem_say_so(tmp_path: Path) -> None:
    (tmp_path / "sources.yaml").write_text("sources: {}\n")
    assert "No problems registered" in _invoke("list", "--store", str(tmp_path)).output
    missing = _invoke("info", "fixture/absent@fefefefefefe", "--store", str(tmp_path))
    assert missing.exit_code == 1
    assert "not imported" in missing.output
    assert _invoke("info", "nonsense", "--store", str(tmp_path)).exit_code == 2


def test_import_and_validate_refuse_ambiguous_or_missing_selections(store: ProblemStore) -> None:
    where = ["--store", str(store.root)]
    for command in ("import", "validate"):
        none = _invoke(command, *where)
        assert none.exit_code == 1
        assert "provide a problem reference" in none.output
        both = _invoke(command, POSITIVE_REF, "--problems", "all", *where)
        assert both.exit_code == 1
        assert "not both" in both.output
        assert _invoke(command, "nonsense", *where).exit_code == 2
    bulk = _invoke("import", "--problems", "all", *where)
    assert bulk.exit_code == 1
    assert "requires --source" in bulk.output
    assert _invoke("validate", POSITIVE_REF, "--repeats", "0", *where).exit_code == 2


def test_native_evaluation_reduces_to_the_same_case_keys_as_the_oracle() -> None:
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
    flagged = grade_from_evaluation(
        {"pass_counts": {}, "total_counts": {}, "tests": {}, "infrastructure_failure": True}, "s"
    )
    assert flagged.infra_failure == "native_infrastructure_failure"


async def test_an_unknown_engine_is_refused_before_any_work(store: ProblemStore) -> None:
    options = ValidateOptions("ctx", engine="x")
    with pytest.raises(ValueError, match="engine must be one of"):
        await validate_problem(store, ProblemId.parse(POSITIVE_REF), options)
