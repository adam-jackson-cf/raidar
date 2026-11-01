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
from typing import Any

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.grading import offline
from raidar.grading.regrade import RegradeOptions


@pytest.mark.parametrize(
    ("scorers", "replay", "makes"),
    [
        (("code-quality@9",), False, True),
        (("code-quality@9", "correctness@10"), True, False),
        (("correctness@10", "resource-usage@8"), False, False),
    ],
)
def test_rescore_makes_coverage_first_only_for_a_scorer_that_reads_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scorers: tuple[str, ...],
    replay: bool,
    makes: bool,
) -> None:
    called: list[Any] = []

    async def fake(
        path: Path, store: Path, options: RegradeOptions, concurrency: int = 1
    ) -> dict[str, Any]:
        called.append((path, store, options.docker_context, concurrency))
        return {"steps": [], "errors": 0}

    monkeypatch.setattr(offline, "produce_coverage_path", fake)

    result = offline.produce_coverage(
        tmp_path / "job",
        tmp_path / "store",
        scorers,
        RegradeOptions(docker_context="orbstack"),
        replay=replay,
        concurrency=2,
    )

    assert (result is not None) is makes
    assert called == ([(tmp_path / "job", tmp_path / "store", "orbstack", 2)] if makes else [])


def test_coverage_check_needs_regrade(tmp_path: Path) -> None:
    done = CliRunner().invoke(
        main, ["assess", "--rescore", str(tmp_path), "--coverage-check", "--store", "s"]
    )

    assert done.exit_code == 2
    assert "--coverage-check applies to --regrade only" in done.output
