"""``python -m tests.review_fixtures.quality DEST``: the Quality fixture target."""

import sys
from pathlib import Path

from tests.review_fixtures.quality.generate import generate_quality_fixture

print(f"Wrote synthetic Quality fixture to {generate_quality_fixture(Path(sys.argv[1]))}")
