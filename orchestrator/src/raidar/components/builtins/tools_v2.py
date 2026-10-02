"""``service`` built-in: lint-tool@2, the pinned Ruff runnable the ways agents call it.

lint-tool@1 (``raidar.components.builtins.tools``, left byte-identical because
job ``ruff-lint-02`` is locked against it) links the pinned binary to
``/usr/local/bin/ruff`` only. In that job an agent ran ``python -m ruff`` and got
``No module named ruff`` (gate packet §14.4). lint-tool@2 keeps @1's install and
adds:

- install: a ``.pth`` file in the site-packages of each image interpreter
  (``python3``, ``python``) that points at the install directory, so
  ``python -m ruff`` imports the pinned wheel's ``ruff`` package, whose
  ``__main__`` execs the pinned binary. The ``.pth`` and the install are
  root-owned and live outside the agent image (the pack audit's "no scorer tools
  in the agent image" holds) and outside every path the per-step reset wipes.
  ``uv run ruff`` needs nothing extra: uv puts the project environment's
  ``bin`` first on ``PATH`` and falls through to ``/usr/local/bin/ruff``. A
  virtual environment the agent creates does not see the system site-packages,
  so ``python -m ruff`` inside it is not supported;
- check: before every step's agent, as the agent user, in the workspace, each
  supported invocation's ``--version``: ``ruff`` on ``PATH``, ``python -m ruff``,
  ``python3 -m ruff`` and ``uv run ruff``. ``uv run`` is called with
  ``--no-sync`` and a throwaway ``UV_PROJECT_ENVIRONMENT``, so it discovers the
  workspace's project (if any) but writes no ``.venv`` or ``uv.lock`` into it.
  The check also records whether the workspace is a uv project and whether a
  project ``.venv`` has its own ``ruff`` (which the agent's real ``uv run ruff``
  would prefer).

The discovery probe requires, at every step, @1's guarantees (install ok, the
pinned version at the provisioned path). At the first step, before the agent has
acted, it also requires that ``ruff`` resolves on ``PATH`` to the provisioned link,
that every supported invocation answers the pinned version, and (with
``with.config``) that Ruff resolves that workspace file. Later steps report which
invocations ran the pinned Ruff without enforcing it: the agent may install its
own Ruff or edit its config, which is behaviour, not infrastructure.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from raidar.components.builtins.tools import (
    LINK_PATH,
    LintTool,
    _checked_values,
    _problems,
    resolved_config,
)
from raidar.components.evidence import StepEvidence
from raidar.components.manifest import (
    ComponentManifest,
    ComponentUseContext,
    DiscoveryProbe,
    ProbeOutcome,
)
from raidar.components.provision import PROVISION_CHANNEL, Provision

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


class LintToolV2(LintTool):
    """lint-tool@1 plus ``python -m ruff``, probed across every supported invocation."""

    manifest = ComponentManifest(
        name="lint-tool",
        version="2",
        kind=LintTool.manifest.kind,
        description=(
            "Install a hash-pinned Ruff in the trial container (outside the agent image), "
            "runnable as 'ruff', 'python -m ruff' and 'uv run ruff'"
        ),
        supports=LintTool.manifest.supports,
        network=LintTool.manifest.network,
        fingerprint=(
            *LintTool.manifest.fingerprint,
            "raidar/components/builtins/tools_v2.py",
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

    def pth_name(self, use: ComponentUseContext) -> str:
        return f"raidar-{use.slug}-ruff.pth"

    def provision(self, use: ComponentUseContext) -> Provision:
        base = super().provision(use)
        target = shlex.quote(str(self.install_dir(use)))
        pth = shlex.quote(self.pth_name(use))
        install = "\n".join(
            [
                base.install,
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
                base.check,
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


LINT_TOOL_V2 = LintToolV2()
