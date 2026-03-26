# Context Discovery

- Created: 2026-03-25
- Last updated: 2026-03-25T14:34:53Z

## Clarification Rounds

- Round 1:
  - User narrowed the performance focus to the orchestrator and its Docker image behavior, explicitly excluding homepage-scenario deep-dives.
  - User stated a strong constraint against scenario-specific coupling because scenarios are transient and should not require extra authoring burden when they change.
- Round 2:
  - User requested an ExecPlan that incorporates both the prior orchestrator/performance recommendations and the supplied repo-wide code quality report.
  - User explicitly required a scan of the currently referenced implementation files before plan creation because refactors have already happened since earlier analysis.

## Approved Requirements (pre-freeze draft)

- R1: Produce a brownfield implementation plan for orchestrator spin-up reuse and observability that addresses the remaining warm-path Docker image reuse problem, cache-state visibility, and generic reuse semantics at the orchestrator layer.
- R2: Produce a brownfield refactor plan for the highest-value code quality hotspots: wide CLI/request assembly seams, `_promotion_guard`, duplicated provider adapter behavior, and targeted operational coverage gaps.
- R3: Exclude scenario files from implementation scope entirely; no code or structural changes should be planned for `scenarios/**`, including starter folders.
- R4: Add and enforce repo guidance that `scenarios/**/starter/**` is excluded from analysis and code-quality checks because those files are meant to represent delivery scenarios rather than canonical shared product code.

## Provided Artifacts + Starting Views

- User-provided artifacts:
  - Performance findings and recommendations from earlier orchestrator spin-up analysis in this conversation.
  - Repo-wide code quality report rooted at `/Users/adamjackson/Projects/raidar/.enaible/analyze-code-quality/20260325T100903Z-code-quality-review/`.
  - Explicit skill requirement to use `create-execplan` and scan current references before drafting.
- User-provided constraints/views:
  - Focus on orchestrator and Docker image behavior over individual scenario optimization.
  - Avoid tying implementation to specific scenarios because scenarios are transient.
  - Exclude scenarios from any planned changes as a hard requirement.
  - Plan must be deterministic and handoff-ready.
- Assumptions inferred from provided artifacts:
  - Existing completed work around baseline/preflight cache reuse should be treated as current state, and the plan should pick up from that point instead of re-planning already landed changes.
  - The unresolved live issue is fast-image non-reuse across full invocations, not baseline/preflight reuse.

## Verification Baseline Capture

- Existing verification present: yes
- Existing verification commands and scope:
  - `make quality` is the repo-wide completion gate.
  - `make orchestrator-smoke` exists for the default smoke scenario and currently exposes the live fast-image retention issue across separate invocations.
  - `make smoke-matrix` exists for cross-model smoke coverage of the default smoke scenario.
  - Targeted orchestrator pytest coverage exists for runner, CLI, and Harbor environment behavior.
- If missing, did user approve adding change-scoped verification:
  - n/a-existing
- Additional current-state note:
  - Prior live smoke evidence in this conversation showed warm baseline and preflight hits but continued image misses and regenerated `fast-image-build.log` artifacts across reruns.
  - The user has now explicitly removed `scenarios/**` from implementation scope and requested an `AGENTS.md` note that excludes starter folders from analysis/code-quality scrutiny.

## Online Research Permissions

- Online research allowed: not required for Step 1; use local repository state plus user-provided artifacts.
- Approved domains/APIs: none needed unless later context analysis uncovers an external Docker/Harbor fact gap.
- Recency expectation: rely on the current local repo state as of 2026-03-25.
- Restricted domains/sources: default to no browsing unless a later checkpoint explicitly requires it.
