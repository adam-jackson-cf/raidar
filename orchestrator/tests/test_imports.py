"""Import refusal boundaries and provenance isolation from execution identity."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from raidar_contract.package import StagePackageError
from raidar_contract.references import ReleaseError
from raidar_contract.workflow import load_package_workflow, package_workflow_digest

from raidar.cli import main
from raidar.contract.releases import ReleaseKind
from raidar.subject.imports import PackageImportError, import_folder, import_issues, load_imports
from raidar.subject.package.registry import load_stage_package
from raidar.subject.releases import released_workflow
from tests.package_support import write_package


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path, str]:
    root = tmp_path / "repo"
    (root / "lab").mkdir(parents=True)
    (root / "lab/releases.yaml").write_text("agents: {}\nworkflows: {}\nstages: {}\n")
    package = write_package(tmp_path / "export", {"name": "docs"})
    return root, package, load_stage_package(package).digest


def _import(tree: tuple[Path, Path, str], source: str = " unknown origin ") -> Path:
    root, package, digest = tree
    target, _ = import_folder(ReleaseKind.STAGE, package, "fxl/docs", digest, source, root)
    return target


def test_import_records_verbatim_source_without_registering_and_is_idempotent(tree) -> None:
    root, _, digest = tree
    target = _import(tree)
    assert load_stage_package(target).digest == digest
    assert load_imports(root) == [
        {"name": "fxl/docs", "sha256": digest, "source": " unknown origin "}
    ]
    assert yaml.safe_load((root / "lab/releases.yaml").read_text())["stages"] == {}
    _import(tree)
    assert load_imports(root) == [
        {"name": "fxl/docs", "sha256": digest, "source": " unknown origin "}
    ]
    assert import_issues(root) == []


@pytest.mark.parametrize("source", ["", "  ", "a\nb", "a\n", "a\rb", "a\u2028b"])
def test_blank_or_multiline_source_never_installs(tree, source: str) -> None:
    with pytest.raises(PackageImportError, match="one non-empty line"):
        _import(tree, source)
    assert not (tree[0] / "lab/stages").exists()


def test_recorded_digest_cannot_change_source_or_name(tree) -> None:
    root, package, digest = tree
    _import(tree)
    with pytest.raises(PackageImportError, match="different name or SOURCE"):
        _import(tree, "another source")
    with pytest.raises(PackageImportError, match="different name or SOURCE"):
        import_folder(ReleaseKind.STAGE, package, "other/docs", digest, " unknown origin ", root)


@pytest.mark.parametrize("name", ["raidar/docs", "../docs", "a/b/c", "/fxl/docs"])
def test_reserved_or_traversing_name_is_refused(tree, name: str) -> None:
    root, package, digest = tree
    with pytest.raises((PackageImportError, ReleaseError)):
        import_folder(ReleaseKind.STAGE, package, name, digest, "origin", root)
    assert not (root / "lab/stages").exists()


def test_single_changed_byte_is_refused_before_writing(tree) -> None:
    root, package, _ = tree
    original = (package / "prompt.md").read_bytes()
    (package / "prompt.md").write_bytes(original + b"x")
    with pytest.raises(PackageImportError, match="digest mismatch"):
        _import(tree)
    assert not (root / "lab/stages").exists()


@pytest.mark.parametrize("unsafe", ["link", "fifo", "ancestor"])
def test_unsafe_export_is_refused_including_fixtures(tree, unsafe: str) -> None:
    root, package, digest = tree
    if unsafe == "ancestor":
        link = package.parent.parent / "linked"
        link.symlink_to(package.parent, target_is_directory=True)
        package = link / package.name
    else:
        fixtures = package / "fixtures"
        fixtures.mkdir()
        if unsafe == "link":
            (fixtures / "bad").symlink_to(package / "prompt.md")
        else:
            import os

            os.mkfifo(fixtures / "bad")
    with pytest.raises((PackageImportError, StagePackageError)):
        import_folder(ReleaseKind.STAGE, package, "fxl/docs", digest, "origin", root)
    assert not (root / "lab/stages").exists()


def test_different_existing_folder_and_frozen_release_are_refused(tree) -> None:
    root, package, digest = tree
    target = _import(tree)
    (target / "prompt.md").write_text("changed target")
    with pytest.raises(PackageImportError, match="overwrite different bytes"):
        _import(tree)
    shutil.rmtree(target)
    (root / "lab/releases.yaml").write_text(f"stages:\n  fxl/docs@1: {'a' * 64}\n")
    with pytest.raises(PackageImportError, match="different bytes under existing release"):
        _import(tree)
    assert not target.exists()


def test_workflow_requires_registered_dependencies_and_source_is_not_identity(tree) -> None:
    root, _, digest = tree
    _import(tree)
    exported = root.parent / "workflow-export"
    exported.mkdir()
    (exported / "workflow.yaml").write_text(
        "schema: 2\nname: checked\nbuild: {prompt: '%%%DOCS%%% %%%TASK%%%'}\n"
        "stages: [{use: fxl/docs@1}]\n"
    )
    definition = load_package_workflow(exported, "fxl/checked")
    expected = package_workflow_digest(
        definition, [{"use": "fxl/docs@1", "sha256": digest, "params": {}}]
    )
    with pytest.raises(ReleaseError):
        import_folder(ReleaseKind.WORKFLOW, exported, "fxl/checked", expected, "local", root)
    (root / "lab/releases.yaml").write_text(f"stages:\n  fxl/docs@1: {digest}\n")
    import_folder(ReleaseKind.WORKFLOW, exported, "fxl/checked", expected, "local", root)
    with (root / "lab/releases.yaml").open("a") as handle:
        handle.write(f"workflows:\n  fxl/checked@1: {expected}\n")
    assert released_workflow("fxl/checked@1", repo_root=root, draft=False).digest == expected
    entries = load_imports(root)
    entries[-1]["source"] = "unreachable repo@not-a-commit:dirty"
    (root / "lab/imports.yaml").write_text(yaml.safe_dump(entries))
    assert import_issues(root) == []
    assert released_workflow("fxl/checked@1", repo_root=root, draft=False).digest == expected


def test_release_check_requires_folder_and_every_historical_release_digest(tree) -> None:
    root, _, digest = tree
    _import(tree)
    (root / "lab/releases.yaml").write_text(
        f"stages:\n  fxl/docs@1: {'a' * 64}\n  fxl/docs@2: {digest}\n"
    )
    assert any("fxl/docs@1" in issue for issue in import_issues(root))
    (root / "lab/imports.yaml").write_text("[]\n")
    assert any("stages/fxl/docs:" in issue for issue in import_issues(root))


@pytest.mark.parametrize(
    "document",
    [
        "{}",
        "[{}]",
        "[{name: raidar/docs, sha256: x, source: a}]",
        "[{name: fxl/docs, sha256: 1, source: a}]",
        "[{name: fxl/docs, sha256: abc, source: a}]",
        "[unterminated",
        f"[{{name: fxl/docs, sha256: {'a' * 64}, source: a}}, "
        f"{{name: fxl/other, sha256: {'a' * 64}, source: b}}]",
    ],
    ids=[
        "mapping",
        "empty-row",
        "reserved-name",
        "non-string",
        "short-digest",
        "yaml",
        "duplicate",
    ],
)
def test_malformed_ledger_is_a_release_issue(tree, document: str) -> None:
    root = tree[0]
    (root / "lab/imports.yaml").write_text(document)
    assert import_issues(root)


def test_a_malformed_digest_argument_is_refused(tree) -> None:
    root, package, _ = tree
    with pytest.raises(PackageImportError, match="64 lowercase hexadecimal"):
        import_folder(ReleaseKind.STAGE, package, "fxl/docs", "ABC", "origin", root)


def test_a_symlinked_install_folder_is_refused(tree) -> None:
    root, package, digest = tree
    elsewhere = root.parent / "elsewhere"
    elsewhere.mkdir()
    (root / "lab/stages").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(PackageImportError, match="symlinked folders are refused"):
        import_folder(ReleaseKind.STAGE, package, "fxl/docs", digest, "origin", root)
    assert list(elsewhere.iterdir()) == []


def test_the_import_command_reports_what_it_installed_and_what_it_refused(
    tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, package, digest = tree
    monkeypatch.setattr("raidar.cli.imports.REPO_ROOT", root)
    args = ["stage", "import", "fxl/docs", "--src", str(package), "--source", "origin"]

    refused = CliRunner().invoke(main, [*args, "--sha256", "0" * 64])
    imported = CliRunner().invoke(main, [*args, "--sha256", digest])

    assert refused.exit_code != 0
    assert "digest mismatch" in refused.output
    assert imported.exit_code == 0, imported.output
    assert "provenance is unverified" in imported.output
    assert f"fxl/docs@1: {digest}" in imported.output
    assert [row["name"] for row in load_imports(root)] == ["fxl/docs"]


def test_the_workflow_import_command_is_refused_without_its_dependencies(
    tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _, _ = tree
    monkeypatch.setattr("raidar.cli.imports.REPO_ROOT", root)
    exported = root.parent / "workflow-export"
    exported.mkdir()
    (exported / "workflow.yaml").write_text(
        "schema: 2\nname: checked\nbuild: {prompt: '%%%TASK%%%'}\nstages: [{use: fxl/docs@1}]\n"
    )

    result = CliRunner().invoke(
        main,
        ["workflow", "import", "fxl/checked", "--src", str(exported), "--sha256", "0" * 64]
        + ["--source", "origin"],
    )

    assert result.exit_code != 0
    assert not (root / "lab/workflows").exists()
