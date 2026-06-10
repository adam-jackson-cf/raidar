# Raidar Charter Review Goal Tracker

## Goal Statement

Create a repo-grounded Raidar charter review under `docs/todos/charter-review/` that defines Raidar's evaluation purpose, maps current scenario/scorer/platform coverage, identifies material gaps, and proposes high-level backlog items without implementing those items or changing scorer/scenario behavior.

## Deliverables

- Review document: `docs/todos/charter-review/charter-review.md`
- Tracker: `docs/todos/charter-review/goal-tracker.md`

## Decisions

- Treat Raidar as a scenario evaluation suite for agentic engineering delivery outcomes plus delivery-process quality, not as a generic prompt-output evaluator.
- Use scenario revision x AgentSpec x repeated run evidence as the main comparison unit.
- Prefer deterministic scoring when retained evidence can prove the claim; use LLM-as-judge only for bounded semantic residuals; use hybrid scoring where deterministic prerequisites should cap semantic judgment.
- Treat active scorer families without authorable scenario coverage as material backlog gaps.
- Treat planning/orchestration benchmarks as artifact-contract work first; do not assume interactive orchestration machinery is required before static plan/handoff evidence is evaluated.
- Keep future implementation out of this goal: no scorer code, scenario files, benchmark runs, scorer ID changes, or broad docs restructuring.

## Evidence Sources Used

- Public workflow: `README.md`, `Makefile`, `make help`, `make scenario-list`, `make scenario-info`, `make harness-list`, `make experiments-list`.
- Reference docs permitted by the goal: `docs/references/metrics.md`, `docs/references/new-scenario.md`, `docs/references/orchestration-flow.md`, `docs/references/new-harness.md`, `docs/references/raidar-framework-comparison.md`.
- Scenario contracts, excluding starters: `scenarios/hello-world-smoke/v001/scenario.yaml`, `scenarios/homepage-implementation/v001/scenario.yaml`, `scenarios/homepage-implementation/v002/scenario.yaml`, `scenarios/skill-benchmark-coding-test/v001/scenario.yaml`, `scenarios/skill-benchmark-coding-test/v002/scenario.yaml`, `scenarios/skill-benchmark-coding-test/v003/scenario.yaml`, prompt files, and rules files.
- Scorer and scoring code: `orchestrator/src/raidar/scorers/*`, `orchestrator/src/raidar/runtime/scorecard.py`, `orchestrator/src/raidar/runtime/process_metrics.py`, `orchestrator/src/raidar/schemas/scenario.py`, `orchestrator/src/raidar/schemas/scorecard.py`.
- Harness/matrix/platform code: `orchestrator/src/raidar/agents/*`, `orchestrator/src/raidar/matrix.py`, `orchestrator/src/raidar/application/*`, `orchestrator/src/raidar/commands/*`, `orchestrator/src/raidar/storage.py`.
- Stored matrices: `matrices/hello-world-smoke-codex-gpt55.yaml`, `matrices/hello-world-smoke-trio.yaml`, `matrices/homepage-implementation-codex-gpt55.yaml`, `matrices/skill-benchmark-coding-test-v003-codex-gpt55.yaml`.
- Existing generated evidence only: `experiments/**/run.json`, `experiments/**/experiment-summary.json`, `benchmark-view/src/data.json`, `benchmark-view/scripts/build-data.mjs`.
- Tests as behavioral evidence: scorer registration tests, LLM-as-judge tests, TypeScript scorer tests, runtime scorecard/process tests, matrix and CLI tests.

## Coverage Conclusions

- Current authorable scenario roots: `hello-world-smoke`, `homepage-implementation`, `skill-benchmark-coding-test`.
- Current authorable revisions: six total.
- Scenario-attached active scorers: `design-to-code@1`, `typescript-code-task@1`, `requirements@1`, `resource-efficiency@1`.
- Active scorers with no current scenario coverage: `bugfix@1`, `plan-to-code@1`, `python-code-task@1`, `refactor@1`, `test-generation@1`.
- Current matrices support smoke, Codex low/medium comparisons, and revision comparisons; main delivery-quality matrices are not broad multi-harness, high-repeat benchmark definitions.
- Platform evidence surfaces are comparatively mature: run scorecards, metric/scorer results, gate history, process metadata, experiment summaries, dashboard rows, scenario diffs, and revision deltas.
- Dashboard-only synthetic benchmarks are useful historical/platform evidence but are not current authorable scenario roots.

## Material Backlog Conclusions

The review proposes backlog items for:

1. Targeted bug-fix scenario root.
2. Behavior-preserving refactor scenario root.
3. Test-generation/coverage-lift scenario root.
4. Python code-task scenario root.
5. Plan-to-code artifact benchmark.
6. Requirements scorer expansion into UI/product work.
7. Rules/linting intervention revision pair.
8. Static orchestration evidence packet platform capability.
9. Dashboard-only synthetic benchmark alignment.
10. Multi-harness quality scenario matrices.
11. Responsive/accessibility UI delivery scenario coverage.
12. Workflow/process score visibility refinement.

Each item in the review includes purpose, delivery activity measured, candidate scorer(s), required evidence, why current coverage is insufficient, and work-type classification.

## Rejected or Deferred Paths

- Deferred interactive orchestration machinery until a static retained-artifact representation is proven insufficient.
- Deferred any scorer implementation, scorer ID lifecycle change, scenario edit, or matrix execution.
- Deferred starter-directory inspection because the goal can be satisfied from scenario contracts, platform code, tests, and retained artifacts while respecting repo analysis defaults.
- Deferred broad documentation cleanup despite evidence of reference-doc drift; this goal is charter review/backlog, not docs restructuring.

## Residual Risks

- No live benchmark/scenario proof runs were performed.
- Existing experiment artifacts demonstrate historical evidence shape but not current runtime health.
- Scenario starter internals were not inspected.
- Synthetic dashboard artifact provenance needs a separate focused audit before conversion to scenario roots.
- Reference docs appear partly stale relative to the current scorer registry/schema; future docs work should confirm and correct that drift.
- CodeGraph MCP was invoked first for indexed discovery but returned compressed source payloads in this session, so targeted file reads were used for detailed evidence.

## Verification Results

`make quality` passed on 2026-06-10 after review/tracker edits.
