"""Metric registry (F15, ADR-001 §2.4).

Scorers only observe; weights live here. A policy turns problem-level component
values (C, V, E, H, R, G, each in [0, 1]) into ``S = 100·C·(0.70 + 0.30·I)``.
Arithmetic follows the SCB fork's canonical scoring (``metrics/scoring/schema.py``):
Decimal, precision 50, ROUND_HALF_EVEN, components quantised to 12 places and S
to 6 places.

C is the fork's ``C_p = sum(C_p,k)/K_p`` (``problem_correctness``): the mean over
the K planned checkpoints of each checkpoint's case pass rate (passed / graded
cases, skipped cases left out). A checkpoint the trial never reached
counts 0 (the fork's unproduced checkpoint); a reached checkpoint with no
graded case makes C ineligible (the fork's ``canonical_test_denominator_zero``).
It is not the checkpoint solve rate solved/K (``checkpoint_solve_rate``), which
is the primary outcome, not a score component.

A missing or ineligible component makes the whole score ineligible with typed
reasons; it is never replaced by zero (F6). ``scb-canonical@1`` and
``scb-canonical-oneshot@1`` apply that rule to every component, exactly as the
fork does. Their ``@2`` versions add one rule (``zero_correctness``): a
measured C = 0 scores S = 0 even when a quality component is missing or
ineligible, because S = 100·0·(...) = 0 whatever the quality values are, and
dropping those trials would bias paired comparisons towards the arm that
fails more. Ineligibility then applies only when C > 0 (or C itself is
unmeasured).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum
from pathlib import Path

from raidar.spec.canonical import digest_json
from raidar.spec.references import (
    RefKind,
    RegistryRef,
    RegistryReferenceError,
    ResolvedReference,
)

COMPONENT_IDS = ("C", "V", "E", "H", "R", "G")
CORRECTNESS = "C"
_CONTEXT_PRECISION = 50
COMPONENT_PLACES = Decimal("0.000000000001")
SCORE_PLACES = Decimal("0.000001")
_BASE_SHARE = Decimal("0.70")
_INERTIA_SHARE = Decimal("0.30")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_ONE = Decimal("1")
CORRECTNESS_DEFINITION = (
    "C=sum_k(passed_k/graded_k)/K over planned checkpoints; unreached checkpoint=0; "
    "reached checkpoint with graded_k=0 -> ineligible"
)
ZERO_CORRECTNESS_DEFINITION = "C=0 -> S=0 even when a quality component is missing or ineligible"

ComponentValue = Decimal | float | int


class ComponentStatus(StrEnum):
    USED = "used"
    NOT_APPLICABLE = "not_applicable"
    MISSING = "missing"
    INELIGIBLE = "ineligible"


class IneligibilityCode(StrEnum):
    COMPONENT_MISSING = "component_missing"
    COMPONENT_INELIGIBLE = "component_ineligible"
    CHECKPOINT_COUNT_UNSUPPORTED = "checkpoint_count_unsupported"


@dataclass(frozen=True)
class IneligibilityReason:
    code: IneligibilityCode
    component: str | None
    detail: str


@dataclass(frozen=True)
class MetricScore:
    """Outcome of applying a policy. ``score`` is None whenever ineligible.

    ``zero_correctness`` marks S = 0 from the ``zero_correctness`` rule: C was
    measured as 0, so quality gaps (still visible in ``components``) did not
    make the score ineligible. ``inertia`` is then None unless every weighted
    component was measured.
    """

    policy: str
    checkpoints: int
    components: Mapping[str, ComponentStatus]
    reasons: tuple[IneligibilityReason, ...] = ()
    correctness: Decimal | None = None
    inertia: Decimal | None = None
    score: Decimal | None = None
    zero_correctness: bool = False

    @property
    def eligible(self) -> bool:
        return not self.reasons


def _unit_decimal(component: str, value: ComponentValue) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal | float | int):
        raise TypeError(f"component {component} must be numeric, got {type(value).__name__}")
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if not number.is_finite() or number < _ZERO or number > _ONE:
        raise ValueError(f"component {component} must be a finite value in [0, 1], got {value}")
    return number


@dataclass(frozen=True)
class Metric:
    """A versioned headline metric: inertia weights plus applicability rules.

    ``runtimes`` are the runtimes whose quality components (V, E, H, R, G) a
    registered scorer can measure; the compatibility gate refuses the metric
    elsewhere (``RT4``). It is a gate fact, not part of the definition digest.
    """

    name: str
    version: str
    inertia_weights: Mapping[str, Decimal]
    min_checkpoints: int
    max_checkpoints: int | None = None
    not_applicable: frozenset[str] = field(default_factory=frozenset)
    zero_correctness: bool = False
    description: str = ""
    runtimes: tuple[str, ...] = ("python",)

    def __post_init__(self) -> None:
        unknown = (set(self.inertia_weights) | set(self.not_applicable)) - set(COMPONENT_IDS)
        if unknown or CORRECTNESS in self.inertia_weights:
            raise ValueError(f"policy {self.ref} names invalid inertia components {unknown}")
        if set(self.inertia_weights) & self.not_applicable:
            raise ValueError(f"policy {self.ref} weights a not_applicable component")
        if sum(self.inertia_weights.values(), _ZERO) != _ONE:
            raise ValueError(f"policy {self.ref} inertia weights must sum to 1")

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def supports(self, checkpoints: int) -> bool:
        if checkpoints < self.min_checkpoints:
            return False
        return self.max_checkpoints is None or checkpoints <= self.max_checkpoints

    def definition(self) -> dict[str, object]:
        return {
            "ref": self.ref,
            "formula": "S=100*C*(0.70+0.30*I)",
            "correctness": CORRECTNESS_DEFINITION,
            "zero_correctness": ZERO_CORRECTNESS_DEFINITION if self.zero_correctness else None,
            "inertia_weights": {key: str(value) for key, value in self.inertia_weights.items()},
            "not_applicable": sorted(self.not_applicable),
            "min_checkpoints": self.min_checkpoints,
            "max_checkpoints": self.max_checkpoints,
            "rounding": "ROUND_HALF_EVEN",
            "component_places": str(COMPONENT_PLACES),
            "score_places": str(SCORE_PLACES),
        }

    def digest(self) -> str:
        return digest_json(self.definition())

    def score(
        self,
        *,
        checkpoints: int,
        components: Mapping[str, ComponentValue],
        ineligible: Mapping[str, Sequence[str]] | None = None,
    ) -> MetricScore:
        """Apply the policy to problem-level components.

        ``components`` holds values in [0, 1]. ``ineligible`` carries scorer-typed
        reasons for components that could not be measured; they take precedence
        over any value supplied for the same component.
        """
        ineligible = ineligible or {}
        unknown = (set(components) | set(ineligible)) - set(COMPONENT_IDS)
        if unknown:
            raise ValueError(f"unknown components {sorted(unknown)}")
        values = {key: _unit_decimal(key, value) for key, value in components.items()}
        statuses, reasons = self._classify(values, ineligible)
        if not self.supports(checkpoints):
            reasons.insert(
                0,
                IneligibilityReason(
                    code=IneligibilityCode.CHECKPOINT_COUNT_UNSUPPORTED,
                    component=None,
                    detail=f"{self.ref} supports K in [{self.min_checkpoints}, "
                    f"{self.max_checkpoints or 'inf'}], got K={checkpoints}",
                ),
            )
        if reasons and self._zero_correctness_applies(checkpoints, values, statuses):
            return MetricScore(
                policy=self.ref,
                checkpoints=checkpoints,
                components=statuses,
                correctness=_ZERO.quantize(COMPONENT_PLACES),
                score=_ZERO.quantize(SCORE_PLACES),
                zero_correctness=True,
            )
        if reasons:
            return MetricScore(
                policy=self.ref,
                checkpoints=checkpoints,
                components=statuses,
                reasons=tuple(reasons),
            )
        with localcontext() as context:
            context.prec = _CONTEXT_PRECISION
            context.rounding = ROUND_HALF_EVEN
            inertia = sum(
                (weight * values[key] for key, weight in self.inertia_weights.items()), _ZERO
            )
            correctness = values[CORRECTNESS]
            score = _HUNDRED * correctness * (_BASE_SHARE + _INERTIA_SHARE * inertia)
            return MetricScore(
                policy=self.ref,
                checkpoints=checkpoints,
                components=statuses,
                correctness=correctness.quantize(COMPONENT_PLACES),
                inertia=inertia.quantize(COMPONENT_PLACES),
                score=score.quantize(SCORE_PLACES),
                zero_correctness=self.zero_correctness and correctness == _ZERO,
            )

    def _zero_correctness_applies(
        self,
        checkpoints: int,
        values: Mapping[str, Decimal],
        statuses: Mapping[str, ComponentStatus],
    ) -> bool:
        """True when only quality gaps stand between a measured C = 0 and S = 0."""
        return (
            self.zero_correctness
            and self.supports(checkpoints)
            and statuses.get(CORRECTNESS) is ComponentStatus.USED
            and values[CORRECTNESS] == _ZERO
        )

    def _classify(
        self, values: Mapping[str, Decimal], ineligible: Mapping[str, Sequence[str]]
    ) -> tuple[dict[str, ComponentStatus], list[IneligibilityReason]]:
        statuses: dict[str, ComponentStatus] = {}
        reasons: list[IneligibilityReason] = []
        required = (CORRECTNESS, *self.inertia_weights)
        for component in COMPONENT_IDS:
            if component in self.not_applicable:
                statuses[component] = ComponentStatus.NOT_APPLICABLE
            elif component not in required:
                continue
            elif component in ineligible:
                statuses[component] = ComponentStatus.INELIGIBLE
                upstream = ", ".join(ineligible[component]) or "unspecified"
                reasons.append(
                    IneligibilityReason(IneligibilityCode.COMPONENT_INELIGIBLE, component, upstream)
                )
            elif component not in values:
                statuses[component] = ComponentStatus.MISSING
                reasons.append(
                    IneligibilityReason(
                        IneligibilityCode.COMPONENT_MISSING,
                        component,
                        f"{self.ref} requires component {component}",
                    )
                )
            else:
                statuses[component] = ComponentStatus.USED
        return statuses, reasons


def checkpoint_solve_rate(solved: int, checkpoints: int) -> Decimal:
    """Solved checkpoints / K (``not_attempted`` counts as unsolved).

    The primary outcome (design memo §2.3), not the score component C; see
    ``problem_correctness``.
    """
    if checkpoints < 1 or not 0 <= solved <= checkpoints:
        raise ValueError(f"need 0 <= solved <= K and K >= 1, got {solved}/{checkpoints}")
    with localcontext() as context:
        context.prec = _CONTEXT_PRECISION
        context.rounding = ROUND_HALF_EVEN
        return Decimal(solved) / Decimal(checkpoints)


def checkpoint_pass_rate(passed: int, graded: int) -> Decimal:
    """One checkpoint's ``C_p,k`` = passed / graded cases (the fork's ``_correctness``)."""
    if graded < 1 or not 0 <= passed <= graded:
        raise ValueError(f"need 0 <= passed <= graded and graded >= 1, got {passed}/{graded}")
    return Decimal(passed) / Decimal(graded)


def problem_correctness(rates: Sequence[ComponentValue]) -> Decimal:
    """C = sum(C_p,k) / K: the mean per-checkpoint case pass rate (the fork's ``C_p``).

    ``rates`` holds one value in [0, 1] per planned checkpoint, with 0 for a
    checkpoint the trial never reached. Precision 50, ROUND_HALF_EVEN, as
    the fork's ``_mean``; the result is not quantised (the policy quantises C).
    """
    if not rates:
        raise ValueError("C needs at least one checkpoint")
    values = [_unit_decimal(CORRECTNESS, rate) for rate in rates]
    with localcontext() as context:
        context.prec = _CONTEXT_PRECISION
        context.rounding = ROUND_HALF_EVEN
        return sum(values, _ZERO) / Decimal(len(values))


SCB_CANONICAL = Metric(
    name="scb-canonical",
    version="1",
    inertia_weights={
        "V": Decimal("0.15"),
        "E": Decimal("0.15"),
        "H": Decimal("0.20"),
        "R": Decimal("0.30"),
        "G": Decimal("0.20"),
    },
    min_checkpoints=2,
    description="SCB-fork canonical score for multi-checkpoint problems.",
)
SCB_CANONICAL_ONESHOT = Metric(
    name="scb-canonical-oneshot",
    version="1",
    inertia_weights={"V": Decimal("0.30"), "E": Decimal("0.30"), "H": Decimal("0.40")},
    min_checkpoints=1,
    max_checkpoints=1,
    not_applicable=frozenset({"R", "G"}),
    description="One-shot variant: R/G not applicable; V:E:H renormalised.",
)
SCB_CANONICAL_V2 = replace(
    SCB_CANONICAL,
    version="2",
    zero_correctness=True,
    description="scb-canonical@1 plus zero_correctness: C = 0 scores S = 0 without quality.",
)
SCB_CANONICAL_ONESHOT_V2 = replace(
    SCB_CANONICAL_ONESHOT,
    version="2",
    zero_correctness=True,
    description="scb-canonical-oneshot@1 plus zero_correctness: C = 0 scores S = 0.",
)


class MetricRegistry:
    """Versioned policies addressed by ``name@version``."""

    def __init__(self, policies: Sequence[Metric]) -> None:
        self._policies: dict[str, Metric] = {}
        for policy in policies:
            if policy.ref in self._policies:
                raise ValueError(f"duplicate metric {policy.ref}")
            self._policies[policy.ref] = policy

    def refs(self) -> tuple[str, ...]:
        return tuple(sorted(self._policies))

    def get(self, ref: str) -> Metric:
        try:
            return self._policies[ref]
        except KeyError as exc:
            raise RegistryReferenceError(
                f"unknown metric {ref!r}; known: {', '.join(self.refs())}"
            ) from exc

    def default_for_checkpoints(self, checkpoints: int) -> Metric:
        """Default headline metric by K: one-shot for K = 1, canonical for K >= 2."""
        if checkpoints < 1:
            raise ValueError(f"checkpoint count must be >= 1, got {checkpoints}")
        return self.get(SCB_CANONICAL_ONESHOT.ref if checkpoints == 1 else SCB_CANONICAL.ref)


DEFAULT_METRIC_REGISTRY = MetricRegistry(
    (SCB_CANONICAL, SCB_CANONICAL_ONESHOT, SCB_CANONICAL_V2, SCB_CANONICAL_ONESHOT_V2)
)


@dataclass(frozen=True)
class MetricResolver:
    """``RegistryResolver`` for kind ``metric`` backed by a MetricRegistry."""

    registry: MetricRegistry = DEFAULT_METRIC_REGISTRY

    @property
    def kind(self) -> RefKind:
        return RefKind.METRIC

    def resolve(self, ref: RegistryRef, *, base_dir: Path) -> ResolvedReference:
        del base_dir
        metric = self.registry.get(str(ref))
        return ResolvedReference(
            ref=ref,
            digest=metric.digest(),
            metadata=metric.definition(),
            binding={"runtimes": list(metric.runtimes)},
        )
