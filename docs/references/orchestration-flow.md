# Eval Orchestration Flow

End-to-end outline of how the evaluation harness prepares workspaces, executes agents, and produces scorecards.

## 1. Task + scaffold preparation
1. Author `tasks/<task>/task.yaml` (see `docs/references/new-task.md`).
2. The CLI copies the referenced scaffold template (default `scaffold/`) into a workspace via `prepare_workspace`, injects the requested rules variant, and snapshots `scaffold.manifest.json` for later audits.

## 2. Agent configuration
- Harness choice now maps to adapter-backed entries in `orchestrator/src/agentic_eval/harness/config.py` (claude-code, codex-cli, gemini, openhands, etc.). Each adapter validates model compatibility before Harbor runs.
- Models are specified as `provider/model` strings (e.g., `anthropic/claude-sonnet-4-20250514`) and parsed into `ModelConfig` objects consumed by LiteLLM.
- CLI entry points:
  - `eval-orchestrator run` for single runs.
  - `eval-orchestrator matrix` (via `MatrixRunner`) to sweep multiple harness/model/rules combinations defined in a matrix YAML.

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
  - `config`: harness/model/rules metadata
  - `scores`: full `Scorecard` with dimension payloads
  - `events`: optional reconstructed session events
  - `gate_history`: chronological gate execution artifacts
- Aggregation utilities:
  - `comparison/aggregator.py` loads scorecards, builds reports, and exports CSV leaderboards.
  - `comparison/matrix_runner.py` coordinates batch runs and tracks per-run success/failure counts.
- Historical artifacts can be replayed or compared by pointing reporting commands at the same results directory.

## 6. Where to look when debugging
1. **Workspace** (`workspace/` by default) – inspect files the agent produced.
2. **Results JSON** – confirm scoring, termination reasons, metadata.
3. **Harbor logs** – captured via `subprocess.run` stderr/stdout inside `run_task`.
4. **Analyzer artifacts** – `.enaible/artifacts/*` hold structural assessments when architecture review workflows run.
