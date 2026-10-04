"""``service`` built-in: python-guardrails@2, the @1 pack that can stay out of the workspace.

python-guardrails@1 (``raidar.components.builtins.guardrails``) is frozen: its
bytes are in released locks. @2 installs the same payload (the same
``python_guardrails_1`` files, byte for byte: the same gate, pinned Ruff 0.8.6
and mypy 2.3.1, protected configs and bespoke checks) the same way, and adds one
``with`` key:

- ``workspace_configs`` (default ``true``): ``true`` is exactly @1 (byte-identical
  ``ruff.toml`` and ``mypy.ini`` copies in the workspace). ``false`` puts nothing
  in the workspace: the gate still runs on its protected configs under
  ``/opt/raidar-tools/python-guardrails``, so a shadow gate
  (``guardrail-gate@2`` ``mode: shadow``) can check a baseline arm whose agent
  must not see the gate or its configs. The discovery probe then requires, at
  step 1, that neither copy is in the workspace, and the integrity flags keep
  only the protection flags (a config-copy or suppression flag would describe
  a gate the agent never saw; a bypass pattern has no hook to get round).

Rule provenance: the rule set was derived on 2026-10-04 from the user's
``setup-repository-guardrails`` skill (the Enaible rule contract adapted to an
agent workspace), used once to set the pack's initial verification; the skill
is not a dependency. The pack's own verification is its rule tests: one passing
and one violating canary per implemented rule (73),
``orchestrator/tests/fixtures/python_guardrails/``, which ``make
guardrails-verify`` runs against the installed pack in the pack image, with a
mutant-pack negative control.
"""

from __future__ import annotations

from raidar.components.builtins._common import (
    COMMON_FINGERPRINT,
    require_file_count,
    require_with_keys,
)
from raidar.components.builtins.guardrails import (
    PAYLOAD_FILES,
    PAYLOAD_PACKAGE_DIR,
    PROTECTED_CONFIGS,
    WORKSPACE_CONFIGS,
    PythonGuardrails,
    _config_state,
    _key,
    _protection_flags,
    _provision_values,
    _sha,
    _tool_problems,
)
from raidar.components.contribution import Contribution
from raidar.components.evidence import StepEvidence
from raidar.components.manifest import (
    ComponentConfigError,
    ComponentKind,
    ComponentManifest,
    ComponentUseContext,
    DiscoveryProbe,
    ProbeOutcome,
)

WITH_KEYS = ("workspace_configs",)
ABSENT = (None, "", "missing")


def workspace_configs(use: ComponentUseContext) -> bool:
    value = use.with_.get("workspace_configs", True)
    if not isinstance(value, bool):
        raise ComponentConfigError("'with.workspace_configs' must be true or false")
    return value


class PythonGuardrailsV2(PythonGuardrails):
    """python-guardrails@1's pack, with the workspace config copies optional."""

    manifest = ComponentManifest(
        name="python-guardrails",
        version="2",
        kind=ComponentKind.SERVICE,
        description=(
            "Install the python-guardrails gate (pinned Ruff and mypy, protected configs, "
            "bespoke checks) as 'guardrail-check', and place ruff.toml and mypy.ini copies "
            "in the workspace unless workspace_configs is false"
        ),
        supports=PythonGuardrails.manifest.supports,
        network=PythonGuardrails.manifest.network,
        fingerprint=(
            *COMMON_FINGERPRINT,
            "raidar/components/builtins/guardrails.py",
            "raidar/components/builtins/guardrails_v2.py",
            "raidar/components/integrity.py",
            "raidar/components/provision.py",
            *(f"{PAYLOAD_PACKAGE_DIR}/{relative}" for relative in PAYLOAD_FILES),
        ),
        discovery_probe=DiscoveryProbe(
            id="python-guardrails-gate-works",
            description=(
                "Before every step's agent, guardrail-check answers the pinned versions at "
                "its installed link and a smoke passes a clean file and refuses an unused "
                "import; at step 1 the protected configs carry the payload's bytes, the "
                "tool tree is not agent-writable and the workspace copies are in place "
                "(or, with workspace_configs false, absent)"
            ),
            evidence=(
                "raidar-components/<slug>.provision.jsonl",
                "raidar-components.json workspace_files",
            ),
        ),
    )

    def _validate(self, use: ComponentUseContext) -> None:
        require_with_keys(use, allowed=WITH_KEYS)
        require_file_count(use, minimum=0, maximum=0)
        workspace_configs(use)

    def contribute(self, use: ComponentUseContext) -> Contribution:
        contribution = super().contribute(use)
        if workspace_configs(use):
            return contribution
        return Contribution(support_files=contribution.support_files)

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        if workspace_configs(use):
            return super().probe(use, evidence, step_index)
        self._validate(use)
        values, failure = _provision_values(evidence, use.slug)
        if failure is not None:
            return ProbeOutcome(False, failure)
        problems = _tool_problems(values)
        if step_index == 0:
            problems += _first_step_problems_without_copies(values, evidence)
        if problems:
            return ProbeOutcome(False, "; ".join(problems))
        configs = ", ".join(
            f"{name} {_config_state(values.get(f'workspace_{_key(name)}'), name)}"
            for name in WORKSPACE_CONFIGS
        )
        detail = (
            f"{values.get('version')} at {values.get('link_target')}; smoke clean "
            f"{values.get('smoke_clean_rc')}, refused {values.get('smoke_refused_rc')} "
            f"({values.get('smoke_refused_codes')}); no workspace copies; workspace {configs}"
        )
        return ProbeOutcome(True, detail, (str(values.get("version")), configs))

    def step_flags(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> tuple[str, ...]:
        if workspace_configs(use):
            return super().step_flags(use, evidence, step_index)
        self._validate(use)
        values, _ = _provision_values(evidence, use.slug)
        return tuple(_protection_flags(values, step_index))


def _first_step_problems_without_copies(
    values: dict[str, str], evidence: StepEvidence
) -> list[str]:
    """Step 1, before the agent: @1's protection checks, and no config copy in the workspace."""
    problems = _first_step_protection(values)
    recorded = evidence.workspace_files
    problems += [
        f"workspace {name} present at step start without workspace copies"
        for name in WORKSPACE_CONFIGS
        if values.get(f"workspace_{_key(name)}") not in ABSENT or recorded.get(name) not in ABSENT
    ]
    return problems


def _first_step_protection(values: dict[str, str]) -> list[str]:
    """@1's step-1 protection checks: the protected bytes and a tool tree the agent cannot write."""
    problems = [
        f"protected config/{name} differs from the payload"
        for name in PROTECTED_CONFIGS
        if values.get(f"protected_{_key(name)}") != _sha(f"config/{name}")
    ]
    if values.get("home_owner") != "root" or values.get("home_writable") != "no":
        problems.append(
            f"tool tree owner {values.get('home_owner')!r}, agent-writable "
            f"{values.get('home_writable')!r}: not protected"
        )
    return problems


PYTHON_GUARDRAILS_V2 = PythonGuardrailsV2()
