# Adding a New Agent (Model + Harness)

Follow these steps to make a new agent available across CLI commands, session parsers, and rules.

## 1. Extend the harness definitions
1. Update `orchestrator/src/agentic_eval/harness/config.py`:
   - Add the agent to the `Agent` enum (name should match how Harbor identifies the harness).
   - Ensure `HarnessConfig.harbor_args()` emits the correct CLI flags for the new harness.
2. If the agent requires special timeout defaults or Harbor arguments, extend `HarnessConfig` with additional fields rather than hardcoding elsewhere.

## 2. Wire the CLI
- In `orchestrator/src/agentic_eval/cli.py`, add the agent name to every `click.Choice` referencing agents (`run`, `inject`, `matrix`, `list-agents`, etc.).
- Provide help text describing any prerequisites (e.g., “requires OpenHands binary”).

## 3. Provide rules + scaffolding support
- Create rule files under `tasks/<task>/rules/<agent>/` so `inject_rules` can copy agent-specific guidance.
- If the scaffold needs additional setup steps for the agent (env vars, toolchains), document them in the task prompt and rules.

## 4. Session log parsing
- Extend `orchestrator/src/agentic_eval/parser/session_log.py`:
  - Add parser functions for the agent’s log format.
  - Update `parse_session()` to dispatch when `harness` equals the new agent.
  - Add tests under `orchestrator/tests/` to ensure session events are captured (user prompts, assistant outputs, bash commands, tool calls, gate results).

## 5. Harbor + LiteLLM integration
1. Confirm Harbor can launch the agent; if not, add the necessary container or adapter scripts.
2. Ensure the desired model identifiers are supported by LiteLLM; update any configuration (`config.py`, `.env.example`) with provider-specific settings.

## 6. Update documentation and tooling
- Mention the agent in `docs/architecture-review.md` or team onboarding docs so evaluators know when to use it.
- If pre-commit hooks or CI need additional binaries, update the relevant scripts (`Makefile`, GitHub Actions, etc.).

## 7. Validate end-to-end
1. Run `eval-orchestrator run` with the new agent + model string against a known task.
2. Check `orchestrator/results/<run_id>.json` for correct metadata (agent name, model, scorecard entries).
3. Verify session logs parse successfully and show up in scorecard attachments or audits.
