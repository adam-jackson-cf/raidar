# Context Code Map

- Created: 2026-03-25

| Area | File anchor | Current behavior | Planned change |
| ---- | ----------- | ---------------- | -------------- |
| Scenario-exclusion policy | `/Users/adamjackson/Projects/raidar/AGENTS.md:11` | Repo guidance excludes starter folders from analysis/code-quality checks | Align tooling/config enforcement with the documented rule |
| Orchestrator smoke entry | `/Users/adamjackson/Projects/raidar/Makefile:205` | Public smoke target for orchestrator runtime sanity | Use as mandatory repeated warm-path verification |
| Smoke matrix entry | `/Users/adamjackson/Projects/raidar/Makefile:216` | Public smoke matrix target | Preserve invocation shape while refactors land |
| Fast image ensure | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/runner.py:1718` | Builds or reuses fast task images | Resolve live cross-invocation warm-path gap |
| Prep context creation | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/runner.py:1961` | Creates baseline/preflight-backed run context | Preserve cache semantics while extending observability |
| Prep phase orchestration | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/runner.py:4134` | Emits prep timings and cache metadata | Extend metadata/reporting for investigation and regression tracking |
| Orchestrator CLI experiment run | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/cli.py:984` | Wide experiment-run command | Extract typed option/request helper |
| Experiment summary assembly | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/experiment.py:155` | Large experiment summary payload construction | Split into typed helpers and preserve downstream shape |
| Storage CSV export | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/storage.py:177` | Large row serializer | Extract row builders and surface cache metadata intentionally |
| Auto-researcher CLI init | `/Users/adamjackson/Projects/raidar/auto_researcher/src/auto_researcher/cli.py:124` | Wide init Click entrypoint | Extract typed request-building helper |
| Promotion guard | `/Users/adamjackson/Projects/raidar/auto_researcher/src/auto_researcher/engine.py:669` | Concentrated promotion decision logic | Split into smaller rule evaluators |
| Claude adapter | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/agents/adapters/claude_code_cli.py:15` | Provider-specific adapter implementation | Extract shared adapter behavior safely |
| Gemini adapter | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/agents/adapters/gemini_cli.py:15` | Provider-specific adapter implementation | Extract shared adapter behavior safely |
| Codex adapter | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/agents/adapters/codex_cli.py:15` | Comparison adapter with similar runtime/workspace concerns | Keep provider-specific model alias behavior intact |
| Fast Harbor agents | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/agents/harbor_agents/fast_cli_agents.py:1` | Low-coverage Harbor fast agents | Add deterministic coverage without scenario edits |
| Acceptance scoring | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/scoring/acceptance.py:239` | Low-coverage acceptance evaluation | Add targeted parser/evaluator coverage |
| Verification stability | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/scoring/verification_stability.py:22` | Low-coverage stability scoring | Add direct evaluator coverage |
| Gate watcher | `/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/watcher/gate_watcher.py:56` | Low-coverage gate execution path | Add timeout/not-found/repeat-failure coverage |
