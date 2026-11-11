# Plan an experiment

A researcher uses the orchestrator CLI (`raidar`) to write a new two-arm experiment, validate
and lock it, and dry-run its trial plan before spending any agent time.

## Sub-features

- **Init.** `raidar init <dir>/experiment.yaml` writes a two-arm `ab-test` (baseline and
  treatment) on the default pinned problem. The directory name is the experiment id and must
  read `python__<subject>__<intent>`.
- **Validate.** `raidar validate` checks schema, releases, kind and naming rules and lists
  the references it resolved. Under an `experiments/` directory the path must be
  `experiments/<kind>/<id>/experiment.yaml`.
- **Lock.** `raidar lock` writes `experiment.lock.json` with content hashes and pinned
  versions; `raidar lock --check` reports "Lock is current" or names each stale section.
- **Plan.** `raidar plan --json` lists every trial with its input hashes without running an
  agent. End state for the default experiment: 10 selected trials (5 attempts × 2 arms).

## How to get to it (user POV)

1. From the repo root: `make experiment-init EXPERIMENT=<path>/experiment.yaml`, or
   `uv run --project orchestrator raidar init <path>/experiment.yaml`.
2. `make experiment-validate`, `make experiment-lock` and `make experiment-plan` with the same
   `EXPERIMENT=`; `make help` lists them.
3. `raidar run` executes the plan (Docker target required); that step is outside this feature.

## Driving it with node scripts/surface/lever.mjs

- Journey: [plan-experiment.journey.mjs](../../review-surface/journeys/plan-experiment.journey.mjs)
  (`preserve`).
- Command: `node scripts/surface/lever.mjs --data-dir "$(mktemp -d)/data" --evidence-dir <dir>
  --journey review-surface/journeys/plan-experiment.journey.mjs`.
- The journey runs `raidar` through the lever's `raidar()` helper (`uv run --project
  orchestrator raidar`, `RAIDAR_VAR` and `TMPDIR` inside the data directory). It asserts
  arms `baseline` and `treatment`, 10 selected trials and a current lock, and records the lock
  digest and a digest of the trial hashes.
- Evidence: `plan-experiment.result.json`, `.junit.xml`, `.log`.

## Gotchas

- The lock digest depends on host facts (Python patch version, package hashes), so identical
  results are expected only on one machine and revision.
- `raidar init` refuses a directory name that is not a valid experiment id.
- No Docker, credentials or network are needed; `raidar run` and smoke trials are covered by
  the `raidar-smoke` skill, not by this lever.
