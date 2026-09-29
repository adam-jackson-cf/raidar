"""Overhead baseline runner (plan task 0.4), built on the A' spike harness.

Runs N reps of a no-LLM A' trial per cell on OrbStack and writes one JSON per
rep under ``_work/results/<cell>/``. Agent and oracle execution are recorded
but excluded from the budgets (requirements N1).

Cells:
  cold          env image absent and BuildKit layer cache busted (a unique
                ``RUN`` after ``FROM``), oracle image removed and rebuilt with
                ``--no-cache``; base images stay local (a registry pull is
                network, not framework); ``delete=True`` removes the image.
  warm          image layers cached, snapshot downloaded from the env.
  warm-artifact as warm, but the verifier grades Harbor's artifact copy
                (``--snapshot-source artifact``) instead of re-downloading.
  warm-init     as warm, plus a task compose override ``init: true`` so the
                env container stops on SIGTERM instead of the 10 s grace.
  pack-warm     WS-I I1: the task environment is bound to the prebuilt Python
                pack image (Harbor ``docker_image``, no Dockerfile) with the
                pack's ``init: true`` compose override; snapshots come from
                the artifact copy (the accepted verifier default).

Usage (orchestrator venv python; OrbStack only)::

    PY=../../orchestrator/.venv/bin/python
    $PY bench.py --cell warm --reps 10
    $PY bench.py --cell cold --reps 10
    $PY summarize.py
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
APRIME = HERE.parent / "a-prime"
sys.path.insert(0, str(APRIME))
sys.path.insert(0, str(HERE))

import run_trial  # noqa: E402  (a-prime harness: preflight, oracle image, docker helper)
from raidar_spike import report  # noqa: E402
from raidar_spike.verifier import tree_digest  # noqa: E402

WORK = HERE / "_work"
SOURCE_TASK = "recli-2-continue"


def _images() -> set[str]:
    out = run_trial._docker("images", "--format", "{{.Repository}}:{{.Tag}}").stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def _running_containers() -> int:
    out = run_trial._docker("ps", "-q").stdout
    return len([line for line in out.splitlines() if line.strip()])


def _prepare_task(cell: str, rep: int) -> Path:
    src = APRIME / "_work" / "tasks" / SOURCE_TASK
    if not src.is_dir():
        raise SystemExit(f"missing {src}: build it with spikes/a-prime/build_task.py")
    if cell == "warm-init":
        dest = WORK / "tasks" / f"{SOURCE_TASK}-init"
        if not dest.is_dir():
            shutil.copytree(src, dest)
        # Harbor's main service runs `sh -c "sleep infinity"` as PID 1, which
        # ignores SIGTERM, so `compose down` waits the full 10 s stop grace.
        # A task compose override with `init: true` lets SIGTERM stop it.
        (dest / "environment" / "docker-compose.yaml").write_text(
            "services:\n  main:\n    init: true\n"
        )
        return dest
    if cell == "pack-warm":
        return _prepare_pack_task(src)
    if cell != "cold":
        return src
    dest = WORK / "tasks" / f"{SOURCE_TASK}-cold-{rep:02d}"
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest)
    dockerfile = dest / "environment" / "Dockerfile"
    lines = dockerfile.read_text().splitlines()
    bust = f"RUN echo {uuid.uuid4().hex} > /etc/raidar-cold-bust"
    idx = next(i for i, line in enumerate(lines) if line.startswith("FROM "))
    lines.insert(idx + 1, bust)
    dockerfile.write_text("\n".join(lines) + "\n")
    return dest


def _pack_image_record(task_dir: Path) -> dict | None:
    """Name the exact pack image a pack-warm rep ran on (tag and image ID)."""
    import tomllib  # noqa: PLC0415

    from raidar.packs.python import inspect_image  # noqa: PLC0415 (orchestrator venv)

    tag = tomllib.loads((task_dir / "task.toml").read_text())["environment"].get("docker_image")
    record = inspect_image(tag) if tag else None
    return {"tag": tag, "image_id": record.image_id if record else None} if tag else None


def _prepare_pack_task(src: Path) -> Path:
    from raidar.packs.python import (  # noqa: PLC0415 (orchestrator venv)
        bind_task_environment,
        image_present,
        load_pack_spec,
    )

    spec = load_pack_spec()
    dest = WORK / "tasks" / f"{SOURCE_TASK}-pack"
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest)
    (dest / "environment" / "Dockerfile").unlink()
    fields = bind_task_environment(dest / "environment", spec)
    if not image_present(fields["docker_image"]):
        raise SystemExit(f"missing {fields['docker_image']}: run `make pack-build RUNTIME=python`")
    toml = dest / "task.toml"
    lines = toml.read_text().splitlines()
    idx = lines.index("[environment]")
    lines.insert(idx + 1, f"docker_image = {json.dumps(fields['docker_image'])}")
    toml.write_text("\n".join(lines) + "\n")
    return dest


def _cold_oracle_image(oracle_dir: Path, problem: str) -> dict:
    dockerfile = oracle_dir / "environment" / "Dockerfile"
    tag = (
        f"raidar-spike/{problem}-oracle:{hashlib.sha256(dockerfile.read_bytes()).hexdigest()[:12]}"
    )
    run_trial._docker("image", "rm", "-f", tag, check=False)
    started = time.perf_counter()
    run_trial._docker("build", "--no-cache", "-t", tag, str(dockerfile.parent))
    return {"image": tag, "cold": True, "build_s": time.perf_counter() - started}


def _scorer_orchestration_stub(trial_dir: Path, image: str, steps: list[str]) -> dict:
    """Closest stand-in for Raidar scorer orchestration (not built yet).

    Per step: spin up one network-less scorer container on the step's host
    artifact copy (the mechanical cost of a containerised scorer, doing no
    scoring work), then a host-side pass that digests the artifact and writes
    a per-step score record.
    """
    per_step = []
    for step in steps:
        app = trial_dir / "steps" / step / "artifacts" / "app"
        t = time.perf_counter()
        proc = run_trial._docker(
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{app}:/app:ro",
            image,
            "python",
            "-c",
            "import os; print(sum(len(f) for _, _, f in os.walk('/app')))",
            check=False,
        )
        container_s = time.perf_counter() - t
        t = time.perf_counter()
        record = {
            "step": step,
            "digest": tree_digest(app) if app.is_dir() else None,
            "files_seen_in_container": proc.stdout.strip(),
            "rc": proc.returncode,
        }
        out = trial_dir / "steps" / step / "raidar-score-stub.json"
        out.write_text(json.dumps(record))
        host_s = time.perf_counter() - t
        per_step.append(
            {"step": step, "container_s": container_s, "host_s": host_s, "rc": proc.returncode}
        )
    return {"steps": per_step, "total_s": sum(s["container_s"] + s["host_s"] for s in per_step)}


def _component_apply(trial_dir: Path) -> dict | None:
    for path in sorted(trial_dir.rglob("raidar-component-apply.json")):
        return json.loads(path.read_text())
    return None


async def _run_one(cell: str, rep: int, oracle_dir: Path, manifest: dict) -> dict:
    from harbor.models.trial.config import (  # noqa: PLC0415
        AgentConfig,
        EnvironmentConfig,
        TaskConfig,
        TrialConfig,
        VerifierConfig,
    )
    from harbor.trial.hooks import TrialEvent  # noqa: PLC0415
    from harbor.trial.trial import Trial  # noqa: PLC0415

    cold = cell == "cold"
    task_dir = _prepare_task(cell, rep)
    images_before = _images()
    if cold:
        image = _cold_oracle_image(oracle_dir, manifest["problem"])
    else:
        image = run_trial.ensure_oracle_image(oracle_dir, manifest["problem"])
    trial_name = f"ovh-{cell}-{rep:02d}-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    trials_dir = WORK / "trials"
    config = TrialConfig(
        task=TaskConfig(path=task_dir),
        trial_name=trial_name,
        trials_dir=trials_dir,
        agent=AgentConfig(
            import_path="overhead_spike.components:OverheadReferenceAgent",
            model_name=None,
            kwargs={"oracle_dir": str(oracle_dir), "plant_step": None},
        ),
        environment=EnvironmentConfig(type="docker", delete=cold, force_build=False),
        verifier=VerifierConfig(
            import_path="overhead_spike.components:OverheadVerifier",
            kwargs={
                "oracle_dir": str(oracle_dir),
                "oracle_image": image["image"],
                "docker_context": run_trial.DOCKER_CONTEXT,
                "uv_cache_volume": run_trial.UV_CACHE_VOLUME,
                "snapshot_source": (
                    "artifact" if cell in {"warm-artifact", "pack-warm"} else "download"
                ),
                "work_root": str(WORK / "oracle-tmp"),
            },
        ),
    )
    events: list[dict] = []

    async def hook(event) -> None:
        events.append({"event": event.event.value, "at": event.timestamp.isoformat()})

    load = _running_containers()
    wall = time.perf_counter()
    trial = await Trial.create(config)
    for event in TrialEvent:
        trial.add_hook(event, hook)
    result = await trial.run()
    wall_s = time.perf_counter() - wall

    result_json = json.loads(result.model_dump_json())
    trial_dir = trials_dir / trial_name
    timing = report.timings(result_json, trial_dir, events)
    steps = [s["step_name"] for s in result_json.get("step_results") or []]
    scorer = _scorer_orchestration_stub(trial_dir, image["image"], steps)
    out = {
        "kind": "raidar-overhead-rep",
        "cell": cell,
        "rep": rep,
        "trial_name": trial_name,
        "task_dir": str(task_dir),
        "oracle_image": image,
        "pack_image": _pack_image_record(task_dir) if cell == "pack-warm" else None,
        "running_containers_at_start": load,
        "wall_s": wall_s,
        **timing,
        "component_apply_stub": _component_apply(trial_dir),
        "scorer_orchestration_stub": scorer,
        "rewards": {
            s["step_name"]: (s.get("verifier_result") or {}).get("rewards")
            for s in result_json.get("step_results") or []
        },
        "exception": (result_json.get("exception_info") or {}).get("exception_message"),
    }
    out_path = WORK / "results" / cell / f"rep-{rep:02d}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    t = time.perf_counter()
    out_path.write_text(json.dumps(out, indent=2, default=str))
    out["raidar_result_write_s"] = time.perf_counter() - t
    out_path.write_text(json.dumps(out, indent=2, default=str))

    new_images = sorted(_images() - images_before)
    if cold:
        # delete=True already ran `compose down --rmi local`; remove leftovers.
        for tag in new_images:
            if "env-main" in tag or tag.startswith("hb__"):
                run_trial._docker("image", "rm", "-f", tag, check=False)
        shutil.rmtree(task_dir, ignore_errors=True)
    out["new_images"] = new_images
    out_path.write_text(json.dumps(out, indent=2, default=str))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Raidar overhead baseline (task 0.4)")
    parser.add_argument(
        "--cell",
        choices=("cold", "warm", "warm-artifact", "warm-init", "pack-warm"),
        required=True,
    )
    parser.add_argument("--reps", type=int, default=10)
    parser.add_argument("--start", type=int, default=1)
    args = parser.parse_args()

    run_trial.preflight()
    oracle_dir = APRIME / "_work" / "oracle" / SOURCE_TASK
    manifest = json.loads((oracle_dir / "manifest.json").read_text())
    for rep in range(args.start, args.start + args.reps):
        t = time.perf_counter()
        out = asyncio.run(_run_one(args.cell, rep, oracle_dir, manifest))
        print(
            json.dumps(
                {
                    "cell": args.cell,
                    "rep": rep,
                    "env_setup_s": out["harbor_timing_info"]["environment_setup_s"],
                    "rewards": out["rewards"],
                    "exception": out["exception"],
                    "elapsed_s": round(time.perf_counter() - t, 1),
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
