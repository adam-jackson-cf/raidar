**NEVER** surface or search files in `docs/`, may only be access with explicit user consent (ask). If a user references a file within this exclusion treat that as automatic consent.

## Workflows

- Public interface: repo-root `make ...`
- Use `make help` for command discovery and target descriptions.
- Treat direct `uv run --project orchestrator raidar ...` as implementation detail behind the root `Makefile`.
- LiteLLM must stay exact-pinned to a known-safe release across manifests and lockfiles. Do not use `1.82.7` or `1.82.8`.
- Keep `README.md` as the only human entrypoint; do not duplicate exhaustive command lists in other docs.
- The experiment is the only authoring unit: one `experiment.yaml` naming pinned `problems`, `agents`, `conditions` (lists of components; optional `shared_components` are put ahead of every condition's own, identically), `arms` (one condition on one agent), `assessment` (`scorers`, optional `headline_policy`) and `analysis` (`baseline_arm`, `blocks`, `seed`, verdict thresholds). Do not reintroduce scenario, revision or matrix files.
- Lifecycle: `make experiment-validate` -> `make experiment-lock` -> `make experiment-plan` -> `make experiment-execute` (each with `EXPERIMENT=<path>/experiment.yaml`; execute also `RUN_ID=`) -> `make experiment-assess RUN=runs/<id>/<run> STORE=stores/<id> SCORERS="..."` -> `make experiment-analyze RUNS="runs/<id>/<run>" STORE=stores/<id> ANALYSIS_OUT=analysis/<id>` -> `make review-surface-quality-data QUALITY_RUNS=runs/<id> QUALITY_STORE=stores/<id>` -> `make review-surface-serve REVIEW_RUNS_ROOT=runs/<id> REVIEW_STORE=stores/<id>`. Rescoring and analysis use the assessment settings of the run's lock (the experiment's current lock when its `lock_sha256` matches, else the run's own `run.lock.json`); pass `LOCK=`/`REVIEW_LOCK=`/`QUALITY_LOCK=` only for a lock kept elsewhere (smoke locks live under `.tmp/smoke/`).
- Use `make experiment-init EXPERIMENT=<dir>/experiment.yaml [PROBLEM=<ref>]` only to start a brand-new experiment; it writes a two-arm starter and refuses an existing path.
- To test another variant of a treatment, add a condition and an arm inside the existing `experiment.yaml` so it stays paired with the baseline arm. Create a new experiment only for a new question (different problems, baseline arm or analysis plan); do not create a sibling experiment for a variant. Arms may differ only in the declared `factors`.
- Use `make experiment-plan EXPERIMENT=<path>` to inspect an experiment's resolved contract (every trial with its hashes), `make problem-list` to see the registered problems, and `make problem-info PROBLEM=<ref>` to inspect one.
- Any change to an experiment, a component file or a problem pin requires `make experiment-lock` before the next run (`make experiment-execute` refuses a stale lock). Never hand-edit a `*.lock.json`. Each run needs a new `RUN_ID`; a run directory is never reused.
- Lock provenance: a lock's `toolchain` section hashes the Raidar spec code and every locked scorer's implementation (including its imports). Any semantic change under `orchestrator/src/raidar/spec/` or to a scorer module therefore makes every `examples/**/*.lock.json` stale, and `make experiment-locks-check` (part of `make quality`) fails until each is relocked with `make experiment-lock EXPERIMENT=<path>`. Relock the examples in the same change.
- A scorer, headline-policy, threshold or toolchain relock keeps `lock_sha256` (it covers the execution section only), so existing runs still match: rescore them into a new Assessment store with `make experiment-assess` instead of rerunning agents. A changed `lock_sha256` means a new experiment. Move a run executed under a version-1 lock with `make experiment-migrate-run-lock RUN=<run-dir> EXECUTED_LOCK=<v1 lock> LOCK=<current lock>`, never by editing `run.json`.
- Problems come only from the pinned registry: `make problem-import PROBLEM=<ref>`, then `make problem-validate PROBLEM=<ref>` when `problems/registry/` has no validation report for it. The oracle and held-out tests stay on the host; never place them in an agent workspace or a component.
- Toolchains come from runtime packs (`orchestrator/src/raidar/packs/<runtime>/`): `make pack-info`, `make pack-build`, `make pack-audit`. Agent images carry no scorer tools, and an agent's harness `version` must equal the pack pin.
- Scoring is offline: `make experiment-assess` writes Assessments to a store outside the run and never re-runs agents. Change a scorer, component or headline policy by adding a new `@<version>`, never by editing a released one in place.
- Real-LLM trials default to `codex-cli` with `gpt-6-luna` at `low` reasoning effort. Containers run on OrbStack by default (`DOCKER_CONTEXT=orbstack`). After a stopped run use `make harbor-cleanup RUN=<run-dir>`.
- Exclude component payloads under `examples/**/components/**` (rules files, skills, starter files) from analysis and code-quality checks by default; they are treatment content delivered to agents, not canonical shared product code.
- Treat `runs/`, Assessment stores (`stores/`), analysis output (`analysis/`), `problems/store/`, `problems/cache/`, `.tmp/` and `experiments/` as generated runtime artifacts by default: exclude them from quality checks unless the request explicitly asks to review/analyze them.
- Nothing reads `experiments/` any more: the scenario-epoch review read path is removed. Never write runs or synthetic fixtures into it; `make review-fixture-synthetic` writes under `.tmp/review-fixture/`.
- The review surface's user flow and the signals and persona questions it surfaces must not be lost (requirements F12). Layout and navigation may change; Playwright specs may be updated only where the flow legitimately changes, and every signal they assert must still be asserted. Run `make review-surface-test` after review-surface or projection changes; to include real runs pass `REVIEW_RUNS_ROOT` with one `REVIEW_STORE` for all roots or one per root. Use problem/condition/arm wording in the UI; the review projection (`schema_version=2`) uses `problem`, `arm` (the arm's condition label), `problem_meta` and `arm_diffs`.
- Task completion requires `make quality` to pass.

## Known Experiment Configs

- `examples/pilot/experiment.yaml`: Phase 1 pilot `pilot-forge` on `scb/forge@38d627ecf668` (8 checkpoints), pilot v2: both arms share the `prompt-wrapper@1` starter framing; `baseline` vs `guided` (rules-file, skills, lint-config, stop-hook, review-loop@2) on codex-cli `gpt-6-luna` low, 16 blocks, all four scorers.
- `examples/pilot/oneshot.experiment.yaml`: one-shot pilot `pilot-circuit-eval-oneshot` on `scb/circuit_eval.checkpoint_1@38d627ecf668`, same arms, 5 blocks, `scb-canonical-oneshot@1`.
- `examples/smoke/experiment.yaml`: operational smoke `smoke` (one codex-cli `gpt-6-luna` low agent, correctness only); not a benchmark.
- `examples/smoke/agents.experiment.yaml`: operational smoke `smoke-agents`, one arm per pack harness (`codex`: `gpt-6-luna` low; `claude`: `claude-haiku-4-5`, no reasoning effort); not a benchmark.

## Smoke Testing

- Smoke targets run `examples/smoke/` through the normal experiment path (lock under `.tmp/smoke/`, runs under `runs/`) on OrbStack. Use `make orchestrator-smoke`, `make agent-smoke` and `make smoke-agents` without setting implementation env vars manually.
- Use the public `make` targets for smoke runs rather than invoking Raidar implementation commands directly.
- Default single smoke (codex-cli + `gpt-6-luna` low): `make orchestrator-smoke`
- Single smoke with an explicit agent: `make agent-smoke HARNESS=codex-cli PROVIDER=openai MODEL=gpt-6-luna REASONING_EFFORT=low TIMEOUT_SEC=300` (harness versions default to the pack pins)
- One smoke trial per pack harness: `make smoke-agents`
- Codex-only smoke arm: `make smoke-agents SMOKE_ARM=codex`
- Claude-only smoke arm: `make smoke-agents SMOKE_ARM=claude`
- Single Claude Code smoke: `make agent-smoke HARNESS=claude-code` (defaults to `claude-haiku-4-5` with no reasoning effort). Claude Code trials run only on `claude-haiku-4-5` without `REASONING_EFFORT`.
- An empty override unsets the field: `REASONING_EFFORT=` (and `EXPERIMENT_SET='agents.0.reasoning_effort='`) leaves the effort unset; `''` sets an empty string.

## Secrets

- Secrets live in the Infisical project `mac-os-cf` (env `dev`), bound by `orchestrator/.infisical.json`. Setup: `infisical login` in a real terminal (interactive).
- Secret-using targets (`experiment-execute`, smoke targets, `experiment-assess`, `jev-shadow-rubric`, `harness-validate`, `component-probe-trials`) run under `infisical run` when the CLI is installed and logged in, else fall back to `orchestrator/.env`. `SECRETS=infisical|dotenv` forces a source.
- `orchestrator/.env.example` lists key names only. Never print secret values; use presence or length checks. Do not delete `orchestrator/.env` without user approval.
- `raidar run` preflights credentials (presence only) for every selected arm before any trial and fails naming the `agents.<i>.harness` field.

## Performance (N1 overhead budgets)

- The enforced overhead check is `make perf-gate`: the no-LLM fixture (`make perf-fixture`) at fixed concurrency 1 and 3, each checked strictly against the accepted budgets in `docs/reboot/overhead-baseline.md` §5. It needs Docker (OrbStack; check `orbstack-data-status` reports `external_available=True` first), so it is a release/gate check and is not part of `make quality`. Run it before a phase gate or release and after changes to the execution path.
- `make perf-check RUN=<run-dir>` on real experiment runs is report-only: it lists every phase against its budget, prints breaches as warnings with the run's trial concurrency and contention, and exits 0. Malformed input still fails. Use `PERF_STRICT=1` only when a strict check of a real run is wanted.
- Do not re-baseline or change budgets to make a run pass. A budget structure change (such as a concurrency-scaled budget) needs user approval and is recorded in `overhead-baseline.md` §5.

## Branch Syncing

- The canonical autoresearch removal commit is `d1973d6` `refactor: remove autoresearch surface from main`.
- When pulling `main` changes into `feat/autoresearch-v3`, prefer cherry-picking specific wanted commits instead of merging all of `main`.
- Do not cherry-pick `d1973d6` into `feat/autoresearch-v3`.
- Do not cherry-pick later cleanup commits that assume `auto_researcher/` is absent from `main`, including CI or workflow updates that remove direct `auto_researcher` setup steps.
- If a later `main` commit depends on `d1973d6` and removes or rewires autoresearch files, skip that commit too unless the feature branch is explicitly being reconciled with the removal.
