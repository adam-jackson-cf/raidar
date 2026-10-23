# Repository structure, primitives and versioning (proposal)

- **Status:** accepted 2026-10-24 with these user decisions: keep `orchestrator/` (no rename); `lab/` and `var/` adopted; `spikes/` deleted; package layout §4.2 approved as proposed; and **a clean start**. Once the restructure is signed off (no functional regression, verification and code reviews green), jobs and artefacts start afresh as v1. Old experiments, jobs and stores are not carried forward: finished and closed experiment definitions leave the tree (they are preserved at `archive/pre-structure-reset`), and old jobs, stores and analyses move to `var/archive/pre-v1/`. The schema-v1/legacy-job migration code and the dormant `process-compliance@1` are deleted. §3.4 ("closed, not relocked") is therefore not built.
- **Date:** 2026-10-24
- **Inputs:** the version inventory and structure audit of 2026-10-24 (session reports `version-map.md`, `structure-map.md`), and how factory-x-loops (fxl) stores versions (`docs/references/workflows.md:114-153`, `.agents/skills/workflow-experiments/references/change-workflow-or-engine.md:79-135` in that repo).

## 1. Problem

The tree grew by accretion:
- **About 75 version-named modules.** Each release of a scorer or component was a new sibling file (`continue_on_stall_v2.py`, `guardrail_gate_v2…v4.py`, `builtins/guardrails_v2…v8.py`, `scorers/gate_effort_v2…v10/`, `process_checks_v3…v6/`, `code_quality_v2/v3/`), each subclassing the one before. There are also 7 near-identical payload trees (`payloads/python_guardrails_1…8`, about 12k lines) and about 30 test files, one per version. No tracked experiment pins two versions of one family, so none of the coexistence is used as a control.
- **Top-level `raidar` packages are not grouped by any rule.** There are 21 packages plus loose root modules (`codex_auth.py`, `job_layout.py`), several one-module packages (`harness/`, `oracle/`, `schemas/`), a flat 26-file `components/` that mixes definition code with code that runs inside trials, and a `commands/` layer that holds some domain logic.
- **The repo root mixes tracked source, tracked lab content and gigabytes of generated or abandoned data:** `jobs/` 3.3G, `stores/` 220M, `analysis/`, `spikes/` 2.5G, `problems/store`, `problems/cache`, `experiments/operational_smoke/`.

## 2. Primitives

Raidar's own vocabulary (requirements, ADR-001/002) has these primitives, in lifecycle order:

| Stage | Primitive | Meaning |
|---|---|---|
| define | **problem** | a task with hidden tests and a grader (`scb/forge@<pin>`) |
| define | **runtime pack** | the language image and tools that trials run in (`python`) |
| define | **agent** | a harness, model and effort (codex `gpt-6-luna` low) |
| define | **component** | one treatment unit given to an agent (rules file, gate, loop) |
| define | **experiment** | conditions × arms × problems × attempts, plus scorers and metric; frozen by a **lock** |
| run | **target** | the Docker daemon trials run on |
| run | **job → trial → step** | one execution of a lock; a trial is one arm × attempt, a step is one checkpoint |
| verify | **grade** | the host-side verdict of a step from the hidden tests |
| assess | **scorer → assessment (in a store)** | a deterministic measure of a trial, kept in an add-only store |
| analyse | **analysis** | paired deltas, verdicts and the pooling gate over jobs |
| review | **review surface** | the UI and its projection contract |
| learn | **ledger** | fault categories and remedies |

Folder names should be these words and nothing else.

## 3. Versioning policy

**Rule: no version in a file, module or folder name.** A version is data: `name@N` in a manifest, `schema_version`, `scb-canonical@2`.

1. **Code (scorers, component runtimes, packs): one module per primitive, edited in place.** A behaviour change bumps the manifest's `@N`. The release being replaced is preserved by an annotated git tag `archive/<name>@<N>`, created by `make release-archive REF=<name>@<N>` before the edit lands. This matches fxl, which never keeps Python implementations side by side: its engine code is pinned through git provenance, and comparability is guarded by revision checks. Raidar already records the runner commit and toolchain hash and refuses to pool across them (ADR-002 A.6).
2. **Control vs treatment never needs two code versions at once.** Arms of one experiment share every scorer. Component variants that must run side by side are expressed as `with:` options on one implementation; guardrails r1 vs r2 already worked this way (`mode: shadow|enforced`).
3. **Data that must coexist goes in fxl-style folders** (`<name>/v<N>/`). These are hand-edited, immutable once measured, and allowed only for payload or config data, never for code. Raidar's natural place for them is the experiment's own `components/` directory, which already holds per-condition payloads. A shared copy goes in `lab/components/<name>/v<N>/` only when two experiments need it. Nothing uses this today.
4. **Finished experiments are closed, not relocked.** `experiment.yaml` gains `status: open|closed`. A closed experiment keeps its definition and lock byte for byte. `experiment-locks-check` skips it, `experiment-execute` refuses it, and its jobs are rescored or re-analysed at the archive tag named in its rationale. This replaces "every old version stays registered so old jobs stay rescorable".
5. **Enforcement:** `make structure-check` joins `make quality`. It refuses (a) any tracked path matching a version suffix (`_v\d+`, `-v\d+`, `\bv\d+\b`), except `v<N>/` data folders under `components/`; (b) top-level directories and `raidar` packages not on the allowlist in §4; (c) package depth over 3 below `raidar/`.

## 4. Target layout

### 4.1 Repository root

```
raidar/                     tracked Python project (was orchestrator/): pyproject, src/raidar, tests
review-surface/             tracked UI (unchanged)
lab/                        tracked research content
  experiments/<kind>/<id>/  experiment.yaml, lock, components/ (open and closed)
  problems/                 sources.yaml, registry/, fixtures/
  ledger/                   faults/, remedies/, README.md (generated)
docs/  scripts/  .agents/  .github/
var/                        ALL generated data, git-ignored, relocatable with RAIDAR_VAR=<dir>
  jobs/  stores/  analysis/  problems/{store,cache}/  tmp/   (tmp replaces .tmp/)
Makefile  README.md  AGENTS.md  CHANGELOG.md
```

Deleted: `spikes/` (no code, Makefile or CI references it; findings live in `docs/reboot/evidence/`), `experiments/operational_smoke/`, `scripts/oracle-browser-run.sh`, and the stale `runs`/`workspace*`/`results` excludes.

### 4.2 `raidar` packages, grouped by stage

Top level stays flat: one package per primitive, no stage wrapper packages, so import paths stay short.

```
raidar/
  cli/          the click entry point and command handlers ONLY (was cli.py + commands/; logic in
                commands/{assess_grading,repo_state,run_checks}.py moves to its domain package)
  core/         shared value types used across stages: job layout, step snapshot (was oracle/snapshot),
                review contract (was schemas/review)
  problems/     (unchanged)
  packs/        runtime packs (unchanged)
  agents/       adapters/, harness.py (was harness/definitions.py), codex_auth.py
  components/   definition/ (registry, manifest, compiler, compiled, integrity, contribution, evidence)
                runtime/   (code that runs in or around a trial: gate, review_loop, continue_on_stall,
                            transient_retry, session_chain, provision, autofix, mcp_shim, setup_script)
                catalog/   (the built-in component manifests, was builtins/; one file per component)
                payloads/  (one data tree per component, no version suffix)
  spec/         experiment schema, validation, lock (schema-v1 loader removed)
  targets/      (unchanged)
  execution/    plan, driver, trial, environment, cleanup, reaper, guard
                health/ (health, liveness, admission, provenance, fault candidates)
                perf/   (phases, budgets, perf report)
  verifier/     grader, sandbox, regrade, records, outcomes; runtime/ (in-image pytest plugin)
  scorers/      catalog, sdk, one package per scorer: correctness, code_quality, process_checks,
                gate_effort, resource_usage
  analysis/     (unchanged, plus pooling)
  review/       projection/ (was review_projection), quality/ (was review_quality)
  ledger/       (unchanged)
  jev/          dormant (user decision 2026-10-04), unchanged
```

Removed: the legacy schema-v1 loader and legacy job migration (`spec/schema_v1.py`, `execution/job_migration.py`, `execution/errata_migration.py`, the `LEGACY_*` helpers, `experiment-upgrade`/`experiment-migrate-job`), `scorers/code_quality/sensitivity.py` (no importer), the empty `agents/harbor_agents/`, and the dormant `process-compliance@1` scorer (`scorers/process_compliance/`), which shares its id with the surviving `process-compliance@6`.

### 4.3 Version collapse (survivor = latest)

| Family | Survivor | Unversioned home |
|---|---|---|
| continue-on-stall | @2 | `components/runtime/continue_on_stall.py`, `components/catalog/stall.py` |
| review-loop | @2 | `components/runtime/review_loop.py`, `catalog/workflow.py` |
| guardrail-gate | @4 | `components/runtime/{guardrail_gate,gate_run,autofix}.py`, `catalog/gate.py` |
| python-guardrails | @8 | `components/catalog/guardrails.py`, `runtime/provision.py`, `payloads/python_guardrails/` |
| lint-tool | @2 | `components/catalog/tools.py` |
| code-quality | @3 | `scorers/code_quality/` |
| process-compliance | @6 | `scorers/process_checks/` |
| gate-effort | @10 | `scorers/gate_effort/` |

Each inheritance chain is flattened into its one module. Tests that cover only an old version are deleted, and the survivor's tests take the unversioned name.

## 5. Consequences

- **One declared re-key** (like ADR-002 step −1): every scorer implementation digest and every component fingerprint changes, and every open lock is relocked. Closed experiments keep their locks (§3.4).
- **Open experiments re-pinned to survivors:** `code-quality@1 → @3` and `process-compliance@2 → @6` in guided-pilot ×2, guided-r3 and the forge smoke. @3 reads coverage like @1 and differs only on parity failures; @6 adds diagnostic metrics the headline does not read. **Proposed:** close the three pilot experiments instead, since they are finished Phase 1 work, which leaves no re-pin.
- **Regression proof:** a fresh-store rescore of guardrails main-04 with the collapsed `code-quality`, `process-compliance` and `gate-effort` must reproduce the stored `result` of `@3`/`@6`/`@10` exactly (48 assessments). It runs at the new code against the closed lock's scorer refs. A smoke on the collapsed components must have a healthy, valid trial.
- **Moving data to `var/`:** jobs are moved, not copied (same filesystem). Ledger evidence refs and docs that say `jobs/…` are rewritten to `var/jobs/…`. Recorded absolute paths inside old job files stay as they are.
- **Archive tag before any of this:** `archive/pre-structure-reset` at the pushed head of `reboot-impl`.

## 6. Decisions for the user

1. Rename `orchestrator/` → `raidar/` (recommended), or keep `orchestrator/`.
2. Group tracked research content under `lab/` (recommended), or keep `experiments/`, `problems/`, `ledger/` at the root.
3. Put generated data under a single `var/` (recommended), or keep `jobs/`, `stores/`, `analysis/` at the root.
4. Close the finished pilot experiments, which avoids re-pinning (recommended), or re-pin them to the survivors.
5. Delete `spikes/` (2.5G, untracked) and the schema-v1/legacy-job migration code (recommended).
