"""Regression tests for visual and functional scoring edge cases."""

from pathlib import Path

from raidar.scoring import functional
from raidar.scoring.visual import compare_images


def test_run_tests_passes_when_no_tests_found(monkeypatch):
    """No-test suites should be treated as pass with zero counts."""

    def mock_run_command(command, cwd, timeout=None):
        return 1, "", "No test files found, exiting with code 1"

    monkeypatch.setattr(functional, "run_command", mock_run_command)

    all_passed, tests_passed, tests_total = functional.run_tests(Path.cwd())

    assert all_passed is True
    assert tests_passed == 0
    assert tests_total == 0


def test_compare_images_parses_diff_percent_from_nonzero_exit(monkeypatch, tmp_path):
    """Odiff diff exits are non-zero and must still produce similarity."""

    class FakeResult:
        returncode = 22
        stdout = "Different pixels: 46402 (3.580401%)"
        stderr = ""

    reference = tmp_path / "reference.png"
    actual = tmp_path / "actual.png"
    diff = tmp_path / "diff.png"
    reference.write_bytes(b"ref")
    actual.write_bytes(b"actual")
    diff.write_bytes(b"diff")

    monkeypatch.setattr("raidar.scoring.visual.subprocess.run", lambda *a, **k: FakeResult())

    similarity, diff_path = compare_images(
        workspace=tmp_path,
        reference=reference,
        actual=actual,
        diff_output=diff,
    )

    assert abs(similarity - 0.96419599) < 0.000001
    assert diff_path == str(diff)
