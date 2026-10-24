"""Jev: calibrated, cached, audited classifier judgments (F7, N5; plan WS-H).

Entry points for scorers:

- ``open_runtime(store_root, ...)`` builds the policy-bound ``JevRuntime``.
- ``JevRuntime.decide`` / ``record_deterministic``: every decision is audited.
- ``raidar.scoring.judge.rubric.RubricRunner``: SCB rubric criteria per changed symbol.
- ``raidar.scoring.judge.gating.verdict_observations``: keeps shadow Jev observations
  out of Metrics and verdicts.
"""

from raidar.scoring.judge.audit import AuditLog, DecisionRecord, InputSufficiency, SufficiencyLevel
from raidar.scoring.judge.policies import JevPolicy, PolicyMode, PolicyRegistry
from raidar.scoring.judge.runtime import DecisionOutcome, JevRuntime
from raidar.scoring.judge.session import open_runtime

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
