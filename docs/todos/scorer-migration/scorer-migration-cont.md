# Scorer Canonicalization Cleanup Plan

## Summary
Refactor scorer architecture so registered scorers are concrete implementations only, `CodeTaskScorer` is the internal interface for language-specific code-task implementations, and legacy acceptance scorer/schema/metric terminology is removed in favor of requirements coverage and requirements adherence.

This is a breaking cleanup with no compatibility layer for legacy `acceptance` YAML, `acceptance` metric IDs, or the registered `code-task` scorer.

## Key Implementation Changes

### Code-Task Interface
- Remove the registered generic `CodeTask` scorer class entirely; `code-task` must not exist in the scorer registry.
- Convert `CodeTaskScorer` into the internal interface for language-specific code-task scorers.
- Move the current `CODE_TASK_METRICS` contract onto `CodeTaskScorer` as a class-owned `default_metrics()` helper.
- Require every `CodeTaskScorer` subclass to expose exactly these metric IDs:
  - `functional`
  - `code-quality`
  - `test-coverage`
  - `artifact-checks`
  - `verification-stability`
- Keep `typescript-code-task` and `python-code-task` as the concrete code-task implementations using `CodeTaskScorer.default_metrics()`.
- Move `bugfix` and `refactor` off `CodeTaskScorer`; keep them as proposed standalone `BaseScorer` implementations with their own first-pass contracts.

### Metric Contract Metadata
- Extend `ScorerMetricDefinition` with required structured fields:
  - `evidence`: what evidence the metric consumes
  - `score_derivation`: how the 0..1 score is derived
  - `pass_fail`: what makes the metric pass or fail
- Update the shared `metric(...)` helper to require those three fields as keyword-only arguments.
- Update every active and proposed scorer definition to provide those fields.
- Add tests that all registered scorer metrics expose non-empty `evidence`, `score_derivation`, and `pass_fail`.

### Proposed Concrete Scorers
- Keep proposed concrete scorers registered and loadable:
  - `python-code-task`
  - `bugfix`
  - `refactor`
  - `plan-to-code`
  - `test-generation`
- Keep them non-attachable while `status = "proposed"`.
- Give each proposed scorer a first-pass usable metric contract through the new structured metric fields.
- Do not promote any proposed scorer to `active` in this cleanup.

### Requirements Replace Acceptance
- Remove `raidar.scorers.acceptance` from scorer registration and delete the `Acceptance` scorer implementation.
- Remove `acceptance` from `CoreMetricId`, `MetricId`, score fallback maps, CSV/report columns, and scenario profile expectations.
- Replace scenario YAML shape exactly:
  - remove `acceptance`
  - add `requirements`
  - define `RequirementsConfig`
  - use `requirements.items` as the canonical list of requirement specs
- Remove scenario-wide `deterministic_checks`; requirement-level checks under `requirements.items[*].check` are the only deterministic requirement coverage input.
- Update runtime task bundles and verifier scenario specs to emit `requirements.items`, never `acceptance.requirements`.
- Update requirements scorer and LLM judge prompt assembly to read `scenario.requirements.items`.
- Remove top-level `acceptance` from scorecard/runtime output; requirement outcomes are represented by `requirements_coverage`, `metric_scores`, and `scorer_results`.

### Shared Scorer Helpers
- Centralize true duplicated mechanics:
  - metric definition construction
  - required artifact extraction
  - missing artifact detection
  - coverage ratio scoring
  - verification-stability `MetricScore` construction
- Add a code-task artifact scoring helper used by Python and TypeScript, parameterized by language label, files, tests, workspace, and required artifact patterns.
- Keep language-specific command execution, static checks, source/test discovery, and evidence wording in the concrete Python/TypeScript modules.

## Public API / Schema Changes
- Removed scorer IDs:
  - `acceptance`
  - `code-task`
- Removed metric ID:
  - `acceptance`
- Scenario YAML now uses:
  - `requirements.items`
- Scenario YAML no longer allows:
  - `acceptance`
  - `acceptance.requirements`
  - `acceptance.deterministic_checks`
- Removed/legacy references should fail as unknown or extra inputs, not as proposed/inactive definitions.

## Test Plan
- Schema and registry tests:
  - `load_scorer_definition("code-task", 1)` fails as unknown.
  - `load_scorer_definition("acceptance", 1)` fails as unknown.
  - legacy `acceptance` YAML is rejected.
  - canonical `requirements.items` YAML validates.
  - `requirements-coverage` requires `requirements.items`.
  - all registered scorer metrics have non-empty `evidence`, `score_derivation`, and `pass_fail`.
- Code-task tests:
  - `CodeTaskScorer` owns the exact five-metric interface.
  - `typescript-code-task` and `python-code-task` match that interface.
  - `bugfix` and `refactor` are not `CodeTaskScorer` subclasses.
- Requirements tests:
  - requirements coverage uses `requirements.items[*].check`.
  - requirements adherence judge receives requirements from `requirements.items`.
- Runtime/reporting tests:
  - task bundle emits `requirements.items`, not `acceptance`.
  - scorecard serialization has no top-level `acceptance`.
  - reports and CSV no longer include `acceptance_score`.
- Final inventory:
  - run `rg -n "acceptance|Acceptance" -g '!docs/**' -g '!scenarios/**' -g '!experiments/**'`
  - remove or rename every hit unless it is clearly non-legacy generic prose in an external judge prompt
  - explicitly verify no code, tests, schemas, generated specs, CLI output, reports, or fixtures retain legacy acceptance terminology
- Final validation:
  - run focused scorer/schema/runtime tests
  - run `make quality`

## Assumptions
- `requirements.items` is the canonical replacement for the old `acceptance.requirements` shape.
- Scenario-wide deterministic checks are removed, not renamed.
- `bugfix` and `refactor` are proposed standalone scorers, not language-specific code-task interface implementations.
- Proposed concrete scorers remain registered and loadable but not attachable until promoted.
- No backward compatibility is added for removed scorer IDs, metric IDs, or YAML fields.
