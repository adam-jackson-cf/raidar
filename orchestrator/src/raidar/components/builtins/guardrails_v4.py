"""``service`` built-in: python-guardrails@4, @3 whose gate also sees scripts and ``.py`` symlinks.

python-guardrails@1-@3 are frozen (their bytes are in released locks). @4 ships
its own payload (``payloads/python_guardrails_4``): @3's gate, rules, pins,
tests check and per-trial install byte for byte except the gate's scope
(``guardrails.scope``), which @3 limited to regular ``*.py`` files. A workspace
whose program was an extensionless ``#!...python`` script with a ``.py``
symlink to it (``smoke-03`` of the guardrails experiment, r2) was checked as
empty: 0 files, no finding, no test required. @4 counts

- an extensionless regular file whose first line is a ``#!`` line naming
  ``python`` as a Python file (Ruff is given it explicitly; mypy, the bespoke
  checks and the tests check's change detection see it too);
- a ``*.py`` symlink whose target is a regular file inside the workspace as
  that target, once (a finding reported through the link is reported at the
  target's path). Links that leave the workspace are still skipped.

Install, per-step check, probe, ``with`` keys and integrity flags are @3's
(``raidar.components.builtins.guardrails_v3``), against @4's payload and pinned
version line.
"""

from __future__ import annotations

from pathlib import Path

from raidar.components.builtins._common import (
    BOTH_HARNESSES,
    COMMON_FINGERPRINT,
    require_file_count,
    require_with_keys,
    sha256_bytes,
)
from raidar.components.builtins.guardrails import (
    WORKSPACE_CONFIGS,
    _bypass_flags,
    _config_flags,
    _gate_reports,
    _key,
    _provision_values,
    _suppression_flags,
)
from raidar.components.builtins.guardrails_v2 import ABSENT, workspace_configs
from raidar.components.builtins.guardrails_v3 import (
    ENTRYPOINT,
    GATE_LIBRARY,
    LINK_PATH,
    PLATFORMS,
    PROTECTED_CONFIGS,
    SMOKE_REFUSED_CODE,
    TOOL_HOME,
    WITH_KEYS,
    check_script,
    install_script,
)
from raidar.components.contribution import Contribution, SupportFile
from raidar.components.evidence import StepEvidence
from raidar.components.manifest import (
    ComponentKind,
    ComponentManifest,
    ComponentUseContext,
    DiscoveryProbe,
    NetworkNeeds,
    ProbeOutcome,
)
from raidar.components.provision_v2 import TrialProvision

PAYLOAD_PACKAGE_DIR = "raidar/components/payloads/python_guardrails_4"
PAYLOAD_DIR = Path(__file__).resolve().parents[1] / "payloads" / "python_guardrails_4"
PAYLOAD_FILES: tuple[str, ...] = (
    "bin/guardrail-check",
    *(f"lib/guardrails/{name}" for name in GATE_LIBRARY),
    "lib/guardrails_site/sitecustomize.py",
    "config/ruff.toml",
    "config/mypy.ini",
    "config/policy.toml",
    *(f"requirements-{platform}-unknown-linux-gnu.lock" for platform in PLATFORMS),
)
PINNED_VERSION = "python-guardrails@4: ruff 0.8.6, mypy 2.3.1, pytest 9.1.1, coverage 7.16.2"


def payload_bytes(relative: str) -> bytes:
    return (PAYLOAD_DIR / relative).read_bytes()


def _sha(relative: str) -> str:
    return sha256_bytes(payload_bytes(relative))


# ---- probe -----------------------------------------------------------------------------


def _smoke_problems(values: dict[str, str]) -> list[str]:
    problems = []
    if values.get("install_smoke_clean_rc") != "0":
        problems.append(
            f"smoke: a clean file with its test exits {values.get('install_smoke_clean_rc')!r}"
        )
    refused = (values.get("install_smoke_refused_codes") or "").split(",")
    if values.get("install_smoke_refused_rc") != "1" or SMOKE_REFUSED_CODE not in refused:
        problems.append(
            f"smoke: an unused import exits {values.get('install_smoke_refused_rc')!r} with "
            f"{values.get('install_smoke_refused_codes')!r}, not 1 with {SMOKE_REFUSED_CODE}"
        )
    return problems


def tool_problems(values: dict[str, str]) -> list[str]:
    """Every step: the pinned gate is installed, smoked, linked and its tree unchanged."""
    problems = []
    if values.get("install_version") != PINNED_VERSION:
        problems.append(
            f"guardrail-check answered {values.get('install_version')!r}, not {PINNED_VERSION!r}"
        )
    if values.get("link_target") != str(TOOL_HOME / ENTRYPOINT):
        problems.append(f"{LINK_PATH} resolves to {values.get('link_target')!r}")
    problems += _smoke_problems(values)
    if values.get("record_owner") != "root":
        problems.append(f"the install record is owned by {values.get('record_owner')!r}")
    if not values.get("tree_digest") or values.get("tree_digest") != values.get(
        "install_tree_digest"
    ):
        problems.append(
            f"tool tree digest {values.get('tree_digest')!r} differs from the install's "
            f"{values.get('install_tree_digest')!r}"
        )
    return problems


def protection_problems(values: dict[str, str]) -> list[str]:
    """Step 1, before the agent: the protected bytes and a tree the agent cannot write."""
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


def _workspace_problems(values: dict[str, str], evidence: StepEvidence, copies: bool) -> list[str]:
    recorded = evidence.workspace_files
    if copies:
        return [
            f"workspace {name} at step start: {recorded.get(name)!r}"
            for name in WORKSPACE_CONFIGS
            if recorded.get(name) != _sha(f"config/{name}")
        ]
    return [
        f"workspace {name} present at step start without workspace copies"
        for name in WORKSPACE_CONFIGS
        if values.get(f"workspace_{_key(name)}") not in ABSENT or recorded.get(name) not in ABSENT
    ]


def _state(found: str | None, name: str) -> str:
    """A workspace config copy's state against this pack's protected config."""
    if found in ABSENT:
        return "missing"
    return "identical" if found == _sha(f"config/{name}") else "modified"


def protection_flags(values: dict[str, str], step_index: int) -> list[str]:
    """After step 1: a protected config, the tree's protection or its digest changed."""
    if step_index == 0 or not values:
        return []
    flags = [
        f"protected-config-modified: config/{name}"
        for name in PROTECTED_CONFIGS
        if values.get(f"protected_{_key(name)}") != _sha(f"config/{name}")
    ]
    if values.get("home_writable", "no") != "no":
        flags.append(f"protected-tree-writable: {values.get('home_writable')}")
    if values.get("tree_digest") != values.get("install_tree_digest"):
        flags.append(
            f"protected-tree-modified: digest {values.get('tree_digest')} != install "
            f"{values.get('install_tree_digest')}"
        )
    return flags


class PythonGuardrailsV4:
    """The python-guardrails@4 pack: @3 with scripts and ``.py`` symlinks in the gate's scope."""

    runtimes = ("python",)
    manifest = ComponentManifest(
        name="python-guardrails",
        version="4",
        kind=ComponentKind.SERVICE,
        description=(
            "Install the python-guardrails gate (pinned Ruff, mypy, pytest and coverage, "
            "protected configs, bespoke checks and the agent's own tests with an 85% owned "
            "coverage minimum; Python files include extensionless python scripts and the "
            "in-workspace targets of .py symlinks) as 'guardrail-check' once per trial, and "
            "place ruff.toml and mypy.ini copies in the workspace unless workspace_configs "
            "is false"
        ),
        supports=BOTH_HARNESSES,
        network=NetworkNeeds(egress_hosts=("files.pythonhosted.org", "pypi.org")),
        fingerprint=(
            *COMMON_FINGERPRINT,
            "raidar/components/builtins/guardrails.py",
            "raidar/components/builtins/guardrails_v2.py",
            "raidar/components/builtins/guardrails_v3.py",
            "raidar/components/builtins/guardrails_v4.py",
            "raidar/components/integrity.py",
            "raidar/components/provision.py",
            "raidar/components/provision_v2.py",
            *(f"{PAYLOAD_PACKAGE_DIR}/{relative}" for relative in PAYLOAD_FILES),
        ),
        discovery_probe=DiscoveryProbe(
            id="python-guardrails-gate-works",
            description=(
                "At every step the gate installed at environment start answers the pinned "
                "versions, its smoke passed (a clean file with its test passes, an unused "
                "import is refused) and the installed tree's digest equals the install's; at "
                "step 1 the protected configs carry the payload's bytes, the tree is not "
                "agent-writable and the workspace copies are in place (or, with "
                "workspace_configs false, absent)"
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
        self._validate(use)
        if not workspace_configs(use):
            return Contribution()
        return Contribution(
            overlay={name: payload_bytes(f"config/{name}") for name in WORKSPACE_CONFIGS}
        )

    def provision(self, use: ComponentUseContext) -> TrialProvision:
        self._validate(use)
        payload = {
            relative: SupportFile(payload_bytes(relative), executable=relative == ENTRYPOINT)
            for relative in PAYLOAD_FILES
        }
        return TrialProvision(
            use.slug, self.manifest.ref, payload, install_script(), check_script()
        )

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        self._validate(use)
        values, failure = _provision_values(evidence, use.slug)
        if failure is not None:
            return ProbeOutcome(False, failure)
        problems = tool_problems(values)
        if step_index == 0:
            problems += protection_problems(values)
            problems += _workspace_problems(values, evidence, workspace_configs(use))
        if problems:
            return ProbeOutcome(False, "; ".join(problems))
        configs = ", ".join(
            f"{name} {_state(values.get(f'workspace_{_key(name)}'), name)}"
            for name in WORKSPACE_CONFIGS
        )
        detail = (
            f"{values.get('install_version')} at {values.get('link_target')}; smoke clean "
            f"{values.get('install_smoke_clean_rc')}, refused "
            f"{values.get('install_smoke_refused_rc')} "
            f"({values.get('install_smoke_refused_codes')}); tree "
            f"{str(values.get('tree_digest'))[:12]} unchanged; workspace {configs}"
        )
        return ProbeOutcome(True, detail, (str(values.get("install_version")), configs))

    def step_flags(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> tuple[str, ...]:
        """Tampering and bypass evidence for one step; never fails it."""
        self._validate(use)
        values, _ = _provision_values(evidence, use.slug)
        flags = protection_flags(values, step_index)
        if not workspace_configs(use):
            return tuple(flags)
        # @1's config-copy flags: the protected ruff.toml and mypy.ini are @1's bytes.
        reports = list(_gate_reports(evidence))
        flags = _config_flags(values, reports, step_index) + flags
        flags += _suppression_flags(reports)
        flags += _bypass_flags(evidence)
        return tuple(flags)


PYTHON_GUARDRAILS_V4 = PythonGuardrailsV4()
