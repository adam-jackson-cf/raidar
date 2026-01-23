# RESULT
- Summary: Architecture assessment completed for /Users/adamjackson/Projects/typescript-ui-eval (monorepo) at MIN_SEVERITY=high; analyzer outputs stored for audit.
- Artifacts: `.enaible/artifacts/analyze-architecture/20260123T104444Z/`

## RECONNAISSANCE
- Project type: Monorepo (Python orchestrator + Next.js scaffold)
- Primary stack: Python 3.12 (Pydantic, Click, LiteLLM, uv) and Next.js 15/React 19 with Tailwind 4 + shadcn/ui
- Detected languages: python, typescript/tsx, yaml/json
- Auto-excluded: `dist/**`, `build/**`, `node_modules/**`, `__pycache__/**`, `.next/**`, `vendor/**`, `.venv/**`, `.mypy_cache/**`, `.ruff_cache/**`, `.pytest_cache/**`, `.gradle/**`, `target/**`, `bin/**`, `obj/**`, `coverage/**`, `.turbo/**`, `.svelte-kit/**`, `.cache/**`, `.enaible/artifacts/**`

## ARCHITECTURE OVERVIEW
- Domain Boundaries: The orchestration core (CLI, runner, matrix) coordinates Harbor execution and workspace prep (`orchestrator/src/agentic_eval/cli.py:1`, `orchestrator/src/agentic_eval/runner.py:24`, `orchestrator/src/agentic_eval/comparison/matrix_runner.py:40`). Scoring/compliance relies on deterministic + LLM checks tied to the global `settings` singleton (`orchestrator/src/agentic_eval/scoring/compliance.py:11`, `orchestrator/src/agentic_eval/schemas/scorecard.py:14`). UI work happens inside the Next.js scaffold consumed by tasks such as `tasks/homepage-implementation/task.yaml:1`.
- Layering & Contracts: CLI commands map directly to Harbor invocations without service abstractions, so workspace provisioning, manifest auditing, and run execution all live in `run_task` (`orchestrator/src/agentic_eval/runner.py:61`), while gate execution shells out directly from `GateWatcher` with the same global timeout contract for every gate type (`orchestrator/src/agentic_eval/watcher/gate_watcher.py:55`). Config contracts are centralized in `config.py` and imported by schemas, tests, and watchers, creating an implicit dependency direction from data models back to config code.
- Patterns Observed: `architecture:patterns` flagged 53 high-severity God-Class instances — notably the Pydantic models (`config.py`, `schemas/task.py`, `schemas/scorecard.py`) and operational classes (`comparison/aggregator.py`, `comparison/matrix_runner.py`, `watcher/gate_watcher.py`) that encapsulate policy, orchestration, and data concerns simultaneously (`.enaible/artifacts/analyze-architecture/20260123T104444Z/architecture-patterns.json`). No high-severity dependency, coupling, or scalability issues were emitted, though 9 medium scalability signals were filtered per severity gate (`architecture-scalability.json` metadata).

## DEPENDENCY MATRIX (Top Findings)
| Source Module | Target Module | Notes | Evidence |
| ------------- | ------------- | ----- | -------- |
| — | — | `architecture:dependency` emitted 0 findings at MIN_SEVERITY=high (metadata shows 64 files analyzed, 0 violations). | `.enaible/artifacts/analyze-architecture/20260123T104444Z/architecture-dependency.json` |

## COUPLING HOTSPOTS
| Component | Finding | Impact | Analyzer |
| --------- | ------- | ------ | -------- |
| — | No high-severity coupling hotspots detected (analyzer processed 63 files, all below threshold). | Coupling review deferred to artifact if severity threshold is lowered. | `.enaible/artifacts/analyze-architecture/20260123T104444Z/architecture-coupling.json` |

## RISKS & GAPS
1. Runner + MatrixRunner concentrate workspace prep, Harbor invocation, scoring, and artifact persistence in a single stack, so introducing alternative execution engines or async job control requires rewriting monolithic functions (`orchestrator/src/agentic_eval/runner.py:25`, `orchestrator/src/agentic_eval/comparison/matrix_runner.py:40`). Impact: high (blast radius across all CLI flows); Likelihood: high (already evidenced by analyzer God-Class warnings).
2. GateWatcher executes user-defined shell commands with `shell=True` and only regex categorization, exposing the host to arbitrary command execution without sandboxing or streaming safeguards (`orchestrator/src/agentic_eval/watcher/gate_watcher.py:65`). Impact: high (security + stability); Likelihood: medium (depends on task author discipline).
3. Session parser coverage contradicts CLI promises: CLI advertises `openhands`/`copilot`, yet `parse_session` only handles `codex-cli`, `claude-code`, `gemini` and returns an empty list otherwise (`orchestrator/src/agentic_eval/parser/session_log.py:340`). Impact: medium (missing telemetry, undermining audits); Likelihood: high (any non-supported agent silently drops logs).
4. Global `settings` singleton leaks into schemas (`orchestrator/src/agentic_eval/schemas/scorecard.py:32`) and scoring logic, so tests and concurrent runs cannot vary weights/timeouts independently, risking inconsistent scoring across matrices. Impact: medium; Likelihood: high whenever multiple evaluations run with different requirements.
5. Matrix runs never clean up generated workspaces; run IDs only include timestamps, so parallel runs can still collide with shared directories and steadily grow disk usage (`orchestrator/src/agentic_eval/comparison/matrix_runner.py:70`). Impact: medium (resource exhaustion, stray artifacts); Likelihood: medium.

## GAP ANALYSIS
| Gap Category              | Status            | Finding                                                                 | Confidence |
| ------------------------- | ----------------- | ----------------------------------------------------------------------- | ---------- |
| Business domain alignment | Inspected         | Task/compliance schema matches orchestrator needs, but schemas depend on runtime settings singleton, so data layer is not isolated from config policy (`orchestrator/src/agentic_eval/schemas/scorecard.py:32`). | Medium |
| Team ownership boundaries | Flagged           | No `CODEOWNERS` or ownership metadata found; ownership of orchestrator vs. scaffold modules cannot be inferred automatically. Requires manual verification. | Low |
| Cross-cutting concerns    | Inspected         | Logging and security hardening are minimal—gate execution, Harbor calls, and session parsing rely on print/click outputs with no structured audit trail or sandboxing (`orchestrator/src/agentic_eval/runner.py:85`, `orchestrator/src/agentic_eval/watcher/gate_watcher.py:65`). | Medium |

## RECOMMENDATIONS
1. Refactor the orchestration pipeline into smaller services (workspace prep, Harbor executor, scoring, artifact writer) and inject them into CLI/matrix commands so alternative backends or async queues can be plugged in without editing `runner.py`/`matrix_runner.py`. Include an interface for streaming Harbor logs instead of blocking `subprocess.run`.
2. Introduce a gate execution sandbox (dedicated runner container or constrained subprocess wrapper) and structured logging around GateWatcher; replace `shell=True` commands with argument lists plus allowlists per task, and stream outputs into events for observability.
3. Expand `parser/session_log.py` to cover every agent exposed by the CLI (openhands, copilot, future harnesses), and add contract tests to ensure telemetry is captured for each harness.
4. Replace the global `settings` singleton with dependency-injected settings snapshots per run, allowing scorecards/tests to assert against deterministic configuration and reducing incidental coupling between schemas and config.
5. Add workspace lifecycle management to `MatrixRunner` (per-run temp dirs + cleanup hooks) and provide retention policies to prevent orphaned directories during large evaluation matrices.
6. Establish CODEOWNERS/ownership markers and adopt structured logging (e.g., stdlib `logging` or structlog) so audit artifacts tie back to accountable teams—this also unlocks richer analyzer coverage in future runs.

## ATTACHMENTS
- architecture:patterns → `.enaible/artifacts/analyze-architecture/20260123T104444Z/architecture-patterns.json`
- architecture:dependency → `.enaible/artifacts/analyze-architecture/20260123T104444Z/architecture-dependency.json`
- architecture:coupling → `.enaible/artifacts/analyze-architecture/20260123T104444Z/architecture-coupling.json`
- architecture:scalability → `.enaible/artifacts/analyze-architecture/20260123T104444Z/architecture-scalability.json`
- Recon & synthesis notes → `.enaible/artifacts/analyze-architecture/20260123T104444Z/recon.md`, `.enaible/artifacts/analyze-architecture/20260123T104444Z/synthesis-notes.md`
