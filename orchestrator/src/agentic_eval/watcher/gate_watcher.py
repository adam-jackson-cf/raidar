"""Verification gate watching and event tracking."""

import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from ..config import settings
from ..schemas.events import FAILURE_CATEGORIES, GateEvent
from ..schemas.task import VerificationGate


def categorize_failure(stdout: str, stderr: str) -> str | None:
    """Categorize a failure based on output patterns.

    Args:
        stdout: Standard output from command
        stderr: Standard error from command

    Returns:
        Category ID or None if no match
    """
    combined = stdout + stderr

    for category_id, pattern, _ in FAILURE_CATEGORIES:
        if re.search(pattern, combined):
            return category_id

    return "unknown" if combined.strip() else None


def truncate_output(output: str, max_length: int | None = None) -> str:
    """Truncate output to max length."""
    if max_length is None:
        max_length = settings.gate.max_output_length

    if len(output) <= max_length:
        return output
    return output[:max_length] + f"\n... (truncated, {len(output)} total chars)"


class GateWatcher:
    """Watches verification gate executions and tracks failures."""

    def __init__(self, max_failures: int | None = None):
        self.max_failures = max_failures or settings.gate.max_failures
        self.events: list[GateEvent] = []
        self.failure_categories_seen: set[str] = set()
        self.total_failures = 0

    def should_terminate(self) -> bool:
        """Check if we should terminate due to max failures."""
        return self.total_failures >= self.max_failures

    def run_gate(self, gate: VerificationGate, workspace: Path) -> GateEvent:
        """Run a verification gate and record the result.

        Args:
            gate: Gate configuration
            workspace: Working directory

        Returns:
            GateEvent with execution results
        """
        timestamp = datetime.now(UTC).isoformat()

        try:
            result = subprocess.run(
                gate.command,
                shell=True,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=settings.timeouts.gate,
            )
            exit_code = result.returncode
            stdout = result.stdout
            stderr = result.stderr
        except subprocess.TimeoutExpired:
            exit_code = -1
            stdout = ""
            stderr = "Command timed out"

        failure_category = None
        is_repeat = False

        if exit_code != 0:
            self.total_failures += 1
            failure_category = categorize_failure(stdout, stderr)

            if failure_category:
                is_repeat = failure_category in self.failure_categories_seen
                self.failure_categories_seen.add(failure_category)

        event = GateEvent(
            timestamp=timestamp,
            gate_name=gate.name,
            command=gate.command,
            exit_code=exit_code,
            stdout=truncate_output(stdout),
            stderr=truncate_output(stderr),
            failure_category=failure_category,
            is_repeat=is_repeat,
        )

        self.events.append(event)
        return event

    def run_all_gates(self, gates: list[VerificationGate], workspace: Path) -> list[GateEvent]:
        """Run all verification gates.

        Args:
            gates: List of gate configurations
            workspace: Working directory

        Returns:
            List of all gate events
        """
        for gate in gates:
            event = self.run_gate(gate, workspace)

            if event.exit_code != 0 and gate.on_failure == "terminate":
                break

            if self.should_terminate():
                break

        return self.events

    def get_summary(self) -> dict:
        """Get summary of gate executions."""
        passed = sum(1 for e in self.events if e.exit_code == 0)
        failed = len(self.events) - passed
        repeat_failures = sum(1 for e in self.events if e.is_repeat)

        return {
            "total_gates": len(self.events),
            "passed": passed,
            "failed": failed,
            "unique_failure_categories": len(self.failure_categories_seen),
            "repeat_failures": repeat_failures,
            "terminated_early": self.should_terminate(),
        }
