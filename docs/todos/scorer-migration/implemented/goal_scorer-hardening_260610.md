# Objective

Complete and harden Raidar's scorer system so all intended scorer mechanisms are implemented, active, scenario-attachable, and demonstrable through benchmark scenarios.

# Outcome

Raidar has a coherent scorer platform where proposed scorer definitions have been promoted or deliberately retired, active scorers produce reliable metric evidence, scenario validation accepts the completed scorer set, reports carry scorer outcomes clearly, and benchmark scenarios demonstrate the scorer groupings in realistic use.

# Scope Boundaries

Allowed work:

- Deep-dive the current scorer architecture, including scorer definitions, runtime scoring outputs, schema validation, scorecard aggregation, reports, scenario wiring, and tests.
- Complete current proposed scorers and fix active scorer/platform gaps discovered during the audit.
- Prefer deterministic scoring and process-derived evidence wherever reliable.
- Use LLM-as-judge only where deterministic scoring would be brittle, too narrow, or unable to capture required nuance.
- Create or revise scenarios that naturally demonstrate one or more scorer groupings.
- Use existing project scenario-creation skills and repo `make` workflows.
- Add unit, integration, and scenario-level tests proving scorer verification behavior.
- Run live scenario proof with `gpt-5.5` on low reasoning for every new or revised scenario.
- Finish with passing quality gates and an atomic Conventional Commit.

Excluded work:

- Do not duplicate exhaustive command documentation outside `README.md`.
- Do not treat generated runtime outputs under `scenarios/**/starter/**`, `experiments/`, or other generated surfaces as canonical product code unless the task explicitly requires it.
- Do not make speculative scorer families unrelated to existing scorer purposes unless a deep-dive finding shows they are required.
- Do not use LLM-as-judge as a shortcut where deterministic evidence is practical.

# Approach Context

Starting context:

- Existing scorer mechanisms live under `orchestrator/src/raidar/scorers/`, scorer assembly under `orchestrator/src/raidar/runtime/scoring_outputs.py`, and schema validation under `orchestrator/src/raidar/schemas/scenario.py`.
- Proposed scorer status currently appears in tests and scorer definitions; these should be audited as the source of work, not assumed complete.
- Scenario creation should use the repo's scenario skills, especially the project-local scenario creation and revision workflows.
- Public workflow entrypoints are repo-root `make ...` targets. Use `make help` for discovery and `make quality` as the completion gate.
- Scenario runs used as proof should use `gpt-5.5` with low reasoning to control cost.
- Follow repo-local Python conventions and quality gates, including the `python-conventions` skill and the strengthened Ruff/checker setup.

# Decision Boundaries

Executor may decide:

- Which scorer mechanism gaps must be fixed to make the scorer platform coherent and usable.
- Whether a scorer should be implemented deterministically, with LLM-as-judge, or with a hybrid, provided the choice is justified by evidence quality.
- How to group scorers across demonstration scenarios when one scenario naturally exercises multiple scorers.
- Whether to create a brand-new scenario root or clone a new revision inside an existing scenario root, following repo scenario rules.
- How to organize tests across unit, integration, and scenario validation layers.

Executor must ask before:

- Retiring or renaming an existing scorer id in a way that changes public scenario configuration semantics.
- Introducing a materially new scorer family not implied by current scorer names, tests, or scenario needs.
- Increasing live benchmark scope beyond the new/revised scenario proof runs required here.
- Changing model choice away from `gpt-5.5` low reasoning for scenario proof runs.
- Reading or modifying files under `docs/`, unless separately authorized.

# Definition Of Done

- All intended scorer definitions are either active and usable or explicitly retired with rationale.
- Previously proposed scorer mechanisms are completed enough to attach to scenarios, collect evidence, score metrics, and appear in scorer results.
- Deterministic scoring is used wherever practical; any LLM-as-judge scoring has a documented reason and stable judge contract.
- Scorer tests cover metric contracts, evidence collection, score derivation, scenario validation, and scorecard/report integration.
- New or revised scenarios demonstrate grouped scorer use, with each active scorer represented by at least one scenario where its purpose is clear.
- Every new or revised scenario has a live proof run using `gpt-5.5` low reasoning, with retained scorer evidence.
- `make quality` passes from the repo root.
- Final work is committed as one or more atomic Conventional Commits, with the main implementation commit clearly covering the scorer/scenario completion.
- `goal-tracker.md` is updated with decisions, commits, verification evidence, open questions, and deferred work.

# Produced Goal Statement

Deep-dive and complete Raidar's scorer platform by promoting or retiring proposed scorers, hardening scorer runtime/schema/reporting mechanics, creating grouped demonstration scenarios for the completed scorer set, proving every new or revised scenario with `gpt-5.5` low reasoning plus deterministic/unit verification, and finishing with passing quality gates and atomic commits.

# Executor Guidance

Use this `goal.md` as the source of truth for the implementation. Maintain `goal-tracker.md` during execution and update it when material events occur.

`goal-tracker.md` should capture high-value events only: decisions, direction changes, commits, verification evidence, open questions discovered during implementation, convergence of risk, complexity, or user journeys, deferred work, and rejected paths.

Keep tracker entries concise and evidence-oriented. Prefer recording high-risk, high-complexity, user-impacting, or direction-changing events over routine progress.
