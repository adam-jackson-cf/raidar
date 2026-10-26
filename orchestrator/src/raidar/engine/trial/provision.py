"""Per-trial provisioning: lab work done in the agent container before any step.

A ``TrialProvision`` is installed once per trial, right after the container starts
(``RunScopedDockerEnvironment.add_post_start_hook``), so its time is the trial's
``environment_start``: never a step's, never the agent's. Its payload files are uploaded to a
root-only staging dir (``$RAIDAR_PAYLOAD_DIR`` in the script) and its ``install`` script runs
as root, with ``$RAIDAR_AGENT_USER`` naming the agent user. ``snapshot`` names a container
directory whose tree digest (``raidar.contract.snapshot`` excludes applied) is recorded after
the install: the state the first step starts from. Before every step's agent phase its
``check`` script runs as the agent user and prints ``key=value`` lines.

Each step appends one record per provision to ``/logs/agent/raidar-provision/<slug>.jsonl``
(the install outcome, the snapshot digest and that step's check), which Harbor archives with
the step's agent logs. Nothing raises: a failed upload, install, snapshot or check is
recorded, and the step's lab check (``provision_check``) then fails, so the trial is
infra-invalid rather than run without what was provisioned. An environment that cannot run
work at start records the install as failed for the same reason.
"""

from __future__ import annotations

import base64
import json
import shlex
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from harbor.models.trial.paths import EnvironmentPaths

from raidar.contract.snapshot import SNAPSHOT_EXCLUDES, tree_digest

PROVISION_RECORD_KIND = "raidar-provision"
PROVISION_DIR_NAME = "raidar-provision"
PROVISION_DIR = EnvironmentPaths.agent_dir / PROVISION_DIR_NAME
INSTALL_PHASE = "environment_start"
INSTALL_TIMEOUT_S = 600
CHECK_TIMEOUT_S = 60
UPLOAD_TIMEOUT_S = 60
RECORD_TIMEOUT_S = 30
# Output kept in a record when an install or check fails (tail, characters).
MAX_OUTPUT_CHARS = 4000
STAGING_ROOT = PurePosixPath("/opt/raidar-tools/.staging")
PAYLOAD_ENV = "RAIDAR_PAYLOAD_DIR"
AGENT_USER_ENV = "RAIDAR_AGENT_USER"


@dataclass(frozen=True)
class PayloadFile:
    content: bytes
    executable: bool = False


@dataclass(frozen=True)
class TrialProvision:
    """One install at environment start, its optional snapshot, and its per-step check."""

    slug: str
    payload: Mapping[str, PayloadFile]
    install: str
    check: str
    snapshot: str | None = None
    facts: Mapping[str, Any] = field(default_factory=dict)
    """What was provisioned, recorded verbatim in every step's record."""

    @property
    def staging(self) -> PurePosixPath:
        return STAGING_ROOT / self.slug


def provision_log(slug: str) -> PurePosixPath:
    return PROVISION_DIR / f"{slug}.jsonl"


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


def append_record_command(slug: str, record: Mapping[str, Any]) -> str:
    """Shell that appends ``record`` as one JSONL line to the slug's provision log."""
    line = json.dumps(record, sort_keys=True) + "\n"
    encoded = base64.b64encode(line.encode()).decode("ascii")
    target = shlex.quote(str(provision_log(slug)))
    return (
        f"mkdir -p {shlex.quote(str(PROVISION_DIR))} && "
        f"printf %s {encoded} | base64 -d >> {target} && chmod a+r {target}"
    )


@dataclass
class _Installed:
    ok: bool = False
    duration_s: float | None = None
    output: str = "the install did not run: the environment never started"
    snapshot_digest: str | None = None


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


async def _snapshot_digest(environment: Any, source: str) -> str:
    with tempfile.TemporaryDirectory(prefix="raidar-provision-snapshot-") as tmp:
        await environment.download_dir_with_exclusions(
            source_dir=source, target_dir=Path(tmp), exclude=list(SNAPSHOT_EXCLUDES)
        )
        return tree_digest(Path(tmp))


async def install_at_start(
    environment: Any, provision: TrialProvision, user: str | None, state: _Installed
) -> None:
    """Upload the payload, run the install as root and take the snapshot; record into ``state``."""
    started = time.perf_counter()
    try:
        await _upload(environment, provision)
        env = {PAYLOAD_ENV: str(provision.staging), AGENT_USER_ENV: user or ""}
        result = await environment.exec(
            provision.install, env=env, user="root", timeout_sec=INSTALL_TIMEOUT_S
        )
        state.ok = result.return_code == 0
        state.output = "" if state.ok else _tail(f"{result.stdout or ''}\n{result.stderr or ''}")
        if state.ok and provision.snapshot is not None:
            state.snapshot_digest = await _snapshot_digest(environment, provision.snapshot)
    except Exception as error:  # noqa: BLE001 - recorded; the step's lab check turns it into infra
        state.ok, state.output = False, f"install failed: {type(error).__name__}: {error}"
    state.duration_s = round(time.perf_counter() - started, 3)


async def _check(environment: Any, provision: TrialProvision, user: str | None) -> dict[str, Any]:
    try:
        result = await environment.exec(provision.check, user=user, timeout_sec=CHECK_TIMEOUT_S)
    except Exception as error:  # noqa: BLE001 - recorded; the step's lab check turns it into infra
        return {"ok": False, "error": f"check exec failed: {type(error).__name__}: {error}"}
    record: dict[str, Any] = {"ok": result.return_code == 0}
    record["values"] = parse_check_output(result.stdout or "")
    if not record["ok"]:
        record["error"] = _tail(f"{result.stdout or ''}\n{result.stderr or ''}")
    return record


def _record(
    provision: TrialProvision, step: str | None, user: str | None, state: _Installed, check: Any
) -> dict[str, Any]:
    return {
        "kind": PROVISION_RECORD_KIND,
        "slug": provision.slug,
        "step": step,
        "agent_user": user,
        "facts": dict(provision.facts),
        "install": {
            "ok": state.ok,
            "phase": INSTALL_PHASE,
            "duration_s": state.duration_s,
            "output": state.output,
            "snapshot": provision.snapshot,
            "snapshot_digest": state.snapshot_digest,
        },
        "check": check,
    }


def install_trial_provisioning(trial: Any, provisions: Sequence[TrialProvision]) -> None:
    """Install at environment start; check and record before every step's agent phase."""
    if not provisions:
        return
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
                    timeout_sec=RECORD_TIMEOUT_S,
                )
            except Exception:  # noqa: BLE001 - a missing record fails the lab check (infra)
                continue

    trial.add_hook(TrialEvent.AGENT_START, before_agent)


def read_provision_record(agent_dir: Path, slug: str) -> dict[str, Any] | None:
    """The last record a step's agent logs hold for ``slug`` (``None`` when there is none)."""
    path = agent_dir / PROVISION_DIR_NAME / f"{slug}.jsonl"
    try:
        lines = [line for line in path.read_text().splitlines() if line.strip()]
        record = json.loads(lines[-1]) if lines else None
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def provision_check(agent_dir: Path, slug: str) -> tuple[bool, str]:
    """The step's record for ``slug`` shows a successful install and a passing check."""
    record = read_provision_record(agent_dir, slug)
    if record is None:
        return False, f"{slug}: no provision record before the agent started"
    install = record.get("install") or {}
    if not install.get("ok"):
        return False, f"{slug}: install failed: {install.get('output') or 'no output'}"
    check = record.get("check") or {}
    if not check.get("ok"):
        return False, f"{slug}: check failed: {check.get('error') or 'no output'}"
    return True, f"{slug}: provisioned"
