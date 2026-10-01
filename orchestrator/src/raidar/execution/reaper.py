"""Agent-end reap: no agent process outlives its agent phase (requirements F3).

Harbor ends a timed-out agent by cancelling the host-side ``docker exec``
client, which leaves the in-container process tree running. Without a reap, a
timed-out ``codex exec`` keeps editing ``/app`` while artifacts are collected
and graded, and on into the next step's workdir upload. The per-step
``setup.sh`` reset (``components/step_reset.sh``) runs too late for that: it
runs after the next step's upload and never after the last step.

``install_agent_reaper`` adds an ``AGENT_END`` hook (Harbor emits it in a
``finally``, so it also fires after a timeout or an agent error) that, as root,
kills every process owned by the agent user, rescans, stashes an agent-authored
``/app/setup.sh`` for the next step's ``setup.sh`` to put back, and writes the report to
``raidar-oracle/steps/<step>/agent-reap.json`` host-side. ``collect`` marks the
step infra-invalid when the reap could not run or a process survived it. The
hook never raises: a failure is recorded, not thrown over the agent outcome.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harbor.trial.hooks import TrialEvent, TrialHookEvent
from harbor.trial.trial import Trial

from raidar.components.setup_script import AGENT_SETUP_STASH, SETUP_SCRIPT_NAME
from raidar.oracle.bundle import OracleBundle, load_bundle
from raidar.oracle.guard import current_step
from raidar.oracle.records import step_record_dir
from raidar.oracle.snapshot import APP_SOURCE

REAP_REPORT = "agent-reap.json"
REAP_TIMEOUT_S = 60
_KILLED = "@@RAIDAR_KILLED"
_RESIDUAL = "@@RAIDAR_RESIDUAL"
_ERROR = "@@RAIDAR_ERROR"

# Runs as root. Lists the agent uid's live (non-zombie) processes, kills them
# (three passes, for processes forked mid-kill), then lists what survived. It
# also stashes an agent-authored workspace setup.sh, which the next step's
# workdir upload overwrites and the generated setup.sh then puts back.
_SCRIPT = """
uid=$(id -u -- {user} 2>/dev/null) || {{ echo "{error} unknown agent user {user}"; exit 0; }}
if [ "$uid" = 0 ]; then echo "{error} agent user is root"; exit 0; fi
live() {{ ps -e -o pid=,uid=,stat=,args= 2>/dev/null | awk -v u="$uid" '$2==u && $3 !~ /^Z/'; }}
echo "{killed}"
live
for pass in 1 2 3; do
  pids=$(live | awk '{{print $1}}')
  [ -z "$pids" ] && break
  for pid in $pids; do kill -KILL "$pid" 2>/dev/null; done
  sleep 0.3
done
echo "{residual}"
live
rm -f -- {stash}
if [ -f {agent_setup} ] && [ ! -L {agent_setup} ]; then
  cp -- {agent_setup} {stash} && chown "$uid" {stash}
fi
"""


@dataclass(frozen=True)
class ReapReport:
    user: str
    killed: tuple[str, ...]
    residual: tuple[str, ...]
    error: str | None

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": "raidar-agent-reap",
            "user": self.user,
            "killed": list(self.killed),
            "residual": list(self.residual),
            "error": self.error,
        }


def reap_script(user: str) -> str:
    return _SCRIPT.format(
        user=shlex.quote(user),
        killed=_KILLED,
        residual=_RESIDUAL,
        error=_ERROR,
        stash=shlex.quote(AGENT_SETUP_STASH),
        agent_setup=shlex.quote(f"{APP_SOURCE}/{SETUP_SCRIPT_NAME}"),
    )


def parse_reap_output(user: str, output: str) -> ReapReport:
    sections: dict[str, list[str]] = {_KILLED: [], _RESIDUAL: []}
    current: list[str] | None = None
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(_ERROR):
            return ReapReport(user, (), (), stripped[len(_ERROR) :].strip() or "reap refused")
        if stripped in sections:
            current = sections[stripped]
        elif stripped and current is not None:
            current.append(stripped)
    if _RESIDUAL not in output:
        return ReapReport(user, (), (), f"reap did not complete: {output[-500:]!r}")
    return ReapReport(user, tuple(sections[_KILLED]), tuple(sections[_RESIDUAL]), None)


def _agent_user(trial: Trial) -> str | None:
    user = trial.task.config.agent.user
    return None if user is None else str(user)


async def reap_agent_processes(environment: Any, user: str | None) -> ReapReport:
    if user is None:
        return ReapReport("", (), (), "the task sets no agent user; nothing can be reaped")
    try:
        result = await environment.exec(reap_script(user), user="root", timeout_sec=REAP_TIMEOUT_S)
    except Exception as error:  # recorded, never raised over the agent outcome
        return ReapReport(user, (), (), f"reap exec failed: {type(error).__name__}: {error}")
    return parse_reap_output(user, f"{result.stdout or ''}\n{result.stderr or ''}")


def write_reap_report(record_dir: Path, report: ReapReport) -> None:
    record_dir.mkdir(parents=True, exist_ok=True)
    (record_dir / REAP_REPORT).write_text(json.dumps(report.as_json(), indent=2, sort_keys=True))


def load_reap_report(record_dir: Path) -> dict[str, Any] | None:
    path = record_dir / REAP_REPORT
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text())
    return loaded if isinstance(loaded, dict) else None


def reap_infra_reason(report: dict[str, Any] | None) -> str | None:
    """Why the step's state after its agent phase cannot be trusted, if it cannot."""
    if report is None:
        return None
    if report.get("error"):
        return f"agent_reap_failed:{report['error']}"
    if report.get("residual"):
        return f"agent_reap_residual:{len(report['residual'])}"
    return None


def install_agent_reaper(trial: Trial, oracle_bundle: str | Path | OracleBundle) -> None:
    bundle = (
        oracle_bundle
        if isinstance(oracle_bundle, OracleBundle)
        else load_bundle(Path(oracle_bundle))
    )

    async def after_agent(event: TrialHookEvent) -> None:
        report = await reap_agent_processes(trial.agent_environment, _agent_user(trial))
        try:
            step = current_step(event, bundle)
        except Exception:  # noqa: BLE001 - an unresolvable step is recorded at the root
            step = "unknown"
        write_reap_report(step_record_dir(trial.paths.trial_dir, step), report)

    trial.add_hook(TrialEvent.AGENT_END, after_agent)
