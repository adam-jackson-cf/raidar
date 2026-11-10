"""Fast behavioural proofs for target-owned Python policy decisions."""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
PRODUCT = "orchestrator/src/raidar/"
CONTRACT = f"{PRODUCT}contracts.py"
WRAPPER = f"{PRODUCT}engine/process.py"
ADAPTER = f"{PRODUCT}adapters/plugins.py"


class Finding(Protocol):
    code: str
    relpath: str
    line: int
    message: str

    def render(self) -> str: ...


class PythonChecker(Protocol):
    def structure_diagnostics_for_relpath(self, relpath: str) -> list[Finding]: ...

    def class_interface_diagnostics_for_source(
        self, source: str, relpath: str
    ) -> list[Finding]: ...

    def scalability_diagnostics_for_source(self, source: str, relpath: str) -> list[Finding]: ...

    def main(self, argv: Sequence[str] | None = None) -> int: ...


def _load_checker() -> PythonChecker:
    spec = importlib.util.spec_from_file_location(
        "python_policy_proofs", ROOT / "scripts/checks/check-python-quality.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("PythonChecker", module)


@pytest.fixture
def checker(monkeypatch: pytest.MonkeyPatch) -> PythonChecker:
    loaded = _load_checker()
    policy: dict[str, object] = {
        "MAINTAINED_ROOTS": (PRODUCT, "orchestrator/tests/", "scripts/checks/", ".agents/skills/"),
        "SCRIPT_ROOTS": ("scripts/checks/",),
        "TEST_ROOTS": ("orchestrator/tests/",),
        "SKILL_ROOTS": (".agents/skills/",),
        "SCRIPT_NAME_ALLOWLIST": {"check-policy.py", "legacy_command.py"},
        "SCRIPT_NAME_EXCEPTIONS": {"scripts/checks/legacy_command.py"},
        "CANONICAL_TEST_GROUPS": {"policy-fast": ("orchestrator/tests/test_policy.py",)},
        "ALLOWED_TEST_ROOT_FILES": {"orchestrator/tests/conftest.py"},
        "GENERIC_NAME_ALLOWLIST": {f"{PRODUCT}approved/common", f"{PRODUCT}approved/common.py"},
        "SEAM_SUBPROCESS_WRAPPERS": {WRAPPER: {"run_command"}},
        "DYNAMIC_IMPORT_ROOTS": (PRODUCT,),
        "ADAPTER_IMPORT_ALLOWLIST": {ADAPTER},
        "CONTRACT_FILES": {CONTRACT},
        "LOOP_DEPTH_ROOTS": (PRODUCT,),
    }
    for name, value in policy.items():
        monkeypatch.setattr(loaded, name, value, raising=False)
    return loaded


def _assert_refusal(findings: list[Finding], path: str, rule: str) -> None:
    owned = [finding for finding in findings if finding.code == rule]
    assert len(owned) == 1
    finding = owned[0]
    assert finding.relpath == path
    assert finding.line >= 1
    assert re.fullmatch(
        rf"{re.escape(path)}:\d+: {re.escape(rule)} [^\r\n;]+; [^\r\n]+",
        finding.render(),
    )


@pytest.mark.parametrize(
    ("rule", "accepted", "refused"),
    [
        ("PYQ100", f"{PRODUCT}engine/probe.py", "unowned/probe.py"),
        ("PYQ101", "scripts/checks/check-policy.py", "scripts/checks/check_policy.py"),
        ("PYQ102", "orchestrator/tests/test_policy.py", "orchestrator/tests/test_unmapped.py"),
        ("PYQ103", f"{PRODUCT}engine/probe.py", f"{PRODUCT}common/probe.py"),
        ("PYQ104", f"{PRODUCT}engine/probe.py", f"{PRODUCT}engine/common.py"),
        (
            "PYQ106",
            ".agents/skills/check-policy/scripts/probe.py",
            ".agents/skills/check_policy/scripts/probe.py",
        ),
    ],
)
def test_structure_accepts_owned_paths_and_refuses_policy_violations(
    checker: PythonChecker, rule: str, accepted: str, refused: str
) -> None:
    assert checker.structure_diagnostics_for_relpath(accepted) == []
    _assert_refusal(checker.structure_diagnostics_for_relpath(refused), refused, rule)


@pytest.mark.parametrize("part", ["common", "helpers", "misc", "stuff", "temp"])
@pytest.mark.parametrize("directory", [False, True])
def test_every_restricted_name_requires_exact_path_approval(
    checker: PythonChecker, part: str, directory: bool
) -> None:
    path = f"{PRODUCT}{part}/probe.py" if directory else f"{PRODUCT}{part}.py"
    _assert_refusal(
        checker.structure_diagnostics_for_relpath(path), path, "PYQ103" if directory else "PYQ104"
    )


@pytest.mark.parametrize(
    "path",
    [
        f"{PRODUCT}approved/common/probe.py",
        f"{PRODUCT}approved/common.py",
        "scripts/checks/legacy_command.py",
    ],
)
def test_exact_target_exceptions_accept_existing_owned_names(
    checker: PythonChecker, path: str
) -> None:
    assert checker.structure_diagnostics_for_relpath(path) == []


def test_kebab_case_alone_does_not_approve_a_command(checker: PythonChecker) -> None:
    path = "scripts/checks/unapproved-command.py"
    _assert_refusal(checker.structure_diagnostics_for_relpath(path), path, "PYQ101")


@pytest.mark.parametrize("path", ["docs/unowned.py", "var/unowned.py"])
def test_explicit_nonmaintained_trees_are_excluded(checker: PythonChecker, path: str) -> None:
    assert checker.structure_diagnostics_for_relpath(path) == []


@pytest.mark.parametrize("selection", [[], ["unowned"], ["unowned/probe.py"]])
def test_cli_does_not_filter_unknown_roots_before_rejection(
    checker: PythonChecker, tmp_path: Path, capsys: pytest.CaptureFixture[str], selection: list[str]
) -> None:
    path = "unowned/probe.py"
    candidate = tmp_path / path
    candidate.parent.mkdir()
    candidate.write_text("VALUE = 1\n", encoding="utf-8")
    assert checker.main(["--repo-root", str(tmp_path), *selection]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert re.fullmatch(rf"{re.escape(path)}:\d+: PYQ100 [^\r\n;]+; [^\r\n]+\n", captured.err)


def test_cli_accepts_a_compliant_owned_source(
    checker: PythonChecker, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    candidate = tmp_path / PRODUCT / "probe.py"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("VALUE = 1\n", encoding="utf-8")
    assert checker.main(["--repo-root", str(tmp_path)]) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "invocation",
    [
        "import subprocess\nsubprocess.run(['probe'], timeout=1)",
        "import subprocess as process\nprocess.run(['probe'], timeout=1)",
        "from subprocess import run as execute\nexecute(['probe'], timeout=1)",
    ],
)
def test_subprocess_approval_is_scoped_to_module_and_wrapper(
    checker: PythonChecker, invocation: str
) -> None:
    body = "\n".join(f"    {line}" for line in invocation.splitlines())
    approved = f"def run_command():\n{body}\n"
    assert checker.class_interface_diagnostics_for_source(approved, WRAPPER) == []
    for source, path in [
        (approved, f"{PRODUCT}engine/other.py"),
        (approved.replace("run_command", "other"), WRAPPER),
    ]:
        _assert_refusal(
            checker.class_interface_diagnostics_for_source(source, path), path, "PYQ220"
        )


@pytest.mark.parametrize(
    "source",
    [
        "import importlib\nplugin = importlib.import_module('plugin')\n",
        "import importlib as loader\nplugin = loader.import_module('plugin')\n",
        "from importlib import import_module as load\nplugin = load('plugin')\n",
        "plugin = __import__('plugin')\n",
    ],
)
def test_dynamic_imports_require_exact_adapter_approval(
    checker: PythonChecker, source: str
) -> None:
    assert checker.class_interface_diagnostics_for_source(source, ADAPTER) == []
    path = f"{PRODUCT}engine/plugins.py"
    _assert_refusal(checker.class_interface_diagnostics_for_source(source, path), path, "PYQ221")


@pytest.mark.parametrize(
    "annotation",
    [
        "Callable[[str], int]",
        "typing.Callable[[str], int]",
        "'Callable[[str], int]'",
        "Callback[[str], int]",
    ],
)
def test_contract_callables_require_named_aliases(checker: PythonChecker, annotation: str) -> None:
    imports = (
        "import typing\nfrom typing import Callable\nfrom typing import Callable as Callback\n"
    )
    accepted = imports + "Runner = Callable[[str], int]\nclass Contract:\n    run: Runner\n"
    assert checker.class_interface_diagnostics_for_source(accepted, CONTRACT) == []
    refused = imports + f"class Contract:\n    run: {annotation}\n"
    _assert_refusal(
        checker.class_interface_diagnostics_for_source(refused, CONTRACT), CONTRACT, "PYQ230"
    )


@pytest.mark.parametrize("annotation", ["Any", "typing.Any", "list[Any]", "'Any'", "Unknown"])
def test_contract_collaborators_cannot_escape_through_any(
    checker: PythonChecker, annotation: str
) -> None:
    imports = "import typing\nfrom typing import Any\nfrom typing import Any as Unknown\n"
    accepted = imports + "class Contract:\n    collaborator: object\n"
    assert checker.class_interface_diagnostics_for_source(accepted, CONTRACT) == []
    refused = imports + f"class Contract:\n    collaborator: {annotation}\n"
    _assert_refusal(
        checker.class_interface_diagnostics_for_source(refused, CONTRACT), CONTRACT, "PYQ231"
    )


@pytest.mark.parametrize(
    ("accepted", "refused"),
    [
        (
            "for a in rows:\n    for b in a:\n        pass\n",
            "for a in rows:\n    for b in a:\n        for c in b:\n            pass\n",
        ),
        (
            "result = [b for a in rows for b in a]\n",
            "result = [c for a in rows for b in a for c in b]\n",
        ),
        (
            "for a in rows:\n    result = [b for b in a]\n",
            "for a in rows:\n    result = [c for b in a for c in b]\n",
        ),
        (
            "while ready:\n    for a in rows:\n        pass\n",
            "while ready:\n    for a in rows:\n        while a:\n            pass\n",
        ),
        (
            "async def scan(rows):\n"
            "    async for a in rows:\n        for b in a:\n            pass\n",
            "async def scan(rows):\n"
            "    async for a in rows:\n        for b in a:\n"
            "            for c in b:\n                pass\n",
        ),
    ],
)
def test_loop_and_comprehension_depth_accepts_two_and_refuses_three(
    checker: PythonChecker, accepted: str, refused: str
) -> None:
    path = f"{PRODUCT}scoring/compute.py"
    assert checker.scalability_diagnostics_for_source(accepted, path) == []
    _assert_refusal(checker.scalability_diagnostics_for_source(refused, path), path, "PYS250")


def test_nested_function_loop_depth_is_independent(checker: PythonChecker) -> None:
    source = (
        "for a in rows:\n    for b in a:\n        def scan():\n"
        "            for c in values:\n                return c\n"
    )
    assert checker.scalability_diagnostics_for_source(source, f"{PRODUCT}scoring/compute.py") == []
