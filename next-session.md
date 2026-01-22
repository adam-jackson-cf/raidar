# Handoff: Merge Agentic Eval System Implementations

## Starting Prompt

Execute the merge plan in `/Users/adamjackson/.claude/plans/majestic-hugging-minsky.md` to combine the best components from 4 parallel implementations into main.

**Context:** We ran 4 parallel Claude sessions to implement an agentic evaluation system. Each implementation is in a git worktree. Analysis determined impl-3 should be the base, with cherry-picks from impl-1, impl-2, and impl-4.

**Execute these phases in order:**

1. Merge impl-3 into main as base
2. Cherry-pick test infrastructure from impl-2 (vitest.config.ts, test deps)
3. Cherry-pick comparison module from impl-4 (aggregator.py, matrix_runner.py)
4. Cherry-pick schemas from impl-1/impl-2 (task.py, scorecard.py, evaluator.py)
5. Add CLI enhancements from impl-4 (list-agents, info commands)
6. Verify merged system works

After merge, run verification commands in the plan to confirm everything works.

## Relevant Files

| File | Purpose |
|------|---------|
| `/Users/adamjackson/.claude/plans/majestic-hugging-minsky.md` | **READ FIRST** - Complete merge plan with commands |
| `docs/agentic-eval-system-research.md` | Original architecture specification |
| `docs/implementation-plan.md` | Implementation phases (all 4 completed) |
| `/Users/adamjackson/Projects/eval-impl-3/orchestrator/src/` | Primary base - cleanest architecture |
| `/Users/adamjackson/Projects/eval-impl-2/scaffold/vitest.config.ts` | Test infrastructure to cherry-pick |
| `/Users/adamjackson/Projects/eval-impl-4/orchestrator/src/agentic_eval/comparison/` | Aggregation module to cherry-pick |
| `/Users/adamjackson/Projects/eval-impl-1/orchestrator/src/agentic_eval/evaluator.py` | Evaluator to cherry-pick |

## Key Context

**Worktree Locations:**
```
/Users/adamjackson/Projects/typescript-ui-eval  [main] - merge target
/Users/adamjackson/Projects/eval-impl-1         [impl-1] - 11 commits
/Users/adamjackson/Projects/eval-impl-2         [impl-2] - 11 commits
/Users/adamjackson/Projects/eval-impl-3         [impl-3] - 5 commits ⭐ BASE
/Users/adamjackson/Projects/eval-impl-4         [impl-4] - 5 commits
```

**Cherry-Pick Summary:**
| Component | Source | Reason |
|-----------|--------|--------|
| Base structure | impl-3 | Cleanest (2,346 LOC) |
| Test infra | impl-2 | Only one with vitest setup |
| Comparison | impl-4 | Best aggregation + parallel execution |
| Schemas | impl-1/impl-2 | Most complete models |
| CLI commands | impl-4 | list-agents, info |

**Outstanding After Merge (Priority Order):**
1. Unit tests - ALL implementations have ZERO tests
2. LLM judge robustness - fragile JSON parsing
3. Configurable values - hardcoded thresholds
4. Session log parsers - only Codex supported
5. Pre-commit hooks
6. Documentation

**Tech Stack:**
- Orchestrator: Python 3.12+, uv, Pydantic, Click, LiteLLM
- Scaffold: Next.js 16, React 19, Tailwind 4, shadcn/ui
- Execution: Harbor framework, Docker
