# Handoff: Implement Outstanding Items for Agentic Eval System

## Starting Prompt

Execute the plan at `/Users/adamjackson/.claude/plans/delightful-cooking-conway.md` to implement 5 outstanding items for the agentic eval system.

**Context:** We just completed merging 4 parallel implementations into main. The system works but has gaps: no tests, hardcoded config values, fragile LLM parsing, and incomplete session log parsers.

**Execute these phases in order:**

1. **Phase 1: Configurable Values** - Create `config.py` with pydantic-settings, extract 29 hardcoded values
2. **Phase 2: LLM Judge Robustness** - Fix fragile parsing in compliance.py with fallback strategies
3. **Phase 3: Unit Tests** - Create tests/ directory with ~40 tests across 6 test files
4. **Phase 4: Session Log Parsers** - Implement Gemini parser, fix Claude Code parser
5. **Phase 5: Pre-commit Hooks** - Add ruff + pytest hooks

**Also:** Delete `docs/implementation-plan.md` (already actioned, no longer needed).

After each phase, run the verification commands in the plan to confirm everything works.

## Relevant Files

| File | Purpose |
|------|---------|
| `/Users/adamjackson/.claude/plans/delightful-cooking-conway.md` | **READ FIRST** - Complete implementation plan |
| `orchestrator/src/agentic_eval/config.py` | **CREATE** - Centralized configuration |
| `orchestrator/src/agentic_eval/scoring/compliance.py` | LLM judge parsing to fix (line 142) |
| `orchestrator/src/agentic_eval/schemas/scorecard.py` | Has duplicated weights to consolidate |
| `orchestrator/src/agentic_eval/parser/session_log.py` | Gemini stub, Claude incomplete |
| `orchestrator/tests/` | **CREATE** - Test infrastructure |
| `docs/implementation-plan.md` | **DELETE** - Already actioned |

## Key Context

**This Session Completed:**
- Merged impl-3 as base + cherry-picked from impl-1, impl-2, impl-4
- Added vitest infrastructure, comparison module, CLI commands
- Fixed visual=None validation, lint script
- All CLI commands working (`run`, `matrix`, `list-agents`, `info`, etc.)

**Key Findings from Exploration:**
- 29 hardcoded values found (timeouts, weights, model names)
- Weights duplicated in `scoring/__init__.py` AND `schemas/scorecard.py`
- LLM judge at compliance.py:142 uses fragile first-line parsing
- Gemini parser returns empty list (stub only)
- Claude parser misses tool_result entries

**Tech Stack:**
- Orchestrator: Python 3.12+, uv, Pydantic, Click, LiteLLM
- Testing: pytest 8.0+, pytest-asyncio (already in pyproject.toml)
- Config: pydantic-settings (to add)
