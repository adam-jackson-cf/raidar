import subprocess

import pytest


def run(argv, *args):
    return subprocess.run([*argv, *args], capture_output=True, text=True, check=False)


def test_adds(entrypoint_argv):
    assert run(entrypoint_argv, "add", "2", "3").stdout.strip() == "5"


@pytest.mark.functionality
def test_adds_negative(entrypoint_argv):
    assert run(entrypoint_argv, "add", "-2", "3").stdout.strip() == "1"


@pytest.mark.error
def test_rejects_non_numbers(entrypoint_argv):
    assert run(entrypoint_argv, "add", "x", "1").returncode == 2


@pytest.mark.skip(reason="fixture skip")
def test_skipped():
    raise AssertionError("never runs")
