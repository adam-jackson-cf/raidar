"""What a job records about the code and limits it ran under (pooling gate inputs).

``job.json`` carries ``runner`` (the Raidar checkout's commit and dirty flag, and the
spec/lock toolchain digest a lock records as ``toolchain.raidar_spec``), ``recipes``
(the pack image recipe hash and each problem's grader recipe digest, never image ids,
which differ per daemon) and ``effective_limits`` (the per-phase timeouts the trials
were configured with).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from harbor.models.task.config import EnvironmentConfig as TaskEnvironmentConfig
from harbor.models.trial.config import TrialConfig

from raidar.execution.detach import git_state
from raidar.execution.plan import ExperimentPlan
from raidar.execution.trial_config import verifier_timeout_s
from raidar.packs import load_runtime_pack
from raidar.packs.python.image import image_lock_hash
from raidar.problems.store import ImportedProblem
from raidar.spec.lock import spec_source_digest
from raidar.verifier.grader_image import grader_image_tag

# Changes here never reach a trial, so they do not make the runner dirty (the
# user keeps untracked notes under docs/).
RUNNER_DIRTY_EXCLUDES = ("docs",)


def runner_provenance() -> dict[str, Any]:
    """The running Raidar code: git commit, dirty flag and the lock's toolchain digest."""
    state = git_state(Path(__file__).resolve().parents[2], exclude=RUNNER_DIRTY_EXCLUDES)
    return {
        "commit": state["commit"],
        "dirty": state["dirty"],
        "toolchain_sha256": spec_source_digest(),
    }


def recipe_digests(plan: ExperimentPlan, problems: Mapping[int, ImportedProblem]) -> dict[str, Any]:
    """The pack image recipe hash and the grader recipe digest of every planned problem."""
    graders = {
        str(target.ref): grader_image_tag(problems[target.index].bundle.manifest.grader).rsplit(
            ":", 1
        )[1]
        for target in plan.problems
        if target.index in problems
    }
    return {
        "pack": image_lock_hash(load_runtime_pack(plan.loaded.spec.runtime)),
        "graders": graders,
    }


def effective_limits(plan: ExperimentPlan) -> dict[str, Any]:
    """The per-phase timeouts (seconds) trials run with: as configured, before multipliers."""
    defaults = TrialConfig.model_fields
    multipliers = {
        name: defaults[name].default
        for name in (
            "timeout_multiplier",
            "agent_timeout_multiplier",
            "verifier_timeout_multiplier",
            "agent_setup_timeout_multiplier",
            "environment_build_timeout_multiplier",
        )
    }
    arms = {arm: blueprint.agent_config for arm, blueprint in plan.arms.items()}
    return {
        "agent_s": {arm: config.override_timeout_sec for arm, config in arms.items()},
        "agent_setup_s": {arm: config.override_setup_timeout_sec for arm, config in arms.items()},
        "verifier_s": verifier_timeout_s(plan.loaded.spec.protocol),
        "environment_build_s": TaskEnvironmentConfig().build_timeout_sec,
        "multipliers": multipliers,
    }
