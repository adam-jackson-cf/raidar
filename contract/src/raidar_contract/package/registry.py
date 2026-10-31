"""Locating and loading stage packages: pure path and data work, no primitive imports.

Release-register resolution (``name@N`` against ``lab/releases.yaml``) is layered on top of
``stage_folder`` and ``load_stage_package``; this module never executes or imports package code.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from raidar_contract.canonical import IGNORED_TREE_NAMES
from raidar_contract.package import StagePackageError
from raidar_contract.package.digest import (
    FIXTURES_DIR,
    StageFile,
    read_stage_tree,
    stage_tree_digest,
)
from raidar_contract.package.manifest import (
    NAME_RE,
    StageManifest,
    StagePackage,
    parse_manifest,
)
from raidar_contract.releases import Release

LAB_DIR = "lab"
STAGES_DIR = "stages"
MANIFEST_FILE = "stage.yaml"
PROMPT_FILE = "prompt.md"
INSTRUCTIONS_FILE = "instructions.md"
SKILLS_DIR = "skills"
SKILL_FILE = "SKILL.md"
TOOL_DIR = "tool"
PYPROJECT = f"{TOOL_DIR}/pyproject.toml"
FORBIDDEN_IMPORT = "raidar"
_DYNAMIC_IMPORTS = frozenset({"__import__", "import_module"})
_TOP_FILES = frozenset({MANIFEST_FILE, PROMPT_FILE, INSTRUCTIONS_FILE})
_TOP_DIRS = frozenset({SKILLS_DIR, TOOL_DIR})


def stage_folder(release: Release, repo_root: Path) -> Path:
    """The folder ``lab/stages/<ns>/<name>`` a release reference names below ``repo_root``."""
    return repo_root / LAB_DIR / STAGES_DIR / release.name


def _text(files: dict[str, StageFile], path: str) -> str:
    try:
        return files[path].data.decode("utf-8")
    except KeyError:
        raise StagePackageError(f"missing required file {path}") from None
    except UnicodeDecodeError as error:
        raise StagePackageError(f"{path}: not UTF-8 text") from error


def _check_skills(manifest: StageManifest, files: dict[str, StageFile]) -> None:
    for skill in manifest.skills:
        if f"{SKILLS_DIR}/{skill}/{SKILL_FILE}" not in files:
            raise StagePackageError(f"skill {skill!r} has no {SKILL_FILE}")


def _check_pyproject(text: str) -> None:
    try:
        project = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise StagePackageError(f"{PYPROJECT}: {error}") from error
    if "build-system" in project:
        raise StagePackageError(f"{PYPROJECT}: a tool project is dependency-only (no build-system)")
    if project.get("tool", {}).get("uv", {}).get("package"):
        raise StagePackageError(f"{PYPROJECT}: a tool project is not a built package")
    dependencies = project.get("project", {}).get("dependencies", [])
    if any("@" in item or "://" in item for item in dependencies):
        raise StagePackageError(f"{PYPROJECT}: direct references are refused")


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and _dynamic_target(node):
            names.add(_dynamic_target(node) or "")
    return names


def _dynamic_target(call: ast.Call) -> str | None:
    function = call.func
    name = function.id if isinstance(function, ast.Name) else getattr(function, "attr", None)
    if name not in _DYNAMIC_IMPORTS or not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value.split(".")[0]
    return None


def _check_tool_sources(files: dict[str, StageFile]) -> None:
    for path, item in files.items():
        if not (path.startswith(f"{TOOL_DIR}/") and path.endswith(".py")):
            continue
        try:
            tree = ast.parse(item.data, filename=path)
        except (SyntaxError, ValueError) as error:
            raise StagePackageError(f"{path}: not valid Python: {error}") from error
        if FORBIDDEN_IMPORT in _imported_names(tree):
            raise StagePackageError(f"{path}: authored code never imports {FORBIDDEN_IMPORT}")


def _check_tool(manifest: StageManifest, files: dict[str, StageFile]) -> None:
    owned = [path for path in files if path.startswith(f"{TOOL_DIR}/")]
    if manifest.tool is None:
        if owned:
            raise StagePackageError(f"{owned[0]}: files under {TOOL_DIR}/ need a manifest tool")
        return
    _check_pyproject(_text(files, PYPROJECT))
    _check_tool_sources(files)


def _check_location(root: Path) -> None:
    """Refuse a symlinked package root or ancestor below ``stages``, and a path that is not
    exactly ``stages/<namespace>/<name>`` with valid components."""
    if root.is_symlink():
        raise StagePackageError(f"{root}: a package root is never a symlink")
    if STAGES_DIR not in root.parts:
        return
    index = len(root.parts) - 1 - root.parts[::-1].index(STAGES_DIR)
    below = root.parts[index + 1 :]
    if len(below) != 2 or not all(NAME_RE.fullmatch(part) for part in below):
        raise StagePackageError(f"{root}: expected {STAGES_DIR}/<namespace>/<name>")
    for ancestor in root.parents:
        if ancestor.is_symlink():
            raise StagePackageError(f"{ancestor}: symlinked ancestors are refused")
        if ancestor.name == STAGES_DIR:
            break


def _check_shape(root: Path) -> None:
    for entry in root.iterdir():
        if entry.name in IGNORED_TREE_NAMES:
            continue
        allowed = _TOP_DIRS | {FIXTURES_DIR} if entry.is_dir() else _TOP_FILES
        if entry.name not in allowed:
            raise StagePackageError(f"{entry.name}: not part of the stage package layout")


def load_stage_package(root: Path) -> StagePackage:
    """The validated package at ``root`` (folder leaf equals the manifest name)."""
    _check_location(root)
    all_files = read_stage_tree(root)
    files = {item.path: item for item in all_files}
    manifest = parse_manifest(_text(files, MANIFEST_FILE))
    if manifest.name != root.name:
        raise StagePackageError(
            f"folder {root.name!r} does not match manifest name {manifest.name!r}"
        )
    prompt = _text(files, PROMPT_FILE)
    instructions = _text(files, INSTRUCTIONS_FILE) if INSTRUCTIONS_FILE in files else None
    _check_shape(root)
    _check_skills(manifest, files)
    _check_tool(manifest, files)
    return StagePackage(
        root, manifest, stage_tree_digest(all_files), prompt, instructions, all_files
    )
