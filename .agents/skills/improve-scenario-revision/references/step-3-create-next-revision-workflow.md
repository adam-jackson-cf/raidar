# Step 3 Workflow: Create Next Revision

## Objective

Create exactly one comparable next revision.

## Required actions

1. Clone the latest accepted revision.
2. Apply only the selected change.
3. Verify the cloned scenario has `parent_revision` set to the latest accepted revision.
4. Preserve `scorers[]`, scenario metric overrides, verification, acceptance, and visual config unless starting a new baseline.
5. For matrix-backed comparisons, add or update only the candidate matrix entries needed to run the new revision with the same AgentSpecs and experiment settings.

## Done when

- One new revision exists.
- Revision lineage points to the latest accepted revision.
- Matrix-backed comparison entries target the new `scenario_revision` when used.
- Future revisions were not pre-authored.
- Comparable contract remains unchanged.
