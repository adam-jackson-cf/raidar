"""``workspace`` built-ins: rules-file, workspace-files, lint-config.

All three map to the task overlay: files are placed in the workspace through
the first step's ``workdir/`` (Harbor uploads it before that step's
``setup.sh``). Later steps meet whatever the agent left, so the overlay is
applied once (F3: anything written into the project persists).

Discovery probes read the sha256 of each overlay path that ``setup.sh``
recorded at step start. ``rules-file`` additionally requires harness evidence
that the rules text reached the agent's context.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath

from raidar.subject.agent.config import Harness
from raidar.subject.agent.harness import harness_definition
from raidar.subject.treatment.catalog._common import (
    BOTH_HARNESSES,
    COMMON_FINGERPRINT,
    missing_lines,
    read_file,
    require_file_count,
    require_with_keys,
    sha256_bytes,
    tree_files,
)
from raidar.subject.treatment.definition.contribution import Contribution
from raidar.subject.treatment.definition.evidence import StepEvidence
from raidar.subject.treatment.definition.manifest import (
    ComponentConfigError,
    ComponentKind,
    ComponentManifest,
    ComponentUseContext,
    DiscoveryProbe,
    ProbeOutcome,
)

_FINGERPRINT = (
    *COMMON_FINGERPRINT,
    "orchestrator/src/raidar/subject/treatment/catalog/workspace.py",
    "orchestrator/src/raidar/subject/agent/harness.py",  # rules_target: the harness's rules filename
)
LINT_CONFIG_NAMES = frozenset(
    {
        "ruff.toml",
        ".ruff.toml",
        "mypy.ini",
        ".mypy.ini",
        ".flake8",
        ".pylintrc",
        "pylintrc",
        ".editorconfig",
        ".pre-commit-config.yaml",
    }
)


def rules_target(harness: Harness) -> str:
    """The rules filename the harness reads (``subjects.agents.harness`` owns the mapping)."""
    filename = harness_definition(harness.value).rule_filename
    if filename is None:
        raise ComponentConfigError(f"{harness.value} declares no rules filename")
    return filename


def overlay_probe(
    overlay: Mapping[str, bytes], evidence: StepEvidence, step_index: int
) -> ProbeOutcome:
    """First step: every overlay path is in the workspace with the component's bytes."""
    if step_index > 0:
        return ProbeOutcome(True, "overlay applies at the first step; later content is the agent's")
    recorded = evidence.workspace_files
    problems = []
    for path, content in sorted(overlay.items()):
        if path not in recorded:
            problems.append(f"{path}: not recorded by setup.sh")
        elif recorded[path] != sha256_bytes(content):
            problems.append(f"{path}: {'absent' if recorded[path] is None else 'content differs'}")
    if problems:
        return ProbeOutcome(False, "; ".join(problems))
    return ProbeOutcome(True, f"{len(overlay)} overlay file(s) present at step start")


class RulesFile:
    """An agent rules file (AGENTS.md for Codex, CLAUDE.md for Claude Code)."""

    manifest = ComponentManifest(
        name="rules-file",
        version="1",
        kind=ComponentKind.WORKSPACE,
        description="Place one rules file at the workspace root under the harness's rules name",
        supports=BOTH_HARNESSES,
        fingerprint=_FINGERPRINT,
        discovery_probe=DiscoveryProbe(
            id="rules-file-loaded",
            description=(
                "The file is in the workspace at the first step with the component's bytes, "
                "and its text appears in the harness context whenever it is unchanged"
            ),
            evidence=(
                "raidar-components.json workspace_files",
                "codex-cli: rollout user/developer messages",
                "claude-code: session transcript (sessions/projects/**.jsonl)",
            ),
        ),
    )

    def _content(self, use: ComponentUseContext) -> bytes:
        require_with_keys(use, allowed=())
        require_file_count(use, minimum=1, maximum=1)
        return read_file(use, use.files[0])

    def contribute(self, use: ComponentUseContext) -> Contribution:
        return Contribution(overlay={rules_target(use.harness): self._content(use)})

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        target = rules_target(use.harness)
        content = self._content(use)
        placed = overlay_probe({target: content}, evidence, step_index)
        if not placed.passed:
            return placed
        current = evidence.workspace_files.get(target)
        if current != sha256_bytes(content):
            return ProbeOutcome(True, f"{target} was changed by the agent; context not checked")
        context = (
            evidence.codex_text("user", "developer")
            if use.harness is Harness.CODEX_CLI
            else evidence.claude_transcript_text
        )
        missing = missing_lines(content.decode("utf-8", errors="replace"), context)
        if not missing:
            return ProbeOutcome(True, f"every line of {target} found in the harness context")
        return ProbeOutcome(False, f"{target} lines not in the harness context: {missing[:3]}")


def _destination(use: ComponentUseContext) -> PurePosixPath:
    dest = use.with_.get("dest", "")
    if not isinstance(dest, str):
        raise ComponentConfigError("'with.dest' must be a relative path string")
    path = PurePosixPath(dest)
    if path.is_absolute() or ".." in path.parts:
        raise ComponentConfigError("'with.dest' must stay inside the workspace")
    return path


class WorkspaceFiles:
    """Starter files or directories copied into the workspace (optionally under ``dest``)."""

    manifest = ComponentManifest(
        name="workspace-files",
        version="1",
        kind=ComponentKind.WORKSPACE,
        description="Copy files or directories into the workspace at the first step",
        supports=BOTH_HARNESSES,
        fingerprint=_FINGERPRINT,
        discovery_probe=DiscoveryProbe(
            id="workspace-files-present",
            description="Every overlay file is in the workspace at the first step, byte-identical",
            evidence=("raidar-components.json workspace_files",),
        ),
    )

    def overlay(self, use: ComponentUseContext) -> dict[str, bytes]:
        require_with_keys(use, allowed=("dest",))
        require_file_count(use, minimum=1, maximum=None)
        dest = _destination(use)
        overlay: dict[str, bytes] = {}
        for relative in use.files:
            source = use.file_path(relative)
            name = PurePosixPath(relative).name
            if source.is_dir():
                for path in tree_files(source):
                    key = dest / name / path.relative_to(source).as_posix()
                    overlay[key.as_posix()] = path.read_bytes()
            else:
                overlay[(dest / name).as_posix()] = read_file(use, relative)
        return overlay

    def contribute(self, use: ComponentUseContext) -> Contribution:
        return Contribution(overlay=self.overlay(use))

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        return overlay_probe(self.overlay(use), evidence, step_index)


class LintConfig:
    """Lint/type-check configuration files placed at the workspace root."""

    manifest = ComponentManifest(
        name="lint-config",
        version="1",
        kind=ComponentKind.WORKSPACE,
        description="Place lint and type-check config files at the workspace root",
        supports=BOTH_HARNESSES,
        fingerprint=_FINGERPRINT,
        discovery_probe=DiscoveryProbe(
            id="lint-config-present",
            description="Every config file is at the workspace root at the first step",
            evidence=("raidar-components.json workspace_files",),
        ),
    )

    def overlay(self, use: ComponentUseContext) -> dict[str, bytes]:
        require_with_keys(use, allowed=())
        require_file_count(use, minimum=1, maximum=None)
        overlay: dict[str, bytes] = {}
        for relative in use.files:
            name = PurePosixPath(relative).name
            if name not in LINT_CONFIG_NAMES:
                raise ComponentConfigError(
                    f"{relative!r} is not a known lint config; allowed names: "
                    f"{sorted(LINT_CONFIG_NAMES)}"
                )
            overlay[name] = read_file(use, relative)
        return overlay

    def contribute(self, use: ComponentUseContext) -> Contribution:
        return Contribution(overlay=self.overlay(use))

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        return overlay_probe(self.overlay(use), evidence, step_index)


RULES_FILE = RulesFile()
WORKSPACE_FILES = WorkspaceFiles()
LINT_CONFIG = LintConfig()
