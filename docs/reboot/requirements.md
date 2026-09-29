# Raidar reboot — requirements v1 (FROZEN 2026-09-29)

Frozen yardstick for the reboot. Any change needs an explicit user decision, recorded in the change log at the bottom.

## Purpose

Benchmark coding agents (harness + model) against defined problems that have hidden automated oracles. Then change or add **starting conditions** and measure, with paired statistics, whether scores improve. Correctness is primary. Additional scoring (e.g. code quality/inertia) is pluggable.

## Functional

| ID | Requirement |
|---|---|
| F1 | A problem has **1..N checkpoints**. **One-shot tasks** (N=1) are first-class: same pipeline, config and scoring, no special mode. |
| F2 | *Removed.* Starting mid-problem at checkpoint k is not required. |
| F3 | **Isolate context, not environment.** A checkpoint is a completely fresh agent meeting what already exists in the project. Its only knowledge is what it finds in the workspace. Environment and workspace persist across checkpoints. Each checkpoint gets:<ul><li>no prior conversation;</li><li>harness-persisted state (session history, auto-memory, todos) reset to the Condition baseline;</li><li>no surviving agent processes.</li></ul>Anything the agent wrote *into the project* persists. |
| F4 | The oracle is hidden and trusted, and graded strict all-cases. **Oracle tests never enter the agent's environment, and the agent never sees oracle results.** Progression is `continue` (default) or `halt_on_failure` (SCB strict). Because the agent gets no feedback, strict scores are derivable from continue runs. Halting is only an optional cost saving. |
| F5 | Starting conditions are **pluggable components** of five kinds:<ul><li>`workspace`: AGENTS.md, lint/type configs, starter overlay;</li><li>`harness`: skills, MCP, hooks, settings;</li><li>`prompt`: wrappers;</li><li>`workflow`: e.g. a review loop;</li><li>`service`: LSP, review bot.</li></ul>Each component proves it took effect via a discovery probe. Otherwise the trajectory is infra-invalid. |
| F6 | **Pluggable scorers**, one or more per experiment:<ul><li>Scorers observe snapshots and traces only. They never decide success, apply weights or compare arms.</li><li>Correctness is privileged.</li><li>Code quality/inertia is one scorer.</li><li>Offline rescoring is supported.</li><li>An unavailable result is never scored as zero.</li></ul> |
| F7 | Measurement preference: deterministic established tools, then the Jev classifier (calibrated, shadow first), then an LLM judge (explicit opt-in only). |
| F8 | Runtimes: Python (Phase 1), then Node/TS, Go, Rust, Java, .NET. Each runtime's toolchain and measurement dependencies are **provisioned lazily on first use**. |
| F9 | **One config file** per experiment (`experiment.yaml`), with registry references plus a lockfile. CLI overrides are allowed. |
| F10 | Experiments cover:<ul><li>arms × repeats (blocks), with declared factors;</li><li>paired deltas with CIs;</li><li>per-checkpoint curves;</li><li>cost reported beside every effect;</li><li>the verdicts `improved / worse / inconclusive / insufficient`.</li></ul> |
| F11 | Harness coverage: Claude Code first, then Codex. Others come from Harbor's built-in agents as needed. |
| F12 | **The review surface is retained as-is. Its presented components are frozen**: the same signals and the same persona questions. Only the projection mechanics underneath change. Harbor's viewer is held in reserve. |
| F13 | Problem sourcing: start from SCB's published Harbor dataset, after oracle validation. Iterate later. |
| F14 | **Port the user's SCB-fork quality scoring** into the `code-quality` scorer:<ul><li>C/V/E/H/R/G components;</li><li>eligibility;</li><li>lineage, regression, clone, mass and graph evidence;</li><li>sensitivity analysis.</li></ul>Surface it as **new review-surface Quality elements** alongside the frozen components. |
| F15 | **Score policies:**<ul><li>An experiment declares one headline ScorePolicy. It drives `composite_score` and the delivery tiers.</li><li>Default for multi-checkpoint problems: `scb-canonical@1`, where `S = 100·C·(0.70 + 0.30·I)` and `I = .15V + .15E + .20H + .30R + .20G`.</li><li>Default for one-shot problems: `scb-canonical-oneshot@1`. R and G are dropped as `not_applicable`, and I is reweighted to `I₁ = .30V + .30E + .40H` (SCB's V:E:H ratio renormalised). S has the same form.</li><li>The review surface warns when compared experiments use different headline policies.</li></ul> |

## Non-functional

| ID | Requirement |
|---|---|
| N1 | **Strict budgets on framework overhead, not on runs.** Agent and oracle execution time is problem-dependent and has no target. Every other phase has a measured budget that is recorded per run and enforced by a regression check. Those phases are:<ul><li>environment start;</li><li>snapshot transfer;</li><li>component application;</li><li>per-step reset;</li><li>verifier plumbing;</li><li>scorer orchestration;</li><li>result writing.</li></ul>Budgets are set from the Phase 0 cold and warm baselines. **Concurrency is configurable** per experiment and per machine. |
| N2 | **Isolation.** Container or host-kernel isolation is acceptable. Required guarantees:<ul><li>hidden tests and references are unreadable by the agent;</li><li>no host agent config or credentials leak in;</li><li>network is allowlisted.</li></ul> |
| N3 | **Reproducibility.**<ul><li>Inputs are content-hashed.</li><li>Tool, harness, model and Harbor versions are pinned or recorded.</li><li>Deterministic scorers replay byte-identically.</li></ul> |
| N4 | **Minimize bespoke code.** Adopt established tools as-is; Harbor is the execution kernel. Raidar owns:<ul><li>experiment design;</li><li>components;</li><li>the oracle plugin;</li><li>scorers;</li><li>Jev;</li><li>analysis;</li><li>the review surface.</li></ul> |
| N5 | **Cost.** Run at natural cost and always report it. Jev is used instead of an LLM for judgment. |
| N6 | Runs on macOS arm64 (macOS 15) and Linux. |

## Out of scope

- Cross-language absolute quality rankings.
- Public leaderboard or hosted service.
- Training/RL.
- Starting at checkpoint k.

## Change log

- 2026-09-29, v1 frozen. Decisions:
  - F2 removed.
  - N1 reframed as overhead budgets.
  - F12: review surface frozen.
  - F14: port the SCB-fork quality scoring.
  - F15: one-shot policy drops R/G, with weights .30/.30/.40.
  - Headline-policy warning.
