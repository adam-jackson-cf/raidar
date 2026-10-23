"""``raidar problem validate``: run the verifier validation checks and write a report.

Engines:

- ``direct`` (default): reference payloads graded by the Raidar verifier in
  fresh grader containers (``raidar.problems.direct``);
- ``harbor``: the imported task run as Harbor trials with the ``oracle`` agent
  and the Raidar verifier (``raidar.problems.harbor_engine``).

The native parity arm runs only when an SCB fork checkout is given. The
spec-determinacy audit (``raidar.problems.audit``) runs by default and grades its
reference variants in the direct engine's grader sandbox whatever the engine;
``audit_problem`` runs it alone and ``merge_audit`` writes it into the committed
report without touching that report's checks or verdict.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from raidar.problems import direct, harbor_engine
from raidar.problems.audit import DynamicEvidence, audit_report, grade_audit_variants
from raidar.problems.importer import resolve_checkout
from raidar.problems.models import ProblemId
from raidar.problems.native import NativeRunner, stage_catalog
from raidar.problems.store import ImportedProblem, ProblemStore
from raidar.problems.validation import (
    NEGATIVE,
    POSITIVE,
    CheckStatus,
    StepGrades,
    ValidationRecordError,
    ValidationReport,
    ValidationRuns,
    Verdict,
    read_validation_record,
    run_checks,
)
from raidar.verifier.grader_image import ensure_grader_image, grader_image_tag
from raidar.verifier.sandbox import DockerSandbox, GradingSandbox

ENGINES = ("direct", "harbor")


@dataclass(frozen=True)
class ValidateOptions:
    docker_context: str
    engine: str = "direct"
    repeats: int = 3
    concurrency: int = direct.DEFAULT_CONCURRENCY
    timeout_s: float = direct.DEFAULT_RUN_TIMEOUT_S
    native_runner: Path | None = None
    source_checkout: Path | None = None
    audit: bool = True


async def _docker_sandbox(
    problem: ImportedProblem, docker_context: str
) -> tuple[DockerSandbox, dict[str, object]]:
    image = await ensure_grader_image(problem.bundle.manifest.grader, docker_context=docker_context)
    sandbox = DockerSandbox(image=image.tag, docker_context=docker_context)
    return sandbox, {
        "grader_image": image.tag,
        "grader_environment_id": await sandbox.environment_id(),
        "docker_context": docker_context,
    }


async def _harbor_runs(
    problem: ImportedProblem, options: ValidateOptions, work: Path
) -> dict[str, list[StepGrades]]:
    empty = work / "empty-starter"
    empty.mkdir(parents=True, exist_ok=True)
    snapshots = direct.variant_snapshots(problem, empty)
    runs: dict[str, list[StepGrades]] = {}
    for variant in (POSITIVE, NEGATIVE):
        runs[variant] = await harbor_engine.run_variant(
            problem,
            variant,
            snapshots[variant],
            work,
            repeats=options.repeats,
            docker_context=options.docker_context,
        )
    return runs


def _native_runs(
    store: ProblemStore, problem: ImportedProblem, options: ValidateOptions, work: Path
) -> dict[str, StepGrades]:
    assert options.native_runner is not None
    manifest = problem.manifest
    checkout = resolve_checkout(store, manifest.problem_id, options.source_checkout)
    catalog = work / "native-catalog"
    stage_catalog(checkout.root / manifest.source.problem, catalog)
    runner = NativeRunner(options.native_runner, docker_context=options.docker_context)
    empty = work / "native-empty"
    empty.mkdir(parents=True, exist_ok=True)
    snapshots = direct.variant_snapshots(problem, empty)
    native: dict[str, StepGrades] = {}
    for variant, by_step in snapshots.items():
        native[variant] = {
            step: runner.grade(
                catalog,
                manifest.source.problem,
                snapshot,
                index + 1,
                work / "native" / variant / step,
            )
            for index, (step, snapshot) in enumerate(by_step.items())
        }
    return native


async def validate_problem(
    store: ProblemStore,
    problem_id: ProblemId,
    options: ValidateOptions,
    *,
    sandbox: GradingSandbox | None = None,
    work_root: Path | None = None,
) -> ValidationReport:
    if options.engine not in ENGINES:
        raise ValueError(f"engine must be one of {ENGINES}, not {options.engine!r}")
    problem = store.load(problem_id)
    environment: dict[str, object] = {
        "engine": options.engine,
        "harbor": version("harbor"),
        "pack_image": problem.manifest.pack.image,
        "docker_context": options.docker_context,
    }
    with tempfile.TemporaryDirectory(prefix="raidar-problem-validate-", dir=work_root) as tmp:
        work = Path(tmp)
        if options.engine == "harbor":
            # The verifier grades in this content-addressed image (built on demand).
            environment["grader_image"] = grader_image_tag(problem.bundle.manifest.grader)
            oracle = await _harbor_runs(problem, options, work)
        else:
            if sandbox is None:
                sandbox, details = await _docker_sandbox(problem, options.docker_context)
                environment.update(details)
            oracle = await direct.grade_references(
                problem,
                sandbox,
                repeats=options.repeats,
                concurrency=options.concurrency,
                timeout_s=options.timeout_s,
                work_root=work,
            )
        native = None
        if options.native_runner is not None:
            native = await asyncio.to_thread(_native_runs, store, problem, options, work)
            environment["native_runner"] = NativeRunner(
                options.native_runner, options.docker_context
            ).revision()
        audit = (
            await _audit(problem, options, sandbox=sandbox, work_root=work)
            if options.audit
            else None
        )
    runs = ValidationRuns(oracle=oracle, native=native)
    return ValidationReport(
        problem=str(problem_id),
        manifest_sha256=problem.manifest.digest(),
        mode=problem.manifest.mode.value,
        steps=tuple(step.name for step in problem.manifest.steps),
        engine=options.engine,
        repeats=options.repeats,
        checks=run_checks(runs),
        runs=runs,
        environment=environment,
        spec_determinacy=audit,
    )


async def _audit(
    problem: ImportedProblem,
    options: ValidateOptions,
    *,
    sandbox: GradingSandbox | None,
    work_root: Path | None,
) -> dict[str, object]:
    details: dict[str, object] = {}
    if sandbox is None:
        sandbox, details = await _docker_sandbox(problem, options.docker_context)
    dynamic = await grade_audit_variants(
        problem,
        sandbox,
        concurrency=options.concurrency,
        timeout_s=options.timeout_s,
        work_root=work_root,
    )
    environment = {**details, **(dynamic.environment or {})}
    return audit_report(problem, DynamicEvidence(dynamic.grades, dynamic.not_run, environment))


async def audit_problem(
    store: ProblemStore,
    problem_id: ProblemId,
    options: ValidateOptions,
    *,
    sandbox: GradingSandbox | None = None,
    work_root: Path | None = None,
) -> dict[str, object]:
    """The spec-determinacy audit alone (``raidar problem validate --audit-only``)."""
    problem = store.load(problem_id)
    return await _audit(problem, options, sandbox=sandbox, work_root=work_root)


def merge_audit(store: ProblemStore, problem_id: ProblemId, audit: dict[str, object]) -> Path:
    """Write ``audit`` into the committed report of the current manifest; nothing else changes."""
    path = store.validation_path(problem_id)
    manifest = store.load_manifest(problem_id)
    record = read_validation_record(path)
    if record.manifest_sha256 != manifest.digest():
        raise ValidationRecordError(
            f"{path} is for manifest {record.manifest_sha256[:12]}, not "
            f"{manifest.digest()[:12]}; run a full validation instead"
        )
    report = json.loads(path.read_text())
    report["spec_determinacy"] = audit
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return path


def default_report_path(store: ProblemStore, report: ValidationReport) -> Path:
    """The committed report path, except when the new report would weaken it.

    An infra failure never replaces the committed report, and neither does a
    report for the same manifest that skipped a check the committed one ran
    (e.g. ``--repeats 1`` over a full three-repeat validation): both go to the
    uncommitted attempt path.
    """
    problem_id = ProblemId.parse(report.problem)
    if report.verdict is Verdict.INFRA_FAILURE:
        return store.validation_attempt_path(problem_id)
    committed = store.validation_path(problem_id)
    try:
        existing = read_validation_record(committed)
    except ValidationRecordError:
        return committed
    skipped = {check.name for check in report.checks if check.status is CheckStatus.NOT_RUN}
    if existing.manifest_sha256 == report.manifest_sha256 and skipped - set(existing.not_run):
        return store.validation_attempt_path(problem_id)
    return committed


def write_report(report: ValidationReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_json(), indent=2, sort_keys=True) + "\n")
    return path
