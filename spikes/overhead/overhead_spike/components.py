"""Overhead-baseline variants of the A' spike agent and verifier (plan task 0.4).

Only two things change relative to ``spikes/a-prime``:

* ``OverheadReferenceAgent.setup`` performs a **component-apply stub**: it
  uploads a representative component bundle (instruction file, config,
  three skills; ``components/fixture``) into the harness baseline dir as root
  and fixes permissions. Raidar's component application does not exist yet;
  this is the closest mechanical equivalent (host -> env transfer + root-side
  install into the baseline that the per-step reset restores from). Timing is
  written host-side to ``agent/raidar-component-apply.json``.
* ``OverheadVerifier`` skips the spike-only leak scan (a 0.3 assertion, not a
  production phase) so ``verifier_s`` is not inflated by it. Everything else
  (snapshot, stage, separate oracle container, result write) is unchanged.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, ClassVar

from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from raidar_spike.agents import ReferenceAgentOptions, ReferenceSolutionAgent
from raidar_spike.verifier import RaidarOracleVerifier

FIXTURE = Path(__file__).resolve().parent.parent / "components" / "fixture"
BASELINE = "/opt/raidar/harness-baseline/codex-home"
STAGING = "/tmp/raidar-components-staging"


class OverheadAgentOptions(ReferenceAgentOptions):
    component_dir: str = str(FIXTURE)


class OverheadReferenceAgent(ReferenceSolutionAgent):
    options_model: ClassVar[type[AgentOptions] | None] = OverheadAgentOptions

    @staticmethod
    def name() -> str:
        return "raidar-overhead-reference"

    async def setup(self, environment: BaseEnvironment) -> None:
        opts: OverheadAgentOptions = self.options  # type: ignore[assignment]
        source = Path(opts.component_dir)
        started = time.perf_counter()
        await environment.exec(f"mkdir -p {STAGING}", user="root")
        upload_t = time.perf_counter()
        await environment.upload_dir(source, STAGING)
        upload_s = time.perf_counter() - upload_t
        install_t = time.perf_counter()
        result = await environment.exec(
            f"mkdir -p {BASELINE} && cp -a {STAGING}/. {BASELINE}/ && "
            f"chmod -R a+rX /opt/raidar && rm -rf {STAGING} && "
            f"find {BASELINE} -type f | wc -l",
            user="root",
        )
        install_s = time.perf_counter() - install_t
        report = {
            "kind": "raidar-component-apply-stub",
            "files": sum(1 for p in source.rglob("*") if p.is_file()),
            "bytes": sum(p.stat().st_size for p in source.rglob("*") if p.is_file()),
            "installed_files_in_env": (result.stdout or "").strip(),
            "return_code": result.return_code,
            "upload_s": upload_s,
            "install_s": install_s,
            "total_s": time.perf_counter() - started,
        }
        target = Path(self.logs_dir) / "raidar-component-apply.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2), encoding="utf-8")


class OverheadVerifier(RaidarOracleVerifier):
    async def _leak_check(self) -> dict[str, Any]:
        return {"skipped": "overhead baseline: spike-only assertion", "clean": True}
