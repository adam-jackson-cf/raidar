---
name: "raidar-release"
description: "Use when changing a released Raidar agent, workflow, scorer, headline metric or runtime pack, or relocking after toolchain edits."
---

# Task

## Establish release impact

- Identify manifest versions, fingerprint sources and affected experiment locks before editing.
- Preserve released bytes first with `make release-archive REF=<name>@<N>` for each affected agent, workflow or scorer.
  Edit its single implementation in place and bump the manifest @N for behaviour changes.
  Recover old implementations from archive tags, never a parallel module.
- Treat agent and workflow release fingerprint sources, scorer manifest descriptions, observation subject trajectory,
  evidence input oracle.cases and problem oracle/ bundles as identity-bearing.
- Preserve legacy wire names including Harness CLI ids, raidar_oracle_plugin.py,
  raidar-oracle/, oracle_leak, oracle_bundle and oracle-bundle.json unless deliberately releasing them.

## Pack releases

- A pack image release changes the computed image tag. The hash covers pack id/version/Python,
  image settings except the context path, and build-context paths/bytes (excluding editor/OS files).
  Task-binding fields such as entry_file_template and task_environment are separate contract inputs;
  do not claim they change the image tag.
- Revalidate every pinned problem with native parity:
  `make problem-validate PROBLEM=<ref> ENGINE=harbor NATIVE_RUNNER=<checkout>`.
- Re-key errata whose manifest changed, complete work held for this pack release, then relock.
  Do not change a deferred-work test's pinned hash instead of completing the work.

## Relock and verify

- Semantic changes under design/experiment/, to contract/canonical.py, references.py, source_digest.py,
  scoring/framework/metrics.py or any scorer implementation/import require relocking all tracked experiments.
- Run `make experiment-lock EXPERIMENT=<path>` for each affected definition, then `make experiment-locks-check`.
- An unchanged lock_sha256 permits offline rescoring of existing jobs; changed execution identity needs a new experiment.
- Run `make perf-gate TARGET=<name>` before a release/phase gate and after execution-path changes.
  Respect the accepted target budgets; a budget change requires a user decision.
- Return archived refs, version changes, affected locks, validation results and any unresolved release conditions.
