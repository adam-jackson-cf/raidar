# Homepage Scenario Walkthrough

Use the homepage scenario as the reference example for how Raidar scenario design works at a high level. It is a good teaching scenario because it combines product requirements, quality gates, test expectations, visual comparison, and efficiency/ranking signals in one contract.

## Why This Scenario Works As A Teaching Example

- The task is easy to understand: implement a SaaS landing page from a reference image.
- The scenario mixes deterministic requirements, test expectations, scorer-based quality assessment, visual comparison, and efficiency/ranking signals.
- It uses a visual baseline, which makes the difference between correctness and quality easier to explain.
- It is representative of day-to-day delivery work: build UI, satisfy requirements, keep tests green, and avoid fragile behavior.

## Scenario Anatomy

- `prompt/task.md` defines the user-facing job: implement the page, run the required verification commands, cover every requirement with tests, and only report completion after those commands succeed.
- `environment` selects the `node:20` runtime stack and declares the concrete inventory needed for the scenario workflow.
- `starter/` defines the baseline workspace the harness starts from.
- `rules/` defines local coding guidance for the scenario. These rules should support the scenario contract rather than redefine conflicting gates.
- `verification.setup_actions` prepares the starter workspace before preflight and gate execution.
- `verification.required_commands` defines the commands the run is expected to satisfy before completion.
- `verification.gates` defines the tracked gate history used during scoring and stability analysis.
- `requirements.items` defines the required product outcomes, deterministic checks, and test evidence expectations.
- `scorers[]` attaches `design-to-code` and `resource-efficiency`, including their scenario-level weights.
- `visual` defines the reference image, capture command, and similarity threshold for visual review.

## What Each Layer Teaches

- The prompt teaches task framing.
- The runtime stack teaches what concrete tools the scenario depends on.
- The starter teaches what the harness inherits versus what it must create.
- Verification teaches the difference between "did the workflow pass" and "did it pass consistently."
- Requirements teach the difference between output obligations and toolchain success.
- Scorers teach which grouped delivery-task judgments matter for comparison, diagnosis, and ranking.
- Metrics teach how each scorer is measured.
- Visual config teaches that a scenario can care about appearance, not just code output.

## Running The Scenario

Use these supported entrypoints from the repo root:

```bash
make scenario-info SCENARIO_DIR=scenarios/homepage-implementation/v001
make scenario-validate SCENARIO=scenarios/homepage-implementation/v001/scenario.yaml
make experiment-run SCENARIO=scenarios/homepage-implementation/v001/scenario.yaml HARNESS=... MODEL=...
make experiments-list
```

Use `make matrix-run` when you want a structured comparison across multiple `AgentSpec`s instead of a single experiment run. Author the matrix config with `matrix.id`, `matrix.scenario`, `matrix.experiment`, and `matrix.entries`; each entry declares `scenario_revision` and a nested `agent` with `harness`, `provider`, `model`, and optional `reasoning_effort`.

## What This Scenario Can Tell You

- Can a harness build the required homepage and keep the normal quality workflow intact?
- Does it satisfy the stated business requirements, not just build successfully?
- Does it add or update tests that actually cover those requirements?
- Does the implementation stay visually close to the intended design?
- Is the run stable enough to trust, or does it only pass after repeated verification churn?
- If two runs are both valid, which one is more resource-efficient?

## What This Scenario Cannot Tell You On Its Own

- How a harness performs outside UI-heavy delivery work.
- Whether a model is universally "better" rather than better for this scenario contract.
- Whether the reference design itself was the right product choice.
- Whether the scenario contract captures every qualitative judgment a reviewer may still care about.

## Review Flow

- Start with `make scenario-info` to understand the active contract.
- Run the scenario and inspect `experiments/.../runs/*/run.json` for one-repeat details.
- Use `experiment-summary.json` to compare repeats for the same `(scenario, revision, harness, model, evaluation_profile)` identity.
- Inspect `scores.scorer_results[]` for the quality and efficiency blend, and `scores.metric_scores[]` for individual metric evidence.
- Use [metrics.md](metrics.md) when you need metric-by-metric interpretation.
