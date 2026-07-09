"""Canonical verifier runner definitions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.schemas.environment import CapabilityRequirements


class VerifierRunnerError(ValueError):
    """Raised when a verifier runner is missing or invalid."""


@dataclass(frozen=True, slots=True)
class VerifierRunnerDefinition:
    """Verifier script, invocation, and output contract."""

    id: str
    required_capabilities: CapabilityRequirements
    asset_path: Path
    bundle_filename: str
    argv_template: tuple[str, ...]
    output_manifest: tuple[str, ...]

    def cache_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "required_capabilities": self.required_capabilities.model_dump(mode="json"),
            "asset": str(self.asset_path),
            "bundle_filename": self.bundle_filename,
            "argv_template": list(self.argv_template),
            "output_manifest": list(self.output_manifest),
        }

    def render_command(self, script_dir_variable: str = "SCRIPT_DIR") -> str:
        script_path = f'"${script_dir_variable}/{self.bundle_filename}"'
        scenario_spec_path = f'"${script_dir_variable}/scenario-spec.json"'
        parts = [
            item.format(script=script_path, scenario_spec=scenario_spec_path)
            for item in self.argv_template
        ]
        return " ".join(parts)


_ASSETS_ROOT = Path(__file__).parent.parent / "assets"
_OUTPUT_MANIFEST = (
    "scorecard.json",
    "gate-history.json",
    "execution-validity.json",
    "performance-gates.json",
    "reward.txt",
    "test-stdout.txt",
)

_RUNNERS: dict[str, VerifierRunnerDefinition] = {
    "python@1": VerifierRunnerDefinition(
        id="python@1",
        required_capabilities=CapabilityRequirements(runtimes={"python": ">=3"}),
        asset_path=_ASSETS_ROOT / "verifier_score_scenario.py",
        bundle_filename="score-scenario.py",
        argv_template=("python", "{script}", "{scenario_spec}"),
        output_manifest=_OUTPUT_MANIFEST,
    ),
    "bun@1": VerifierRunnerDefinition(
        id="bun@1",
        required_capabilities=CapabilityRequirements(package_managers={"bun": ">=1"}),
        asset_path=_ASSETS_ROOT / "verifier-score-scenario.mjs",
        bundle_filename="score-scenario.mjs",
        argv_template=("bun", "run", "{script}", "{scenario_spec}"),
        output_manifest=_OUTPUT_MANIFEST,
    ),
}


def verifier_runner_definition(runner_id: str) -> VerifierRunnerDefinition:
    """Return a registered verifier runner definition."""

    try:
        return _RUNNERS[runner_id]
    except KeyError as exc:
        available = ", ".join(sorted(_RUNNERS))
        raise VerifierRunnerError(
            f"Unknown verifier runner {runner_id!r}; available runners: {available}"
        ) from exc


def verifier_output_manifest() -> tuple[str, ...]:
    """Return the union of verifier outputs persisted by current runners."""

    return tuple(
        sorted({filename for runner in _RUNNERS.values() for filename in runner.output_manifest})
    )
