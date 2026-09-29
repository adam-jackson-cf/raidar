"""Build an A'-shaped Harbor task + host-only oracle bundle from a converted SCB task.

Input: a task produced by ``scb-problems/scripts/scb_to_harbor.py`` (Harbor
``[[steps]]`` format with per-step tests/solutions inside the task dir).

Output (under ``--out``):

* ``tasks/<name>/``: what Harbor sees. No ``tests/`` content, no solutions.
  ``tests/test.sh`` is a stub that exits 1 (Harbor 0.23 requires a test script
  for shared-mode steps even when a custom verifier is used; the stub is never
  uploaded because the Raidar verifier does not upload tests). Each step gets
  ``workdir/setup.sh`` = the Raidar per-step reset.
* ``oracle/<problem>/``: the hidden bundle, host-only. Shared tests, per-step
  ``test.sh``, reference payloads, the SCB environment Dockerfile for the oracle
  container, ``instruction_index.json`` and ``leak_tokens.json``.

Usage (orchestrator venv python, because it loads the task through Harbor)::

    python build_task.py --converted _work/converted/scb/recli --name recli-8-continue
    python build_task.py ... --steps 3 --progression halt --agent-timeout 900
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODEX_VERSION = "0.156.1"
STUB_TEST_SH = """#!/bin/bash
# Placeholder: the hidden oracle runs host-side (raidar_spike.verifier).
# Harbor 0.23 requires a test script for shared-mode steps; this file is never
# uploaded to the agent environment.
echo "raidar: hidden oracle is host-side" >&2
exit 1
"""


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {_toml_value(v)}" for k, v in value.items()) + " }"
    raise TypeError(type(value))


def _render_task_toml(
    source: dict,
    *,
    name: str,
    steps: list[str],
    progression: str,
    agent_timeout: float,
    verifier_timeout: float,
    cpus: int,
    memory_mb: int,
) -> str:
    env = source["environment"]
    lines = [
        'schema_version = "1.1"',
        'multi_step_reward_strategy = "mean"',
        "",
        "[task]",
        f"name = {json.dumps('raidar-spike/' + name)}",
        f"description = {json.dumps(source['task'].get('description', ''))}",
        "",
        "[metadata]",
        f"source_problem = {json.dumps(source['metadata']['source_problem'])}",
        'raidar_spike = "a-prime"',
        f"progression = {json.dumps(progression)}",
        "",
        "[environment]",
        f"build_timeout_sec = {env.get('build_timeout_sec', 600.0)}",
        f"cpus = {cpus}",
        f"memory_mb = {memory_mb}",
        f"storage_mb = {env.get('storage_mb', 10240)}",
        "allow_internet = true",
        'workdir = "/app"',
        "",
        "[environment.env]",
    ]
    for key, value in env.get("env", {}).items():
        lines.append(f"{key} = {json.dumps(value)}")
    lines += [
        "",
        "[agent]",
        f"timeout_sec = {agent_timeout}",
        'user = "agent"',
        "",
        "[verifier]",
        f"timeout_sec = {verifier_timeout}",
        'environment_mode = "shared"',
        "",
    ]
    by_name = {s["name"]: s for s in source["steps"]}
    for step in steps:
        artifacts = by_name[step].get("artifacts", [])
        lines += ["[[steps]]", f"name = {json.dumps(step)}"]
        lines.append(f"artifacts = {_toml_value(artifacts)}")
        if progression == "halt":
            lines.append("min_reward = { strict_pass_rate = 1.0 }")
        lines += [
            "",
            "[steps.agent]",
            f"timeout_sec = {agent_timeout}",
            "",
            "[steps.verifier]",
            f"timeout_sec = {verifier_timeout}",
            "",
        ]
    return "\n".join(lines)


def _agent_dockerfile(source_dockerfile: str) -> str:
    return (
        source_dockerfile.rstrip()
        + f"""

# --- Raidar A' spike additions -------------------------------------------
# Harness CLI pinned in-image (codex-cli); Condition baseline for per-step reset.
USER root
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \\
 && rm -rf /var/lib/apt/lists/* \\
 && npm install -g @openai/codex@{CODEX_VERSION} \\
 && mkdir -p /opt/raidar/harness-baseline/codex-home /opt/raidar/harness-baseline/claude-config \\
 && chmod -R a+rX /opt/raidar
WORKDIR /app
"""
    )


_TEST_DEF = re.compile(r"^\s*def (test_[A-Za-z0-9_]{24,})\(", re.MULTILINE)


def _leak_tokens(tests_dir: Path) -> list[str]:
    tokens: list[str] = []
    for path in sorted(tests_dir.glob("test_checkpoint_*.py")):
        tokens.extend(_TEST_DEF.findall(path.read_text())[:3])
    return tokens


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def build(args: argparse.Namespace) -> dict:
    converted: Path = args.converted.resolve()
    source = tomllib.loads((converted / "task.toml").read_text())
    all_steps = [s["name"] for s in source["steps"]]
    steps = all_steps[: args.steps] if args.steps else all_steps
    problem = source["metadata"]["source_problem"]

    task_dir = args.out / "tasks" / args.name
    oracle_dir = args.out / "oracle" / args.name
    for d in (task_dir, oracle_dir):
        if d.exists():
            shutil.rmtree(d)

    # ---- agent-visible task ------------------------------------------------
    (task_dir / "environment").mkdir(parents=True)
    (task_dir / "environment" / "Dockerfile").write_text(
        _agent_dockerfile((converted / "environment" / "Dockerfile").read_text())
    )
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test.sh").write_text(STUB_TEST_SH)
    setup_sh = (HERE / "raidar_spike" / "step_setup.sh").read_text()
    for step in steps:
        step_dir = task_dir / "steps" / step
        (step_dir / "workdir").mkdir(parents=True)
        shutil.copy2(converted / "steps" / step / "instruction.md", step_dir / "instruction.md")
        (step_dir / "workdir" / "setup.sh").write_text(setup_sh)
    (task_dir / "task.toml").write_text(
        _render_task_toml(
            source,
            name=args.name,
            steps=steps,
            progression=args.progression,
            agent_timeout=args.agent_timeout,
            verifier_timeout=args.verifier_timeout,
            cpus=args.cpus,
            memory_mb=args.memory_mb,
        )
    )

    # ---- host-only oracle bundle ------------------------------------------
    shutil.copytree(converted / "tests", oracle_dir / "tests")
    (oracle_dir / "environment").mkdir(parents=True)
    shutil.copy2(
        converted / "environment" / "Dockerfile", oracle_dir / "environment" / "Dockerfile"
    )
    for step in all_steps:
        (oracle_dir / "steps" / step).mkdir(parents=True)
        shutil.copy2(
            converted / "steps" / step / "tests" / "test.sh",
            oracle_dir / "steps" / step / "test.sh",
        )
        shutil.copytree(
            converted / "steps" / step / "solution" / "payload", oracle_dir / "solutions" / step
        )

    from harbor.models.task.task import Task  # noqa: PLC0415 (needs orchestrator venv)

    task = Task(task_dir)
    index = {
        hashlib.sha256(task.step_instruction(step).encode()).hexdigest(): step for step in steps
    }
    (oracle_dir / "instruction_index.json").write_text(json.dumps(index, indent=2))
    tokens = _leak_tokens(oracle_dir / "tests")
    (oracle_dir / "leak_tokens.json").write_text(json.dumps(tokens, indent=2))

    # ---- static isolation assertion ------------------------------------------
    leaked = [
        p.relative_to(task_dir).as_posix()
        for p in task_dir.rglob("*")
        if p.is_file()
        and (
            p.name.startswith("test_checkpoint_")
            or p.name in {"_ctrf_to_reward.py", "conftest.py", "solve.sh"}
            or "solution" in p.parts
        )
    ]
    token_hits = [
        p.relative_to(task_dir).as_posix()
        for p in task_dir.rglob("*")
        if p.is_file() and any(t in p.read_text(errors="ignore") for t in tokens)
    ]
    manifest = {
        "problem": problem,
        "name": args.name,
        "steps": steps,
        "progression": args.progression,
        "task_dir": str(task_dir),
        "oracle_dir": str(oracle_dir),
        "task_digest": _tree_digest(task_dir),
        "oracle_digest": _tree_digest(oracle_dir),
        "source_converted_digest": _tree_digest(converted),
        "codex_version": CODEX_VERSION,
        "static_isolation": {
            "oracle_files_in_task": leaked,
            "oracle_tokens_in_task": token_hits,
            "clean": not leaked and not token_hits,
            "tokens_checked": len(tokens),
        },
    }
    (oracle_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if not manifest["static_isolation"]["clean"]:
        raise SystemExit(f"oracle content found in task dir: {leaked + token_hits}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--converted", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--out", type=Path, default=HERE / "_work")
    parser.add_argument("--steps", type=int, default=0, help="first N steps only (0 = all)")
    parser.add_argument("--progression", choices=("continue", "halt"), default="continue")
    parser.add_argument("--agent-timeout", type=float, default=900.0)
    parser.add_argument("--verifier-timeout", type=float, default=750.0)
    parser.add_argument("--cpus", type=int, default=2)
    parser.add_argument("--memory-mb", type=int, default=4096)
    manifest = build(parser.parse_args())
    print(
        json.dumps(
            {
                k: manifest[k]
                for k in (
                    "name",
                    "steps",
                    "progression",
                    "task_dir",
                    "oracle_dir",
                    "static_isolation",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
