# RAIDAR Framework Comparison

## Purpose

RAIDAR is not a generic LLM eval toolkit. Its purpose is to evaluate real project-delivery tasks by treating a **delivery scenario** and a **harness + model pair** as first-class experimental objects.

In practical terms, RAIDAR is designed to help answer questions like:

- Which harness + model pair is best for this delivery task?
- What should be changed next: prompt, rules, starter, scenario design, or harness/model choice?
- Is a run good enough in ways that matter to delivery, not just to prompt quality?

That makes RAIDAR closer to an opinionated delivery-evaluation system than to a generic prompt-eval, benchmark, or metrics library.

## Key Differentiators

### Delivery-first evaluation unit

RAIDAR is centered on an explicit **scenario contract** for delivery work rather than on a single prompt, test case, or benchmark item. This gives it a stronger fit for tasks that resemble implementation, refactoring, verification, and review work in a real repository.

### `AgentSpec = harness + model`

RAIDAR makes the **harness/runtime** part of the experimental unit, not just the model. That is the clearest differentiator surfaced by the comparison work. The core question is not only "which model is better?" but "which harness + model pair performs best for this kind of delivery task?"

### Decision-grade evidence for delivery

RAIDAR appears more opinionated about the evidence needed to make delivery decisions. The emphasis is on outcomes such as:

- functional correctness
- acceptance and verification quality
- execution validity
- efficiency
- repeat stability
- visual quality where relevant

This is more delivery-specific than the default evidence model in the shortlisted alternatives.

### Improvement loop, not just leaderboard output

RAIDAR is designed to support decisions about what to improve next:

- prompt design
- rules
- starter scaffolding (project context, linting, tools etc)
- scenario design
- harness choice
- model choice

That makes it a tool for harness engineering and delivery optimisation, not only for comparative reporting.

## Comparison Matrix

Scoring rubric:

- `1` = adjacent only
- `2` = weak fit / major scaffolding needed
- `3` = partial fit
- `4` = strong fit with notable gaps
- `5` = first-class fit for RAIDAR-style needs

The weights below come from the RAIDAR-specific comparison work and intentionally emphasize delivery-scenario authoring, harness-as-variable experimentation, repeated experiment matrices, and custom scoring.

| Dimension | Weight | RAIDAR | Inspect AI | Promptfoo | DeepEval |
| --- | ---: | ---: | ---: | ---: | ---: |
| Scenario/task contract authoring flexibility | 14 | 5 | 4 | 3 | 2 |
| Harness/runtime as experimental variable | 16 | 5 | 5 | 3 | 1 |
| Model abstraction and multi-model execution | 8 | 5 | 5 | 5 | 2 |
| Matrix/repeats/sweep support | 12 | 5 | 5 | 4 | 2 |
| Custom metrics/scorers/evaluator composition | 12 | 5 | 5 | 5 | 5 |
| Agentic/multi-step/tool/file execution support | 10 | 5 | 5 | 4 | 3 |
| Run orchestration, retries, logs, resumability | 10 | 4 | 5 | 4 | 3 |
| Fit for real project delivery tasks | 8 | 5 | 4 | 4 | 2 |
| Result comparison ergonomics | 5 | 4 | 4 | 5 | 2 |
| Maturity, maintenance, license, cost | 5 | 2 | 4 | 5 | 5 |
| **Unweighted total / 50** |  | **45** | **46** | **42** | **27** |
| **Weighted total / 100** |  | **94.0** | **93.6** | **80.0** | **51.0** |

## How To Read The Scores

RAIDAR scores highest when the matrix reflects its own purpose. That is expected and useful. The goal of the self-score is not to prove RAIDAR is "best" in the abstract, but to show where it is intentionally shaped around delivery evaluation decisions.

The most important rows are:

- **Scenario/task contract authoring flexibility**
- **Harness/runtime as experimental variable**
- **Matrix/repeats/sweep support**
- **Fit for real project delivery tasks**

These are the rows where RAIDAR and Inspect AI are strongest, and where Promptfoo and DeepEval begin to separate into lighter comparison tooling and companion metric infrastructure rather than direct substitutes.

The main weakness in RAIDAR's current public profile is **maturity**:

- public repo maturity is still low compared with the shortlist
- no public license is currently visible
- public adoption signals are minimal compared with the alternatives

That weakness does not undercut the delivery-specific design, but it does matter for portability, reuse, and external adoption.

## Practical Takeaways

### RAIDAR vs Inspect AI

Inspect AI is the closest architectural comparison point. It is the best reference if the question is how to structure reusable tasks, execution plans, scorers, logs, retries, and multi-model runs.

RAIDAR is still more opinionated about delivery scenarios and about harness + model pair selection as a decision problem.

### RAIDAR vs Promptfoo

Promptfoo is the best lightweight comparison tool in the shortlist. It is strongest on fast side-by-side comparisons, custom assertions, and practical benchmark ergonomics.

RAIDAR is stronger where the evaluation needs to remain anchored to delivery scenarios and to harness engineering decisions.

### RAIDAR vs DeepEval

DeepEval is best understood as a companion evaluation layer, not a direct replacement. It is strongest on evaluators, metrics, tracing, and regression-style evaluation.

RAIDAR is stronger where the missing piece is scenario execution and harness/model comparison.

## Recommended Framing

For future comparison work, the most accurate framing is:

- **Best architectural comparison point:** Inspect AI
- **Best practical lightweight comparison point:** Promptfoo
- **Best companion metric/evaluation layer:** DeepEval
- **Most delivery-opinionated system for harness + model decisions:** RAIDAR

## Sources

- RAIDAR: <https://github.com/adam-jackson-cf/raidar>
- Inspect AI: <https://github.com/UKGovernmentBEIS/inspect_ai>
- Inspect docs: <https://inspect.aisi.org.uk/>
- Promptfoo: <https://github.com/promptfoo/promptfoo>
- Promptfoo docs: <https://www.promptfoo.dev/docs/intro/>
- DeepEval: <https://github.com/confident-ai/deepeval>
- DeepEval docs: <https://deepeval.com/docs/getting-started>
