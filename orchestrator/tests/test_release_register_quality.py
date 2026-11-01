"""The release register's own check passes on this repository's real tree."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from raidar.cli import main

pytestmark = pytest.mark.quality


def test_releases_check_passes_on_the_real_tree() -> None:
    result = CliRunner().invoke(main, ["releases", "check"])

    assert result.exit_code == 0, result.output
    assert "releases: ok" in result.output
