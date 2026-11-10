#!/usr/bin/env python3
"""Pure validation and decisions for invocation-bound fast-test budgets."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any, NotRequired, TypedDict, TypeGuard, Unpack

Json = dict[str, Any]
FAST_RULE = "TEST.budget.fast-tier-time"
CASE_RULE = "TEST.budget.per-test-time"
PHASES = ("setup", "call", "teardown")
FORMULAS = {
    "wall": "ceil_s(1.25*max)",
    "perTest": "ceil_10ms(1.5*p95(test_maxima))",
    "exception": "ceil_10ms(1.5*test_max)",
}
REMEDY = "Inject a clock or runner and cut needless setup and execution steps"


class FindingOptions(TypedDict, total=False):
    rule: str
    kind: str
    test: str | None
    phase: str | None
    duration: float | None
    limit: float | None


def _finding(remedy: str, **details: Unpack[FindingOptions]) -> Json:
    return {
        "rule": FAST_RULE,
        "kind": "validity",
        "test": None,
        "phase": None,
        "duration": None,
        "limit": None,
        "remedy": remedy,
        **details,
    }


def _seconds(value: Any) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _profile_valid(profile: Any) -> bool:
    return (
        isinstance(profile, dict)
        and profile.get("schemaVersion") == 1
        and all(
            _text(profile.get(key))
            for key in (
                "runnerClass",
                "osImageFamily",
                "arch",
                "pythonVersion",
                "lockDigest",
                "coverageDigest",
                "selector",
            )
        )
        and type(profile.get("workers")) is int
        and profile["workers"] >= 1
    )


def validate_report(report: Json | None, *, revision: str, invocation_id: str) -> list[Json]:
    """Reject stale, truncated or non-numeric observations before using timings."""
    if not isinstance(report, dict):
        return [_finding("Produce the complete report from this fast-test invocation")]
    findings = []
    if (
        report.get("schemaVersion") != 1
        or not _text(revision)
        or not _text(invocation_id)
        or report.get("revision") != revision
        or report.get("invocationId") != invocation_id
    ):
        findings.append(
            _finding("Record schema v1 with the current revision and invocation identity")
        )
    if (
        report.get("complete") is not True
        or type(report.get("exitCode")) is not int
        or report["exitCode"] != 0
    ):
        findings.append(
            _finding("Finish every selected fast test successfully and retain all phases")
        )
    if not _profile_valid(report.get("profile")):
        findings.append(_finding("Record the complete versioned measurement profile"))
    machine = report.get("machine")
    if not isinstance(machine, dict) or not all(
        _text(machine.get(key)) for key in ("imageVersion", "cpuModel")
    ):
        findings.append(_finding("Record the actual runner image version and CPU model"))
    if not _seconds(report.get("wallSeconds")):
        findings.append(_finding("Measure a finite nonnegative outer wall duration"))
    findings.extend(_validate_cases(report))
    return findings


def _validate_cases(report: Json) -> list[Json]:
    inventory = report.get("inventory")
    if (
        not isinstance(inventory, list)
        or not inventory
        or not all(_text(node) for node in inventory)
    ):
        return [_finding("Collect the selected fast inventory independently of execution")]
    findings = []
    if len(set(inventory)) != len(inventory):
        findings.append(_finding("Record each selected nodeid exactly once in the inventory"))
    cases = report.get("cases")
    if not isinstance(cases, dict):
        return findings + [_finding("Retain an executed case record for every selected nodeid")]
    for node in sorted(set(inventory) | set(cases), key=str):
        case = cases.get(node)
        if node not in inventory or not isinstance(case, dict) or case.get("outcome") != "passed":
            findings.append(
                _finding(
                    "Execute the selected test successfully without skipping it",
                    rule=CASE_RULE,
                    test=node,
                )
            )
            continue
        phases = case.get("phases")
        findings.extend(
            _finding(
                "Retain a finite nonnegative duration for every executed phase",
                rule=CASE_RULE,
                test=node,
                phase=phase,
            )
            for phase in PHASES
            if not isinstance(phases, dict) or not _seconds(phases.get(phase))
        )
    return findings


def record_reports(reports: list[Json], *, revision: str, profile: Json) -> list[Json]:
    """Validate downloaded provenance without inventing or dropping observations."""
    if not isinstance(reports, list) or not _profile_valid(profile):
        raise ValueError(f"{FAST_RULE}: malformed measurement evidence or profile")
    inventory = None
    identities: set[tuple[str, str]] = set()
    invocations: set[str] = set()
    for report in reports:
        if not isinstance(report, dict):
            raise ValueError(f"{FAST_RULE}: malformed measurement record")
        findings = validate_report(
            report, revision=revision, invocation_id=report.get("invocationId", "")
        )
        provenance = report.get("measurement")
        if (
            findings
            or report.get("profile") != profile
            or not isinstance(provenance, dict)
            or provenance.get("successful") is not True
            or not _text(provenance.get("runId"))
            or not _text(provenance.get("jobId"))
        ):
            raise ValueError(f"{FAST_RULE}: incomplete, unsuccessful or incomparable baseline")
        selected = set(report["inventory"])
        identity = (provenance["runId"], provenance["jobId"])
        if (
            (inventory is not None and selected != inventory)
            or identity in identities
            or report["invocationId"] in invocations
        ):
            raise ValueError(f"{FAST_RULE}: duplicate measurement or mismatched inventory")
        inventory = selected
        identities.add(identity)
        invocations.add(report["invocationId"])
    return copy.deepcopy(reports)


def _total(case: Json) -> Decimal:
    return sum((Decimal(str(case["phases"][phase])) for phase in PHASES), Decimal(0))


def _ceil_centiseconds(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_CEILING))


def compute_limits(baselines: list[Json]) -> Json:
    """Calculate the declared population percentile from comparable observations."""
    if not baselines:
        raise ValueError(f"{FAST_RULE}: no baseline observations")
    first = baselines[0]
    if not isinstance(first, dict):
        raise ValueError(f"{FAST_RULE}: malformed baseline")
    records = record_reports(
        baselines, revision=first.get("revision", ""), profile=first.get("profile", {})
    )
    maxima = {
        node: max(_total(record["cases"][node]) for record in records)
        for node in first["inventory"]
    }
    if not maxima:
        raise ValueError(f"{CASE_RULE}: no selected baseline tests")
    population = sorted(maxima.values())
    percentile = population[math.ceil(Decimal("0.95") * len(population)) - 1]
    common = _ceil_centiseconds(Decimal("1.5") * percentile)
    return {
        "wallSeconds": int(
            (
                Decimal("1.25") * max(Decimal(str(record["wallSeconds"])) for record in records)
            ).to_integral_value(rounding=ROUND_CEILING)
        ),
        "perTestSeconds": common,
        "exceptionCeilings": {
            node: _ceil_centiseconds(Decimal("1.5") * maximum)
            for node, maximum in sorted(maxima.items())
            if maximum > Decimal(str(common))
        },
    }


def evaluate_exceptions(exceptions: Json, *, now: date) -> list[Json]:
    """Require exact identities and accountable, unexpired remediation."""
    if not isinstance(exceptions, dict):
        return [
            _finding(
                "Record exceptions as exact nodeid mappings",
                rule=CASE_RULE,
                kind="policy",
            )
        ]
    findings = []
    for node, exception in exceptions.items():
        valid = (
            _text(node)
            and "::" in node
            and not any(char in node for char in "*?")
            and isinstance(exception, dict)
            and _seconds(exception.get("limitSeconds"))
            and _text(exception.get("owner"))
            and _text(exception.get("remedy"))
        )
        try:
            deadline = exception.get("reviewDeadline") if isinstance(exception, dict) else None
            valid = (
                valid
                and isinstance(deadline, str)
                and date.fromisoformat(deadline).isoformat() == deadline
                and date.fromisoformat(deadline) >= now
            )
        except (TypeError, ValueError):
            valid = False
        if not valid:
            findings.append(
                _finding(
                    "Name an owner, concrete speed-up remedy, exact nodeid "
                    "and unexpired ISO reviewDeadline",
                    rule=CASE_RULE,
                    kind="policy",
                    test=node,
                )
            )
    return findings


def policy_increases(base: Json, proposed: Json) -> list[str]:
    """Return deterministic paths requiring review; simple tightening is free."""
    paths = []
    for field in ("wallSeconds", "perTestSeconds"):
        old, new = base.get(field), proposed.get(field)
        if _seconds(old) and (not _seconds(new) or new > old):
            paths.append(field)
    for field in ("formulas", "profile"):
        old, new = base.get(field, {}), proposed.get(field, {})
        if isinstance(old, dict) and isinstance(new, dict):
            paths.extend(
                f"{field}.{key}"
                for key in sorted(set(old) | set(new))
                if old.get(key) != new.get(key)
            )
        elif old != new:
            paths.append(field)
    paths.extend(_exception_increases(base.get("exceptions", {}), proposed.get("exceptions", {})))
    return sorted(paths)


def _exception_increases(old_exceptions: Any, new_exceptions: Any) -> list[str]:
    if not isinstance(old_exceptions, dict) or not isinstance(new_exceptions, dict):
        return ["exceptions"] if old_exceptions != new_exceptions else []
    paths = []
    for node, exception in sorted(new_exceptions.items()):
        previous = old_exceptions.get(node)
        path = f"exceptions.{node}"
        if not isinstance(previous, dict) or not isinstance(exception, dict):
            paths.append(path)
            continue
        old, new = previous.get("limitSeconds"), exception.get("limitSeconds")
        if _seconds(old) and (not _seconds(new) or new > old):
            paths.append(f"{path}.limitSeconds")
        old, new = previous.get("reviewDeadline"), exception.get("reviewDeadline")
        if isinstance(old, str) and isinstance(new, str) and new > old:
            paths.append(f"{path}.reviewDeadline")
    return paths


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def policy_diff_digest(base: Json, proposed: Json) -> str:
    return _digest({"base": base, "proposed": proposed})


def verify_approval(
    approval: Json,
    *,
    digest: str,
    owner: str,
    read_review: Callable[[str, int], Json],
) -> bool:
    """Authorize only from an injected external record bound to this change."""
    if (
        not isinstance(approval, dict)
        or not _text(owner)
        or not _text(approval.get("repository"))
        or type(approval.get("reviewId")) is not int
        or approval["reviewId"] <= 0
        or not _text(approval.get("commit"))
        or approval.get("policyDiffDigest") != digest
        or not callable(read_review)
    ):
        return False
    try:
        review = read_review(approval["repository"], approval["reviewId"])
    except (OSError, ValueError, RuntimeError):
        return False
    return isinstance(review, dict) and all(
        review.get(key) == value
        for key, value in {
            "reviewer": owner,
            "state": "APPROVED",
            "policyDiffDigest": digest,
            "commit": approval["commit"],
        }.items()
    )


class BudgetOptions(TypedDict):
    revision: str
    invocation_id: str
    ci: bool
    now: Callable[[], date]
    base_policy: NotRequired[Json | None]
    approval: NotRequired[Json | None]
    owner: NotRequired[str]
    read_review: NotRequired[Callable[[str, int], Json] | None]


@dataclass(frozen=True)
class BudgetContext:
    revision: str
    invocation_id: str
    ci: bool
    now: Callable[[], date]
    base_policy: Json | None = None
    approval: Json | None = None
    owner: str = ""
    read_review: Callable[[str, int], Json] | None = None


def _validate_policy(policy: Json, today: date) -> list[Json]:
    findings = []
    if policy.get("schemaVersion") != 1 or not _profile_valid(policy.get("profile")):
        findings.append(
            _finding("Record a valid schema v1 budget policy and profile", kind="policy")
        )
    if policy.get("formulas") != FORMULAS:
        findings.append(_finding("Use the declared reproducible budget formulas", kind="policy"))
    if policy.get("goal") != {"wallSeconds": 30, "perTestSeconds": 0.1}:
        findings.append(
            _finding(
                "Retain the 30-second wall and 100-ms per-test optimization goals",
                kind="policy",
            )
        )
    return findings + evaluate_exceptions(policy.get("exceptions", {}), now=today)


def _calibrate(policy: Json, baselines: list[Json]) -> tuple[Json | None, list[Json]]:
    try:
        if baselines:
            record_reports(
                baselines,
                revision=baselines[0].get("revision", ""),
                profile=policy.get("profile", {}),
            )
        limits = compute_limits(baselines) if len(baselines) >= 5 else None
    except (ValueError, TypeError, AttributeError) as error:
        return None, [
            _finding(f"Retain valid comparable baseline evidence: {error}", kind="policy")
        ]
    return limits, _validate_limits(policy, limits)


def _validate_limits(policy: Json, limits: Json | None) -> list[Json]:
    exceptions = policy.get("exceptions", {})
    if limits is None:
        if (
            policy.get("wallSeconds") is not None
            or policy.get("perTestSeconds") is not None
            or exceptions
        ):
            return [
                _finding(
                    "Keep limits unavailable until five valid comparable baseline runs exist",
                    kind="policy",
                )
            ]
        return []
    findings = [
        _finding(f"Recompute {field} from retained baseline observations", kind="policy")
        for field in ("wallSeconds", "perTestSeconds")
        if not _seconds(policy.get(field)) or policy[field] != limits[field]
    ]
    ceilings = limits["exceptionCeilings"]
    if not isinstance(exceptions, dict) or set(exceptions) != set(ceilings):
        findings.append(
            _finding(
                "Retain exactly the named observed outlier exceptions",
                rule=CASE_RULE,
                kind="policy",
            )
        )
    elif any(
        not isinstance(exceptions[node], dict) or exceptions[node].get("limitSeconds") != ceiling
        for node, ceiling in ceilings.items()
    ):
        findings.append(
            _finding(
                "Recompute every named exception ceiling from observations",
                rule=CASE_RULE,
                kind="policy",
            )
        )
    return findings


def _accepted_status(
    policy: Json, baselines: list[Json], limits: Json | None, context: BudgetContext
) -> bool:
    reference = policy.get("measurementApproval")
    unsigned = {key: value for key, value in policy.items() if key != "measurementApproval"}
    return (
        limits is not None
        and isinstance(reference, dict)
        and reference.get("kind") == "measurement"
        and reference.get("commit") == baselines[0].get("revision")
        and context.read_review is not None
        and verify_approval(
            reference,
            digest=_digest(unsigned),
            owner=context.owner,
            read_review=context.read_review,
        )
    )


def _increase_approved(policy: Json, context: BudgetContext) -> bool:
    return (
        isinstance(context.approval, dict)
        and context.read_review is not None
        and context.base_policy is not None
        and verify_approval(
            context.approval,
            digest=policy_diff_digest(context.base_policy, policy),
            owner=context.owner,
            read_review=context.read_review,
        )
    )


def _authorize_policy(
    policy: Json, baselines: list[Json], limits: Json | None, context: BudgetContext
) -> list[Json]:
    findings = []
    status = policy.get("status")
    if status == "accepted":
        if not _accepted_status(policy, baselines, limits, context):
            findings.append(
                _finding(
                    "Verify a successful repository measure completion "
                    "bound to this policy and profile",
                    kind="policy",
                )
            )
    elif status != "provisional":
        findings.append(
            _finding(
                "Use provisional status until an externally verified measure completion",
                kind="policy",
            )
        )
    if (
        context.base_policy is not None
        and policy_increases(context.base_policy, policy)
        and not _increase_approved(policy, context)
    ):
        findings.append(
            _finding(
                "Obtain external repository-owner approval "
                "bound to the exact policy diff and revision",
                kind="approval",
            )
        )
    return findings


def _budget_breaches(report: Json, policy: Json, limits: Json) -> list[Json]:
    findings = []
    wall = report["wallSeconds"]
    if wall > limits["wallSeconds"]:
        findings.append(
            _finding(
                REMEDY,
                kind="breach",
                duration=wall,
                limit=limits["wallSeconds"],
                phase="wall",
            )
        )
    exceptions = policy.get("exceptions", {})
    for node, case in report["cases"].items():
        duration = float(_total(case))
        candidate = exceptions.get(node, {}) if isinstance(exceptions, dict) else {}
        exception = candidate if isinstance(candidate, dict) else {}
        limit = limits["exceptionCeilings"].get(node, limits["perTestSeconds"])
        if duration > limit:
            phase = max(PHASES, key=lambda name: case["phases"][name])
            findings.append(
                _finding(
                    exception.get("remedy", REMEDY),
                    rule=CASE_RULE,
                    kind="breach",
                    test=node,
                    phase=phase,
                    duration=duration,
                    limit=limit,
                )
            )
    return findings


def _decision_lines(
    report: Json, policy: Json, limits: Json | None, count: int, ci: bool
) -> list[str]:
    wall = report.get("wallSeconds")
    available_wall = wall if _seconds(wall) else None
    enforced = limits["wallSeconds"] if limits is not None else "unavailable"
    gap = max(0, available_wall - 30) if available_wall is not None else "unavailable"
    lines = [
        f"Fast budget: measured wall {available_wall}s; enforced limit {enforced}s; "
        f"goal 30s; gap {gap}s"
    ]
    if limits is None:
        lines.append(f"Baseline calibration: no limits ({count}/5); enforce validity only")
    if ci:
        profile_status = (
            "recognised" if report.get("profile") == policy.get("profile") else "mismatch"
        )
        lines.append(f"CI measurement profile: {profile_status}")
    return lines


def _exit_code(findings: list[Json], ci: bool) -> int:
    if any(item["kind"] in {"validity", "policy", "approval"} for item in findings):
        return 1
    if any(item["kind"] == "infrastructure" for item in findings):
        return 2
    return int(ci and any(item["kind"] == "breach" for item in findings))


def check_budget(
    report: Json | None,
    policy: Json,
    baselines: list[Json],
    **options: Unpack[BudgetOptions],
) -> Json:
    """Combine validity, reproducibility, authorization and CI-only timings."""
    context = BudgetContext(**options)
    findings = validate_report(
        report, revision=context.revision, invocation_id=context.invocation_id
    )
    policy = policy if isinstance(policy, dict) else {}
    findings.extend(_validate_policy(policy, context.now()))
    limits, calibration_findings = _calibrate(policy, baselines)
    findings.extend(calibration_findings)
    findings.extend(_authorize_policy(policy, baselines, limits, context))
    report_values = report if isinstance(report, dict) else {}
    if context.ci and report_values.get("profile") != policy.get("profile"):
        findings.append(
            _finding(
                "Run the measured fast tests on the pinned policy profile",
                kind="infrastructure",
            )
        )
    if limits is not None and not any(item["kind"] == "validity" for item in findings):
        findings.extend(_budget_breaches(report_values, policy, limits))
    lines = _decision_lines(report_values, policy, limits, len(baselines), context.ci)
    lines.extend(
        f"{item['rule']} {item['kind']}: test={item['test']} phase={item['phase']} "
        f"duration={item['duration']} limit={item['limit']}; {item['remedy']}"
        for item in findings
    )
    return {
        "exitCode": _exit_code(findings, context.ci),
        "findings": findings,
        "lines": lines,
    }


if __name__ == "__main__":
    specification = importlib.util.spec_from_file_location(
        "test_budget_evidence", Path(__file__).with_name("test_budget_evidence.py")
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("Cannot load the test budget evidence CLI")
    evidence = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = evidence
    specification.loader.exec_module(evidence)
    raise SystemExit(evidence.main(sys.modules[__name__]))
