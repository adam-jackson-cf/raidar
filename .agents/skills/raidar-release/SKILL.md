---
name: "raidar-release"
description: "Use when changing a released Raidar agent, workflow, stage package, scorer, headline metric or runtime pack, or relocking after toolchain edits."
---

# Task

## Establish release impact

- Identify manifest versions, fingerprint sources and affected experiment locks before editing.
- Preserve released bytes first with `make release-archive REF=<name>@<N>` for each affected agent, workflow, stage package or scorer.
  Edit its single implementation in place and bump the manifest @N for behaviour changes.
  Recover old implementations from archive tags, never a parallel module.
- Agent/workflow fingerprints, stage-tree/1 shipped bytes, primitive_code inventories,
  scorer manifest descriptions, observation subject trajectory, evidence input oracle.cases
  and problem oracle/ bundles feed experiment locks: changing them needs a release and relock.
- Raidar keeps no execution-identity record and versions no implementation code to keep
  experiments valid. A code change that invalidates an experiment means rerunning it.
  Each job's `job.json` records the Raidar commit and dirty flag it ran on (`runner.commit`,
  `runner.dirty`), so results that predate a change are knowable.
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

## Stage packages

- Follow `lab/stages/README.md` (`stage/1`, `stage-tree/1`, `criteria/1`).
  Packages live at `lab/stages/<ns>/<name>/`; references require `ns/name@N`, never mutable paths.
- Generate tool dependencies with `make stage-lock NAME=<ns>/<name>`, never hand-edit `uv.lock`.
  ST1–ST7 refuse unsupported contracts/primitives, unavailable inputs, changed registered bytes,
  stale/non-wheel/incomplete closures, missing frame slots and colliding claims.
- Run `make stage-check NAME=<ns>/<name> TARGET=mbp`: fixtures and replay execute offline as UID 1000
  in the pack container, never on the host. Preserve its exact package/pack/primitive check record.
- Add the next immutable stage release to `lab/releases.yaml` (checks print the line; no writer).
  Re-release dependent workflows and relock every affected experiment. Fixtures are excluded
  from stage-tree/1; shipped prompts, source, skills and tool locks are not.
- `make experiment-lock EXPERIMENT=<smoke-yaml> DRAFT=1` writes development locks under `var/tmp/`;
  drafts never admit measured comparisons. Run `make security-gate TARGET=mbp` for primitive,
  runner or `engine/trial` changes, as well as `make quality`.

## Relock and verify

- Semantic changes under design/experiment/, to contract/canonical.py, references.py, source_digest.py,
  scoring/framework/metrics.py or any scorer implementation/import require relocking all tracked experiments.
- Run `make experiment-lock EXPERIMENT=<path>` for each affected definition, then `make experiment-locks-check`.
- An unchanged lock_sha256 permits offline rescoring of existing jobs. A code change that invalidates results means rerunning the experiment.
- Run `make perf-gate TARGET=<name>` before a release/phase gate and after execution-path changes.
  Respect the accepted target budgets; a budget change requires a user decision.
- Return archived refs, version changes, affected locks, validation results and any unresolved release conditions.
