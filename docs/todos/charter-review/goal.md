# Raidar Charter Review Goal

## Objective

Produce a Raidar charter review, grounded in a repo deep dive, that defines the suite's evaluation objective, maps current scenario/scorer/platform coverage against that objective, identifies material gaps, and creates a high-level backlog of proposed scenario, revision, scorer, and platform-capability work.

## Goal Statement

Create a review document under `docs/todos/charter-review/` that first deep-dives the repository to understand Raidar's existing concepts, workflows, scenario model, scorer model, harness/runtime behavior, matrices, artifacts, and public make-based interface; then uses that evidence to frame Raidar as a flexible evaluation suite for agentic engineering delivery outcomes plus delivery process quality, audit current coverage, identify gaps, and define a prioritized high-level backlog of new or revised scenario briefs and platform capability proposals without implementing or running any of that follow-on work.

## Outcome

This goal succeeds when the charter review provides a durable decision foundation for future Raidar work:

- The review demonstrates evidence-backed understanding of the repo concepts that feed into Raidar's evaluation model.
- Raidar's evaluation purpose is stated clearly enough to guide scenario, scorer, and harness decisions.
- Current scenario, scorer, and platform evidence coverage is mapped against that purpose.
- Coverage gaps are identified without overfitting to a single example such as planning-mode benchmarking.
- Each material gap has a concrete follow-on backlog item or scenario/revision brief.
- Future executors can use the review to create implementation goals without re-litigating Raidar's evaluation charter.

## Scope

In scope:

- Deep dive the repository before finalizing the charter framing, using repo evidence to understand the concepts that define what Raidar offers and aims to do.
- Review existing public workflow surfaces, scenario/revision structure, scorer definitions, scorer attachment/configuration, harness/runtime behavior, matrix configuration, experiment artifacts, reporting/storage surfaces, and relevant tests or schemas.
- Define Raidar's objective as measuring agentic software delivery outcomes plus delivery process quality.
- Define evaluation principles, including deterministic scoring preference where evidence is sufficient and LLM-as-judge or hybrid scoring only where justified.
- Create a delivery-activity taxonomy covering both delivered artifacts and process behaviors.
- Audit current scenarios, revisions, active scorers, and visible platform evidence surfaces against that taxonomy.
- Identify material gaps, including gaps around under-tested scorers, skills/rules/linting interventions, planning artifacts, orchestration methods, and other delivery-process measurement needs.
- Define a high-level backlog of proposed new scenario roots, scenario revisions, scorer refinements, scorer proposals, or platform capability proposals.
- For each proposed scenario or revision brief, include:
  - Purpose.
  - Delivery activity measured.
  - Candidate scorer or scorers.
  - Required evidence.
  - Why current coverage is insufficient.
- Include decision rules for:
  - Deterministic vs LLM-as-judge vs hybrid scoring.
  - Creating a new scenario root vs revising an existing scenario.
  - Treating a gap as scorer work, scenario work, harness/platform work, or documentation/workflow clarification.
- Include platform-gap analysis for orchestration and planning-mode benchmarks, while considering the simplest sufficient observable representation first. For example, prefer evaluating whether pre-produced planning artifacts in scenario starter material are enough before proposing complex interactive harness capabilities.

## Non-goals

- Do not change scorer code.
- Do not change scenario files.
- Do not run live benchmarks, experiments, or scenario proof runs.
- Do not retire or rename scorer IDs.
- Do not implement new scorer families.
- Do not restructure `README.md` or broad documentation.
- Do not assume interactive orchestration machinery is required before simpler artifact-based representations are evaluated.
- User grants permission to read and search the full `docs/` tree and subdirectories for this goal, except other `docs/todos/` directories outside `docs/todos/charter-review/`. Do not read, search, or modify other todo directories unless the user explicitly grants additional consent.
- Do not infer Raidar's charter from intuition alone when repo evidence exists; unsupported claims should be labeled as proposals or open questions.

## Decision Boundaries

Executor may decide:

- How to structure the charter review document under `docs/todos/charter-review/`.
- Which non-doc repo files and public make targets to inspect for the repo deep dive.
- Which current scenarios, revisions, scorers, and platform evidence surfaces are relevant to the coverage audit.
- Which gaps are material enough to include in the backlog.
- How to phrase backlog items so they are useful for later implementation goals.
- Whether a future gap should be framed as scenario work, scorer work, platform work, or a mixed proposal.

Executor must not decide without user approval:

- To implement any backlog item.
- To modify scorer or scenario code/configuration.
- To retire, rename, or replace any scorer ID.
- To introduce a new active scorer family.
- To run benchmark/scenario experiments as part of this goal.
- To read, search, or modify other `docs/todos/` directories outside `docs/todos/charter-review/`, except files explicitly referenced by the user. Reading/searching non-todo `docs/` subdirectories is permitted for this goal.

## Approach Context

This goal follows the scorer hardening work now stored under `docs/todos/scorer-migration/implemented/`. That prior work activated concrete scorer definitions but did not extensively scenario-run-test every active scorer. The next useful step is not another implementation pass; it is to define Raidar's evaluation charter and coverage model so follow-on implementation work has a clear basis.

The review must be grounded in a repo deep dive rather than only the starting hypothesis. It should inspect enough of the non-doc repository surface to understand the concepts that shape Raidar's evaluation model, including the public make workflow, scenario schema, scorer registry/definitions, runtime/harness pipeline, matrix definitions, experiment/report artifacts, and tests that encode expected behavior.

The review should account for the current Raidar design:

- Scenarios and revisions are the primary mechanism for flexible benchmark design and iterative improvement.
- Scorers are delivery-activity themed and should prefer deterministic evidence when available.
- LLM-as-judge remains acceptable when deterministic evidence is insufficient and the rubric/evidence boundary is explicit.
- Raidar should support measuring improvements from agent skills, system rules, linting processes, planning approaches, and orchestration methods where those affect software delivery.
- Planning/orchestration support should be evaluated with a simplest-sufficient-capability mindset. A static artifact-based representation may be enough for some comparisons; interactive mode support should be proposed only when needed for observability or measurement fidelity.

## Definition of Done

- A charter review document exists under `docs/todos/charter-review/`.
- The review includes a repo-grounded concept inventory or equivalent evidence-backed summary explaining the Raidar concepts that inform the charter.
- The tracker records the key repo evidence sources used for the deep dive.
- The review defines Raidar's purpose, evaluation domain, and evaluation principles.
- The review includes a coverage map of current scenarios/revisions/scorers/platform evidence surfaces against the delivery-activity taxonomy.
- The review identifies material coverage gaps.
- The review includes a high-level backlog of proposed new/revised scenario briefs and relevant scorer/platform proposals.
- Each scenario/revision brief includes purpose, measured activity, candidate scorer(s), expected evidence, and why existing coverage is insufficient.
- The review includes explicit decision rules for scoring approach and scenario-root vs revision choice.
- The review includes platform-gap analysis for orchestration/planning-mode benchmarks with a simplest-sufficient-capability bias.
- `docs/todos/charter-review/goal-tracker.md` is updated with decisions, evidence, residual risks, and any user approvals needed.
- `make quality` passes before this goal is considered complete.

## Tracker Guidance

Maintain `docs/todos/charter-review/goal-tracker.md` during execution. Capture high-value events only: decisions, direction changes, evidence sources, coverage conclusions, rejected paths, residual risks, and verification results. Avoid routine progress logging.
