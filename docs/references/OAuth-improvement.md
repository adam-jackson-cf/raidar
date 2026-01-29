# Codex OAuth Enablement Plan

Goal: allow evaluators to run Codex CLI harnesses inside Harbor containers using OAuth-backed identities without leaking tokens or forcing ad-hoc manual steps.

## Current State

- **CLI behaviour**: `codex auth login` opens a browser on the host and caches refresh/access tokens under `~/.codex/`.
- **Harbor runs**: tasks execute inside ephemeral containers that only see the env vars we inject. They do **not** inherit host filesystem state, so cached OAuth credentials are missing.
- **Adapters**: our `CodexCliAdapter` supports `CODEX_API_KEY` or `CODEX_OAUTH_TOKEN`. Without either, runs fail.

## Risks & Constraints

1. **Interactive OAuth inside containers** is impractical (no browser, every run would require copy/paste).
2. **Long-lived env tokens** must be guarded; we should encourage scoped, short-lived tokens or read-only mounts.
3. **Harbor volume mounting** is not yet configurable through our orchestrator wrapper; adding it would require adapter changes plus docs.

## Proposed Enhancements

### Phase 1 – Doc-first, env-token workflow (fast)
1. **Guided doc**: add a “Codex OAuth in Harbor” section explaining:
   - Run `codex auth login --oauth` locally.
   - Run `codex auth token --scoped eval` (or similar) to mint a short-lived bearer token.
   - Export `CODEX_OAUTH_TOKEN` before invoking `eval-orchestrator`.
   - Clear the variable afterward.
2. **Helper script**: optional `scripts/codex-auth.sh` that:
   - Calls `codex auth token`, captures output, exports it for the current shell, and prints safety reminders.
3. **Adapter check**: enhance `CodexCliAdapter.validate()` to show distinct errors when neither API key nor OAuth token is present, pointing to the doc.

### Phase 2 – Credential mounts (optional, needs Harbor support)
1. Allow users to pass `--codex-credentials /path/to/.codex` to `eval-orchestrator`.
2. Adapter maps that host path to a fixed container path via new Harbor CLI flag (requires Harbor feature to expose host-volume mounts or we fallback to `HARBOUR_OPTS` env var).
3. Update docs to describe when to prefer mounts vs env tokens.

### Phase 3 – Automation / UX polish
1. Provide a pre-flight command (`eval-orchestrator auth codex`) that checks whether `CODEX_API_KEY` or `CODEX_OAUTH_TOKEN` is set and, if not, launches the helper script.
2. Cache sanitized credential metadata (expiration time) alongside run artifacts so auditors know which auth path was used without exposing secrets.

## Implementation Checklist (Phase 1 scope)

- [ ] Document env-token workflow in `docs/references/orchestration-flow.md`.
- [ ] Add helper script (optional) for exporting tokens.
- [ ] Update adapter error message with OAuth guidance.
- [ ] Add E2E README snippet showing `CODEX_OAUTH_TOKEN` usage.

## Open Questions

1. Does Codex expose a refreshable “service token” that is safe to inject via env vars, or must we rely on interactive tokens?
2. Will Harbor gain first-class support for host volume mounts? If not, we may need to run Harbor locally (without Docker sandbox) for OAuth flows.
3. Should we store Codex session logs produced inside the container anywhere outside the workspace (e.g., to help debug OAuth failures)?

Once Phase 1 is documented + shipped, we can revisit Phase 2 if evaluators regularly use OAuth-only accounts.
