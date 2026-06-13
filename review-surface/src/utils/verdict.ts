// Semantic translation layer: raw scores, samples, and finding categories
// rendered as plain-language verdicts with one consistent tier vocabulary.
import { C } from '@/utils/colors';
import type { ExperimentRecord, RunRecord, StatBlock } from '@/utils/types';

export interface Tier {
  label: string;
  color: string;
  blurb: string;
}

/** Composite-score verdict tiers shared by every score rendering. */
export function scoreTier(score: number | null | undefined): Tier {
  if (score == null) return { label: 'Unscored', color: C.fg1, blurb: 'No score recorded — run needs a rerun' };
  if (score >= 0.9) return { label: 'Strong', color: C.green, blurb: 'Delivered to spec' };
  if (score >= 0.75) return { label: 'Solid', color: C.cyan, blurb: 'Delivered with minor gaps' };
  if (score >= 0.5) return { label: 'Shaky', color: C.orange, blurb: 'Delivered with significant gaps' };
  return { label: 'Failing', color: C.red, blurb: 'Did not deliver' };
}

/**
 * Spread-of-results verdict from a stat block's stddev. The boundary matches
 * Raidar's REPEAT_VARIANCE_STDDEV_THRESHOLD (0.1) — at or above it Raidar
 * raises a repeat-variance finding, so "Volatile" here means "Raidar would
 * flag this for inconsistency".
 */
const REPEAT_VARIANCE_STDDEV_THRESHOLD = 0.1;

export function spreadTier(stat: StatBlock | undefined): Tier | null {
  if (stat?.stddev == null || stat.mean == null) return null;
  if (stat.stddev < REPEAT_VARIANCE_STDDEV_THRESHOLD) {
    return { label: 'Consistent', color: C.fg2, blurb: `Repeats stay within ±${REPEAT_VARIANCE_STDDEV_THRESHOLD} — no variance flag` };
  }
  return { label: 'Volatile', color: C.orange, blurb: 'Repeats disagree enough for Raidar to flag variance — inspect the outlier runs' };
}

/** Sample-trust verdict from adequacy flags. */
export function sampleTrust(sample: ExperimentRecord['sample'], scored: number, total: number): Tier {
  if (sample.minimum_met === false) {
    return { label: 'Low confidence', color: C.orange, blurb: `Below the minimum sample (${scored}/${total} scored) — treat results as directional` };
  }
  if (sample.preferred_met) {
    return { label: 'High confidence', color: C.green, blurb: `Preferred sample size met (${scored}/${total} scored)` };
  }
  return { label: 'Fair confidence', color: C.fg2, blurb: `Minimum sample met (${scored}/${total} scored), below preferred` };
}

/** Plain-language names for Raidar finding categories (infra ids stay in tooltips). */
export const CATEGORY_INFO: Record<string, { label: string; hint: string }> = {
  'failed-gate': { label: 'Verification gate failed', hint: 'A required check (tests, lint, …) did not pass' },
  'missing-required-command': { label: 'Required step never ran', hint: 'The scenario expects a verification command the agent never executed' },
  'requirements-gap': { label: 'Requirement not satisfied', hint: 'One or more scenario requirements were not met' },
  'requirements-satisfied': { label: 'All requirements satisfied', hint: 'Every scenario requirement was met' },
  'missing-artifact': { label: 'Evidence file missing', hint: 'A declared evidence artifact is absent or unusable' },
  'retained-evidence': { label: 'Evidence retained', hint: 'Declared evidence artifacts were kept and usable' },
  'deterministic-cap': { label: 'Score capped by failed checks', hint: 'Deterministic prerequisites failed, capping this metric' },
  'judge-review': { label: 'Judge verdict needs review', hint: 'An LLM-judge score disagrees with deterministic evidence' },
  'completion-claim': { label: 'Claimed done without proof', hint: 'The agent reported success that evidence does not support' },
  'performance-gate': { label: 'Performance threshold missed', hint: 'The run breached a performance limit' },
  'workflow-anomaly': { label: 'Unusual workflow', hint: 'The delivery process deviated from the expected pattern' },
  'resource-outlier': { label: 'Unusual resource use', hint: 'Tokens or duration far from this scenario’s norm' },
  'repeat-variance': { label: 'Inconsistent across repeats', hint: 'Repeat runs disagree more than expected' },
  'unscored-run': { label: 'Run could not be scored', hint: 'Scoring failed — the run needs a rerun' },
  'rerun-target': { label: 'Rerun required', hint: 'This experiment has unresolved unscored runs' },
  'sample-adequacy': { label: 'Sample too small', hint: 'Not enough scored runs to trust the aggregate' },
  'clean-verification': { label: 'Verification clean first try', hint: 'All gates passed without retries' },
};

export function categoryLabel(category: string): string {
  return CATEGORY_INFO[category]?.label ?? humanize(category);
}

export function categoryHint(category: string): string {
  return CATEGORY_INFO[category]?.hint ?? category;
}

/** kebab-or-snake id → sentence-case words, e.g. defect-evidence-completeness → Defect evidence completeness. */
export function humanize(id: string): string {
  const words = id.replace(/[@:].*$/, '').split(/[-_]/).filter(Boolean).join(' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Scorer id like "resource-efficiency@1" → "Resource efficiency". Version stays in tooltips. */
export function scorerName(id: string): string {
  return humanize(id);
}

/**
 * Concise run label: trailing alphanumeric chunk(s) of the id, e.g.
 * "synthetic-low-04" → "Run 04", "r017" → "Run r017". Full id belongs in a tooltip.
 */
export function runLabel(id: string): string {
  const tail = id.match(/(\d+)$/);
  if (tail) return `Run ${tail[1]}`;
  const seg = id.split(/[-_]/).pop() ?? id;
  return `Run ${seg}`;
}

/** One plain-language sentence describing a run's outcome. */
export function runSummary(run: RunRecord): string {
  const tier = scoreTier(run.unscored ? null : run.composite_score);
  const parts: string[] = [];
  if (run.unscored) {
    parts.push('This run was not scored');
  } else {
    parts.push(`${tier.blurb} — composite ${run.composite_score?.toFixed(2)}`);
  }
  if (run.failed_gates.length > 0) {
    parts.push(`${run.failed_gates.length === 1 ? 'gate' : 'gates'} ${run.failed_gates.join(' and ')} failed`);
  }
  const issues = run.finding_counts.issue;
  if (issues > 0) parts.push(`${issues} issue${issues === 1 ? '' : 's'} recorded`);
  else if (!run.unscored) parts.push('no issues recorded');
  if (!run.valid) parts.push('validity checks failed');
  return parts.join(' · ');
}
