"""Stage packages: strict manifest, tree digest, safe loading and typed param overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from raidar.subject.package import StagePackageError
from raidar.subject.package.registry import load_stage_package
from tests.package_support import fixture_package, write_package


def _note_state(tmp_path: Path) -> Path:
    return fixture_package("note-state", tmp_path)


def test_same_bytes_same_digest_and_location_is_not_identity(tmp_path: Path) -> None:
    first = load_stage_package(fixture_package("note-state", tmp_path / "a"))
    second = load_stage_package(fixture_package("note-state", tmp_path / "b"))
    assert first.digest == second.digest


def test_byte_exec_and_ignored_changes(tmp_path: Path) -> None:
    root = _note_state(tmp_path)
    base = load_stage_package(root).digest
    (root / "fixtures" / "done" / "state" / "note.md").write_text("changed fixture\n")
    (root / ".DS_Store").write_bytes(b"x")
    (root / "tool" / "src" / "__pycache__").mkdir()
    (root / "tool" / "src" / "__pycache__" / "x.pyc").write_bytes(b"x")
    assert load_stage_package(root).digest == base
    path = root / "instructions.md"
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    changed = load_stage_package(root).digest
    assert changed != base
    path.write_bytes(original)
    path.chmod(0o755)
    assert load_stage_package(root).digest not in {base, changed}


def test_fixture_tree_symlinks_and_specials_are_refused(tmp_path: Path) -> None:
    root = _note_state(tmp_path)
    (root / "fixtures" / "done" / "state" / "link").symlink_to("/etc/passwd")
    with pytest.raises(StagePackageError, match="symlink"):
        load_stage_package(root)


def test_special_files_are_refused(tmp_path: Path) -> None:
    root = _note_state(tmp_path)
    os.mkfifo(root / "tool" / "pipe")
    with pytest.raises(StagePackageError, match="special"):
        load_stage_package(root)


def test_symlinked_package_and_fixtures_are_refused(tmp_path: Path) -> None:
    real = _note_state(tmp_path / "real")
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(StagePackageError):
        load_stage_package(link)
    (real / "fixtures").rename(tmp_path / "moved")
    (real / "fixtures").symlink_to(tmp_path / "moved")
    with pytest.raises(StagePackageError, match="symlink"):
        load_stage_package(real)


def test_authored_code_is_never_imported_by_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "authored-import"
    monkeypatch.setenv("RAIDAR_IMPORT_MARKER", str(marker))
    load_stage_package(fixture_package("marker-tool", tmp_path))
    assert not marker.exists()


@pytest.mark.parametrize(
    "source",
    [
        "import raidar\n",
        "import raidar.contract.canonical as c\n",
        "from raidar.subject import x\n",
        "__import__('raidar')\n",
        "import importlib\nimportlib.import_module('raidar.x')\n",
    ],
)
def test_authored_raidar_imports_are_refused(tmp_path: Path, source: str) -> None:
    root = _note_state(tmp_path)
    (root / "tool" / "src" / "note_state" / "evil.py").write_text(source)
    with pytest.raises(StagePackageError, match="raidar"):
        load_stage_package(root)


def test_authored_syntax_error_is_refused(tmp_path: Path) -> None:
    root = _note_state(tmp_path)
    (root / "tool" / "src" / "note_state" / "bad.py").write_text("def (\n")
    with pytest.raises(StagePackageError, match="Python"):
        load_stage_package(root)


def _tool_manifest() -> dict[str, Any]:
    return {"tool": {"project": "tool", "commands": {"x": ["x"]}}}


@pytest.mark.parametrize(
    "pyproject",
    [
        '[project]\nname="x"\nversion="0"\n[build-system]\nrequires=["hatchling"]\n',
        '[project]\nname="x"\nversion="0"\n[tool.uv]\npackage=true\n',
        '[project]\nname="x"\nversion="0"\ndependencies=["a @ https://h/a.whl"]\n',
        "not = [toml",
    ],
)
def test_tool_project_must_be_dependency_only(tmp_path: Path, pyproject: str) -> None:
    root = write_package(tmp_path, _tool_manifest(), {"tool/pyproject.toml": pyproject})
    with pytest.raises(StagePackageError):
        load_stage_package(root)


def test_tool_files_need_manifest_tool_and_pyproject(tmp_path: Path) -> None:
    with pytest.raises(StagePackageError, match="manifest tool"):
        load_stage_package(write_package(tmp_path / "a", None, {"tool/x.py": "x = 1\n"}))
    with pytest.raises(StagePackageError, match="pyproject"):
        load_stage_package(write_package(tmp_path / "b", _tool_manifest()))


def test_required_files_name_and_skills(tmp_path: Path) -> None:
    with pytest.raises(StagePackageError, match="prompt.md"):
        load_stage_package(write_package(tmp_path / "a", prompt=None))
    renamed = write_package(tmp_path / "b")
    renamed = renamed.rename(renamed.with_name("other"))
    with pytest.raises(StagePackageError, match="match"):
        load_stage_package(renamed)
    with pytest.raises(StagePackageError, match="SKILL.md"):
        load_stage_package(write_package(tmp_path / "c", {"skills": ["lint"]}))
    (tmp_path / "d").mkdir()
    with pytest.raises(StagePackageError, match="stage.yaml"):
        load_stage_package(tmp_path / "d")
    with pytest.raises(StagePackageError, match="UTF-8"):
        load_stage_package(write_package(tmp_path / "e", prompt=None, files={"prompt.md": b"\xff"}))


@pytest.mark.parametrize(
    "extra",
    [
        {"surprise": 1},
        {"contract": "stage/2"},
        {"name": "Bad_Name"},
        {"summary": ""},
        {"requires": {"setup": "setup"}},
        {"setup": {"run": ["x"]}},
        {"requires": {"s": "setup/1"}, "setup": {"run": []}},
        {"requires": {"s": "setup/1"}, "setup": {"run": ["x"], "timeout_s": 301}},
        {"requires": {"s": "setup/1"}, "setup": {"run": ["x"], "timeout_s": "60"}},
        {"requires": {"s": "setup/1"}, "setup": {"run": ["x"], "extra": 1}},
        {"requires": {"g": "gate/1"}, "gate": [{"name": "a"}]},
        {"requires": {"g": "gate/1"}, "gate": [{"name": "a", "run": ["x"], "kind": "lint"}]},
        {"requires": {"g": "gate/1"}, "gate": [{"name": "a", "run": ["x"]}] * 2},
        {"requires": {"g": "gate/1"}, "gate": [{"name": "a", "run": ["x"], "watch": "/tmp/a"}]},
        {"requires": {"s": "stop/1"}, "on_stop": {"run": ["x"], "max_rounds": 6}},
        {"requires": {"o": "outputs/1"}, "outputs": {"n": {"path": "/app/solution.py"}}},
        {"requires": {"o": "outputs/1"}, "outputs": {"n": {"path": "/app/.raidar/../x"}}},
        {"requires": {"o": "outputs/1"}, "outputs": {"n": {"path": "/app/.raidar/x", "k": 1}}},
        {
            "requires": {"o": "outputs/1"},
            "outputs": {"n": {"path": "/app/.raidar/x", "contract": "z/1"}},
        },
        {"inputs": {"a": {"from": "other.out", "contract": "other/1"}}},
        {"inputs": {"a": {"from": "nodot", "contract": "criteria/1"}}},
        {"tokens": ["lower"]},
        {"tokens": ["A", "A"]},
        {"params": {"x": float("nan")}},
        {"params": {"x": float("inf")}},
        {"params": {"min-words": 1, "min_words": 2}},
        {"params": {"limit": 1}, "tokens": ["LIMIT"]},
        {"skills": ["a", "a"]},
        {"tool": {"project": "elsewhere", "commands": {}}},
        {"tool": {"project": "tool", "commands": {"x": []}}},
        {"tool": {"project": "tool", "commands": {"a/b": ["x"]}}},
        {"outputs": {"n": {"path": "/app/.raidar/x"}}},
    ],
)
def test_manifest_refusals(tmp_path: Path, extra: dict[str, Any]) -> None:
    with pytest.raises(StagePackageError):
        load_stage_package(write_package(tmp_path, extra))


def test_manifest_yaml_errors_are_refused(tmp_path: Path) -> None:
    root = write_package(tmp_path)
    (root / "stage.yaml").write_text("a: [unclosed")
    with pytest.raises(StagePackageError):
        load_stage_package(root)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, "bool"), (7, "int"), (2.5, "float"), ("s", "str")],
)
def test_scalar_overrides_keep_their_type(tmp_path: Path, value: object, expected: str) -> None:
    defaults = {"a": False, "b": 1, "c": 1.5, "d": "x"}
    package = load_stage_package(write_package(tmp_path, {"params": defaults}))
    key = {"bool": "a", "int": "b", "float": "c", "str": "d"}[expected]
    effective = package.effective_params({key: value})
    assert effective[key] == value
    assert type(effective[key]) is type(value)
    assert effective != package.effective_params()
    assert package.effective_params() == defaults


@pytest.mark.parametrize("override", [{"zzz": 1}, {"b": True}, {"b": 1.0}, {"b": "1"}, {"a": 1}])
def test_bad_overrides_are_refused(tmp_path: Path, override: dict[str, object]) -> None:
    package = load_stage_package(write_package(tmp_path, {"params": {"a": False, "b": 1}}))
    with pytest.raises(StagePackageError):
        package.effective_params(override)


def test_param_tokens_and_declared_tokens(tmp_path: Path) -> None:
    package = load_stage_package(
        write_package(tmp_path, {"params": {"test-timeout": 5}, "tokens": ["GUIDE"]})
    )
    assert package.manifest.param_tokens() == {"TEST_TIMEOUT": "test-timeout"}


def test_unknown_top_level_entries_are_refused(tmp_path: Path) -> None:
    with pytest.raises(StagePackageError, match="layout"):
        load_stage_package(write_package(tmp_path / "a", None, {"extra.txt": "x"}))
    with pytest.raises(StagePackageError, match="layout"):
        load_stage_package(write_package(tmp_path / "b", None, {"docs/x.md": "x"}))


def test_symlinked_ancestor_below_stages_is_refused(tmp_path: Path) -> None:
    real = write_package(tmp_path / "real" / "stages" / "ns")
    (tmp_path / "other" / "stages").mkdir(parents=True)
    (tmp_path / "other" / "stages" / "ns").symlink_to(real.parent)
    with pytest.raises(StagePackageError, match="symlink"):
        load_stage_package(tmp_path / "other" / "stages" / "ns" / "demo")
    assert load_stage_package(real).name == "demo"
    deep = write_package(tmp_path / "x" / "stages" / "A_b" / "c")
    with pytest.raises(StagePackageError, match="expected"):
        load_stage_package(deep)


@pytest.mark.parametrize("entry", [".git", "__pycache__"])
def test_ignored_trees_do_not_hide_unsafe_entries(tmp_path: Path, entry: str) -> None:
    root = _note_state(tmp_path)
    ignored = root / "fixtures" / "done" / "state" / entry
    ignored.mkdir()
    (ignored / "escape").symlink_to("/etc/passwd")
    with pytest.raises(StagePackageError, match="symlink"):
        load_stage_package(root)


def test_empty_unknown_directories_are_not_part_of_a_package(tmp_path: Path) -> None:
    root = _note_state(tmp_path)
    (root / "extra").mkdir()
    with pytest.raises(StagePackageError, match="layout"):
        load_stage_package(root)


def test_nonfinite_param_override_is_refused(tmp_path: Path) -> None:
    package = load_stage_package(write_package(tmp_path, {"params": {"scale": 1.0}}))
    with pytest.raises(StagePackageError, match="finite"):
        package.effective_params({"scale": float("nan")})


def test_json_null_param_cannot_be_overridden_with_a_boolean(tmp_path: Path) -> None:
    package = load_stage_package(write_package(tmp_path, {"params": {"optional": None}}))
    with pytest.raises(StagePackageError, match="NoneType"):
        package.effective_params({"optional": False})
