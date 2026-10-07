"""``service`` built-in: python-guardrails@8, @7 that exposes its safe auto-fixers as a command.

python-guardrails@1-@7 are frozen (their bytes are in released locks). @8 ships
its own payload (``payloads/python_guardrails_8``): @7's gate, rules, pins,
scope, tests check, agent tool links and ``.pth`` exposure byte for byte except
the version strings, plus one library module, ``lib/guardrails/autofix.py``,
and the gate flag that runs it, ``guardrail-check --autofix`` (``AUTOFIX_COMMAND``).

``--autofix`` is the fix pass a repository's pre-commit autofix hook runs: the
pack's safe auto-fixers only (``ruff format``, then ``ruff check --fix
--no-unsafe-fixes``, then ``ruff format``; the protected config; the scope's
Python files only; never mypy, never the tests), run in a throwaway copy and
written back only when they all succeeded. Its report
(``python-guardrails.autofix-report/1``) names the files and lines it changed
and the codes it fixed (all and block-tier). A commit gate runs it before the
real commit (``guardrail-gate@4`` ``autofix_command``); a contributor may run it.

The config files (``ruff.toml``, ``mypy.ini``, ``policy.toml``) are @7's bytes
unchanged (``policy.toml``'s header still names @7's tiers, which @8 keeps), so
@8's protection probe and config-copy flags are @7's (they compare against those
same bytes). The install adds one smoke, as the agent user: ``--autofix`` on a
file with an unsorted import, a layout error and an unused local fixes ``I001``
and ``FORMAT``, keeps ``F841`` (Ruff marks that fix unsafe) and exits 0; the
probe requires it at every step beside @7's requirements. The component is the
same in both conditions, so the arms still differ only in ``condition``.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from raidar.components.builtins._common import COMMON_FINGERPRINT
from raidar.components.builtins.guardrails import WORKSPACE_CONFIGS, _key, _provision_values
from raidar.components.builtins.guardrails_v2 import workspace_configs
from raidar.components.builtins.guardrails_v3 import (
    ENTRYPOINT,
    GATE_LIBRARY,
    INSTALL_RECORD,
    LINK_PATH,
    PLATFORMS,
    TOOL_HOME,
    TREE_DIGEST,
    _smoke_lines,
)
from raidar.components.builtins.guardrails_v5 import (
    EXECUTABLES,
    RUN_MODULE,
    _agent_tool_lines,
    _link_lines,
    _smoke_problems,
    agent_tool_problems,
)
from raidar.components.builtins.guardrails_v7 import (
    AGENT_SITE,
    EXPOSE_TEST_TOOLS,
    PTH_NAME,
    PURELIB,
    SITE_DIGEST,
    PythonGuardrailsV7,
    _python_module_lines,
    _state,
    _workspace_problems,
    check_script,
    protection_problems,
    python_module_problems,
)
from raidar.components.contribution import Contribution, SupportFile
from raidar.components.evidence import StepEvidence
from raidar.components.manifest import (
    ComponentManifest,
    ComponentUseContext,
    DiscoveryProbe,
    ProbeOutcome,
)
from raidar.components.provision_v2 import AGENT_USER_ENV, PAYLOAD_ENV, TrialProvision

PAYLOAD_PACKAGE_DIR = "raidar/components/payloads/python_guardrails_8"
PAYLOAD_DIR = Path(__file__).resolve().parents[1] / "payloads" / "python_guardrails_8"
AUTOFIX_MODULE = "lib/guardrails/autofix.py"
PAYLOAD_FILES: tuple[str, ...] = (
    "bin/guardrail-check",
    "bin/pytest",
    "bin/coverage",
    RUN_MODULE,
    EXPOSE_TEST_TOOLS,
    *(f"lib/guardrails/{name}" for name in GATE_LIBRARY),
    AUTOFIX_MODULE,
    "lib/guardrails_site/sitecustomize.py",
    "config/ruff.toml",
    "config/mypy.ini",
    "config/policy.toml",
    *(f"requirements-{platform}-unknown-linux-gnu.lock" for platform in PLATFORMS),
)
CONFIG_FILES = ("config/ruff.toml", "config/mypy.ini", "config/policy.toml")
"""@7's bytes unchanged (``test_component_python_guardrails_v8`` holds them equal)."""
PINNED_VERSION = "python-guardrails@8: ruff 0.8.6, mypy 2.3.1, pytest 9.1.1, coverage 7.16.2"
AUTOFIX_COMMAND = "guardrail-check --autofix"
"""The pack's safe-fix command (the gate's ``autofix_command``)."""
SMOKE_AUTOFIX = (
    "import sys\nimport os\n\n\n"
    "def separator() -> str :\n"
    '    """Return the path separator and the platform."""\n'
    "    unused = 1\n"
    "    return os.sep + sys.platform\n"
)
AUTOFIX_FIXED = ("FORMAT", "I001")
"""Codes the autofix smoke must fix."""
AUTOFIX_KEPT = "F841"
"""The smoke's unused local: Ruff's fix for it is unsafe, so it must remain."""


def payload_bytes(relative: str) -> bytes:
    return (PAYLOAD_DIR / relative).read_bytes()


# ---- install and check scripts ------------------------------------------------------


def _autofix_smoke_lines() -> list[str]:
    """Run as the agent user by the install: ``--autofix`` fixes the safe codes only."""
    codes = (
        "python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); "
        'print(",".join(sorted(r.get(sys.argv[2]) or {})))\' "$smoke/autofix.json"'
    )
    smoke = [
        "set -u",
        'smoke="$(mktemp -d)"; mkdir -p "$smoke/autofix"',
        f"printf '%s' {shlex.quote(SMOKE_AUTOFIX)} > \"$smoke/autofix/one.py\"",
        f'{LINK_PATH} --autofix --root "$smoke/autofix" --json-out "$smoke/autofix.json" '
        ">/dev/null 2>&1",
        'echo "install_smoke_autofix_rc=$?"',
        f'echo "install_smoke_autofix_fixed=$({codes} codes_fixed 2>/dev/null)"',
        f'echo "install_smoke_autofix_after=$({codes} codes_after 2>/dev/null)"',
        'rm -rf "$smoke"',
    ]
    return ['runuser -u "$' + AGENT_USER_ENV + '" -- bash -c ' + shlex.quote("\n".join(smoke))]


def install_script() -> str:
    """@7's root install (payload 8), plus the autofix smoke."""
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
            f'purelib="$({PURELIB})"; pth="$purelib/{PTH_NAME}"',
            'rm -rf "$home" "$record" "$pth" && mkdir -p "$home"',
            'cp -R "$src/bin" "$src/lib" "$src/config" "$home/"',
            # No cache: a root-owned entry in the image's shared UV_CACHE_DIR would
            # break the agent's own uv installs.
            "uv venv --quiet --no-config --no-cache --no-python-downloads --python python3 "
            '"$home/venv"',
            'uv pip install --quiet --no-config --no-cache --python "$home/venv/bin/python" '
            '--require-hashes --no-deps --only-binary :all: -r "$lock"',
            f'"$home/venv/bin/python" "$home/{EXPOSE_TEST_TOOLS}" {shlex.quote(str(AGENT_SITE))}',
            'chown -R root:root "$home"',
            'chmod -R u+rwX,go+rX,go-w "$home"',
            f"printf '%s\\n' {shlex.quote(str(AGENT_SITE))} > \"$pth\"",
            'chown root:root "$pth" && chmod 644 "$pth"',
            f'ln -sf "$home/{ENTRYPOINT}" {link}',
            *_link_lines(),
            'rm -rf "$src"',
            "{",
            f'  echo "install_version=$({link} --version 2>&1 | head -n 1)"',
            *(f"  {line}" for line in _smoke_lines()),
            *(f"  {line}" for line in _autofix_smoke_lines()),
            *(f"  {line}" for line in _agent_tool_lines()),
            *(f"  {line}" for line in _python_module_lines()),
            f'  echo "install_agent_site_digest=$({SITE_DIGEST})"',
            f'  echo "install_tree_digest=$({TREE_DIGEST})"',
            '} > "$record.tmp"',
            'chmod 644 "$record.tmp" && mv "$record.tmp" "$record"',
            'cat "$record"',
        ]
    )


# ---- probe -----------------------------------------------------------------------------


def autofix_problems(values: dict[str, str]) -> list[str]:
    """Every step: the install's autofix smoke fixed the safe codes and kept the unsafe one."""
    fixed = set((values.get("install_smoke_autofix_fixed") or "").split(","))
    after = set((values.get("install_smoke_autofix_after") or "").split(","))
    problems = []
    if values.get("install_smoke_autofix_rc") != "0":
        problems.append(f"autofix smoke exits {values.get('install_smoke_autofix_rc')!r}")
    missing = [code for code in AUTOFIX_FIXED if code not in fixed]
    if missing:
        problems.append(f"autofix smoke did not fix {', '.join(missing)} (fixed {sorted(fixed)})")
    if AUTOFIX_KEPT not in after or AUTOFIX_KEPT in fixed:
        problems.append(
            f"autofix smoke applied the unsafe {AUTOFIX_KEPT} fix (left {sorted(after)})"
        )
    return problems


def tool_problems(values: dict[str, str]) -> list[str]:
    """Every step: the pinned gate is installed, smoked (autofix too), linked and unchanged."""
    problems = []
    if values.get("install_version") != PINNED_VERSION:
        problems.append(
            f"guardrail-check answered {values.get('install_version')!r}, not {PINNED_VERSION!r}"
        )
    if values.get("link_target") != str(TOOL_HOME / ENTRYPOINT):
        problems.append(f"{LINK_PATH} resolves to {values.get('link_target')!r}")
    problems += _smoke_problems(values)
    problems += autofix_problems(values)
    if values.get("record_owner") != "root":
        problems.append(f"the install record is owned by {values.get('record_owner')!r}")
    if not values.get("tree_digest") or values.get("tree_digest") != values.get(
        "install_tree_digest"
    ):
        problems.append(
            f"tool tree digest {values.get('tree_digest')!r} differs from the install's "
            f"{values.get('install_tree_digest')!r}"
        )
    return problems + agent_tool_problems(values) + python_module_problems(values)


class PythonGuardrailsV8(PythonGuardrailsV7):
    """The python-guardrails@8 pack: @7 plus ``guardrail-check --autofix``."""

    manifest = ComponentManifest(
        name="python-guardrails",
        version="8",
        kind=PythonGuardrailsV7.manifest.kind,
        description=(
            f"{PythonGuardrailsV7.manifest.description}; and expose the pack's safe "
            f"auto-fixers as '{AUTOFIX_COMMAND}' (ruff format and ruff's safe fixes only, "
            "with the protected config, on the scope's Python files, applied atomically and "
            "reported: files, lines and codes fixed) for a commit gate's autofix step"
        ),
        supports=PythonGuardrailsV7.manifest.supports,
        network=PythonGuardrailsV7.manifest.network,
        fingerprint=(
            *COMMON_FINGERPRINT,
            "raidar/components/builtins/guardrails.py",
            "raidar/components/builtins/guardrails_v2.py",
            "raidar/components/builtins/guardrails_v3.py",
            "raidar/components/builtins/guardrails_v5.py",
            "raidar/components/builtins/guardrails_v6.py",
            "raidar/components/builtins/guardrails_v7.py",
            "raidar/components/builtins/guardrails_v8.py",
            "raidar/components/integrity.py",
            "raidar/components/provision.py",
            "raidar/components/provision_v2.py",
            *(f"{PAYLOAD_PACKAGE_DIR}/{relative}" for relative in PAYLOAD_FILES),
        ),
        discovery_probe=DiscoveryProbe(
            id="python-guardrails-gate-works",
            description=(
                f"{PythonGuardrailsV7.manifest.discovery_probe.description}; and the "
                "install's autofix smoke fixed I001 and FORMAT, kept the unsafe F841 and "
                "exited 0"
            ),
            evidence=PythonGuardrailsV7.manifest.discovery_probe.evidence,
        ),
    )

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
            relative: SupportFile(payload_bytes(relative), executable=relative in EXECUTABLES)
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
            f"({values.get('install_smoke_refused_codes')}); autofix fixed "
            f"{values.get('install_smoke_autofix_fixed')}, kept "
            f"{values.get('install_smoke_autofix_after')}; agent tools "
            f"{values.get('install_agent_tool_versions')}; python -m "
            f"{values.get('install_agent_python_module_versions')}; tree "
            f"{str(values.get('tree_digest'))[:12]} unchanged; workspace {configs}"
        )
        return ProbeOutcome(True, detail, (str(values.get("install_version")), configs))


PYTHON_GUARDRAILS_V8 = PythonGuardrailsV8()
