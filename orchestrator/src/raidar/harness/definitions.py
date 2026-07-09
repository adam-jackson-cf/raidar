"""Harness execution, artifact, parser, and usage definitions."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from raidar.schemas.environment import CapabilityRequirements


class HarnessDefinitionError(ValueError):
    """Raised when a harness is not registered."""


@dataclass(frozen=True, slots=True)
class UsagePolicy:
    """Token usage support for a harness."""

    supported: bool
    required: bool
    parser: str | None = None


@dataclass(frozen=True, slots=True)
class HarnessDefinition:
    """Canonical harness metadata consumed by runtime phases."""

    id: str
    npm_package: str | None
    npm_version_probe: tuple[str, ...] | None
    execution_requirements: CapabilityRequirements
    artifact_files: tuple[str, ...]
    final_workspace_archive: str
    rule_filename: str | None
    event_stream_pointer: str
    command_parser: str
    usage_policy: UsagePolicy
    emits_structured_trace_events: bool
    trace_parser: str | None
    additional_stdout_files: tuple[str, ...] = ()

    def npm_install_spec(self) -> str | None:
        """Return a concrete npm install spec for in-image harness execution."""

        if self.npm_package is None:
            return None
        if self.npm_version_probe is None:
            return self.npm_package
        version = _local_cli_version(self.npm_version_probe)
        return f"{self.npm_package}@{version}" if version else self.npm_package

    def dockerfile_install_fragment(self) -> str:
        """Return Dockerfile instructions needed for in-image harness execution."""

        install_spec = self.npm_install_spec()
        if install_spec is None:
            return ""
        return f"""RUN apt-get update && apt-get install -y --no-install-recommends \\
  npm \\
  && rm -rf /var/lib/apt/lists/*
RUN npm install -g {install_spec}
"""

    def cache_payload(self) -> dict[str, Any]:
        """Return stable harness material for runtime cache keys."""

        return {
            "id": self.id,
            "npm_package": self.npm_package,
            "npm_install_spec": self.npm_install_spec(),
            "dockerfile_install_fragment": self.dockerfile_install_fragment(),
            "execution_requirements": self.execution_requirements.model_dump(mode="json"),
            "artifact_files": list(self.artifact_files),
            "final_workspace_archive": self.final_workspace_archive,
            "rule_filename": self.rule_filename,
            "event_stream_pointer": self.event_stream_pointer,
            "command_parser": self.command_parser,
            "usage_policy": {
                "supported": self.usage_policy.supported,
                "required": self.usage_policy.required,
                "parser": self.usage_policy.parser,
            },
            "emits_structured_trace_events": self.emits_structured_trace_events,
            "trace_parser": self.trace_parser,
            "additional_stdout_files": list(self.additional_stdout_files),
        }


def _codex_version_probe() -> tuple[str, ...]:
    executable = os.environ.get("CODEX_CLI_PATH") or shutil.which("codex") or "codex"
    return (executable, "--version")


def _local_cli_version(command: tuple[str, ...]) -> str | None:
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    match = re.search(r"(\d+\.\d+\.\d+)", completed.stdout or "")
    return match.group(1) if match else None


_COMMON_COMMAND_ARTIFACTS = (
    "install.sh",
    "final-app.tar.gz",
)

_DEFINITIONS: dict[str, HarnessDefinition] = {
    "codex-cli": HarnessDefinition(
        id="codex-cli",
        npm_package="@openai/codex",
        npm_version_probe=_codex_version_probe(),
        execution_requirements=CapabilityRequirements(
            runtimes={"node": ">=20"},
            tools={"git": ">=2"},
        ),
        artifact_files=("trajectory.json", "codex.txt", *_COMMON_COMMAND_ARTIFACTS),
        final_workspace_archive="final-app.tar.gz",
        rule_filename="AGENTS.md",
        event_stream_pointer="codex.txt",
        command_parser="codex-jsonl",
        usage_policy=UsagePolicy(supported=True, required=True, parser="codex-jsonl"),
        emits_structured_trace_events=True,
        trace_parser="codex-jsonl",
    ),
    "claude-code": HarnessDefinition(
        id="claude-code",
        npm_package="@anthropic-ai/claude-code",
        npm_version_probe=None,
        execution_requirements=CapabilityRequirements(
            runtimes={"node": ">=20"},
            tools={"git": ">=2"},
        ),
        artifact_files=("claude-code.txt", *_COMMON_COMMAND_ARTIFACTS),
        final_workspace_archive="final-app.tar.gz",
        rule_filename="CLAUDE.md",
        event_stream_pointer="commands",
        command_parser="claude-stdout",
        usage_policy=UsagePolicy(supported=True, required=True, parser="claude-jsonl"),
        emits_structured_trace_events=False,
        trace_parser="claude",
    ),
    "gemini": HarnessDefinition(
        id="gemini",
        npm_package="@google/gemini-cli",
        npm_version_probe=None,
        execution_requirements=CapabilityRequirements(
            runtimes={"node": ">=20"},
            tools={"git": ">=2"},
        ),
        artifact_files=("gemini-cli.txt", "gemini-cli.trajectory.json", *_COMMON_COMMAND_ARTIFACTS),
        final_workspace_archive="final-app.tar.gz",
        rule_filename="GEMINI.md",
        event_stream_pointer="commands",
        command_parser="gemini",
        usage_policy=UsagePolicy(supported=True, required=True, parser="gemini-trajectory"),
        emits_structured_trace_events=False,
        trace_parser="gemini",
        additional_stdout_files=("gemini-cli.txt",),
    ),
    "cursor": HarnessDefinition(
        id="cursor",
        npm_package=None,
        npm_version_probe=None,
        execution_requirements=CapabilityRequirements(tools={"git": ">=2"}),
        artifact_files=_COMMON_COMMAND_ARTIFACTS,
        final_workspace_archive="final-app.tar.gz",
        rule_filename="user-rules-setting.md",
        event_stream_pointer="commands",
        command_parser="command-stdout",
        usage_policy=UsagePolicy(supported=False, required=False),
        emits_structured_trace_events=False,
        trace_parser="cursor",
    ),
    "copilot": HarnessDefinition(
        id="copilot",
        npm_package=None,
        npm_version_probe=None,
        execution_requirements=CapabilityRequirements(tools={"git": ">=2"}),
        artifact_files=_COMMON_COMMAND_ARTIFACTS,
        final_workspace_archive="final-app.tar.gz",
        rule_filename="copilot-instructions.md",
        event_stream_pointer="commands",
        command_parser="command-stdout",
        usage_policy=UsagePolicy(supported=False, required=False),
        emits_structured_trace_events=False,
        trace_parser="copilot",
    ),
    "pi": HarnessDefinition(
        id="pi",
        npm_package=None,
        npm_version_probe=None,
        execution_requirements=CapabilityRequirements(tools={"git": ">=2"}),
        artifact_files=_COMMON_COMMAND_ARTIFACTS,
        final_workspace_archive="final-app.tar.gz",
        rule_filename="AGENTS.md",
        event_stream_pointer="commands",
        command_parser="command-stdout",
        usage_policy=UsagePolicy(supported=False, required=False),
        emits_structured_trace_events=False,
        trace_parser="pi",
    ),
}


def harness_definition(harness_id: str) -> HarnessDefinition:
    """Return a registered harness definition."""

    try:
        return _DEFINITIONS[harness_id]
    except KeyError as exc:
        available = ", ".join(sorted(_DEFINITIONS))
        raise HarnessDefinitionError(
            f"Unknown harness {harness_id!r}; available harnesses: {available}"
        ) from exc


def harness_rule_filenames() -> tuple[str, ...]:
    """Return unique rule filenames declared by registered harnesses."""

    return tuple(
        sorted(
            {
                definition.rule_filename
                for definition in _DEFINITIONS.values()
                if definition.rule_filename is not None
            }
        )
    )
