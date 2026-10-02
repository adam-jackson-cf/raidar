"""Spec-determinacy audit: static rules, reference variants and the combined report."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from click.testing import CliRunner

from raidar.cli import main as cli
from raidar.problems import variant_shim
from raidar.problems.audit import (
    DynamicEvidence,
    StepGradeSummary,
    audit_report,
    function_of,
    grade_audit_variants,
)
from raidar.problems.determinacy import (
    AssertionKind,
    HelperIndex,
    SpecText,
    classify,
    scan_test_file,
)
from raidar.problems.errata import ProblemErrata
from raidar.problems.models import ProblemId
from raidar.problems.validator import ValidateOptions, audit_problem, merge_audit
from raidar.problems.variants import build_variant, original_name, shim_source
from tests.audit_support import KV_REF, build_kv_problem
from tests.verifier_support import LocalProcessSandbox

SPEC = SpecText.from_markdown(
    [
        textwrap.dedent(
            """
            # Errors

            Errors print `error` and `detail`.

            ### Not Found Detail

            The detail identifies the requested key.

            A `CycleError` message must include the cycle path (e.g. `a -> b -> a`).
            """
        )
    ]
)


def _scan(body: str, helpers: str = "") -> list:
    index = HelperIndex.scan([textwrap.dedent(helpers)])
    return scan_test_file("test_x.py", textwrap.dedent(body), index)


def _rules(body: str, helpers: str = "") -> list[tuple[str, str, bool]]:
    return [
        (item.kind.value, classify(item, SPEC).rule, classify(item, SPEC).determined)
        for item in _scan(body, helpers)
    ]


# ---- static half ------------------------------------------------------------------


def test_error_text_rules_separate_stated_from_unstated_values() -> None:
    body = """
    def test_cases(argv):
        assert_error(run(argv, "get", "k1"), category="not_found", detail_contains="k1")
        assert_error(run(argv, ""), category="validation_error", detail_contains="key must be set")
        assert_error(run(argv, "bad-op"), category="invalid_operation", detail_contains="bad-op")
        assert_error(run(argv, "get", missing), category="not_found", detail=missing)
        assert_error(run(argv, "get", "x"), category="not_found", detail_contains="requested key")
    """
    assert _rules(body) == [
        ("error_text_contains", "content-stated", True),
        ("error_text_contains", "wording-unstated", False),
        ("error_text_contains", "content-unstated", False),
        ("error_text_equals", "exact-unstated", False),
        ("error_text_contains", "literal-in-spec", True),
    ]


def test_bare_asserts_on_error_text_are_traced_through_assignments() -> None:
    body = """
    def test_cycle(argv):
        error = assert_error_payload(run(argv), error_type="CycleError")
        message = error.get("message", "")
        text = str(message)
        assert "->" in text
        assert re.search(pattern, text)
        assert text == "cycle found"
        assert error.get("line") is None
    """
    found = _scan(body)
    assert [(item.kind, item.expected) for item in found] == [
        (AssertionKind.ERROR_TEXT_CONTAINS, "'->'"),
        (AssertionKind.ERROR_TEXT_PATTERN, "pattern"),
        (AssertionKind.ERROR_TEXT_EQUALS, "'cycle found'"),
    ]
    assert [classify(item, SPEC).rule for item in found] == [
        "literal-in-spec",
        "content-stated",
        "wording-unstated",
    ]
    assert all(item.category == "CycleError" for item in found)


def test_output_byte_assertions_come_from_helpers_and_direct_comparisons() -> None:
    helpers = """
    def assert_compact(result):
        assert result.stdout == json.dumps(json.loads(result.stdout), separators=(",", ":"))

    def assert_detail_contains(detail, expected):
        assert expected in detail

    def assert_no_newline(result):
        assert not result.stdout.endswith("\\n")
    """
    body = """
    def test_format(argv):
        assert_compact(run(argv))
        assert_no_newline(run(argv))
        assert run(argv).stdout == "{}"
        assert run(argv).stdout == ""
        assert_detail_contains(payload["detail"], "gamma")
    """
    found = _scan(body, helpers)
    assert [item.kind for item in found] == [
        AssertionKind.OUTPUT_BYTES,
        AssertionKind.OUTPUT_BYTES,
        AssertionKind.OUTPUT_BYTES,
        AssertionKind.ERROR_TEXT_CONTAINS,
    ]
    assert {classify(item, SPEC).rule for item in found[:3]} == {"format-unstated"}
    stated = SpecText.from_markdown(["Output is compact JSON with sorted keys."])
    assert classify(found[0], stated).rule == "format-stated"


def test_exact_detail_is_determined_only_by_an_exactness_statement() -> None:
    body = """
    def test_exact(argv):
        assert_error(run(argv, "get", key), category="not_found", detail=key)
    """
    (item,) = _scan(body)
    assert classify(item, SPEC).rule == "exact-unstated"
    exact = SpecText.from_markdown(["The `not_found` detail is exactly the requested key."])
    assert classify(item, exact).rule == "exact-stated"
    assert "the requested key" in classify(item, exact).why


def test_parametrized_inputs_and_unknown_categories() -> None:
    body = """
    @pytest.mark.parametrize("op", ["zap-op"])
    def test_op(argv, op):
        assert_error(run(argv, op), category=category, detail_contains="zap-op")
    """
    (item,) = _scan(body)
    assert item.input_derived
    assert item.category is None
    verdict = classify(item, SPEC)
    assert verdict.rule == "content-unstated"
    assert "<unknown category>" in verdict.why
    assert function_of("test_x.py::test_op[zap-op]") == "test_x.py::test_op"


# ---- variants -----------------------------------------------------------------------


def test_neutral_error_text_keeps_category_and_byte_format() -> None:
    compact = '{"detail":"name must be set","error":"validation_error"}'
    assert variant_shim.transform(compact, "neutral_error_text") == (
        '{"detail":"request rejected","error":"validation_error"}'
    )
    nested = json.dumps({"ok": False, "error": {"type": "X", "message": "m"}}, indent=2) + "\n"
    assert json.loads(variant_shim.transform(nested, "neutral_error_text")) == {
        "ok": False,
        "error": {"type": "X", "message": "request rejected"},
    }
    assert variant_shim.transform(nested, "neutral_error_text").endswith("}\n")
    assert variant_shim.transform('{"value": 1}', "neutral_error_text") == '{"value": 1}'
    assert variant_shim.transform("plain text\n", "neutral_error_text") == "plain text\n"
    odd = '{"error":"e",  "detail":"d"}'
    assert json.loads(variant_shim.transform(odd, "neutral_error_text"))["detail"] == (
        "request rejected"
    )


def test_reformatted_json_changes_only_whitespace() -> None:
    out = variant_shim.transform('{"b":1,"a":[1,2]}', "reformatted_json")
    assert out == '{\n  "b": 1,\n  "a": [\n    1,\n    2\n  ]\n}\n'
    assert variant_shim.transform(out, "reformatted_json") != out
    assert json.loads(variant_shim.transform(out, "reformatted_json")) == {"b": 1, "a": [1, 2]}
    with pytest.raises(ValueError, match="unknown variant"):
        variant_shim.transform("{}", "other")


def test_variant_shim_runs_the_reference_unchanged_but_rewrites_stdout(tmp_path: Path) -> None:
    reference = tmp_path / "ref"
    reference.mkdir()
    (reference / "app.py").write_text(
        "import json, sys\n"
        "data = sys.stdin.read()\n"
        "sys.stderr.write('warn')\n"
        "print(json.dumps({'error': 'e', 'detail': data + sys.argv[1]}), end='')\n"
        "sys.exit(3)\n"
    )
    variant = build_variant(reference, "app.py", "neutral_error_text", tmp_path / "variant")
    assert (variant / original_name("app.py")).read_text() == (reference / "app.py").read_text()
    proc = subprocess.run(
        [sys.executable, str(variant / "app.py"), "x"],
        input="in-",
        capture_output=True,
        text=True,
        check=False,
    )
    assert (proc.returncode, proc.stderr) == (3, "warn")
    assert json.loads(proc.stdout) == {"error": "e", "detail": "request rejected"}
    with pytest.raises(ValueError, match="unknown variant"):
        shim_source("app.py", "nope")
    with pytest.raises(FileNotFoundError):
        build_variant(reference, "missing.py", "reformatted_json", tmp_path / "v2")


def test_shim_main_propagates_exit_code_in_process(
    tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    target = tmp_path / "t.py"
    target.write_text("import sys; print('{\"a\":1}', end=''); sys.exit(4)\n")
    assert variant_shim.main(str(target), "reformatted_json", []) == 4
    assert capsysbinary.readouterr().out == b'{\n  "a": 1\n}\n'


# ---- combined audit (real grading, local sandbox) -------------------------------------------


@pytest.fixture
def kv(tmp_path: Path):
    return build_kv_problem(tmp_path / "problems")


def _audit(kv, tmp_path: Path) -> dict[str, Any]:
    _, problem = kv
    dynamic = asyncio.run(
        grade_audit_variants(
            problem,
            LocalProcessSandbox(tmp_path / "sandbox"),
            concurrency=1,
            work_root=tmp_path,
        )
    )
    return cast(dict[str, Any], audit_report(problem, dynamic))


def test_audit_flags_only_cases_a_conforming_variant_breaks_on_undetermined_values(
    kv, tmp_path: Path
) -> None:
    report = _audit(kv, tmp_path)
    flagged = {entry["case"]: entry for entry in report["flagged"]}
    assert set(flagged) == {
        "test_checkpoint_1.py::test_empty_key_wording",
        "test_checkpoint_1.py::test_output_is_compact",
        "test_checkpoint_2.py::test_put_conflict_detail_is_key[a]",
        "test_checkpoint_2.py::test_put_conflict_detail_is_key[b]",
    }
    wording = flagged["test_checkpoint_1.py::test_empty_key_wording"]
    assert wording["variants"] == ["neutral_error_text"]
    assert wording["assertions"][0]["rule"] == "wording-unstated"
    assert flagged["test_checkpoint_1.py::test_output_is_compact"]["variants"] == [
        "reformatted_json"
    ]
    assert (
        flagged["test_checkpoint_2.py::test_put_conflict_detail_is_key[a]"]["assertions"][0]["rule"]
        == "exact-unstated"
    )
    # The spec states the not_found detail, so breaking it is expected, not a finding.
    assert report["sensitive_determined"] == [
        {
            "case": "test_checkpoint_1.py::test_get_missing_names_key",
            "variants": ["neutral_error_text"],
        }
    ]
    assert report["summary"]["flagged_by_checkpoint"] == {"checkpoint_1": 2, "checkpoint_2": 2}
    steps = report["steps"]
    assert (
        steps["checkpoint_1"]["reference"]["passed"] == steps["checkpoint_1"]["reference"]["total"]
    )
    assert report["dynamic"]["status"] == "ran"


def test_audit_without_an_entry_file_reports_static_findings_only(kv, tmp_path: Path) -> None:
    _, problem = kv
    stripped = problem.manifest.model_copy(update={"entry_file": None})
    no_entry = type(problem)(stripped, problem.task_dir, problem.bundle, problem.references_dir)
    dynamic = asyncio.run(
        grade_audit_variants(no_entry, LocalProcessSandbox(tmp_path / "s"), concurrency=1)
    )
    report = audit_report(no_entry, dynamic)
    assert report["dynamic"]["status"] == "not_run"
    assert report["flagged"] == []
    assert {entry["rule"] for entry in report["static_undetermined"]} == {
        "wording-unstated",
        "format-unstated",
        "exact-unstated",
    }


def test_unconfirmed_and_unexplained_buckets(kv) -> None:
    _, problem = kv
    passed = {"test_checkpoint_1.py::test_empty_key_wording": "passed"}
    extra = {"test_checkpoint_1.py::test_get_known": "passed"}
    dynamic = DynamicEvidence(
        {
            "reference": {"checkpoint_1": StepGradeSummary(2, 2, None, {**passed, **extra})},
            "neutral_error_text": {
                "checkpoint_1": StepGradeSummary(
                    1, 2, None, {**passed, **dict.fromkeys(extra, "failed")}
                )
            },
            "reformatted_json": {"checkpoint_1": StepGradeSummary(0, 0, "sandbox_error", {})},
        }
    )
    report = audit_report(problem, dynamic)
    assert report["unconfirmed"] == [
        {
            "case": "test_checkpoint_1.py::test_empty_key_wording",
            "reason": "no variant changed its outcome",
        }
    ]
    assert report["sensitive_unexplained"] == [
        {"case": "test_checkpoint_1.py::test_get_known", "variants": ["neutral_error_text"]}
    ]
    assert report["steps"]["checkpoint_1"]["variants"]["reformatted_json"]["infra_failure"] == (
        "sandbox_error"
    )


def test_audit_only_merges_into_the_committed_report_and_drafts_errata(kv, tmp_path: Path) -> None:
    store, problem = kv
    problem_id = ProblemId.parse(KV_REF)
    committed = {
        "kind": "raidar-problem-validation",
        "manifest_sha256": problem.manifest.digest(),
        "verdict": "validated",
        "not_run": [],
        "checks": [{"name": "reference_k_passes_1..k", "status": "pass", "detail": []}],
    }
    store.validation_path(problem_id).write_text(json.dumps(committed))
    audit = asyncio.run(
        audit_problem(
            store,
            problem_id,
            ValidateOptions(concurrency=1),
            sandbox=LocalProcessSandbox(tmp_path / "sandbox"),
            work_root=tmp_path,
        )
    )
    path = merge_audit(store, problem_id, audit)
    merged = json.loads(path.read_text())
    assert {key: merged[key] for key in committed} == committed
    assert merged["spec_determinacy"]["summary"]["flagged"] == 4

    out = tmp_path / "draft.errata.yaml"
    result = CliRunner().invoke(
        cli,
        [
            "problem",
            "errata-draft",
            KV_REF,
            "--store",
            str(store.root),
            "--output",
            str(out),
            "--registered",
            "2026-10-02",
        ],
    )
    assert result.exit_code == 0, result.output
    draft = yaml.safe_load(out.read_text())
    errata = ProblemErrata.model_validate(draft)
    assert {case.disposition.value for case in errata.cases} == {"exclude-from-solved"}
    assert all(case.reason.startswith("TODO") for case in errata.cases)
    assert any("does not pass on the spec-conformant" in line for line in errata.cases[0].evidence)


def test_merge_refuses_a_report_for_another_manifest(kv) -> None:
    store, _ = kv
    problem_id = ProblemId.parse(KV_REF)
    store.validation_path(problem_id).write_text(
        json.dumps({"verdict": "validated", "manifest_sha256": "0" * 64})
    )
    with pytest.raises(ValueError, match="run a full validation"):
        merge_audit(store, problem_id, {})
    result = CliRunner().invoke(
        cli,
        [
            "problem",
            "errata-draft",
            KV_REF,
            "--store",
            str(store.root),
            "--output",
            "x.yaml",
            "--registered",
            "2026-10-02",
        ],
    )
    assert result.exit_code != 0
    assert "no spec_determinacy audit" in result.output
