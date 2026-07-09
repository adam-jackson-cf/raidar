# Runtime Hardcoding Audit

Scope: runtime implementation and adjacent runtime-owned adapters in
`orchestrator/src/raidar/runtime/**`, `orchestrator/src/raidar/harness/**`,
`orchestrator/src/raidar/assets/**`, and scorer evidence paths that replaced prior runtime
scorecard behavior.

This audit reflects the current canonical model:

- scenarios declare the environment, verification commands, setup actions, visual artifacts,
  and retained evidence they require;
- scorers declare scorer tooling requirements and own scorer-specific evidence collection;
- environments declare image-provided capabilities, build metadata, probes, resources, and
  verifier runner support;
- harness definitions own harness install, artifacts, command parser, usage parser, trace
  parser, rule file, and execution requirements;
- runtime code assembles those contracts and should not encode Node/Bun/Python/frontend
  assumptions except inside explicit environment, verifier, harness, or scorer definitions.

## Current Status

Resolved from the original audit:

- Harness installation and artifact contracts now live in `raidar.harness.definitions`.
- Harness command, usage, and trace parsing now live under `raidar.harness`.
- Legacy runtime/parser shims for command records, usage metrics, and trace parsing were removed.
- Scenario environment resolution now uses registered environments and capability requirements.
- Capability probe commands are catalog-driven through `environments/tools.yaml`.
- Verifier runner dispatch now goes through `raidar.runtime.verifier_runners`.
- Task bundle verifier script selection now uses the effective verifier runner contract.
- Command evidence no longer fabricates Bun commands from prose or normalizes npm/pnpm/yarn
  aliases to Bun. Extraction is seeded by scenario verification patterns and preserves exact
  observed commands.
- TypeScript/Istanbul coverage parsing and Testing Library requirement-evidence matching moved
  from generic runtime scorecard code to `raidar.scorers.code_task.typescript_evidence`.

## Active Findings

### Harbor Result Layout Is Still Runtime-Owned

Files:

- `orchestrator/src/raidar/runtime/harbor_results.py`
- `orchestrator/src/raidar/runtime/artifacts.py`
- `orchestrator/src/raidar/agents/harbor_agents/cli_agents.py`

Current issue:

- Runtime and agent code still know concrete Harbor paths such as `agent/`, verifier output
  names, result files, stdout/stderr logs, timing metadata, and final workspace archive names.

Required direction:

- Keep Harbor-specific parsing in a Harbor adapter/manifest boundary.
- Runtime phases should consume a declared result manifest instead of repeating fixed filenames.

Severity: High.

### Docker Image Policy Is Still Fixed In Runtime

Files:

- `orchestrator/src/raidar/runtime/harbor.py`

Current issue:

- Registry allowlists and unresolved `FROM $...` handling are fixed in code.

Required direction:

- Move image policy into runtime/environment configuration with strict defaults.
- Include policy inputs in cache keys where image admission affects build behavior.

Severity: High.

### Secret And Auth Metadata Remain Runtime/Harness Coupled

Files:

- `orchestrator/src/raidar/runtime/harbor_env.py`
- `orchestrator/src/raidar/agents/adapters/**`
- `orchestrator/src/raidar/harness/definitions.py`

Current issue:

- Secret names, redaction assumptions, auth metadata, and mount behavior remain spread across
  runtime environment assembly and harness/provider adapters.

Required direction:

- Add an auth/secret registry owned by harness/provider definitions.
- Runtime should merge declared secret mounts and redaction rules rather than knowing each
  provider-specific variable.

Severity: High.

### Cleanup Policy Still Uses Runtime Regexes

Files:

- `orchestrator/src/raidar/runtime/harbor_cleanup.py`

Current issue:

- Cleanup uses fixed container/process-name regexes and command-line matching.

Required direction:

- Prefer labels, generated job metadata, project names, or adapter-declared cleanup selectors.
- Avoid broad process-line regexes where a run id or Harbor job id can be used.

Severity: High.

### Workspace Copy, Prune, Cache, And Archive Policies Are Still Scattered

Files:

- `orchestrator/src/raidar/runtime/workspace.py`
- `orchestrator/src/raidar/runtime/workspace_artifacts.py`
- `orchestrator/src/raidar/runtime/workspace_cache.py`
- `orchestrator/src/raidar/runtime/task_bundle.py`
- `orchestrator/src/raidar/runtime/starter_preflight.py`

Current issue:

- Generated-directory exclusions, archive exclusions, copy rules, and prune behavior still live
  in multiple modules.
- Cache keys do not yet serialize every effective policy input.

Required direction:

- Introduce one workspace artifact policy object with copy, bundle, archive, prune, and
  generated-artifact rules.
- Include the effective policy in baseline workspace and task-image cache keys.

Severity: Medium.

### Environment Registry Roots Are Fixed

Files:

- `orchestrator/src/raidar/runtime/environments.py`

Current issue:

- Environment discovery assumes the repo-local `environments/` tree.

Required direction:

- Keep repo-local environments as the default, but allow matrix/runtime configuration to declare
  additional environment registry roots when needed.

Severity: Medium.

### Runtime Profile Still Contains Operational Defaults

Files:

- `orchestrator/src/raidar/runtime/profile.py`
- `orchestrator/src/raidar/runtime/harbor_preflight.py`
- `orchestrator/src/raidar/runtime/harbor_env.py`
- `orchestrator/src/raidar/runtime/task_images.py`

Current issue:

- Compose compatibility flags, Harbor preflight checks, Docker Compose minimums, retry limits,
  and timeouts are still mostly code defaults.

Required direction:

- Treat `RuntimeProfile` as the canonical holder for these operational defaults.
- Serialize the effective profile into cache keys and runtime metadata.

Severity: Medium.

### Verifier Assets Are Still Monolithic Per Runner

Files:

- `orchestrator/src/raidar/assets/verifier-score-scenario.mjs`
- `orchestrator/src/raidar/assets/verifier_score_scenario.py`
- `orchestrator/src/raidar/runtime/verifier_runners.py`

Current issue:

- Runner selection is now canonical, but each verifier asset still carries a large bundled
  implementation. The Bun verifier owns TypeScript/frontend behaviors; the Python verifier owns
  Python behaviors.

Required direction:

- This is acceptable as runner-owned behavior for now.
- If more verifier behaviors are added, split runner assets into registered scorer/verifier
  adapters rather than adding more generic runtime branching.

Severity: Medium.

### Scenario Verification Matching Has No Alias Policy

Files:

- `orchestrator/src/raidar/harness/command_records.py`
- `orchestrator/src/raidar/runtime/verification_metrics.py`
- `orchestrator/src/raidar/schemas/scenario.py`

Current issue:

- Verification command extraction now avoids hardcoded frontend aliases and only records exact
  scenario-configured command patterns plus git-commit bypass checks.
- There is no configured alias or matcher policy for scenarios that intentionally accept
  multiple equivalent commands.

Required direction:

- Do not reintroduce implicit Bun/npm/pnpm aliasing.
- If aliases are needed, add them to `scenario.verification` as explicit authored matchers and
  include them in cache/metadata.

Severity: Medium.

## Guardrails

- Do not add fallback compatibility exports for removed runtime APIs.
- Do not add behavior labels to environment capabilities; capabilities describe available
  runtimes, package managers, tools, and browsers only.
- Do not add scorer behavior to environment declarations; scorer behavior belongs in scorer code.
- Do not infer verification success from prose when an actual configured command was not
  observed.
- Keep runtime generic; put stack-specific behavior in environment, verifier, harness, scenario,
  or scorer definitions.
