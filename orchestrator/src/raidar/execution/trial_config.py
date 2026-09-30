"""The Harbor ``TrialConfig`` of one planned trial (plan D2).

Wiring (ADR-001 Appendix A.1): the Raidar oracle verifier is selected per trial
through ``TrialConfig.verifier`` (it is not a ``task.toml`` setting in Harbor
0.23), pointed at the problem's host-only oracle bundle, grading Harbor's host
copy of ``/app`` (accepted N1 decision ``snapshot_source=artifact``) with the
separate coverage pass on.
"""

from __future__ import annotations

from pathlib import Path

from harbor.models.trial.config import EnvironmentConfig, TaskConfig, TrialConfig, VerifierConfig

from raidar.execution.agent_config import with_host_auth
from raidar.execution.models import PlannedTrial
from raidar.execution.plan import ExperimentPlan
from raidar.problems.rewrite import ORACLE_VERIFIER_IMPORT_PATH

# Harbor's verifier phase covers the grading run and the coverage run (each
# bounded by grader_timeout_s) plus a grader-image presence check or build.
VERIFIER_PHASE_MARGIN_S = 600


def harbor_trial_config(
    plan: ExperimentPlan,
    trial: PlannedTrial,
    task: Path,
    *,
    trials_dir: Path,
    bundle_root: Path,
    docker_context: str,
) -> TrialConfig:
    spec = plan.loaded.spec
    harness = spec.agent(next(a.agent for a in spec.arms if a.id == trial.arm)).harness
    grader_timeout = float(spec.protocol.verifier_timeout_sec)
    return TrialConfig(
        task=TaskConfig(path=task),
        trial_name=trial.trial_id,
        trials_dir=trials_dir,
        agent=with_host_auth(plan.arms[trial.arm].agent_config, harness),
        environment=EnvironmentConfig(type="docker", delete=True),
        verifier=VerifierConfig(
            import_path=ORACLE_VERIFIER_IMPORT_PATH,
            override_timeout_sec=2 * grader_timeout + VERIFIER_PHASE_MARGIN_S,
            kwargs={
                "oracle_bundle": str(bundle_root),
                "docker_context": docker_context,
                "snapshot_source": spec.protocol.snapshot_source,
                "coverage": True,
                "grader_timeout_s": grader_timeout,
            },
        ),
    )
