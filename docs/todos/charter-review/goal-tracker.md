# Raidar Eval Suite Improvement Goal Tracker

## Goal Statement

Transform Raidar into a fully realised eval suite with scorers that enable effective delivery-activity measurement to support experimentation through iteration. That requires a review surface for findings that enables users to understand what is happening, what has worked, and where problems or potential improvements lie through easy surfacing of relevant evidence that helps drive decisions on the next iteration. This is achieved through a scenario-driven evaluation suite for agentic software delivery by implementing scorer-backed scenario coverage and matrix/reporting improvements that feed a Raidar-native findings/review surface inspired by Raindrop Workshop. The work should preserve Raidar's core mechanism—scenario revisions, AgentSpecs, repeated run evidence, deterministic/hybrid scoring, and public `make ...` workflows—while using the charter review and Workshop comparison as the source of truth for backlog priorities, process-measurement gaps, and review UX direction.

## Lifecycle Status

- Status: execution in progress.
- Current phase: Phase 2 (scenario/scorer-backed measurement slice).
- Do not mark this tracker complete until the implementation definition of done in `goal.md` is satisfied.

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

Status: pending.

Candidate gap families:

- `bugfix@1`
- `refactor@1`
- `test-generation@1`
- `python-code-task@1`
- `plan-to-code@1`

Expected outputs:

- Scenario root or revision decision.
- Scorer/matrix/platform changes as needed.
- Tests or scenario validation.

### Phase 3: Findings surface MVP

Status: pending.

Expected finding categories:

- failed gates;
- missing required commands;
- missing required artifacts;
- requirements gaps;
- deterministic caps on judge-backed scores;
- resource outliers;
- completion-claim inconsistencies;
- workflow/process anomalies;
- repeat variance.

Expected outputs:

- Raidar-native finding records or projection.
- Evidence references into Raidar artifacts.
- Initial UI/report/API surface.

### Phase 4: Experiment/report iteration path

Status: pending.

Expected outputs:

- Matrix/report or benchmark-view path for comparing AgentSpecs, scenario revisions, or interventions.
- Findings visible enough to support iteration decisions.

### Phase 5: Follow-on backlog

Status: pending.

Expected outputs:

- Remaining scorer/scenario gaps.
- Deferred Workshop-inspired UX/platform work.
- Residual risks and next recommended implementation slices.

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

## Files Changed During Execution

Record implementation changes here. Do not include goal-asset drafting as implementation progress.

| File/path | Change summary | Phase |
|---|---|---|

## Validation Log

Record validation commands and results here.

| Date | Command | Result | Notes |
|---|---|---|---|

## Rejected or Deferred Alternatives

Record alternatives that are intentionally not pursued.

| Alternative | Reason deferred/rejected |
|---|---|
| Replacing Raidar with Workshop runtime concepts | Out of scope; Raidar remains the benchmark mechanism. |
| Treating Workshop replay as the primary scenario runner | Out of scope; replay UX may inspire Raidar-native rerun/finding workflows only. |
| Retiring or renaming scorer IDs | Requires explicit user approval. |
| Broad docs restructuring | Out of scope unless required and approved; `README.md` remains the human entrypoint. |

## Residual Risks

Update during execution.

- First implementation slice may prove too broad if it combines scenario coverage, scorer changes, matrix reporting, and UI work at once.
- Findings surface must remain evidence-linked and non-authoritative unless deliberately promoted into scorer/scenario behavior.
- Workshop UI/component reuse may carry hidden dependency, styling, routing, or data-shape costs.
- Live benchmark runs may be expensive; use minimal validation, default to GPT 5.5 low reasoning, and prefer synthetic benchmark-shaped fixtures for review-surface development where valid.
- Subagent use can reduce context load but may create integration drift; orchestrator should keep final architecture, validation, and tracker updates centralized.

## Follow-on Backlog

Update after the first implementation slice.

- Additional scorer-backed scenarios for remaining uncovered active scorer families.
- Workshop-inspired evidence search and payload retrieval over Raidar artifacts.
- Durable annotations over runs, scorer results, commands, requirements, and artifacts.
- Phase tree/timeline over Raidar execution evidence.
- Finding-to-rerun and finding-to-scenario promotion workflow.
- Optional Workshop-compatible export adapter for interoperability experiments.
