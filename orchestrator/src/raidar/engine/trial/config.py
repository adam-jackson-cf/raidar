"""The Harbor ``TrialConfig`` of one planned trial (plan D2).

Wiring (ADR-001 Appendix A.1): the Raidar verifier is selected per trial
through ``TrialConfig.verifier`` (it is not a ``task.toml`` setting in Harbor
0.23), pointed at the problem's host-only verifier bundle, grading Harbor's host
copy of ``/app`` (accepted N1 decision ``snapshot_source=artifact``) with the
separate coverage pass on, unless the protocol defers it
(``verifier_coverage: offline``: assessment makes it from the stored snapshots,
``raidar.grading.coverage_offline``). A problem whose lock pins an errata passes it to the
verifier, which records the errata-applied verdict beside the raw grade.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from harbor.models.trial.config import EnvironmentConfig, TaskConfig, TrialConfig, VerifierConfig

from raidar.contract.trial import PlannedTrial
from raidar.design.experiment.schema import ProtocolSpec
from raidar.design.problem.rewrite import VERIFIER_IMPORT_PATH
from raidar.engine.trial.agent_config import with_host_auth
from raidar.engine.trial.environment import RUN_SCOPED_DOCKER_IMPORT_PATH, SCOPE_KWARG, run_scope
from raidar.engine.trial.plan import ExperimentPlan

# Harbor's verifier phase covers the grading run and the coverage run (each
# bounded by grader_timeout_s) plus a grader-image presence check or build.
VERIFIER_PHASE_MARGIN_S = 600


def verifier_timeout_s(protocol: ProtocolSpec) -> float:
    """Harbor's verifier phase: the grading run (and the coverage run) plus the margin."""
    runs = 2 if protocol.verifier_coverage == "trial" else 1
    return runs * float(protocol.verifier_timeout_sec) + VERIFIER_PHASE_MARGIN_S


def harbor_trial_config(
    plan: ExperimentPlan,
    trial: PlannedTrial,
    task: Path,
    *,
    trials_dir: Path,
    bundle_root: Path,
    docker_context: str,
    errata: Mapping[str, Any] | None = None,
) -> TrialConfig:
    spec = plan.loaded.spec
    harness = spec.agent(next(a.agent for a in spec.arms if a.id == trial.arm)).harness
    grader_timeout = float(spec.protocol.verifier_timeout_sec)
    coverage = spec.protocol.verifier_coverage == "trial"
    return TrialConfig(
        task=TaskConfig(path=task),
        trial_name=trial.trial_id,
        trials_dir=trials_dir,
        agent=with_host_auth(plan.arms[trial.arm].agent_config, harness),
        environment=EnvironmentConfig(
            type="docker",
            import_path=RUN_SCOPED_DOCKER_IMPORT_PATH,
            delete=True,
            kwargs={SCOPE_KWARG: run_scope(trials_dir)},
        ),
        verifier=VerifierConfig(
            import_path=VERIFIER_IMPORT_PATH,
            override_timeout_sec=verifier_timeout_s(spec.protocol),
            kwargs={
                "oracle_bundle": str(bundle_root),
                "docker_context": docker_context,
                "snapshot_source": spec.protocol.snapshot_source,
                "coverage": coverage,
                "grader_timeout_s": grader_timeout,
                **({"errata": dict(errata)} if errata is not None else {}),
            },
        ),
    )
