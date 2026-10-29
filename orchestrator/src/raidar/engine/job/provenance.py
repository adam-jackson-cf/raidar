"""What a job records about the code and limits it ran under (pooling gate inputs).

``job.json`` carries ``runner`` (the Raidar checkout's commit and dirty flag, and the
spec/lock toolchain digest a lock records as ``toolchain.raidar_spec``, plus a separate
semantic execution-code digest), and ``effective_limits`` (the per-phase timeouts
the trials were configured with). Recipe identities live in ``job.recipe``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harbor.models.task.config import EnvironmentConfig as TaskEnvironmentConfig
from harbor.models.trial.config import TrialConfig
from harbor.trial.trial import Trial

from raidar.contract.canonical import digest_json
from raidar.contract.source_digest import SOURCE_DIGEST_SCHEME, python_source_digest
from raidar.design.experiment.lock import spec_source_digest
from raidar.engine.job.detach import git_state
from raidar.engine.trial.config import verifier_timeout_s
from raidar.engine.trial.plan import ExperimentPlan

# Changes here never reach a trial, so they do not make the runner dirty (the
# user keeps untracked notes under docs/).
RUNNER_DIRTY_EXCLUDES = ("docs",)


def execution_source_digest(source_root: Path | None = None) -> str:
    """Semantic identity of the installed Raidar code used to execute a job.

    Cover the whole package conservatively: execution imports owners outside
    ``engine``, and the experiment-spec digest intentionally covers only lock
    rules. Relative paths make the identity independent of installation location;
    normalized syntax trees exclude comments and docstrings.
    """
    root = source_root or Path(__file__).resolve().parents[2]
    modules = {
        path.relative_to(root).as_posix(): python_source_digest(path)
        for path in sorted(root.rglob("*.py"))
    }
    if not modules:
        raise ValueError(f"no Raidar Python source found under {root}")
    return digest_json({"scheme": SOURCE_DIGEST_SCHEME, "modules": modules})


def runner_provenance() -> dict[str, Any]:
    """Record git state, lock-spec identity and semantic execution-code identity."""
    state = git_state(Path(__file__).resolve().parents[2], exclude=RUNNER_DIRTY_EXCLUDES)
    return {
        "commit": state["commit"],
        "dirty": state["dirty"],
        "toolchain_sha256": spec_source_digest(),
        "execution_sha256": execution_source_digest(),
    }


def setup_limit(override: float | None) -> float:
    """The agent-setup limit a trial runs with before multipliers: the arm's override, else
    Harbor's own default, so H5 always has a limit to measure against."""
    return float(Trial._AGENT_SETUP_TIMEOUT_SEC if override is None else override)  # noqa: SLF001


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
        "agent_setup_s": {
            arm: setup_limit(config.override_setup_timeout_sec) for arm, config in arms.items()
        },
        "verifier_s": verifier_timeout_s(plan.loaded.spec.protocol),
        "environment_build_s": TaskEnvironmentConfig().build_timeout_sec,
        "multipliers": multipliers,
    }
