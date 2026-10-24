"""T1: hash Raidar's provisioned tool tree around the agent phases (mechanics-v1 design r2 §3.2).

``/opt/raidar-tools`` holds the root-owned tools a component provisions (gate, linters,
their virtualenvs and configs); ``/usr/local/bin`` links that point into it are the
provisioned entry points. ``install_tool_tree_watch`` hashes both through a root ``exec``
before the trial's first agent phase (the baseline) and after every agent phase, once the
reaper has ended the agent's processes. Every measurement goes to
``<trial dir>/raidar-tool-hashes.json`` on the host, where the agent cannot reach it, and
``raidar health`` (check ``T1``) compares each against the first. A hash that cannot be
taken is recorded as unmeasured, never as unchanged. The hooks never raise over the agent
outcome. ``/opt/raidar-tools/.staging`` (the install's scratch area) is not part of the hash.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harbor.trial.hooks import TrialEvent, TrialHookEvent
from harbor.trial.trial import Trial

from raidar.grading.bundle import VerifierBundle, load_bundle
from raidar.grading.guard import current_step

TOOL_HASHES = "raidar-tool-hashes.json"
TOOLS_ROOT = "/opt/raidar-tools"
HASH_TIMEOUT_S = 120
_ERROR = "@@RAIDAR_ERROR"
_MARK = "@@TOOLS"

# Runs as root. One digest over: entry metadata (type, mode, owner, link target), the sha256
# of every regular file, and the /usr/local/bin links that point into the tree.
_SCRIPT = """
if [ ! -d /opt/raidar-tools ]; then echo "@@TOOLS absent"; exit 0; fi
manifest=$(
  cd /opt/raidar-tools || exit 3
  find . -path ./.staging -prune -o -printf 'e|%p|%y|%m|%U|%G|%l\\n' | LC_ALL=C sort
  find . -path ./.staging -prune -o -type f -exec sha256sum {} + | LC_ALL=C sort -k2
  for link in /usr/local/bin/*; do
    [ -L "$link" ] || continue
    target=$(readlink "$link")
    case "$target" in /opt/raidar-tools/*) echo "l|$link|$target";; esac
  done
) || { echo "@@RAIDAR_ERROR tool tree manifest failed"; exit 0; }
digest=$(printf '%s\\n' "$manifest" | sha256sum | cut -d' ' -f1)
echo "@@TOOLS present $digest $(printf '%s\\n' "$manifest" | wc -l)"
"""


@dataclass(frozen=True)
class ToolTreeReading:
    present: bool
    sha256: str | None
    entries: int


def parse_tool_output(output: str) -> ToolTreeReading | None:
    """The reading, or ``None`` (unmeasured) when the output is missing or malformed."""
    for line in output.splitlines():
        if line.startswith(_ERROR):
            return None
        fields = line.split()
        if fields[:1] != [_MARK]:
            continue
        if fields[1:] == ["absent"]:
            return ToolTreeReading(False, None, 0)
        if len(fields) == 4 and fields[1] == "present" and fields[3].isdigit():
            return ToolTreeReading(True, fields[2], int(fields[3]))
    return None


async def hash_tool_tree(environment: Any) -> tuple[ToolTreeReading | None, str | None]:
    """One reading and no error, or no reading and why."""
    try:
        result = await environment.exec(_SCRIPT, user="root", timeout_sec=HASH_TIMEOUT_S)
    except Exception as error:  # an unmeasured point, never a trial failure
        return None, f"hash exec failed: {type(error).__name__}: {error}"
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    reading = parse_tool_output(output)
    return (reading, None) if reading else (None, f"hash output unusable: {output[-200:]!r}")


def _point(
    label: str, step: str | None, reading: ToolTreeReading | None, error: str | None
) -> dict[str, Any]:
    point: dict[str, Any] = {"point": label, "step": step, "measured": reading is not None}
    if reading is not None:
        point |= {"present": reading.present, "sha256": reading.sha256, "entries": reading.entries}
    else:
        point["unmeasured"] = error
    return point


class ToolTreeWatch:
    """The host-held hash record of one trial."""

    def __init__(self, trial: Trial, bundle: VerifierBundle) -> None:
        self.trial = trial
        self.bundle = bundle
        self.points: list[dict[str, Any]] = []

    @property
    def path(self) -> Path:
        return Path(self.trial.paths.trial_dir) / TOOL_HASHES

    def _step(self, event: TrialHookEvent) -> str | None:
        try:
            return current_step(event, self.bundle)
        except Exception:  # noqa: BLE001 - a label only
            return None

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"kind": "raidar-tool-hashes", "root": TOOLS_ROOT, "points": self.points}
        self.path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")

    async def measure(self, label: str, event: TrialHookEvent) -> None:
        reading, error = await hash_tool_tree(self.trial.agent_environment)
        self.points.append(_point(label, self._step(event), reading, error))
        self.write()

    async def before_agent(self, event: TrialHookEvent) -> None:
        if not self.points:  # the baseline: taken once, before the first agent phase
            await self.measure("baseline", event)

    async def after_agent(self, event: TrialHookEvent) -> None:
        await self.measure("after_agent", event)


def install_tool_tree_watch(trial: Trial, oracle_bundle: str | Path | VerifierBundle) -> None:
    """Install after the provisioning hooks (the tree exists) and the reaper (agent is dead)."""
    bundle = (
        oracle_bundle
        if isinstance(oracle_bundle, VerifierBundle)
        else load_bundle(Path(oracle_bundle))
    )
    watch = ToolTreeWatch(trial, bundle)
    trial.add_hook(TrialEvent.AGENT_START, watch.before_agent)
    trial.add_hook(TrialEvent.AGENT_END, watch.after_agent)


def load_tool_hashes(trial_dir: Path) -> dict[str, Any] | None:
    path = trial_dir / TOOL_HASHES
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text())
    return loaded if isinstance(loaded, dict) else None
