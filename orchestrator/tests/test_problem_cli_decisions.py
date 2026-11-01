"""``raidar problem`` command decisions: output, refusals and exit codes, with injected shells.

The store is a tiny on-disk registry; the pinned-source checkout, importer and validator are
injected, so nothing here clones, converts, grades or starts a process.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

import raidar.cli.problem as problem_cli
from raidar.cli import main
from raidar.design.problem import catalog
from raidar.design.problem.errata import draft_errata
from raidar.design.problem.importer import ImportResult
from raidar.design.problem.models import (
    PackBinding,
    ProblemId,
    ProblemManifest,
    ProblemMode,
    ProblemSource,
    ProblemStep,
)
from raidar.design.problem.store import ProblemStore, ProblemStoreError
from raidar.design.problem.validation import (
    Check,
    CheckStatus,
    StepSolvableRecord,
    ValidationRecord,
    ValidationRecordError,
    Verdict,
)
from tests.problems_support import FIXTURE_COMMIT, FIXTURE_VERSION, POSITIVE_REF

OTHER = ProblemId.parse(f"fixture/other@{FIXTURE_VERSION}")
POSITIVE = ProblemId.parse(POSITIVE_REF)


def _step(name: str) -> ProblemStep:
    return ProblemStep(
        name=name, checkpoint=name, instruction_sha256="1" * 64, test_files=(f"test_{name}.py",)
    )


@pytest.fixture(name="manifest")
def manifest_fixture() -> ProblemManifest:
    return ProblemManifest(
        ref=POSITIVE_REF,
        mode=ProblemMode.MULTI_CHECKPOINT,
        checkpoints=2,
        steps=(_step("checkpoint_1"), _step("checkpoint_2")),
        source=ProblemSource(
            dataset="fixture",
            url="fixture://adder",
            commit=FIXTURE_COMMIT,
            problem="adder",
            checkpoint_selection=None,
            problem_tree="1" * 40,
            converter_tree="2" * 40,
            converted_task_sha256="3" * 64,
        ),
        pack=PackBinding(id="python", version=1, image="example/python:1"),
        task_sha256="4" * 64,
        bundle_sha256="5" * 64,
        references_sha256="6" * 64,
    )


@pytest.fixture(name="light")
def light_fixture(tmp_path: Path, manifest: ProblemManifest) -> ProblemStore:
    """A store holding only the positive manifest: nothing imported, nothing validated."""
    store = ProblemStore(tmp_path / "light")
    store.write_manifest(manifest)
    return store


@pytest.fixture(name="fixture_store")
def fixture_store_fixture(light: ProblemStore, manifest: ProblemManifest) -> ProblemStore:
    """The positive problem imported (its task content present) beside a second registered one."""
    (light.content_dir(POSITIVE, manifest.content_key()) / "task").mkdir(parents=True)
    negative = manifest.model_copy(update={"ref": _ref("adder_negative")})
    light.write_manifest(negative)
    return light


@pytest.fixture(name="runner")
def runner_fixture() -> CliRunner:
    return CliRunner()


def _record(manifest: ProblemManifest, verdict: Verdict = Verdict.VALIDATED, **kw: Any) -> Any:
    return ValidationRecord(verdict, kw.pop("sha", manifest.digest()), kw.pop("not_run", ()), **kw)


def _invoke(runner: CliRunner, *args: str) -> Any:
    return runner.invoke(main, ["problem", *args])


def _no_record(path: Path) -> ValidationRecord:
    raise ValidationRecordError(f"no validation report at {path}")


def _catalog_dir(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "upstream"
    (root / "adder").mkdir(parents=True)
    (root / "adder" / "config.yaml").write_text("entry_file: app\n")
    for step in ("checkpoint_1", "checkpoint_2"):
        (root / "adder" / f"{step}.md").write_text("spec\n")
    pin = SimpleNamespace(url="file:///nowhere", commit="ab" * 20)
    return SimpleNamespace(root=root, pin=pin)


# ---- list / info ----------------------------------------------------------------------------


def test_list_reports_an_empty_registry(tmp_path: Path, runner: CliRunner) -> None:
    result = _invoke(runner, "list", "--store", str(tmp_path / "empty"))
    assert result.exit_code == 0
    assert "No problems registered under" in result.output


def test_list_shows_each_problem_with_its_validation_errata_and_import_state(
    fixture_store: ProblemStore, runner: CliRunner
) -> None:
    result = _invoke(runner, "list", "--store", str(fixture_store.root))
    assert result.exit_code == 0, result.output
    assert POSITIVE_REF in result.output
    assert "  validation: not validated" in result.output
    assert "  errata: none" in result.output
    assert "  store: imported" in result.output


def test_list_marks_a_manifest_without_content_as_not_imported(
    light: ProblemStore, runner: CliRunner
) -> None:
    result = _invoke(runner, "list", "--store", str(light.root))
    assert "  store: not imported" in result.output


def test_list_refuses_an_unreadable_manifest(tmp_path: Path, runner: CliRunner) -> None:
    store = ProblemStore(tmp_path / "broken")
    path = store.manifest_path(POSITIVE)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    result = _invoke(runner, "list", "--store", str(store.root))
    assert result.exit_code == 1
    assert result.output.startswith("Error:")


def test_list_of_a_source_separates_registered_from_unimported_problems(
    light: ProblemStore, tmp_path: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _catalog_dir(tmp_path)
    available = (POSITIVE, OTHER)
    monkeypatch.setattr(
        problem_cli, "source_catalog", lambda store, source, checkout: (upstream, available)
    )
    (upstream.root / OTHER.problem).mkdir()
    result = _invoke(runner, "list", "--store", str(light.root), "--source", "scb@x")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "PROBLEM  CHECKPOINTS  STATE"
    assert f"{POSITIVE}  2  registered" in lines
    assert f"{OTHER}  0  not imported" in lines


def test_list_of_a_source_surfaces_a_source_failure(
    light: ProblemStore, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(store: ProblemStore, source: str, checkout: Path | None) -> None:
        raise ProblemStoreError("source must match pinned scb@abc")

    monkeypatch.setattr(problem_cli, "source_catalog", refuse)
    result = _invoke(runner, "list", "--store", str(light.root), "--source", "scb@other")
    assert result.exit_code == 1
    assert "source must match pinned scb@abc" in result.output


def test_info_shows_the_manifest_with_entry_file_and_recorded_verdict(
    light: ProblemStore, manifest: ProblemManifest, runner: CliRunner, monkeypatch: Any
) -> None:
    light.write_manifest(manifest.model_copy(update={"entry_file": "app"}))
    updated = light.load_manifest(POSITIVE)
    solvable = {
        "checkpoint_1": StepSolvableRecord(Verdict.VALIDATED, ("t.py::a", "t.py::b"), "d", 1),
        "checkpoint_2": StepSolvableRecord(Verdict.EXCLUDED, (), "d", 0, "no scoreable case"),
    }
    record = _record(
        updated,
        Verdict.VALIDATED_PARTIAL,
        not_run=("native_parity",),
        solvable=solvable,
        reference_observations={"slow": {"checkpoint_1": {"observation": "timeout"}, "x": 3}},
    )
    monkeypatch.setattr(problem_cli, "read_validation_record", lambda path: record)
    result = _invoke(runner, "info", POSITIVE_REF, "--store", str(light.root))
    assert result.exit_code == 0, result.output
    out = result.output
    assert "  entry_file: app" in out
    assert "validation: validated_partial (usable; not run: native_parity)" in out
    assert "checkpoint_1: validated solvable=2 nondeterministic=1" in out
    assert "checkpoint_2: excluded solvable=0 nondeterministic=0 (no scoreable case)" in out
    assert "slow checkpoint_1: timeout" in out
    assert "slow x" not in out
    assert "store: not imported" in out


def test_info_flags_a_validation_report_for_another_manifest_as_stale(
    light: ProblemStore, manifest: ProblemManifest, runner: CliRunner, monkeypatch: Any
) -> None:
    record = _record(manifest, Verdict.EXCLUDED, sha="0" * 64)
    monkeypatch.setattr(problem_cli, "read_validation_record", lambda path: record)
    result = _invoke(runner, "info", POSITIVE_REF, "--store", str(light.root))
    assert "validation: stale (excluded for another manifest)" in result.output
    assert "solvable" not in result.output


def test_info_says_when_a_recorded_verdict_carries_no_solvable_sets(
    light: ProblemStore, manifest: ProblemManifest, runner: CliRunner, monkeypatch: Any
) -> None:
    record = _record(manifest, Verdict.EXCLUDED)
    monkeypatch.setattr(problem_cli, "read_validation_record", lambda path: record)
    result = _invoke(runner, "info", POSITIVE_REF, "--store", str(light.root))
    assert "validation: excluded (not usable)" in result.output
    assert "solvable sets: unavailable" in result.output


def test_info_reports_a_registered_errata_and_refuses_a_stale_one(
    light: ProblemStore, manifest: ProblemManifest, runner: CliRunner
) -> None:
    audit = {
        "audit_version": 1,
        "flagged": [
            {
                "case": "t.py::a",
                "checkpoint_origin": "checkpoint_1",
                "assertions": [{"rule": "R1", "line": 3, "why": "w", "assertion": "a"}],
            }
        ],
    }
    text = draft_errata(str(POSITIVE), manifest.digest(), audit, registered="2026-01-01")
    path = light.errata_path(POSITIVE)
    path.write_text(text.replace("TODO", "because"))
    ok = _invoke(runner, "info", POSITIVE_REF, "--store", str(light.root))
    assert "sha256" in ok.output
    assert "(1 exclude-from-solved, 0 informational)" in ok.output
    path.write_text(text)  # the drafted placeholder reason is refused
    drafted = _invoke(runner, "info", POSITIVE_REF, "--store", str(light.root))
    assert "errata: invalid (" in drafted.output
    assert "drafted placeholder reason" in drafted.output


def test_info_refuses_a_malformed_reference_and_an_unregistered_problem(
    light: ProblemStore, runner: CliRunner
) -> None:
    malformed = _invoke(runner, "info", "not-a-ref", "--store", str(light.root))
    assert malformed.exit_code == 2
    assert "REF" in malformed.output
    missing = _invoke(runner, "info", str(OTHER), "--store", str(light.root))
    assert missing.exit_code == 1
    assert missing.output.startswith("Error:")


def test_info_of_a_source_problem_prints_its_checkpoints_and_config(
    light: ProblemStore, tmp_path: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _catalog_dir(tmp_path)
    monkeypatch.setattr(
        problem_cli, "source_catalog", lambda store, source, checkout: (upstream, (POSITIVE,))
    )
    result = _invoke(runner, "info", POSITIVE_REF, "--store", str(light.root), "--source", "scb@x")
    assert result.exit_code == 0, result.output
    assert "  checkpoints: 2" in result.output
    assert "  steps: checkpoint_1, checkpoint_2" in result.output
    assert f"  source: file:///nowhere@{'ab' * 20}" in result.output
    assert "entry_file: app" in result.output
    absent = _invoke(runner, "info", str(OTHER), "--store", str(light.root), "--source", "scb@x")
    assert absent.exit_code == 1
    assert f"{OTHER} is not in scb@x" in absent.output


# ---- import ---------------------------------------------------------------------------------


def _imported(
    manifest: ProblemManifest, tmp_path: Path, *, changed: bool, refusal: Any = None
) -> ImportResult:
    pins = SimpleNamespace(policy="declared-wheels/1", sets=("a", "b"), exclude_newer="2026-01-01")
    shown = SimpleNamespace(
        ref=manifest.ref,
        mode=manifest.mode,
        checkpoints=manifest.checkpoints,
        source=manifest.source,
        digest=manifest.digest,
        dependencies=None if refusal else pins,
        dependency_refusal=refusal,
    )
    return SimpleNamespace(  # type: ignore[return-value]
        manifest=shown,
        manifest_path=tmp_path / "m.json",
        content_dir=tmp_path / "c",
        changed=changed,
    )


def test_import_reports_imported_and_verified_problems_with_their_pins(
    light: ProblemStore,
    manifest: ProblemManifest,
    tmp_path: Path,
    runner: CliRunner,
    monkeypatch: Any,
) -> None:
    outcomes = iter([True, False])

    def fake(store: ProblemStore, problem: ProblemId, **kw: Any) -> ImportResult:
        return _imported(manifest, tmp_path, changed=next(outcomes))

    monkeypatch.setattr(problem_cli, "import_problem", fake)
    first = _invoke(runner, "import", POSITIVE_REF, "--store", str(light.root), "--update")
    again = _invoke(runner, "import", POSITIVE_REF, "--store", str(light.root))
    assert f"{POSITIVE_REF}: imported" in first.output
    assert f"{POSITIVE_REF}: verified (matches manifest)" in again.output
    assert f"sha256: {manifest.digest()}" in first.output
    assert "  dependencies:" in first.output
    assert "excluded:" not in first.output


def test_import_reports_a_problem_excluded_for_its_dependencies(
    light: ProblemStore,
    manifest: ProblemManifest,
    tmp_path: Path,
    runner: CliRunner,
    monkeypatch: Any,
) -> None:
    refusal = SimpleNamespace(reason="unprovided", detail="numpy has no wheel")
    monkeypatch.setattr(
        problem_cli,
        "import_problem",
        lambda *a, **kw: _imported(manifest, tmp_path, changed=True, refusal=refusal),
    )
    result = _invoke(runner, "import", POSITIVE_REF, "--store", str(light.root))
    assert result.exit_code == 0
    assert "  excluded: unprovided: numpy has no wheel" in result.output
    assert "  dependencies:" not in result.output


def test_import_stops_at_the_first_problem_that_cannot_be_imported(
    light: ProblemStore, runner: CliRunner, monkeypatch: Any
) -> None:
    def refuse(store: ProblemStore, problem: ProblemId, **kw: Any) -> ImportResult:
        raise ProblemStoreError("content differs from the manifest; pass --update")

    monkeypatch.setattr(problem_cli, "import_problem", refuse)
    result = _invoke(runner, "import", POSITIVE_REF, "--store", str(light.root))
    assert result.exit_code == 1
    assert f"Error: {POSITIVE_REF}: content differs from the manifest" in result.output


def test_import_selection_refusals(light: ProblemStore, runner: CliRunner) -> None:
    store = str(light.root)
    mixed = _invoke(runner, "import", POSITIVE_REF, "--problems", "all", "--store", store)
    assert "use references or --problems/--source, not both" in mixed.output
    nothing = _invoke(runner, "import", "--store", store)
    assert "provide a problem reference or --problems" in nothing.output
    bulk = _invoke(runner, "import", "--problems", "all", "--store", store)
    assert "bulk import requires --source" in bulk.output
    bad = _invoke(runner, "import", "nope", "--store", store)
    assert bad.exit_code == 2


def test_bulk_import_takes_the_named_problems_from_the_source(
    light: ProblemStore,
    manifest: ProblemManifest,
    tmp_path: Path,
    runner: CliRunner,
    monkeypatch: Any,
) -> None:
    upstream = _catalog_dir(tmp_path)
    monkeypatch.setattr(
        catalog, "source_catalog", lambda store, source, checkout, run=None: (upstream, (POSITIVE,))
    )
    imported: list[ProblemId] = []

    def fake(store: ProblemStore, problem: ProblemId, **kw: Any) -> ImportResult:
        imported.append(problem)
        return _imported(manifest, tmp_path, changed=True)

    monkeypatch.setattr(problem_cli, "import_problem", fake)
    args = ("import", "--store", str(light.root), "--source", "scb@x")
    ok = _invoke(runner, *args, "--problems", "adder")
    unknown = _invoke(runner, *args, "--problems", "ghost")
    assert ok.exit_code == 0, ok.output
    assert imported == [POSITIVE]
    assert unknown.exit_code == 1
    assert "'ghost' must identify one pinned problem" in unknown.output


# ---- validate -------------------------------------------------------------------------------


@pytest.fixture(name="validating")
def validating_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, docker_target_offline: None
) -> SimpleNamespace:
    """Inject the validator, report writer and report reader; record what each run was given."""
    state = SimpleNamespace(
        reports={},
        failures={},
        options=[],
        written=[],
        record=None,
        audit=None,
        merged=None,
    )

    async def validate(store: ProblemStore, problem: ProblemId, options: Any) -> Any:
        state.options.append(options)
        if str(problem) in state.failures:
            raise state.failures[str(problem)]
        return state.reports[str(problem)]

    async def audit(store: ProblemStore, problem: ProblemId, options: Any) -> Any:
        state.options.append(options)
        if isinstance(state.audit, Exception):
            raise state.audit
        return state.audit

    def merge(store: ProblemStore, problem: ProblemId, audit_: Any) -> Path:
        if isinstance(state.merged, Exception):
            raise state.merged
        return state.merged

    def write(report: Any, path: Path) -> Path:
        state.written.append(path)
        return path

    monkeypatch.setattr("raidar.cli.target_options.sweep_grader_orphans", lambda context: None)
    monkeypatch.setattr(problem_cli, "validate_problem", validate)
    monkeypatch.setattr(problem_cli, "audit_problem", audit)
    monkeypatch.setattr(problem_cli, "merge_audit", merge)
    monkeypatch.setattr(problem_cli, "write_report", write)
    monkeypatch.setattr(problem_cli, "default_report_path", lambda s, r: tmp_path / "default.json")
    monkeypatch.setattr(problem_cli, "read_validation_record", lambda path: state.record)
    return state


def _report(ref: str, verdict: Verdict, checks: tuple[Check, ...] = (), audit: Any = None) -> Any:
    return SimpleNamespace(
        problem=ref,
        verdict=verdict,
        checks=list(checks),
        spec_determinacy=audit,
        to_json=lambda: {"headline_policy": {"metric": "solved"}},
    )


def _ref(name: str) -> str:
    return f"fixture/{name}@{FIXTURE_VERSION}"


def test_validated_problem_prints_checks_audit_flags_and_exits_zero(
    fixture_store: ProblemStore, manifest: ProblemManifest, runner: CliRunner, validating: Any
) -> None:
    audit = {
        "summary": {"flagged": 1},
        "dynamic": {"status": "measured"},
        "flagged": [
            {"case": "t.py::a", "assertions": [{"rule": "R2"}, {"rule": "R1"}, {"rule": "R1"}]}
        ],
    }
    checks = (
        Check("positive", CheckStatus.PASS),
        Check("repeats", CheckStatus.FAIL, ("repeat 1 differs", "repeat 2 differs")),
        Check("dependency", CheckStatus.INFRA),
        Check("native", CheckStatus.NOT_RUN, ("native runner not run",)),
    )
    validating.reports[POSITIVE_REF] = _report(POSITIVE_REF, Verdict.VALIDATED, checks, audit)
    validating.record = _record(
        manifest,
        solvable={"checkpoint_1": StepSolvableRecord(Verdict.VALIDATED, ("t.py::a",), "d", 0)},
    )
    result = _invoke(runner, "validate", POSITIVE_REF, "--store", str(fixture_store.root))
    assert result.exit_code == 0, result.output
    out = result.output
    assert "[PASS] positive" in out
    assert "[FAIL] repeats" in out
    assert "[INFRA] dependency" in out
    assert "[SKIP] native" in out
    assert "    repeat 2 differs" in out
    assert 'headline metric: {"metric": "solved"}' in out
    assert 'spec-determinacy audit (measured): {"flagged": 1}' in out
    assert "  [FLAG] t.py::a (R1, R2)" in out
    assert f"{POSITIVE_REF}: VALIDATED" in out
    assert "  checkpoint_1: validated solvable=1 nondeterministic=0" in out
    assert out.rstrip().endswith(f"PROBLEM  VERDICT\n{POSITIVE_REF}  validated")


def test_excluded_problem_exits_one_and_keeps_going_for_the_others(
    fixture_store: ProblemStore, manifest: ProblemManifest, runner: CliRunner, validating: Any
) -> None:
    validating.reports[_ref("adder")] = _report(_ref("adder"), Verdict.VALIDATED_PARTIAL)
    validating.reports[_ref("adder_negative")] = _report(_ref("adder_negative"), Verdict.EXCLUDED)
    validating.record = _record(manifest)
    args = ["--problems", "adder,adder_negative", "--concurrency", "4", "--no-audit"]
    result = _invoke(runner, "validate", "--store", str(fixture_store.root), *args)
    assert result.exit_code == 1
    assert f"{_ref('adder')}  validated_partial" in result.output
    assert f"{_ref('adder_negative')}  excluded" in result.output
    assert "solvable sets: unavailable" in result.output
    # The target's capacity is divided between problems, not multiplied.
    assert [o.concurrency for o in validating.options] == [2, 2]


def test_infrastructure_failure_gives_no_verdict_and_exit_two(
    fixture_store: ProblemStore, manifest: ProblemManifest, runner: CliRunner, validating: Any
) -> None:
    validating.reports[_ref("adder")] = _report(_ref("adder"), Verdict.EXCLUDED)
    validating.failures[_ref("adder_negative")] = RuntimeError("docker went away")
    validating.record = _record(manifest)
    result = _invoke(
        runner, "validate", "--store", str(fixture_store.root), "--problems", "adder,adder_negative"
    )
    assert result.exit_code == 2
    assert f"{_ref('adder_negative')}: INFRA: docker went away" in result.output
    assert f"{_ref('adder_negative')}  infra_failure" in result.output


def test_an_infra_failure_verdict_in_a_report_exits_two(
    fixture_store: ProblemStore, manifest: ProblemManifest, runner: CliRunner, validating: Any
) -> None:
    validating.reports[POSITIVE_REF] = _report(POSITIVE_REF, Verdict.INFRA_FAILURE)
    validating.record = _record(manifest, Verdict.INFRA_FAILURE)
    result = _invoke(runner, "validate", POSITIVE_REF, "--store", str(fixture_store.root))
    assert result.exit_code == 2


def test_report_path_needs_exactly_one_problem_and_is_used_when_given(
    fixture_store: ProblemStore,
    manifest: ProblemManifest,
    tmp_path: Path,
    runner: CliRunner,
    validating: Any,
) -> None:
    store = str(fixture_store.root)
    many = _invoke(
        runner,
        "validate",
        "--store",
        store,
        "--problems",
        "all",
        "--report",
        str(tmp_path / "r.json"),
    )
    assert many.exit_code == 2
    assert "--report requires exactly one problem" in many.output
    validating.reports[POSITIVE_REF] = _report(POSITIVE_REF, Verdict.VALIDATED)
    validating.record = _record(manifest)
    target = tmp_path / "chosen" / "r.json"
    ok = _invoke(runner, "validate", POSITIVE_REF, "--store", store, "--report", str(target))
    assert ok.exit_code == 0, ok.output
    assert f"report: {target}" in ok.output


def test_validate_with_nothing_selected_is_refused(
    tmp_path: Path, runner: CliRunner, validating: Any
) -> None:
    result = _invoke(runner, "validate", "--store", str(tmp_path / "empty"), "--problems", "all")
    assert result.exit_code == 1
    assert "no problems selected" in result.output


def test_validate_refuses_a_malformed_reference_before_touching_the_target(
    fixture_store: ProblemStore, runner: CliRunner, validating: Any
) -> None:
    result = _invoke(runner, "validate", "nope", "--store", str(fixture_store.root))
    assert result.exit_code == 2


def test_audit_only_prints_the_audit_and_the_merged_report_path(
    fixture_store: ProblemStore, tmp_path: Path, runner: CliRunner, validating: Any
) -> None:
    validating.audit = {"summary": {"flagged": 0}, "dynamic": None, "flagged": []}
    validating.merged = tmp_path / "merged.json"
    result = _invoke(
        runner,
        "validate",
        POSITIVE_REF,
        "--store",
        str(fixture_store.root),
        "--audit-only",
        "--no-audit",
    )
    assert result.exit_code == 0, result.output
    assert 'spec-determinacy audit (None): {"flagged": 0}' in result.output
    assert f"report: {tmp_path / 'merged.json'} (spec_determinacy updated" in result.output


@pytest.mark.parametrize(
    "error", [ProblemStoreError("no manifest"), ValidationRecordError("stale"), RuntimeError("x")]
)
def test_audit_only_infrastructure_failure_exits_two(
    fixture_store: ProblemStore, runner: CliRunner, validating: Any, error: Exception
) -> None:
    validating.audit = {"summary": {}}
    validating.merged = error
    result = _invoke(
        runner, "validate", POSITIVE_REF, "--store", str(fixture_store.root), "--audit-only"
    )
    assert result.exit_code == 2
    assert f"INFRA: {error}" in result.output


def test_audit_failure_before_merge_exits_two(
    fixture_store: ProblemStore, runner: CliRunner, validating: Any
) -> None:
    validating.audit = ValueError("no grader")
    result = _invoke(
        runner, "validate", POSITIVE_REF, "--store", str(fixture_store.root), "--audit-only"
    )
    assert result.exit_code == 2
    assert "INFRA: no grader" in result.output


# ---- errata-draft ---------------------------------------------------------------------------


def _draft(runner: CliRunner, store: ProblemStore, out: Path, ref: str = POSITIVE_REF) -> Any:
    store_arg = str(store.root)
    args = ["errata-draft", ref, "--store", store_arg, "--output", str(out)]
    return _invoke(runner, *args, "--registered", "2026-01-02")


def _write_report(store: ProblemStore, manifest: ProblemManifest, **fields: Any) -> None:
    body = {"manifest_sha256": manifest.digest(), **fields}
    store.validation_path(POSITIVE).write_text(json.dumps(body))


def test_errata_draft_writes_the_flagged_cases_with_placeholder_reasons(
    light: ProblemStore, manifest: ProblemManifest, tmp_path: Path, runner: CliRunner
) -> None:
    flagged = {
        "case": "t.py::a",
        "checkpoint_origin": "checkpoint_1",
        "assertions": [{"rule": "R1", "line": 2, "why": "w", "assertion": "a"}],
    }
    _write_report(light, manifest, spec_determinacy={"audit_version": 3, "flagged": [flagged]})
    out = tmp_path / "draft.yaml"
    result = _draft(runner, light, out)
    assert result.exit_code == 0, result.output
    assert f"wrote {out}: 1 flagged case(s)" in result.output
    text = out.read_text()
    assert "t.py::a" in text
    assert "TODO" in text
    assert "2026-01-02" in text


def test_errata_draft_counts_zero_when_nothing_was_flagged(
    light: ProblemStore, manifest: ProblemManifest, tmp_path: Path, runner: CliRunner
) -> None:
    _write_report(light, manifest, spec_determinacy={"audit_version": 3, "flagged": None})
    result = _draft(runner, light, tmp_path / "d.yaml")
    assert "0 flagged case(s)" in result.output


@pytest.mark.parametrize(
    "report",
    [
        {"manifest_sha256": "0" * 64, "spec_determinacy": {"flagged": []}},
        {"spec_determinacy": "absent"},
        {},
    ],
)
def test_errata_draft_refuses_a_report_without_a_current_audit(
    light: ProblemStore, tmp_path: Path, runner: CliRunner, report: dict[str, Any]
) -> None:
    light.validation_path(POSITIVE).write_text(json.dumps(report))
    out = tmp_path / "d.yaml"
    result = _draft(runner, light, out)
    assert result.exit_code == 1
    assert "run `raidar problem validate --audit-only` first" in result.output
    assert not out.exists()


def test_errata_draft_refuses_unreadable_inputs(
    light: ProblemStore, tmp_path: Path, runner: CliRunner
) -> None:
    out = tmp_path / "d.yaml"
    assert _draft(runner, light, out).exit_code == 1  # no validation report
    light.validation_path(POSITIVE).write_text("{broken")
    assert _draft(runner, light, out).exit_code == 1
    unregistered = _draft(runner, light, out, ref=str(OTHER))
    assert unregistered.exit_code == 1
    assert not out.exists()
    assert _draft(runner, light, out, ref="bad").exit_code == 2
