"""``python -m tests.review_fixtures.projection [DEST]``: the review fixture target."""

import sys
from pathlib import Path

from raidar.contract.paths import tmp_root
from tests.review_fixtures.projection.generate import generate_review_fixture

dest = Path(sys.argv[1]) if len(sys.argv) > 1 else tmp_root() / "review-fixture"
for job_dir in generate_review_fixture(dest.resolve()):
    print(f"Wrote synthetic run: {job_dir}")
