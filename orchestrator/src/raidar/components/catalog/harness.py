"""``harness`` built-ins: skills, harness-settings, stop-hook, mcp-server (C2).

Mapping onto Harbor 0.23 inputs for its installed ``codex`` / ``claude-code``
agents:

- ``skills`` -> ``AgentConfig.skills`` (``--skill``); Harbor uploads each skill
  directory and the agent copies it into the harness skills dir.
- ``harness-settings`` and ``stop-hook`` -> the agent's ``config`` kwarg
  (``--ak config``): Codex ``config.toml`` keys / Claude ``--settings`` JSON.
  The stop hook runs through a wrapper that logs each invocation.
- ``mcp-server`` -> ``AgentConfig.mcp_servers`` (stdio), launched through the
  recording shim (``raidar.components.runtime.mcp_shim``).
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable, Mapping
from importlib.resources import files
from pathlib import PurePosixPath
from typing import Any

from harbor.models.task.config import MCPServerConfig
from pydantic import JsonValue

from raidar.agents.config import Harness
from raidar.components.catalog._common import (
    BOTH_HARNESSES,
    COMMON_FINGERPRINT,
    read_file,
    require_file_count,
    require_with_keys,
)
from raidar.components.definition.contribution import (
    Contribution,
    SupportFile,
    component_dir,
    evidence_log,
)
from raidar.components.definition.evidence import StepEvidence
from raidar.components.definition.manifest import (
    ComponentConfigError,
    ComponentKind,
    ComponentManifest,
    ComponentUseContext,
    DiscoveryProbe,
    ProbeOutcome,
)

_FINGERPRINT = (*COMMON_FINGERPRINT, "raidar/components/catalog/harness.py")
SKILL_FILE = "SKILL.md"
_FRONTMATTER_NAME = re.compile(r"^name:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", re.MULTILINE)


# ---- skills ------------------------------------------------------------------


def skill_name(skill_md: str, fallback: str) -> str:
    """The ``name`` from SKILL.md front matter (the directory name when absent)."""
    if skill_md.startswith("---"):
        front = skill_md.split("---", 2)[1] if skill_md.count("---") >= 2 else ""
        match = _FRONTMATTER_NAME.search(front)
        if match:
            return match.group(1).strip()
    return fallback


def _claude_listed_skills(evidence: StepEvidence) -> set[str]:
    """Skill and slash-command names from Claude Code's stream-json init events."""
    listed: set[str] = set()
    for event in evidence.claude_init_events:
        for key in ("skills", "slash_commands"):
            values = event.get(key)
            if isinstance(values, list):
                listed.update(str(value).lstrip("/") for value in values)
    return listed


class Skills:
    manifest = ComponentManifest(
        name="skills",
        version="1",
        kind=ComponentKind.HARNESS,
        description="Inject skill directories (each with SKILL.md) through Harbor --skill",
        supports=BOTH_HARNESSES,
        fingerprint=_FINGERPRINT,
        discovery_probe=DiscoveryProbe(
            id="skills-listed",
            description="Each skill name is in the harness's list of available skills",
            evidence=(
                "codex-cli: rollout developer <skills_instructions>",
                "claude-code: stream-json system/init skills or slash_commands",
            ),
        ),
    )

    def names(self, use: ComponentUseContext) -> list[str]:
        require_with_keys(use, allowed=())
        require_file_count(use, minimum=1, maximum=None)
        names = []
        for relative in use.files:
            skill_md = use.file_path(relative) / SKILL_FILE
            if not skill_md.is_file():
                raise ComponentConfigError(f"{relative!r} must be a directory with {SKILL_FILE}")
            names.append(skill_name(skill_md.read_text(encoding="utf-8"), skill_md.parent.name))
        return names

    def contribute(self, use: ComponentUseContext) -> Contribution:
        self.names(use)
        return Contribution(skills=tuple(use.file_path(relative) for relative in use.files))

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        del step_index
        if use.harness is Harness.CODEX_CLI:
            listing = evidence.codex_text("developer")
            missing = [name for name in self.names(use) if f"- {name}:" not in listing]
        else:
            listed = _claude_listed_skills(evidence)
            missing = [name for name in self.names(use) if name not in listed]
        if missing:
            return ProbeOutcome(False, f"skills not listed by the harness: {missing}")
        return ProbeOutcome(True, "all skills listed by the harness", tuple(self.names(use)))


# ---- harness-settings ----------------------------------------------------------

Observer = Callable[[StepEvidence], list[Any]]


def _codex_turn(key: str) -> Observer:
    return lambda evidence: [context.get(key) for context in evidence.codex_turn_contexts]


def _codex_developer_text(evidence: StepEvidence) -> list[Any]:
    return [text for role, text in evidence.codex_messages if role == "developer"]


def _claude_init(key: str) -> Observer:
    return lambda evidence: [event.get(key) for event in evidence.claude_init_events]


# Settings a harness reports back at runtime, keyed by the dotted settings path.
# A setting outside this table cannot be proven to take effect, so it is refused.
OBSERVABLE_SETTINGS: Mapping[Harness, Mapping[str, Observer]] = {
    Harness.CODEX_CLI: {
        "model_reasoning_summary": _codex_turn("summary"),
        "developer_instructions": _codex_developer_text,
    },
    Harness.CLAUDE_CODE: {
        "outputStyle": _claude_init("output_style"),
    },
}
# Settings Harbor 0.23's built-in agents always override with CLI flags, so a
# config value can never take effect: refused at compile time, not left to
# fail the probe on every trajectory.
HARBOR_FORCED_SETTINGS: Mapping[Harness, Mapping[str, str]] = {
    Harness.CODEX_CLI: {
        "approval_policy": "codex exec --dangerously-bypass-approvals-and-sandbox",
        "sandbox_mode": "codex exec --dangerously-bypass-approvals-and-sandbox",
    },
    Harness.CLAUDE_CODE: {
        "permissions.defaultMode": "claude --permission-mode=bypassPermissions",
    },
}
# Agent nouns (model, effort) belong to the AgentSpec and the factor guard;
# hooks and MCP servers have their own components.
RESERVED_SETTINGS = frozenset(
    {
        "model",
        "model_reasoning_effort",
        "effortLevel",
        "notify",
        "hooks",
        "mcp_servers",
        "mcpServers",
    }
)


def _flatten(settings: Mapping[str, JsonValue], prefix: str = "") -> dict[str, JsonValue]:
    flat: dict[str, JsonValue] = {}
    for key, value in settings.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, dotted))
        else:
            flat[dotted] = value
    return flat


class HarnessSettings:
    manifest = ComponentManifest(
        name="harness-settings",
        version="1",
        kind=ComponentKind.HARNESS,
        description="Harness-native settings passed as the agent's config (--ak config)",
        supports=BOTH_HARNESSES,
        fingerprint=_FINGERPRINT,
        discovery_probe=DiscoveryProbe(
            id="harness-settings-effective",
            description="Every declared setting is reported back by the harness at runtime",
            evidence=(
                "codex-cli: rollout turn_context / developer messages",
                "claude-code: stream-json system/init",
            ),
        ),
    )

    def settings(self, use: ComponentUseContext) -> dict[str, JsonValue]:
        require_with_keys(use, allowed=("settings",), required=("settings",))
        require_file_count(use, minimum=0, maximum=0)
        settings = use.with_["settings"]
        if not isinstance(settings, dict) or not settings:
            raise ComponentConfigError("'with.settings' must be a non-empty object")
        reserved = sorted(RESERVED_SETTINGS.intersection(settings))
        if reserved:
            raise ComponentConfigError(
                f"settings {reserved} are not harness-settings (agent nouns or other components)"
            )
        flat = _flatten(settings)
        forced = HARBOR_FORCED_SETTINGS[use.harness]
        overridden = sorted(forced.keys() & flat.keys())
        if overridden:
            flags = sorted({forced[key] for key in overridden})
            raise ComponentConfigError(
                f"settings {overridden} are always overridden on {use.harness.value} by "
                f"Harbor's {flags}; they cannot take effect"
            )
        observable = OBSERVABLE_SETTINGS[use.harness]
        unobservable = sorted(set(flat) - set(observable))
        if unobservable:
            raise ComponentConfigError(
                f"settings {unobservable} have no discovery probe on {use.harness.value}; "
                f"observable: {sorted(observable)}"
            )
        return settings

    def contribute(self, use: ComponentUseContext) -> Contribution:
        return Contribution(native_config=self.settings(use))

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        del step_index
        observable = OBSERVABLE_SETTINGS[use.harness]
        problems = []
        for key, expected in sorted(_flatten(self.settings(use)).items()):
            observed = observable[key](evidence)
            if key == "developer_instructions":
                ok = any(isinstance(text, str) and str(expected) in text for text in observed)
            else:
                ok = bool(observed) and all(value == expected for value in observed)
            if not ok:
                problems.append(f"{key}: expected {expected!r}, observed {observed[:3]!r}")
        if problems:
            return ProbeOutcome(False, "; ".join(problems))
        return ProbeOutcome(True, "all declared settings reported by the harness")


# ---- stop-hook -------------------------------------------------------------------


def _hook_wrapper(slug: str, harness: Harness) -> bytes:
    """Log the invocation, run the body as a child, then log its exit status."""
    log = shlex.quote(str(evidence_log(slug, "hook")))
    body = component_dir(slug) / "hook-body.sh"
    return (
        "#!/usr/bin/env bash\n"
        "# Raidar stop-hook wrapper: record the invocation, run the hook body, record its exit.\n"
        f"mkdir -p {shlex.quote(str(evidence_log(slug, 'hook').parent))}\n"
        f'printf \'{{"event":"stop","harness":"{harness.value}","argc":%d,"ts":%s}}\\n\' '
        f'"$#" "$(date +%s)" >> {log}\n'
        f'bash {shlex.quote(str(body))} "$@"\n'
        "status=$?\n"
        f'printf \'{{"event":"exit","status":%d,"ts":%s}}\\n\' "$status" "$(date +%s)" >> {log}\n'
        'exit "$status"\n'
    ).encode()


class StopHook:
    manifest = ComponentManifest(
        name="stop-hook",
        version="1",
        kind=ComponentKind.HARNESS,
        description=(
            "Run a command when the agent finishes a turn (Codex notify, Claude Stop hook)"
        ),
        supports=BOTH_HARNESSES,
        fingerprint=_FINGERPRINT,
        discovery_probe=DiscoveryProbe(
            id="stop-hook-fired",
            description="The hook wrapper logged at least one invocation in the step",
            evidence=("raidar-components/<slug>.hook.jsonl",),
        ),
    )

    def body(self, use: ComponentUseContext) -> bytes:
        require_with_keys(use, allowed=("command",))
        command = use.with_.get("command")
        if (command is None) == (len(use.files) == 0):
            raise ComponentConfigError("set exactly one of 'with.command' or one file")
        if command is not None:
            if not isinstance(command, str) or not command.strip():
                raise ComponentConfigError("'with.command' must be a non-empty string")
            return f"#!/usr/bin/env bash\n{command}\n".encode()
        require_file_count(use, minimum=1, maximum=1)
        return read_file(use, use.files[0])

    def contribute(self, use: ComponentUseContext) -> Contribution:
        wrapper = component_dir(use.slug) / "stop-hook.sh"
        native: dict[str, JsonValue]
        if use.harness is Harness.CODEX_CLI:
            native = {"notify": ["bash", str(wrapper)]}
        else:
            hook: dict[str, JsonValue] = {
                "type": "command",
                "command": f"bash {shlex.quote(str(wrapper))}",
            }
            native = {"hooks": {"Stop": [{"hooks": [hook]}]}}
        return Contribution(
            support_files={
                "stop-hook.sh": SupportFile(_hook_wrapper(use.slug, use.harness), executable=True),
                "hook-body.sh": SupportFile(self.body(use), executable=True),
            },
            native_config=native,
        )

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        del step_index
        records = evidence.component_log(use.slug, "hook")
        fired = [record for record in records if record.get("event", "stop") == "stop"]
        if not fired:
            return ProbeOutcome(False, "stop hook never ran in this step")
        statuses = [record.get("status") for record in records if record.get("event") == "exit"]
        if statuses and 0 not in statuses:
            # A body that can never succeed (e.g. a tool absent from the agent image,
            # exit 127) is a turn-end check that never ran. Wrap it in `|| true` when
            # failing is intended.
            return ProbeOutcome(
                False, f"stop hook ran {len(fired)} time(s) but its body never exited 0 {statuses}"
            )
        recorded = f"; exits {statuses}" if statuses else "; exit status not recorded"
        return ProbeOutcome(True, f"stop hook ran {len(fired)} time(s){recorded}")


# ---- mcp-server --------------------------------------------------------------------

_COMPONENT_DIR_TOKEN = "{component_dir}"


def mcp_shim_source() -> bytes:
    return files("raidar.components.runtime").joinpath("mcp_shim.py").read_bytes()


class McpServer:
    manifest = ComponentManifest(
        name="mcp-server",
        version="1",
        kind=ComponentKind.HARNESS,
        description="A stdio MCP server registered through Harbor mcp_servers",
        supports=BOTH_HARNESSES,
        fingerprint=(*_FINGERPRINT, "raidar/components/runtime/mcp_shim.py"),
        discovery_probe=DiscoveryProbe(
            id="mcp-server-connected",
            description="The harness initialised the server and listed its tools",
            evidence=("raidar-components/<slug>.mcp.jsonl (recording shim)",),
        ),
    )

    def server(self, use: ComponentUseContext) -> tuple[str, str, list[str]]:
        require_with_keys(use, allowed=("name", "command", "args"), required=("name", "command"))
        name, command, args = use.with_["name"], use.with_["command"], use.with_.get("args", [])
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ComponentConfigError("'with.name' must match [A-Za-z0-9_-]+")
        if not isinstance(command, str) or not command:
            raise ComponentConfigError("'with.command' must be a non-empty string")
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ComponentConfigError("'with.args' must be a list of strings")
        base = str(component_dir(use.slug) / "files")
        expand = [str(arg).replace(_COMPONENT_DIR_TOKEN, base) for arg in args]
        return name, command.replace(_COMPONENT_DIR_TOKEN, base), expand

    def contribute(self, use: ComponentUseContext) -> Contribution:
        name, command, args = self.server(use)
        support = {"mcp_shim.py": SupportFile(mcp_shim_source())}
        for relative in use.files:
            file_name = PurePosixPath(relative).name
            support[f"files/{file_name}"] = SupportFile(read_file(use, relative), executable=True)
        shim = component_dir(use.slug) / "mcp_shim.py"
        log = evidence_log(use.slug, "mcp")
        server = MCPServerConfig(
            name=name,
            transport="stdio",
            command="python3",
            args=[str(shim), "--log", str(log), "--", command, *args],
        )
        return Contribution(support_files=support, mcp_servers=(server,))

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        del step_index
        records = evidence.component_log(use.slug, "mcp")
        initialised = any(record.get("method") == "initialize" for record in records)
        if not initialised:
            return ProbeOutcome(False, "the harness never sent initialize to the MCP server")
        if not any(record.get("result_of") == "tools/list" for record in records):
            return ProbeOutcome(False, "the harness never listed the MCP server's tools")
        if not _answered(records, "initialize"):
            return ProbeOutcome(False, "the MCP server never answered initialize without an error")
        listings = _answered(records, "tools/list")
        tools = [str(tool) for record in listings for tool in record.get("tools", [])]
        if not tools:
            return ProbeOutcome(False, "the MCP server listed no tools (or only errors)")
        return ProbeOutcome(True, "MCP server initialised and tools listed", tuple(tools))


def _answered(records: list[dict[str, Any]], method: str) -> list[dict[str, Any]]:
    """The server's non-error responses to ``method``."""
    return [
        record
        for record in records
        if record.get("result_of") == method and not record.get("error")
    ]


SKILLS = Skills()
HARNESS_SETTINGS = HarnessSettings()
STOP_HOOK = StopHook()
MCP_SERVER = McpServer()
