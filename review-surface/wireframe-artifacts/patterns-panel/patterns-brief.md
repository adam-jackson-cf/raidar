# Raidar wireframe Patterns panel redesign brief

You are designing alternative UI treatments for the Raidar review-surface wireframe. You are not implementing production code. Create standalone HTML artifacts only.

## Hard constraints

- Do not edit existing wireframe/source files.
- Write only inside this directory.
- Create four standalone HTML artifacts with embedded CSS and static sample data.
- No external network assets or dependencies.
- Use the existing wireframe visual language: black background, thin borders, compact technical typography, muted foreground text, green/orange/red/cyan/accent semantic colors, dense but readable diagnostic UI.
- Treat the screenshot `current-patterns-panel.png` as the current state to improve.
- Use `patterns-data-snapshot.json` as the available data shape/sample.

## Current problem with the Patterns panel

The current control is structurally close but semantically too flat.

It currently identifies repeated signals from scorer criteria, gates, and findings, but it does not clearly tell the reviewer:

1. What frictions are costing them and why.
2. Which strengths are trending up or producing repeatable consistency.
3. Why a raw value like `6 of 6` matters.
4. How the pattern summary connects to revision movement and visible revision state.

The panel should help a reviewer understand consequence, trajectory, and confidence, not just counts.

## Desired conceptual model

Use visible scenario family + visible revisions as the scope.

The control should answer:

- What is hurting the result?
- What changed positively?
- What can I trust as repeatable?

Prefer interpretive states over raw threshold buckets:

- `Costing you`: current/latest visible revision still fails above threshold, or failures spread across multiple agents/revisions.
- `Trending up`: earlier failures improved or cleared in later visible revisions.
- `Repeatable strengths`: consistently passing in current/latest visible revision, especially across multiple agents.
- Optional `Watchlist`: mixed signal below threshold, if useful.

Avoid letting one metric appear as both friction and strength. Classify each scorer criterion into one dominant state.

## Threshold rule

- Scorer criteria: pass/fail count qualifies when it is at least one third of that criterion's sample, using `ceil(sample_size / 3)`.
- Gates/findings: affected runs qualify when they are at least one third of visible runs.
- Threshold is a signal filter, not the final UI message. The UI still needs narrative context.

## Data schema available

From `ExperimentRecord`:

```ts
interface ExperimentRecord {
  scenario: string | null;
  revision: string | null;
  agent_spec: string;
  synthetic: boolean;
  repeats: number | null;
  aggregate: {
    run_count_total?: number;
    run_count_scored?: number;
    unscored_count?: number;
    composite_score?: StatBlock;
    quality_score?: StatBlock;
    duration_sec?: StatBlock;
    uncached_input_tokens?: StatBlock;
    metric_outcomes?: Record<string, {
      pass_count: number;
      fail_count: number;
      sample_size: number;
      pass_rate: number;
      mean_score: number;
    }>;
    scorer_outcomes?: Record<string, {
      sample_size: number;
      mean_score: number;
    }>;
  };
  sample: {
    sample_adequacy?: number;
    minimum_met?: boolean;
    preferred_met?: boolean;
    sample_class?: string;
  };
  findings: Array<{
    id: string;
    kind: 'issue' | 'good' | 'note';
    category: string;
    title: string;
    detail: string;
    evidence: Array<{ source: string; reference: string; detail: string }>;
  }>;
  run_ids: string[];
}
```

From `RunRecord`:

```ts
interface RunRecord {
  id: string;
  scenario: string;
  revision: string;
  agent_spec: string;
  duration_ms: number;
  status: 'OK' | 'ERROR' | 'UNSET';
  total_input_tokens: number;
  total_output_tokens: number;
  composite_score: number | null;
  unscored: boolean;
  valid: boolean;
  synthetic: boolean;
  finding_counts: { issue: number; good: number; note: number };
  issue_categories: Record<string, number>;
  failed_gates: string[];
}
```

From revision diffs:

```ts
interface RevisionDiff {
  scenario: string;
  from_revision: string;
  to_revision: string;
  summary: string[];
}
```

## Styling tokens

```ts
const C = {
  bg: '#000000',
  surface: '#0a0a0a',
  elevated: '#111111',
  border: 'rgba(255,255,255,0.06)',
  borderLight: 'rgba(255,255,255,0.1)',
  fg0: '#5a6a72',
  fg1: '#7d8a90',
  fg2: '#a0acb2',
  fg3: '#c8d5dc',
  fg4: '#e1e8ec',
  fg5: '#f2f5f7',
  accent: '#5B8DEF',
  green: '#60E36D',
  red: '#EB1414',
  orange: '#F0AD4E',
  cyan: '#4FCAE3',
  selected: 'rgba(91,141,239,0.08)',
  selectedBorder: 'rgba(91,141,239,0.2)',
};
```

## Output required

Create exactly these files:

- `variation-1.html`
- `variation-2.html`
- `variation-3.html`
- `variation-4.html`
- `README.md`

Each HTML artifact should contain one different proposed UI for the Patterns control. Each should include:

- A short visible title and subtitle.
- A way to see `Costing you`, `Trending up`, and `Repeatable strengths`.
- At least one representation of why a friction costs the reviewer something.
- At least one representation of why a strength is meaningful beyond `6 of 6`.
- Static sample data derived from `patterns-data-snapshot.json`.
- Hover-like detail can be represented as always-visible side notes or simulated overlay cards, since this is standalone HTML.

In `README.md`, briefly explain the design intent and tradeoffs of each variation.
