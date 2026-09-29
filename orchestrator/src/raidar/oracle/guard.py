"""Pre-agent leak check: the half of the leak assertion ``verify()`` cannot do.

In Harbor 0.23's shared multi-step mode, Harbor empties ``/tests`` right
before it calls ``verify()``, so a hidden test baked into the agent image (or
placed by a step's setup) is gone by then, even though the agent could read
it. ``install_leak_guard`` adds an ``AGENT_START`` hook that runs the same scan
immediately before each agent phase and stores the report host-side, where
``RaidarOracleVerifier`` requires it (fail closed) and folds it into the step's
leak verdict. A scan that cannot complete raises, so the agent phase never
starts on an unchecked environment.

Usage (trial runner)::

    trial = await Trial.create(config)
    install_leak_guard(trial, oracle_bundle)
    await trial.run()
"""

from __future__ import annotations

from pathlib import Path

from harbor.trial.hooks import TrialEvent, TrialHookEvent
from harbor.trial.trial import Trial

from raidar.oracle.bundle import OracleBundle, load_bundle
from raidar.oracle.leak import scan_agent_environment
from raidar.oracle.records import step_record_dir, write_pre_agent_leak


def current_step(event: TrialHookEvent, bundle: OracleBundle) -> str:
    """The step whose agent phase is starting (Harbor appends its StepResult first)."""
    steps = event.result.step_results
    return bundle.resolve_step(steps[-1].step_name if steps else None)


def install_leak_guard(trial: Trial, oracle_bundle: str | Path) -> None:
    bundle = load_bundle(Path(oracle_bundle))

    async def before_agent(event: TrialHookEvent) -> None:
        step = current_step(event, bundle)
        report = await scan_agent_environment(trial.agent_environment, bundle)
        write_pre_agent_leak(step_record_dir(trial.paths.trial_dir, step), report)

    trial.add_hook(TrialEvent.AGENT_START, before_agent)
