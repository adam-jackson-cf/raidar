"""Pure behavioural proofs for invocation-bound, owner-reviewed test budgets."""

from __future__ import annotations

import copy
import importlib.util
import math
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, Protocol, cast

import pytest

Json = dict[str, Any]
ROOT = Path(__file__).resolve().parents[2]
FAST_RULE = "TEST.budget.fast-tier-time"
CASE_RULE = "TEST.budget.per-test-time"
TODAY = date(2026, 11, 10)
NODE = "test_sample.py::test_20"
PROFILE: Json = {
    "schemaVersion": 1,
    "runnerClass": "ubuntu-24.04",
    "osImageFamily": "ubuntu24",
    "arch": "X64",
    "pythonVersion": "3.13.7",
    "lockDigest": "lock-sha256",
    "coverageDigest": "coverage-sha256",
    "selector": "not slow and not quality and not docker and not security and not perf",
    "workers": 1,
}


class BudgetApi(Protocol):
    def validate_report(
        self, report: Json | None, *, revision: str, invocation_id: str
    ) -> list[Json]: ...

    def compute_limits(self, baselines: list[Json]) -> Json: ...

    def evaluate_exceptions(self, exceptions: Json, *, now: date) -> list[Json]: ...

    def policy_increases(self, base: Json, proposed: Json) -> list[str]: ...

    def policy_diff_digest(self, base: Json, proposed: Json) -> str: ...

    def verify_approval(
        self,
        approval: Json,
        *,
        digest: str,
        owner: str,
        read_review: Callable[[str, int], Json],
    ) -> bool: ...

    def check_budget(
        self, report: Json | None, policy: Json, baselines: list[Json], **kwargs: Any
    ) -> Json: ...

    def record_reports(
        self, reports: list[Json], *, revision: str, profile: Json
    ) -> list[Json]: ...


@pytest.fixture
def budgets() -> BudgetApi:
    spec = importlib.util.spec_from_file_location(
        "test_budget_checker", ROOT / "scripts/checks/check-test-budgets.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("BudgetApi", module)


def _report(*, invocation: str = "current", wall: float = 10.0) -> Json:
    cases: Json = {
        f"test_sample.py::test_{index:02}": {
            "outcome": "passed",
            "phases": {"setup": 0.01, "call": 0.02, "teardown": 0.01},
        }
        for index in range(21)
    }
    cases[NODE]["phases"]["call"] = 0.98
    return {
        "schemaVersion": 1,
        "invocationId": invocation,
        "revision": "revision-a",
        "profile": copy.deepcopy(PROFILE),
        "machine": {"imageVersion": "20261101.1", "cpuModel": "test CPU"},
        "wallSeconds": wall,
        "inventory": list(cases),
        "cases": cases,
        "exitCode": 0,
        "complete": True,
        "measurement": {"runId": invocation, "jobId": "measure", "successful": True},
    }


def _baselines() -> list[Json]:
    return [_report(invocation=f"baseline-{index}") for index in range(5)]


def _exception() -> Json:
    return {
        "limitSeconds": 1.5,
        "owner": "repo-owner",
        "remedy": "Inject a runner instead of starting a tool",
        "reviewDeadline": "2026-12-10",
    }


def _policy() -> Json:
    return {
        "schemaVersion": 1,
        "profile": copy.deepcopy(PROFILE),
        "formulas": {
            "wall": "ceil_s(1.25*max)",
            "perTest": "ceil_10ms(1.5*p95(test_maxima))",
            "exception": "ceil_10ms(1.5*test_max)",
        },
        "status": "provisional",
        "goal": {"wallSeconds": 30, "perTestSeconds": 0.1},
        "wallSeconds": 13,
        "perTestSeconds": 0.06,
        "exceptions": {NODE: _exception()},
    }


def _check(budgets: BudgetApi, report: Json | None, **options: Any) -> Json:
    arguments: Json = {
        "revision": "revision-a",
        "invocation_id": "current",
        "ci": True,
        "now": lambda: TODAY,
    }
    arguments.update(options)
    policy = arguments.pop("policy", _policy())
    baselines = arguments.pop("baselines", _baselines())
    return budgets.check_budget(report, policy, baselines, **arguments)


@pytest.mark.parametrize(
    "defect",
    [
        "missing-report",
        "missing-case",
        "unexecuted",
        "skipped",
        "truncated",
        "nan",
        "infinity",
        "negative",
        "missing-phase",
        "revision",
        "invocation",
        "negative-wall",
        "nan-wall",
        "duplicate-inventory",
        "extra-case",
    ],
)
def test_incomplete_or_unbound_measurements_fail_everywhere(
    budgets: BudgetApi, defect: str
) -> None:
    report = _report()
    case = report["cases"][NODE]
    scalar_defects = {
        "revision": ("revision", "stale"),
        "invocation": ("invocationId", "stale"),
        "negative-wall": ("wallSeconds", -1.0),
        "nan-wall": ("wallSeconds", math.nan),
    }
    if defect == "missing-case":
        del report["cases"][NODE]
    elif defect == "unexecuted":
        case["phases"] = {}
    elif defect == "skipped":
        case["outcome"] = "skipped"
    elif defect == "truncated":
        report.update(complete=False, exitCode=1)
    elif defect in {"nan", "infinity", "negative"}:
        case["phases"]["call"] = {"nan": math.nan, "infinity": math.inf, "negative": -0.01}[defect]
    elif defect == "missing-phase":
        del case["phases"]["teardown"]
    elif defect in scalar_defects:
        field, value = scalar_defects[defect]
        report[field] = value
    elif defect == "duplicate-inventory":
        report["inventory"].append(NODE)
    elif defect == "extra-case":
        report["cases"]["not-collected"] = copy.deepcopy(case)
    candidate = None if defect == "missing-report" else report
    findings = budgets.validate_report(candidate, revision="revision-a", invocation_id="current")
    assert findings
    assert all(finding["rule"] in {FAST_RULE, CASE_RULE} for finding in findings)
    assert _check(budgets, candidate, ci=False)["exitCode"] != 0


def test_complete_report_is_valid(budgets: BudgetApi) -> None:
    assert budgets.validate_report(_report(), revision="revision-a", invocation_id="current") == []


def test_limits_use_population_percentile_of_test_maxima_and_round_up(budgets: BudgetApi) -> None:
    records = _baselines()
    # 21 maxima: 19*.04, .111, 1.001. Population p95 is .111, not a pooled run p95.
    for record in records:
        record["cases"][NODE]["phases"]["call"] = 0.01
    records[0]["cases"]["test_sample.py::test_19"]["phases"]["call"] = 0.091
    records[1]["cases"][NODE]["phases"]["call"] = 0.981
    records[2]["wallSeconds"] = 10.01
    limits = budgets.compute_limits(records)
    assert limits["wallSeconds"] == 13  # ceil(12.5125)
    assert limits["perTestSeconds"] == 0.17  # ceil_10ms(.1665)
    assert limits["exceptionCeilings"] == {NODE: 1.51}  # ceil_10ms(1.5015)


@pytest.mark.parametrize("ci", [False, True])
def test_breaches_are_ci_only_and_have_actionable_phase_diagnostics(
    budgets: BudgetApi, ci: bool
) -> None:
    report = _report(wall=14.0)
    report["cases"][NODE]["phases"]["call"] = 2.0
    result = _check(budgets, report, ci=ci)
    assert result["exitCode"] == (1 if ci else 0)
    findings = {finding["rule"]: finding for finding in result["findings"]}
    assert findings[FAST_RULE]["duration"] == 14.0
    finding = findings[CASE_RULE]
    assert finding["test"] == NODE
    assert finding["phase"] == "call"
    assert finding["duration"] == pytest.approx(2.02)
    assert finding["limit"] == 1.5
    assert finding["remedy"].strip()
    assert "tier" not in finding["remedy"].lower()
    assert all("move" not in line.lower() or "tier" not in line.lower() for line in result["lines"])


def test_profile_mismatch_on_ci_is_infrastructure_failure(budgets: BudgetApi) -> None:
    report = _report()
    report["profile"]["arch"] = "ARM64"
    result = _check(budgets, report)
    assert result["exitCode"] == 2
    assert any(finding["kind"] == "infrastructure" for finding in result["findings"])
    assert _check(budgets, report, ci=False)["exitCode"] == 0


@pytest.mark.parametrize("count", [0, 1, 4])
def test_uncalibrated_policy_reports_progress_and_enforces_validity_only(
    budgets: BudgetApi, count: int
) -> None:
    policy = _policy()
    policy.update(wallSeconds=None, perTestSeconds=None, exceptions={})
    result = _check(budgets, _report(wall=1000), policy=policy, baselines=_baselines()[:count])
    assert result["exitCode"] == 0
    assert any(f"{count}/5" in line for line in result["lines"])
    broken = _report()
    broken["complete"] = False
    assert _check(budgets, broken, policy=policy, baselines=[])["exitCode"] != 0


@pytest.mark.parametrize(("deadline", "valid"), [("2026-11-09", False), ("2026-11-10", True)])
def test_exception_deadline_uses_injected_date(
    budgets: BudgetApi, deadline: str, valid: bool
) -> None:
    exception = _exception()
    exception["reviewDeadline"] = deadline
    assert (budgets.evaluate_exceptions({NODE: exception}, now=TODAY) == []) is valid
    policy = _policy()
    policy["exceptions"][NODE] = exception
    assert (_check(budgets, _report(), policy=policy)["exitCode"] == 0) is valid


@pytest.mark.parametrize("field", ["owner", "remedy", "reviewDeadline"])
def test_exception_requires_accountable_remediation(budgets: BudgetApi, field: str) -> None:
    exception = _exception()
    del exception[field]
    assert budgets.evaluate_exceptions({NODE: exception}, now=TODAY)


def _increase(kind: str) -> tuple[Json, Json]:
    proposed = _policy()
    base = copy.deepcopy(proposed)
    if kind == "limit":
        base["wallSeconds"] = 12
    elif kind == "new-exception":
        base["exceptions"] = {}
    elif kind == "deadline":
        base["exceptions"][NODE]["reviewDeadline"] = "2026-11-30"
    else:
        base["formulas"]["wall"] = "ceil_s(1.20*max)"
    return base, proposed


@pytest.mark.parametrize("kind", ["limit", "new-exception", "deadline", "formula"])
def test_every_policy_increase_requires_external_owner_approval(
    budgets: BudgetApi, kind: str
) -> None:
    base, proposed = _increase(kind)
    assert budgets.policy_increases(base, proposed)
    assert _check(budgets, _report(), policy=proposed, base_policy=base)["exitCode"] == 1
    digest = budgets.policy_diff_digest(base, proposed)
    approval = {
        "repository": "owner/repo",
        "reviewId": 123,
        "commit": "revision-a",
        "policyDiffDigest": digest,
    }

    def read_review(repository: str, review_id: int) -> Json:
        return {
            "reviewer": "repo-owner",
            "state": "APPROVED",
            "policyDiffDigest": digest,
            "commit": "revision-a",
        }

    result = _check(
        budgets,
        _report(),
        policy=proposed,
        base_policy=base,
        approval=approval,
        owner="repo-owner",
        read_review=read_review,
    )
    assert result["exitCode"] == 0


@pytest.mark.parametrize("defect", ["reviewer", "digest", "state", "commit", "self-attested"])
def test_unverified_or_unbound_reviews_cannot_authorize(budgets: BudgetApi, defect: str) -> None:
    base, proposed = _increase("limit")
    digest = budgets.policy_diff_digest(base, proposed)
    approval: Json = {
        "repository": "owner/repo",
        "reviewId": 123,
        "commit": "revision-a",
        "policyDiffDigest": digest,
        "ownerApproved": True,
    }
    review: Json = {
        "reviewer": "repo-owner",
        "state": "APPROVED",
        "policyDiffDigest": digest,
        "commit": "revision-a",
    }
    field = {
        "reviewer": "reviewer",
        "digest": "policyDiffDigest",
        "state": "state",
        "commit": "commit",
    }
    if defect == "self-attested":
        review = {}
    else:
        review[field[defect]] = "wrong"
    assert not budgets.verify_approval(
        approval,
        digest=digest,
        owner="repo-owner",
        read_review=lambda repository, review_id: review,
    )
    assert (
        _check(
            budgets,
            _report(),
            base_policy=base,
            approval=approval,
            owner="repo-owner",
            read_review=lambda repository, review_id: review,
        )["exitCode"]
        == 1
    )


def test_tightening_needs_no_approval(budgets: BudgetApi) -> None:
    proposed = _policy()
    base = copy.deepcopy(proposed)
    base["wallSeconds"] = 14
    base["perTestSeconds"] = 0.07
    base["exceptions"][NODE]["limitSeconds"] = 1.6
    base["exceptions"][NODE]["reviewDeadline"] = "2027-01-01"
    assert budgets.policy_increases(base, proposed) == []
    assert _check(budgets, _report(), base_policy=base)["exitCode"] == 0


@pytest.mark.parametrize("field", ["wallSeconds", "perTestSeconds", "exception"])
def test_hand_edited_limits_must_equal_recomputation(budgets: BudgetApi, field: str) -> None:
    policy = _policy()
    if field == "exception":
        policy["exceptions"][NODE]["limitSeconds"] = 1.6
    else:
        policy[field] += 1
    result = _check(budgets, _report(), policy=policy)
    assert result["exitCode"] == 1
    assert any(finding["kind"] == "policy" for finding in result["findings"])


def test_summary_keeps_measured_limit_goal_and_gap_visible(budgets: BudgetApi) -> None:
    result = _check(budgets, _report(wall=40))
    summaries = [line.lower() for line in result["lines"] if "goal" in line.lower()]
    assert any(
        "gap" in line and all(str(value) in line for value in (40, 13, 30, 10))
        for line in summaries
    )


@pytest.mark.parametrize("defect", ["none", "revision", "profile", "incomplete", "unsuccessful"])
def test_importer_promotes_only_comparable_successful_measurements(
    budgets: BudgetApi, defect: str
) -> None:
    records = _baselines()
    if defect == "revision":
        records[0]["revision"] = "other-revision"
    elif defect == "profile":
        records[0]["profile"]["workers"] = 2
    elif defect == "incomplete":
        records[0]["complete"] = False
    elif defect == "unsuccessful":
        records[0]["measurement"]["successful"] = False
    if defect != "none":
        with pytest.raises(ValueError, match=r"TEST\.budget\."):
            budgets.record_reports(records, revision="revision-a", profile=PROFILE)
    else:
        promoted = budgets.record_reports(records, revision="revision-a", profile=PROFILE)
        assert len(promoted) == 5
        assert [record["wallSeconds"] for record in promoted] == [10.0] * 5
        assert promoted[0]["cases"][NODE]["phases"]["call"] == 0.98
