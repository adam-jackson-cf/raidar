"""Slow-tier behaviours split from :mod:`tests.test_problem_spec_determinacy`."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from raidar.cli import main as cli
from raidar.design.problem import variant_shim
from raidar.design.problem.errata import ProblemErrata
from raidar.design.problem.models import ProblemId
from raidar.design.problem.validator import ValidateOptions, audit_problem, merge_audit
from raidar.design.problem.variants import build_variant, original_name
from tests.audit_support import KV_REF
from tests.test_problem_spec_determinacy import (
    _audit as _audit,
)
from tests.test_problem_spec_determinacy import (
    kv_fixture as kv_fixture,
)
from tests.verifier_support import LocalProcessSandbox

pytestmark = pytest.mark.slow


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


def test_shim_main_propagates_exit_code_in_process(
    tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    target = tmp_path / "t.py"
    target.write_text("import sys; print('{\"a\":1}', end=''); sys.exit(4)\n")
    assert variant_shim.main(str(target), "reformatted_json", []) == 4
    assert capsysbinary.readouterr().out == b'{\n  "a": 1\n}\n'


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
    assert report["assertions"]["audited"] == 4
    assert report["assertions"]["total"] == 6
    assert report["summary"]["status"] == "measured"


@pytest.mark.parametrize("format_rule", ["JSON Lines", "compact JSON", "NDJSON"])
def test_fixed_output_format_is_not_treated_as_a_conforming_reformat(
    kv, tmp_path: Path, format_rule: str
) -> None:
    _, problem = kv
    instruction = problem.task_dir / "steps" / "checkpoint_2" / "instruction.md"
    instruction.write_text(instruction.read_text() + f"\nOutput must be {format_rule}.\n")
    report = _audit(kv, tmp_path)
    assert "reformatted_json" in report["steps"]["checkpoint_1"]["variants"]
    assert "reformatted_json" not in report["steps"]["checkpoint_2"]["variants"]
    assert report["summary"]["flagged_by_checkpoint"] == {"checkpoint_1": 2, "checkpoint_2": 2}


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
            ValidateOptions(docker_context="test-ctx", concurrency=1),
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
