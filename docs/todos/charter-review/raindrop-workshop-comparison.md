# Raidar vs Raindrop Workshop Evaluation Comparison

Date: 2026-06-10  
Preset: `technical-deep-dive`  
Status: comparative assessment; no scorer, scenario, matrix, or runtime changes.

## Scope

This comparison uses the Raidar charter review in `docs/todos/charter-review/charter-review.md` as the Raidar baseline and compares it against the Raindrop Workshop evaluation loop as represented by the public `raindrop-ai/workshop` repository.

Terminology note: the public project is named **Raindrop Workshop**. This document treats the user phrase "Raindrops Workshop eval suite" as referring to Raindrop Workshop's local trace debugger, replay, MCP trace tools, and agent-written eval loop rather than a fixed public benchmark corpus.

## Evidence Base

Raidar evidence:

- `docs/todos/charter-review/charter-review.md`.
- `docs/todos/charter-review/goal-tracker.md`.
- Current charter-review conclusions: Raidar is a scenario evaluation suite for agentic engineering delivery outcomes plus delivery-process quality; the main comparison unit is `scenario revision x AgentSpec x repeated run evidence`; deterministic scoring is preferred where evidence is sufficient, with bounded LLM-as-judge or hybrid scoring for semantic residuals.

Raindrop Workshop evidence:

- Public repository: <https://github.com/raindrop-ai/workshop>.
- README: Workshop is described as a local debugger that streams every token, tool call, and span; lets Claude Code read traces, write evals, fix failures; supports a self-healing eval loop and local replay.
- `skills/instrument-agent/SKILL.md`: instrumentation workflow emphasizes one real agent entry point first, minimal useful trace visibility, then enrichment with LLM/tool spans and useful properties.
- `skills/setup-agent-replay/SKILL.md`: replay scaffolds a local HTTP endpoint, uses trace-prefilled context, supports model/system/user-message overrides, and should avoid side effects through dependency injection, dry-run flags, no-op adapters, or transaction rollback.
- `src/mcp/tools.ts`: MCP surface exposes trace querying, run outlines, span payload retrieval, annotations, captured-agent debugging, local replay, trace search, span context, and cloud trace import.

## Executive Comparison

Raidar and Raindrop Workshop optimize for different evaluation jobs.

Raidar is stronger as a **controlled benchmark suite**. It defines scenario contracts, scorer families, repeatable matrices, quality/resource scorecards, and retained benchmark artifacts. It is better suited to answer: "Which AgentSpec delivers this software task better under the same scenario contract, and did the outcome meet a stable rubric?"

Raindrop Workshop is stronger as an **observability-driven debugging and eval authoring loop**. It captures rich live traces from real agents, exposes spans and payloads to coding agents, supports local replay, and encourages agents to write assertions against observed failures. It is better suited to answer: "Why did this agent fail in this real run, can we replay the failure safely, and can we create an eval that prevents recurrence?"

For measuring agentic engineering, Raidar has the better structure for comparative outcome measurement; Workshop has the better raw telemetry and replay machinery for process diagnosis and failure-to-eval conversion. The strongest combined approach would use Workshop-like trace capture and replay to generate or debug Raidar scenarios, while preserving Raidar's stable scenario/scorer/matrix contracts for benchmark comparability.

## Strengths and Weaknesses

| Dimension | Raidar strengths | Raidar weaknesses | Raindrop Workshop strengths | Raindrop Workshop weaknesses |
|---|---|---|---|---|
| Evaluation unit | Explicit scenario revisions, AgentSpecs, repeats, matrices, and scorecards. Good for apples-to-apples model/harness comparisons. | Current scenario set is small; several active scorer families have no scenario coverage. | Real runs and spans are first-class. Good for inspecting actual agent behavior in situ. | Less naturally a benchmark unit; replayed runs are not automatically normalized into scenario revisions, repeat counts, or stable scorecards. |
| Outcome measurement | Deterministic gates, scorer families, requirements coverage, resource-efficiency metrics, composite scorecards, retained run artifacts. | Current coverage over-indexes TypeScript utility and homepage UI scenarios; bugfix/refactor/test-generation/Python/plan-to-code are identified gaps. | Agent-written assertions can target concrete observed failures; replay can validate fixes against the same trace context. | Assertion quality depends on the authoring agent/human. Without an external benchmark contract, tests can overfit to one trace. |
| Process measurement | Captures process metrics such as command counts, failures, verification rounds, duration, token counts, completion integrity, and atomic-commit discipline. | Process evidence is mostly coarse retained metadata; planning/orchestration artifacts are proposed but not yet mature benchmark contracts. | Fine-grained trace telemetry: tokens, tool calls, spans, payloads, live events, annotations, subagent indications, and replay context. | Telemetry is rich but not inherently scored. It diagnoses process behavior better than it ranks process quality across agents. |
| Reproducibility | Scenarios and matrices can be rerun across AgentSpecs with controlled scoring. | Existing generated artifacts demonstrate historical evidence shape, not necessarily current runtime health unless rerun. | Replay scaffolding can rerun a production/local trace against real agent code with modified prompt/model/context. | Replay fidelity depends on instrumentation coverage, side-effect isolation, and stable external dependencies. |
| Semantic assessment | Supports hybrid scoring: deterministic prerequisites cap LLM-as-judge where semantic judgment is necessary. | Semantic work beyond simple requirements/UI coverage is still under-expanded. | Trace-informed agents can inspect rich context and create tailored semantic assertions. | No obvious fixed policy for judge calibration, scorer versioning, or repeatable semantic rubric comparability. |
| Harness/model comparison | Harness registry and matrices are built for cross-agent comparison, even if current delivery-quality matrices are limited. | Multi-harness quality matrices are a backlog item. | Supports many SDKs/languages/providers/coding agents through instrumentation. | Broad integration support is not the same as benchmark comparability across those agents. |
| Failure analysis | Run artifacts and scorecards identify what failed at scenario/scorer level. | Less suited to deep causal inspection unless artifacts retain enough process detail. | Excellent causal inspection surface: query traces, search spans, inspect payloads, annotate evidence, ask captured-agent context, replay failures. | Strong debugging loop can become ad hoc unless failures are promoted into durable, versioned eval assets. |
| Governance | Scorer IDs, scenario versions, matrices, and make-based workflows support controlled lifecycle management. | Governance can slow fast exploratory eval creation. | Fast local loop; coding agent can instrument, inspect, write evals, fix, and rerun. | Governance/versioning of evals appears less central than local iteration and observability. |

## Ability to Measure Delivery Outcomes

### Raidar

Raidar is currently stronger for delivery-outcome measurement because it treats outcomes as stable scenario artifacts scored under explicit contracts. Its evidence model supports:

- functional correctness through required commands, tests, gates, and scorer checks;
- requirements coverage and adherence where `requirements@1` is attached;
- UI/design delivery quality through `design-to-code@1`;
- TypeScript implementation quality through `typescript-code-task@1`;
- resource efficiency through `resource-efficiency@1`;
- retained scorecards and experiment summaries for comparison across repeats.

The main limitation is coverage breadth. The charter review identifies material gaps for active scorer families without authorable scenario coverage: `bugfix@1`, `plan-to-code@1`, `python-code-task@1`, `refactor@1`, and `test-generation@1`. That means Raidar's evaluation architecture is outcome-oriented, but the current suite does not yet cover enough delivery modes to represent the full engineering lifecycle.

### Raindrop Workshop

Workshop measures delivery outcomes indirectly through trace-backed assertions and replay results. Its strongest outcome capability is converting a real observed failure into a reproducible local test/eval loop. It is well suited to:

- validate that a particular production/local failure no longer occurs;
- compare a replayed prompt/model/context against a known trace;
- generate focused assertions from observed bad behavior;
- preserve run-level annotations and span evidence explaining why a run passed or failed.

Its weakness is benchmark generality. A Workshop-authored eval can be strong for a concrete failure mode, but the public Workshop model does not itself define a scenario taxonomy, scorer registry, matrix schema, repeat policy, or versioned benchmark comparability contract equivalent to Raidar.

## Ability to Measure Delivery Processes

### Raidar

Raidar measures delivery process through retained runtime metadata and scorer-visible evidence. It can already represent:

- verification discipline: required commands, gate failures, retries, coverage thresholds;
- implementation discipline: changed files, retained artifacts, atomic commits where required;
- resource/process cost: duration, token counts, command counts, failed command categories;
- scenario discipline: completion claims versus actual gate state.

The gap is process granularity. Current Raidar process evidence is strong enough to compare high-level delivery discipline, but weaker for causal reconstruction of agent reasoning, tool-selection mistakes, subagent coordination, or plan drift unless those are explicitly retained as artifacts. The charter backlog item for static orchestration evidence packets is the right next step: plan, delegation, worker-result, and integration artifacts can make planning/orchestration measurable without requiring interactive orchestration machinery first.

### Raindrop Workshop

Workshop is stronger for raw process observability. It can expose:

- token, tool-call, span, and payload streams;
- live events and run/span structure;
- searchable payloads and attributes;
- annotations for durable findings;
- captured-agent debugging and replay against registered local agents;
- prefilled replay context from real traces.

This makes Workshop very strong for diagnosing process failures: wrong tool called, missing context, malformed prompt, model drift, hidden exception, incomplete tool result handling, bad subagent handoff, or prompt/model sensitivity. Its weakness is that observability is not scoring. Without an additional rubric layer, Workshop can show the process, but it does not necessarily say whether the process was good, comparable, efficient, or repeatably better than another process.

## Agentic Engineering Fit

| Agentic engineering need | Better fit | Reason |
|---|---|---|
| Rank models/harnesses on a stable delivery task | Raidar | Scenario revisions, AgentSpecs, repeats, matrices, and scorecards are designed for controlled comparison. |
| Debug why one real agent run failed | Workshop | Trace search, span payloads, annotations, captured-agent context, and replay are purpose-built for causal inspection. |
| Convert production failures into regression evals | Workshop first, then Raidar | Workshop can import/inspect/replay the trace; Raidar can later canonicalize the failure as a scenario or scorer-backed benchmark. |
| Measure final software artifact quality | Raidar | Scorers and deterministic gates target functional, requirement, artifact, and quality outcomes. |
| Measure planning and orchestration quality | Neither is complete alone | Raidar has the benchmark charter/backlog; Workshop has trace/subagent visibility. Raidar needs retained planning/orchestration artifact contracts; Workshop needs scoring/rubric governance. |
| Measure agent skills/rules/linting interventions | Raidar for experiment design; Workshop for diagnosis | Raidar can create controlled revision pairs; Workshop can explain why the intervention changed behavior. |
| Fast local iterative eval authoring | Workshop | The agent-written eval/replay loop is optimized for local debugging speed. |
| Longitudinal benchmark reporting | Raidar | Stored experiments, summaries, and dashboard rows are closer to benchmark reporting than Workshop traces. |

## Core Tradeoff

The core tradeoff is **control versus observability**.

Raidar controls the task boundary. That makes its measurements more comparable, but the process signal is limited to what the harness retains and scorers consume.

Workshop observes the agent boundary. That makes its process signal richer, but the evaluation contract is more ad hoc unless trace-derived evals are promoted into durable, versioned benchmark assets.

For agentic engineering delivery, both are useful, but they answer different questions:

- Raidar: "Did this agent deliver the task under a stable contract, and how does it compare to other AgentSpecs?"
- Workshop: "What actually happened inside this agent run, and can we replay/fix/assert against that behavior?"

## Practical Integration Path

1. **Use Workshop to harvest high-fidelity failures.** Instrument representative agents, inspect failed runs, annotate causal issues, and replay with modified user message/model/system prompt/context.
2. **Promote recurring Workshop failures into Raidar backlog items.** A recurring trace failure should become a Raidar scenario root, scenario revision, scorer refinement, or platform-evidence proposal only after the failure generalizes beyond one trace.
3. **Keep Raidar as the benchmark authority.** Once promoted, the task should get a scenario contract, scorer references, required evidence, and matrix coverage. Workshop traces should be input evidence, not the final benchmark contract.
4. **Add Workshop-like trace fields only where they improve Raidar scoring.** Avoid importing every span by default. Retain the minimum process evidence needed for scoring: plan artifacts, tool-call summaries, verification loop history, prompt/rule provenance, failure categories, and replay linkage where relevant.
5. **Use Raidar to test process interventions.** Skills/rules/linting/planning/orchestration interventions should be modeled as controlled scenario revisions or scenario pairs, with Workshop used to explain the mechanism behind score changes.

## Bottom Line

Raidar is the stronger foundation for measuring **delivery outcomes** and producing defensible cross-agent benchmark comparisons. Raindrop Workshop is the stronger foundation for measuring and debugging **delivery process behavior** at trace level.

Raidar's main weakness is coverage maturity: it needs more scenario roots/revisions for bug fixing, refactoring, test generation, Python, plan-to-code, orchestration artifacts, and rules/linting interventions. Workshop's main weakness is evaluation governance: it has rich traces, replay, and agent-authored eval loops, but those do not automatically produce stable, versioned, repeatable benchmark comparisons.

The best architecture is complementary: Workshop discovers and explains failures; Raidar codifies generalized failures into stable scenarios and measures agentic engineering delivery outcomes over time.
