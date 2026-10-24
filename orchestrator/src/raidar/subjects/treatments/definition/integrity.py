"""Integrity flags: what a step's evidence shows the agent did to a component's guardrails.

A discovery probe decides whether a component took effect; a failed probe makes
the step infra-invalid (F5). Some agent behaviour must instead be recorded
without failing anything: editing a workspace copy of a protected config, or
trying to get round a commit hook. A component reports such behaviour through
``step_flags``; ``condition_step_flags`` collects every use's flags for one
archived step, and the step result keeps them beside its probe summary
(``ProbeSummary.flags``). A flag never changes a step's state or validity.

A definition opts in by implementing ``step_flags(use, evidence, step_index)``.
This module is separate from the component contract (``manifest.py``,
``compiled.py``), so adding it moved no released component's digest.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from raidar.subjects.treatments.definition.evidence import StepEvidence
from raidar.subjects.treatments.definition.manifest import ComponentConfigError, ComponentUseContext

if TYPE_CHECKING:
    from raidar.subjects.treatments.definition.compiled import CompiledCondition


@runtime_checkable
class FlaggingComponent(Protocol):
    def step_flags(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> tuple[str, ...]: ...


def condition_step_flags(
    compiled: CompiledCondition, agent_dir: Path, step_index: int
) -> tuple[str, ...]:
    """Every flagging use's flags for one archived step, as ``<ref>/<slug>: <flag>``.

    A flag check that cannot read its evidence is itself recorded as a flag:
    an unreadable record must not hide a tampering signal, nor fail the step.
    """
    evidence = StepEvidence(agent_dir)
    flags: list[str] = []
    for use in compiled.uses:
        if not isinstance(use.definition, FlaggingComponent):
            continue
        try:
            found = use.definition.step_flags(use.context, evidence, step_index)
        except (ComponentConfigError, OSError, ValueError) as exc:
            found = (f"flag-check-error: {exc}",)
        flags += [f"{use.ref}/{use.slug}: {flag}" for flag in found]
    return tuple(flags)
