"""Per-trial provisioning for components that install tools in the agent container.

The workspace overlay and the per-step ``setup.sh`` can only place files: the
step setup runs as the non-root agent user and wipes ``/tmp/raidar-components``
at every step. A component that must make an *executable tool* available (a
pinned linter, say) instead returns a ``Provision``:

- ``install``: a bash script run once per trial, as root, in the agent
  container, immediately before the first step's agent phase. The tool is
  installed outside every path the per-step reset touches, so it survives the
  later steps, and outside the agent image, so the pack audit ("no scorer tools
  in the agent image") stays true;
- ``check``: a bash script run before *every* step's agent phase as the agent
  user. It prints ``key=value`` lines that become that step's evidence record.

``install_provisioning`` adds the ``AGENT_START`` hook that runs both and
appends one JSON record per step to
``/logs/agent/raidar-components/<slug>.provision.jsonl``. Harbor archives that
file with the step's agent logs, where the component's discovery probe reads it.
The hook never raises: a failed install or check is recorded, and the probe then
fails, which makes the trajectory infra-invalid (F5) rather than letting the
arm run without its tool.

A definition opts in by implementing ``provision(use) -> Provision``; the
compiled condition's uses are scanned with ``condition_provisions``.
"""

from __future__ import annotations

import base64
import json
import shlex
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from raidar.components.contribution import EVIDENCE_DIR, evidence_log
from raidar.components.manifest import ComponentUseContext

if TYPE_CHECKING:
    from raidar.components.compiled import CompiledCondition

PROVISION_CHANNEL = "provision"
PROVISION_RECORD_KIND = "raidar-provision"
INSTALL_TIMEOUT_S = 300
CHECK_TIMEOUT_S = 60
# Output kept in a record when an install or check fails (tail, characters).
MAX_OUTPUT_CHARS = 4000


@dataclass(frozen=True)
class Provision:
    """One component use's per-trial install and per-step check."""

    slug: str
    component: str
    install: str
    check: str


@runtime_checkable
class ProvisioningComponent(Protocol):
    def provision(self, use: ComponentUseContext) -> Provision: ...


def condition_provisions(compiled: CompiledCondition) -> tuple[Provision, ...]:
    """The provisions of every use in a compiled condition, in condition order."""
    return tuple(
        use.definition.provision(use.context)
        for use in compiled.uses
        if isinstance(use.definition, ProvisioningComponent)
    )


def parse_check_output(stdout: str) -> dict[str, str]:
    """``key=value`` lines from a check script (later keys win; other lines ignored)."""
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and key and key.replace("_", "").isalnum():
            values[key] = value.strip()
    return values


def _tail(text: str | None) -> str:
    return (text or "")[-MAX_OUTPUT_CHARS:]


def append_record_command(slug: str, record: dict[str, Any]) -> str:
    """Shell that appends ``record`` as one JSONL line to the slug's provision log."""
    line = json.dumps(record, sort_keys=True) + "\n"
    encoded = base64.b64encode(line.encode()).decode("ascii")
    target = shlex.quote(str(evidence_log(slug, PROVISION_CHANNEL)))
    return (
        f"mkdir -p {shlex.quote(str(EVIDENCE_DIR))} && "
        f"printf %s {encoded} | base64 -d >> {target} && chmod a+r {target}"
    )


@dataclass
class _InstallState:
    """The trial's install outcome, recorded at the first step and carried after."""

    attempted: bool = False
    ok: bool = False
    duration_s: float | None = None
    output: str = ""


async def _install(environment: Any, provision: Provision, state: _InstallState) -> None:
    started = time.perf_counter()
    try:
        result = await environment.exec(
            provision.install, user="root", timeout_sec=INSTALL_TIMEOUT_S
        )
        state.ok = result.return_code == 0
        state.output = "" if state.ok else _tail(f"{result.stdout or ''}\n{result.stderr or ''}")
    except Exception as error:  # noqa: BLE001 - recorded; the probe turns it into infra
        state.ok, state.output = False, f"install exec failed: {type(error).__name__}: {error}"
    state.attempted = True
    state.duration_s = round(time.perf_counter() - started, 3)


async def _check(environment: Any, provision: Provision, user: str | None) -> dict[str, Any]:
    try:
        result = await environment.exec(provision.check, user=user, timeout_sec=CHECK_TIMEOUT_S)
    except Exception as error:  # noqa: BLE001 - recorded; the probe turns it into infra
        return {"ok": False, "error": f"check exec failed: {type(error).__name__}: {error}"}
    record: dict[str, Any] = {"ok": result.return_code == 0, "values": {}}
    record["values"] = parse_check_output(result.stdout or "")
    if not record["ok"]:
        record["error"] = _tail(f"{result.stdout or ''}\n{result.stderr or ''}")
    return record


def install_provisioning(trial: Any, provisions: Sequence[Provision]) -> None:
    """Add the ``AGENT_START`` hook that installs (once) and checks (every step)."""
    if not provisions:
        return
    from harbor.trial.hooks import TrialEvent

    states = {provision.slug: _InstallState() for provision in provisions}
    configured = trial.task.config.agent.user
    user = None if configured is None else str(configured)

    async def before_agent(event: Any) -> None:
        steps = event.result.step_results
        step = steps[-1].step_name if steps else None
        environment = trial.agent_environment
        for provision in provisions:
            state = states[provision.slug]
            installed_now = not state.attempted
            if installed_now:
                await _install(environment, provision, state)
            check = await _check(environment, provision, user) if state.ok else None
            record = {
                "kind": PROVISION_RECORD_KIND,
                "component": provision.component,
                "slug": provision.slug,
                "step": step,
                "agent_user": user,
                "install": {
                    "ok": state.ok,
                    "ran_this_step": installed_now,
                    "duration_s": state.duration_s,
                    "output": state.output,
                },
                "check": check,
            }
            try:
                await environment.exec(
                    append_record_command(provision.slug, record), user="root", timeout_sec=30
                )
            except Exception:  # noqa: BLE001 - a missing record fails the probe (infra)
                continue

    trial.add_hook(TrialEvent.AGENT_START, before_agent)
