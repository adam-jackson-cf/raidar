"""Acceptance scoring: deterministic checks."""

import re
from dataclasses import dataclass
from pathlib import Path

from ..schemas.scenario import AcceptanceConfig, DeterministicCheck
from ..schemas.scorecard import AcceptanceCheck, AcceptanceScore, MetricScore


@dataclass
class JudgeResult:
    """Structured result from LLM judge response parsing."""

    passed: bool
    evidence: str
    raw_response: str


def parse_judge_response(response: str) -> JudgeResult:
    """Parse LLM judge response with multiple fallback strategies.

    Strategy 1: Look for structured VERDICT: PASS/FAIL + EVIDENCE: pattern
    Strategy 2: Check first line for PASS/FAIL keywords
    Strategy 3: Fail conservatively if unparseable

    Args:
        response: Raw LLM response text

    Returns:
        JudgeResult with parsed verdict and evidence
    """
    response = response.strip()

    # Strategy 1: Structured format with VERDICT: and EVIDENCE:
    verdict_match = re.search(r"VERDICT:\s*(PASS|FAIL)", response, re.IGNORECASE)
    evidence_match = re.search(r"EVIDENCE:\s*(.+?)(?=\n\n|\Z)", response, re.IGNORECASE | re.DOTALL)

    if verdict_match:
        passed = verdict_match.group(1).upper() == "PASS"
        evidence = evidence_match.group(1).strip() if evidence_match else response[:200]
        return JudgeResult(passed=passed, evidence=evidence, raw_response=response)

    # Strategy 2: Check first line for PASS/FAIL
    first_line = response.split("\n")[0].upper()
    if "PASS" in first_line and "FAIL" not in first_line:
        return JudgeResult(
            passed=True,
            evidence=response[:200],
            raw_response=response,
        )
    if "FAIL" in first_line:
        return JudgeResult(
            passed=False,
            evidence=response[:200],
            raw_response=response,
        )

    # Strategy 3: Fail conservatively if unparseable
    return JudgeResult(
        passed=False,
        evidence=f"Could not parse response: {response[:100]}...",
        raw_response=response,
    )


def check_import_present(workspace: Path, pattern: str) -> tuple[bool, str]:
    """Check if an import pattern is present in source files."""
    src_dir = workspace / "src"
    if not src_dir.exists():
        return False, "src directory not found"

    for ts_file in src_dir.rglob("*.ts"):
        content = ts_file.read_text()
        if pattern in content:
            return True, f"Found in {ts_file.relative_to(workspace)}"

    for tsx_file in src_dir.rglob("*.tsx"):
        content = tsx_file.read_text()
        if pattern in content:
            return True, f"Found in {tsx_file.relative_to(workspace)}"

    return False, f"Pattern '{pattern}' not found in any source file"


def check_file_exists(workspace: Path, pattern: str) -> tuple[bool, str]:
    """Check if files matching pattern exist."""
    matches = list(workspace.glob(pattern))
    if matches:
        return True, f"Found {len(matches)} matching files"
    return False, f"No files matching '{pattern}'"


def _has_nested_quantifier(pattern: str) -> bool:
    nested_quantifier = re.compile(r"\((?:[^()\\]|\\.|\([^()]*\))*[+*](?:[^()\\]|\\.)*\)[+*{]")
    return bool(nested_quantifier.search(pattern))


def _has_ambiguous_repeated_alternation(pattern: str) -> bool:
    group_pattern = re.compile(r"\(([^()\\]*(?:\\.[^()\\]*)*)\)([+*]|\{\d+,?\d*\})")
    for match in group_pattern.finditer(pattern):
        alternatives = [part for part in match.group(1).split("|") if part]
        alternatives.sort(key=len)
        for index, alternative in enumerate(alternatives):
            if any(other.startswith(alternative) for other in alternatives[index + 1 :]):
                return True
    return False


def validate_safe_regex_pattern(pattern: str) -> tuple[bool, str]:
    """Validate scenario-authored regex before compiling it."""
    if len(pattern) > 512:
        return False, "Pattern exceeds 512 characters"
    if _has_nested_quantifier(pattern):
        return False, "Pattern contains nested quantifiers with ReDoS risk"
    if _has_ambiguous_repeated_alternation(pattern):
        return False, "Pattern contains ambiguous repeated alternation with ReDoS risk"
    return True, "Pattern passed regex safety validation"


def check_no_pattern(workspace: Path, pattern: str) -> tuple[bool, str]:
    """Check that a pattern does NOT appear in source files."""
    src_dir = workspace / "src"
    if not src_dir.exists():
        return True, "src directory not found (pattern check passes)"

    safe, reason = validate_safe_regex_pattern(pattern)
    if not safe:
        return False, f"Unsafe regex pattern '{pattern}': {reason}"

    regex = re.compile(pattern)

    for ts_file in src_dir.rglob("*.ts"):
        content = ts_file.read_text()
        if regex.search(content):
            return False, f"Pattern found in {ts_file.relative_to(workspace)}"

    for tsx_file in src_dir.rglob("*.tsx"):
        content = tsx_file.read_text()
        if regex.search(content):
            return False, f"Pattern found in {tsx_file.relative_to(workspace)}"

    return True, "Pattern not found (good)"


def run_deterministic_check(check: DeterministicCheck, workspace: Path) -> AcceptanceCheck:
    """Run a single deterministic acceptance check."""
    if check.type == "import_present":
        passed, evidence = check_import_present(workspace, check.pattern)
    elif check.type == "file_exists":
        passed, evidence = check_file_exists(workspace, check.pattern)
    elif check.type == "no_pattern":
        passed, evidence = check_no_pattern(workspace, check.pattern)
    else:
        passed, evidence = False, f"Unknown check type: {check.type}"

    return AcceptanceCheck(
        rule=check.description,
        type="deterministic",
        passed=passed,
        evidence=evidence,
    )


def evaluate_acceptance(
    workspace: Path,
    config: AcceptanceConfig,
) -> AcceptanceScore:
    """Evaluate acceptance against scenario configuration."""
    checks = _collect_acceptance_checks(workspace, config)
    return _score_acceptance_checks(checks)


def evaluate_llm_as_judge_metric(
    *,
    workspace: Path,
    scenario_dir: Path,
    scenario: object,
    metric_id: str,
    judge_path: str,
) -> MetricScore:
    """Evaluate the scorer-level LLM-as-judge metric."""

    from .llm_as_judge import evaluate_llm_as_judge_metric as _evaluate

    return _evaluate(
        workspace=workspace,
        scenario_dir=scenario_dir,
        scenario=scenario,
        metric_id=metric_id,
        judge_path=judge_path,
    )


def _collect_acceptance_checks(
    workspace: Path,
    config: AcceptanceConfig,
) -> list[AcceptanceCheck]:
    checks = list(_run_deterministic_checks(config.deterministic_checks, workspace))
    return checks


def _run_deterministic_checks(
    deterministic_checks: list[DeterministicCheck],
    workspace: Path,
) -> list[AcceptanceCheck]:
    return [run_deterministic_check(check, workspace) for check in deterministic_checks]


def _score_acceptance_checks(checks: list[AcceptanceCheck]) -> AcceptanceScore:
    if not checks:
        return AcceptanceScore(checks=[])
    return AcceptanceScore(checks=checks)


def _ratio_passed(checks: list[AcceptanceCheck]) -> float:
    if not checks:
        return 1.0
    return sum(1 for c in checks if c.passed) / len(checks)
