# Requirements Freeze

- Created: 2026-03-25
- Last updated: 2026-03-25T14:34:53Z

## Captured Inputs Playback

- Scope and user-visible outcomes:
  - Create a deterministic brownfield ExecPlan package for the next round of orchestrator/performance and repo-quality improvements.
  - Base the plan on current repository structure after scanning the live reference files named by the performance and code-quality findings.
  - Produce a plan that can be handed to an implementer without rediscovering the current hotspots or the key sequencing decisions.
- Constraints and non-goals:
  - Keep the plan generic at the orchestrator/platform layer rather than tied to one scenario.
  - Exclude `scenarios/**` from implementation scope as a hard requirement.
  - Avoid adding authoring overhead for new or swapped scenarios.
  - Do not proceed beyond the requirements-freeze checkpoint until the user explicitly confirms the playback.
- User-provided artifacts and starting views:
  - Current conversation findings about orchestrator cache/image reuse and smoke behavior.
  - Repo-wide code quality report at `/Users/adamjackson/Projects/raidar/.enaible/analyze-code-quality/20260325T100903Z-code-quality-review/`.
  - User direction that scenarios are transient and should not become fixed coupling points.
- Assumptions to validate with user:
  - Include aggregate reporting of new cache metadata as a planned follow-on, not just `run.json` persistence.

## Frozen Requirements

- R1: Produce a brownfield implementation plan for orchestrator spin-up reuse and observability that resolves the remaining live Docker image warm-path issue and extends cache visibility into persisted and aggregate reporting.
- R2: Produce a brownfield refactor plan for the highest-value code-quality hotspots: typed request/option objects for CLI-heavy entrypoints, decomposition of `_promotion_guard`, consolidation of shared provider-adapter behavior, and targeted tests for low-coverage operational modules.
- R3: Exclude scenario files from implementation scope entirely, including starter folders; the plan must not schedule code or structural changes under `scenarios/**`.
- R4: Add and enforce repo guidance that `scenarios/**/starter/**` is excluded from analysis and code-quality checks because those files are representative delivery-scenario artifacts rather than canonical shared product code.

## Verification Decision

- Existing verification present:
  - yes: `make quality`, `make orchestrator-smoke`, `make smoke-matrix`, and targeted orchestrator pytest coverage
- If missing, user decision (`approved-change-scoped`|`declined-blocked`|`n/a-existing`):
  - n/a-existing
- Minimum smoke gate command:
  - `make orchestrator-smoke`

## Confirmation

- Confirmation prompt:
  - Confirm the requirements playback in `/Users/adamjackson/Projects/raidar/.plan/create-execplan/20260325T143453Z/workspace/requirements-freeze.md` is final and I should proceed to context analysis.
- Confirmed by user at:
  - 2026-03-25T15:02:23Z
- User approval response (verbatim excerpt):
  - "proceed"
- Confirmation note:
  - Step 1 approved; proceed to Context Pack construction using local repository evidence only.
