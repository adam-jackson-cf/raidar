# Implementation Plan: Agentic Eval System

## Overview

Build an evaluation system for testing model/harness combinations on coding tasks. Extends Harbor (Terminal-Bench framework) with multi-dimensional scoring: functional, compliance, visual, and efficiency.

**Source Document:** `docs/agentic-eval-system-research.md`

---

## Project Structure

```
typescript-ui-eval/
├── orchestrator/              # Python orchestrator (uv)
│   ├── pyproject.toml
│   ├── src/
│   │   ├── __init__.py
│   │   ├── cli.py             # CLI entrypoint
│   │   ├── runner.py          # Task execution via Harbor
│   │   ├── harness/
│   │   │   ├── __init__.py
│   │   │   ├── config.py      # Harness/model configuration
│   │   │   └── rules.py       # CLI-to-rule-file mapping
│   │   ├── scoring/
│   │   │   ├── __init__.py
│   │   │   ├── functional.py
│   │   │   ├── compliance.py
│   │   │   ├── visual.py
│   │   │   └── efficiency.py
│   │   ├── watcher/
│   │   │   └── gate_watcher.py
│   │   ├── parser/
│   │   │   └── session_log.py
│   │   ├── audit/
│   │   │   └── scaffold_manifest.py  # Base project auditing
│   │   └── schemas/
│   │       ├── task.py
│   │       ├── scorecard.py
│   │       └── events.py
│   └── tests/
├── scaffold/                  # Next.js + shadcn starter template
│   ├── package.json
│   ├── scaffold.manifest.json # Audit manifest (auto-generated)
│   └── ...
├── tasks/
│   └── homepage-implementation/
│       ├── task.yaml
│       ├── reference/
│       │   └── homepage.png
│       └── rules/             # Rule variants per CLI
│           ├── strict/
│           │   ├── AGENTS.md      # For codex/pi
│           │   ├── CLAUDE.md      # For claude-code
│           │   ├── copilot-instructions.md
│           │   └── GEMINI.md
│           ├── minimal/
│           │   └── ...
│           └── none/
│               └── ...
├── docs/
│   ├── agentic-eval-system-research.md
│   └── implementation-plan.md  # Copy of this plan
└── results/
```

---

## Key Decisions

- **Primary Harness:** Codex CLI (session log format documented, simpler integration)
- **Reference Design:** Create HTML/Tailwind mockup and screenshot it
- **Harbor:** Verify installation and setup as first step
- **Harness Configuration:** Leverage Harbor's existing `-a` and `-m` CLI flags
- **Rule File Mapping:** Borrow from enaible's SYSTEM_RULES pattern
- **Parallel Execution:** Use git worktrees for 4 parallel build comparison

---

## Harness & Model Configuration

### Harbor CLI (Already Supported)
```bash
harbor run -d terminal-bench@2.0 -a "<agent>" -m "<model>"
```

### Supported Agents via Harbor
| Agent | Description |
|-------|-------------|
| `claude-code` | Claude Code CLI |
| `codex` | Codex CLI (OpenAI) |
| `gemini` | Gemini CLI |
| `openhands` | OpenHands agent |
| Custom | Via `--agent-import-path` |

### Model Selection (via LiteLLM)
```bash
# Examples
harbor run -a codex -m openai/gpt-5
harbor run -a claude-code -m anthropic/claude-sonnet-4-5
```

---

## CLI-to-Rule-File Mapping

Borrowed from enaible (`/Users/adamjackson/Projects/enaible/tools/enaible/src/enaible/commands/install.py`):

```python
SYSTEM_RULES = {
    "claude-code": "CLAUDE.md",
    "codex": "AGENTS.md",
    "copilot": "copilot-instructions.md",
    "cursor": "user-rules-setting.md",
    "gemini": "GEMINI.md",
    "pi": "AGENTS.md",
}
```

### Rule Injection Logic
1. Determine target CLI from task config
2. Look up rule filename from SYSTEM_RULES mapping
3. Copy appropriate variant (strict/minimal/none) to scaffold root
4. Rename to target filename (e.g., `strict/CLAUDE.md` → `CLAUDE.md`)

---

## Base Project Auditing

### Scaffold Manifest
Generate `scaffold.manifest.json` capturing baseline state:

```json
{
  "generated_at": "2026-01-22T10:00:00Z",
  "version": "1.0.0",
  "files": {
    "package.json": { "hash": "sha256:...", "size": 1234 },
    "tsconfig.json": { "hash": "sha256:...", "size": 567 }
  },
  "dependencies": {
    "next": "15.x.x",
    "tailwindcss": "4.x.x"
  },
  "quality_gates": {
    "typecheck": "bun run typecheck",
    "lint": "bunx ultracite check src",
    "test": "bun test"
  },
  "lint_rules": ["...extracted from ultracite config..."],
  "pre_commit_hooks": ["typecheck", "lint"]
}
```

### Audit in Scorecard
Include scaffold baseline in results:
```json
{
  "scores": { ... },
  "scaffold_baseline": {
    "manifest_version": "1.0.0",
    "lint_rules_count": 42,
    "pre_commit_hooks": ["typecheck", "lint"],
    "changes_from_baseline": [
      "Added: eslint-plugin-security (lint rule)"
    ]
  }
}
```

---

## Implementation Phases

### Phase 1: Core Infrastructure

**Goal:** Working baseline with harness/model configuration.

#### Step 1.0: Harbor Verification
- Verify Docker Desktop running
- Install Harbor: `uv add harbor`
- Run Harbor smoke test
- **Commit checkpoint**

#### Step 1.1: Python Project Setup
- Initialize `orchestrator/` with uv
- Dependencies: `harbor`, `litellm`, `pydantic`, `pyyaml`, `click`

#### Step 1.2: Harness Configuration Module
- Create `harness/config.py` - wraps Harbor's `-a` and `-m` flags
- Create `harness/rules.py` - SYSTEM_RULES mapping from enaible
- Support config via YAML or CLI args

#### Step 1.3: Next.js Scaffold Template
- Create `scaffold/` with Next.js 15 + shadcn/ui + Tailwind
- Configure TypeScript strict, quality gates
- **Generate initial `scaffold.manifest.json`**
- **Commit checkpoint**

#### Step 1.4: Scaffold Audit Module
- Create `audit/scaffold_manifest.py`
- Generate manifest from scaffold directory
- Diff manifest between runs to detect changes
- Track lint rules, pre-commit hooks, dependencies

#### Step 1.5: Task Definition Schema
- Pydantic models for task YAML
- Include: harness, model, rules_variant, scaffold_template

#### Step 1.6: Reference Design
- Create HTML/Tailwind mockup
- Screenshot at 1440x900
- Save as reference PNG

#### Step 1.7: Rule Variants
- Create `tasks/homepage-implementation/rules/strict/` with all CLI variants
- Create minimal and none variants
- Test rule injection for each supported CLI

#### Step 1.8: Basic Orchestrator
- Load task YAML
- Copy scaffold, inject rules per CLI type
- Execute via Harbor with configured harness/model
- Report pass/fail

**Checkpoint:** Run task with configurable harness/model, correct rule file injected.

---

### Phase 2: Scoring System

**Goal:** Multi-dimensional scorecard with scaffold audit.

#### Step 2.1-2.7: (As previously defined)
- Functional scoring
- Gate watcher with categorization
- Session log parser
- Compliance checker (deterministic + LLM judge)
- Visual regression (odiff)
- Efficiency scoring
- Composite scorecard

#### Step 2.8: Integrate Scaffold Audit
- Include scaffold manifest in scorecard output
- Track changes from baseline (new rules, hooks, deps)

**Checkpoint:** Full scorecard with scaffold baseline audit.

---

### Phase 3: Comparison Infrastructure

**Goal:** Compare harness/model/rules combinations.

#### Step 3.1: Configuration Matrix
```yaml
matrix:
  harnesses: [codex, claude-code]
  models: [openai/gpt-5, anthropic/claude-sonnet-4-5]
  rules_variants: [strict, minimal, none]
```

#### Step 3.2: Run Storage & Aggregation
- Save runs to `results/{run_id}.json`
- Include scaffold audit in each run
- Generate comparison reports

**Checkpoint:** Compare multiple harness/model/rules combinations.

---

## Parallel Execution Strategy (4 Implementations)

### Approach: Git Worktrees

Per Claude Code documentation, git worktrees provide isolated parallel execution:

```bash
# Setup from main project
cd /Users/adamjackson/Projects/typescript-ui-eval

# Create 4 parallel worktrees
git worktree add ../eval-impl-1 -b impl-1
git worktree add ../eval-impl-2 -b impl-2
git worktree add ../eval-impl-3 -b impl-3
git worktree add ../eval-impl-4 -b impl-4
```

### Execution (4 Terminal Windows)
```bash
# Terminal 1
cd ../eval-impl-1 && claude
# Prompt: "Implement the plan in docs/implementation-plan.md"

# Terminal 2
cd ../eval-impl-2 && claude
# Same prompt

# Terminal 3-4: Same pattern
```

### Comparison (After All Complete)
```bash
cd /Users/adamjackson/Projects/typescript-ui-eval
claude
# Prompt: "Compare implementations in @../eval-impl-1, @../eval-impl-2,
#          @../eval-impl-3, @../eval-impl-4. Create comparison matrix."
```

### Cleanup
```bash
git worktree list
git worktree remove ../eval-impl-1  # After merging selected impl
```

---

## Critical Files to Create

| File | Purpose |
|------|---------|
| `orchestrator/src/harness/config.py` | Harness/model configuration |
| `orchestrator/src/harness/rules.py` | CLI-to-rule-file mapping |
| `orchestrator/src/audit/scaffold_manifest.py` | Base project auditing |
| `tasks/.../rules/strict/CLAUDE.md` | Claude Code strict rules |
| `tasks/.../rules/strict/AGENTS.md` | Codex strict rules |
| `docs/implementation-plan.md` | Copy of this plan for parallel execution |

---

## Verification Plan

### After Phase 1
1. Run with `--harness codex --model openai/gpt-5`
2. Run with `--harness claude-code --model anthropic/claude-sonnet-4-5`
3. Verify correct rule file injected for each CLI
4. Verify scaffold manifest generated

### After Phase 2
1. Verify scaffold audit appears in scorecard
2. Verify changes from baseline tracked

### After Phase 3
1. Run comparison matrix
2. Verify CSV export includes scaffold audit data

---

## Notes

- Following atomic commit pattern
- No backward compatibility or fallbacks
- Complexity under 10
- Leverage Harbor's existing harness/model support
- Borrow CLI mapping from enaible
- Plan stored in docs/ for parallel execution reference
