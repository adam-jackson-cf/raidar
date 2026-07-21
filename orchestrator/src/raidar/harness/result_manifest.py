"""Typed Harbor trial artifact layout owned by the harness boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from raidar.harness.definitions import HarnessDefinition, harness_definition


class HarborArtifactRole(StrEnum):
    """Stable roles for Harbor trial artifacts."""

    TRIAL_ROOT = "trial-root"
    TRIAL_RESULT = "trial-result"
    HARNESS_OUTPUT_ROOT = "harness-output-root"
    RUNNER_EVIDENCE_ROOT = "runner-evidence-root"
    RUNNER_EVIDENCE = "runner-evidence"
    RUNNER_ACTION_LOGS = "runner-action-logs"
    BASELINE_RUNNER_EVIDENCE = "baseline-runner-evidence"
    BASELINE_RUNNER_ACTION_LOGS = "baseline-runner-action-logs"
    HARNESS_EVENT_STREAM = "harness-event-stream"
    FINAL_WORKSPACE_ARCHIVE = "final-workspace-archive"


@dataclass(frozen=True, slots=True)
class HarborResultManifest:
    """Resolved paths for one Harbor trial and harness definition."""

    trial_dir: Path
    definition: HarnessDefinition

    @classmethod
    def for_trial(cls, trial_dir: Path, harness: str) -> HarborResultManifest:
        """Resolve a manifest for a known Harbor trial directory."""

        return cls(trial_dir=trial_dir, definition=harness_definition(harness))

    @staticmethod
    def looks_like_trial(path: Path) -> bool:
        """Return whether a directory contains Harbor harness output."""

        return HarborResultManifest.unbound_path(
            path,
            HarborArtifactRole.HARNESS_OUTPUT_ROOT,
        ).is_dir()

    @staticmethod
    def unbound_path(trial_dir: Path, role: HarborArtifactRole) -> Path:
        """Resolve a harness-independent Harbor trial role."""

        paths = {
            HarborArtifactRole.TRIAL_ROOT: trial_dir,
            HarborArtifactRole.TRIAL_RESULT: trial_dir / "result.json",
            HarborArtifactRole.HARNESS_OUTPUT_ROOT: trial_dir / "agent",
            HarborArtifactRole.RUNNER_EVIDENCE_ROOT: trial_dir / "verifier",
            HarborArtifactRole.RUNNER_EVIDENCE: trial_dir / "verifier" / "evidence.json",
            HarborArtifactRole.RUNNER_ACTION_LOGS: trial_dir / "verifier" / "action-logs",
            HarborArtifactRole.BASELINE_RUNNER_EVIDENCE: trial_dir
            / "verifier"
            / "baseline"
            / "evidence.json",
            HarborArtifactRole.BASELINE_RUNNER_ACTION_LOGS: trial_dir
            / "verifier"
            / "baseline"
            / "action-logs",
        }
        try:
            return paths[role]
        except KeyError as exc:
            raise ValueError(f"Artifact role {role.value!r} requires a harness definition") from exc

    @staticmethod
    def container_path(role: HarborArtifactRole, *, harness: str | None = None) -> Path:
        """Resolve a role inside Harbor's trial container."""

        paths = {
            HarborArtifactRole.HARNESS_OUTPUT_ROOT: Path("/logs/agent"),
            HarborArtifactRole.RUNNER_EVIDENCE_ROOT: Path("/logs/verifier"),
            HarborArtifactRole.RUNNER_EVIDENCE: Path("/logs/verifier/evidence.json"),
            HarborArtifactRole.RUNNER_ACTION_LOGS: Path("/logs/verifier/action-logs"),
            HarborArtifactRole.BASELINE_RUNNER_EVIDENCE: Path(
                "/logs/verifier/baseline/evidence.json"
            ),
            HarborArtifactRole.BASELINE_RUNNER_ACTION_LOGS: Path(
                "/logs/verifier/baseline/action-logs"
            ),
        }
        if role in paths:
            return paths[role]
        if harness is None:
            raise ValueError(f"Container artifact role {role.value!r} requires a harness")
        definition = harness_definition(harness)
        if role is HarborArtifactRole.HARNESS_EVENT_STREAM:
            return Path("/logs/agent") / definition.event_stream_pointer
        if role is HarborArtifactRole.FINAL_WORKSPACE_ARCHIVE:
            return Path("/logs/agent") / definition.final_workspace_archive
        raise ValueError(f"Artifact role {role.value!r} has no container path")

    def path(self, role: HarborArtifactRole) -> Path:
        """Return the path for a stable artifact role."""

        paths = {
            HarborArtifactRole.HARNESS_EVENT_STREAM: self.trial_dir
            / "agent"
            / self.definition.event_stream_pointer,
            HarborArtifactRole.FINAL_WORKSPACE_ARCHIVE: self.trial_dir
            / "agent"
            / self.definition.final_workspace_archive,
        }
        if role in paths:
            return paths[role]
        return self.unbound_path(self.trial_dir, role)

    def harness_artifact(self, filename: str) -> Path:
        """Return a declared harness artifact path."""

        if filename not in self.definition.artifact_files:
            raise ValueError(
                f"Harness {self.definition.id!r} does not declare artifact {filename!r}"
            )
        return self.path(HarborArtifactRole.HARNESS_OUTPUT_ROOT) / filename

    def runner_evidence_artifact(self, relative_path: str) -> Path:
        """Return a traversal-safe path beneath the raw runner evidence root."""

        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Invalid runner evidence artifact path: {relative_path!r}")
        return self.path(HarborArtifactRole.RUNNER_EVIDENCE_ROOT).joinpath(*relative.parts)

    def command_stdout_paths(self) -> tuple[Path, ...]:
        """Return Harbor command stdout artifacts in stable order."""

        root = self.path(HarborArtifactRole.HARNESS_OUTPUT_ROOT)
        return tuple(sorted(root.glob("command-*/stdout.txt")))

    def additional_stdout_paths(self) -> tuple[Path, ...]:
        """Return definition-declared top-level stdout artifacts."""

        root = self.path(HarborArtifactRole.HARNESS_OUTPUT_ROOT)
        return tuple(root / name for name in self.definition.additional_stdout_files)

    @property
    def setup_dir(self) -> Path:
        """Return the Harbor harness setup artifact directory."""

        return self.path(HarborArtifactRole.HARNESS_OUTPUT_ROOT) / "setup"

    def command_dirs(self) -> tuple[Path, ...]:
        """Return Harbor command artifact directories in stable order."""

        root = self.path(HarborArtifactRole.HARNESS_OUTPUT_ROOT)
        return tuple(sorted(path for path in root.glob("command-*") if path.is_dir()))
