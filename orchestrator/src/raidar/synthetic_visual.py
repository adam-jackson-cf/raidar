"""Synthetic visual-ui-implementation benchmark fixture.

Provides a homepage-replication scenario with screenshot evidence (reference,
actual, diff, and per-region assets) so the review surface can exercise the
visual evidence model. Assets are tiny generated PNGs and every payload keeps
the ``synthetic`` marker; they must never be read as real benchmark evidence.
"""

from __future__ import annotations

import struct
import zlib
from datetime import UTC, datetime
from pathlib import Path

from raidar.schemas.events import TraceEvent
from raidar.schemas.scorecard import (
    EvalConfig,
    EvalRun,
    FunctionalScore,
    MetricScore,
    Scorecard,
    ScorerResult,
    VisualScore,
)

SYNTHETIC_MARKER = "synthetic"
VISUAL_SCENARIO = "homepage-hero-replication"
VISUAL_REVISION = "v001"
VISUAL_PROFILE = "scorers:design-to-code@1:0.95+resource-efficiency@1:0.05"
VISUAL_METRICS = [
    "functional",
    "visual-regression",
    "verification-stability",
    "resource-efficiency",
]
VISUAL_SCORERS = ["design-to-code@1", "resource-efficiency@1"]
_THRESHOLD = 0.8
_STARTED = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

_IMAGE_WIDTH = 480
_IMAGE_HEIGHT = 300
_REGION_HEIGHT = 100
# Region bands of the reference homepage: (name, y-start fraction, y-end fraction, rgb).
_REGIONS = [
    ("hero", 0.0, 0.45, (30, 60, 120)),
    ("features", 0.45, 0.8, (40, 90, 80)),
    ("footer", 0.8, 1.0, (25, 28, 40)),
]


def visual_experiment_specs() -> list[dict[str, object]]:
    """Experiment specs consumed by :mod:`raidar.synthetic`."""

    meta = {
        "scenario": VISUAL_SCENARIO,
        "revision": VISUAL_REVISION,
        "profile": VISUAL_PROFILE,
        "metric_ids": VISUAL_METRICS,
        "scorer_ids": VISUAL_SCORERS,
    }
    benchmark_runs = [
        _visual_run(
            f"{SYNTHETIC_MARKER}-home-bench-{index:02d}",
            "claude-code",
            "anthropic/claude-opus-4.8",
            similarity=sim,
            regions={"hero": sim + 0.01, "features": sim - 0.01, "footer": sim + 0.02},
            duration=210.0 + 12 * index,
            tokens=58000,
        )
        for index, sim in enumerate((0.95, 0.94, 0.93), start=1)
    ]
    candidate_sims = (0.86, 0.85, 0.84, 0.72)
    candidate_runs = [
        _visual_run(
            f"{SYNTHETIC_MARKER}-home-cand-{index:02d}",
            "codex-cli",
            "openai/gpt-5.5:low",
            similarity=sim,
            regions=_candidate_regions(sim),
            duration=150.0 + 10 * index,
            tokens=31000,
        )
        for index, sim in enumerate(candidate_sims, start=1)
    ]
    return [
        {"harness": "claude-code", "model_label": "opus-4.8", "meta": meta, "runs": benchmark_runs},
        {
            "harness": "codex-cli",
            "model_label": "gpt-5.5-low",
            "meta": meta,
            "runs": candidate_runs,
        },
    ]


def _candidate_regions(similarity: float) -> dict[str, float]:
    if similarity < _THRESHOLD:
        return {"hero": 0.61, "features": 0.78, "footer": 0.84}
    return {"hero": similarity - 0.07, "features": similarity + 0.04, "footer": similarity + 0.02}


def write_visual_assets(experiment_dir: Path, runs: list[EvalRun]) -> None:
    """Write reference/actual/diff PNGs for each run's visual evidence."""

    for run in runs:
        visual = run.scores.visual
        if visual is None:
            continue
        visual_dir = experiment_dir / "runs" / run.id / "visual"
        visual_dir.mkdir(parents=True, exist_ok=True)
        _write_page_assets(visual_dir, visual.similarity)
        for region in visual.regional_scores:
            _write_region_assets(visual_dir, str(region["name"]), float(region["similarity"]))


def _visual_run(
    run_id: str,
    harness: str,
    model: str,
    *,
    similarity: float,
    regions: dict[str, float],
    duration: float,
    tokens: int,
) -> EvalRun:
    passed = similarity >= _THRESHOLD
    scorecard = Scorecard(
        run_id=run_id,
        scenario_name=VISUAL_SCENARIO,
        scenario_revision=VISUAL_REVISION,
        harness=harness,
        model=model,
        starter_root="starter",
        duration_sec=duration,
        functional=FunctionalScore(
            passed=True,
            tests_passed=4,
            tests_total=4,
            build_succeeded=True,
            gates_passed=3,
            gates_total=3,
        ),
        visual=_visual_score(similarity, regions, passed),
        metadata={
            SYNTHETIC_MARKER: True,
            "run": {"canonical_run_dir": None, "run_json_path": None},
            "process": {"uncached_input_tokens": tokens},
        },
        metric_scores=_visual_metric_scores(similarity, passed),
        scorer_results=[
            ScorerResult(
                scorer_id="design-to-code",
                version=1,
                category="quality",
                weight=0.95,
                score=round(similarity, 3),
            ),
            ScorerResult(
                scorer_id="resource-efficiency",
                version=1,
                category="efficiency",
                weight=0.05,
                score=0.9,
            ),
        ],
    )
    return EvalRun(
        id=run_id,
        timestamp=_STARTED.isoformat(),
        config=EvalConfig(
            model=model,
            harness=harness,
            scenario_name=VISUAL_SCENARIO,
            scenario_revision=VISUAL_REVISION,
            starter_root="starter",
            evaluation_profile=VISUAL_PROFILE,
            scorers=VISUAL_SCORERS,
        ),
        duration_sec=duration,
        terminated_early=False,
        scores=scorecard,
        traces=_visual_trace_events(passed),
    )


def _visual_score(similarity: float, regions: dict[str, float], passed: bool) -> VisualScore:
    worst = min(regions.values())
    return VisualScore(
        similarity=round(similarity, 3),
        global_similarity=round(similarity, 3),
        regional_similarity=round(sum(regions.values()) / len(regions), 3),
        worst_region_similarity=round(worst, 3),
        region_decent_pass_rate=round(
            sum(1 for value in regions.values() if value >= _THRESHOLD) / len(regions), 3
        ),
        passed=passed,
        fidelity_tier="passed" if passed else "failed",
        expected_region_count=len(regions),
        available_region_count=len(regions),
        region_evidence_status="present",
        actual_path="visual/actual.png",
        reference_path="visual/reference.png",
        diff_path="visual/diff.png",
        capture_succeeded=True,
        regional_scores=[
            {
                "name": name,
                "similarity": round(value, 3),
                "threshold": _THRESHOLD,
                "passed": value >= _THRESHOLD,
                "actual_path": f"visual/actual-region-{name}.png",
                "reference_path": f"visual/reference-region-{name}.png",
                "diff_path": f"visual/diff-region-{name}.png",
            }
            for name, value in regions.items()
        ],
    )


def _visual_metric_scores(similarity: float, passed: bool) -> list[MetricScore]:
    return [
        MetricScore(metric_id="functional", score=1.0, passed=True),
        MetricScore(
            metric_id="visual-regression",
            score=round(similarity, 3),
            passed=passed,
            evidence=f"odiff similarity {similarity:.3f} vs threshold {_THRESHOLD:.2f}",
            missing_patterns=[] if passed else ["hero spacing and palette match"],
        ),
        MetricScore(metric_id="verification-stability", score=1.0, passed=True),
        MetricScore(metric_id="resource-efficiency", score=0.9, passed=True),
    ]


def _visual_trace_events(passed: bool) -> list[TraceEvent]:
    def at(offset: float) -> str:
        return datetime(2026, 1, 1, 0, 0, int(offset), tzinfo=UTC).isoformat()

    note = (
        "Hero, features, and footer match the reference layout."
        if passed
        else "Hero spacing drifts from the reference; gradient palette is off."
    )
    return [
        TraceEvent(
            timestamp=at(0),
            event_type="user_prompt",
            data={"content": "Replicate the homepage reference design pixel-faithfully."},
        ),
        TraceEvent(
            timestamp=at(5), event_type="file_change", data={"file_path": "src/components/Hero.tsx"}
        ),
        TraceEvent(
            timestamp=at(9),
            event_type="file_change",
            data={"file_path": "src/components/Features.tsx"},
        ),
        TraceEvent(
            timestamp=at(13),
            event_type="file_change",
            data={"file_path": "src/components/Footer.tsx"},
        ),
        TraceEvent(timestamp=at(17), event_type="bash_command", data={"command": "bun run build"}),
        TraceEvent(
            timestamp=at(20), event_type="gate_result", data={"status": "completed", "exit_code": 0}
        ),
        TraceEvent(
            timestamp=at(24), event_type="bash_command", data={"command": "bun run screenshot"}
        ),
        TraceEvent(
            timestamp=at(27), event_type="gate_result", data={"status": "completed", "exit_code": 0}
        ),
        TraceEvent(timestamp=at(30), event_type="assistant_message", data={"content": note}),
    ]


# --- tiny PNG rendering (no imaging dependency) -----------------------------


def _png_bytes(width: int, height: int, row_rgb) -> bytes:
    raw = b"".join(b"\x00" + row_rgb(y) for y in range(height))

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _solid_row(rgb: tuple[int, int, int]) -> bytes:
    return bytes(rgb) * _IMAGE_WIDTH


def _drift(rgb: tuple[int, int, int], similarity: float) -> tuple[int, int, int]:
    shift = int((1.0 - similarity) * 180)
    red, green, blue = rgb
    return (min(red + shift, 255), max(green - shift // 2, 0), blue)


def _page_row(y: int, *, similarity: float | None) -> bytes:
    fraction = y / _IMAGE_HEIGHT
    for _name, start, end, rgb in _REGIONS:
        if start <= fraction < end or end == 1.0 and fraction >= start:
            return _solid_row(rgb if similarity is None else _drift(rgb, similarity))
    return _solid_row((0, 0, 0))


def _diff_row(y: int, *, similarity: float) -> bytes:
    fraction = y / _IMAGE_HEIGHT
    mismatch_band = 1.0 - similarity
    return _solid_row((200, 30, 30) if fraction < mismatch_band else (8, 8, 8))


def _write_page_assets(visual_dir: Path, similarity: float) -> None:
    (visual_dir / "reference.png").write_bytes(
        _png_bytes(_IMAGE_WIDTH, _IMAGE_HEIGHT, lambda y: _page_row(y, similarity=None))
    )
    (visual_dir / "actual.png").write_bytes(
        _png_bytes(_IMAGE_WIDTH, _IMAGE_HEIGHT, lambda y: _page_row(y, similarity=similarity))
    )
    (visual_dir / "diff.png").write_bytes(
        _png_bytes(_IMAGE_WIDTH, _IMAGE_HEIGHT, lambda y: _diff_row(y, similarity=similarity))
    )


def _write_region_assets(visual_dir: Path, name: str, similarity: float) -> None:
    rgb = next(band_rgb for band_name, _s, _e, band_rgb in _REGIONS if band_name == name)
    (visual_dir / f"reference-region-{name}.png").write_bytes(
        _png_bytes(_IMAGE_WIDTH, _REGION_HEIGHT, lambda _y: _solid_row(rgb))
    )
    (visual_dir / f"actual-region-{name}.png").write_bytes(
        _png_bytes(_IMAGE_WIDTH, _REGION_HEIGHT, lambda _y: _solid_row(_drift(rgb, similarity)))
    )
    mismatch_rows = int((1.0 - similarity) * _REGION_HEIGHT)
    (visual_dir / f"diff-region-{name}.png").write_bytes(
        _png_bytes(
            _IMAGE_WIDTH,
            _REGION_HEIGHT,
            lambda y: _solid_row((200, 30, 30) if y < mismatch_rows else (8, 8, 8)),
        )
    )
