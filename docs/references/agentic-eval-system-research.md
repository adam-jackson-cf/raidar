# Agentic Eval System: Research & Architecture

## Executive Summary

This document proposes an evaluation system for testing model/harness combinations on coding tasks, measuring both functional completion and implicit instruction adherence. The system builds on Harbor (the Terminal-Bench framework) as the execution substrate while adding custom scoring dimensions for verification gate behaviour, AGENTS.md compliance, and visual fidelity.

**Key design decisions:**
- Extend Harbor rather than build from scratch (validated infrastructure, Docker isolation, agent adapters)
- Separate "does it work" from "did it follow instructions" as independent scoring axes
- Track verification gate failures semantically (category + repetition count)
- Use deterministic checks where possible (Zod imports, file structure, linting results) with LLM-as-judge fallback
- odiff for visual regression against reference design
- Max 3 verification gate failures before task termination
- Context bundle capture adapted for session event logging

---

## Research Findings

### Terminal-Bench & Harbor

<cite index="9-1">Terminal-Bench 2.0 is the current standard for agent evaluation, with 89 rigorously validated Docker-containerised tasks.</cite> <cite index="17-1">Harbor abstracts away the complexities of container-based rollouts and scales from local Docker to cloud providers like Daytona and Modal.</cite>

**What Terminal-Bench provides:**
- Task specification format (YAML with instruction, environment, test scripts)
- Docker isolation per task run
- Pytest-based verification returning binary pass/fail
- Agent adapters for Claude Code, Codex CLI, Cursor, and custom agents
- Rollout infrastructure for parallel execution

**What we need to add:**
- Multi-dimensional scoring (functional + compliance + visual + efficiency)
- Verification gate event tracking with semantic categorisation
- AGENTS.md rule injection and compliance checking
- Visual regression via odiff
- Session log capture and event correlation

### Agent Performance Data

<cite index="13-1">Factory's Droid achieved 58.8% on Terminal-Bench, demonstrating that agent design is as decisive as model selection.</cite> This validates the premise that evaluating different harness/model/rules combinations is meaningful—small changes in agent architecture produce measurable performance differences.

### odiff for Visual Verification

<cite index="26-1">odiff is a SIMD-optimised image comparison library that completes pixel-by-pixel comparisons in milliseconds.</cite> It returns:
- `match: true` for identical images
- `match: false, reason: "pixel-diff", diffPercentage: number` for visual differences

This enables deterministic visual scoring: capture screenshot of implementation, compare against reference design PNG, return percentage match.

---

## Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                          EVAL ORCHESTRATOR                          │
├─────────────────────────────────────────────────────────────────────┤
│  Config: model, harness, rules variant, task definition             │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         HARBOR RUNTIME                              │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    DOCKER CONTAINER                          │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │   │
│  │  │ Task        │  │ Agent       │  │ Verification        │  │   │
│  │  │ Scaffold    │  │ (Codex/     │  │ Watcher             │  │   │
│  │  │ + AGENTS.md │  │  Claude)    │  │ (lint/type/test)    │  │   │
│  │  └─────────────┘  └─────────────┘  └─────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────┘   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       POST-RUN ANALYSIS                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │ Session Log  │  │ Compliance   │  │ Visual       │              │
│  │ Parser       │  │ Checker      │  │ Diff (odiff) │              │
│  │ (context     │  │ (deterministic│ │              │              │
│  │  bundles)    │  │  + LLM judge)│  │              │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         SCORECARD OUTPUT                            │
│  {                                                                  │
│    functional: { passed: bool, tests_passed: n, tests_total: n }   │
│    compliance: { score: 0-1, rules_checked: [...], failures: [...]}│
│    visual: { similarity: 0-1, diff_path: "..." }                   │
│    efficiency: { gate_failures: n, unique_issues: n, repeats: n }  │
│    event_log: [ { timestamp, type, data }, ... ]                   │
│  }                                                                  │
└─────────────────────────────────────────────────────────────────────┘
```

### Scoring Dimensions

| Dimension | Weight | Measurement Method | Pass Threshold |
|-----------|--------|-------------------|----------------|
| Functional | 40% | Pytest pass/fail + build succeeds | All tests pass |
| Compliance | 25% | Deterministic checks + LLM rubric | ≥80% rules followed |
| Visual | 20% | odiff pixel comparison | ≥95% similarity |
| Efficiency | 15% | Gate failures, issue repetition | ≤3 total failures |

**Composite scoring formula:**
```
score = (functional × 0.4) + (compliance × 0.25) + (visual × 0.2) + (efficiency × 0.15)
```

Where efficiency is calculated as:
```
efficiency = max(0, 1 - (gate_failures / 4) - (repeat_failures × 0.2))
```

### Task Definition Format

Extending Harbor's YAML format with custom fields:

```yaml
name: "homepage-figma-implementation"
description: "Implement homepage matching provided Figma design"
difficulty: medium
category: greenfield-ui
timeout_sec: 1800

# Standard Harbor fields
dockerfile: "./Dockerfile"
test_scripts:
  - "verify-build.sh"
  - "verify-tests.sh"
  - "verify-visual.sh"

# Custom fields for our eval
scaffold:
  template: "next-shadcn-starter"
  agents_md: "./rules/agents-strict.md"

verification:
  max_gate_failures: 3
  gates:
    - name: "typecheck"
      command: "bun run typecheck"
      on_failure: "continue"  # or "terminate"
    - name: "lint"
      command: "bunx ultracite check src"
      on_failure: "continue"
    - name: "test"
      command: "bun test"
      on_failure: "continue"

compliance:
  deterministic_checks:
    - type: "import_present"
      pattern: "from 'zod'"
      description: "Uses Zod for validation"
    - type: "file_exists"
      pattern: "src/components/ui/*.tsx"
      description: "Uses shadcn component structure"
    - type: "no_pattern"
      pattern: "style={{.*}}"
      description: "Avoids inline styles"

  llm_judge_rubric:
    - criterion: "Error handling follows AGENTS.md patterns"
      weight: 0.3
    - criterion: "Component composition follows shadcn patterns"
      weight: 0.4
    - criterion: "Code organisation matches project conventions"
      weight: 0.3

visual:
  reference_image: "./reference/homepage.png"
  screenshot_command: "bun run capture-screenshot"
  threshold: 0.95
```

### Verification Gate Watcher

The watcher intercepts verification command execution and logs:

```typescript
interface GateEvent {
  timestamp: string;
  gate_name: string;
  command: string;
  exit_code: number;
  stdout: string;
  stderr: string;
  failure_category?: string;  // Derived from stderr/stdout parsing
  is_repeat: boolean;         // Same category as previous failure
}

interface FailureCategory {
  id: string;
  pattern: RegExp;
  label: string;
}

const FAILURE_CATEGORIES: FailureCategory[] = [
  { id: "type_error", pattern: /TS\d+:/, label: "TypeScript Error" },
  { id: "lint_unused", pattern: /no-unused-vars/, label: "Unused Variable" },
  { id: "lint_import", pattern: /import\/order/, label: "Import Order" },
  { id: "lint_complexity", pattern: /complexity/, label: "Complexity" },
  { id: "test_assertion", pattern: /AssertionError/, label: "Test Assertion" },
  { id: "test_timeout", pattern: /Timeout/, label: "Test Timeout" },
  { id: "build_module", pattern: /Cannot find module/, label: "Missing Module" },
];
```

When `max_gate_failures` is reached, the task terminates early with partial results.

### Session Log Capture

Adapted from the Codex context bundle approach, the session log parser extracts:

```typescript
interface SessionEvent {
  timestamp: string;
  event_type: "user_prompt" | "assistant_message" | "file_change" |
              "bash_command" | "tool_call" | "gate_result";
  data: {
    content?: string;      // Truncated for prompts/messages
    file_path?: string;    // For file changes
    command?: string;      // For bash/tool calls
    gate_name?: string;    // For gate results
    success?: boolean;
  };
}
```

The parser reads from:
- **Codex CLI**: `~/.codex/sessions/YYYY/MM/DD/*.jsonl` + `~/.codex/history.jsonl`
- **Claude Code**: `~/.claude/projects/{project}/sessions/*.jsonl`

### AGENTS.md Rule Injection

Before task execution, the scaffold generator:

1. Copies base project template (Next.js + shadcn starter)
2. Injects the specified `agents_md` variant
3. Creates initial git commit as baseline
4. Records baseline linting/typecheck state

This enables A/B testing of rule variants:
- `agents-strict.md`: Full verification requirements
- `agents-minimal.md`: Basic guidelines only
- `agents-none.md`: No project-specific guidance

### Visual Regression Flow

```typescript
async function captureAndCompare(
  screenshotCommand: string,
  referencePath: string,
  outputDir: string
): Promise<VisualResult> {
  // Execute screenshot capture (e.g., Playwright headless)
  await exec(screenshotCommand);

  const actualPath = `${outputDir}/actual.png`;
  const diffPath = `${outputDir}/diff.png`;

  const result = await odiff.compare(
    referencePath,
    actualPath,
    diffPath,
    { threshold: 0.1 }  // Anti-aliasing tolerance
  );

  if (result.match) {
    return { similarity: 1.0, diff_path: null };
  }

  return {
    similarity: 1 - (result.diffPercentage / 100),
    diff_path: diffPath
  };
}
```

### Reference Design Specification

For the initial test task (homepage implementation), the reference design should be:

**Dimensions:** 1440×900 (desktop viewport)

**Layout:**
- Header: Logo left, nav links right (Home, About, Contact)
- Hero: Full-width, centered headline + subheadline + CTA button
- Features: 3-column grid with icon, title, description
- Footer: Copyright text centered

**Colours (Tailwind defaults):**
- Primary: `#2563eb` (blue-600)
- Background: `#ffffff`
- Text: `#1f2937` (gray-800)
- Muted: `#6b7280` (gray-500)

**Typography:**
- Headings: Inter, font-bold
- Body: Inter, font-normal

This deterministic design ensures odiff scoring is meaningful—any significant deviation produces measurable pixel differences.

---

## Test Task: Homepage Implementation

### Task Prompt

```markdown
Implement a homepage for a SaaS product landing page.

The page should include:
1. A header with logo and navigation links (Home, About, Contact)
2. A hero section with headline, subheadline, and call-to-action button
3. A features section displaying 3 features in a grid
4. A footer with copyright text

The design reference image is available at ./reference/homepage.png

Use the existing project setup with shadcn/ui components.
Run `bun run dev` to start the development server.
Run `bun run build` to verify the build succeeds.
Run `bun test` to run the test suite.
```

### AGENTS.md Rules (Strict Variant)

```markdown
# Project Guidelines

## Code Style
- Use TypeScript strict mode
- Use Tailwind utility classes exclusively (no inline styles, no CSS modules)
- Use shadcn/ui components from `@/components/ui`
- Use Lucide icons via `lucide-react`

## Validation
- Use Zod for all input validation schemas
- Use React Hook Form with Zod resolver for forms

## Component Structure
- Place page components in `app/` directory
- Place reusable components in `components/`
- Export types from component files

## Quality Gates
Before committing, ensure:
1. `bun run typecheck` passes
2. `bunx ultracite check src` passes
3. `bun test` passes

## Error Handling
- Use try/catch with typed error handling
- Display user-friendly error messages
- Log errors to console in development only
```

### Expected Compliance Checks

| Check Type | Pattern | Pass Condition |
|------------|---------|---------------|
| Deterministic | `from 'zod'` | Import present |
| Deterministic | `style={{` | Pattern absent |
| Deterministic | `lucide-react` | Import present |
| LLM Judge | Component composition | Follows shadcn patterns |
| LLM Judge | Error handling | Matches guidelines |

---

## Implementation Roadmap

### Phase 1: Core Infrastructure (Week 1-2)

**Deliverables:**
- Harbor installation and local Docker execution verified
- Custom task YAML schema with compliance/visual extensions
- Next.js + shadcn scaffold template
- Reference design PNG created
- Basic orchestrator script

**Success criteria:**
- Can execute a task against Codex CLI locally
- Task produces pass/fail result

### Phase 2: Scoring System (Week 2-3)

**Deliverables:**
- Verification gate watcher with failure categorisation
- Session log parser for Codex (adapt existing context bundle code)
- odiff integration for visual comparison
- Deterministic compliance checker
- LLM-as-judge rubric evaluator

**Success criteria:**
- Run produces multi-dimensional scorecard
- Failure categories are semantically labelled
- Repeat failures are tracked

### Phase 3: Comparison Infrastructure (Week 3-4)

**Deliverables:**
- Run configuration matrix (model × harness × rules variant)
- Aggregation and comparison reports
- Event log analysis (identify patterns in failures)
- Export to CSV/JSON for further analysis

**Success criteria:**
- Can compare Codex vs Claude Code on same task
- Can compare strict vs minimal AGENTS.md variants
- Can identify which configurations produce fewer repeat failures

### Phase 4: Expansion (Future)

**Deferred items:**
- Automated task generation from natural language descriptions
- Brownfield test tasks (refactoring legacy code)
- Multi-file feature implementation tasks
- Cloud execution via Daytona/Modal

---

## Event Log Schema

```typescript
interface EvalRun {
  id: string;
  timestamp: string;
  config: {
    model: string;           // "claude-opus-4" | "gpt-5" | etc
    harness: string;         // "codex-cli" | "claude-code" | "cursor"
    rules_variant: string;   // "strict" | "minimal" | "none"
    task_name: string;
  };
  duration_sec: number;
  terminated_early: boolean;
  termination_reason?: string;

  scores: {
    functional: {
      passed: boolean;
      tests_passed: number;
      tests_total: number;
      build_succeeded: boolean;
    };
    compliance: {
      score: number;          // 0-1
      checks: ComplianceCheck[];
    };
    visual: {
      similarity: number;     // 0-1
      diff_path?: string;
    };
    efficiency: {
      total_gate_failures: number;
      unique_failure_categories: number;
      repeat_failures: number;
    };
    composite: number;        // Weighted final score
  };

  events: SessionEvent[];
  gate_history: GateEvent[];
}

interface ComplianceCheck {
  rule: string;
  type: "deterministic" | "llm_judge";
  passed: boolean;
  evidence?: string;
}
```

---

## Technology Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Orchestrator | Python (uv) | Harbor is Python; minimise polyglot complexity |
| Task runtime | Docker | Harbor standard; isolation guaranteed |
| Test scaffold | Next.js 15 + shadcn + Tailwind | User preference; production-representative |
| Type checking | TypeScript strict | Quality gate |
| Linting | Ultracite (bunx) | User preference |
| Visual diff | odiff-bin (npm) | Fast, deterministic, Node.js bindings |
| Screenshot | Playwright | Headless Chrome, reliable capture |
| LLM Judge | Claude (via API) | Consistent with your toolchain |

---

## Dependencies Checklist

**System:**
- [ ] Docker Desktop installed and running
- [ ] Python 3.11+ with uv
- [ ] Node.js 20+ with Bun
- [ ] Codex CLI installed and authenticated
- [ ] Claude Code installed and authenticated

**Python packages:**
- [ ] harbor (pip install harbor)
- [ ] litellm (for LLM judge calls)

**Node packages:**
- [ ] odiff-bin
- [ ] playwright

**Assets:**
- [ ] Reference homepage design (PNG, 1440×900)
- [ ] Next.js + shadcn starter template
- [ ] AGENTS.md variants (strict, minimal, none)

---

## Open Questions

1. **Claude Code session logs:** Need to verify the exact location and format of Claude Code's session logs to adapt the context bundle parser. The Codex format is documented; Claude Code may differ.

2. **Harness timeout vs gate failure limit:** Should we use a time-based timeout alongside the gate failure limit, or rely solely on failure count? Current proposal: gate failure limit only, as per your input.

3. **LLM Judge model selection:** Should the judge use the same model being evaluated, or a fixed model (e.g., always Claude Opus) for consistency? Recommendation: fixed model for judge to avoid evaluator variance.

4. **Visual threshold calibration:** The 95% similarity threshold may need tuning based on initial runs. Anti-aliasing differences and font rendering variations can produce false negatives.

---

## Next Steps

1. **Confirm architecture** - Review this document; flag any misalignments with intent
2. **Create reference design** - Simple Figma mockup exported as PNG
3. **Set up Harbor locally** - Install and verify basic Terminal-Bench task execution
4. **Build scaffold template** - Next.js + shadcn starter with quality gates configured
5. **Implement verification watcher** - Gate interception and failure categorisation
6. **First end-to-end run** - Single task, single model/harness, verify scorecard output

---

## References

- Terminal-Bench: https://www.tbench.ai/
- Harbor Framework: https://github.com/laude-institute/harbor
- odiff: https://github.com/dmtrKovalenko/odiff
- Context Bundle (Codex): https://github.com/adam-jackson-cf/enaible/tree/main/shared/context
