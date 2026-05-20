# Interactive Mode Evaluation

Status: future improvement

## Context

Raidar currently evaluates a scenario task against an `AgentSpec`, where an `AgentSpec` is the CLI harness plus model pairing. The current Harbor route is effectively passive: Raidar renders the scenario prompt into a Harbor task bundle, invokes the selected CLI harness once, and lets the harness run to completion without further user interaction.

This works for prompts that fully specify the task up front, including prompts that ask the agent to produce a plan or prompts that invoke a local planning skill or subagent without requiring more input. It does not cover workflows where the CLI harness expects later user input, mode changes, plan approval, clarification answers, or an interactive skill/subagent step.

## Working Model

The useful execution-mode split is:

- `passive`: one autonomous prompt, covered by the current `codex exec` route.
- `interactive`: the task requires user interaction after the initial prompt.

Interactive mode should cover any user interaction expected by the harness, including:

- starting or switching into plan mode;
- answering clarification questions;
- approving, rejecting, or refining a proposed plan;
- continuing a multi-step prompt workflow;
- responding to interactive skill or subagent prompts;
- driving slash-command or TUI-only flows that are not available through non-interactive execution.

## Current Evidence

Local code inspection found:

- `AgentSpec` currently contains harness, model, and timeout only.
- Scenario prompt artifacts are concatenated into one `instruction.md` in the Harbor task bundle.
- The Codex Harbor agent runs one non-interactive `codex exec ... -- <instruction>` invocation with stdin closed.
- Matrix configs vary harness, provider, model, and reasoning effort, but do not vary interaction behavior.

For Codex specifically, `codex exec` is explicitly the non-interactive route. It can run one prompt and supports session resume, but it is not equivalent to the interactive TUI. Public Codex materials describe interactive mode as the TUI surface for real-time user messages, command output, file changes, and streamed feedback. Public Codex issue history also shows slash-command support in `codex exec` has been requested as a gap, which supports treating TUI interaction as a separate capability rather than assuming passive mode can cover it.

## Preferred Direction

Implement interactive mode as a deterministic interaction controller first, not as a second model-powered user agent.

A user-simulator agent would add another model and policy into the benchmark. That makes results harder to interpret because the experiment would no longer isolate the harness plus model under test. A model-powered user may be useful later for adaptive conversation benchmarks, but it should be an explicit experiment dimension rather than the default implementation.

For the initial feature, fixed scripted responses are sufficient and more reproducible:

- send initial prompt;
- send `/plan` or equivalent mode command when required;
- wait for a recognizable event;
- send a fixed answer, approval, rejection, or refinement;
- continue until final state, idle state, or timeout.

## Proposed Implementation Shape

Keep Harbor's agent slot as the agent under test. Add an interactive execution path inside the repo-local Harbor agent for each supported harness that needs it.

For Codex, the likely implementation is tmux-backed:

1. Raidar renders an interaction script into the task bundle.
2. `CodexCliHarborAgent` detects interactive mode.
3. The task image includes `tmux` or an equivalent PTY controller.
4. The Harbor agent starts a real Codex TUI session in tmux.
5. A deterministic controller loops over screen/log capture, event matching, and send actions.
6. The controller records an auditable transcript under `/logs/agent/`.
7. Existing verifier/scoring can evaluate the final workspace, with additional metrics able to inspect the interaction transcript.

Example interaction shape:

```yaml
interaction:
  mode: interactive
  driver: tmux
  steps:
    - send: "/plan"
    - send_file: prompts/task.md
    - expect: proposed_plan
    - send: "Ask one clarification question before finalizing."
    - expect: user_input_request
    - send: "Assume a small Python repo with pytest and no external services."
    - expect: final
```

This schema is intentionally small. It should not become a broad protocol until actual scenario needs force that complexity.

## Why tmux

tmux gives the harness a real PTY. That matters because interactive CLI behavior often depends on terminal semantics rather than piped stdin. Plan mode, slash commands, mode cycling, approval prompts, and rich TUI state may not behave correctly through a non-interactive process.

This also matches how some real-world terminal agent tools handle Codex, Claude Code, Aider, and similar tools: each agent runs inside its own tmux session so it behaves as it would in a normal terminal.

## Alternative: `codex exec resume`

`codex exec resume` may be enough for scripted multi-turn prompt workflows where each step is just another prompt and no TUI-only behavior is needed.

It should not be the canonical interactive implementation because:

- `codex exec` is explicitly non-interactive;
- current local CLI help exposes `exec resume`, but not a plan-mode startup flag;
- slash commands and plan-mode behavior are not guaranteed to work through `exec`;
- plan mode is a collaboration mode with user input and proposed-plan semantics, not just the `update_plan` checklist tool.

`exec resume` can be an optimization or a secondary driver for simple multi-turn passive sessions.

## External Findings

- Harbor models work as task plus agent plus container environment. Agents implement Harbor interfaces and run commands inside the environment. This fits Raidar's current custom Harbor agent approach, but does not remove the need for a PTY controller when the CLI itself is interactive.
- Terminal-Bench evaluates agents in terminal environments and verifies final state with task tests. It supports the general idea of terminal-agent evaluation, but does not by itself define a user-interaction layer for plan approval or clarification workflows.
- `tau-bench` is the relevant benchmark pattern for simulated user interaction. It uses user simulation for dynamic conversations, but that is a different research question from deterministic CLI workflow driving.
- Claude Code exposes plan mode and permission modes more directly through CLI flags and startup configuration. If a harness provides stable non-interactive mode controls, Raidar should use those rather than driving TUI keystrokes.
- Public Codex materials distinguish interactive TUI mode from `codex exec` non-interactive mode, and public issue history indicates slash-command support in `exec` has been an open/requested capability.

## References

- Harbor agents: https://www.harborframework.com/docs/agents
- Harbor core concepts: https://www.harborframework.com/docs/core-concepts
- Codex interactive mode: https://www.mintlify.com/openai/codex/concepts/interactive-mode
- Codex exec mode: https://www.mintlify.com/openai/codex/advanced/exec-mode
- Codex plan mode template: https://github.com/openai/codex/blob/main/codex-rs/collaboration-mode-templates/templates/plan.md
- Codex exec slash-command request: https://github.com/openai/codex/issues/3641
- Claude Code permission modes: https://code.claude.com/docs/en/permission-modes
- Terminal-Bench paper: https://arxiv.org/abs/2601.11868
- `tau-bench` paper: https://arxiv.org/abs/2406.12045
- tmux-based terminal agent discussion: https://www.reddit.com/r/vibecoding/comments/1rzgjby/i_built_a_terminal_dashboard_to_watch_all_ai/

## Open Questions

- How should interactive runs be named in experiment identity: as an execution mode, an interaction profile, or a matrix dimension?
- Which transcript events are stable enough for scoring: raw tmux pane captures, Codex JSON session logs, or a controller-normalized event stream?
- Should interactive mode be Codex-only at first, or should the scenario schema define generic interaction steps and let unsupported adapters reject them?
- Should the first implementation support only fixed responses, or also conditional branching on matched output?
- How should timeouts distinguish harness inactivity, controller mismatch, and normal long-running agent work?

## Recommendation

Defer implementation until there is a concrete scenario that requires it.

When implemented, start with:

- `interaction.mode = passive | interactive`;
- deterministic fixed-response interactive scripts;
- a Codex tmux driver;
- transcript artifacts under `/logs/agent/`;
- adapter-level validation for unsupported interactive steps;
- no model-powered user simulator in the first version.

Add a model-powered user simulator later only when the evaluation objective requires adaptive user behavior and the extra model becomes an explicit part of the benchmark design.
