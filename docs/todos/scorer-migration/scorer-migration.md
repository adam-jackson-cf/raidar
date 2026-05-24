# Canonical Scorer-Owned Migration Plan

## Summary

Refactor scoring so every metric score is emitted by a selected scorer class. Runtime code may still collect shared evidence, but it must not globally score metrics. Universal concerns become standalone scorers, so scenarios compose scorers explicitly, for example `typescript-code-task + requirements + resource-efficiency`.

## Phase 1: Make Concrete Code-Task Scorers Canonical

- Change TypeScript scenarios and scenario generation to reference `typescript-code-task` instead of generic `code-task`.
- Change `code-task` from an attachable active scorer to a proposed/interface-only family definition; concrete children like `typescript-code-task` and `python-code-task` remain attachable.
- Bring `PythonCodeTask` to parity with `TypeScriptCodeTask`:
  - respect scenario `artifact-checks.required_paths`
  - emit all code-task metric scores itself
  - keep status `proposed` until its deterministic command strategy is reviewed.
- Update tests and fixtures that still expect `code-task@1` in runnable TypeScript scenarios.

## Phase 2: Move All Metric Scoring Into Scorer Classes

- Refactor `runtime/scoring_outputs.py` so it only:
  - creates `ScorerContext`
  - instantiates selected scorer classes
  - collects their `MetricScore`s
  - builds scorer result contributions.
- Remove `_core_metric_scores()` as a scoring source for selected scenario metrics.
- Keep shared evidence builders in runtime, but not shared metric ownership:
  - verifier outputs remain evidence
  - execution validity remains evidence
  - process metrics remain evidence
  - visual outputs remain evidence.
- Add scorer implementations for universal metrics:
  - `resource-efficiency` emits `resource-efficiency`
  - `execution-validity` emits `execution-validity` if we want it scenario-selectable
  - `requirements` emits `requirements-adherence`
- Ensure unresolved selected metrics fail loudly with “scorer did not emit metric” rather than being filled by a global fallback.

## Phase 3: Split Legacy Domains Out Of Existing Scorers

- Remove `acceptance` and `requirements-coverage` from non-design scorer families unless they are intentionally redesigned as scorer-owned metrics.
- Treat deterministic acceptance checks as verifier evidence, not a default global score.
- Keep `requirements` as the conceptual requirements scorer; expand it later with deterministic requirement submetrics where possible.
- Keep `design-to-code` active, but make it a real scorer-owned implementation:
  - emits `visual-regression`
  - emits any design-specific artifact checks
  - uses verifier visual evidence, but owns the metric interpretation.
- Leave `plan-to-code`, `bugfix`, `refactor`, and `test-generation` as proposed until each is redesigned with explicit scorer-owned metrics.

## Phase 4: Clean Schema, Reporting, and Fixtures

- Narrow schema metric types so `core` no longer means “globally scored”; either rename it to a scorer-owned metric type or remove the distinction where it is misleading.
- Update scenario validation so metric dependency checks are scorer/domain aware:
  - `visual-regression` requires visual config only when selected by a visual/design scorer
  - `test-coverage` requires coverage evidence only when selected by a scorer that uses it
  - `requirements-adherence` requires requirement context only through the `requirements` scorer.
- Rename or clarify reporting fields where needed:
  - `evaluation_profile` currently represents scorer composition; either keep the field but document it as scorer profile, or migrate to `scorer_profile`.
- Update experiment summaries, CLI tests, storage tests, and smoke fixtures to expect scorer-owned outputs rather than legacy metric profiles.
- Remove stale YAML scorer definition assumptions and any tests that describe YAML fallback behavior.

## Test Plan

- Schema tests:
  - generic `code-task` cannot attach to scenarios once made interface/proposed
  - `typescript-code-task + requirements + resource-efficiency` resolves cleanly
  - unknown scorer config keys still fail.
- Runtime tests:
  - selected metrics are present only when emitted by selected scorers
  - missing scorer output fails explicitly
  - no global fallback silently supplies `functional`, `test-coverage`, `artifact-checks`, `visual-regression`, or `requirements-coverage`.
- Scorer tests:
  - `typescript-code-task` emits all code-task metrics and enforces configured artifacts
  - `resource-efficiency` emits its metric from process evidence
  - `requirements` emits `requirements-adherence` through Codex judge
  - `design-to-code` emits visual/design-owned metrics from visual evidence.
- Scenario tests:
  - `hello-world-smoke` and coding benchmark revisions validate with concrete scorer children
  - matrix config still runs against `skill-benchmark-coding-test@v003`.
- Do not run `make quality` during this transition unless explicitly requested.

## Assumptions

- “Everything scorer owned” means runtime can collect evidence, but only scorer classes can convert evidence into metric scores.
- Universal metrics are modeled as standalone selectable scorers, not implicit global defaults.
- Generic `code-task` remains useful as a family/interface contract, but should not be used directly by runnable scenarios.
- `requirements` is the conceptual scorer name; `requirements-adherence` remains its internal LLM-as-judge metric.
