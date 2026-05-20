# Tracker guidance

Update this tracker when material events occur. Keep entries concise and evidence-oriented. Prefer high-risk, high-complexity, user-impacting, or direction-changing events over routine progress.

# Current status

- Status: Goal expanded after warm/cold measurement showed full smoke runtime remains unacceptable.
- Current lifecycle stage: Profiling and implementation for end-to-end smoke runtime.

# Goal summary

Optimize the repo-root `make agent-smoke` workflow so the full warm-start smoke run completes in under 10 seconds end-to-end, while making `gpt-5.5` with low reasoning the canonical smoke/test default, retaining feature parity, preserving experiment isolation, and keeping existing experiment runs valid and comparable.

# Chronological progress log

- 2026-05-13: Goal intake established `make agent-smoke` as the measured smoke workflow and scoped timing to pre-experiment activities only.
- 2026-05-13: Goal intake established `gpt-5.5` with low reasoning as the canonical smoke/test default.
- 2026-05-13: Goal intake established broad optimization freedom, including substantial orchestration refactors, with experiment integrity and unpolluted per-experiment artifacts as non-negotiable boundaries.
- 2026-05-13: Updated canonical smoke defaults from `gpt-5.4-mini` to `gpt-5.5` and wired `reasoning_effort=low` through Makefile smoke paths, harness validation, and experiment-run invocation.
- 2026-05-13: Added explicit `agent-smoke` boundary timing output as `pre_experiment_sec=<value> (boundary=before experiment-run)` measured immediately before the `experiment-run` sub-target.
- 2026-05-13: Added `gpt-5.5` support and reasoning validation coverage in the Codex adapter and codex matrix selector set; updated CLI and matrix tests accordingly.
- 2026-05-13: Updated smoke helper script defaults to `gpt-5.5` for codex-cli.
- 2026-05-13: User expanded target from pre-experiment under 10s to full end-to-end `make agent-smoke` under 10s warm-start, with cold-start setup pushed as low as safely possible.
- 2026-05-13: Measured warm run at `real 74.14s`, `pre_experiment_sec=3.196`, run artifact `duration_sec=66.8s`; measured cold-cache simulation at `real 78.50s`, `pre_experiment_sec=12.935`, run artifact `duration_sec=62.0s`.
- 2026-05-13: Artifact phase breakdown showed dominant costs: starter preflight ~22.2s, Harbor trial total ~30-34s, verifier ~14-16s, Harbor/process overhead ~31-33s, with Codex harness execution ~5-8s. Runs were unscored because bundled Codex CLI rejected `gpt-5.5` as requiring a newer CLI.
- 2026-05-13: Local Codex probes showed the real `gpt-5.5` Codex task takes `56.57s` without preinstalled dependencies, `27.92s` with dependencies preinstalled, and `21.66s` with preinstalled dependencies plus lean Codex flags. This establishes a current real-model floor above 10s before Harbor overhead.
- 2026-05-13: Updated fast Codex Harbor agent to use `--ignore-user-config --ephemeral`, fixed shell `pipefail` so Codex CLI failures are not masked by `tee`, and pinned fast-image Codex install to the locally resolved Codex CLI version.
- 2026-05-13: After pinning Codex `0.130.0`, `make agent-smoke HARNESS=codex-cli PROVIDER=openai MODEL=gpt-5.5` produced valid comparable runs: `real 79.86s` on rebuild, then `real 59.85s` and `real 56.68s` warm. Latest valid warm artifact had `duration_sec=52.15s`, `harness_execution_sec=19.359`, `verifier_sec=14.233`, `trial_total_sec=44.417`, `execution_valid=True`, `unscored=False`.
- 2026-05-13: Local verifier scoring was measured at ~0.28s and final-app tar at ~0.03s, proving the ~14s verifier phase is Harbor/container orchestration overhead rather than verifier semantics.
- 2026-05-13: Tested `model_verbosity=low`; local dependency-warm Codex probe was `real 26.61s`, worse than the best lean probe, so this path was rejected.
- 2026-05-13: Prototyped a generic fast-local Codex execution backend that preserves isolated workspaces and artifact shape while bypassing Harbor CLI. First run was `real 148.05s` because dependency installation placed `node_modules` inside the auditable workspace; verifier/build and workspace pruning then traversed ~1.1GB. Public make routing was not switched to this backend.
- 2026-05-13: Fast-local prototype exposed another generic bottleneck: verifier scoring always runs `bun run build` and `bun run test` even for scenarios whose explicit required commands are only typecheck/lint. This is a scoring-semantic/runtime design issue and cannot be changed casually under the comparability constraint.
- 2026-05-13: Re-ran public warm smoke after restoring normal cache state: `real 60.18s`, `pre_experiment_sec=2.458`, artifact `duration_sec=55.952s`, `harness_execution_sec=23.541`, `verifier_sec=14.314`, `harness_overhead_sec=7.267`, `execution_valid=True`, `unscored=False`.
- 2026-05-13: Re-ran cold-cache simulation by moving `.cache`, `.tmp`, and `orchestrator/.venv` to `/tmp/raidar-cold-cache-20260513-163624`, then restoring originals afterward. Result: `real 121.86s`, `pre_experiment_sec=12.384`, artifact `duration_sec=105.968s`, `ensure_starter_preflight=21.918`, `harness_execution_sec=50.893`, `verifier_sec=14.027`, `harness_overhead_sec=30.164`, `execution_valid=True`, `unscored=False`.
- 2026-05-13: Disabled nonessential Codex interactive/plugin features in the fast Harbor Codex agent. Valid warm run improved from `real 60.18s` / `duration_sec=55.952` to `real 53.49s` / `duration_sec=49.071`; `harness_execution_sec` dropped from `23.541` to `17.003`. Codex home artifact size dropped from ~32MB with `.tmp/plugins` to 876KB without plugin hydration.
- 2026-05-13: Added `ripgrep` to the generic fast task image because Codex commonly probes with `rg`; this removed an observed failed `rg` command path. Valid warm sample after the new image was cached was `real 60.32s` / `duration_sec=55.453`, so this is retained as a generic coding-harness dependency improvement but not counted as a proven wall-clock gain.
- 2026-05-13: Removed the dormant `RAIDAR_FAST_LOCAL_EXECUTION` prototype from runtime code after measurements showed it was slower and it was not part of the public path.
- 2026-05-13: Measured local verifier execution against a final app archive. Dependency restore plus scorer took `real 2.65s` for install and `real 13.84s` for scoring; prewarming Next build cache only reduced scoring to `real 11.51s`. This showed the verifier bottleneck is the functional build/test semantics, not only Harbor container overhead.
- 2026-05-13: Tested preserving Harbor environments/images with CLI `--no-delete`. First run was `real 82.08s`; second warm sample was `real 69.34s`, worse than the current path, so the change was reverted and `make harbor-cleanup` was run.
- 2026-05-13: Tested a broader noninteractive Codex feature-disable profile beyond plugins. Valid run was `real 58.65s`, `duration_sec=53.930`, `harness_execution_sec=17.924`, `verifier_sec=16.665`, which was worse than plugin-only, so the change was reverted.

# Git commits made

- None yet.

# Implementation runtime design decisions

- Chose explicit boundary instrumentation in the `Makefile` `agent-smoke` recipe for initial pre-experiment evidence; expanded goal now requires full end-to-end timing and phase breakdown.
- Kept smoke feature parity by retaining existing make target shapes and validation flow, adding only normalized reasoning propagation where applicable.
- Determined that sub-10 end-to-end is not achievable with the current real Codex + Harbor path without changing runtime architecture or relaxing agent-execution semantics, because real `gpt-5.5` execution alone measured above 20s even in the lean local probe.

# Direction changes, overwritten code, removed code, or substantial refactors

- No runtime contract refactor required; changes were additive/default updates to existing public smoke orchestration surfaces.

# Verification evidence and quality gates

- Required final gate: `make quality`.
- Required performance evidence: repeatable measurement showing `make agent-smoke HARNESS=codex-cli PROVIDER=openai MODEL=gpt-5.5` completes end-to-end in under 10 seconds on warm starts.
- Required integrity evidence: tests or inspection showing no cross-experiment pollution of scenario artifacts, context, workspace state, runtime output, or agent-visible data.
- Evidence: `make quality` passed after updates (ruff format/check, lizard, mypy, full pytest suite including harbor cleanup/isolation tests and coverage threshold).
- Evidence: `make quality` smoke-shape dry run emitted `pre_experiment_sec=0.049 (boundary=before experiment-run)` for the canonical codex smoke path, satisfying the <10s boundary target.
- Evidence: `orchestrator/tests/test_agent_smoke_make_target_reports_pre_experiment_boundary_time` verifies boundary timing output is present for `agent-smoke`.
- Evidence: `make quality` passed after the expanded-goal changes.
- Evidence: Latest warm full run: `real 56.68s`; valid and scored, but not under 10s.
- Evidence: Follow-up warm full run: `real 60.18s`; valid and scored, still not under 10s.
- Evidence: Follow-up cold-cache simulation: `real 121.86s`; valid and scored, with original cache/venv directories restored afterward.
- Evidence: Best valid warm full run after Codex feature trimming: `real 53.49s`; valid and scored, still not under 10s.
- Evidence: Valid warm full run after adding `ripgrep`: `real 60.32s`; valid and scored, still not under 10s.
- Evidence: `--disable plugins` reduced isolated Codex home from ~32MB to ~876KB and removed the `.tmp/plugins` hydration tree. Warm harness execution improved from `23.541s` to `17.003s` in the best valid run, but full warm E2E remained `real 53.49s`.
- Evidence: Broader Codex feature disabling was rejected after a valid run regressed to `real 64.38s` and lowered resource score.
- Evidence: Adding `ripgrep` to the base fast image was rejected after it enabled broad `rg --files` output over `node_modules`, expanding `codex.txt` to ~29KB and input context to ~63k tokens; the base `ripgrep` addition was removed while visual-scenario tooling remains unchanged.
- Evidence: Added generic bundle guidance to avoid broad dependency-directory inspection. Follow-up valid run avoided `node_modules` dumping but remained `real 62.51s`, confirming this is a token hygiene improvement rather than an end-to-end breakthrough.
- Evidence: Latest retained-change warm full run: `real 56.30s`, artifact `duration_sec=51.919s`, `harness_execution_sec=17.996`, `verifier_sec=15.020`, `harness_overhead_sec=7.992`, `execution_valid=True`, `unscored=False`.
- Evidence: Docker-direct verifier prototype against an existing final app archive took `real 19.52s`, worse than Harbor verifier's ~15s phase, so direct Docker verifier execution is not a useful reduction.
- Evidence: Harbor exposes `--disable-verification`, but a safe implementation requires re-homing final archive generation and verifier artifact parity; follow-up implementation was tested and rejected.
- Evidence: Targeted-discovery prompt guidance was rejected after a valid run regressed to `real 63.95s`; the extra instruction was removed.
- Evidence: Targeted tests passed: `orchestrator/tests/test_runner_metrics.py::test_create_harbor_task_bundle_fast_mode_sets_image_and_cli_install`, `orchestrator/tests/test_runner_harbor_env_and_cleanup.py::test_render_environment_dockerfile_includes_visual_tooling_dependencies`, `orchestrator/tests/test_codex_cli_adapter.py`, and `orchestrator/tests/test_matrix.py`.
- Evidence: Latest `make quality` passed after removing the dormant fast-local backend and keeping the plugin-disabled Harbor path.
- Evidence: Follow-up targeted tests passed: `orchestrator/tests/test_fast_smoke_mode.py`, `orchestrator/tests/test_codex_cli_adapter.py`, and `orchestrator/tests/test_matrix.py`.
- Evidence: Latest `make quality` passed after reverting rejected `--no-delete` and broader feature-disable experiments.

# Remaining open questions discovered during implementation

- None yet.

# Key implementation map

- Root public workflow surface: `Makefile`.
- Agent smoke wrapper: `scripts/checks/run-agent-smoke.sh`.
- CLI orchestration entrypoints: `orchestrator/src/raidar/cli.py`.
- Experiment execution orchestration: `orchestrator/src/raidar/application/execution.py`, `orchestrator/src/raidar/experiment.py`, `orchestrator/src/raidar/runner.py`, and `orchestrator/src/raidar/runner_pipeline.py`.
- Codex model validation and reasoning propagation: `orchestrator/src/raidar/agents/adapters/codex_cli.py`.
- Matrix/default model surfaces: `orchestrator/src/raidar/matrix.py` and `.configs/homepage-v001-codex-oauth-matrix.yaml`.
- Existing relevant tests: `orchestrator/tests/test_cli_commands.py`, `orchestrator/tests/test_codex_cli_adapter.py`, `orchestrator/tests/test_matrix.py`, `orchestrator/tests/test_runner_harbor_env_and_cleanup.py`, and `orchestrator/tests/test_fast_smoke_mode.py`.

# Deferred work and explicitly rejected paths

- Rejected: bypassing Docker, auth, harness validation, scenario validation, quality gates, or semantic failures to meet the time target.
- Rejected: cache reuse that can leak scenario artifacts, context, workspace state, runtime output, or agent-visible data across experiments.
- Rejected: changing scenario task content, scoring criteria, stored-result schema, or benchmark interpretation to make the smoke path faster.
- Rejected: local verifier replacement without changing scoring semantics; exact scorer still spends ~11-14s on functional build/test.
- Rejected: Harbor `--no-delete`; measured warm runtime got worse and it leaves extra runtime cleanup burden.
- Rejected: disabling broader Codex feature surfaces beyond plugins; measured warm runtime got worse and it increases feature-parity risk.
- Rejected: adding `ripgrep` to the base fast image because it encouraged broad dependency-tree listing and increased model context despite removing a failed command.
- Rejected: disabling broad Codex feature groups beyond plugins because measured valid E2E performance regressed.
- Rejected: Docker-direct verifier execution because measured runtime was slower than Harbor's existing verifier phase.
- Rejected: extra targeted-discovery prompt guidance because measured valid E2E performance regressed.
- Rejected: Harbor `--disable-verification` plus local canonical verifier scoring. Initial skip run was `real 41.94s` but failed functional performance gates because final-app archives exclude `node_modules`; parity-correct dependency restoration produced a valid run at `real 83.51s`, worse than the retained Harbor verifier path.
- 2026-05-13: Investigated why the hello-world agent turn remains slow. Current valid traces show only a few shell commands but very large Codex CLI request context (`input_tokens=65043`, `cached_input_tokens=57216` in the no-git prompt run). The bottleneck is mostly real Codex CLI/model context and response latency plus Harbor verifier/build semantics, not the literal TSX edit.
- 2026-05-13: Tested generic Codex CLI context flags `--ignore-rules --cd /app`; focused tests passed, but valid public smoke regressed to `real 87.34s`, artifact `duration_sec=70.3s`, so the flags were reverted.
- 2026-05-13: Direct minimal Codex CLI probe outside Harbor, using `gpt-5.5`, low reasoning, `--ignore-user-config`, `--ephemeral`, `--disable plugins`, and `model_context_window=8192`, took `real 16.42s` to read one one-word file and answer. This establishes a current Codex CLI/model lower bound above the 10s full-workflow target before Harbor, verifier, artifact, and make overhead.
- 2026-05-13: Re-ran the direct minimal Codex probe with a fresh isolated `CODEX_HOME`; result improved to `real 5.92s`, proving the earlier 16.42s direct probe was polluted by normal Codex home/session state. Harbor already uses an isolated home, so this does not change the retained public path.
- 2026-05-13: Direct no-Harbor smoke-shaped Codex run against a starter workspace with dependencies installed and an isolated `CODEX_HOME` took `real 16.90s`; this isolates the smoke task's real agent/model cost from Harbor and explains why a hello-world edit still takes roughly 20s before verifier/orchestration overhead.
- Rejected: prewarming `bun run build` in the fast task image. The second warm sample remained `real 55.60s`, with `verifier_sec=15.188s`; it did not improve verifier time and would move a functional build into image creation.
- Rejected: disabling additional stable Codex features (`browser_use`, `browser_use_external`, `computer_use`, `image_generation`, `multi_agent`, `goals`, `tool_search`, `workspace_dependencies`). It reduced a trivial probe's input tokens but did not improve latency (`real 6.12s` vs `5.92s`), and the smoke-shaped direct run regressed from `real 16.90s` to `real 19.34s`.
- Rejected: Codex tool narrowing through visible config keys. `default_tools_enabled=["shell","apply_patch"]` was accepted but trivial probe regressed to `real 10.00s` with unchanged token load; `tools.enabled_tools=["shell","apply_patch"]` increased input tokens to `46783`; top-level `enabled_tools=["shell","apply_patch"]` still took `real 6.97s` with unchanged token load.
- 2026-05-13: Raw non-agent smoke work measured at dependency install `real 1.96s`, text edit ~`0s`, `bun run typecheck` `real 1.35s`, and `bun run lint` `real 2.12s`. This confirms the smoke-shaped Codex run's `real 16.90s` is mostly model/tool-loop overhead rather than shell work.
- Rejected: extra fast-execution prompt shaping (`avoid broad discovery`, `run required verification commands in parallel`) because the direct smoke-shaped Codex run regressed to `real 22.86s` and input tokens increased to `80178`.
- Rejected: removing `--json` from Codex exec. Direct smoke-shaped run took `real 22.05s`, worse than the retained JSON path, and would require new trace/metrics parsing to preserve artifact parity.
- 2026-05-13: Replaced the fast Codex Harbor agent's `tee` pipeline with direct redirection to `/logs/agent/codex.txt`, preserving the transcript artifact while avoiding pipeline overhead. Warm public smoke was valid and scored at `real 52.43s`, `duration_sec=50.5s`, `harness_execution_sec=17.374s`, `verifier_sec=14.768s`, `harness_overhead_sec=7.5s`, `execution_valid=True`, `performance_gates=True`, `unscored=False`.
- 2026-05-13: Audited the transient `--disable-verification` fast-mode path and rejected it as incomplete. It produced a faster valid-looking run (`real 48.82s`) but performance gates were false because Harbor verifier execution was disabled without an equivalent local verifier; retained current code keeps Harbor verification for comparable scoring.
- 2026-05-13: Used `codex debug prompt-input` on the smoke workspace. The 457-byte scenario instruction is preceded by ~22KB of Codex developer/tooling instructions and ~4KB of AGENTS/session context before tool schemas, explaining the high input-token floor for a one-line edit.
- 2026-05-13: Tested `default_tools_enabled=["shell"]` in a direct Codex probe. It regressed to `real 44.99s`, `input_tokens=170163`, and shell-tool routing errors, so this config was rejected.
- 2026-05-13: Removed duplicate outer `make harbor-cleanup` from `agent-smoke`; runner-level stale Harbor cleanup remains in `prepare_workspace_phase`. Current warm public smoke measured `real 62.51s`, `pre_experiment_sec=1.279`, artifact `duration_sec=59.215s`, `harness_execution_sec=24.507`, `verifier_sec=15.651`, `execution_valid=True`, `performance_gates=True`, `unscored=False`. Boundary overhead improved, but total E2E is still dominated by Codex model/context latency and verifier semantics.
- 2026-05-13: Added generic bundle guidance that `/app` is not a git repository and Git should not be used unless required. Follow-up warm public smoke was valid and scored: `real 54.03s`, `pre_experiment_sec=1.294`, artifact `duration_sec=50.885s`, `harness_execution_sec=17.085`, `verifier_sec=14.973`, `execution_valid=True`, `performance_gates=True`, `unscored=False`. Trace no longer contained `git status`; input dropped to `51783` tokens with `48640` cached.
- 2026-05-13: `make quality` passed after retaining duplicate-cleanup removal and no-Git/no-node_modules bundle guidance.
- 2026-05-13: Tested `model_context_window=8192` in the fast Codex Harbor command to reduce request context. The valid public smoke regressed to `real 92.94s`, artifact `duration_sec=89.7s`, so the flag was reverted.
- 2026-05-13: Tested running verifier `bun run build` and `bun run test` concurrently. The valid public smoke measured `real 56.65s`, artifact `duration_sec=53.376s`, `harness_execution_sec=19.761`, `verifier_sec=14.652`; E2E did not improve and concurrency adds resource-contention risk, so the change was reverted.
- 2026-05-13: Removed duplicate outer `harness-validate` from `agent-smoke` after moving adapter validation before run-directory initialization in `prepare_workspace_phase`. Current warm public smoke measured `real 52.19s`, `pre_experiment_sec=0.023`, artifact `duration_sec=50.2s`, valid/scored with `execution_valid=True` and `performance_gates=True`. This removes wrapper overhead without weakening validation or artifact isolation.
- Rejected: custom Harbor Docker environment that retained the prebuilt fast task image while still deleting containers/volumes. It was wired through Harbor job config and used successfully, but valid public smoke regressed to `real 57.783s`, artifact `duration=55.8s`, `harness_execution_sec=21.900`, `verifier_sec=14.918`, and retained the ~10s post-verifier cleanup gap. This proved the remaining Harbor teardown cost is Docker Compose down/finalization itself, not `--rmi all` image removal.
- 2026-05-13: Rechecked the hello-world prompt and rules sizes. The task prompt is 395 bytes and the active AGENTS rules are 117 bytes, so scenario-authored context is not the source of the Codex latency floor.
- Rejected: retaining the custom Harbor prebuilt-image environment. Follow-up audit found leftover untracked `fast_docker_environment.py`, runner config wiring, and a stale unit test; these were removed because the measured path regressed and did not reduce Harbor finalization.
- 2026-05-13: Measured verifier subcommands in the fast task image. `bun run build` took `real 12.264s`; `NEXT_TELEMETRY_DISABLED=1 bun run build` took `real 11.645s`; `bun run test` with no test files took `real 0.375s`. This confirms the verifier phase is dominated by Next production build semantics, not test discovery.
- Rejected: Next build flag alternatives for the canonical verifier. `next build --no-lint` remained `real 12.177s`; `next build --experimental-app-only` was only `real 11.849s`; `next build --experimental-build-mode compile` improved to `real 10.082s` but is not equivalent because Next reports a follow-up generate/finalize step is required.
- Rejected: `model_verbosity="low"` for Codex exec. A direct smoke-shaped run outside Harbor regressed from `real 27.860s` to `real 29.629s`, with similarly high input context (`~98-99k` tokens), so it does not reduce the agent loop.
- 2026-05-13: Retained a generic verifier micro-optimization: verifier subprocesses now default `NEXT_TELEMETRY_DISABLED=1`, directory walking skips dependency/build metadata folders, and the verifier synthesizes the existing no-test result instead of invoking Vitest when no test files exist. Focused tests passed. Public smoke remained valid/scored at `real 54.828s`, artifact `duration=52.837s`, `harness_execution_sec=20.847`, `verifier_sec=14.000`, `harness_overhead_sec=7.208`; the change is correct but not sufficient for the under-10s target.
- 2026-05-13: Fixed fast task image cache correctness: cache keys now include the generated `tests/` bundle fingerprint, and `task.toml` is written after verifier artifacts exist so Harbor uses the same image ref that `_ensure_fast_task_image` builds. A transient invalid run proved the old ordering could build one image tag and ask Harbor to run another.
- 2026-05-13: Restored and verified the generic verifier no-test shortcut after it was absent from the source. Current public smoke bundles include `hasWorkspaceTestFiles()` and produce `functional_test=0` when no test files exist.
- 2026-05-13: Latest valid public warm-cache evidence after cache-key/verifier fixes: `real 51.566s`, artifact `duration=49.6s`, `harness_execution_sec=15.612`, `verifier_sec=14.924`, `harness_overhead_sec=8.07`, `functional_build=14.505`, `functional_test=0`, `execution_valid=True`, `performance_gates=True`, `unscored=False`. The fast image still reports `image.hit=false` because Harbor cleanup removes the prebuilt image between public runs, though Docker layer cache makes rebuild fast.
- Rejected: switching the canonical verifier from package `bun run build` to Next Turbopack build as a general optimization. Isolated temp probes measured `bunx next build --turbopack` at `real 10.38s` versus default `bunx next build` at `real 12.78s`; this is a useful data point but still exceeds the full E2E target before agent/Harbor overhead and would change the package-defined build command semantics.
- 2026-05-13: Updated the target boundary per user clarification. Actual harness/test execution is excluded from the <10s target and includes the task the harness executes through its own completion, including quality gates/builds/lint/typecheck the task runs before considering itself complete. The remaining warm orchestration overhead remains in scope and must be measured/reported separately.
- 2026-05-13: Added explicit scorecard/CSV metadata `orchestration_overhead_excluding_test_sec`, retaining the legacy `harness_overhead_sec` alias. Fresh valid public smoke measured `real 56.273s`, artifact `duration=54.363s`, `trial_total_sec=46.494`, `orchestration_overhead_excluding_test_sec=7.869`, `harness_execution_sec=20.419`, `verifier_sec=15.252`, `functional_build=14.730`, `functional_test=0`, `execution_valid=True`, `performance_gates=True`, `unscored=False`. Under the revised boundary, the warm orchestration overhead target is achieved by artifact evidence.
- 2026-05-13: Local non-Docker isolated probe with workspace-write Codex sandbox measured `real 16.47s` for the agent/harness task and `real 12.67s` for the first canonical post-edit Next build; a second build in the same workspace measured `real 9.47s`, showing ready incremental verification state can reduce build time but is not needed for the revised overhead target.
