"""Reading a mapped test from the retained final test files.

A test id finds the test and one hop of the helpers it names (imported kept modules and
fixtures in ``conftest.py`` files above it); ``has_assertion`` and ``skipped`` are read from the
syntax tree, and anything the retained files do not hold is ``None``, never a guess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.scoring.coverage_inputs.testsource import read_test

CONFTEST = """import pytest


@pytest.fixture
def conf_fixture():
    return 1
"""
HELPERS = """def check(value):
    assert value > 0


class Builder:
    pass
"""
TESTS = """import os
import pytest
from tests.helpers import Builder, check, missing
from tests.nowhere import other
from .sibling import relative


class TestKind:
    def test_in_class(self, conf_fixture):
        check(conf_fixture)

    def test_raises(self):
        with pytest.raises(ValueError):
            int("x")

    def test_unittest_style(self):
        self.assertEqual(1, 1)

    def test_constant_assert_only(self):
        assert True
        self.assert_(True)

    @pytest.mark.skip
    def test_marked(self):
        assert 1


def test_builds(conf_fixture):
    Builder()


def test_skips_inside():
    pytest.skip("later")
    assert 1


def test_xfail_call():
    assert 1 == pytest.mark.xfail(reason="x")
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text(CONFTEST)
    (tmp_path / "tests" / "helpers.py").write_text(HELPERS)
    (tmp_path / "tests" / "test_mod.py").write_text(TESTS)
    return tmp_path


def test_a_test_carries_its_class_helpers_and_fixtures_with_the_file_they_come_from(
    root: Path,
) -> None:
    found = read_test(root, "tests/test_mod.py::TestKind::test_in_class[case-1]")

    assert found is not None
    assert found.source.startswith("    def test_in_class(self, conf_fixture):")
    assert "# tests/helpers.py\ndef check(value):" in found.helpers
    assert "# tests/conftest.py\n@pytest.fixture\ndef conf_fixture():" in found.helpers
    assert found.has_assertion
    assert not found.skipped


def test_a_helper_class_is_followed_from_a_plain_test(root: Path) -> None:
    found = read_test(root, "tests/test_mod.py::test_builds")

    assert found is not None
    assert "class Builder:" in found.helpers
    assert not found.has_assertion


def test_raises_and_assert_methods_count_as_assertions_but_constants_do_not(root: Path) -> None:
    def asserted(name: str) -> bool:
        found = read_test(root, f"tests/test_mod.py::TestKind::{name}")
        assert found is not None
        return found.has_assertion

    assert asserted("test_raises")
    assert asserted("test_unittest_style")
    assert not asserted("test_constant_assert_only")


def test_skip_markers_and_calls_make_a_test_skipped(root: Path) -> None:
    def skipped(name: str) -> bool:
        found = read_test(root, name)
        assert found is not None
        return found.skipped

    assert skipped("tests/test_mod.py::TestKind::test_marked")
    assert skipped("tests/test_mod.py::test_skips_inside")
    assert skipped("tests/test_mod.py::test_xfail_call")
    assert not skipped("tests/test_mod.py::test_builds")


def test_a_module_level_skip_marker_skips_every_test_in_it(tmp_path: Path) -> None:
    (tmp_path / "test_all.py").write_text(
        "import pytest\npytestmark = pytest.mark.skipif(True, reason='x')\n\n"
        "def test_a():\n    assert 1\n"
    )

    found = read_test(tmp_path, "test_all.py::test_a")

    assert found is not None
    assert found.skipped


def test_a_class_id_reads_the_class_itself(root: Path) -> None:
    found = read_test(root, "tests/test_mod.py::TestKind")

    assert found is not None
    assert found.source.startswith("class TestKind:")


@pytest.mark.parametrize(
    "test_id",
    [
        "tests/test_mod.py",
        "tests/test_mod.py::test_absent",
        "tests/test_mod.py::TestKind::test_absent",
        "tests/absent.py::test_builds",
        "../outside.py::test_builds",
    ],
)
def test_a_test_the_retained_files_do_not_hold_is_not_read(root: Path, test_id: str) -> None:
    assert read_test(root, test_id) is None


def test_a_module_that_does_not_parse_holds_no_tests(tmp_path: Path) -> None:
    (tmp_path / "test_bad.py").write_text("def test_a(:\n")

    assert read_test(tmp_path, "test_bad.py::test_a") is None
