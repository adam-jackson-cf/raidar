---
name: "improve-scenario-revision"
description: "Improve one scenario revision at a time from run evidence. USE WHEN you need to create the next comparable scenario revision."
---

# Workflow

### Step 0: Load Prior Evidence

- **Purpose**: Use completed run evidence before deciding revision changes.
- **When**: Run before creating or editing the next revision.
- Inspect the latest completed revision run evidence.
- Review aggregate results, run-level failures, unscored reasons, and gate outcomes.
- Do not create or edit the next revision before evidence has been analysed.
- Workflow: [references/step-0-load-prior-evidence-workflow.md](references/step-0-load-prior-evidence-workflow.md)

### Step 1: Confirm Comparability

- **Purpose**: Ensure the next revision keeps the same benchmark contract.
- **When**: Run after prior evidence is loaded and before selecting changes.
- Compare score_profile id, weighted metrics, weights, metrics, verification, and acceptance against the baseline.
- If any contract element needs to change, stop and start a new baseline instead.
- Treat prompt, rules, starter guidance, and non-contract implementation guidance as the normal revision surface.
- Workflow: [references/step-1-confirm-comparability-workflow.md](references/step-1-confirm-comparability-workflow.md)

### Step 2: Select Improvement Hypothesis

- **Purpose**: Choose one evidence-backed change for the next revision.
- **When**: Run after comparability is confirmed.
- Identify the strongest generalizable failure mode or inefficiency.
- Prefer deterministic enforcement gaps before prompt-only tuning.
- Reject scenario-specific fixes that only target a known individual run.
- Workflow: [references/step-2-select-improvement-hypothesis-workflow.md](references/step-2-select-improvement-hypothesis-workflow.md)

### Step 3: Create Next Revision

- **Purpose**: Author exactly one next comparable revision.
- **When**: Run after one improvement hypothesis is selected.
- Clone from the latest accepted revision.
- Apply only the selected generalizable improvement.
- Do not author later revisions in the same pass.
- Workflow: [references/step-3-create-next-revision-workflow.md](references/step-3-create-next-revision-workflow.md)

### Step 4: Run And Analyse Revision

- **Purpose**: Determine whether the revision actually improved under the stable contract.
- **When**: Run after the candidate revision validates.
- Validate and run the new revision with the same target AgentSpec and comparable run shape.
- Compare composite, quality, diagnostic, validity, gate stability, resource use, and unscored counts.
- Classify the result as improved, tied with secondary gains, regressed, or inconclusive.
- Workflow: [references/step-4-run-and-analyse-revision-workflow.md](references/step-4-run-and-analyse-revision-workflow.md)

### Step 5: Accept Or Reject Candidate

- **Purpose**: Avoid counting failed candidates as successful revisions.
- **When**: Run after candidate revision evidence exists.
- Accept the revision only when evidence supports the stated improvement.
- If the candidate regresses or is inconclusive, archive or revise it before proceeding.
- Report strict composite improvement separately from secondary efficiency or stability gains.
- Workflow: [references/step-5-accept-or-reject-candidate-workflow.md](references/step-5-accept-or-reject-candidate-workflow.md)

## Output

### Result Format

- Report prior evidence, unchanged contract check, selected hypothesis, files changed, validation status, run evidence, and accept/reject decision.
- State whether improvement was strict composite improvement, secondary improvement, tie, regression, or inconclusive.
- State whether another revision should be created next, based only on completed evidence.
