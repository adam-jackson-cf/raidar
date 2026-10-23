---
name: "raidar-onboarding"
description: "Use when getting the Raidar repo up and running on a new machine or Docker target."
---

# Guidance

## Setup order

- Check `uv` and `docker compose` 2.40.1 or newer are installed; report a missing tool with its install hint and stop.
- Run `make env-setup` to sync the orchestrator environment.
- Secrets come from Infisical (`infisical run --env=dev`), else `orchestrator/.env`.
- The user runs `infisical login` themselves in a real terminal; it is interactive, so never run it for them.
- Check secrets by presence or length only; never print a value.

## Agent credentials

- Default: API keys in the secrets source, `OPENAI_API_KEY` for codex and `ANTHROPIC_API_KEY` for claude-code; check each is present.
- The user choosing Codex subscription auth selects `make codex-auth-setup` instead of `OPENAI_API_KEY`.

## Docker target

- Run `make target-init`, then edit `~/.config/raidar/targets.toml`; never write secrets into it.
- A local daemon gets `docker_context` set to a name from `docker context ls`, plus an optional `data_check` argv.
- A remote daemon gets a `[targets.<name>.transport]` block with `kind = "ssh-socket"`, `host`, `identity` and `remote_socket`.
- Bring a remote target up with `make target-up TARGET=<name>`.
- Set `default` to the target this machine should use, and `max_concurrency` to suit the host.
- Default: a remote target stays untrusted; the user confirming they own the host selects `trusted = true`.

## Ready checks

- Call the repo ready only after every check below passes on the chosen target.
- `make target-status TARGET=<name>` reports healthy, including the target's data check.
- `make quality` passes.
- `make perf-fixture TARGET=<name>` (no LLM) passes.
- Default: no real-LLM run; the user asking for a real-agent check selects `make orchestrator-smoke` (codex, gpt-6-luna, low).
- On a failed check, report the failing command and its error; do not work around it.
