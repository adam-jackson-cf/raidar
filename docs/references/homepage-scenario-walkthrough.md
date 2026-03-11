# Homepage Scenario Walkthrough

Use the homepage scenario as the reference example for how Raidar scenario design works at a high level. It is a good teaching scenario because it combines product requirements, quality gates, test expectations, visual comparison, and efficiency/ranking signals in one contract.

## Why This Scenario Works As A Teaching Example

- The task is easy to understand: implement a SaaS landing page from a reference image.
- The scenario mixes deterministic requirements, test expectations, and subjective review via `llm-judge`.
- It uses a visual baseline, which makes the difference between correctness and quality easier to explain.
- It is representative of day-to-day delivery work: build UI, satisfy requirements, keep tests green, and avoid fragile behavior.

## Scenario Anatomy

- `prompt/task.md` defines the user-facing job: implement the page, run the required verification commands, cover every requirement with tests, and only report completion after those commands succeed.
- `starter/` defines the baseline workspace the agent starts from.
- `rules/` defines local coding guidance for the scenario. These rules should support the scenario contract rather than redefine conflicting gates.
- `verification.required_commands` defines the commands the run is expected to satisfy before completion.
- `verification.gates` defines the tracked gate history used during scoring and stability analysis.
- `acceptance.requirements` defines the required product outcomes and the test patterns that should cover them.
- `acceptance.deterministic_checks` captures simple pass/fail content and structure checks.
- `acceptance.llm_judge_rubric` captures subjective criteria that are hard to score deterministically.
- `metrics[]` defines which capability signals are active for comparison and review.
- `visual` defines the reference image, capture command, and similarity threshold for visual review.

## What Each Layer Teaches

- The prompt teaches task framing.
- The starter teaches what the agent inherits versus what it must create.
- Verification teaches the difference between "did the workflow pass" and "did it pass consistently."
- Acceptance teaches the difference between output requirements and toolchain success.
- Metrics teach which signals matter for comparison, diagnosis, and ranking.
- Visual config teaches that a scenario can care about appearance, not just code output.

## Running The Scenario

Use these supported entrypoints from the repo root:

```bash
make scenario-info SCENARIO_DIR=scenarios/homepage-implementation/v001
make scenario-validate SCENARIO=scenarios/homepage-implementation/v001/scenario.yaml
make experiment-run SCENARIO=scenarios/homepage-implementation/v001/scenario.yaml AGENT=... MODEL=...
make experiments-list
```

Use `make matrix-run` when you want a structured comparison across multiple models or agents instead of a single experiment run.

## What This Scenario Can Tell You

- Can an agent build the required homepage and keep the normal quality workflow intact?
- Does it satisfy the stated business requirements, not just build successfully?
- Does it add or update tests that actually cover those requirements?
- Does the implementation stay visually close to the intended design?
- Is the run stable enough to trust, or does it only pass after repeated verification churn?
- If two runs are both valid, which one is more resource-efficient?

## What This Scenario Cannot Tell You On Its Own

- How an agent performs outside UI-heavy delivery work.
- Whether a model is universally "better" rather than better for this scenario contract.
- Whether the reference design itself was the right product choice.
- Whether the scenario contract captures every qualitative judgment a reviewer may still care about.

## Review Flow

- Start with `make scenario-info` to understand the active contract.
- Run the scenario and inspect `experiments/.../runs/*/run.json` for one-repeat details.
- Use `experiment-summary.json` to compare repeats for the same `(scenario, revision, agent, model, evaluation_profile)` identity.
- Use [analyze-results.md](/Users/adamjackson/Projects/raidar/docs/todos/analyze-results.md) for the review procedure and [metrics.md](/Users/adamjackson/Projects/raidar/docs/references/metrics.md) when you need metric-by-metric interpretation.
