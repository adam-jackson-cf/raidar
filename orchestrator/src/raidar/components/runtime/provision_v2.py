"""Per-trial tool installs at environment start, with a cheap per-step check.

``raidar.components.runtime.provision`` (python-guardrails@1 and @2) is frozen: its
install runs at the first step's ``AGENT_START``, inside that step's
``per_step_reset``, and its per-step check may run the tool. A component that
returns a ``TrialProvision`` instead gets:

- **install, once per trial, at environment start** (as root). The payload
  files are uploaded to a root-only staging dir (``STAGING_ROOT/<slug>``,
  ``$RAIDAR_PAYLOAD_DIR`` in the script) and the install script runs right
  after the container starts (``RunScopedDockerEnvironment.add_post_start_hook``),
  so its time is the trial's ``environment_start``, never a step's. The agent
  user is ``$RAIDAR_AGENT_USER`` (for a smoke the install runs as that user).
  Nothing is materialised by the per-step ``setup.sh``, so no step pays for
  the payload either;
- **check, before every step's agent** (as the agent user): ``key=value``
  lines that become the step's evidence record. It should be cheap (hashes,
  ownership, the install's own recorded results), so ``per_step_reset`` stays
  within its budget while the protected tree is still verified at each step.

Each step appends one JSON record to
``/logs/agent/raidar-components/<slug>.provision.jsonl`` (the same channel and
record shape as ``provision``, with ``install.phase = "environment_start"``),
where the component's probe reads it. Nothing raises: a failed upload, install
or check is recorded, and the probe then fails, so the trial is infra-invalid
(F5) rather than run without its tool. An environment that cannot run work at
start records the install as failed for the same reason.

``trial_provisions`` lists a compiled condition's provisions of both kinds and
``install_trial_provisioning`` installs each kind's hooks.
"""

from __future__ import annotations

import shlex
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from raidar.components.definition.contribution import SupportFile
from raidar.components.runtime.provision import (
    CHECK_TIMEOUT_S,
    INSTALL_TIMEOUT_S,
    PROVISION_RECORD_KIND,
    Provision,
    ProvisioningComponent,
    _check,
    _tail,
    append_record_command,
    install_provisioning,
)

if TYPE_CHECKING:
    from raidar.components.definition.compiled import CompiledCondition

STAGING_ROOT = PurePosixPath("/opt/raidar-tools/.staging")
INSTALL_PHASE = "environment_start"
PAYLOAD_ENV = "RAIDAR_PAYLOAD_DIR"
AGENT_USER_ENV = "RAIDAR_AGENT_USER"
UPLOAD_TIMEOUT_S = 60


@dataclass(frozen=True)
class TrialProvision:
    """One component use's install at environment start and its per-step check."""

    slug: str
    component: str
    payload: Mapping[str, SupportFile]
    install: str
    check: str

    @property
    def staging(self) -> PurePosixPath:
        return STAGING_ROOT / self.slug


AnyProvision = Provision | TrialProvision


def trial_provisions(compiled: CompiledCondition) -> tuple[AnyProvision, ...]:
    """The provisions of every use in a compiled condition, in condition order."""
    found: list[AnyProvision] = []
    for use in compiled.uses:
        if isinstance(use.definition, ProvisioningComponent):
            provision: Any = use.definition.provision(use.context)
            found.append(provision)
    return tuple(found)


@dataclass
class _Installed:
    ok: bool = False
    duration_s: float | None = None
    output: str = "the install did not run: the environment never started"


def _write_payload(provision: TrialProvision, folder: Path) -> None:
    for relative, support in provision.payload.items():
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(support.content)
        target.chmod(0o755 if support.executable else 0o644)


async def _upload(environment: Any, provision: TrialProvision) -> None:
    staging = shlex.quote(str(provision.staging))
    await environment.exec(
        f"rm -rf {staging} && mkdir -p {staging} && chmod 700 {shlex.quote(str(STAGING_ROOT))}",
        user="root",
        timeout_sec=UPLOAD_TIMEOUT_S,
    )
    with tempfile.TemporaryDirectory(prefix="raidar-provision-") as tmp:
        _write_payload(provision, Path(tmp))
        await environment.upload_dir(tmp, str(provision.staging))


async def install_at_start(
    environment: Any, provision: TrialProvision, user: str | None, state: _Installed
) -> None:
    """Upload the payload and run the install as root; the outcome goes to ``state``."""
    started = time.perf_counter()
    try:
        await _upload(environment, provision)
        env = {PAYLOAD_ENV: str(provision.staging), AGENT_USER_ENV: user or ""}
        result = await environment.exec(
            provision.install, env=env, user="root", timeout_sec=INSTALL_TIMEOUT_S
        )
        state.ok = result.return_code == 0
        state.output = "" if state.ok else _tail(f"{result.stdout or ''}\n{result.stderr or ''}")
    except Exception as error:  # noqa: BLE001 - recorded; the probe turns it into infra
        state.ok, state.output = False, f"install failed: {type(error).__name__}: {error}"
    state.duration_s = round(time.perf_counter() - started, 3)


def _record(
    provision: TrialProvision, step: str | None, user: str | None, state: _Installed, check: Any
) -> dict[str, Any]:
    return {
        "kind": PROVISION_RECORD_KIND,
        "component": provision.component,
        "slug": provision.slug,
        "step": step,
        "agent_user": user,
        "install": {
            "ok": state.ok,
            "phase": INSTALL_PHASE,
            "duration_s": state.duration_s,
            "output": state.output,
        },
        "check": check,
    }


def _install_trial_provisions(trial: Any, provisions: Sequence[TrialProvision]) -> None:
    from harbor.trial.hooks import TrialEvent

    states = {provision.slug: _Installed() for provision in provisions}
    configured = trial.task.config.agent.user
    user = None if configured is None else str(configured)
    environment = trial.agent_environment
    register = getattr(environment, "add_post_start_hook", None)

    async def at_start() -> None:
        for provision in provisions:
            await install_at_start(environment, provision, user, states[provision.slug])

    if callable(register):
        register(at_start)
    else:
        for state in states.values():
            state.output = (
                f"{type(environment).__name__} cannot run an install at environment start"
            )

    async def before_agent(event: Any) -> None:
        steps = event.result.step_results
        step = steps[-1].step_name if steps else None
        for provision in provisions:
            state = states[provision.slug]
            check = await _check(environment, _as_check(provision), user) if state.ok else None
            try:
                await environment.exec(
                    append_record_command(
                        provision.slug, _record(provision, step, user, state, check)
                    ),
                    user="root",
                    timeout_sec=30,
                )
            except Exception:  # noqa: BLE001 - a missing record fails the probe (infra)
                continue

    trial.add_hook(TrialEvent.AGENT_START, before_agent)


def _as_check(provision: TrialProvision) -> Provision:
    """The per-step check in ``provision``'s runner shape (same timeout and parsing)."""
    return Provision(provision.slug, provision.component, "", provision.check)


def install_trial_provisioning(trial: Any, provisions: Sequence[AnyProvision]) -> None:
    """Install each kind's hooks: a frozen ``Provision`` as before, a ``TrialProvision`` here."""
    install_provisioning(trial, [item for item in provisions if isinstance(item, Provision)])
    trial_side = [item for item in provisions if isinstance(item, TrialProvision)]
    if trial_side:
        _install_trial_provisions(trial, trial_side)


__all__ = [
    "CHECK_TIMEOUT_S",
    "INSTALL_PHASE",
    "STAGING_ROOT",
    "AnyProvision",
    "TrialProvision",
    "install_trial_provisioning",
    "trial_provisions",
]
