"""Run one A' trial through Harbor's Python API and emit timing + check JSON.

Reusable by task 0.4 (overhead baseline): every run writes
``<out>`` = {config, harbor_timing_info, raidar_phases, hook_events, checks}.

Examples (orchestrator venv python; OrbStack only)::

    PY=../../orchestrator/.venv/bin/python
    $PY run_trial.py --task recli-8-continue --agent reference --skip-steps checkpoint_3
    $PY run_trial.py --task recli-3-codex --agent codex --model openai/gpt-6-luna --reasoning low

The codex agent authenticates with the host's existing Codex OAuth/session
``auth.json`` (resolved by ``raidar.codex_auth``); its path is passed through
``AGENTIC_EVAL_SECRET_FILE_CODEX_AUTH_JSON`` and never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DOCKER_CONTEXT = "orbstack"
UV_CACHE_VOLUME = "raidar-spike-uv-cache"


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "--context", DOCKER_CONTEXT, *args], capture_output=True, text=True, check=check
    )


def preflight() -> dict:
    status = subprocess.run(["orbstack-data-status"], capture_output=True, text=True)
    if "external_available=True" not in status.stdout:
        raise SystemExit("blocked: OrbStack external data dir unavailable (orbstack-data-status)")
    os.environ["DOCKER_CONTEXT"] = DOCKER_CONTEXT
    _docker("volume", "create", UV_CACHE_VOLUME)
    return {"orbstack_external_available": True, "docker_context": DOCKER_CONTEXT}


def ensure_oracle_image(oracle_dir: Path, problem: str) -> dict:
    dockerfile = oracle_dir / "environment" / "Dockerfile"
    tag = (
        f"raidar-spike/{problem}-oracle:{hashlib.sha256(dockerfile.read_bytes()).hexdigest()[:12]}"
    )
    present = _docker("image", "inspect", tag, check=False).returncode == 0
    started = time.perf_counter()
    if not present:
        _docker("build", "-t", tag, str(dockerfile.parent))
    return {"image": tag, "cold": not present, "build_s": time.perf_counter() - started}


def codex_auth_env() -> None:
    sys.path.insert(0, str(HERE.parent.parent / "orchestrator" / "src"))
    from raidar.codex_auth import resolve_codex_auth  # noqa: PLC0415

    auth = resolve_codex_auth()
    if auth.resolved_mode != "chatgpt" or auth.auth_json_path is None:
        raise SystemExit("blocked: host Codex OAuth/session auth.json not available")
    os.environ["AGENTIC_EVAL_SECRET_FILE_CODEX_AUTH_JSON"] = str(auth.auth_json_path)


async def run(args: argparse.Namespace) -> dict:
    from harbor.models.trial.config import (  # noqa: PLC0415
        AgentConfig,
        EnvironmentConfig,
        TaskConfig,
        TrialConfig,
        VerifierConfig,
    )
    from harbor.trial.hooks import TrialEvent  # noqa: PLC0415
    from harbor.trial.trial import Trial  # noqa: PLC0415

    from raidar_spike import report  # noqa: PLC0415

    work = args.work.resolve()
    task_dir = work / "tasks" / args.task
    oracle_dir = work / "oracle" / args.task
    manifest = json.loads((oracle_dir / "manifest.json").read_text())

    env_info = preflight()
    image = ensure_oracle_image(oracle_dir, manifest["problem"])

    plant_step = None if args.no_plant else manifest["steps"][0]
    common = {"oracle_dir": str(oracle_dir), "plant_step": plant_step}
    if args.agent == "codex":
        codex_auth_env()
        agent = AgentConfig(
            import_path="raidar_spike.agents:SpikeCodexAgent",
            model_name=args.model,
            kwargs={**common, "reasoning_effort": args.reasoning},
        )
    else:
        agent = AgentConfig(
            import_path="raidar_spike.agents:ReferenceSolutionAgent",
            model_name=None,
            kwargs={**common, "skip_steps": args.skip_steps, "leak_step": args.leak_step},
        )
    trial_name = (
        args.trial_name or f"{args.task}-{args.agent}-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    )
    trials_dir = work / "trials"
    config = TrialConfig(
        task=TaskConfig(path=task_dir),
        trial_name=trial_name,
        trials_dir=trials_dir,
        agent=agent,
        environment=EnvironmentConfig(
            type="docker", delete=args.delete, force_build=args.force_build
        ),
        verifier=VerifierConfig(
            import_path="raidar_spike.verifier:RaidarOracleVerifier",
            kwargs={
                "oracle_dir": str(oracle_dir),
                "oracle_image": image["image"],
                "docker_context": DOCKER_CONTEXT,
                "uv_cache_volume": UV_CACHE_VOLUME,
                "snapshot_source": args.snapshot_source,
                "work_root": str(work / "oracle-tmp"),
            },
        ),
    )

    events: list[dict] = []

    async def hook(event) -> None:  # TrialHookEvent
        events.append({"event": event.event.value, "at": event.timestamp.isoformat()})

    wall = time.perf_counter()
    trial = await Trial.create(config)
    for event in TrialEvent:
        trial.add_hook(event, hook)
    result = await trial.run()
    wall_s = time.perf_counter() - wall

    result_json = json.loads(result.model_dump_json())
    trial_dir = trials_dir / trial_name
    out = {
        "kind": "raidar-a-prime-trial",
        "trial_name": trial_name,
        "trial_dir": str(trial_dir),
        "task": args.task,
        "agent": args.agent,
        "model": args.model if args.agent == "codex" else None,
        "reasoning_effort": args.reasoning if args.agent == "codex" else None,
        "progression": manifest["progression"],
        "skip_steps": args.skip_steps,
        "snapshot_source": args.snapshot_source,
        "environment": env_info,
        "oracle_image": image,
        "wall_s": wall_s,
        **report.timings(result_json, trial_dir, events),
        "checks": report.checks(result_json, trial_dir, manifest, plant_step),
    }
    out_path = args.out or (work / "results" / f"{trial_name}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(
        json.dumps(
            {
                "out": str(out_path),
                "steps": out["checks"]["steps_executed"],
                "rewards": {
                    k: (v or {}).get("strict_pass_rate")
                    for k, v in out["checks"]["rewards"].items()
                },
                "check1": out["checks"]["check1_host_verifier_no_tests_in_env"]["ok"],
                "check2": out["checks"]["check2_context_isolation"]["ok"],
                "check3": out["checks"]["check3_artifacts_on_host"]["ok"],
                "exception": out["checks"]["exception"],
                "wall_s": round(wall_s, 1),
            },
            indent=2,
        )
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one A' spike trial")
    parser.add_argument("--task", required=True, help="task name under _work/tasks")
    parser.add_argument("--agent", choices=("reference", "codex"), default="reference")
    parser.add_argument("--model", default="openai/gpt-6-luna")
    parser.add_argument("--reasoning", default="low")
    parser.add_argument("--skip-steps", nargs="*", default=[])
    parser.add_argument("--no-plant", action="store_true")
    parser.add_argument(
        "--leak-step", help="negative control: leak a hidden test file on this step"
    )
    parser.add_argument("--snapshot-source", choices=("download", "artifact"), default="download")
    parser.add_argument("--trial-name")
    parser.add_argument("--work", type=Path, default=HERE / "_work")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--delete", action="store_true", help="delete env images after the trial (cold runs)"
    )
    parser.add_argument("--force-build", action="store_true")
    args = parser.parse_args()
    if args.agent == "codex" and (
        args.model.split("/")[-1] != "gpt-6-luna" or args.reasoning != "low"
    ):
        raise SystemExit("model policy: real-agent trials must use gpt-6-luna at reasoning low")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
