"""Grade one snapshot against one step of a verifier bundle.

1. **Grading run**: hidden tests, black-box, against a fresh copy of the
   snapshot in the sandbox. No coverage instrumentation. Produces the verdict.
2. **Coverage run** (optional, E2): the same invocation on another fresh copy,
   wrapped in coverage.py with per-test contexts. Never changes the verdict;
   its evidence is usable only when ``check_parity`` passes.

Each run has a hard sandbox timeout (``timeout_s``) and, inside it, a grading
budget that ends ``BUDGET_MARGIN_S`` plus one per-test timeout earlier. Cases
not started within the budget fail (agent-caused slowness), so only a run that
wedges past the hard timeout is an infrastructure failure.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from raidar.contracts.snapshot import copy_snapshot, tree_digest
from raidar.grading.bundle import VerifierBundle, VerifierStepSpec
from raidar.grading.models import (
    CorpusEvidence,
    CoverageContext,
    EnvironmentEvidence,
    InfraFailure,
    InvocationEvidence,
    LedgerEntry,
    ParityVerdict,
    ProcessEvent,
    RunEvidence,
    VerifierGrade,
)
from raidar.grading.normalise import (
    case_results,
    ledger_consistency_failure,
    ledger_infra_failure,
    no_graded_cases,
    timing_sensitive,
)
from raidar.grading.outcomes import RunOutcome, StepGrade
from raidar.grading.parity import check_parity, ineligible, outcome_differences
from raidar.grading.sandbox import (
    LEDGER_NAME,
    PLUGIN_FILE,
    GradingSandbox,
    SandboxResult,
    SandboxRun,
    render_pytest_args,
    sandbox_argv,
    sandbox_env,
)
from raidar.grading.submission_deps import (
    provisioned_pins,
    python_path_entries,
    resolve_dependencies,
)

DEFAULT_RUN_TIMEOUT_S = 900.0
BUDGET_MARGIN_S = 60.0
_LOG_TAIL = 2000


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _environment(ledger: dict[str, Any], environment_id: str) -> EnvironmentEvidence:
    probe = ledger.get("environment", {})
    return EnvironmentEvidence(
        grader_environment_id=environment_id,
        grader_tools_sha256=_sha256_hex("\n".join(probe.get("grader_tools", []))),
        grader_interpreter_sha256=str(probe.get("grader_python_sha256", "")),
        submission_interpreter_sha256=str(probe.get("submission_python_sha256", "")),
        platform_sha256=_sha256_hex(json.dumps(probe.get("platform", {}), sort_keys=True)),
        plugin_sha256=hashlib.sha256(PLUGIN_FILE.read_bytes()).hexdigest(),
    )


def _coverage_contexts(rows: list[dict[str, Any]]) -> tuple[CoverageContext, ...]:
    lines: dict[tuple[str, str], set[int]] = defaultdict(set)
    for row in rows:
        lines[(str(row["node_id"]), str(row["path"]))].add(int(row["executed_line"]))
    return tuple(
        CoverageContext(node_id=node, path=path, executed_lines=tuple(sorted(executed)))
        for (node, path), executed in sorted(lines.items())
    )


def _evidence(
    role: str,
    ledger: dict[str, Any],
    *,
    environment_id: str,
    corpus: CorpusEvidence,
    invocation: InvocationEvidence,
    prefix: tuple[str, ...],
) -> RunEvidence:
    return RunEvidence(
        role=role,
        environment=_environment(ledger, environment_id),
        corpus=corpus,
        invocation=invocation,
        measurement_prefix=prefix,
        ledger=tuple(LedgerEntry.model_validate(row) for row in ledger.get("ledger", [])),
        coverage=_coverage_contexts(ledger.get("coverage", [])),
        process_events=tuple(
            ProcessEvent.model_validate(row) for row in ledger.get("process_events", [])
        ),
        invalid_coverage_paths=int(ledger.get("invalid_coverage_paths", 0)),
    )


def _sandbox_infra(result: SandboxResult, ledger_path: Path) -> InfraFailure | None:
    if result.timed_out:
        return InfraFailure(code="sandbox_timeout")
    if not ledger_path.is_file():
        tail = result.output[-_LOG_TAIL:]
        return InfraFailure(code="sandbox_error", detail=f"exit={result.exit_code}: {tail}")
    return None


def grading_budget(step: VerifierStepSpec, timeout_s: float) -> float | None:
    """Seconds pytest may start new cases in, leaving room for the last case to finish."""
    budget = timeout_s - (step.timeout_s or 0.0) - BUDGET_MARGIN_S
    return budget if budget > 0 else None


def _stage(snapshot: Path, run_dir: Path) -> tuple[Path, Path]:
    if run_dir.exists():
        shutil.rmtree(run_dir)
    app, out = run_dir / "app", run_dir / "out"
    app.mkdir(parents=True)
    copy_snapshot(snapshot, app)
    out.mkdir()
    return app, out


async def run_oracle(
    bundle: VerifierBundle,
    step: VerifierStepSpec,
    snapshot: Path,
    sandbox: GradingSandbox,
    run_dir: Path,
    *,
    coverage: bool,
    timeout_s: float = DEFAULT_RUN_TIMEOUT_S,
) -> RunOutcome:
    role = "coverage" if coverage else "grading"
    started = time.perf_counter()
    app, out = _stage(snapshot, run_dir)
    dependencies = resolve_dependencies(
        app, provisioned_pins(bundle.manifest.grader.test_dependencies)
    )
    argv, prefix = sandbox_argv(step, sandbox.layout, coverage=coverage)
    result = await sandbox.run(
        SandboxRun(
            app_dir=app,
            tests_dir=bundle.tests_dir,
            out_dir=out,
            argv=argv,
            env=sandbox_env(
                bundle,
                sandbox.layout,
                coverage=coverage,
                budget_s=grading_budget(step, timeout_s),
                python_path=python_path_entries(dependencies),
            ),
            timeout_s=timeout_s,
            cpus=bundle.manifest.grader.cpus,
            memory_mb=bundle.manifest.grader.memory_mb,
        )
    )
    (out / "sandbox.log").write_text(result.output)
    infra = _sandbox_infra(result, out / LEDGER_NAME)
    if infra is not None:
        return RunOutcome(role, (), infra, None, (), time.perf_counter() - started, dependencies)
    try:
        ledger = json.loads((out / LEDGER_NAME).read_text())
        cases = case_results(ledger, step.checkpoint)
        evidence = _evidence(
            role,
            ledger,
            environment_id=await sandbox.environment_id(),
            corpus=CorpusEvidence(
                corpus_sha256=tree_digest(bundle.tests_dir), selected_tests=step.test_files
            ),
            invocation=InvocationEvidence(
                argv=render_pytest_args(step, sandbox.layout), cwd=sandbox.layout.app
            ),
            prefix=prefix,
        )
        infra = (
            ledger_infra_failure(ledger)
            or ledger_consistency_failure(ledger, result.exit_code, step.test_files)
            or no_graded_cases(cases)
        )
        sensitive = timing_sensitive(ledger, step.timeout_s)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        # The submission shares the ledger's uid, so a malformed ledger is an untrusted run.
        malformed = InfraFailure(
            code="ledger_unreadable", detail=f"{type(error).__name__}: {error}"
        )
        return RunOutcome(
            role, (), malformed, None, (), time.perf_counter() - started, dependencies
        )
    return RunOutcome(
        role=role,
        cases=cases,
        infra_failure=infra,
        evidence=evidence,
        timing_sensitive=sensitive,
        seconds=time.perf_counter() - started,
        dependencies=dependencies,
    )


def parity_verdict(
    grading: RunOutcome, coverage: RunOutcome
) -> tuple[ParityVerdict, tuple[str, ...]]:
    if coverage.infra_failure is not None or coverage.evidence is None:
        code = coverage.infra_failure.code if coverage.infra_failure else "missing"
        return ineligible(f"coverage_run_failed:{code}"), ()
    if grading.evidence is None:
        return ineligible("grading_run_failed"), ()
    verdict = check_parity(grading.evidence, coverage.evidence, grading.cases, coverage.cases)
    return verdict, tuple(outcome_differences(grading.cases, coverage.cases))


async def grade_step(
    bundle: VerifierBundle,
    step_name: str,
    snapshot: Path,
    sandbox: GradingSandbox,
    work: Path,
    *,
    coverage: bool,
    timeout_s: float = DEFAULT_RUN_TIMEOUT_S,
) -> StepGrade:
    step = bundle.step(step_name)
    grading = await run_oracle(
        bundle, step, snapshot, sandbox, work / "grading", coverage=False, timeout_s=timeout_s
    )
    grade = VerifierGrade(
        step=step.name,
        checkpoint=step.checkpoint,
        cases=grading.cases,
        infra_failure=grading.infra_failure,
    )
    timings = {"oracle_run_s": grading.seconds}
    if not coverage:
        return StepGrade(grade=grade, grading=grading, coverage=None, parity=None, timings=timings)
    if grading.infra_failure is not None:
        verdict = ineligible(f"grading_infra_failure:{grading.infra_failure.code}")
        return StepGrade(grade, grading, None, verdict, timings=timings)
    measured = await run_oracle(
        bundle, step, snapshot, sandbox, work / "coverage", coverage=True, timeout_s=timeout_s
    )
    timings["coverage_run_s"] = measured.seconds
    verdict, differences = parity_verdict(grading, measured)
    return StepGrade(grade, grading, measured, verdict, differences, timings)
