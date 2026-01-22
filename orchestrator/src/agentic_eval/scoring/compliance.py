"""Compliance scoring: deterministic checks and LLM judge."""

import re
from pathlib import Path

from litellm import completion

from ..config import settings
from ..schemas.scorecard import ComplianceCheck, ComplianceScore
from ..schemas.task import ComplianceConfig, DeterministicCheck, LLMJudgeCriterion


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


def check_no_pattern(workspace: Path, pattern: str) -> tuple[bool, str]:
    """Check that a pattern does NOT appear in source files."""
    src_dir = workspace / "src"
    if not src_dir.exists():
        return True, "src directory not found (pattern check passes)"

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


def run_deterministic_check(
    check: DeterministicCheck, workspace: Path
) -> ComplianceCheck:
    """Run a single deterministic compliance check."""
    if check.type == "import_present":
        passed, evidence = check_import_present(workspace, check.pattern)
    elif check.type == "file_exists":
        passed, evidence = check_file_exists(workspace, check.pattern)
    elif check.type == "no_pattern":
        passed, evidence = check_no_pattern(workspace, check.pattern)
    else:
        passed, evidence = False, f"Unknown check type: {check.type}"

    return ComplianceCheck(
        rule=check.description,
        type="deterministic",
        passed=passed,
        evidence=evidence,
    )


def collect_source_code(workspace: Path, max_chars: int | None = None) -> str:
    """Collect source code for LLM evaluation."""
    if max_chars is None:
        max_chars = settings.llm_judge.max_source_chars

    src_dir = workspace / "src"
    if not src_dir.exists():
        return "No source directory found"

    collected: list[str] = []
    total_chars = 0

    for file_path in sorted(src_dir.rglob("*.tsx")) + sorted(src_dir.rglob("*.ts")):
        if total_chars >= max_chars:
            break

        content = file_path.read_text()
        rel_path = file_path.relative_to(workspace)
        file_block = f"=== {rel_path} ===\n{content}\n"

        if total_chars + len(file_block) > max_chars:
            remaining = max_chars - total_chars
            file_block = file_block[:remaining] + "\n... (truncated)"

        collected.append(file_block)
        total_chars += len(file_block)

    return "\n".join(collected)


def run_llm_judge(
    criterion: LLMJudgeCriterion,
    source_code: str,
    rules_content: str,
    judge_model: str | None = None,
) -> ComplianceCheck:
    """Run LLM judge for a criterion."""
    if judge_model is None:
        judge_model = settings.llm_judge.model

    prompt = f"""You are evaluating code compliance with project guidelines.

## Project Rules
{rules_content}

## Source Code
{source_code}

## Evaluation Criterion
{criterion.criterion}

Evaluate whether the code follows this criterion. Respond with:
1. PASS or FAIL
2. Brief evidence (1-2 sentences)

Format:
VERDICT: [PASS/FAIL]
EVIDENCE: [your evidence]"""

    try:
        response = completion(
            model=judge_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=settings.llm_judge.max_tokens,
        )
        result = response.choices[0].message.content or ""

        passed = "PASS" in result.upper().split("\n")[0]
        evidence_match = re.search(r"EVIDENCE:\s*(.+)", result, re.IGNORECASE)
        evidence = evidence_match.group(1).strip() if evidence_match else result[:100]

        return ComplianceCheck(
            rule=criterion.criterion,
            type="llm_judge",
            passed=passed,
            evidence=evidence,
        )
    except Exception as e:
        return ComplianceCheck(
            rule=criterion.criterion,
            type="llm_judge",
            passed=False,
            evidence=f"LLM judge error: {e}",
        )


def evaluate_compliance(
    workspace: Path,
    config: ComplianceConfig,
    rules_path: Path | None = None,
    run_llm_checks: bool = True,
) -> ComplianceScore:
    """Evaluate compliance against task configuration.

    Args:
        workspace: Path to workspace directory
        config: Compliance configuration from task
        rules_path: Path to rules file for LLM context
        run_llm_checks: Whether to run LLM judge checks

    Returns:
        ComplianceScore with all check results
    """
    checks: list[ComplianceCheck] = []

    # Run deterministic checks
    for check in config.deterministic_checks:
        result = run_deterministic_check(check, workspace)
        checks.append(result)

    # Run LLM judge checks if enabled
    if run_llm_checks and config.llm_judge_rubric:
        source_code = collect_source_code(workspace)
        rules_content = ""
        if rules_path and rules_path.exists():
            rules_content = rules_path.read_text()

        for criterion in config.llm_judge_rubric:
            result = run_llm_judge(criterion, source_code, rules_content)
            checks.append(result)

    # Calculate weighted score
    if not checks:
        return ComplianceScore(score=1.0, checks=[])

    # Weight deterministic and LLM checks differently
    deterministic_checks = [c for c in checks if c.type == "deterministic"]
    llm_checks = [c for c in checks if c.type == "llm_judge"]

    det_score = sum(1 for c in deterministic_checks if c.passed) / len(deterministic_checks) if deterministic_checks else 1.0
    llm_score = sum(1 for c in llm_checks if c.passed) / len(llm_checks) if llm_checks else 1.0

    # Weighted average: 60% deterministic, 40% LLM
    if llm_checks:
        score = (det_score * 0.6) + (llm_score * 0.4)
    else:
        score = det_score

    return ComplianceScore(score=score, checks=checks)
