---
name: "experiment-execution"
description: "Use when launching, monitoring or cleaning up a Raidar experiment job on a Docker target."
---

# Task

## Admission

- Read the experiment and inspect its resolved trials with `make experiment-plan EXPERIMENT=<path>`.
- Apply its explore/confirm objective; if not established, use explore.
- Resolve TARGET from explicit selection, RAIDAR_TARGET or the targets file default.
  Check `make target-status TARGET=<name>`; never substitute the current Docker context.
- Send verifier tests and credentials only to a trusted target.
- Require a clean smoke matching toolchain, pack, every arm's agent/model/effort and target.
  Every executed smoke step must change /app; a question-only step does not qualify.
- Never use ALLOW_UNSMOKED=1 for confirm. Record any explicitly requested explore override.
- Relock changed experiments, components or problem pins before execution.
  Give each job a new JOB_ID; never reuse a job directory.

## Launch and monitor

- Launch every real-LLM execution, and any job expected to outlive the shell or turn, detached:
  `make experiment-execute EXPERIMENT=<path> JOB_ID=<new-id> TARGET=<name> DETACH=1`.
- Never use shell backgrounding or session-bound tool background mode for these jobs.
- Monitor to completion with `make experiment-status JOB=<job-dir>`.
  Supervisor pid, log and exit record are under the job's detach/ directory.
- Keep default liveness enforcement unless an opt-out was requested.
  Treat an idle kill as a graded timeout, not an infrastructure exclusion or automatic retry.
- On finished jobs hand off their path and lock for offline assessment.
  On failed/died jobs report persisted status and the failing phase.

## Cleanup and output

- After a stopped job use `make harbor-cleanup JOB=<job-dir>`.
  Use target-prune only for stale Raidar projects; never touch another workload's containers.
- Return job path, target, objective, terminal state and completion/failure evidence.
