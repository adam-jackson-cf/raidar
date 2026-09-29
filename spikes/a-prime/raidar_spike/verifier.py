"""Raidar oracle verifier (A' spike): host-side ``BaseVerifier``.

Loaded by Harbor through ``verifier.import_path``. It runs in the Harbor host
process and, per step:

1. leak check: asserts (as root in the agent env) that no hidden test file,
   test-only token or ``/tests`` content exists in the agent environment;
2. downloads ``/app`` from the agent environment into a host temp dir;
3. copies the snapshot and the hidden tests (from the host-only oracle bundle)
   into a fresh work dir and runs the SCB-generated ``test.sh`` in a
   **separate** throwaway container (``docker run --rm``) on that copy;
4. reads ``reward.json`` and returns it as the step's ``VerifierResult``.

Nothing from the oracle bundle is ever uploaded to the agent environment.
Phase timings are written to ``<verifier_dir>/raidar_verifier.json``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from harbor.models.verifier.result import VerifierResult
from harbor.verifier.base import BaseVerifier

SNAPSHOT_EXCLUDES = [
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
    "setup.sh",
]


def tree_digest(root: Path) -> str:
    """Content digest of a directory tree (relative path + bytes), order-stable."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if any(part in SNAPSHOT_EXCLUDES for part in Path(rel).parts):
            continue
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class RaidarOracleVerifier(BaseVerifier):
    def __init__(
        self,
        *,
        oracle_dir: str,
        oracle_image: str,
        docker_context: str = "orbstack",
        uv_cache_volume: str = "raidar-spike-uv-cache",
        container_network: str = "bridge",
        snapshot_source: str = "download",
        work_root: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.oracle_dir = Path(oracle_dir)
        self.oracle_image = oracle_image
        self.docker_context = docker_context
        self.uv_cache_volume = uv_cache_volume
        self.container_network = container_network
        self.snapshot_source = snapshot_source
        self.work_root = Path(work_root) if work_root else None
        if self.work_root:
            self.work_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ leak
    async def _leak_check(self) -> dict[str, Any]:
        tokens_path = self.oracle_dir / "leak_tokens.json"
        tokens: list[str] = json.loads(tokens_path.read_text()) if tokens_path.exists() else []
        names = await self.environment.exec(
            "find / -xdev \\( -path /proc -o -path /sys \\) -prune -o "
            "\\( -name 'test_checkpoint_*.py' -o -name '_ctrf_to_reward.py' \\) -print 2>/dev/null | grep -v '/site-packages/' | head -n 50",
            user="root",
            timeout_sec=300,
        )
        tests_dir = await self.environment.exec(
            "if [ -d /tests ]; then find /tests -mindepth 1 | head -n 20; fi", user="root"
        )
        token_hits: dict[str, list[str]] = {}
        if tokens:
            # One pass; -o prints the matched token so hits are attributable.
            patterns = " ".join(f"-e '{token}'" for token in tokens)
            res = await self.environment.exec(
                "grep -rIo -s --exclude-dir=proc --exclude-dir=sys --exclude-dir=dev "
                f"--exclude-dir=usr --exclude-dir=lib -F {patterns} / 2>/dev/null | head -n 50",
                user="root",
                timeout_sec=300,
            )
            for line in (res.stdout or "").splitlines():
                path, _, token = line.rpartition(":")
                if token:
                    token_hits.setdefault(token, []).append(path)
        file_hits = [h for h in (names.stdout or "").splitlines() if h.strip()]
        tests_entries = [h for h in (tests_dir.stdout or "").splitlines() if h.strip()]
        return {
            "hidden_test_files_found": file_hits,
            "tests_dir_entries": tests_entries,
            "oracle_token_hits": token_hits,
            "tokens_checked": len(tokens),
            "clean": not (file_hits or tests_entries or token_hits),
        }

    # -------------------------------------------------------------- snapshot
    async def _snapshot(self, dest: Path) -> None:
        if self.snapshot_source == "artifact":
            src = self.trial_paths.artifacts_dir / "app"
            shutil.copytree(src, dest, dirs_exist_ok=True)
            return
        await self.environment.download_dir_with_exclusions(
            source_dir="/app", target_dir=dest, exclude=SNAPSHOT_EXCLUDES
        )

    # --------------------------------------------------------------- oracle
    def _stage_tests(self, step: str, dest: Path) -> None:
        shutil.copytree(self.oracle_dir / "tests", dest)
        shutil.copy2(self.oracle_dir / "steps" / step / "test.sh", dest / "test.sh")

    async def _run_container(self, app: Path, tests: Path, out: Path) -> dict[str, Any]:
        name = f"raidar-oracle-{os.getpid()}-{int(time.time() * 1000)}"
        cmd = [
            "docker",
            "--context",
            self.docker_context,
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            self.container_network,
            "-v",
            f"{app}:/app",
            "-v",
            f"{tests}:/tests",
            "-v",
            f"{out}:/logs/verifier",
            "-v",
            f"{self.uv_cache_volume}:/tmp/uv-cache",
            self.oracle_image,
            "bash",
            "/tests/test.sh",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        stdout, _ = await proc.communicate()
        (out / "oracle-container.log").write_bytes(stdout or b"")
        return {"container_name": name, "return_code": proc.returncode, "argv": cmd}

    async def verify(self) -> VerifierResult:
        step = self.step_name or "single"
        out_dir = self.trial_paths.verifier_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        timings: dict[str, float] = {}
        report: dict[str, Any] = {"kind": "raidar-oracle-verifier", "step": step}

        t = time.perf_counter()
        report["leak_check"] = await self._leak_check()
        timings["leak_check_s"] = time.perf_counter() - t

        work = Path(tempfile.mkdtemp(prefix=f"raidar-oracle-{step}-", dir=self.work_root))
        try:
            snapshot = work / "snapshot"
            t = time.perf_counter()
            await self._snapshot(snapshot)
            timings["snapshot_transfer_s"] = time.perf_counter() - t
            report["snapshot_source"] = self.snapshot_source
            report["snapshot_digest"] = tree_digest(snapshot)

            t = time.perf_counter()
            app_copy = work / "app"
            shutil.copytree(snapshot, app_copy)
            tests = work / "tests"
            self._stage_tests(step, tests)
            container_out = work / "out"
            container_out.mkdir()
            for path in (app_copy, tests, container_out):
                os.chmod(path, 0o777)
            timings["stage_s"] = time.perf_counter() - t

            t = time.perf_counter()
            report["oracle_container"] = await self._run_container(app_copy, tests, container_out)
            timings["oracle_run_s"] = time.perf_counter() - t

            t = time.perf_counter()
            reward_path = container_out / "reward.json"
            rewards: dict[str, float | int] = {}
            if reward_path.exists():
                rewards = json.loads(reward_path.read_text())
            for name in ("reward.json", "reward_details.json", "oracle-container.log"):
                src = container_out / name
                if src.exists():
                    shutil.copy2(src, out_dir / name)
            timings["result_write_s"] = time.perf_counter() - t
        finally:
            shutil.rmtree(work, ignore_errors=True)

        if not report["leak_check"]["clean"]:
            # A leaked oracle invalidates the step: never report a pass.
            rewards = dict.fromkeys(rewards, 0.0) | {"oracle_leak": 1.0}
        report["rewards"] = rewards
        report["timings"] = timings
        (out_dir / "raidar_verifier.json").write_text(json.dumps(report, indent=2, sort_keys=True))
        return VerifierResult(rewards=rewards)
