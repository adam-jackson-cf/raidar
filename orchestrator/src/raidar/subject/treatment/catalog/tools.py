"""``service`` built-in: lint-tool@2, the pinned Ruff runnable the ways agents call it.

lint-config@1 only places a config file; the agent image has no linter (Ruff
is a scorer tool, and the pack audit forbids scorer tools in the agent image).
lint-tool makes a pinned Ruff executable available to the agent, per trial:

- install (``raidar.subject.treatment.runtime.provision``): once per trial, as root, before the
  first step's agent. ``uv pip install --require-hashes`` puts the wheel pinned
  below into ``/opt/raidar-tools/<slug>/ruff-<version>`` and links its binary to
  ``/usr/local/bin/ruff``. Both paths are outside the agent image, the
  workspace and every path the per-step reset wipes, and root-owned, so the
  agent can use the tool but not remove it. This is the agent's copy: the
  ``code-quality`` scorer keeps its own Ruff (tool lock ``scb-python-quality``)
  and its own isolated rule set (design memo §10.5 invariant 3). A ``.pth`` file
  in the site-packages of each image interpreter (``python3``, ``python``)
  points at the install directory, so ``python -m ruff`` imports the pinned
  wheel's ``ruff`` package, whose ``__main__`` execs the pinned binary. The
  ``.pth`` is root-owned and outside the agent image and every path the per-step
  reset wipes. ``uv run ruff`` needs nothing extra: uv puts the project
  environment's ``bin`` first on ``PATH`` and falls through to
  ``/usr/local/bin/ruff``. A virtual environment the agent creates does not see
  the system site-packages, so ``python -m ruff`` inside it is not supported;
- check: before every step's agent, as the agent user, in the workspace: the
  pinned binary's version, where ``ruff`` resolves on ``PATH``, the config file
  Ruff resolves for the workspace (``ruff check -v --show-files``, which lists
  files without linting them and writes no cache), and each supported
  invocation's ``--version``: ``ruff`` on ``PATH``, ``python -m ruff``,
  ``python3 -m ruff`` and ``uv run ruff``. ``uv run`` is called with
  ``--no-sync`` and a throwaway ``UV_PROJECT_ENVIRONMENT``, so it discovers the
  workspace's project (if any) but writes no ``.venv`` or ``uv.lock`` into it.
  The check also records whether the workspace is a uv project and whether a
  project ``.venv`` has its own ``ruff`` (which the agent's real ``uv run ruff``
  would prefer).

The discovery probe requires, at every step, a successful install and the pinned
version at the provisioned path. At the first step, before the agent has acted,
it also requires that ``ruff`` resolves on ``PATH`` to the provisioned link, that
every supported invocation answers the pinned version, and (with ``with.config``,
for example the ``ruff.toml`` lint-config@1 places) that Ruff resolves exactly
that file at the workspace root. Later steps report which invocations ran the
pinned Ruff without enforcing it: the agent may install its own Ruff or edit its
config, which is behaviour, not infrastructure.

It installs from PyPI (egress declared in the manifest) and is Python-only.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from raidar.subject.treatment.catalog._common import (
    BOTH_HARNESSES,
    COMMON_FINGERPRINT,
    require_file_count,
    require_with_keys,
)
from raidar.subject.treatment.definition.contribution import Contribution
from raidar.subject.treatment.definition.evidence import StepEvidence
from raidar.subject.treatment.definition.manifest import (
    ComponentConfigError,
    ComponentKind,
    ComponentManifest,
    ComponentUseContext,
    DiscoveryProbe,
    NetworkNeeds,
    ProbeOutcome,
)
from raidar.subject.treatment.runtime.provision import PROVISION_CHANNEL, Provision

TOOLS_ROOT = PurePosixPath("/opt/raidar-tools")
LINK_PATH = PurePosixPath("/usr/local/bin/ruff")
DEFAULT_RUFF_VERSION = "0.8.6"
# Every published wheel of each supported release (``uv pip compile
# --generate-hashes``), so the install is hash-pinned on any container platform.
RUFF_WHEEL_SHA256: dict[str, tuple[str, ...]] = {
    "0.8.6": (
        "0509e8da430228236a18a677fcdb0c1f102dd26d5520f71f79b094963322ed25",
        "0c000a471d519b3e6cfc9c6680025d923b4ca140ce3e4612d1a2ef58e11f11fe",
        "248b1fb3f739d01d528cc50b35ee9c4812aa58cc5935998e776bf8ed5b251e75",
        "45a56f61b24682f6f6709636949ae8cc82ae229d8d773b4c76c09ec83964a95a",
        "496dd38a53aa173481a7d8866bcd6451bd934d06976a2505028a50583e001b76",
        "52d587092ab8df308635762386f45f4638badb0866355b2b86760f6d3c076188",
        "54799ca3d67ae5e0b7a7ac234baa657a9c1784b48ec954a094da7c206e0365b1",
        "61323159cf21bc3897674e5adb27cd9e7700bab6b84de40d7be28c3d46dc67cf",
        "7ae4478b1471fc0c44ed52a6fb787e641a2ac58b1c1f91763bafbc2faddc5117",
        "7d7fc2377a04b6e04ffe588caad613d0c460eb2ecba4c0ccbbfe2bc973cbc162",
        "91a7ddb221779871cf226100e677b5ea38c2d54e9e2c8ed847450ebbdf99b32d",
        "9257aa841e9e8d9b727423086f0fa9a86b6b420fbf4bf9e1465d1250ce8e4d8d",
        "bc3c083c50390cf69e7e1b5a5a7303898966be973664ec0c4a4acea82c1d4315",
        "dcad24b81b62650b0eb8814f576fc65cfee8674772a6e24c9b747911801eeaa5",
        "defed167955d42c68b407e8f2e6f56ba52520e790aba4ca707a9c88619e580e3",
        "e169ea1b9eae61c99b257dc83b9ee6c76f89042752cb2d83486a7d6e48e8f764",
        "e88b8f6d901477c41559ba540beeb5a671e14cd29ebd5683903572f4b40a9807",
        "f1d70bef3d16fdc897ee290d7d20da3cbe4e26349f62e8a0274e7a3f4ce7a905",
    ),
}
_RESOLVE_MARKER = "Using configuration file"
# (check key, invocation as the agent types it), in report order.
INVOCATIONS: tuple[tuple[str, str], ...] = (
    ("ruff", "ruff"),
    ("python_m", "python -m ruff"),
    ("python3_m", "python3 -m ruff"),
    ("uv_run", "uv run ruff"),
)
INTERPRETERS = ("python3", "python")
_INVOKE_FUNCTION = (
    "invoke() {\n"
    '  key="$1"; shift\n'
    '  err="$(mktemp)"\n'
    '  out="$("$@" --version 2>"$err")"; rc=$?\n'
    '  echo "${key}_rc=$rc"\n'
    '  echo "${key}=$(printf \'%s\\n\' "$out" | head -n 1)"\n'
    '  if [ "$rc" -ne 0 ]; then echo "${key}_error=$(tail -c 300 "$err" | tr \'\\n\' \' \')"; fi\n'
    '  rm -f "$err"\n'
    "}"
)


@dataclass(frozen=True)
class InvocationResult:
    """One invocation's check result: what it answered and whether that is the pin."""

    label: str
    answer: str
    exit_code: str
    error: str
    pinned: bool

    def describe(self) -> str:
        reason = self.error or self.answer or "no output"
        return f"{self.label} -> {reason!r} (exit {self.exit_code or '?'})"


def invocation_results(values: dict[str, str], version: str) -> tuple[InvocationResult, ...]:
    """Every supported invocation's result from one check record's values."""
    expected = f"ruff {version}"
    results = []
    for key, label in INVOCATIONS:
        answer, exit_code = values.get(key, ""), values.get(f"{key}_rc", "")
        results.append(
            InvocationResult(
                label=label,
                answer=answer,
                exit_code=exit_code,
                error=values.get(f"{key}_error", "").strip(),
                pinned=exit_code == "0" and answer == expected,
            )
        )
    return tuple(results)


def requirements(version: str) -> str:
    hashes = " ".join(f"--hash=sha256:{digest}" for digest in RUFF_WHEEL_SHA256[version])
    return f"ruff=={version} {hashes}\n"


class LintTool:
    """A hash-pinned Ruff on the agent's PATH, installed per trial."""

    runtimes = ("python",)
    manifest = ComponentManifest(
        name="lint-tool",
        version="2",
        kind=ComponentKind.SERVICE,
        description=(
            "Install a hash-pinned Ruff in the trial container (outside the agent image), "
            "runnable as 'ruff', 'python -m ruff' and 'uv run ruff'"
        ),
        supports=BOTH_HARNESSES,
        network=NetworkNeeds(egress_hosts=("files.pythonhosted.org", "pypi.org")),
        fingerprint=(
            *COMMON_FINGERPRINT,
            "orchestrator/src/raidar/subject/treatment/catalog/tools.py",
            "orchestrator/src/raidar/subject/treatment/runtime/provision.py",
        ),
        discovery_probe=DiscoveryProbe(
            id="lint-tool-invocable",
            description=(
                "The pinned Ruff answers its version at the provisioned path before every "
                "step's agent; at step 1, 'ruff', 'python -m ruff', 'python3 -m ruff' and "
                "'uv run ruff' all run it and, with 'config', it resolves that workspace "
                "config; later steps record which invocations still run it"
            ),
            evidence=("raidar-components/<slug>.provision.jsonl",),
        ),
    )

    def options(self, use: ComponentUseContext) -> tuple[str, str | None]:
        """``(version, config)`` from ``with``; config is a workspace-root file name."""
        require_with_keys(use, allowed=("version", "config"))
        require_file_count(use, minimum=0, maximum=0)
        version = use.with_.get("version", DEFAULT_RUFF_VERSION)
        if not isinstance(version, str) or version not in RUFF_WHEEL_SHA256:
            raise ComponentConfigError(
                f"'with.version' must be one of {sorted(RUFF_WHEEL_SHA256)}, got {version!r}"
            )
        config = use.with_.get("config")
        if config is not None and (
            not isinstance(config, str) or not config or "/" in config or config in (".", "..")
        ):
            raise ComponentConfigError("'with.config' must be a file name at the workspace root")
        return version, config

    def install_dir(self, use: ComponentUseContext) -> PurePosixPath:
        version, _ = self.options(use)
        return TOOLS_ROOT / use.slug / f"ruff-{version}"

    def contribute(self, use: ComponentUseContext) -> Contribution:
        self.options(use)
        return Contribution()

    def pth_name(self, use: ComponentUseContext) -> str:
        return f"raidar-{use.slug}-ruff.pth"

    def provision(self, use: ComponentUseContext) -> Provision:
        version, _ = self.options(use)
        target = shlex.quote(str(self.install_dir(use)))
        link = shlex.quote(str(LINK_PATH))
        pth = shlex.quote(self.pth_name(use))
        install = "\n".join(
            [
                "set -euo pipefail",
                'command -v uv >/dev/null || { echo "uv is not in the agent image" >&2; exit 2; }',
                'req="$(mktemp)"',
                "cat > \"$req\" <<'RAIDAR_REQUIREMENTS'",
                f"{requirements(version)}RAIDAR_REQUIREMENTS",
                f"rm -rf {target}",
                # No cache: a root-owned entry in the image's shared UV_CACHE_DIR
                # would break the agent's own uv installs.
                "uv pip install --quiet --no-config --no-cache --python python3 "
                "--require-hashes --no-deps --only-binary :all: "
                f'--target {target} -r "$req"',
                'rm -f "$req"',
                f"ln -sf {target}/bin/ruff {link}",
                f"chmod -R a+rX {shlex.quote(str(TOOLS_ROOT))}",
                f"{link} --version",
                f"for py in {' '.join(INTERPRETERS)}; do",
                '  bin="$(command -v "$py" || true)"',
                '  [ -n "$bin" ] || continue',
                '  site="$("$bin" -c \'import sysconfig; print(sysconfig.get_path("purelib"))\')"',
                f"  printf '%s\\n' {target} > \"$site/\"{pth}",
                f'  chmod a+r "$site/"{pth}',
                '  "$bin" -m ruff --version',
                "done",
            ]
        )
        check = "\n".join(
            [
                'echo "workspace=$(pwd)"',
                'echo "path_ruff=$(command -v ruff || true)"',
                f'echo "pinned_target=$(readlink -f {link} 2>/dev/null || true)"',
                f'echo "version=$({link} --version 2>&1 | head -n 1)"',
                f'resolve="$({link} check -v --no-cache --exit-zero --show-files . 2>&1 '
                '| grep -m 1 "ruff::resolve" || true)"',
                'echo "resolve=${resolve#*\\] }"',
                _INVOKE_FUNCTION,
                "invoke ruff ruff",
                "invoke python_m python -m ruff",
                "invoke python3_m python3 -m ruff",
                'uv_env="$(mktemp -d)"',
                'invoke uv_run env UV_PROJECT_ENVIRONMENT="$uv_env/venv" '
                "uv run --no-sync --quiet ruff",
                'rm -rf "$uv_env"',
                "if [ -f uv.lock ]; then echo uv_project=uv.lock; "
                "elif [ -f pyproject.toml ]; then echo uv_project=pyproject.toml; "
                "else echo uv_project=none; fi",
                "if [ -x .venv/bin/ruff ]; then echo project_venv_ruff=yes; "
                "else echo project_venv_ruff=no; fi",
            ]
        )
        return Provision(use.slug, self.manifest.ref, install, check)

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        version, config = self.options(use)
        records = evidence.component_log(use.slug, PROVISION_CHANNEL)
        if not records:
            return ProbeOutcome(False, "no provision record: the trial runner did not provision")
        values, failure = _checked_values(records[-1])
        if failure is not None:
            return ProbeOutcome(False, failure)
        found, target = values.get("version"), values.get("pinned_target", "")
        resolved = resolved_config(values.get("resolve", ""))
        expected = f"{values.get('workspace', '').rstrip('/')}/{config}" if config else None
        first = step_index == 0
        problems = _problems(
            found=(found, f"ruff {version}"),
            target=(target, f"{self.install_dir(use)}/"),
            config=(resolved, expected if first else None),
        )
        results = invocation_results(values, version)
        path_ruff = values.get("path_ruff", "")
        if first:
            problems += _invocation_problems(results, path_ruff, version)
        if problems:
            return ProbeOutcome(False, "; ".join(problems))
        usable = ",".join(result.label for result in results if result.pinned)
        detail = f"ruff {version} at {target}; {_invocation_report(results, values)}; "
        detail += f"config {resolved or 'ruff defaults'}"
        return ProbeOutcome(True, detail, (str(found), path_ruff, resolved or "", usable))


def _problems(
    *,
    found: tuple[str | None, str],
    target: tuple[str, str],
    config: tuple[str | None, str | None],
) -> list[str]:
    """Mismatches of (observed, expected) version, install prefix and enforced config."""
    problems = []
    if found[0] != found[1]:
        problems.append(f"pinned ruff answers {found[0]!r}, expected {found[1]!r}")
    if not target[0].startswith(target[1]):
        problems.append(f"{LINK_PATH} resolves to {target[0]!r}, not the install")
    if config[1] is not None and config[0] != config[1]:
        problems.append(f"ruff resolves config {config[0]!r}, expected {config[1]!r}")
    return problems


def _invocation_problems(
    results: tuple[InvocationResult, ...], path_ruff: str, version: str
) -> list[str]:
    """Why the step-1 environment does not run the pin through every invocation."""
    problems = []
    if path_ruff != str(LINK_PATH):
        problems.append(f"'ruff' resolves on PATH to {path_ruff!r}, not {LINK_PATH}")
    problems += [
        f"{result.describe()}, expected 'ruff {version}'" for result in results if not result.pinned
    ]
    return problems


def _invocation_report(results: tuple[InvocationResult, ...], values: dict[str, str]) -> str:
    """Which invocations run the pin, what the others answered, and the uv project state."""
    usable = ", ".join(result.label for result in results if result.pinned) or "none"
    other = "; ".join(result.describe() for result in results if not result.pinned)
    return (
        f"runs the pin: {usable}{'; not the pin: ' + other if other else ''}; "
        f"PATH ruff {values.get('path_ruff') or 'none'}; "
        f"uv project {values.get('uv_project', '?')} "
        f"(project .venv ruff: {values.get('project_venv_ruff', '?')})"
    )


def _checked_values(record: dict[str, Any]) -> tuple[dict[str, str], str | None]:
    """The check's values, or why the install or check did not succeed."""
    install = record.get("install") or {}
    if not install.get("ok"):
        return {}, f"install failed: {str(install.get('output', ''))[-400:]}"
    check = record.get("check") or {}
    if not check.get("ok"):
        return {}, f"check failed: {str(check.get('error', ''))[-400:]}"
    return dict(check.get("values") or {}), None


def resolved_config(resolve_line: str) -> str | None:
    """The config path from Ruff's ``ruff::resolve`` debug line (None: defaults or unknown)."""
    if _RESOLVE_MARKER in resolve_line and " at: " in resolve_line:
        return resolve_line.rsplit(" at: ", 1)[1].strip()
    return None


LINT_TOOL = LintTool()
