# Raidar Eval Suite Improvement Goal

## Goal Statement

Transform Raidar into a fully realised eval suite with scorers that enable effective delivery-activity measurement to support experimentation through iteration. That requires a review surface for findings that enables users to understand what is happening, what has worked, and where problems or potential improvements lie through easy surfacing of relevant evidence that helps drive decisions on the next iteration. This is achieved through a scenario-driven evaluation suite for agentic software delivery by implementing scorer-backed scenario coverage and matrix/reporting improvements that feed a Raidar-native findings/review surface inspired by Raindrop Workshop. The work should preserve Raidar's core mechanism—scenario revisions, AgentSpecs, repeated run evidence, deterministic/hybrid scoring, and public `make ...` workflows—while using the charter review and Workshop comparison as the source of truth for backlog priorities, process-measurement gaps, and review UX direction.

## Objective

Turn the charter-review analysis into an executable improvement plan and implementation sequence that expands Raidar's ability to measure both delivery outcomes and delivery process quality for agentic engineering.

The implementation should prioritize:

1. Scenario and scorer coverage for material suite gaps identified in `charter-review.md`.
2. Workshop-inspired findings surface that helps users understand why a run scored as it did and what evidence to inspect next.
3. Experimentation workflows that let users compare agentic delivery interventions across scenario revisions, matrices, and retained evidence.
4. Workshop-inspired evidence review concepts adapted to Raidar artifacts, not a replacement of Raidar with Workshop.

## Outcome

The goal succeeds when Raidar has a coherent, validated fully implemented eval suite:

- controlled, repeatable scenario evaluation of agentic engineering delivery;
- scorers that are effective at measuring quality and efficiency of activities across planning, context management, code generation, code quality, verification, design and discovery, quality of delivered artifacts;
- retained evidence for delivery-process quality;
- actionable findings that support iteration through experiments;
- review surface that supports measurement, analysis and hypothesis formation

## Source Inputs

Use these files as primary inputs:

- `docs/todos/charter-review/charter-review.md`
- `docs/todos/charter-review/raindrop-workshop-comparison.md`

Key conclusions to preserve:

- Raidar remains the benchmark mechanism.
- Raidar's main comparison unit is `scenario revision x AgentSpec x repeated run evidence`.
- Deterministic scoring is preferred where retained evidence is sufficient; bounded LLM-as-judge or hybrid scoring is acceptable for semantic residuals.
- Active scorer families without authorable scenario coverage are material gaps.
- Workshop's strongest ideas to adopt are trace-style review, evidence search, finding surfacing, annotations, and replay/failure-to-eval workflow framing—not replacement of Raidar scenarios or scorers.

## Scope

### In scope

Phased implementation may include:

1. **Planning and sequencing**
  - Convert charter backlog items into an implementation plan.
  - Define dependencies between scenario work, scorer work, matrix work, and findings/review work.
2. **Scenario and scorer coverage**
  - Add or revise scenarios for one or more material gaps, especially active scorer families without scenario coverage:
    - `bugfix@1`
    - `refactor@1`
    - `test-generation@1`
    - `python-code-task@1`
    - `plan-to-code@1`
  - Prefer one high-signal first scenario/revision over broad shallow coverage.
  - Use `make scenario-clone-revision` for revisions inside existing scenario roots.
  - Use `make scenario-init` only for genuinely new scenario roots.
3. **Scoring and evidence improvements**
  - Improve scorer integration, scorecard evidence, or platform evidence where needed to make scenario results defensible.
  - Preserve scorer IDs unless the user explicitly approves lifecycle changes.
  - Prefer deterministic evidence and hybrid caps over unbounded semantic judging.
4. **Findings/review surface**
  - Create a Raidar-native findings layer over existing or newly retained artifacts.
  - Findings should explain concrete review items such as failed gates, missing required commands, missing artifacts, requirements gaps, deterministic caps on judge scores, resource outliers, completion-claim inconsistencies, and repeat variance.
  - Use Workshop's `issue | good | note` framing and evidence-linked review concepts where useful.
  - Keep findings non-authoritative unless promoted into scorer/scenario behavior.
5. **Workshop-inspired adaptation**
  - Use Workshop source concepts as implementation references where useful:
    - run outline / span tree / payload review;
    - searchable evidence retrieval;
    - annotations and finding chips;
    - replay UX as inspiration for Raidar-native rerun or failure-to-scenario workflows.
  - Adapt concepts around Raidar artifacts such as `run.json`, scorecards, experiment summaries, command records, gate history, workspace diffs, scorer evidence, requirements mapping, and benchmark-view data.

### Out of scope unless separately approved

- Replacing Raidar scenarios with Workshop replays.
- Replacing Raidar scorers with agent-written assertions as the primary scoring mechanism.
- Making Workshop's daemon, database, or UI the authoritative Raidar runtime.
- Retiring, renaming, or changing scorer IDs without explicit approval.
- Broad documentation restructuring outside the minimum needed for implemented changes.
- Reading or modifying other `docs/todos/` directories without explicit user consent.
- Treating scenario `starter/` folders as canonical shared product code for broad quality checks.

## Approach Context

Follow repo workflow constraints:

- Public interface is repo-root `make ...`.
- Use `make help` for command discovery.
- Use `make scenario-info` to inspect scenario contracts.
- Use `make scenario-clone-revision SCENARIO_DIR=scenarios/<scenario-id> FROM_REVISION=v001 [TO_REVISION=v002]` for revisions inside existing scenario roots.
- Use `make scenario-init` only for brand-new scenario roots.
- Exclude `scenarios/**/starter/**` from broad analysis and code-quality checks by default unless the specific scenario work requires targeted starter edits.
- Treat `scenarios/` and `experiments/` as build-generated/runtime artifacts by default unless the request explicitly requires scenario or experiment work.
- Task completion requires `make quality` to pass.

Execution constraints:

- The goal orchestrator should use suitable reasoning-level subagents for scoped implementation and review tasks where doing so preserves main-thread context and reduces token cost. Use subagents deliberately: delegate bounded discovery, implementation slices, test/debug loops, and review passes; keep final decisions, integration, and tracker updates in the orchestrator.
- Synthetic benchmark data may be created in the existing shape of a benchmark run to speed up findings/review-surface development and provide stable UI/API test fixtures. Synthetic data must be clearly labeled as synthetic and must not be treated as real benchmark evidence.
- Real benchmark runs, when needed, should use GPT 5.5 with low reasoning by default to control cost unless the user explicitly approves a different model, higher reasoning level, or broader matrix.

## Decision Boundaries

Executor may decide:

- Which charter backlog item is the best first implementation slice.
- Whether the first scenario work should be a new scenario root or revision of an existing root, following repo decision rules.
- Whether findings should first appear in JSON artifacts, experiment summaries, benchmark-view, or a combination.
- Which Workshop review concepts to copy, adapt, or defer.
- Which validation commands are necessary in addition to `make quality`.
- Which scoped tasks are appropriate for subagent delegation, and what reasoning level is sufficient for each delegated task.
- Whether synthetic benchmark-shaped data is enough to validate a review-surface change before running real benchmarks.

Executor must not decide without user approval:

- To replace Raidar's scenario/scorer/matrix mechanism with Workshop runtime concepts.
- To retire, rename, or replace any scorer ID.
- To introduce a broad new scorer family beyond the scoped implementation need.
- To restructure public documentation or make `README.md` stop being the sole human entrypoint.
- To read or modify other `docs/todos/` directories.
- To run expensive live benchmark matrices beyond the minimum needed for validation, unless approved.
- To use a model other than GPT 5.5 low reasoning for real benchmark runs without approval, except where the repo workflow makes that impossible and the deviation is recorded.
- To present synthetic benchmark data as real benchmark evidence.

## Definition of Done

A later executor may mark this goal complete only when:

1. The charter-review source documents have been reviewed and their material recommendations have been translated into an implementation sequence.
2. At least one high-signal scorer/scenario coverage gap has been improved with scenario, scorer, matrix, or platform work as appropriate.
3. A Raidar-native findings/review surface MVP exists and is populated from Raidar artifacts, with evidence links or references sufficient for review.
4. The resulting workflow helps users measure both delivery outcomes and delivery-process quality for agentic engineering.
5. Public `make ...` workflows remain the supported interface.
6. Any changed scenario, matrix, scorer, reporting, or benchmark-view behavior is covered by appropriate tests or validation commands.
7. `make quality` passes.
8. `docs/todos/charter-review/goal-tracker.md` is updated with decisions, implemented scope, validation results, residual risks, and follow-on backlog.

## Tracker Maintenance

Maintain `docs/todos/charter-review/goal-tracker.md` during execution. Record:

- phase status;
- decisions and rationale;
- selected scenario/scorer/finding-surface slice;
- files changed;
- validation commands and results;
- rejected or deferred alternatives;
- residual risks;
- follow-on backlog.

Do not use the tracker to mark this goal complete unless the definition of done is actually satisfied.