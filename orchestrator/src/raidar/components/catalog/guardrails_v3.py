"""``service`` built-in: python-guardrails@3, the pack whose gate also runs the agent's own tests.

python-guardrails@1 and @2 are frozen (their bytes are in released locks). @3
ships its own payload (``payloads/python_guardrails_3``): @1's gate, rules,
pinned Ruff 0.8.6 and mypy 2.3.1 and protected configs byte for byte, plus the
``tests`` check (``guardrails.tests_check``), with pytest 9.1.1 and coverage
7.16.2 hash-pinned in the same per-platform locks:

- **the agent's own tests**, like a real repository's commit check: ``unittest``
  discovery always, pytest when the project has pytest-style tests, both under
  coverage (Python subprocesses included), in a throwaway copy of the
  workspace (never the hidden verifier's tests, F4). A change to production
  code with no test that ran is refused (``TEST.missing``; a step with no
  Python change is exempt), so are failing tests (``TEST.failed``) and owned
  line coverage below 85% (``TEST.coverage-minimum``: the original rule
  catalogue's threshold, git history ``f61d81b``,
  ``enaible-rules.json`` ``threshold_percent: 85``);
- **installed once per trial at environment start**
  (``raidar.components.runtime.provision_v2``): the payload is uploaded to a root-only
  staging dir, installed root-owned under ``/opt/raidar-tools/python-guardrails``
  (its own virtualenv, ``uv pip install --require-hashes --no-deps
  --only-binary :all:``) and linked at ``/usr/local/bin/guardrail-check``. The
  install then records, root-owned beside the tree
  (``/opt/raidar-tools/python-guardrails.provision``), the gate's ``--version``,
  a gate smoke run as the agent user (a clean file with its test passes, an
  unused import is refused with ``F401``) and a sha256 digest of the whole
  installed tree. Nothing enters the agent image (the pack audit's "no scorer
  tools in the agent image" holds), and nothing is materialised per step;
- **checked cheaply before every step's agent** (as the agent user): the link
  target, the tree's owner and writability, the protected and workspace
  configs' sha256, the install record, and the tree digest recomputed and
  compared with the install's. No tool runs per step, so ``per_step_reset``
  stays within its budget while the protected tree is verified at each step.

``with``: ``workspace_configs`` as in @2 (``true``: ``ruff.toml`` and
``mypy.ini`` copies in the workspace; ``false``: nothing in the workspace, for
a shadow-gated baseline).

The discovery probe requires, at every step: a successful install, the pinned
versions, a passing smoke, the link and a tree digest equal to the install's.
At the first step it also requires the protected configs to carry the
payload's bytes, the tree to be root-owned and not agent-writable, and the
workspace copies to be in place (or absent, with ``workspace_configs: false``).
Tamper detection keeps @1's flags (@2's protection-only set without workspace
copies) and adds ``protected-tree-modified`` when the tree digest moved after
step 1.
"""

from __future__ import annotations

import shlex
from pathlib import Path, PurePosixPath

from raidar.components.catalog._common import (
    BOTH_HARNESSES,
    COMMON_FINGERPRINT,
    require_file_count,
    require_with_keys,
    sha256_bytes,
)
from raidar.components.catalog.guardrails import (
    WORKSPACE_CONFIGS,
    _bypass_flags,
    _config_flags,
    _gate_reports,
    _key,
    _provision_values,
    _suppression_flags,
)
from raidar.components.catalog.guardrails_v2 import ABSENT, workspace_configs
from raidar.components.definition.contribution import Contribution, SupportFile
from raidar.components.definition.evidence import StepEvidence
from raidar.components.definition.manifest import (
    ComponentKind,
    ComponentManifest,
    ComponentUseContext,
    DiscoveryProbe,
    NetworkNeeds,
    ProbeOutcome,
)
from raidar.components.runtime.provision_v2 import AGENT_USER_ENV, PAYLOAD_ENV, TrialProvision

PAYLOAD_PACKAGE_DIR = "raidar/components/payloads/python_guardrails_3"
PAYLOAD_DIR = Path(__file__).resolve().parents[1] / "payloads" / "python_guardrails_3"
GATE_LIBRARY = (
    "__init__.py",
    "diagnostics.py",
    "fanout.py",
    "gate.py",
    "scope.py",
    "source_rules.py",
    "structure.py",
    "tests_check.py",
    "testrun.py",
)
PLATFORMS = ("aarch64", "x86_64")
PAYLOAD_FILES: tuple[str, ...] = (
    "bin/guardrail-check",
    *(f"lib/guardrails/{name}" for name in GATE_LIBRARY),
    "lib/guardrails_site/sitecustomize.py",
    "config/ruff.toml",
    "config/mypy.ini",
    "config/policy.toml",
    *(f"requirements-{platform}-unknown-linux-gnu.lock" for platform in PLATFORMS),
)
ENTRYPOINT = "bin/guardrail-check"
PROTECTED_CONFIGS = ("ruff.toml", "mypy.ini", "policy.toml")
TOOL_HOME = PurePosixPath("/opt/raidar-tools/python-guardrails")
INSTALL_RECORD = PurePosixPath("/opt/raidar-tools/python-guardrails.provision")
LINK_PATH = PurePosixPath("/usr/local/bin/guardrail-check")
PINNED_VERSION = "python-guardrails@3: ruff 0.8.6, mypy 2.3.1, pytest 9.1.1, coverage 7.16.2"
COVERAGE_MINIMUM_PERCENT = 85
SMOKE_MODULE = 'def one() -> int:\n    """Return one."""\n    return 1\n'
SMOKE_TEST = (
    "import unittest\n\nfrom one import one\n\n\n"
    "class OneTest(unittest.TestCase):\n"
    "    def test_one(self) -> None:\n"
    "        self.assertEqual(one(), 1)\n"
)
SMOKE_REFUSED = "import os\n"
SMOKE_REFUSED_CODE = "F401"
WITH_KEYS = ("workspace_configs",)
# The tree digest: every file under the tool home, sorted, content-hashed.
TREE_DIGEST = (
    'cd "$home" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum '
    "| sha256sum | cut -d' ' -f1"
)


def payload_bytes(relative: str) -> bytes:
    return (PAYLOAD_DIR / relative).read_bytes()


def _sha(relative: str) -> str:
    return sha256_bytes(payload_bytes(relative))


# ---- install and check scripts ------------------------------------------------------


def _smoke_lines() -> list[str]:
    """Run as the agent user by the install: a clean workspace passes, an unused import fails."""
    smoke = [
        "set -u",
        'smoke="$(mktemp -d)"; mkdir -p "$smoke/clean" "$smoke/refused"',
        f"printf '%s' {shlex.quote(SMOKE_MODULE)} > \"$smoke/clean/one.py\"",
        f"printf '%s' {shlex.quote(SMOKE_TEST)} > \"$smoke/clean/test_one.py\"",
        f"printf '%s' {shlex.quote(SMOKE_REFUSED)} > \"$smoke/refused/one.py\"",
        f'{LINK_PATH} --root "$smoke/clean" --json-out "$smoke/clean.json" >/dev/null 2>&1',
        'echo "install_smoke_clean_rc=$?"',
        f'{LINK_PATH} --root "$smoke/refused" --json-out "$smoke/refused.json" >/dev/null 2>&1',
        'echo "install_smoke_refused_rc=$?"',
        'echo "install_smoke_refused_codes=$(python3 -c '
        '\'import json,sys; print(",".join(sorted(json.load(open(sys.argv[1]))'
        '["counts"]["block"])))\' "$smoke/refused.json" 2>/dev/null)"',
        'rm -rf "$smoke"',
    ]
    return ['runuser -u "$' + AGENT_USER_ENV + '" -- bash -c ' + shlex.quote("\n".join(smoke))]


def install_script() -> str:
    """Root install at environment start: payload -> TOOL_HOME, pinned venv, record, smoke."""
    home, link = shlex.quote(str(TOOL_HOME)), shlex.quote(str(LINK_PATH))
    record = shlex.quote(str(INSTALL_RECORD))
    return "\n".join(
        [
            "set -euo pipefail",
            'command -v uv >/dev/null || { echo "uv is not in the agent image" >&2; exit 2; }',
            f'[ -n "${{{AGENT_USER_ENV}:-}}" ] || '
            '{ echo "no agent user to smoke as" >&2; exit 2; }',
            f'src="${PAYLOAD_ENV}"; home={home}; record={record}',
            'case "$(uname -m)" in',
            "  x86_64|amd64) platform=x86_64 ;;",
            "  aarch64|arm64) platform=aarch64 ;;",
            '  *) echo "unsupported platform $(uname -m)" >&2; exit 2 ;;',
            "esac",
            'lock="$src/requirements-$platform-unknown-linux-gnu.lock"',
            '[ -f "$lock" ] || { echo "payload missing: $lock" >&2; exit 2; }',
            'rm -rf "$home" "$record" && mkdir -p "$home"',
            'cp -R "$src/bin" "$src/lib" "$src/config" "$home/"',
            # No cache: a root-owned entry in the image's shared UV_CACHE_DIR would
            # break the agent's own uv installs.
            "uv venv --quiet --no-config --no-cache --no-python-downloads --python python3 "
            '"$home/venv"',
            'uv pip install --quiet --no-config --no-cache --python "$home/venv/bin/python" '
            '--require-hashes --no-deps --only-binary :all: -r "$lock"',
            'chown -R root:root "$home"',
            'chmod -R u+rwX,go+rX,go-w "$home"',
            f'ln -sf "$home/{ENTRYPOINT}" {link}',
            'rm -rf "$src"',
            "{",
            f'  echo "install_version=$({link} --version 2>&1 | head -n 1)"',
            *(f"  {line}" for line in _smoke_lines()),
            f'  echo "install_tree_digest=$({TREE_DIGEST})"',
            '} > "$record.tmp"',
            'chmod 644 "$record.tmp" && mv "$record.tmp" "$record"',
            'cat "$record"',
        ]
    )


def check_script() -> str:
    """Per-step check as the agent user: no tool runs, only hashes and the install record."""
    link, home = shlex.quote(str(LINK_PATH)), shlex.quote(str(TOOL_HOME))
    lines = [
        f"link={link}; home={home}",
        'echo "workspace=$(pwd)"',
        'echo "link_target=$(readlink -f "$link" 2>/dev/null || true)"',
        'echo "home_owner=$(stat -c %U "$home" 2>/dev/null || echo missing)"',
        'writable=no; for p in "$home" "$home/config" "$home/bin" "$home/lib/guardrails" '
        '"$home/venv/bin"; do [ -w "$p" ] && writable="$p"; done',
        'echo "home_writable=$writable"',
        f'echo "record_owner=$(stat -c %U {shlex.quote(str(INSTALL_RECORD))} 2>/dev/null || '
        'echo missing)"',
        f"cat {shlex.quote(str(INSTALL_RECORD))} 2>/dev/null",
        f'echo "tree_digest=$({TREE_DIGEST})"',
    ]
    lines += [
        f'echo "protected_{_key(name)}=$(sha256sum "$home/config/{name}" 2>/dev/null '
        '| cut -d" " -f1)"'
        for name in PROTECTED_CONFIGS
    ]
    lines += [
        f'if [ -f {name} ]; then echo "workspace_{_key(name)}=$(sha256sum {name} '
        f'| cut -d" " -f1)"; else echo "workspace_{_key(name)}=missing"; fi'
        for name in WORKSPACE_CONFIGS
    ]
    return "\n".join(lines)


# ---- probe -----------------------------------------------------------------------------


def tool_problems(values: dict[str, str]) -> list[str]:
    """Every step: the pinned gate is installed, smoked, linked and its tree unchanged."""
    problems = []
    if values.get("install_version") != PINNED_VERSION:
        problems.append(
            f"guardrail-check answered {values.get('install_version')!r}, not {PINNED_VERSION!r}"
        )
    if values.get("link_target") != str(TOOL_HOME / ENTRYPOINT):
        problems.append(f"{LINK_PATH} resolves to {values.get('link_target')!r}")
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


class PythonGuardrailsV3:
    """The python-guardrails@3 pack: @1's gate plus the agent's own tests, installed at start."""

    runtimes = ("python",)
    manifest = ComponentManifest(
        name="python-guardrails",
        version="3",
        kind=ComponentKind.SERVICE,
        description=(
            "Install the python-guardrails gate (pinned Ruff, mypy, pytest and coverage, "
            "protected configs, bespoke checks and the agent's own tests with an 85% owned "
            "coverage minimum) as 'guardrail-check' once per trial, and place ruff.toml and "
            "mypy.ini copies in the workspace unless workspace_configs is false"
        ),
        supports=BOTH_HARNESSES,
        network=NetworkNeeds(egress_hosts=("files.pythonhosted.org", "pypi.org")),
        fingerprint=(
            *COMMON_FINGERPRINT,
            "raidar/components/catalog/guardrails.py",
            "raidar/components/catalog/guardrails_v2.py",
            "raidar/components/catalog/guardrails_v3.py",
            "raidar/components/definition/integrity.py",
            "raidar/components/runtime/provision.py",
            "raidar/components/runtime/provision_v2.py",
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
        # @1's config-copy flags: the protected configs are @1's bytes (asserted in tests).
        reports = list(_gate_reports(evidence))
        flags = _config_flags(values, reports, step_index) + flags
        flags += _suppression_flags(reports)
        flags += _bypass_flags(evidence)
        return tuple(flags)


PYTHON_GUARDRAILS_V3 = PythonGuardrailsV3()
