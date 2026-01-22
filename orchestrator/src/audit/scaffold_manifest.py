"""Base project auditing and manifest generation."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field


class FileEntry(BaseModel):
    """File entry in the scaffold manifest."""

    hash: str = Field(description="SHA256 hash of file contents")
    size: int = Field(description="File size in bytes")


class QualityGates(BaseModel):
    """Quality gate commands."""

    typecheck: str = Field(default="bun run typecheck")
    lint: str = Field(default="bunx ultracite check src")
    test: str = Field(default="bun test")


class ScaffoldManifest(BaseModel):
    """Manifest capturing baseline state of scaffold."""

    generated_at: str = Field(description="ISO timestamp of manifest generation")
    version: str = Field(default="1.0.0")
    files: dict[str, FileEntry] = Field(default_factory=dict)
    dependencies: dict[str, str] = Field(default_factory=dict)
    dev_dependencies: dict[str, str] = Field(default_factory=dict)
    quality_gates: QualityGates = Field(default_factory=QualityGates)
    pre_commit_hooks: list[str] = Field(default_factory=list)


def compute_file_hash(path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return f"sha256:{sha256.hexdigest()}"


def generate_manifest(scaffold_dir: Path) -> ScaffoldManifest:
    """Generate a manifest for a scaffold directory.

    Args:
        scaffold_dir: Path to the scaffold directory

    Returns:
        ScaffoldManifest with baseline state
    """
    files: dict[str, FileEntry] = {}

    # Track key configuration files
    key_files = [
        "package.json",
        "tsconfig.json",
        "next.config.ts",
        "postcss.config.mjs",
    ]

    for filename in key_files:
        file_path = scaffold_dir / filename
        if file_path.exists():
            files[filename] = FileEntry(
                hash=compute_file_hash(file_path),
                size=file_path.stat().st_size,
            )

    # Track all TypeScript/TSX files in src
    src_dir = scaffold_dir / "src"
    if src_dir.exists():
        for ts_file in src_dir.rglob("*.ts"):
            rel_path = ts_file.relative_to(scaffold_dir)
            files[str(rel_path)] = FileEntry(
                hash=compute_file_hash(ts_file),
                size=ts_file.stat().st_size,
            )
        for tsx_file in src_dir.rglob("*.tsx"):
            rel_path = tsx_file.relative_to(scaffold_dir)
            files[str(rel_path)] = FileEntry(
                hash=compute_file_hash(tsx_file),
                size=tsx_file.stat().st_size,
            )

    # Extract dependencies from package.json
    dependencies: dict[str, str] = {}
    dev_dependencies: dict[str, str] = {}
    package_json_path = scaffold_dir / "package.json"

    if package_json_path.exists():
        with open(package_json_path) as f:
            pkg = json.load(f)
            dependencies = pkg.get("dependencies", {})
            dev_dependencies = pkg.get("devDependencies", {})

    return ScaffoldManifest(
        generated_at=datetime.now(UTC).isoformat(),
        files=files,
        dependencies=dependencies,
        dev_dependencies=dev_dependencies,
        quality_gates=QualityGates(),
        pre_commit_hooks=["typecheck", "lint"],
    )


def save_manifest(manifest: ScaffoldManifest, output_path: Path) -> None:
    """Save manifest to a JSON file."""
    with open(output_path, "w") as f:
        f.write(manifest.model_dump_json(indent=2))


def load_manifest(path: Path) -> ScaffoldManifest:
    """Load manifest from a JSON file."""
    with open(path) as f:
        return ScaffoldManifest.model_validate_json(f.read())


def diff_manifests(
    baseline: ScaffoldManifest, current: ScaffoldManifest
) -> dict[str, list[str]]:
    """Compare two manifests and return differences.

    Returns:
        Dict with 'added', 'removed', 'modified' lists of file paths
    """
    baseline_files = set(baseline.files.keys())
    current_files = set(current.files.keys())

    added = list(current_files - baseline_files)
    removed = list(baseline_files - current_files)
    modified = [
        f
        for f in baseline_files & current_files
        if baseline.files[f].hash != current.files[f].hash
    ]

    return {
        "added": sorted(added),
        "removed": sorted(removed),
        "modified": sorted(modified),
    }
