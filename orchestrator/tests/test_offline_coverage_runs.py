"""The verifier's coverage pass made offline from stored step snapshots (iteration 3b.2).

A trial graded with ``coverage=False`` (``protocol.verifier_coverage: offline``)
records no coverage pass. ``produce_coverage_path`` makes it into the
Assessment store, never the job, with the same parity verdict and the same G
input the in-trial pass writes for the same snapshot; ``check_coverage_path``
makes it again and compares it with the trial's own pass or the stored run,
writing nothing. ``raidar assess --rescore`` makes it first only when a scorer
that reads it (``code-quality@9``) runs; the trial config defers it only when
the protocol says so.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from raidar.cli import main


def test_coverage_check_needs_regrade(tmp_path: Path) -> None:
    done = CliRunner().invoke(
        main, ["assess", "--rescore", str(tmp_path), "--coverage-check", "--store", "s"]
    )

    assert done.exit_code == 2
    assert "--coverage-check applies to --regrade only" in done.output
