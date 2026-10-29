"""Progression (plan D3; requirements F4): continue by default, halt via ``min_reward``.

Halting is Harbor's native ``[[steps]].min_reward`` gate on the verifier's step reward
``halt_gate``: the share of the step's solvable cases (the reference's reliably passing
set, pinned by the lock) that passed, so ``1.0`` is the solvable verdict "solved". A step the
lock pins with an empty solvable set (excluded, e.g. ``no_new_scoreable_tests``) has no
verdict: the verifier reports a clearing ``halt_gate`` plus ``solvable_excluded`` and no
``solvable_pass_rate``, so the run executes it and advances, as the scorer's continue-mode
skipping treats it. The gate never reads the raw ``strict_pass_rate``: a case the reference
itself fails or varies on cannot stop a trial. The verifier omits the key when the step has no
verdict (infrastructure failure, verifier leak, no pinned entry), and Harbor treats a missing
key as -inf, so those steps halt too.

Because the agent never sees verifier results (F4), a continue run's first k
checkpoints are exactly what a halt run would have executed. The strict
``reached`` vector is therefore derived from a continue run by replaying the
gate over the recorded step rewards: checkpoint k is reached iff every
earlier checkpoint was attempted and passed the gate.

``reached`` is always SCB-strict: it replays the raw strict gate
(``strict_pass_rate >= 1.0``, i.e. solved) in both modes, whatever the halt gate or its
``min_reward``. A halt run executes past a raw failure the solvable gate forgives, and past a
partial pass with ``min_reward < 1``, but those later checkpoints are not strictly reached, so a
halt run and a continue run of the same agent behaviour give the same ``reached``. The halt gate
itself is only used to check that Harbor executed exactly what the protocol allows
(``collect._halt_check``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from raidar.contract.trial import ProgressionRule
from raidar.design.experiment.schema import ProtocolSpec
from raidar.grading.solvable import HALT_GATE_REWARD_KEY

GATE_REWARD_KEY = HALT_GATE_REWARD_KEY
STRICT_REWARD_KEY = "strict_pass_rate"
STRICT_THRESHOLD = 1.0


def progression_rule(protocol: ProtocolSpec) -> ProgressionRule:
    if protocol.progression == "halt_on_failure":
        return ProgressionRule(
            mode=protocol.progression, reward_key=GATE_REWARD_KEY, threshold=protocol.min_reward
        )
    return ProgressionRule(mode="continue", reward_key=STRICT_REWARD_KEY, threshold=None)


STRICT_RULE = ProgressionRule(
    mode="continue", reward_key=STRICT_REWARD_KEY, threshold=STRICT_THRESHOLD
)
"""The SCB-strict gate over the raw strict rate (``reached`` in either mode)."""


def gate_passes(rewards: Mapping[str, float] | None, rule: ProgressionRule) -> bool:
    """Harbor's ``min_reward`` semantics: a missing reward or key is -inf."""
    threshold = rule.threshold if rule.threshold is not None else STRICT_THRESHOLD
    value = (rewards or {}).get(rule.reward_key, float("-inf"))
    return value >= threshold


def derive_reached(
    rewards_by_step: Sequence[Mapping[str, float] | None],
    rule: ProgressionRule,
    *,
    runnable: int | None = None,
) -> tuple[bool, ...]:
    """Strict ``reached`` per planned checkpoint from a continue run's step rewards.

    ``rewards_by_step`` has one entry per planned checkpoint in order: the
    verifier rewards of an attempted step, ``None`` for a step without a verdict
    or never attempted. ``runnable`` caps the trial (a truncated run),
    exactly as it caps what a halt run could execute.
    """
    limit = len(rewards_by_step) if runnable is None else runnable
    reached: list[bool] = []
    open_ = True
    for index, rewards in enumerate(rewards_by_step):
        open_ = open_ and index < limit
        reached.append(open_)
        open_ = open_ and gate_passes(rewards, rule)
    return tuple(reached)
