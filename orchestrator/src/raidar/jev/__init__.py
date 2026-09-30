"""Jev: calibrated, cached, audited classifier judgments (F7, N5; plan WS-H).

Entry points for scorers:

- ``open_runtime(store_root, ...)`` builds the policy-bound ``JevRuntime``.
- ``JevRuntime.decide`` / ``record_deterministic``: every decision is audited.
- ``raidar.jev.process``: the process-compliance residue interface.
- ``raidar.jev.rubric.RubricRunner``: SCB rubric criteria per changed symbol.
- ``raidar.jev.gating.verdict_observations``: keeps shadow Jev observations
  out of ScorePolicies and verdicts.
"""

from raidar.jev.audit import AuditLog, DecisionRecord, InputSufficiency, SufficiencyLevel
from raidar.jev.policies import JevPolicy, PolicyMode, PolicyRegistry
from raidar.jev.runtime import DecisionOutcome, JevRuntime
from raidar.jev.session import open_runtime

__all__ = [
    "AuditLog",
    "DecisionOutcome",
    "DecisionRecord",
    "InputSufficiency",
    "JevPolicy",
    "JevRuntime",
    "PolicyMode",
    "PolicyRegistry",
    "SufficiencyLevel",
    "open_runtime",
]
