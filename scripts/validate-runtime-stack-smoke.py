#!/usr/bin/env python3
"""Validate persisted evidence from a runtime-stack scenario smoke run."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


def _scenario_identity(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    name = _yaml_scalar(text, "name")
    revision = _yaml_scalar(text, "scenario_revision")
    if not name or not revision:
        raise SystemExit(f"Unable to read scenario identity from {path}")
    return name, revision


def _yaml_scalar(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:\s*['\"]?([^'\"\n]+)['\"]?\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def _experiment_root(root: Path, kind: str) -> Path:
    if kind == "research-loop":
        return root / "research_loops"
    return root / "benchmarks"


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _matching_runs(root: Path, *, scenario_name: str, revision: str, harness: str, model: str):
    expected_model = model if "/" in model else None
    for path in root.glob("**/run.json"):
        payload = _load_json(path)
        if not payload:
            continue
        config = payload.get("config")
        if not isinstance(config, dict):
            continue
        if config.get("scenario_name") != scenario_name:
            continue
        if config.get("scenario_revision") != revision:
            continue
        if config.get("harness") != harness:
            continue
        run_model = config.get("model")
        if expected_model is not None and run_model != expected_model:
            continue
        if expected_model is None and not str(run_model).endswith(f"/{model}"):
            continue
        yield path, payload


def _nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _validate_run(path: Path, payload: dict[str, Any]) -> None:
    metadata = _nested(payload, "scores", "metadata")
    if not isinstance(metadata, dict):
        raise SystemExit(f"Run metadata missing: {path}")
    harbor = metadata.get("harbor")
    if not isinstance(harbor, dict):
        raise SystemExit(f"Harbor metadata missing: {path}")
    cache = harbor.get("cache")
    if not isinstance(cache, dict):
        raise SystemExit(f"Cache metadata missing: {path}")
    contract = cache.get("contract")
    if not isinstance(contract, dict) or not contract.get("id") or not contract.get("hash"):
        raise SystemExit(f"EffectiveRunContract metadata missing: {path}")
    timing = harbor.get("time_to_experiment_start_sec")
    if not isinstance(timing, int | float):
        raise SystemExit(f"time_to_experiment_start_sec missing: {path}")
    if timing >= 10.0:
        raise SystemExit(f"time_to_experiment_start_sec {timing} >= 10.0: {path}")

    image_key = cache.get("image_key")
    image = cache.get("image")
    image_hit = image.get("hit") if isinstance(image, dict) else None
    if image_key and image_hit is not True:
        raise SystemExit(f"Measured run did not report task image cache hit: {path}")

    run_id = payload.get("id")
    experiment_dir = path.parents[2] if len(path.parents) >= 3 else path.parent
    print(
        "runtime-stack-smoke-ok "
        f"run_id={run_id} run_json={path} experiment_dir={experiment_dir} "
        f"contract_id={contract.get('id')} image_hit={image_hit} "
        f"time_to_experiment_start_sec={timing}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--harness", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--experiments-root", required=True, type=Path)
    parser.add_argument("--experiment-kind", required=True)
    args = parser.parse_args()

    scenario_name, revision = _scenario_identity(args.scenario)
    model = args.model if "/" in args.model else f"{args.provider}/{args.model}"
    root = _experiment_root(args.experiments_root, args.experiment_kind)
    runs = sorted(
        _matching_runs(
            root,
            scenario_name=scenario_name,
            revision=revision,
            harness=args.harness,
            model=model,
        ),
        key=lambda item: item[0].stat().st_mtime,
    )
    if not runs:
        raise SystemExit(
            "No persisted run.json found for "
            f"scenario={scenario_name}@{revision} harness={args.harness} model={model}"
        )
    path, payload = runs[-1]
    _validate_run(path, payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
