"""Visual regression scoring using odiff."""

import subprocess
from pathlib import Path

from ..config import settings
from ..schemas.scorecard import VisualScore


def capture_screenshot(workspace: Path, command: list[str], output_path: Path) -> bool:
    """Capture a screenshot of the implementation.

    Args:
        workspace: Working directory
        command: Screenshot command argv to run
        output_path: Path to save screenshot

    Returns:
        True if screenshot was captured successfully
    """
    try:
        result = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.screenshot,
        )
        return result.returncode == 0 and output_path.exists()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def compare_images(
    workspace: Path,
    reference: Path,
    actual: Path,
    diff_output: Path,
    threshold: float | None = None,
) -> tuple[float, str | None]:
    """Compare two images using odiff.

    Args:
        reference: Path to reference image
        actual: Path to actual screenshot
        diff_output: Path to save diff image
        threshold: Anti-aliasing tolerance (0-1)

    Returns:
        Tuple of (similarity_score, diff_path or None)
    """
    if threshold is None:
        threshold = settings.visual.odiff_threshold

    if not reference.exists():
        return 0.0, None
    if not actual.exists():
        return 0.0, None

    try:
        # Run odiff comparison
        result = subprocess.run(
            [
                "bunx",
                "odiff",
                str(reference),
                str(actual),
                str(diff_output),
                "--threshold",
                str(threshold),
            ],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.image_compare,
        )

        output = result.stdout + result.stderr

        # odiff returns 0 for exact match and non-zero for differences/errors.
        if result.returncode == 0:
            # Images match
            return 1.0, None

        # Images differ - parse percentage from odiff output when available.
        import re

        match = re.search(r"(\d+\.?\d*)\s*%", output)
        if match:
            diff_percent = float(match.group(1))
            similarity = max(0, 1 - (diff_percent / 100))
            return similarity, str(diff_output) if diff_output.exists() else None
        if diff_output.exists():
            return 0.0, str(diff_output)
        return 0.0, None

    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0.0, None


def evaluate_visual(
    workspace: Path,
    reference_image: Path,
    screenshot_command: list[str],
    threshold: float | None = None,
) -> VisualScore:
    """Evaluate visual similarity to reference design.

    Args:
        workspace: Path to workspace directory
        reference_image: Path to reference design image
        screenshot_command: Command argv to capture screenshot
        threshold: Minimum similarity threshold (not used in scoring, for reference)

    Returns:
        VisualScore with similarity and diff path
    """
    if threshold is None:
        threshold = settings.visual.similarity_threshold

    actual_path = workspace / "actual.png"
    diff_path = workspace / "diff.png"

    # Capture screenshot
    if not capture_screenshot(workspace, screenshot_command, actual_path):
        return VisualScore(
            similarity=0.0,
            diff_path=None,
            capture_succeeded=False,
            threshold=threshold,
        )

    # Compare images (odiff_threshold is used for anti-aliasing tolerance)
    similarity, diff_output = compare_images(
        workspace=workspace,
        reference=reference_image,
        actual=actual_path,
        diff_output=diff_path,
    )

    return VisualScore(
        similarity=similarity,
        diff_path=diff_output,
        capture_succeeded=True,
        threshold=threshold,
    )
