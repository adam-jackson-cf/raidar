"""Progression (plan D3; requirements F4): continue by default, halt via ``min_reward``.

Halting is Harbor's native ``[[steps]].min_reward`` gate on the verifier's step
reward ``strict_pass_rate``. The oracle omits every rate when the verdict is
unavailable (infrastructure failure, verifier leak, no graded case), and Harbor
treats a missing key as -inf, so those steps halt too.

Because the agent never sees verifier results (F4), a continue run's first k
checkpoints are exactly what a halt run would have executed. The strict
``reached`` vector is therefore derived from a continue run by replaying the
gate over the recorded step rewards: checkpoint k is reached iff every
earlier checkpoint was attempted and passed the gate.

``reached`` is always SCB-strict: it replays the strict gate
(``strict_pass_rate >= 1.0``, i.e. solved) in both modes, whatever halting
``min_reward`` the protocol sets. A halt run with ``min_reward < 1`` executes
past a partial pass, but those later checkpoints are not strictly reached, so
a halt run and a continue run of the same agent behaviour give the same
``reached``. The halt gate itself is only used to check that Harbor executed
exactly what the protocol allows (``collect._halt_check``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from raidar.contract.trial import ProgressionRule
from raidar.design.experiment.schema import ProtocolSpec

GATE_REWARD_KEY = "strict_pass_rate"
STRICT_THRESHOLD = 1.0


def progression_rule(protocol: ProtocolSpec) -> ProgressionRule:
    threshold = protocol.min_reward if protocol.progression == "halt_on_failure" else None
    return ProgressionRule(
        mode=protocol.progression, reward_key=GATE_REWARD_KEY, threshold=threshold
    )


def strict_rule(rule: ProgressionRule) -> ProgressionRule:
    """The SCB-strict gate over the same reward key (``reached`` in either mode)."""
    return ProgressionRule(mode="continue", reward_key=rule.reward_key, threshold=STRICT_THRESHOLD)


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
