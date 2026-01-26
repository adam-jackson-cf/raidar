# Eval Orchestration Flow

End-to-end outline of how the evaluation harness prepares workspaces, executes agents, and produces scorecards.

## 1. Task + scaffold preparation
1. Author `tasks/<task>/task.yaml` (see `docs/references/new-task.md`).
2. The CLI resolves `scaffolds/<template>/<version>/`, copies that versioned scaffold into a workspace, injects the requested rules variant, and snapshots both the baseline manifest (from the catalog) and the workspace manifest for audits.

## 2. Agent configuration
- Harness choice now maps to adapter-backed entries in `orchestrator/src/agentic_eval/harness/config.py` (claude-code, codex-cli, gemini, openhands, etc.). Each adapter validates model compatibility before Harbor runs.
- Provider naming is enforced per harness so we always know which provider/model combo executed:
  - `claude-code` → `anthropic/<model>`
  - `gemini` → `google|vertex/<model>`
  - `copilot` → `github/<profile>`
  - `pi` → `inflection/<model>`
  - `openhands` → `openhands/<profile>`
  - CLI adapters such as Cursor remain flexible but still require explicit provider/model strings.
- Models are specified as `provider/model` strings (e.g., `anthropic/claude-sonnet-4-20250514`) and parsed into `ModelTarget` objects so adapters can pass the fully-qualified name to Harbor.
- CLI entry points:
- `eval-orchestrator run` for single runs.
- `eval-orchestrator matrix` (via `MatrixRunner`) to sweep explicit harness/model/rules combinations defined in a matrix YAML. Each entry declares a `runs` list so we only execute valid harness/model pairs (no cartesian cross-product surprises).

## 3. Agent execution via Harbor
1. `run_task` shells out to Harbor (`harbor run -d terminal-bench@2.0 …`) using the harness configuration.
2. The agent operates inside the workspace until completion or timeout.
3. Session logs (Codex, Claude Code, Gemini) can be parsed with `parser/session_log.py` to reconstruct prompts, tool calls, and gate results.

## 4. Verification & scoring
1. **Functional dimension** – Based on gate execution results and test outcomes. If builds/tests fail, the functional score drops to 0.
2. **Compliance dimension** – Deterministic checks scan the workspace; optional LLM judge prompts evaluate rubric criteria using code excerpts and rules text.
3. **Visual dimension** – When configured, odiff compares the captured screenshot against the reference image and reports similarity.
4. **Efficiency dimension** – `GateWatcher` records exit codes, failure categories, repeats, and total failures to penalize unstable runs.
5. Each dimension’s computed score feeds the weighted composite specified in `orchestrator/src/agentic_eval/config.py` (`settings.weights`).

## 5. Result persistence & inspection
- Completed runs serialize to `orchestrator/results/<run_id>.json` via `EvalRun`. The JSON contains:
  - `config`: harness/model/rules metadata plus the scaffold template/version that ran
  - `scores`: full `Scorecard` with dimension payloads
  - `events`: optional reconstructed session events
  - `gate_history`: chronological gate execution artifacts
- Scorecards also embed `metadata.scaffold` (template, version, manifest fingerprint, and paths to baseline/workspace manifests) so you can correlate score deltas with scaffold tweaks.
- Each run now persists scaffold evidence under `results/<run_id>/` (baseline manifest, workspace manifest snapshot, `.scaffold-meta.json`, and injected rules copy) so audits can reconstruct exactly which scaffold/rules package executed without relying on the ephemeral workspace.
- Aggregation utilities:
  - `comparison/aggregator.py` loads scorecards, builds reports, and exports CSV leaderboards.
  - `comparison/matrix_runner.py` coordinates batch runs and tracks per-run success/failure counts.
- Historical artifacts can be replayed or compared by pointing reporting commands at the same results directory.

## 6. Where to look when debugging
1. **Workspace** (`workspace/` by default) – inspect files the agent produced.
2. **Results JSON** – confirm scoring, termination reasons, metadata.
3. **Harbor logs** – captured via `subprocess.run` stderr/stdout inside `run_task`.
4. **Analyzer artifacts** – `.enaible/artifacts/*` hold structural assessments when architecture review workflows run.
