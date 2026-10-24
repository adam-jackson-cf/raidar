"""Per-trial provisioning for components that install tools in the agent container.

The workspace overlay and the per-step ``setup.sh`` can only place files: the
step setup runs as the non-root agent user and wipes ``/tmp/raidar-components``
at every step. A component that must make an *executable tool* available (a
pinned linter, say) instead returns one of two provisions.

A ``Provision`` is installed at the first step:

- ``install``: a bash script run once per trial, as root, in the agent
  container, immediately before the first step's agent phase (inside that step's
  ``per_step_reset``). The tool is installed outside every path the per-step
  reset touches, so it survives the later steps, and outside the agent image, so
  the pack audit ("no scorer tools in the agent image") stays true;
- ``check``: a bash script run before *every* step's agent phase as the agent
  user. It prints ``key=value`` lines that become that step's evidence record.

A ``TrialProvision`` instead gets:

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

Each step appends one JSON record per provision to
``/logs/agent/raidar-components/<slug>.provision.jsonl`` (``install.phase =
"environment_start"`` for a ``TrialProvision``), which Harbor archives with the
step's agent logs, where the component's discovery probe reads it. Nothing
raises: a failed upload, install or check is recorded, and the probe then
fails, so the trial is infra-invalid (F5) rather than run without its tool. An
environment that cannot run work at start records the install as failed for the
same reason.

A definition opts in by implementing ``provision(use)``; ``trial_provisions``
lists a compiled condition's provisions of both kinds and
``install_trial_provisioning`` installs each kind's hooks.
"""

from __future__ import annotations

import base64
import json
import shlex
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from raidar.subjects.treatments.definition.contribution import (
    EVIDENCE_DIR,
    SupportFile,
    evidence_log,
)
from raidar.subjects.treatments.definition.manifest import ComponentUseContext

if TYPE_CHECKING:
    from raidar.subjects.treatments.definition.compiled import CompiledCondition

PROVISION_CHANNEL = "provision"
PROVISION_RECORD_KIND = "raidar-provision"
INSTALL_TIMEOUT_S = 300
CHECK_TIMEOUT_S = 60
# Output kept in a record when an install or check fails (tail, characters).
MAX_OUTPUT_CHARS = 4000
STAGING_ROOT = PurePosixPath("/opt/raidar-tools/.staging")
INSTALL_PHASE = "environment_start"
PAYLOAD_ENV = "RAIDAR_PAYLOAD_DIR"
AGENT_USER_ENV = "RAIDAR_AGENT_USER"
UPLOAD_TIMEOUT_S = 60


@dataclass(frozen=True)
class Provision:
    """One component use's per-trial install and per-step check."""

    slug: str
    component: str
    install: str
    check: str


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


@runtime_checkable
class ProvisioningComponent(Protocol):
    def provision(self, use: ComponentUseContext) -> AnyProvision: ...


def trial_provisions(compiled: CompiledCondition) -> tuple[AnyProvision, ...]:
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


async def _check(
    environment: Any, provision: Provision | TrialProvision, user: str | None
) -> dict[str, Any]:
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
            check = await _check(environment, provision, user) if state.ok else None
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


def install_trial_provisioning(trial: Any, provisions: Sequence[AnyProvision]) -> None:
    """Install each kind's hooks: ``Provision`` at the first step, ``TrialProvision`` at start."""
    install_provisioning(trial, [item for item in provisions if isinstance(item, Provision)])
    trial_side = [item for item in provisions if isinstance(item, TrialProvision)]
    if trial_side:
        _install_trial_provisions(trial, trial_side)
