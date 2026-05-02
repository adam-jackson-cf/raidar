---
name: "create-scenario"
description: "Create reusable scenario baselines with stable scoring contracts. USE WHEN you need to author a new benchmark scenario baseline."
---

# Workflow

### Step 0: Clarify Benchmark Intent

- **Purpose**: Define the scenario objective before choosing artifacts or scoring.
- **When**: Run before scenario authoring or score-profile selection.
- Capture the target task, intended agent capability, constraints, non-goals, and expected evidence.
- Separate scenario difficulty from scoring mechanics.
- Do not encode one-off lessons from unrelated scenarios into the new scenario.
- Workflow: [references/step-0-clarify-benchmark-intent-workflow.md](references/step-0-clarify-benchmark-intent-workflow.md)

### Step 1: Select Scoring Contract

- **Purpose**: Choose a stable score profile before the baseline is run.
- **When**: Run after benchmark intent is explicit and before authoring comparable revisions.
- Select metrics and score_profile weights that measure the intended evidence.
- Ensure every weighted metric has supporting scenario configuration.
- Treat score_profile id, weighted metrics, weights, verification, and acceptance as the comparable benchmark contract.
- Workflow: [references/step-1-select-scoring-contract-workflow.md](references/step-1-select-scoring-contract-workflow.md)

### Step 2: Author Baseline

- **Purpose**: Create a baseline scenario that can be compared with future revisions.
- **When**: Run after the scoring contract is selected.
- Use the public scenario creation workflow for new scenario roots.
- Author prompt, rules, starter, verification, acceptance, metrics, and score_profile consistently.
- Keep instructions conceptual and reusable; avoid scenario-specific reward hacking.
- Workflow: [references/step-2-author-baseline-workflow.md](references/step-2-author-baseline-workflow.md)

### Step 3: Validate Baseline

- **Purpose**: Verify the scenario contract before benchmark execution.
- **When**: Run after baseline files are authored and before benchmark execution.
- Run scenario validation.
- Check that the score_profile references only enabled metrics.
- Confirm the baseline can be executed without relying on future revision assumptions.
- Workflow: [references/step-3-validate-baseline-workflow.md](references/step-3-validate-baseline-workflow.md)

### Step 4: Run Baseline

- **Purpose**: Produce evidence for the starting benchmark point.
- **When**: Run after baseline validation passes.
- Run the target AgentSpec against the baseline before creating any revision.
- Record aggregate and run-level results.
- Do not pre-author future revisions before analysing the baseline evidence.
- Workflow: [references/step-4-run-baseline-workflow.md](references/step-4-run-baseline-workflow.md)

## Output

### Result Format

- Report selected score profile, scenario files changed, validation status, baseline run evidence, and any limitations.
- Explicitly state whether the scenario is ready for revision work.
- Do not claim future improvement before revisions are run.
