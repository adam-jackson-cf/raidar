import os
import tempfile
from pathlib import Path

MARKER = Path(
    os.environ.get(
        "RAIDAR_IMPORT_MARKER", str(Path(tempfile.gettempdir()) / "raidar-marker-tool.imported")
    )
)
MARKER.write_text("imported", encoding="utf-8")
