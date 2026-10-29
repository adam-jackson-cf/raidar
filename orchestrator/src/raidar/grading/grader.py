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
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from raidar.contract.wheels import DependencyInfraError
from raidar.grading.bundle import VerifierBundle, VerifierStepSpec
from raidar.grading.models import (
    CanaryRecord,
    CaseStatus,
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
from raidar.grading.provision import prepare_dependencies
from raidar.grading.resolver import Resolver
from raidar.grading.sandbox import (
    LEDGER_NAME,
    PLUGIN_FILE,
    GradingSandbox,
    SandboxResult,
    SandboxRun,
    render_pytest_args,
    sandbox_argv,
    sandbox_env,
    stage_snapshot,
)
from raidar.grading.submission_deps import SubmissionDependencies, python_path_entries

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


async def run_oracle(
    bundle: VerifierBundle,
    step: VerifierStepSpec,
    snapshot: Path,
    sandbox: GradingSandbox,
    run_dir: Path,
    *,
    coverage: bool,
    timeout_s: float = DEFAULT_RUN_TIMEOUT_S,
    resolver: Resolver | None = None,
    recorded: SubmissionDependencies | None = None,
) -> RunOutcome:
    role = "coverage" if coverage else "grading"
    started = time.perf_counter()
    app, out = stage_snapshot(snapshot, run_dir)
    try:
        dependencies = await prepare_dependencies(
            bundle, app, sandbox, resolver=resolver, recorded=recorded
        )
    except DependencyInfraError as error:
        # A resolver or index outage is never a verdict about the submission.
        unavailable = InfraFailure(code="dependency_resolver_unavailable", detail=str(error))
        return RunOutcome(role, (), unavailable, None, (), time.perf_counter() - started)
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
            image=dependencies.image_id or None,
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
            corpus=bundle.corpus_evidence(step),
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


SUBMISSION_FAILURE_CODES = frozenset(
    {
        "sandbox_timeout",
        "sandbox_error",
        "ledger_unreadable",
        "incomplete_run",
        "pytest_exit_status",
        "ledger_exit_mismatch",
    }
)
"""Grading failures a submission can cause: it ran and did not finish, crashed the run, or left
a ledger the host cannot read or reconcile. Whether one is the submission's or the sandbox's is
decided by the starter canary. A resolver outage and corpus or collection faults are never here."""


async def run_canary(
    bundle: VerifierBundle,
    step: VerifierStepSpec,
    sandbox: GradingSandbox,
    work: Path,
    *,
    grading: RunOutcome,
    timeout_s: float,
) -> tuple[CanaryRecord, RunOutcome]:
    """Grade the empty starter in the submission's own sandbox configuration.

    The starter runs on the submission's recorded dependency closure and image, with the same
    time limit, CPU and memory. Passing means grading completed (the starter solves nothing and
    need not); it is attribution evidence only, not proof against an input-dependent grader
    defect. Failing means the sandbox cannot grade this step, which is infrastructure.
    """
    starter = work / "canary-starter"
    starter.mkdir(parents=True, exist_ok=True)
    assert grading.infra_failure is not None
    assert grading.dependencies is not None
    outcome = await run_oracle(
        bundle,
        step,
        starter,
        sandbox,
        work / "canary",
        coverage=False,
        timeout_s=timeout_s,
        recorded=grading.dependencies,
    )
    failure = outcome.infra_failure
    record = CanaryRecord(
        status="failed" if failure is not None else "passed",
        trigger=grading.infra_failure.code,
        detail="" if failure is None else f"{failure.code}: {failure.detail}"[:400],
        seconds=outcome.seconds,
    )
    return record, outcome


def attribute_to_submission(grading: RunOutcome, starter: RunOutcome) -> RunOutcome:
    """A grading crash or timeout the canary says the sandbox does not cause: a failed outcome.

    Every case of the step counts failed (none of the submission's cases ran to a result).
    """
    cases = tuple(case.model_copy(update={"status": CaseStatus.FAILED}) for case in starter.cases)
    return RunOutcome(grading.role, cases, None, None, (), grading.seconds, grading.dependencies)


async def grade_step(
    bundle: VerifierBundle,
    step_name: str,
    snapshot: Path,
    sandbox: GradingSandbox,
    work: Path,
    *,
    coverage: bool,
    timeout_s: float = DEFAULT_RUN_TIMEOUT_S,
    resolver: Resolver | None = None,
    recorded: SubmissionDependencies | None = None,
) -> StepGrade:
    step = bundle.step(step_name)
    grading = await run_oracle(
        bundle,
        step,
        snapshot,
        sandbox,
        work / "grading",
        coverage=False,
        timeout_s=timeout_s,
        resolver=resolver,
        recorded=recorded,
    )
    canary = CanaryRecord()
    failure = grading.infra_failure
    if (
        failure is not None
        and failure.code in SUBMISSION_FAILURE_CODES
        and grading.dependencies is not None
    ):
        canary, starter = await run_canary(
            bundle, step, sandbox, work, grading=grading, timeout_s=timeout_s
        )
        if canary.status == "passed":
            grading = attribute_to_submission(grading, starter)
    grade = VerifierGrade(
        step=step.name,
        checkpoint=step.checkpoint,
        cases=grading.cases,
        infra_failure=grading.infra_failure,
    )
    timings = {"oracle_run_s": grading.seconds}
    if canary.status != "not_run":
        timings["canary_run_s"] = canary.seconds
    if not coverage:
        return StepGrade(
            grade=grade, grading=grading, coverage=None, parity=None, timings=timings, canary=canary
        )
    if canary.status == "passed":
        verdict = ineligible(f"grading_failed_on_submission:{canary.trigger}")
        return StepGrade(grade, grading, None, verdict, timings=timings, canary=canary)
    if grading.infra_failure is not None:
        verdict = ineligible(f"grading_infra_failure:{grading.infra_failure.code}")
        return StepGrade(grade, grading, None, verdict, timings=timings, canary=canary)
    measured = await run_oracle(
        bundle,
        step,
        snapshot,
        sandbox,
        work / "coverage",
        coverage=True,
        timeout_s=timeout_s,
        recorded=grading.dependencies,
    )
    timings["coverage_run_s"] = measured.seconds
    verdict, differences = parity_verdict(grading, measured)
    return StepGrade(grade, grading, measured, verdict, differences, timings, canary)
