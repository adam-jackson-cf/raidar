"""Fast proofs for justified test growth and duplicate behaviour paths."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEST = "orchestrator/tests/test_probe.py"
OTHER_TEST = "orchestrator/tests/test_other_probe.py"
SOURCE = "orchestrator/src/raidar/engine/trial/probe.py"
MAP_PATH = ".omp/test-behavior-map.json"
CASE = f"{TEST}::test_probe"
SIMPLE = "from product import probe\n\ndef test_probe():\n    assert probe(2) == 4\n"


class EvidenceChecker(Protocol):
    """The decision seam shared with the check-only command shell."""

    def evidence_findings(
        self,
        *,
        base_sources: Mapping[str, str],
        current_sources: Mapping[str, str],
        changed_source_paths: frozenset[str],
        behavior_map: object,
        map_is_tracked: bool,
    ) -> list[str]: ...


def _load_checker() -> EvidenceChecker:
    spec = importlib.util.spec_from_file_location(
        "test_evidence_proofs", ROOT / "scripts/checks/check-test-evidence.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("EvidenceChecker", module)


checker = _load_checker()


def _mapping(*selectors: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "tests": {
            selector: {"source": SOURCE, "behavior": "Reject invalid trial inputs"}
            for selector in selectors
        },
    }


def _findings(
    current: Mapping[str, str],
    *,
    base: Mapping[str, str] | None = None,
    mapping: object = None,
    changed: frozenset[str] = frozenset({SOURCE}),
    tracked: bool = True,
) -> list[str]:
    return checker.evidence_findings(
        base_sources={} if base is None else base,
        current_sources=current,
        changed_source_paths=changed,
        behavior_map=_mapping() if mapping is None else mapping,
        map_is_tracked=tracked,
    )


def _rule_findings(findings: list[str], rule: str) -> list[str]:
    return [line for line in findings if rule in line]


def _assert_finding(findings: list[str], rule: str, *names: str) -> None:
    selected = _rule_findings(findings, rule)
    assert selected
    for line in selected:
        assert len(line.splitlines()) == 1
        assert (
            "map or remove" in line
            if rule == "TEST.growth.justified"
            else "merge or remove" in line
        )
    for name in names:
        assert any(name in line for line in selected)


def test_growth_accepts_new_file_and_case_mapped_to_changed_behavior() -> None:
    assert _findings({TEST: SIMPLE}, mapping=_mapping(TEST, CASE)) == []


def test_growth_expands_static_range_and_length_without_executing_code() -> None:
    current = (
        "import pytest\n"
        "@pytest.mark.parametrize('value', range(len(b'ab')))\n"
        "def test_probe(value):\n"
        "    assert probe(value) == value\n"
    )
    assert (
        _findings({TEST: current}, mapping=_mapping(TEST, CASE, f"{CASE}[0]", f"{CASE}[1]")) == []
    )
    findings = _findings({TEST: current}, mapping=_mapping(TEST, CASE, f"{CASE}[0]"))
    _assert_finding(findings, "TEST.growth.justified", f"{CASE}[1]")


@pytest.mark.parametrize("missing", [TEST, CASE])
def test_growth_refuses_each_unmapped_file_or_case(missing: str) -> None:
    selectors = [selector for selector in (TEST, CASE) if selector != missing]
    _assert_finding(
        _findings({TEST: SIMPLE}, mapping=_mapping(*selectors)),
        "TEST.growth.justified",
        missing,
    )


@pytest.mark.parametrize(
    "mapping",
    [
        {"schemaVersion": 2, "tests": {}},
        {"schemaVersion": 1, "tests": []},
        {"schemaVersion": 1, "tests": {CASE: {"source": SOURCE}}},
        {
            "schemaVersion": 1,
            "tests": {CASE: {"source": SOURCE, "behavior": "  "}},
        },
    ],
)
def test_growth_refuses_missing_or_malformed_behavior_evidence(mapping: object) -> None:
    _assert_finding(_findings({TEST: SIMPLE}, mapping=mapping), "TEST.growth.justified", TEST)


def test_growth_refuses_mapping_to_unchanged_source() -> None:
    _assert_finding(
        _findings({TEST: SIMPLE}, mapping=_mapping(TEST, CASE), changed=frozenset()),
        "TEST.growth.justified",
        CASE,
    )


def test_growth_refuses_untracked_behavior_map() -> None:
    _assert_finding(
        _findings({TEST: SIMPLE}, mapping=_mapping(TEST, CASE), tracked=False),
        "TEST.growth.justified",
        MAP_PATH,
    )


def test_growth_does_not_require_mapping_unchanged_or_removed_tests() -> None:
    assert _findings({TEST: SIMPLE}, base={TEST: SIMPLE}) == []
    assert _findings({}, base={TEST: SIMPLE}) == []


def test_growth_requires_only_added_case_in_existing_file() -> None:
    added = "\ndef test_invalid():\n    assert probe(-1) is None\n"
    new_case = f"{TEST}::test_invalid"
    assert _findings({TEST: SIMPLE + added}, base={TEST: SIMPLE}, mapping=_mapping(new_case)) == []
    _assert_finding(
        _findings({TEST: SIMPLE + added}, base={TEST: SIMPLE}),
        "TEST.growth.justified",
        new_case,
    )


def _parameterized(rows: str) -> str:
    return (
        "import pytest\nfrom product import probe\n\n"
        f"@pytest.mark.parametrize(('value', 'expected'), [{rows}])\n"
        "def test_probe(value, expected):\n    assert probe(value) == expected\n"
    )


@pytest.mark.parametrize(
    ("before", "after", "row_id"),
    [
        (
            "pytest.param(2, 4, id='valid')",
            "pytest.param(2, 4, id='valid'), pytest.param(-1, None, id='invalid')",
            "invalid",
        ),
        ("(2, 4)", "(2, 4), (-1, None)", "-1-None"),
    ],
)
def test_growth_counts_added_parameter_rows_not_only_functions(
    before: str, after: str, row_id: str
) -> None:
    row = f"{CASE}[{row_id}]"
    base = {TEST: _parameterized(before)}
    current = {TEST: _parameterized(after)}
    _assert_finding(_findings(current, base=base), "TEST.growth.justified", row)
    assert _findings(current, base=base, mapping=_mapping(row)) == []


def test_growth_does_not_count_parameter_reordering_as_new_evidence() -> None:
    valid = "pytest.param(2, 4, id='valid')"
    invalid = "pytest.param(-1, None, id='invalid')"
    assert (
        _findings(
            {TEST: _parameterized(f"{invalid}, {valid}")},
            base={TEST: _parameterized(f"{valid}, {invalid}")},
        )
        == []
    )


def test_growth_counts_each_added_stacked_parameter_combination() -> None:
    template = (
        "import pytest\nfrom product import probe\n\n"
        "@pytest.mark.parametrize('value', {values})\n"
        "@pytest.mark.parametrize('mode', ['strict', 'lenient'])\n"
        "def test_probe(value, mode):\n    assert probe(value, mode) is not None\n"
    )
    base = {TEST: template.format(values="[1]")}
    current = {TEST: template.format(values="[1, 2]")}
    strict = f"{CASE}[strict-2]"
    lenient = f"{CASE}[lenient-2]"
    _assert_finding(_findings(current, base=base), "TEST.growth.justified", strict, lenient)
    assert _findings(current, base=base, mapping=_mapping(strict, lenient)) == []


@pytest.mark.parametrize(
    ("source", "selector"),
    [
        ("async def test_async():\n    assert await probe(2) == 4\n", "test_async"),
        (
            "class TestProbe:\n    def test_value(self):\n        assert probe(2) == 4\n",
            "TestProbe::test_value",
        ),
    ],
)
def test_growth_includes_async_tests_and_class_methods(source: str, selector: str) -> None:
    full_selector = f"{TEST}::{selector}"
    _assert_finding(_findings({TEST: source}), "TEST.growth.justified", full_selector)
    assert _findings({TEST: source}, mapping=_mapping(TEST, full_selector)) == []


@pytest.mark.parametrize(
    "new_source",
    [
        SIMPLE.replace("test_probe", "test_copy"),
        (
            "from product import probe as calculate\n\ndef test_copy():\n"
            "    computed = calculate(2)\n    assert computed == 4\n"
        ),
        (
            "from product import probe\n\n# moved and renamed\n\n"
            "def test_copy():\n    answer = probe(2)\n    assert answer == 4\n"
        ),
    ],
)
def test_duplicate_refuses_existing_path_despite_names_aliases_or_formatting(
    new_source: str,
) -> None:
    other = f"{OTHER_TEST}::test_copy"
    found = _findings(
        {TEST: SIMPLE, OTHER_TEST: new_source},
        base={TEST: SIMPLE},
        mapping=_mapping(OTHER_TEST, other),
    )
    _assert_finding(found, "TEST.duplicate-path", CASE, other)
    assert any(CASE in line and other in line for line in found)


def test_duplicate_compares_other_new_cases_too() -> None:
    other = f"{TEST}::test_copy"
    current = SIMPLE + "\ndef test_copy():\n    assert probe(2) == 4\n"
    _assert_finding(
        _findings({TEST: current}, mapping=_mapping(TEST, CASE, other)),
        "TEST.duplicate-path",
        CASE,
        other,
    )


@pytest.mark.parametrize(
    "new_source",
    [
        SIMPLE.replace("probe(2)", "probe(3)"),
        SIMPLE.replace("== 4", "== 5"),
        SIMPLE.replace("== 4", "> 4"),
        SIMPLE.replace("from product", "from other_product"),
    ],
)
def test_duplicate_accepts_distinct_bound_input_target_or_assertion(new_source: str) -> None:
    other = f"{OTHER_TEST}::test_probe"
    assert (
        _findings(
            {TEST: SIMPLE, OTHER_TEST: new_source},
            base={TEST: SIMPLE},
            mapping=_mapping(OTHER_TEST, other),
        )
        == []
    )


def test_duplicate_ignores_duplicates_already_present_in_base() -> None:
    snapshots = {TEST: SIMPLE, OTHER_TEST: SIMPLE}
    assert _findings(snapshots, base=snapshots) == []


def test_duplicate_compares_parameter_bound_behavior_not_row_labels() -> None:
    before = "pytest.param(2, 4, id='valid')"
    after = before + ", pytest.param(2, 4, id='renamed')"
    old = f"{CASE}[valid]"
    new = f"{CASE}[renamed]"
    _assert_finding(
        _findings(
            {TEST: _parameterized(after)},
            base={TEST: _parameterized(before)},
            mapping=_mapping(new),
        ),
        "TEST.duplicate-path",
        old,
        new,
    )
