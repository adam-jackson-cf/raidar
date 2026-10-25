---
name: "experiment-assessment"
description: "Use when assessing, regrading or pooling stored Raidar jobs into an Assessment store and analysis output."
---

# Task

## Assess

- Select the job, external Assessment store and authoritative experiment lock.
- Use LOCK explicitly when the lock lives elsewhere, including smoke locks under var/tmp/smoke/.
  Otherwise require the current experiment lock to match the job's lock_sha256.
  The job's job.lock.json is not a substitute for assessment settings.
- Run `make experiment-assess JOB=<job-dir> STORE=<store> SCORERS="<locked scorers>" [LOCK=<path>]`.
- Keep scorer and verifier bundles compatible with the job's locked runtime and problem.
- For protocol.verifier_coverage: offline, let code-quality@4 create coverage from stored step snapshots into the store.
- Use `make assess-regrade JOB=<trial-or-job> COVERAGE_CHECK=1 STORE=<store>` to compare coverage reruns.
  Use assess-regrade without COVERAGE_CHECK for verifier/pre-fix regrading as required.
- After assessment-only changes, rescore existing jobs rather than rerunning agents when lock_sha256 still matches.

## Analyze

- Run `make experiment-analyze JOBS="<job-dir> ..." STORE=<store> ANALYSIS_OUT=<dir> [LOCK=<path>]`.
- Before pooling require matching mechanics epoch, toolchain, clean runner, pack/grader recipes,
  subject and effective limits; timing/cost also require the same execution target.
- Apply POOL_OVERRIDE only to explicitly selected fields and report it.
  A records-manifest tamper violation cannot be overridden.
- Preserve the registered primary and report any raw counterpart for errata-applied outcomes.
- Return store and analysis paths, input jobs, lock provenance, refusals/overrides and assessment status.
  For a completed experiment's narrative report use the existing experiment-observations workflow.
