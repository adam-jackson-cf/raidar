"""Grading identity follows executable imports and refuses unaccounted payload dependencies."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from raidar.contract import source_imports
from raidar.grading import identity, payload, records


def test_the_grader_closure_includes_secrets_and_contract_initializers_not_unimported_models() -> (
    None
):
    sources = identity.grading_sources()
    assert {
        "judge/secrets.py",
        "raidar_contract/__init__.py",
        "raidar_contract/canonical.py",
    } <= sources.keys()
    assert "raidar_contract/agent.py" not in sources
    assert "raidar_contract/workflow.py" not in sources


def test_grading_identity_follows_transitive_sources_and_payload_not_distribution_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raidar = tmp_path / "raidar"
    contract = tmp_path / "raidar_contract"
    grading = raidar / "grading"
    grading.mkdir(parents=True)
    cache = grading / "__pycache__"
    cache.mkdir()
    (cache / "ignored.py").write_text('__import__("not executable")\n')
    contract.mkdir()
    (raidar / "__init__.py").write_text("")
    (grading / "__init__.py").write_text("")
    (grading / "entry.py").write_text(
        "from raidar import secret\nfrom raidar_contract import canonical\n"
    )
    secret = raidar / "secret.py"
    secret.write_text("value = 1\n")
    (contract / "__init__.py").write_text("CONTRACTS = {}\n")
    (contract / "canonical.py").write_text("value = 1\n")
    monkeypatch.setattr(source_imports, "RAIDAR_DIR", raidar)
    monkeypatch.setattr(source_imports, "CONTRACT_DIR", contract)
    monkeypatch.setattr(identity, "GRADING_DIR", grading)
    resource = tmp_path / "resource"
    resource.write_bytes(b"payload")
    monkeypatch.setattr(identity, "runtime_files", lambda: {"resource": resource})
    baseline = records.grading_execution_digest()
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "2.0.0"\n')
    (contract / "unimported.py").write_text("value = 3\n")
    assert records.grading_execution_digest() == baseline
    secret.write_text("value = 2\n")
    changed = records.grading_execution_digest()
    assert changed != baseline
    resource.write_bytes(b"changed payload")
    assert records.grading_execution_digest() != changed
    dependency = raidar / "new_dependency.py"
    dependency.write_text("value = 1\n")
    secret.write_text("from raidar import new_dependency\n")
    importer_changed = records.grading_execution_digest()
    dependency.write_text("value = 2\n")
    assert records.grading_execution_digest() != importer_changed
    dependency.write_text('__import__("unaccounted")\n')
    with pytest.raises(ValueError, match="unaccounted grading dynamic roots"):
        records.grading_execution_digest()
    secret.write_text("value = 3\n")
    dependency.unlink()
    assert "new_dependency.py" not in identity.grading_sources()


@pytest.mark.parametrize(
    ("source", "targets"),
    [
        ("import os", set()),
        ('importlib.import_module("coverage")', {"coverage"}),
        ('from importlib import import_module as load\nload("hidden")', {"hidden"}),
        ("__import__(name)", {None}),
        ("entry_points()", {None}),
        ('__import__("hidden")', {"hidden"}),
    ],
)
def test_name_loaded_imports_are_accounted_including_aliases_and_unknown_names(
    source: str,
    targets: set[str | None],
) -> None:
    assert identity._dynamic_targets(ast.parse(source)) == targets


@pytest.mark.parametrize(
    ("source", "roots"),
    [
        ('args = ("-m", "pytest", "-q")', {"pytest"}),
        (
            'PLUGIN_MODULE = "raidar_oracle_plugin"\nargs = ["-p", PLUGIN_MODULE]',
            {"raidar_oracle_plugin"},
        ),
        ('args = ("-m", dynamic)', {None}),
        ('args = ("-p", "unaccounted")', {"unaccounted"}),
        ('args = ("-q", "text")', set()),
        ('args = (flag, "pytest")', set()),
        ('args = ("-m", 4)', {None}),
        ('args = ("-m", choose())', {None}),
    ],
)
def test_subprocess_and_pytest_plugin_roots_are_accounted(
    source: str, roots: set[str | None]
) -> None:
    assert identity._command_roots(ast.parse(source)) == roots


@pytest.mark.parametrize(
    ("module", "source", "refused"),
    [
        (
            "raidar.grading.runtime.raidar_oracle_plugin",
            'importlib.import_module("coverage")',
            False,
        ),
        ("raidar.grading.runtime.raidar_oracle_plugin", 'importlib.import_module("hidden")', True),
        ("raidar.grading.other", '__import__("coverage")', True),
        ("raidar.grading.sandbox", 'args = ("-m", "pytest")', False),
        ("raidar.grading.sandbox", 'args = ("-m", "hidden")', True),
    ],
)
def test_unaccounted_dynamic_imports_and_command_roots_fail_closed(
    tmp_path: Path,
    module: str,
    source: str,
    refused: bool,
) -> None:
    path = tmp_path / "source.py"
    path.write_text(source)
    if refused:
        with pytest.raises(ValueError, match="unaccounted grading dynamic roots"):
            identity._check_dynamic(path, module, (0, 0, 0))
    else:
        identity._check_dynamic(path, module, (0, 0, 0))


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "missing"])
def test_unaccounted_or_missing_runtime_resources_are_refused(tmp_path: Path, kind: str) -> None:
    for name in payload.RESOURCES:
        (tmp_path / name).write_text("payload")
    if kind == "file":
        (tmp_path / "extra").write_text("unknown")
    elif kind == "directory":
        (tmp_path / "extra").mkdir()
    elif kind == "symlink":
        (tmp_path / "extra").symlink_to("pytest.ini")
    else:
        (tmp_path / "pytest.ini").unlink()
    with pytest.raises(ValueError, match="grading resource"):
        payload.runtime_files(tmp_path)


def test_bytecode_caches_never_enter_the_shipped_resource_inventory(tmp_path: Path) -> None:
    for name in payload.RESOURCES:
        (tmp_path / name).write_bytes(name.encode())
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "plugin.pyc").write_bytes(b"not shipped")
    assert {name: path.read_bytes() for name, path in payload.runtime_files(tmp_path).items()} == {
        name: name.encode() for name in payload.RESOURCES
    }


def test_a_missing_closure_source_fails_instead_of_silently_shrinking_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(identity, "grading_roots", lambda: ("missing",))
    monkeypatch.setattr(source_imports, "import_closure", lambda module: frozenset({"missing"}))
    with pytest.raises(ValueError, match="grading source unavailable"):
        identity.grading_sources()


@pytest.mark.parametrize("mutation", ["unchanged", "rewrite", "remove"])
def test_inventory_cache_refreshes_changed_or_disappeared_inputs(
    tmp_path: Path,
    mutation: str,
) -> None:
    source = tmp_path / "source.py"
    source.write_text("value = 1\n")
    watched = {source: identity._facts(source)}
    if mutation == "rewrite":
        source.write_text("value = 2\n")
    elif mutation == "remove":
        source.unlink()
    assert identity._changed(watched) is (mutation != "unchanged")
