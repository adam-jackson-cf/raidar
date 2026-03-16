"""CLI tests for scripted autoresearch smoke workflows."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from auto_researcher.cli import (
    DEFAULT_DEMO_OBJECTIVE_FIXTURE,
    DEFAULT_DEMO_SCRIPT_FIXTURE,
    main,
)


def test_demo_smoke_runs_without_pi(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "demo-smoke",
            "--objective-fixture",
            str(DEFAULT_DEMO_OBJECTIVE_FIXTURE),
            "--script-fixture",
            str(DEFAULT_DEMO_SCRIPT_FIXTURE),
            "--workspace",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    report_path = Path(payload["report_path"])
    assert payload["objective_id"] == "demo-checkout-objective"
    assert payload["status"] == "completed"
    assert report_path.is_file()
    assert (tmp_path / "scenarios" / "demo-checkout-scenario" / "v002" / "scenario.yaml").is_file()
