"""``python -m raidar.evaluation.review.quality.synthetic DEST``: the Quality fixture target."""

import sys
from pathlib import Path

from raidar.evaluation.review.quality.synthetic.generate import generate_quality_fixture

print(f"Wrote synthetic Quality fixture to {generate_quality_fixture(Path(sys.argv[1]))}")
