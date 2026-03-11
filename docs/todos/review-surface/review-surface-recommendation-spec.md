# Review Surface Recommendation Spec

This document defines how the review surface should turn diagnosis into a useful `next experiment` recommendation without overstating what the evidence supports.

## Recommendation Objective

Recommendations exist to help a reviewer choose the next best intervention for a scenario. They are not generic tips, and they are not a substitute for the evidence blocks that justify them.

Each recommendation should answer:

- what to change
- why that change is the best next lever
- which weakness it targets
- what evidence supports the hypothesis
- how confident the system is

## Recommendation Object

Each recommendation should contain:

- `title`: short hypothesis label
- `target_dimension`: one canonical dimension
- `comparator`: benchmark, self-trend, or evidence gap
- `hypothesis`: what change to try
- `expected_gain`: what should improve if the hypothesis is right
- `evidence_refs`: one or more cited evidence blocks
- `confidence`: recommendation confidence, separate from experiment confidence
- `effort`: `Low`, `Medium`, or `High`
- `validation_plan`: how to test the recommendation in the next experiment

## Intervention Taxonomy

Every recommendation should fall into one of these categories.

1. `Prompting and task framing`
2. `Decomposition and planning`
3. `Context and asset provisioning`
4. `Verification workflow`
5. `Implementation approach`
6. `Harness or model choice`
7. `Scenario contract remediation`

This taxonomy keeps the surface from collapsing all advice into vague prompt edits.

## Evidence-To-Recommendation Rules

Recommendations must be derived using the following rules.

### Rule 1: Start From A Weakness Cluster

Each recommendation must map to a specific weakness or evidence gap. Do not generate recommendations from top-line scores alone.

### Rule 2: Use Comparator Context

The recommendation must state whether it is driven by:

- a benchmark gap
- a self-trend regression
- an evidence or confidence gap

If comparator context is unavailable, the recommendation must say so.

### Rule 3: Prefer The Highest-Leverage Controllable Lever

Rank candidate recommendations by:

1. expected impact on the weakest important dimension
2. specificity of supporting evidence
3. controllability by the experiment owner
4. effort to test

### Rule 4: Keep Recommendations Hypothesis-Shaped

The system must not claim that a change will work. It may only claim that the evidence suggests a promising lever to test next.

## Pattern Library

The recommendation layer should use recurring diagnosis patterns.

| Pattern | Recommendation direction |
| --- | --- |
| Behind benchmark on `Scenario Fidelity`, near parity on `Task Fidelity` | focus on design replication, decomposition, or asset-context interventions |
| Strong fidelity, weak `Workflow Discipline` | tighten verification workflow or guardrails |
| Strong task outcome, weak `Execution Reliability` | reduce instability before optimizing fidelity |
| Low `Confidence` because of sample size or missing evidence | recommend more runs or evidence completion before optimization |
| Gap caused by authored scenario mismatch | recommend scenario contract remediation before harness tuning |

## Confidence Gating

Recommendation confidence is derived from experiment confidence plus evidence specificity.

### When Recommendations Are Allowed

- At least one weakness or evidence gap is clearly identified.
- The supporting evidence block is present.
- Comparator context is not `Unavailable`.

### When Recommendations Must Be Hedged

- experiment confidence is `Low`
- benchmark delta is `Inconclusive`
- supporting evidence is partial rather than complete

### When Recommendations Must Abstain

Do not produce an optimization recommendation when:

- experiment confidence is `Very Low`
- primary scenario-family evidence is missing
- benchmark comparison is unavailable and self-trend is also unavailable
- the dominant issue is a scenario contract defect rather than harness behavior

In these cases, the system should emit `Need stronger evidence before recommending a change`.

## Wording Templates

| Condition | Template |
| --- | --- |
| high-confidence benchmark gap | `Hypothesis: {lever} could close the gap in {dimension} because {evidence}.` |
| medium-confidence benchmark gap | `Likely next test: {lever} to improve {dimension}; current evidence suggests {evidence}.` |
| low-confidence signal | `Possible next test: {lever}. Evidence is still limited because {confidence_gap}.` |
| abstain | `Do not recommend an optimization yet. Gather stronger evidence by {validation_plan}.` |

Recommendations should never use language such as `will fix`, `definitively improves`, or `proves`.

## Validation Plan Rules

Each recommendation must include a validation plan describing the next experiment.

The validation plan should specify:

- the intervention to change
- the benchmark or previous representative to compare against
- the dimension expected to move
- the evidence block that should confirm or reject the hypothesis

Validation plans should be small enough to isolate one lever when possible.

## Recommendation Count And Ordering

- Show at most three recommendations in the detail view.
- The first recommendation should be the clearest high-leverage next move.
- Lower-confidence or higher-effort ideas should appear later.
- If the only honest recommendation is evidence gathering, show one recommendation, not filler.

## Homepage Scenario Guidance

For homepage-style scenarios, recommendations should prefer:

- design-replication interventions before efficiency tweaks when fidelity is the main gap
- verification-process interventions only when workflow evidence is actually weak
- scenario remediation when the authored checks and requirement language are misaligned

This keeps the review surface aligned to the motivating use case instead of drifting back toward generic runtime optimization.
