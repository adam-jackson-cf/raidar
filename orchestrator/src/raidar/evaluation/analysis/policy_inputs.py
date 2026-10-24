"""Headline S from statused components: the one place a Metric is applied (F6, F15).

Analysis (``assessed.headline_score``), the review projection
(``review.projection.headline``) and the Quality projection
(``review.quality.run_view``) all weigh components through ``score_inputs``: a
measured component is weighed, an unmeasured one makes S ineligible with its
reason, and a not-applicable one is left out. Each caller keeps its own reason
text; the policy call and the partition are shared.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from raidar.evaluation.analysis.outcomes import Value
from raidar.scoring.framework.metrics import ComponentValue, Metric, MetricScore
from raidar.scoring.framework.result import AssessStatus


@dataclass(frozen=True)
class ComponentInput:
    """A measured ``value``, or the ``gap`` reason it is unmeasured; neither: not applicable."""

    value: ComponentValue | None = None
    gap: str | None = None

    @classmethod
    def from_value(cls, value: Value) -> ComponentInput:
        """Analysis semantics: ``ok`` is weighed, ``not_applicable`` is left out."""
        if value.value is not None:
            return cls(value=value.value)
        if value.status == AssessStatus.NOT_APPLICABLE.value:
            return cls()
        return cls(gap=f"{value.status}: {value.reason}")


def score_inputs(
    policy: Metric, checkpoints: int, inputs: Mapping[str, ComponentInput]
) -> MetricScore:
    """Apply ``policy`` to the measured components; each gap is an ineligibility reason."""
    values = {key: item.value for key, item in inputs.items() if item.value is not None}
    ineligible = {
        key: [item.gap]
        for key, item in inputs.items()
        if item.value is None and item.gap is not None
    }
    return policy.score(checkpoints=checkpoints, components=values, ineligible=ineligible)
