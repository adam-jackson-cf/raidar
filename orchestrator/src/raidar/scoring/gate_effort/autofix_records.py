"""One step's ``guardrail-gate@4`` autofix passes, read back from its gate record.

The wrapper (``raidar.subjects.treatments.runtime.guardrail_gate``) records every autofix
pass under ``autofix.runs`` of ``guardrail-gate/guardrail-gate.json``: its
mode (``apply`` before an enforced commit check, ``dry_run`` on a shadow
gate's throwaway copy), its typed error if it could not complete, what the
pack's command reported (files and lines changed, codes fixed, all and
block-tier) and, for a dry run, the shadow check of the fixed copy
(``post_check``, a gate-run record). Nothing is written. The contract's key
names are repeated here, not imported from the component, so this scorer's
identity does not move with the wrapper's runtime code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.scoring.gate_effort.records import RECORD_PATH, GateRunView, _run_view

AUTOFIX_KEY = "autofix"
APPLY, DRY_RUN = "apply", "dry_run"


@dataclass(frozen=True)
class AutofixPass:
    """One autofix pass as the gate recorded it."""

    run: int
    mode: str
    before_gate_run: int | None
    error_type: str | None
    error: str | None
    files_changed: int | None
    lines_changed: int | None
    fixed_blocking: dict[str, int] | None
    post_check: GateRunView | None
    post_check_record: dict[str, Any] | None

    @property
    def ok(self) -> bool:
        return self.error_type is None


@dataclass(frozen=True)
class StepAutofix:
    """The step's autofix block: whether it was on, and its passes in order."""

    enabled: bool
    mode: str | None
    passes: tuple[AutofixPass, ...]

    def before(self, gate_run: int) -> AutofixPass | None:
        """The apply pass that ran right before gate run ``gate_run`` (1-based)."""
        return next((p for p in self.passes if p.before_gate_run == gate_run), None)


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _codes(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    counts = {str(code): _int(count) for code, count in value.items()}
    if any(count is None for count in counts.values()):
        return None
    return {code: count or 0 for code, count in counts.items()}


def _pass(trace_dir: Path, item: dict[str, Any]) -> AutofixPass:
    check = item.get("post_check")
    view = _run_view(trace_dir, check) if isinstance(check, dict) else None
    changed = item.get("files_changed")
    return AutofixPass(
        run=_int(item.get("run")) or 0,
        mode=str(item.get("mode") or ""),
        before_gate_run=_int(item.get("before_gate_run")),
        error_type=str(item["error_type"]) if item.get("error_type") else None,
        error=str(item["error"]) if item.get("error") else None,
        files_changed=len(changed) if isinstance(changed, list) else None,
        lines_changed=_int(item.get("lines_changed")),
        fixed_blocking=_codes(item.get("codes_fixed_blocking")),
        post_check=view,
        post_check_record=check if isinstance(check, dict) else None,
    )


def read_step_autofix(trace_dir: Path | None) -> StepAutofix | None:
    """The step's autofix block (None: no record, or a gate without one: @1-@3)."""
    if trace_dir is None:
        return None
    try:
        payload = json.loads((trace_dir / RECORD_PATH).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    block = payload.get(AUTOFIX_KEY) if isinstance(payload, dict) else None
    if not isinstance(block, dict):
        return None
    listed = block.get("runs")
    runs = listed if isinstance(listed, list) else []
    return StepAutofix(
        enabled=block.get("enabled") is True,
        mode=str(block["mode"]) if block.get("mode") else None,
        passes=tuple(_pass(trace_dir, item) for item in runs if isinstance(item, dict)),
    )


__all__ = ["APPLY", "DRY_RUN", "AutofixPass", "StepAutofix", "read_step_autofix"]
