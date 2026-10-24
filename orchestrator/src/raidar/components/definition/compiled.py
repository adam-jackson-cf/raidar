"""A compiled condition: the Harbor inputs one arm applies.

``CompiledCondition`` is what execution uses:

- ``write_task_steps(task_dir, steps)``: writes each step's ``workdir/setup.sh``
  (context reset + component support files) and the workspace overlay into the
  first step's ``workdir/``;
- ``apply_to_agent_config(config)``: adds ``kwargs.config`` (``--ak config``),
  ``kwargs.prompt_template_path``, ``skills`` and ``mcp_servers`` to Harbor's
  built-in ``codex`` / ``claude-code`` agent config, and wraps it for a
  ``workflow`` component;
- ``probe_step(agent_dir, index)``: every discovery probe plus the reset check
  over one archived step; a failure makes the trajectory infra-invalid (F5).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harbor.models.task.config import MCPServerConfig
from harbor.models.trial.config import AgentConfig
from pydantic import JsonValue

from raidar.agents.config import Harness
from raidar.components.catalog._common import tree_files
from raidar.components.definition.contribution import AgentWrapper, SupportFile
from raidar.components.definition.evidence import StepEvidence
from raidar.components.definition.manifest import (
    ComponentConfigError,
    ComponentDefinition,
    ComponentUseContext,
    ProbeOutcome,
)
from raidar.components.runtime.setup_script import SETUP_SCRIPT_NAME, render_setup_script
from raidar.spec.canonical import digest_json

# Harbor 0.23 installed agents that implement config / skills / MCP / prompt templates.
HARBOR_AGENT_NAMES: Mapping[Harness, str] = {
    Harness.CODEX_CLI: "codex",
    Harness.CLAUDE_CODE: "claude-code",
}
RESET_PROBE = "context-reset"


@dataclass(frozen=True)
class CompiledUse:
    slug: str
    ref: str
    definition: ComponentDefinition
    context: ComponentUseContext
    fingerprint: str


@dataclass(frozen=True)
class ProbeResult:
    component: str
    slug: str
    passed: bool
    detail: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProbeReport:
    step_index: int
    results: tuple[ProbeResult, ...]

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    def failures(self) -> list[ProbeResult]:
        return [result for result in self.results if not result.passed]

    def to_json(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "passed": self.passed,
            "results": [
                {
                    "component": result.component,
                    "slug": result.slug,
                    "passed": result.passed,
                    "detail": result.detail,
                    "evidence": list(result.evidence),
                }
                for result in self.results
            ],
        }


def reset_probe(evidence: StepEvidence) -> ProbeOutcome:
    """The step's reset ran, left no agent-user process and reset every state path."""
    if not evidence.reset_reports:
        return ProbeOutcome(False, "no raidar-reset.json: setup.sh did not run")
    report = evidence.reset_reports[0]
    if report.get("error"):
        return ProbeOutcome(False, f"reset refused: {report['error']}")
    residual = report.get("residual") or []
    failed = [entry.get("path") for entry in report.get("state", []) if entry.get("reset_failed")]
    if residual or failed:
        return ProbeOutcome(False, f"residual processes {residual}; state not reset {failed}")
    killed = [str(entry.get("args")) for entry in report.get("killed", [])]
    return ProbeOutcome(True, f"reset ok; killed {len(killed)} leftover process(es)", tuple(killed))


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class CompiledCondition:
    condition_id: str
    harness: Harness
    uses: tuple[CompiledUse, ...]
    overlay: Mapping[str, bytes]
    support_files: Mapping[str, SupportFile]
    native_config: Mapping[str, JsonValue]
    skills: tuple[Path, ...]
    mcp_servers: tuple[MCPServerConfig, ...]
    prompt_template: Path | None
    agent_wrapper: AgentWrapper | None
    network_egress: tuple[str, ...]
    secrets: tuple[str, ...]

    def setup_script(self) -> str:
        return render_setup_script(
            support_files=self.support_files,
            workspace_paths=self.overlay,
            component_slugs=[use.slug for use in self.uses],
        )

    @property
    def digest(self) -> str:
        skills = {
            path.name: {
                file.relative_to(path).as_posix(): _sha(file.read_bytes())
                for file in tree_files(path)
            }
            for path in self.skills
        }
        return digest_json(
            {
                "harness": self.harness.value,
                "uses": [[use.slug, use.ref, use.fingerprint] for use in self.uses],
                "setup_sh": _sha(self.setup_script().encode()),
                "overlay": {path: _sha(content) for path, content in self.overlay.items()},
                "native_config": dict(self.native_config),
                "skills": skills,
                "mcp_servers": [server.model_dump(mode="json") for server in self.mcp_servers],
                "prompt_template": (
                    _sha(self.prompt_template.read_bytes()) if self.prompt_template else None
                ),
                "agent_wrapper": (
                    [self.agent_wrapper.import_path, dict(self.agent_wrapper.kwargs)]
                    if self.agent_wrapper
                    else None
                ),
            }
        )

    def write_task_steps(self, task_dir: Path, step_names: list[str]) -> None:
        """Write each step's ``setup.sh`` and the first step's workspace overlay."""
        if not step_names:
            raise ValueError("a task needs at least one step for the per-step reset")
        script = self.setup_script()
        for index, step in enumerate(step_names):
            workdir = task_dir / "steps" / step / "workdir"
            files = {SETUP_SCRIPT_NAME: script.encode()}
            if index == 0:
                files.update(self.overlay)
            for relative, content in files.items():
                target = workdir / relative
                if target.exists():
                    raise FileExistsError(f"{target} already exists; the task owns that path")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                if relative == SETUP_SCRIPT_NAME:
                    target.chmod(0o755)

    def apply_to_agent_config(self, config: AgentConfig) -> AgentConfig:
        """Add this condition's inputs to a Harbor built-in agent config."""
        expected = HARBOR_AGENT_NAMES[self.harness]
        if config.name != expected or config.import_path is not None:
            raise ValueError(
                f"components for {self.harness.value} apply to Harbor's {expected!r} agent, "
                f"not {config.name or config.import_path!r}"
            )
        kwargs = dict(config.kwargs)
        for key, value in (
            ("config", dict(self.native_config) if self.native_config else None),
            ("prompt_template_path", str(self.prompt_template) if self.prompt_template else None),
        ):
            if value is None:
                continue
            if key in kwargs:
                raise ValueError(f"agent kwarg {key!r} is already set; components own it")
            kwargs[key] = value
        update: dict[str, Any] = {
            "kwargs": kwargs,
            "skills": [*config.skills, *(str(path) for path in self.skills)],
            "mcp_servers": [*config.mcp_servers, *self.mcp_servers],
        }
        if self.agent_wrapper is not None:
            update["name"] = None
            update["import_path"] = self.agent_wrapper.import_path
            update["kwargs"] = {
                **dict(self.agent_wrapper.kwargs),
                "inner_agent": {"name": config.name, "kwargs": kwargs},
            }
        return config.model_copy(update=update)

    def probe_step(self, agent_dir: Path, step_index: int) -> ProbeReport:
        evidence = StepEvidence(agent_dir)
        reset = reset_probe(evidence)
        results = [ProbeResult(RESET_PROBE, "", reset.passed, reset.detail, reset.evidence)]
        for use in self.uses:
            try:
                outcome = use.definition.probe(use.context, evidence, step_index)
            except (ComponentConfigError, OSError, ValueError) as exc:
                outcome = ProbeOutcome(False, f"probe error: {exc}")
            results.append(
                ProbeResult(use.ref, use.slug, outcome.passed, outcome.detail, outcome.evidence)
            )
        return ProbeReport(step_index, tuple(results))
