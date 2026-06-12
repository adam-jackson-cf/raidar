# Raidar Eval Suite Improvement Goal Tracker

## Goal Statement

Transform Raidar into a fully realised eval suite with scorers that enable effective delivery-activity measurement to support experimentation through iteration. That requires a review surface for findings that enables users to understand what is happening, what has worked, and where problems or potential improvements lie through easy surfacing of relevant evidence that helps drive decisions on the next iteration. This is achieved through a scenario-driven evaluation suite for agentic software delivery by implementing scorer-backed scenario coverage and matrix/reporting improvements that feed a Raidar-native findings/review surface inspired by Raindrop Workshop. The work should preserve Raidar's core mechanism—scenario revisions, AgentSpecs, repeated run evidence, deterministic/hybrid scoring, and public `make ...` workflows—while using the charter review and Workshop comparison as the source of truth for backlog priorities, process-measurement gaps, and review UX direction.

## Lifecycle Status

- Status: first implementation pass complete (2026-06-11). Phases 1-4 delivered and validated; Phase 5 backlog recorded below.
- Definition-of-done check: (1) source docs translated into an implementation sequence — done; (2) high-signal coverage gap improved — `bugfix@1` now has an authorable scenario root plus the platform evidence it needed — done; (3) findings/review surface MVP populated from Raidar artifacts with evidence references — done; (4) workflow measures outcomes and process quality (gates/requirements/scorers plus process findings) — done; (5) public `make ...` remains the interface (new targets only) — done; (6) changes covered by tests/validation — done; (7) `make quality` passes — done; (8) tracker updated — this update.

## Source Inputs

- `docs/todos/charter-review/charter-review.md`
- `docs/todos/charter-review/raindrop-workshop-comparison.md`

## Execution Phases

### Phase 1: Plan and vertical-slice selection

Status: complete (2026-06-11).

Outputs:

- Source documents reviewed: `charter-review.md` (charter, coverage map, 20 backlog items, decision rules) and `raindrop-workshop-comparison.md` (adopt review framing over Raidar artifacts, not Workshop runtime).
- Selected Phase 2 slice: charter backlog item 1 — new `bugfix` scenario root exercising `bugfix@1`, plus a minimal platform addition (scenario-declared retained-evidence ingestion) required to make `defect-evidence-completeness` scoreable. Rationale: highest-severity gap class (active scorer, zero scenario coverage), fully deterministic scorer (no judge cost), unit-tested contract, no scorer ID changes.
- Selected Phase 3 slice: charter backlog items 14 (finding surfacing) with a thin slice of 13 (run outline data): a deterministic findings module in the orchestrator projecting run/scorecard/experiment-summary evidence into `issue | good | note` records with evidence references, persisted per run and aggregated per experiment, surfaced in benchmark-view.
- Implementation sequence: Phase 2a platform evidence ingestion → Phase 2b bugfix scenario root + matrix config → Phase 3 findings module + benchmark-view surface → Phase 4 comparison path → Phase 5 validation/backlog.
- Validation plan: orchestrator unit tests for evidence ingestion, scenario schema, and findings projection; `make scenario-validate` for the new root; benchmark-view `npm run build-data` against clearly labeled synthetic fixtures; `make quality` as the completion gate. Real benchmark runs are not required for the DoD and are deferred unless needed (GPT 5.5 low if run).

### Phase 2: Scenario/scorer-backed measurement slice

Status: complete (2026-06-11). Selected family: `bugfix@1`.

Delivered:

- Platform (2a): final-workspace hydration for all non-terminated runs (previously visual-only, which left non-visual workspace diffs empty and starved `change-containment`), plus scenario-declared retained-evidence ingestion (`evidence.retained_files` in `scenario.yaml`; JSON files from the run workspace are ingested into scorer-visible retained evidence with reserved-key protection and size caps). This makes `defect-evidence-completeness` fully scoreable and is reusable for a future `plan-to-code@1` scenario.
- Scenario (2b): new root `scenarios/bugfix-ledger-balance/v001` (category `bugfix`, difficulty easy): seeded debit-handling defect in `src/lib/ledger.ts`, parked reproduction test (`it.skip`, so starter preflight passes), defect-linked requirements (`no_pattern it\.skip`, regression-suite and evidence-file existence checks), four gates (typecheck/lint/test/coverage), scorers `bugfix@1` 0.88 + `requirements@1` 0.10 + `resource-efficiency@1` 0.02.
- Matrix: `matrices/bugfix-ledger-balance-codex-gpt55.yaml` (codex-cli, gpt-5.5 low/medium, repeats 1). Not executed live; running it is a separate cost decision.
- Validation: `make scenario-validate` passes (3 scorers, 8 metrics); baseline starter passes all four gates locally (install/typecheck/lint/test/coverage at 100%); the solved state was simulated end-to-end in a scratch copy (repro test fails with the bug — `expected 6500 to be 3500` — and all gates pass after the fix); contract tests in `orchestrator/tests/test_bugfix_ledger_scenario.py`.

### Phase 3: Findings surface MVP

Status: complete (2026-06-11).

Delivered:

- `orchestrator/src/raidar/findings.py`: deterministic projection of retained evidence into `issue | good | note` finding records with evidence references. Run-level categories: failed-gate, missing-required-command, requirements-gap, missing-artifact (metric missing patterns and unusable declared evidence files), judge-review, deterministic-cap, completion-claim, performance-gate, workflow-anomaly (repeated verification failures, verification-bypassing git commits), plus good findings (clean-verification, requirements-satisfied, retained-evidence). Experiment-level categories: unscored-run, repeat-variance, resource-outlier (leave-one-out duration statistics), sample-adequacy, rerun-target. Findings are non-authoritative and never change scores.
- Persistence: per-run `findings.json` written beside `run.json` by `persist_eval_run`; experiment-level `findings` array added to `experiment-summary.json`.
- benchmark-view surface: restored the worktree-deleted `benchmark-view/` from HEAD and added a Findings panel (Workshop-inspired kind chips, category, title, evidence `source:reference`, kind counts, 40-item cap) plus per-run findings in the run diagnostic drawer; rows carry `findings_summary` counts and a `synthetic` flag rendered as a SYNTHETIC FIXTURE badge.
- Synthetic fixtures: `make benchmark-fixture-synthetic` (new public target) generates two clearly-labeled benchmark-shaped experiments (`synthetic` markers in ids and payloads) so the review surface can be developed and tested without live runs.
- Validation: `orchestrator/tests/test_findings.py` (8 behavior tests), `tests/test_synthetic_fixture.py`, findings persistence asserted in `test_run_dispatch_behaviors.py`; `node --check` on the data builder and extracted page module; `make benchmark-view-build` against the synthetic fixtures.

### Phase 4: Experiment/report iteration path

Status: complete (2026-06-11) for the MVP scope.

- The dashboard's existing comparison machinery (AgentSpec leaderboard, revision trajectory, deltas, evidence explorer) now coexists with the findings layer: the Findings panel aggregates over the rows in view, so comparing AgentSpecs or revisions surfaces the differentiating findings directly.
- Validated over HTTP with the synthetic fixtures: two AgentSpecs (gpt-5.5 low vs medium) on `bugfix-ledger-balance@v001` render with findings summaries issue=9/good=9/note=5 vs issue=0/good=9/note=3 — the degraded spec's failed gates, requirements gaps, and missing defect evidence are visible without opening raw artifacts.

### Phase 5: Follow-on backlog

Status: recorded (2026-06-11). See Follow-on Backlog section below.

## Decision Log

Record material decisions here during execution.

| Date | Decision | Rationale | Impact |
|---|---|---|---|
| 2026-06-11 | Goal assets target `docs/todos/charter-review/` | User requested the charter-review folder. | New `goal.md` and `goal-tracker.md` drafted in-place. |
| 2026-06-11 | Goal is phased implementation, not design-only | User selected phased implementation. | Later executor should implement a validated vertical slice, not only write a plan. |
| 2026-06-11 | Use scoped subagents during execution | User requested suitable reasoning-level subagents to preserve context and reduce token costs. | Orchestrator should delegate bounded implementation/review/discovery tasks while retaining integration decisions. |
| 2026-06-11 | Synthetic benchmark-shaped data is allowed for review-surface development | User allowed synthetic data in the existing benchmark-run shape to speed implementation and testing. | Findings/review UI/API can be validated with clearly labeled synthetic fixtures before real runs. |
| 2026-06-11 | Real benchmark runs default to GPT 5.5 low reasoning | User requested cost control for real benchmark runs. | Any real benchmark validation should use GPT 5.5 low reasoning unless explicitly approved otherwise. |
| 2026-06-11 | First measurement slice is a new `bugfix` scenario root | `bugfix@1` is active, deterministic, and unit-tested with zero scenario coverage; bugfix is a materially different delivery activity, so repo decision rules require a new root, not a revision. | `make scenario-init` used for a new root; existing roots untouched. |
| 2026-06-11 | Add scenario-declared retained-evidence ingestion (platform) | `bugfix@1` `defect-evidence-completeness` reads `context.retained_evidence` keys (`reproduction_note`, `regression_tests`, …), but the artifact phase only retains visual evidence today; without ingestion the metric can never fully pass. | Small generic platform capability: scenario contract declares evidence files the agent must write; artifact phase ingests them into `evidence_artifacts`. Reusable for the future `plan-to-code@1` scenario. |
| 2026-06-11 | Findings layer is deterministic orchestrator code, not LLM-generated | Goal prefers deterministic evidence; all target finding categories (failed gates, missing commands/artifacts, requirements gaps, judge caps, resource outliers, completion-claim inconsistencies, repeat variance) are derivable from retained artifacts. | Findings stay non-authoritative review metadata with evidence references; no scorer behavior changes. |
| 2026-06-11 | Restore `benchmark-view/` from HEAD and build findings surface on it | Working tree contains uncommitted deletions of all tracked `benchmark-view/` files (cause unknown, not made by this execution); public `make benchmark-view-build`/`serve` targets depend on the directory and would fail. | Files recreated from HEAD content, then extended with the findings surface. Flagged to user in case the deletion was intentional. |
| 2026-06-11 | Hydrate the final workspace for all non-terminated runs | Hydration was visual-only, so non-visual runs diffed an unchanged baseline: empty changed-file evidence starved `change-containment`, regression-test inventory, and the new evidence ingestion. | Platform behavior change recorded under residual risks; covered by artifact-phase tests. |
| 2026-06-11 | Ship the defect reproduction test as `it.skip` in the starter | Starter preflight executes all `required_commands` against the baseline and aborts on failure, so a hard-failing repro test cannot ship; a parked repro keeps preflight green while `no_pattern it\.skip` plus the test gate force re-enable-and-fix. | Deterministic defect link without preflight breakage; verified by lint/test on the baseline. |
| 2026-06-11 | Findings persist as per-run `findings.json` plus experiment-summary `findings` | Run-level findings need to live with run evidence for drilldown; experiment-level findings (variance, outliers, unscored, sample) only exist at aggregation time. | benchmark-view consumes both without new orchestrator APIs. |
| 2026-06-12 | Workshop-adapted review surface built as `review-surface/` (separate goal) | User requested a fully functional, persona-aware review surface replicating Workshop's findings UX over Raidar data in a new root subdirectory. | Charter items 13/15/16/17 substantially delivered: run outline, span tree, evidence search, payload retrieval, and durable manual annotations over projected Raidar artifacts (MIT-attributed component adaptation; daemon/replay intentionally excluded). Public targets `make review-surface-{data,build,serve}`. |

## Files Changed During Execution

Record implementation changes here. Do not include goal-asset drafting as implementation progress.

| File/path | Change summary | Phase |
|---|---|---|
| `orchestrator/src/raidar/schemas/scenario.py` | Added `EvidenceConfig`/`RetainedEvidenceFile` and `ScenarioDefinition.evidence` | 2a |
| `orchestrator/src/raidar/runtime/artifact_phase.py` | Hydrate final workspace for all non-terminated runs; ingest declared retained-evidence JSON files into scorer-visible evidence | 2a |
| `orchestrator/tests/test_artifact_phase_behaviors.py` | Rewritten for the new artifact-phase contract incl. ingestion edge cases | 2a |
| `scenarios/bugfix-ledger-balance/v001/**` | New scenario root: contract, prompt, rules, starter with seeded defect and parked repro test | 2b |
| `matrices/bugfix-ledger-balance-codex-gpt55.yaml` | Stored matrix config for the new scenario (codex gpt-5.5 low/medium) | 2b |
| `orchestrator/tests/test_bugfix_ledger_scenario.py` | Scenario contract tests | 2b |
| `docs/references/new-scenario.md` | Documented `evidence.retained_files` authoring contract | 2a/2b |
| `orchestrator/src/raidar/findings.py` | New deterministic findings projection (run + experiment level) | 3 |
| `orchestrator/src/raidar/application/run_dispatch.py` | Persist `findings.json` beside `run.json` | 3 |
| `orchestrator/src/raidar/experiment.py` | Experiment-level `findings` in experiment summary payloads | 3 |
| `orchestrator/src/raidar/synthetic.py` | Labeled synthetic benchmark fixture generator | 3 |
| `orchestrator/tests/test_findings.py`, `test_synthetic_fixture.py`, `test_run_dispatch_behaviors.py` | Findings/fixture behavior coverage | 3 |
| `benchmark-view/scripts/build-data.mjs` | Ingest run findings.json + experiment findings; rows carry findings_summary and synthetic flag | 3/4 |
| `benchmark-view/src/index.html` | Findings panel with issue/good/note chips; run drawer findings; SYNTHETIC FIXTURE badge | 3/4 |
| `Makefile` | New public target `benchmark-fixture-synthetic` | 3 |

Commits: `a2d9e3f` (goal assets), `3bab93f` (retained-evidence ingestion), `2f4efb2` (bugfix scenario), `9c8dd3a` (findings layer), `e908a70` (benchmark-view surface), plus a final formatting/tracker commit.

## Validation Log

Record validation commands and results here.

| Date | Command | Result | Notes |
|---|---|---|---|
| 2026-06-11 | `make scenario-validate SCENARIO=scenarios/bugfix-ledger-balance` | pass | 3 scorers, 8 metrics, 4 gates, 4 required commands |
| 2026-06-11 | `bun install/typecheck/lint/test/test:coverage` in new starter | pass | Baseline green: 8 passed + 1 skipped repro; coverage 100% |
| 2026-06-11 | Solved-state simulation in scratch copy | pass | Unskipped repro fails pre-fix (`expected 6500 to be 3500`); all gates pass post-fix |
| 2026-06-11 | `uv run python -m pytest tests` (orchestrator) | pass | 525 tests after all changes |
| 2026-06-11 | `make benchmark-fixture-synthetic` + `make benchmark-view-build` | pass | 2 synthetic rows with findings_summary populated |
| 2026-06-11 | `node --check` on build-data.mjs and extracted page module | pass | Dashboard module parses |
| 2026-06-11 | HTTP smoke of `make benchmark-view-serve` | pass | page 200; data.json findings summaries issue=9/good=9/note=5 vs issue=0/good=9/note=3 |
| 2026-06-11 | `make quality` | pass | smoke dry-run check, ruff, pytest + coverage, lizard CC<10 |

## Rejected or Deferred Alternatives

Record alternatives that are intentionally not pursued.

| Alternative | Reason deferred/rejected |
|---|---|
| Replacing Raidar with Workshop runtime concepts | Out of scope; Raidar remains the benchmark mechanism. |
| Treating Workshop replay as the primary scenario runner | Out of scope; replay UX may inspire Raidar-native rerun/finding workflows only. |
| Retiring or renaming scorer IDs | Requires explicit user approval. |
| Broad docs restructuring | Out of scope unless required and approved; `README.md` remains the human entrypoint. |

## Residual Risks

- No live benchmark run has exercised `bugfix-ledger-balance@v001` end-to-end in Harbor yet; the contract is validated by schema checks, baseline gate runs, and a solved-state simulation, but the first real matrix run (GPT 5.5 low) may surface container/runtime issues (for example archive hydration timing or coverage parsing).
- The all-runs workspace hydration is a behavior change for non-visual scenarios: workspace diffs and scorer file inventories now reflect agent output. This is the intended fix for empty non-visual diffs, but historical run artifacts are not comparable for change-containment-style evidence.
- The `benchmark-view/` working-tree deletion that predated this execution was unexplained; the directory was restored from HEAD because public make targets depend on it. If the deletion was intentional, the findings surface needs a new home.
- Findings remain non-authoritative review metadata; if finding frequency is later promoted into scoring, calibration and versioning will be needed.
- `regression-protection` still uses a filename-keyword proxy; a starter-replay upgrade would make it directly evidential.
- Real `requirements-adherence` judging on the new scenario needs judge-runtime credentials at scoring time; synthetic fixtures bypass this deliberately.

## Follow-on Backlog

Recommended next slices, in priority order:

1. Run `matrices/bugfix-ledger-balance-codex-gpt55.yaml` live (GPT 5.5 low first) to validate the scenario and the evidence-ingestion path end-to-end in Harbor, then review the findings surface against real artifacts.
2. Remaining uncovered active scorer families, reusing the bugfix authoring pattern: `refactor@1` (behavior-preserving refactor root), `test-generation@1` (coverage-lift root), `python-code-task@1` (Python starter), then `plan-to-code@1` using the retained-evidence mechanism for plan packets (charter backlog items 2-5/8).
3. ~~Workshop-inspired evidence search and payload retrieval over Raidar artifacts (charter item 15)~~ — delivered 2026-06-12 by the `review-surface/` app (per-run evidence search, payload slicing API).
4. Durable annotations (charter item 16) — manual `issue|good|note` annotations delivered in `review-surface/` (persisted to `review-surface/data/user-annotations.json`); finding-to-rerun/finding-to-scenario promotion workflow (charter item 18) still open.
5. ~~Phase tree/timeline over execution evidence (charter item 17)~~ — delivered 2026-06-12: the review-surface span tree projects trace events, gates, scoring, requirements, validity, process, and artifact evidence with duration bars. A related platform bug was fixed en route: `run_task` silently dropped execution trace events (`events=` vs `traces` field), so real runs never persisted traces.
6. Multi-harness quality matrices and rules/linting intervention revision pairs (charter items 7/10).
7. Upgrade `regression-protection` from filename proxy to starter-replay evidence if live runs show the proxy is gameable.
