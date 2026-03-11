# Review Surface Evidence Model

This document defines the evidence contract for the review surface. The goal is to make every diagnosis, benchmark comparison, and recommendation traceable to concrete artifacts rather than to detached scores.

## Evidence Principles

- Evidence must sit beside diagnosis, not behind a second navigation step.
- Scenario-family evidence leads. Generic metrics support it.
- Benchmark evidence must appear next to current evidence in the same block.
- Missing evidence is a first-class state. It must lower confidence or suppress claims.
- Every review claim must be traceable to a stable artifact path or derived evidence block.

## Common Evidence Envelope

Every representative experiment should expose the following envelope before scenario-family details are rendered:

- review identity: scenario, revision, agent, model, evaluation profile
- representative selection reason
- benchmark identity and compatibility status
- previous representative experiment, when available
- scored-run count, unscored count, unresolved unscored count
- evidence completeness summary
- scenario-family subtype label for `Scenario Fidelity`

## Evidence Anchor Runs

The detail view needs one `current` run and one `benchmark` run for high-signal side-by-side evidence.

Rules:

1. Prefer valid scored runs.
2. Choose the run closest to the representative experiment median on the primary scenario-fidelity metric.
3. If no valid scored run exists, choose the closest scored run and mark it atypical.
4. If no scored run exists, the scenario-family evidence block becomes `Unavailable`.

This rule prevents the detail view from presenting an unusually good or bad outlier as the whole experiment.

## Visual Scenario Evidence Contract

`visual-ui-implementation` scenarios must provide the following blocks.

### Primary Evidence Strip

The first evidence block must show:

- reference image
- current evidence anchor screenshot
- benchmark evidence anchor screenshot
- visual diff for current versus reference

Captions must state:

- capture status
- similarity score
- threshold
- benchmark delta

### Region Evidence Cards

Visual scenarios should provide local evidence cards for authored regions such as `hero`, `features`, or `footer`.

Each region card must include:

- region name
- current score
- benchmark score
- delta versus benchmark
- threshold result or authored pass/fail result
- direct link or highlight into the underlying evidence

If region evidence is missing, the surface must:

- show a region-evidence gap state
- cap `Confidence` at `Low`
- avoid claiming local strengths or weaknesses with high certainty

### Supporting Visual Evidence

Visual scenarios may also expose:

- DOM or text snippets tied to authored requirements
- changed file clusters for UI components
- trace excerpts showing verification behavior

These are supporting blocks. They do not replace the screenshot-led strip.

## Non-Visual Scenario Evidence Contract

Non-visual scenarios still need a scenario-family evidence model. The detail view must not fall back to generic metric tiles.

For `code-delivery-nonvisual` tasks, the evidence strip should provide:

- outcome proof: deterministic requirement results and authored acceptance outcomes
- implementation proof: changed file clusters and code-diff excerpts tied to affected areas
- verification proof: required gate results and gate history
- runtime proof when relevant: request/response samples, CLI output, or contract evidence
- benchmark proof: the benchmark experiment's equivalent evidence blocks shown side by side

### Local Evidence Cards For Non-Visual Tasks

Non-visual scenarios should use authored requirement clusters or subsystem clusters as the local evidence unit.

Each local evidence card must include:

- cluster name
- current status
- benchmark status
- delta or contrast statement
- direct link into the supporting artifact

Examples:

- `auth flow`
- `API contract`
- `migration safety`
- `test coverage of requirements`

## Benchmark Evidence Requirements

Benchmark evidence is only valid when all of the following hold:

- same `scenario_name`
- same `scenario_revision`
- compatible `evaluation_profile`
- same starter fingerprint, or an explicit incompatible-state warning

Rules:

- The benchmark must be shown beside the current output, not in a separate metric panel.
- If the starter fingerprint changed, benchmark comparison remains visible but must be labelled `Changed Baseline`.
- If no compatible benchmark exists, the review surface must say so explicitly and suppress directional benchmark claims.

## Change-Context Requirements

Every Experiment Review should show what changed since the previous representative experiment for the same configuration.

The change context model must surface:

- harness change
- model change
- prompt, rules, or system-pack change
- scenario revision change
- starter fingerprint change
- evaluation profile change
- rerun or repeat-count change

Changes should be grouped into one of these categories:

- `Model`
- `Harness`
- `Prompting and context`
- `Tooling and verification`
- `Scenario contract`

The surface must not imply causal improvement if the scenario revision or starter changed without saying so.

## Evidence Availability Rules

The evidence layer should track expected blocks by scenario family and mark each block as one of:

- `Present`
- `Missing`
- `Incompatible`
- `Not Applicable`

This availability grid feeds `Confidence.evidence_completeness`.

## Raw Artifact Mapping

The evidence model should map each review block back to canonical artifacts.

| Review block | Primary source |
| --- | --- |
| representative sample summary | `experiment-summary.json` |
| run-level scoring and checks | `runs/*/run.json` |
| visual screenshot, diff, and similarity | verifier outputs under the run directory |
| gate history | `run.json.gate_history` |
| traces and command behavior | `run.json.traces` |
| changed files and workspace outputs | run workspace artifacts |
| authored scenario contract | `scenario.yaml` |

## Evidence Quality Rules

- A `Strength`, `Weakness`, or `Opportunity` item must cite at least one evidence block id.
- A benchmark comparison statement must cite both current and benchmark evidence.
- A local evidence card without a direct artifact link is not complete.
- If the current evidence anchor run is atypical, the UI must say so.
- Evidence blocks should deep-link to raw artifacts rather than re-rendering everything in the review surface.
