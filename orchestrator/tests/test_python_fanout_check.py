from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


def _load_fanout_module():
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "checks" / "check-python-fanout.py"
    )
    spec = importlib.util.spec_from_file_location("check_python_fanout", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo_root, check=True)


def test_discovers_unignored_python_files_only(tmp_path: Path) -> None:
    fanout = _load_fanout_module()
    _git(tmp_path, "init")
    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (tmp_path / "kept").mkdir()
    (tmp_path / "kept" / "module.py").write_text("", encoding="utf-8")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "module.py").write_text("", encoding="utf-8")

    discovered = {
        path.relative_to(tmp_path).as_posix() for path in fanout.discover_python_files(tmp_path)
    }

    assert discovered == {"kept/module.py"}


def test_counts_submodule_imports_without_hiding_grouped_imports(tmp_path: Path) -> None:
    fanout = _load_fanout_module()
    _git(tmp_path, "init")
    package_root = tmp_path / "orchestrator" / "src" / "raidar"
    (package_root / "runtime").mkdir(parents=True)
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "runtime" / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "runtime" / "artifacts.py").write_text("", encoding="utf-8")
    (package_root / "runtime" / "workspace.py").write_text("", encoding="utf-8")
    current_path = package_root / "runtime" / "pipeline.py"
    current_path.write_text(
        "from raidar.runtime import artifacts, workspace\n",
        encoding="utf-8",
    )
    current = fanout.PythonFile(
        path=current_path,
        relpath="orchestrator/src/raidar/runtime/pipeline.py",
        module="raidar.runtime.pipeline",
        is_package=False,
    )
    known_modules = {
        "raidar",
        "raidar.runtime",
        "raidar.runtime.artifacts",
        "raidar.runtime.workspace",
        "raidar.runtime.pipeline",
    }

    imports = fanout.imported_modules(
        current_path.read_text(encoding="utf-8"),
        current,
        root_package="raidar",
        known_modules=known_modules,
    )

    assert imports == {"raidar.runtime.artifacts", "raidar.runtime.workspace"}


def test_architectural_dependencies_exclude_stdlib_but_keep_third_party(
    tmp_path: Path,
) -> None:
    fanout = _load_fanout_module()
    current_path = tmp_path / "orchestrator" / "src" / "raidar" / "module.py"
    current_path.parent.mkdir(parents=True)
    current_path.write_text(
        "import json\nfrom click.testing import CliRunner\n",
        encoding="utf-8",
    )
    current = fanout.PythonFile(
        path=current_path,
        relpath="orchestrator/src/raidar/module.py",
        module="raidar.module",
        is_package=False,
    )

    imports = fanout.imported_modules(
        current_path.read_text(encoding="utf-8"),
        current,
        root_package="raidar",
        known_modules={"raidar", "raidar.module"},
    )

    assert imports == {"click.testing", "json"}
    assert fanout.architectural_dependencies(imports) == {"click.testing"}


def test_source_nloc_ignores_blank_lines_and_comments() -> None:
    fanout = _load_fanout_module()

    assert fanout.source_nloc("\n# comment\nx = 1\n\n    # indented comment\n") == 1
