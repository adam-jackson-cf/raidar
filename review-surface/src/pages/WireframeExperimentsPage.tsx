import { Fragment, useEffect, useMemo, useState, type FocusEvent, type MouseEvent, type ReactNode, type WheelEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronRight, Eye, EyeOff, Info, Pin, PinOff, Trophy } from 'lucide-react';
import { Link } from 'react-router-dom';
import { WireframePatternsMap } from './wireframe-components/WireframePatternsMap';
import { KIND_STYLES } from '@/components/AnnotationChip';
import { EvidenceRefList } from '@/components/AnnotationCards';
import { WireframeTradeoffScatter } from './wireframe-components/WireframeTradeoffScatter';
import { ArmDiffCard } from './wireframe-components/WireframeArmMovement';
import { compactSpec } from './wireframe-components/wireframeLabels';
import { api } from '@/api/client';
import { C } from '@/presentation/colors';
import { fmtPercent, fmtScore } from '@/presentation/format';
import { humanize, runLabel, scoreTier } from '@/presentation/verdict';
import type { ExperimentRecord, RunRecord, StatBlock } from '@/api/types';

type CompositeStat = StatBlock | undefined;

type HoverPayload = {
  id?: string;
  x: number;
  y: number;
  title: string;
  lines: string[];
};

type FindingEvent = { clientX?: number; clientY?: number; currentTarget: EventTarget & Element };

type MetricOutcome = {
  pass_rate: number;
  mean_score: number;
  sample_size: number;
  pass_count: number;
  fail_count: number;
};

function WireframeRunPill({ run, id }: { run: RunRecord | undefined; id: string }) {
  const failed = run?.status === 'ERROR';
  const tier = scoreTier(run?.unscored ? null : (run?.composite_score ?? null));
  return (
    <Link
      to={`/runs/${encodeURIComponent(id)}`}
      className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] transition "
      style={{
        color: failed ? C.red : C.fg3,
        border: `1px solid ${failed ? 'rgba(235,20,20,0.35)' : C.border}`,
      }}
    >
      <span className="inline-block size-1.5 rounded-full" style={{ background: failed ? C.red : tier.color }} />
      {runLabel(id)}
      {run?.composite_score != null ? (
        <span className="num" style={{ color: tier.color }}>
          {run.composite_score.toFixed(2)}
        </span>
      ) : (
        <span className="text-[9px]" style={{ color: C.fg0 }}>
          unscored
        </span>
      )}
    </Link>
  );
}

function WireframeMetricOutcomeRow({
  metric,
  outcome,
}: {
  metric: string;
  outcome: MetricOutcome;
}) {
  const failing = outcome.pass_rate < 1;
  const color = failing ? C.red : C.green;
  return (
    <div
      className="flex items-center gap-2 rounded px-1.5 py-1 text-[10px]"
      title={`${metric} · pass rate ${fmtPercent(outcome.pass_rate)} · mean score ${fmtScore(outcome.mean_score)} · ${outcome.sample_size} samples`}
    >
      <span className="w-3 shrink-0 text-center text-[10px] font-bold" style={{ color }}>
        {failing ? '✗' : '✓'}
      </span>
      <span className="min-w-0 flex-1 truncate" style={{ color: C.fg3 }}>
        {humanize(metric)}
      </span>
      <span className="num w-20 shrink-0 text-right" style={{ color }}>
        {failing ? `${outcome.pass_count}/${outcome.sample_size} pass` : `${outcome.sample_size}/${outcome.sample_size} pass`}
      </span>
      <span className="w-20 shrink-0 text-right" style={{ color: C.fg1 }}>
        {fmtScore(outcome.mean_score)}
      </span>
    </div>
  );
}

function metricCellColor(passed: boolean | null) {
  if (passed === true) return C.green;
  if (passed === false) return C.red;
  return C.fg1;
}

function metricCellSymbol(passed: boolean | null) {
  if (passed === true) return '✓';
  if (passed === false) return '×';
  return '·';
}

function shortCriterion(metric: string) {
  const known: Record<string, string> = {
    'requirements-adherence': 'Req adhere',
    'requirements-coverage': 'Req cover',
    'verification-stability': 'Verify',
    'code-quality': 'Code',
    'resource-efficiency': 'Resource',
    'artifact-checks': 'Artifacts',
    functional: 'Functional',
    'test-coverage': 'Tests',
    'change-containment': 'Contain',
    'defect-evidence-completeness': 'Evidence',
    'defect-resolution': 'Resolve',
    'regression-protection': 'Regress',
  };
  return known[metric] ?? humanize(metric).split(/\s+/).map((part) => part.slice(0, 4)).join(' ');
}

function criterionOverlay(metric: string, outcome: MetricOutcome): Pick<HoverPayload, 'title' | 'lines'> {
  return {
    title: humanize(metric),
    lines: [
      'Scorer: metric outcomes',
      `Scorer field: aggregate.metric_outcomes["${metric}"]`,
      `Criterion label: ${humanize(metric)}`,
      `${outcome.pass_count} pass / ${outcome.fail_count} fail from ${outcome.sample_size} scored checks in this agent arm.`,
      `Mean score: ${fmtScore(outcome.mean_score)}`,
    ],
  };
}

function WireframeRunStrip({
  runIds,
  runsById,
}: {
  runIds: string[];
  runsById: Map<string, RunRecord>;
}) {
  if (runIds.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      {runIds.map((id) => (
        <WireframeRunPill key={id} run={runsById.get(id)} id={id} />
      ))}
    </div>
  );
}

function WireframeExperimentExpansion({
  exp,
  onHover,
  onClose,
  onPin,
}: {
  exp: ExperimentRecord;
  onHover: (payload: HoverPayload | null) => void;
  onClose: () => void;
  onPin: (payload: HoverPayload) => void;
}) {
  const runs = useQuery({ queryKey: ['runs'], queryFn: api.runs });
  const runsById = useMemo(() => new Map((runs.data ?? []).map((run) => [run.id, run])), [runs.data]);
  const metricOutcomes = Object.entries(exp.aggregate.metric_outcomes ?? {}) as Array<[string, MetricOutcome]>;
  const failing = metricOutcomes.filter(([, o]) => o.pass_rate < 1).sort(([, a], [, b]) => a.pass_rate - b.pass_rate);
  const passing = metricOutcomes.filter(([, o]) => o.pass_rate >= 1);

  return (
    <div
      className="flex flex-col gap-3 border-y px-3 py-2.5"
      style={{
        borderColor: 'rgba(114, 176, 222, 0.14)',
        background: 'linear-gradient(180deg, rgba(31, 72, 96, 0.22), rgba(18, 43, 59, 0.18))',
      }}
    >
      <WireframeRunStrip runIds={exp.run_ids} runsById={runsById} />

      {metricOutcomes.length > 0 && (
        <div className="grid gap-3 lg:grid-cols-2">
          <div>
            <div
              className="mb-1 text-[10px] font-medium uppercase tracking-wide"
              style={{ color: failing.length > 0 ? C.orange : C.fg1 }}
            >
              {failing.length > 0 ? `Costing you (${failing.length})` : 'Costing you'}
            </div>
            {failing.length === 0 ? (
              <span className="text-[11px]" style={{ color: C.fg1 }}>
                Nothing — every check passed in every scored run.
              </span>
            ) : (
              <div className="flex max-w-md flex-col">
                {failing.map(([metric, o]) => (
                  <WireframeMetricOutcomeRow key={metric} metric={metric} outcome={o} />
                ))}
              </div>
            )}
          </div>
          <div>
            <div className="mb-1 text-[10px] font-medium uppercase tracking-wide" style={{ color: C.fg1 }}>
              Strengths ({passing.length})
            </div>
            <div className="flex max-w-md flex-col">
              {passing.map(([metric, o]) => (
                <WireframeMetricOutcomeRow key={metric} metric={metric} outcome={o} />
              ))}
            </div>
          </div>
        </div>
      )}

      {exp.findings.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide" style={{ color: C.fg1 }}>
            Findings
          </div>
          <div className="flex flex-col gap-1.5">
            {exp.findings.map((finding) => {
              const style = KIND_STYLES[finding.kind];
              return (
                <div
                  key={finding.id}
                  className="rounded-lg px-2.5 py-2"
                  style={{
                    border: `1px solid ${style.border}`,
                    background: `linear-gradient(180deg, ${C.subtleStrong}, ${C.subtle}), ${style.bg}`,
                  }}
                >
                  <div className="mb-0.5 flex flex-wrap items-center gap-2">
                    <span
                      className="inline-flex h-[18px] items-center gap-1 rounded-full px-1.5 text-[10px] font-medium leading-[18px]"
                      style={{ color: style.fg, background: style.bg, border: `1px solid ${style.border}` }}
                    >
                      <span className="font-bold">{style.icon}</span>
                      {style.label}
                    </span>
                    {finding.category && (
                      <span className="text-[10px] font-medium" style={{ color: C.fg1 }} title={finding.category}>
                        {finding.category}
                      </span>
                    )}
                    <span className="text-xs font-medium" style={{ color: C.fg4 }}>
                      {finding.title}
                    </span>
                  </div>
                  {finding.detail && (
                    <div className="text-xs leading-relaxed" style={{ color: C.fg3 }}>
                      {finding.detail}
                    </div>
                  )}
                  <EvidenceRefList evidence={finding.evidence} />
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function findabilityScore(statMean: number | null | undefined, score?: number | null) {
  return score ?? statMean ?? null;
}

function asNormalizedScore(value: number | null | undefined) {
  if (value == null) return null;
  return Math.max(0, Math.min(1, value));
}

function deliveryRingColor(score: number | null | undefined): string {
  return scoreTier(score).color;
}

function repeatabilityValue(stddev: number | null | undefined): number | null {
  if (stddev == null) return null;
  return asNormalizedScore(1 - stddev);
}

function repeatabilityRingColor(stddev: number | null | undefined): string {
  if (stddev == null) return C.fg2;
  if (stddev <= 0.08) return C.green;
  if (stddev <= 0.16) return C.orange;
  return C.red;
}

function confidenceScore(scored: number, total: number): number {
  if (total <= 0) return 0;
  return asNormalizedScore(scored / total) ?? 0;
}

function confidenceRingColor(sample: ExperimentRecord['sample']): string {
  if (!sample.minimum_met) return C.red;
  if (sample.preferred_met) return C.green;
  return C.orange;
}

function runCoverageColor(validRuns: number, totalRuns: number): string {
  if (totalRuns <= 0) return C.fg2;
  const ratio = validRuns / totalRuns;
  if (ratio >= 0.9) return C.green;
  if (ratio >= 0.7) return C.orange;
  return C.red;
}

const COLUMN_HELPERS: Record<string, string[]> = {
  'Agent spec': [
    'Identifies the harness and model used by this experiment run in compact form.',
    'Use this for quick model/agent comparison within a problem family.',
  ],
  Outcome: [
    'Primary objective score for the run, represented as mean outcome quality.',
    'Higher scores mean stronger overall performance against the problem target.',
  ],
  Stability: [
    'Outcome consistency across runs after repeatability processing.',
    'Higher stability means outcomes are less volatile and more repeatable.',
  ],
  Trust: [
    'How confidently the score can be trusted from available evidence.',
    'Derived from scored runs vs total runs and whether sample thresholds are met.',
  ],
  Runtime: [
    'Runtime movement for this arm row compared with the previous arm of the same agent.',
    'Lower runtime is better, so downward movement is green and upward movement is red.',
  ],
  Input: [
    'Input token movement for this arm row compared with the previous arm of the same agent.',
    'Lower input token use is better, so downward movement is green and upward movement is red.',
  ],
  Output: [
    'Output token movement for this arm row compared with the previous arm of the same agent.',
    'Lower output token use is better, so downward movement is green and upward movement is red.',
  ],
  Run: [
    'How much of the recorded run set completed successfully.',
    'Higher fill means stronger signal strength for this experiment.',
  ],
  Findings: [
    'Grouped runtime findings collected from the problem checks.',
    'Use it to open issue context and review supporting evidence quickly.',
  ],
};

function HeaderInfoCell({
  label,
  className,
  children,
  onOpen,
  onClose,
}: {
  label: string;
  className: string;
  children: ReactNode;
  onOpen: (payload: HoverPayload | null) => void;
  onClose: () => void;
}) {
  return (
    <th
      className={className}
      style={{ color: C.fg0 }}
      onMouseEnter={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        onOpen({
          x: rect.left,
          y: rect.bottom + 4,
          title: label,
          lines: COLUMN_HELPERS[label] ?? ['Column details'],
        });
      }}
      onMouseLeave={onClose}
    >
      <span className="inline-flex cursor-help items-center gap-1">{children}</span>
    </th>
  );
}

function ScoreRing({
  id,
  score,
  title,
  ringColor,
  tooltip,
  hover,
  onMove,
  onPin,
}: {
  id: string;
  score: number | null;
  title: string;
  ringColor: string;
  tooltip: string[];
  hover: (payload: HoverPayload | null) => void;
  onMove: (payload: HoverPayload) => void;
  onPin: (payload: HoverPayload) => void;
}) {
  const value = score == null ? 0 : Math.max(0, Math.min(1, score));
  const size = 28;
  const stroke = 3.5;
  const radius = (size - stroke) / 2;
  const circumference = Math.PI * 2 * radius;
  const dash = value * circumference;
  const neutral = C.ringTrack;

  return (
    <button
      type="button"
      className="inline-flex size-7 items-center justify-center rounded-full transition focus-visible:outline-none focus-visible:ring-2 "
      style={{ color: ringColor }}
      onMouseEnter={(event) => {
        onMove({
          id,
          x: event.clientX,
          y: event.clientY,
          title,
          lines: tooltip,
        });
      }}
      onMouseMove={(event) => {
        onMove({
          id,
          x: event.clientX,
          y: event.clientY,
          title,
          lines: tooltip,
        });
      }}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        onPin({
          id,
          x: event.clientX,
          y: event.clientY,
          title,
          lines: tooltip,
        });
      }}
      onFocus={(event) => {
        const rect = (event.target as HTMLElement).getBoundingClientRect();
        onMove({
          id,
          x: rect.left + rect.width / 2,
          y: rect.top,
          title,
          lines: tooltip,
        });
      }}
      onBlur={() => hover(null)}
      onMouseLeave={() => hover(null)}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={size / 2} cy={size / 2} r={radius} strokeWidth={stroke} fill="none" stroke={neutral} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={ringColor}
          strokeWidth={stroke}
          strokeDasharray={`${dash} ${Math.max(0, circumference - dash)}`}
          strokeDashoffset={circumference * 0.25}
          strokeLinecap="round"
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <span className="sr-only">{title}</span>
    </button>
  );
}

function statString(stat: CompositeStat, fallback = '—') {
  if (!stat?.mean) return fallback;
  const parts = [`mean ${stat.mean.toFixed(3)}`];
  if (stat.stddev != null) parts.push(`±${stat.stddev.toFixed(3)}`);
  if (stat.median != null) parts.push(`median ${stat.median.toFixed(3)}`);
  return parts.join(' · ');
}

function OverlayFrame({
  x,
  y,
  title,
  pinned,
  children,
  onPin,
  onClose,
  nav,
}: {
  x: number;
  y: number;
  title?: string;
  pinned: boolean;
  children: ReactNode;
  onPin: () => void;
  onClose: () => void;
  nav?: ReactNode;
}) {
  return (
    <div
      className="fixed z-30 min-w-60 max-w-72 rounded-md border p-2.5 text-[11px]"
      style={{
        left: Math.min(x + 14, window.innerWidth - 300),
        top: Math.min(y + 14, window.innerHeight - 180),
        borderColor: C.selectedBorder,
        background: C.surface,
        color: C.fg3,
      }}
    >
      <div className="mb-2 flex items-start justify-between gap-2">
        {title && (
          <div className="min-w-0 truncate text-[11px]" style={{ color: C.fg4 }}>
            {title}
          </div>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          {nav}
          <button
            type="button"
            aria-label={pinned ? 'Unpin overlay' : 'Pin overlay'}
            className="inline-flex size-5 items-center justify-center rounded border text-[10px] leading-none"
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              onPin();
            }}
            title="[P]"
          >
            {pinned ? <Pin size={11} /> : <PinOff size={11} />}
          </button>
          <button
            type="button"
            aria-label="Close overlay"
            className="inline-flex size-5 items-center justify-center rounded border text-[11px] font-medium leading-none"
            onClick={(event) => {
              event.stopPropagation();
              onClose();
            }}
          >
            ×
          </button>
        </div>
      </div>
      {children}
    </div>
  );
}

const FINDING_KIND = {
  issue: {
    label: 'Issue',
    symbol: '!',
    color: C.red,
    border: C.red,
    bg: 'rgba(248, 113, 113, 0.18)',
  },
  good: {
    label: 'Good',
    symbol: '✓',
    color: C.green,
    border: C.green,
    bg: 'rgba(34, 197, 94, 0.18)',
  },
  note: {
    label: 'Note',
    symbol: 'i',
    color: C.cyan,
    border: C.cyan,
    bg: 'rgba(56, 189, 248, 0.16)',
  },
} as const;

type FindingKind = keyof typeof FINDING_KIND;
type FindingPanelState = {
  rowKey: string;
  kind: FindingKind;
  findings: ExperimentRecord['findings'];
  activeIndex: number;
  x: number;
  y: number;
  pinned: boolean;
};

const FINDING_SEVERITY: Record<string, number> = {
  'failed-gate': 100,
  'missing-required-command': 96,
  'requirements-gap': 92,
  'sample-adequacy': 86,
  'rerun-target': 82,
  'unscored-run': 80,
  'performance-gate': 75,
  'workflow-anomaly': 72,
  'repeat-variance': 70,
  'resource-outlier': 58,
  'judge-review': 54,
  'requirements-satisfied': 40,
};

function findingSeverity(category = '') {
  return FINDING_SEVERITY[category] ?? 10;
}

function sortFindingsByInterest(a: ExperimentRecord['findings'][number], b: ExperimentRecord['findings'][number]) {
  const kindOrder: Record<string, number> = { issue: 3, good: 2, note: 1 };
  const order = (kindOrder[b.kind] ?? 0) - (kindOrder[a.kind] ?? 0);
  if (order !== 0) return order;
  const sev = findingSeverity(b.category) - findingSeverity(a.category);
  if (sev !== 0) return sev;
  return a.category.localeCompare(b.category);
}

function findingTooltipLines(item: ExperimentRecord['findings'][number]) {
  const lines = [`kind: ${FINDING_KIND[item.kind].label}`, `category: ${item.category}`, item.title, item.detail];
  for (const evidence of item.evidence) {
    const detail = evidence.detail ? ` — ${evidence.detail}` : '';
    lines.push(`${evidence.source}:${evidence.reference}${detail}`);
  }
  return lines;
}

function findingEvidenceLines(item: ExperimentRecord['findings'][number]) {
  const lines = [`kind: ${FINDING_KIND[item.kind].label}`, `category: ${item.category}`];
  for (const evidence of item.evidence) {
    const detail = evidence.detail ? ` — ${evidence.detail}` : '';
    lines.push(`${evidence.source}:${evidence.reference}${detail}`);
  }
  return lines;
}

function eventCoords(event: FindingEvent) {
  if (typeof event.clientX === 'number' && typeof event.clientY === 'number') {
    return { x: event.clientX, y: event.clientY };
  }
  const rect = event.currentTarget.getBoundingClientRect();
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height };
}

function findingPanelKey(experimentKey: string, kind: FindingKind) {
  return `${experimentKey}:${kind}`;
}

type ArmFilterMap = Record<string, string[]>;

function formatFamilyMenuLabel(allSelected: boolean, families: string[]) {
  if (allSelected) return 'all problems';
  if (families.length === 1) return families[0];
  if (families.length > 0) return `${families.length} problems`;
  return 'all problems';
}

function formatArmMenuLabel(allSelected: boolean, arms: string[], hasHidden: boolean) {
  if (allSelected) return 'all arms';
  if (arms.length === 0) return 'no arms';
  if (!hasHidden) return `arm ${arms[0]}`;
  if (arms.length === 1) return `arm ${arms[0]}`;
  return `${arms.length} arms`;
}

function findMissingArmLabel(arms: string[], selected: string[]) {
  return arms.some((arm) => !selected.includes(arm));
}

function experimentRowKey(exp: ExperimentRecord) {
  return `${exp.experiment_id}|${compactSpec(exp.agent_spec)}|${exp.arm ?? ''}`;
}

function armSortValue(arm?: string | null) {
  if (!arm) return 0;
  const match = arm.match(/\d+/g);
  if (!match?.length) return 0;
  return Number(match.join('')) || 0;
}

function topDeliveryScore(experiments: ExperimentRecord[]) {
  let best = -1;

  for (const exp of experiments) {
    const score = findabilityScore(exp.aggregate.composite_score?.mean, exp.aggregate.quality_score?.mean);
    if (score == null) continue;
    if (score > best) {
      best = score;
    }
  }

  return best;
}


function patternDemoOutcome(pass: number, fail: number): MetricOutcome {
  const sample = pass + fail;
  return {
    pass_rate: sample > 0 ? pass / sample : 0,
    mean_score: sample > 0 ? pass / sample : 0,
    sample_size: sample,
    pass_count: pass,
    fail_count: fail,
  };
}

function withPatternDemoData(experiments: ExperimentRecord[]) {
  const targetFamily = 'skill-benchmark-coding-test';
  const hasTarget = experiments.some((exp) => exp.synthetic && exp.problem === targetFamily);
  if (!hasTarget) return experiments;

  const patternByArm: Record<string, Record<string, MetricOutcome>> = {
    v001: {
      'code-quality': patternDemoOutcome(1, 2),
      functional: patternDemoOutcome(3, 0),
      'resource-efficiency': patternDemoOutcome(3, 0),
    },
    v002: {
      'code-quality': patternDemoOutcome(1, 2),
      functional: patternDemoOutcome(3, 0),
      'resource-efficiency': patternDemoOutcome(2, 1),
    },
    v003: {
      'code-quality': patternDemoOutcome(3, 0),
      functional: patternDemoOutcome(3, 0),
      'resource-efficiency': patternDemoOutcome(1, 2),
    },
  };

  return experiments.map((exp) => {
    const overrides = exp.synthetic && exp.problem === targetFamily && exp.arm ? patternByArm[exp.arm] : undefined;
    if (!overrides) return exp;
    return {
      ...exp,
      aggregate: {
        ...exp.aggregate,
        metric_outcomes: {
          ...(exp.aggregate.metric_outcomes ?? {}),
          ...overrides,
        },
      },
    };
  });
}

function compareExperimentRank(left: number[], right: number[]) {
  for (let i = 0; i < Math.max(left.length, right.length); i += 1) {
    const lhs = left[i] ?? 0;
    const rhs = right[i] ?? 0;
    if (lhs === rhs) continue;
    return lhs > rhs ? -1 : 1;
  }
  return 0;
}

function experimentRankKey(exp: ExperimentRecord) {
  const composite = findabilityScore(exp.aggregate.composite_score?.mean, exp.aggregate.quality_score?.mean);
  const repeatScore = repeatabilityValue(exp.aggregate.composite_score?.stddev ?? null);
  const scoredRuns = exp.aggregate.run_count_scored ?? 0;
  const totalRuns = exp.aggregate.run_count_total ?? 0;
  const sampleScore = confidenceScore(scoredRuns, totalRuns);

  return [
    composite == null ? -1 : Math.max(0, Math.min(1, composite)),
    repeatScore == null ? -1 : repeatScore,
    sampleScore == null ? -1 : sampleScore,
    scoredRuns,
    totalRuns,
    armSortValue(exp.arm),
  ];
}

function isKnownFindingKind(value: string): value is FindingKind {
  return value === 'issue' || value === 'good' || value === 'note';
}

function clusterFindings(findings: ExperimentRecord['findings']) {
  const orderedKinds: FindingKind[] = ['issue', 'good', 'note'];
  const sorted = [...findings].sort(sortFindingsByInterest);
  const buckets = new Map<FindingKind, ExperimentRecord['findings']>();

  for (const finding of sorted) {
    if (!isKnownFindingKind(finding.kind)) continue;
    const list = buckets.get(finding.kind) ?? [];
    list.push(finding);
    buckets.set(finding.kind, list);
  }

  return orderedKinds
    .map((kind) => {
      const bucket = buckets.get(kind);
      if (!bucket?.length) return null;
      return { kind, findings: bucket };
    })
    .filter(Boolean) as Array<{ kind: FindingKind; findings: ExperimentRecord['findings'] }>;
}

function FindingClusterBadge({
  kind,
  findings,
  onOpen,
  onClose,
  onPin,
}: {
  kind: FindingKind;
  findings: ExperimentRecord['findings'];
  onOpen: (event: FindingEvent, findings: ExperimentRecord['findings'], kind: FindingKind) => void;
  onClose: () => void;
  onPin: (event: FindingEvent, findings: ExperimentRecord['findings'], kind: FindingKind) => void;
}) {
  const config = FINDING_KIND[kind];
  const title = config.label;

  return (
    <button
      type="button"
      aria-label={`${config.label} findings (${findings.length})`}
      className="relative inline-flex size-4 rotate-45 items-center justify-center rounded-none border text-[9px] font-bold transition focus-visible:outline-none focus-visible:ring-2 "
      style={{ color: config.color, border: `1px solid ${config.border}`, background: config.bg, opacity: 0.7 }}
      onMouseEnter={(event) => onOpen(event, findings, kind)}
      onMouseMove={(event) => onOpen(event, findings, kind)}
      onMouseLeave={onClose}
      onMouseDown={(event) => {
        event.preventDefault();
        event.stopPropagation();
        onPin(event, findings, kind);
      }}
      onKeyDown={(event) => {
        if (event.key === 'p' || event.key === 'P') {
          event.preventDefault();
          onPin(event, findings, kind);
        }
      }}
    >
      <span className="-rotate-45 transform">{config.symbol}</span>
      <span className="sr-only">{`${title} findings ${findings.length}`}</span>
    </button>
  );
}

export function WireframeExperimentsPage() {
  const query = useQuery({ queryKey: ['experiments'], queryFn: api.experiments });
  const runsQuery = useQuery({ queryKey: ['runs'], queryFn: api.runs });
  const [problemFilter, setProblemFilter] = useState('');
  const [selectedFamilies, setSelectedFamilies] = useState<string[]>(['__all_problems__']);
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [openFamilyMenu, setOpenFamilyMenu] = useState(false);
  const [openArmMenu, setOpenArmMenu] = useState<string | null>(null);
  const [tooltip, setTooltip] = useState<HoverPayload | null>(null);
  const [columnTooltip, setColumnTooltip] = useState<HoverPayload | null>(null);
  const [pinnedTooltips, setPinnedTooltips] = useState<HoverPayload[]>([]);
  const [findingPanel, setFindingPanel] = useState<FindingPanelState | null>(null);
  const [pinnedFindingPanels, setPinnedFindingPanels] = useState<FindingPanelState[]>([]);
  const [selectedArms, setSelectedArms] = useState<ArmFilterMap>({});
  const [problemSortMode, setProblemSortMode] = useState<'most_recent' | 'arm_improvement'>('most_recent');
  const [armSortModes, setArmSortModes] = useState<Record<string, 'outcome' | 'agent' | 'arm'>>({});

  useEffect(() => {
    if (!findingPanel?.pinned) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (!findingPanel) return;
      if (event.key === 'Escape') {
        setFindingPanel((current) => (current?.pinned ? null : current));
        return;
      }
      if ((event.key === 'p' || event.key === 'P') && findingPanel.pinned) {
        setFindingPanel((current) => (current?.pinned ? { ...current, pinned: false } : current));
        return;
      }
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') {
        return;
      }
      if (findingPanel.findings.length <= 1) return;
      event.preventDefault();
      setFindingPanel((current) => {
        if (!current?.pinned || !current) return current;
        const delta = event.key === 'ArrowRight' ? 1 : -1;
        const nextIndex = (current.activeIndex + delta + current.findings.length) % current.findings.length;
        return { ...current, activeIndex: nextIndex };
      });
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [findingPanel]);

  const openFindingPanel = (
    event: FindingEvent,
    experimentKey: string,
    findings: ExperimentRecord['findings'],
    kind: FindingKind,
    options: { index?: number } = {}
  ) => {
    if (pinnedFindingPanels.some((panel) => panel.rowKey === experimentKey && panel.kind === kind)) {
      return;
    }
    const coords = eventCoords(event);
    setFindingPanel((current) => ({
      rowKey: experimentKey,
      kind,
      findings,
      activeIndex: Math.max(
        0,
        Math.min(
          options.index ??
            (current?.pinned && current.rowKey === experimentKey && current.kind === kind ? current.activeIndex : 0),
          findings.length - 1
        )
      ),
      x: coords.x,
      y: coords.y,
      pinned: false,
    }));
  };

  const closeFindingPanel = (experimentKey: string, kind?: FindingKind) => {
    setFindingPanel((current) => {
      if (current?.rowKey === experimentKey && (kind == null || current.kind === kind)) {
        return null;
      }
      return current;
    });
  };

  const closeFindingPanelForced = () => setFindingPanel(null);

  const openPinnedFindingPanel = (
    event: FindingEvent,
    experimentKey: string,
    findings: ExperimentRecord['findings'],
    kind: FindingKind,
  ) => {
    const coords = eventCoords(event);
    const index = 0;
    setTooltip(null);
    setFindingPanel(null);
    setPinnedFindingPanels((current) => {
      const key = findingPanelKey(experimentKey, kind);
      if (current.some((panel) => findingPanelKey(panel.rowKey, panel.kind) === key)) {
        return current.filter((panel) => findingPanelKey(panel.rowKey, panel.kind) !== key);
      }
      return [
        ...current,
        {
        rowKey: experimentKey,
        kind,
        findings,
        activeIndex: index,
        ...coords,
        pinned: true,
        },
      ];
    });
  };

  const moveFindingPanel = (delta: number) => {
    setFindingPanel((current) => {
      if (!current || current.findings.length <= 1) return current;
      const nextIndex = (current.activeIndex + delta + current.findings.length) % current.findings.length;
      return { ...current, activeIndex: nextIndex };
    });
  };

  const movePinnedFindingPanel = (experimentKey: string, kind: FindingKind, delta: number) => {
    setPinnedFindingPanels((current) =>
      current.map((panel) => {
        if (panel.rowKey !== experimentKey || panel.kind !== kind || panel.findings.length <= 1) return panel;
        const nextIndex = (panel.activeIndex + delta + panel.findings.length) % panel.findings.length;
        return { ...panel, activeIndex: nextIndex };
      })
    );
  };

  const closePinnedFindingPanel = (experimentKey: string, kind: FindingKind) => {
    setPinnedFindingPanels((current) =>
      current.filter((panel) => panel.rowKey !== experimentKey || panel.kind !== kind)
    );
  };

  const pinTooltip = (payload: HoverPayload) => {
    if (!payload.id) return;
    setPinnedTooltips((current) => {
      const next = current.filter((item) => item.id !== payload.id);
      return [...next, payload];
    });
  };

  const closePinnedTooltip = (id: string | undefined) => {
    if (!id) return;
    setPinnedTooltips((current) => current.filter((item) => item.id !== id));
  };

  const experiments = useMemo(() => withPatternDemoData(query.data?.experiments ?? []), [query.data]);

  const families = useMemo(() => {
    const groups = new Map<string, Map<string, ExperimentRecord[]>>();
    for (const exp of experiments) {
      const family = exp.problem ?? 'unknown';
      const arm = exp.arm ?? 'unknown';
      const arms = groups.get(family) ?? new Map<string, ExperimentRecord[]>();
      const list = arms.get(arm) ?? [];
      list.push(exp);
      arms.set(arm, list);
      groups.set(family, arms);
    }
    return [...groups.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([family, armMap]) => ({
        family,
        arms: [...armMap.entries()]
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([arm, exps]) => ({ arm, exps: exps.sort((a, b) => (b.arm ?? '').localeCompare(a.arm ?? '')) })),
      }));
  }, [experiments]);

  const getLatestArm = (arms: Array<{ arm: string }>) => {
    if (arms.length === 0) return 'unknown';
    const sorted = [...arms].sort((a, b) => a.arm.localeCompare(b.arm));
    return sorted.at(-1)?.arm ?? 'unknown';
  };

  useEffect(() => {
    setSelectedArms((current) => {
      const next = { ...current };
      let changed = false;

      for (const { family, arms } of families) {
        const armIds = arms.map((arm) => arm.arm);
        const validCurrent = next[family]?.filter((arm) => armIds.includes(arm));
        if (validCurrent && validCurrent.length > 0) {
          next[family] = validCurrent;
        } else {
          next[family] = [getLatestArm(arms)];
          changed = true;
        }
      }

      for (const currentFamily of Object.keys(next)) {
        if (!families.some((entry) => entry.family === currentFamily)) {
          delete next[currentFamily];
          changed = true;
        }
      }

      if (!changed && families.every((entry) => next[entry.family] != null)) {
        return current;
      }

      return next;
    });
  }, [families]);

  const toggleArmVisibility = (family: string, arm: string) => {
    setSelectedArms((current) => {
      const arms = current[family] ?? [getLatestArm(families.find((entry) => entry.family === family)?.arms ?? [])];
      if (arms.includes(arm)) {
        return { ...current, [family]: arms.filter((entry) => entry !== arm) };
      }
      return { ...current, [family]: [...arms, arm] };
    });
  };

  const showAllArmsForFamily = (family: string) => {
    const armIds = families.find((entry) => entry.family === family)?.arms ?? [];
    setSelectedArms((current) => ({ ...current, [family]: armIds.map((arm) => arm.arm) }));
  };

  useEffect(() => {
    setSelectedFamilies((current) => {
      const available = families.map((entry) => entry.family);
      const allToken = '__all_problems__';
      if (current.includes(allToken)) {
        return [allToken];
      }

      const next = current.filter((family) => available.includes(family));
      if (next.length === 0) {
        return [];
      }
      if (next.length === current.length && next.every((family) => current.includes(family))) {
        return current;
      }
      return next;
    });
  }, [families]);

  const filteredFamilies = useMemo(() => {
    const search = problemFilter.trim().toLowerCase();
    const familySelection = selectedFamilies.includes('__all_problems__') || selectedFamilies.length === 0
      ? null
      : new Set(selectedFamilies);
    const base = families
      .filter(({ family }) => (familySelection == null || familySelection.has(family)))
      .filter(({ family }) => !search || family.toLowerCase().includes(search));

    return [...base].sort((left, right) => {
      if (problemSortMode === 'most_recent') {
        const leftStamp = armSortValue(left.arms.at(-1)?.arm);
        const rightStamp = armSortValue(right.arms.at(-1)?.arm);
        if (leftStamp !== rightStamp) {
          return rightStamp - leftStamp;
        }
        return left.family.localeCompare(right.family);
      }

      const leftArms = [...left.arms].sort((a, b) => armSortValue(a.arm) - armSortValue(b.arm));
      const rightArms = [...right.arms].sort((a, b) => armSortValue(a.arm) - armSortValue(b.arm));

      const leftCurrent = leftArms.at(-1);
      const leftPrevious = leftArms.length > 1 ? leftArms.at(-2) : undefined;
      const rightCurrent = rightArms.at(-1);
      const rightPrevious = rightArms.length > 1 ? rightArms.at(-2) : undefined;

      const leftDelivery = topDeliveryScore(leftCurrent?.exps ?? []);
      const rightDelivery = topDeliveryScore(rightCurrent?.exps ?? []);
      const leftPreviousBest = leftPrevious ? topDeliveryScore(leftPrevious.exps) : -1;
      const rightPreviousBest = rightPrevious ? topDeliveryScore(rightPrevious.exps) : -1;

      const leftImprovement = leftDelivery - leftPreviousBest;
      const rightImprovement = rightDelivery - rightPreviousBest;

      if (leftImprovement !== rightImprovement) {
        return rightImprovement - leftImprovement;
      }
      if (leftDelivery !== rightDelivery) {
        return rightDelivery - leftDelivery;
      }

      return left.family.localeCompare(right.family);
    });
  }, [families, selectedFamilies, problemFilter, problemSortMode]);

  const hasHiddenProblems = useMemo(() => {
    if (selectedFamilies.includes('__all_problems__') || selectedFamilies.length === 0) {
      return false;
    }
    return selectedFamilies.length < families.length;
  }, [families.length, selectedFamilies]);

  const problemFilterSuggestion = useMemo(() => {
    const queryText = problemFilter.trim().toLowerCase();
    if (!queryText) return null;
    return families.find(({ family }) => family.toLowerCase().startsWith(queryText) && family.toLowerCase() !== queryText)?.family ?? null;
  }, [families, problemFilter]);

  const rankedByFamily = useMemo(() => {
    const map = new Map<string, { winnerId: string; runnerUpId?: string }>();

    for (const { family, arms } of families) {
      for (const { arm, exps } of arms) {
        const ranked = exps
        .map((exp) => {
        const rowKey = experimentRowKey(exp);
        return { rowKey, key: experimentRankKey(exp) };
        })
        .sort((a, b) => compareExperimentRank(a.key, b.key));

      if (ranked.length === 0) {
        continue;
      }

      map.set(family, {
        winnerId: ranked[0].rowKey,
        runnerUpId: ranked[1]?.rowKey,
      });
      map.set(`${family}::${arm}`, {
        winnerId: ranked[0].rowKey,
        runnerUpId: ranked[1]?.rowKey,
      });
      }
    }

    return map;
  }, [families]);
  const runsById = useMemo(() => new Map((runsQuery.data ?? []).map((run) => [run.id, run])), [runsQuery.data]);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Element) || !target.closest('[data-wireframe-menu]')) {
        setOpenFamilyMenu(false);
        setOpenArmMenu(null);
      }
    };

    window.addEventListener('pointerdown', onPointerDown);
    return () => window.removeEventListener('pointerdown', onPointerDown);
  }, []);

  if (query.isLoading) {
    return <div className="flex h-full items-center justify-center text-xs" style={{ color: C.fg1 }}>Loading experiments…</div>;
  }
  if (query.isError || !query.data) {
    return <div className="flex h-full items-center justify-center text-xs" style={{ color: C.red }}>Failed to load experiment data.</div>;
  }
  if (families.length === 0) {
    return <div className="flex h-full items-center justify-center text-xs" style={{ color: C.fg1 }}>No data available for wireframe.</div>;
  }
  const activeFinding = findingPanel ? findingPanel.findings[findingPanel.activeIndex] : null;

  const toggleExpanded = (experimentKey: string) => {
    setExpandedRows((previous) => {
      const next = new Set(previous);
      if (next.has(experimentKey)) {
        next.delete(experimentKey);
      } else {
        next.add(experimentKey);
      }
      return next;
    });
  };

    return (
    <div className="flex flex-col gap-3 p-4">
      <div className="rounded-lg border p-2" style={{ borderColor: C.border, background: C.surface }}>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="inline-flex size-7 items-center justify-center rounded border"
            onClick={() => {
              if (hasHiddenProblems) {
                setSelectedFamilies(['__all_problems__']);
              }
            }}
            aria-label={hasHiddenProblems ? 'Show all problem families' : 'All problem families visible'}
            title={hasHiddenProblems ? 'Show all problem families' : 'All problem families visible'}
          >
            {hasHiddenProblems ? <EyeOff size={12} color={C.red} /> : <Eye size={12} color={C.fg3} />}
          </button>
          <div className="relative" data-wireframe-menu>
                <button
                  type="button"
                  className="min-w-56 h-7 rounded-md px-2 py-1 text-left text-xs"
                  style={{ border: `1px solid ${C.border}`, background: C.input, color: C.fg4 }}
                  onClick={() => setOpenFamilyMenu((open) => !open)}
                >
                  {formatFamilyMenuLabel(selectedFamilies.includes('__all_problems__'), selectedFamilies.filter((item) => item !== '__all_problems__'))}
                </button>
                {openFamilyMenu ? (
              <div className="absolute left-0 top-full z-20 mt-1 min-w-56 rounded-md border  p-2 text-xs" data-wireframe-menu>
                <label className="mb-1 flex cursor-pointer items-center gap-1.5 px-1 py-1">
                    <input
                    type="checkbox"
                    checked={selectedFamilies.includes('__all_problems__')}
                    onChange={(event) => {
                      if (event.target.checked) {
                        setSelectedFamilies(['__all_problems__']);
                      } else {
                        setSelectedFamilies([]);
                      }
                    }}
                  />
                  all problems
                </label>
                {families.map(({ family }) => {
                  const checked = !selectedFamilies.includes('__all_problems__') && selectedFamilies.includes(family);
                  return (
                    <label key={family} className="mb-1 flex cursor-pointer items-center gap-1.5 px-1 py-1">
                      <input
                        type="checkbox"
                        onMouseDown={(event) => event.stopPropagation()}
                        checked={checked}
                        onClick={(event) => {
                          const isSingleSelect = event.ctrlKey || event.metaKey;
                          const nextChecked = event.currentTarget.checked;
                          setSelectedFamilies((current) => {
                            if (nextChecked) {
                              if (isSingleSelect) {
                                return [family];
                              }
                              const next = [...current.filter((value) => value !== '__all_problems__'), family];
                              return [...new Set(next)];
                            }
                            const next = current.filter((value) => value !== family);
                            if (next.length === 0) {
                              return ['__all_problems__'];
                            }
                            return next.includes('__all_problems__') ? ['__all_problems__'] : next;
                          });
                        }}
                      />
                      {family}
                    </label>
                  );
                })}
              </div>
            ) : null}
          </div>
          <div className="relative min-w-56">
            {problemFilterSuggestion ? (
              <div
                className="pointer-events-none absolute inset-0 h-7 rounded-md px-2 py-1 text-xs"
                style={{ border: `1px solid transparent`, color: C.fg0 }}
                aria-hidden="true"
              >
                <span style={{ visibility: 'hidden' }}>{problemFilter}</span>
                <span>{problemFilterSuggestion.slice(problemFilter.length)}</span>
              </div>
            ) : null}
            <input
              className="relative h-7 w-full rounded-md px-2 py-1 text-xs"
              style={{ border: `1px solid ${C.border}`, background: C.input, color: C.fg4 }}
              value={problemFilter}
              placeholder="Search problem family…"
              onChange={(event) => setProblemFilter(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Tab' && problemFilterSuggestion) {
                  event.preventDefault();
                  setProblemFilter(problemFilterSuggestion);
                }
              }}
              aria-label="Problem family filter"
            />
          </div>
          <div className="ml-auto flex items-center gap-2">
            <label className="text-xs" style={{ color: C.fg2 }} htmlFor="problem-sort-mode">
              Sort:
            </label>
            <select
              id="problem-sort-mode"
              value={problemSortMode}
              onChange={(event) => setProblemSortMode(event.target.value as 'most_recent' | 'arm_improvement')}
              className="h-7 min-w-[250px] rounded-md px-2 py-1 text-xs"
              style={{ border: `1px solid ${C.border}`, background: C.input, color: C.fg4 }}
            >
              <option value="most_recent">latest data</option>
              <option value="arm_improvement">delivery score</option>
            </select>
          </div>
        </div>
      </div>

      {filteredFamilies.map(({ family, arms }) => {
        const hasSynthetic = arms.some(({ exps }) => exps.some((exp) => exp.synthetic));
                const armIds = arms.map((arm) => arm.arm);
                const selected = selectedArms[family] ?? [getLatestArm(arms)];
                const selectedSet = new Set(selected);
                const hasHiddenArms = armIds.some((arm) => !selectedSet.has(arm));
                const allFamilyExps = arms.flatMap(({ exps }) => exps);
                const allFamilyRunIds = new Set(allFamilyExps.flatMap((exp) => exp.run_ids));
                const familyRuns = (runsQuery.data ?? []).filter((run) => allFamilyRunIds.has(run.id));
                const familyDiffs = (query.data?.arm_diffs ?? []).filter((diff) => diff.problem === family);
                const armSortMode = armSortModes[family] ?? 'outcome';
                const baseArmRows = arms
                  .filter(({ arm }) => selectedSet.has(arm))
                  .flatMap(({ arm, exps }) => exps.map((exp) => ({ arm, exp })));
                const outputMeanFor = (exp: ExperimentRecord) => {
                  const values = exp.run_ids
                    .map((id) => runsById.get(id)?.total_output_tokens)
                    .filter((value): value is number => typeof value === 'number');
                  if (values.length === 0) return null;
                  return values.reduce((total, value) => total + value, 0) / values.length;
                };
                const spendFor = (exp: ExperimentRecord) => {
                  const input = exp.aggregate.uncached_input_tokens?.mean;
                  if (input == null) return Number.POSITIVE_INFINITY;
                  return input + (outputMeanFor(exp) ?? 0);
                };
                const trustFor = (exp: ExperimentRecord) => confidenceScore(exp.aggregate.run_count_scored ?? 0, exp.aggregate.run_count_total ?? 0);
                const compareArmRowOutcome = (left: { arm: string; exp: ExperimentRecord }, right: { arm: string; exp: ExperimentRecord }) => {
                  const scoreLeft = findabilityScore(left.exp.aggregate.composite_score?.mean, left.exp.aggregate.quality_score?.mean) ?? -1;
                  const scoreRight = findabilityScore(right.exp.aggregate.composite_score?.mean, right.exp.aggregate.quality_score?.mean) ?? -1;
                  if (scoreRight !== scoreLeft) return scoreRight - scoreLeft;

                  const runtimeLeft = left.exp.aggregate.duration_sec?.mean ?? Number.POSITIVE_INFINITY;
                  const runtimeRight = right.exp.aggregate.duration_sec?.mean ?? Number.POSITIVE_INFINITY;
                  if (runtimeLeft !== runtimeRight) return runtimeLeft - runtimeRight;

                  const spendLeft = spendFor(left.exp);
                  const spendRight = spendFor(right.exp);
                  if (spendLeft !== spendRight) return spendLeft - spendRight;

                  const stabilityLeft = repeatabilityValue(left.exp.aggregate.composite_score?.stddev ?? null) ?? -1;
                  const stabilityRight = repeatabilityValue(right.exp.aggregate.composite_score?.stddev ?? null) ?? -1;
                  if (stabilityRight !== stabilityLeft) return stabilityRight - stabilityLeft;

                  const trustLeft = trustFor(left.exp);
                  const trustRight = trustFor(right.exp);
                  if (trustRight !== trustLeft) return trustRight - trustLeft;

                  return armSortValue(left.arm) - armSortValue(right.arm);
                };
                const problemRankedRows = [...baseArmRows].sort(compareArmRowOutcome);
                const problemWinnerId = problemRankedRows[0] ? experimentRowKey(problemRankedRows[0].exp) : null;
                const problemRunnerUpId = problemRankedRows[1] ? experimentRowKey(problemRankedRows[1].exp) : null;
                const visibleArmRows = [...baseArmRows].sort((left, right) => {
                  if (armSortMode === 'agent') {
                    const agentSort = compactSpec(left.exp.agent_spec).localeCompare(compactSpec(right.exp.agent_spec));
                    if (agentSort !== 0) return agentSort;
                  }
                  if (armSortMode === 'arm') {
                    const armSort = armSortValue(right.arm) - armSortValue(left.arm);
                    if (armSort !== 0) return armSort;
                  }
                  return compareArmRowOutcome(left, right);
                });
                return (
          <section
            key={family}
            id={`family-${family}`}
            className="rounded-lg border"
            style={{
              borderColor: C.border,
              background: `linear-gradient(180deg, ${C.subtleStrong}, ${C.subtle})`,
            }}
          >
            <div className="px-2 py-2 border-b" style={{ borderColor: C.border }}>
              <div className="flex items-center gap-2">
                <span className="inline-block size-1.5 rounded-full" style={{ background: '#8FF5D2', boxShadow: '0 0 8px #8FF5D2' }} />
                <div className="text-[15px] font-semibold" style={{ color: C.fg4 }}>
                  {family}
                </div>
                {hasSynthetic ? (
                  <span
                    className="rounded border px-1 py-0.5 text-[8px] font-semibold uppercase tracking-wide"
                    style={{
                      color: '#f59e0b',
                      borderColor: '#f59e0b66',
                      background: 'rgba(245, 158, 11, 0.16)',
                    }}
                  >
                    synth
                  </span>
                ) : null}
              </div>
              <div className="text-[11px]" style={{ color: C.fg1 }}>
                {(arms[0]?.exps[0]?.problem_meta?.description || 'Problem family').slice(0, 180)}
              </div>
            </div>
          <div
            className="space-y-3 px-2 py-2"
            style={{
              background:
                `repeating-linear-gradient(45deg, ${C.hatchLine} 0, ${C.hatchLine} 1px, transparent 1px, transparent 6px)`,
            }}
          >
              <div className="overflow-hidden rounded-lg border" style={{ borderColor: C.border, background: C.surface }}>
                <div className="border-b px-2.5 py-2.5" style={{ borderColor: C.border }}>
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2 text-[15px] font-semibold" style={{ color: C.fg4 }}>
                        <span className="inline-block size-1.5 rounded-full" style={{ background: C.accent, boxShadow: `0 0 8px ${C.accent}` }} />
                        Arm movement
                        <button
                          type="button"
                          className="inline-flex size-4 items-center justify-center rounded-full border"
                          style={{ color: C.fg1, borderColor: C.border }}
                          aria-label="Arm movement sort explanation"
                          onMouseEnter={(event) => {
                            const rect = event.currentTarget.getBoundingClientRect();
                            setColumnTooltip({
                              x: rect.left,
                              y: rect.bottom + 4,
                              title: 'Outcome sorting',
                              lines: [
                                'Default order ranks the visible arm rows in this problem together.',
                                'Sort priority: highest outcome, lower runtime, lower spend, higher stability, higher trust.',
                                'Agent and Arm sort modes group first, then apply the same outcome ranking inside each group.',
                              ],
                            });
                          }}
                          onMouseLeave={() => setColumnTooltip(null)}
                        >
                          <Info className="size-3" />
                        </button>
                        <span className="text-[11px] font-normal leading-4" style={{ color: C.fg0 }}>
                          Did the outcome improve, contract diff shows changes
                        </span>
                      </div>
                      <div className="mt-2 flex items-center gap-2">
                                <button
                      type="button"
                      className="inline-flex size-7 items-center justify-center rounded border"
                      onClick={() => {
                                if (hasHiddenArms) {
                                  showAllArmsForFamily(family);
                                }
                      }}
                      aria-label={hasHiddenArms ? 'Show all arm rows' : 'All arm rows visible'}
                      title={hasHiddenArms ? 'Show all arm rows' : 'All arm rows visible'}
                                >
                      {hasHiddenArms ? <EyeOff size={12} color={C.red} /> : <Eye size={12} color={C.fg3} />}
                                </button>
                                <div className="relative" data-wireframe-menu>
                      <button
                                type="button"
                                className="min-w-60 h-7 rounded-md px-2 py-1 text-left text-xs"
                                style={{ border: `1px solid ${C.border}`, background: C.input, color: C.fg4 }}
                                onClick={() => setOpenArmMenu((current) => (current === family ? null : family))}
                      >
                                {formatArmMenuLabel(
                                  selectedSet.size === armIds.length,
                                  selected,
                                  hasHiddenArms,
                                )}
                      </button>
                      {openArmMenu === family ? (
                                <div className="absolute left-0 top-full z-20 mt-1 min-w-60 rounded-md border  p-2 text-xs" data-wireframe-menu>
                                  <label className="mb-1 flex cursor-pointer items-center gap-1.5 px-1 py-1">
                                    <input
                                      type="checkbox"
                                      checked={selectedSet.size === armIds.length}
                                      onChange={(event) => {
                                        setSelectedArms((current) => ({ ...current, [family]: event.target.checked ? armIds : [] }));
                                      }}
                                    />
                                    all arms
                                  </label>
                                  {armIds.map((arm) => {
                                    const armChecked = selectedSet.has(arm);
                                    return (
                                      <label key={`${family}-arm-${arm}`} className="mb-1 flex cursor-pointer items-center gap-1.5 px-1 py-1">
                                        <input
                                          type="checkbox"
                                          onMouseDown={(event) => event.stopPropagation()}
                                          checked={armChecked}
                                          onClick={(event) => {
                                            const isSingleSelect = event.ctrlKey || event.metaKey;
                                            const nextChecked = event.currentTarget.checked;
                                            setSelectedArms((current) => {
                                              const currentSelected = current[family] ?? [getLatestArm(families.find((entry) => entry.family === family)?.arms ?? [])];
                                              if (nextChecked) {
                                                if (isSingleSelect) return { ...current, [family]: [arm] };
                                                return { ...current, [family]: Array.from(new Set([...currentSelected, arm])) };
                                              }
                                              return { ...current, [family]: currentSelected.filter((value) => value !== arm) };
                                            });
                                          }}
                                        />
                                        arm {arm}
                                      </label>
                                    );
                                  })}
                                </div>
                      ) : null}
                                </div>
                      </div>

                    </div>
                    <div className="flex items-center gap-2">
                      <label className="text-xs" style={{ color: C.fg2 }} htmlFor={`arm-sort-mode-${family}`}>
                        Sort:
                      </label>
                      <select
                        id={`arm-sort-mode-${family}`}
                        value={armSortMode}
                        onChange={(event) => setArmSortModes((current) => ({ ...current, [family]: event.target.value as 'outcome' | 'agent' | 'arm' }))}
                        className="h-7 rounded-md px-2 py-1 text-xs"
                        style={{ border: `1px solid ${C.border}`, background: C.input, color: C.fg4 }}
                      >
                        <option value="outcome">Outcome</option>
                        <option value="agent">Agent</option>
                        <option value="arm">Arm</option>
                      </select>
                    </div>
                  </div>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full table-auto border-collapse">
                    <thead>
                      <tr>
                        <HeaderInfoCell label="Agent spec" className="w-52 px-2.5 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide" onOpen={setColumnTooltip} onClose={() => setColumnTooltip(null)}>Agent spec</HeaderInfoCell>
                        <HeaderInfoCell label="Outcome" className="w-24 px-2.5 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide" onOpen={setColumnTooltip} onClose={() => setColumnTooltip(null)}>Outcome</HeaderInfoCell>
                        <HeaderInfoCell label="Stability" className="w-20 px-2.5 py-1.5 text-center text-[10px] font-medium uppercase tracking-wide" onOpen={setColumnTooltip} onClose={() => setColumnTooltip(null)}>Stability</HeaderInfoCell>
                        <HeaderInfoCell label="Trust" className="w-20 px-2.5 py-1.5 text-center text-[10px] font-medium uppercase tracking-wide" onOpen={setColumnTooltip} onClose={() => setColumnTooltip(null)}>Trust</HeaderInfoCell>
                        <HeaderInfoCell label="Runtime" className="w-24 px-2.5 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide" onOpen={setColumnTooltip} onClose={() => setColumnTooltip(null)}>Runtime</HeaderInfoCell>
                        <HeaderInfoCell label="Input" className="w-24 px-2.5 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide" onOpen={setColumnTooltip} onClose={() => setColumnTooltip(null)}>Input</HeaderInfoCell>
                        <HeaderInfoCell label="Output" className="w-24 px-2.5 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide" onOpen={setColumnTooltip} onClose={() => setColumnTooltip(null)}>Output</HeaderInfoCell>
                        <HeaderInfoCell label="Findings" className="px-2.5 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide" onOpen={setColumnTooltip} onClose={() => setColumnTooltip(null)}>Findings</HeaderInfoCell>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleArmRows
                        .map(({ arm, exp }) => {
                          const previousArm = armIds[armIds.indexOf(arm) - 1];
                          const previousExp = previousArm
                            ? arms.find((entry) => entry.arm === previousArm)?.exps.find((candidate) => candidate.agent_spec === exp.agent_spec)
                            : undefined;
                          const movementDelta = (before: number | null | undefined, after: number | null | undefined) =>
                            before == null || after == null ? null : after - before;
                          const formatMovement = (value: number | null, kind: 'outcome' | 'runtime' | 'spend') => {
                            if (value == null) return '—';
                            const sign = value > 0 ? '+' : '';
                            if (kind === 'outcome') return `${sign}${value.toFixed(3)}`;
                            if (kind === 'runtime') return `${sign}${value.toFixed(1)}s`;
                            return `${sign}${Math.round(value).toLocaleString()}`;
                          };
                          const movementColor = (value: number | null, higherIsBetter: boolean) => {
                            if (value == null || value === 0) return C.fg1;
                            return (higherIsBetter ? value > 0 : value < 0) ? C.green : C.red;
                          };
                          const movementArrow = (value: number | null) => {
                            if (value == null || value === 0) return '→';
                            return value > 0 ? '↑' : '↓';
                          };
                          const MovementValue = ({ value, kind, higherIsBetter }: { value: number | null; kind: 'outcome' | 'runtime' | 'spend'; higherIsBetter: boolean }) => {
                            if (value == null || value === 0) {
                              return (
                                <span className="num inline-flex items-center text-[11px] font-semibold" style={{ color: C.fg1 }}>
                                  -
                                </span>
                              );
                            }
                            return (
                              <span
                                className="num inline-flex items-center gap-1 text-[11px] font-semibold"
                                style={{ color: movementColor(value, higherIsBetter) }}
                              >
                                <span className="text-[15px] leading-none" aria-hidden>{movementArrow(value)}</span>
                                <span>{formatMovement(value, kind)}</span>
                              </span>
                            );
                          };
                          const formatRuntimeMean = (seconds?: number | null) => {
                            if (seconds == null) return '—';
                            return `${(seconds / 60).toFixed(1)}m`;
                          };
                          const formatTokenMean = (tokens?: number | null) => {
                            if (tokens == null) return '—';
                            return Math.round(tokens).toLocaleString();
                          };
                          const outputMean = outputMeanFor(exp);
                          const previousOutputMean = previousExp ? outputMeanFor(previousExp) : null;
                          const inputMean = exp.aggregate.uncached_input_tokens?.mean ?? null;
                          const previousInputMean = previousExp?.aggregate.uncached_input_tokens?.mean ?? null;
                          const runtimeMove = movementDelta(previousExp?.aggregate.duration_sec?.mean, exp.aggregate.duration_sec?.mean);
                          const inputMove = movementDelta(previousInputMean, inputMean);
                          const outputMove = movementDelta(previousOutputMean, outputMean);
                          const outcomeMove = movementDelta(previousExp?.aggregate.composite_score?.mean, exp.aggregate.composite_score?.mean);
                          const experimentKey = experimentRowKey(exp);
                          const isWinner = problemWinnerId === experimentKey;
                          const isRunnerUp = problemRunnerUpId === experimentKey;
                          const composite = findabilityScore(exp.aggregate.composite_score?.mean, exp.aggregate.quality_score?.mean);
                          const compact = compactSpec(exp.agent_spec);
                          const deliveryColor = deliveryRingColor(composite);
                          const stddev = exp.aggregate.composite_score?.stddev ?? null;
                          const repeatScore = repeatabilityValue(stddev);
                          const repeatColor = repeatabilityRingColor(stddev);
                          const scoredRuns = exp.aggregate.run_count_scored ?? 0;
                          const totalRuns = exp.aggregate.run_count_total ?? 0;
                          const confidence = confidenceScore(scoredRuns, totalRuns);
                          const confidenceColor = confidenceRingColor(exp.sample);
                          const isExpanded = expandedRows.has(experimentKey);
                          return (
                            <Fragment key={experimentKey}>
                              <tr
                                className="cursor-pointer border-b transition "
                                style={{
                                  borderColor: C.rowBorder,
                                  background: isExpanded ? C.subtle : 'transparent',
                                  borderLeft: isWinner ? `3px solid ${C.orange}` : isRunnerUp ? `3px solid ${C.fg1}` : '3px solid transparent',
                                }}
                                onClick={() => toggleExpanded(experimentKey)}
                              >
                                <td className="max-w-52 px-2.5 py-2">
                                  <div className="flex items-start gap-2 truncate text-xs" style={{ color: C.fg4 }} title={exp.agent_spec}>
                                    <ChevronRight className="mt-0.5 size-3 shrink-0 transition-transform" style={{ color: C.fg1, transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)' }} />
                                    <div className="min-w-0">
                                      <div className="flex items-center gap-2 truncate">
                                        <span className="truncate">{compact}</span>
                                        {isWinner ? <Trophy className="shrink-0" size={15} style={{ color: C.orange }} aria-label="Top scoring experiment in visible problem rows" /> : null}
                                        {isRunnerUp ? <Trophy className="shrink-0" size={15} style={{ color: '#C0C0C0' }} aria-label="Runner-up experiment in visible problem rows" /> : null}
                                      </div>
                                      <div className="num mt-0.5 text-[10px]" style={{ color: C.fg1 }}>arm {arm}</div>
                                    </div>
                                  </div>
                                </td>
                                <td className="w-24 px-2.5 py-2">
                                  <div className="flex items-center justify-start gap-2">
                                    <ScoreRing id={`${experimentKey}-outcome`} score={composite} title="Outcome score" ringColor={deliveryColor} tooltip={[`mean: ${composite == null ? '—' : composite.toFixed(3)}`, `runs scored: ${exp.aggregate.run_count_scored ?? 0}/${exp.aggregate.run_count_total ?? 0}`, statString(exp.aggregate.composite_score)]} hover={setTooltip} onMove={(next) => setTooltip(next)} onPin={pinTooltip} />
                                    <MovementValue value={outcomeMove} kind="outcome" higherIsBetter />
                                  </div>
                                </td>
                                <td className="w-20 px-2.5 py-2"><div className="flex items-center justify-center"><ScoreRing id={`${experimentKey}-stability`} score={repeatScore} title="Stability" ringColor={repeatColor} tooltip={[`stddev: ${exp.aggregate.composite_score?.stddev?.toFixed(3) ?? '—'}`, `repeat score: ${repeatScore == null ? '—' : repeatScore.toFixed(2)}`, `scored runs: ${exp.aggregate.run_count_scored ?? 0}`]} hover={setTooltip} onMove={(next) => setTooltip(next)} onPin={pinTooltip} /></div></td>
                                <td className="w-20 px-2.5 py-2"><div className="flex items-center justify-center"><ScoreRing id={`${experimentKey}-sample-quality`} score={confidence} title="Trust" ringColor={confidenceColor} tooltip={[`preferred met: ${exp.sample.preferred_met ? 'true' : 'false'}`, `minimum met: ${exp.sample.minimum_met ? 'true' : 'false'}`, `scored ratio: ${totalRuns > 0 ? `${(confidence * 100).toFixed(0)}%` : '—'}`, `unscored: ${exp.aggregate.unscored_count ?? 0}`]} hover={setTooltip} onMove={(next) => setTooltip(next)} onPin={pinTooltip} /></div></td>
                                <td className="w-24 px-2.5 py-2">
                                  <div className="num inline-flex items-center justify-start gap-2 text-[11px]">
                                    <span style={{ color: C.fg3 }}>{formatRuntimeMean(exp.aggregate.duration_sec?.mean)}</span>
                                    <MovementValue value={runtimeMove} kind="runtime" higherIsBetter={false} />
                                  </div>
                                </td>
                                <td className="w-24 px-2.5 py-2">
                                  <div className="num inline-flex items-center justify-start gap-2 text-[11px]">
                                    <span style={{ color: C.fg3 }}>{formatTokenMean(inputMean)}</span>
                                    <MovementValue value={inputMove} kind="spend" higherIsBetter={false} />
                                  </div>
                                </td>
                                <td className="w-24 px-2.5 py-2">
                                  <div className="num inline-flex items-center justify-start gap-2 text-[11px]">
                                    <span style={{ color: C.fg3 }}>{formatTokenMean(outputMean)}</span>
                                    <MovementValue value={outputMove} kind="spend" higherIsBetter={false} />
                                  </div>
                                </td>
                                <td className="px-2.5 py-2" onMouseLeave={() => closeFindingPanel(experimentKey)} style={{ color: C.fg1 }}>
                                  {(() => {
                                    const clusters = clusterFindings(exp.findings);
                                    return (
                                      <div className="flex items-center justify-start gap-3">
                                        {clusters.length === 0 && <span className="text-[10px]" style={{ color: C.fg0 }}>No findings</span>}
                                        {clusters.map(({ kind, findings }) => (
                                          <FindingClusterBadge key={`${experimentKey}-${kind}`} kind={kind} findings={findings} onOpen={(event, groupFindings, groupKind) => { openFindingPanel(event, experimentKey, groupFindings, groupKind); }} onClose={() => closeFindingPanel(experimentKey, kind)} onPin={(event, groupFindings, groupKind) => { openPinnedFindingPanel(event, experimentKey, groupFindings, groupKind); }} />
                                        ))}
                                      </div>
                                    );
                                  })()}
                                </td>
                              </tr>
                              {isExpanded ? (
                                <tr className="border-b" style={{ borderColor: C.rowBorder }}>
                                  <td colSpan={8} className="p-0">
                                    <WireframeExperimentExpansion
                                      exp={exp}
                                      onHover={setTooltip}
                                      onClose={() => setTooltip(null)}
                                      onPin={pinTooltip}
                                    />
                                  </td>
                                </tr>
                              ) : null}
                            </Fragment>
                          );
                        })}
                    </tbody>
                  </table>
                </div>
                <div className="space-y-1.5 border-t p-2.5" style={{ borderColor: C.border }}>
                  {familyDiffs.filter((diff) => selectedSet.has(diff.to_arm)).map((diff) => <ArmDiffCard key={diff.key} diff={diff} />)}
                </div>
              </div>
              <WireframePatternsMap experiments={allFamilyExps} runs={familyRuns} />
              <div className="rounded-lg border px-0.5 py-2.5" style={{ background: C.surface, borderColor: C.border }}>
                <div className="mb-2 ml-2.5 flex items-center gap-2 text-[15px] font-semibold" style={{ color: C.fg4 }}>
                  <span className="inline-block size-1.5 rounded-full" style={{ background: C.accent, boxShadow: `0 0 8px ${C.accent}` }} />
                  Trends
                  <button
                    type="button"
                    className="inline-flex size-4 items-center justify-center rounded-full border"
                    style={{ color: C.fg1, borderColor: C.border }}
                    aria-label="Trends explanation"
                    onMouseEnter={(event) => {
                      const rect = event.currentTarget.getBoundingClientRect();
                      setColumnTooltip({
                        x: rect.left,
                        y: rect.bottom + 4,
                        title: 'Trends',
                        lines: [
                          'Problem view across all arms and agent specs.',
                          'Charts use the highest outcome run for each agent spec, then cheapest, quickest, earliest run.',
                        ],
                      });
                    }}
                    onMouseLeave={() => setColumnTooltip(null)}
                  >
                    <Info className="size-3" />
                  </button>
                  <span className="text-[11px] font-normal leading-4" style={{ color: C.fg0 }}>
                    Problem view: highest outcome run for each agent spec, then cheapest, quickest, earliest run.
                  </span>
                </div>
                <WireframeTradeoffScatter runs={familyRuns} borderless showSubtitle={false} />
              </div>
          </div>
        </section>
      );
      })}

      {columnTooltip ? (
        <div
          className="pointer-events-none fixed z-30 max-w-56 rounded-md border  p-2 text-[10px]"
          style={{ left: columnTooltip.x, top: columnTooltip.y, color: C.fg3 }}
        >
          <div className="mb-1 text-[11px] font-medium" style={{ color: C.fg5 }}>{columnTooltip.title}</div>
          {columnTooltip.lines.map((line) => (
            <div key={line} className="text-[10px] leading-4" style={{ color: C.fg3 }}>{line}</div>
          ))}
        </div>
      ) : null}

      {[
        ...pinnedFindingPanels,
        ...(findingPanel &&
        !pinnedFindingPanels.some(
          (panel) => panel.rowKey === findingPanel.rowKey && panel.kind === findingPanel.kind
        )
          ? [findingPanel]
          : []),
      ].map((panel) => {
        const finding = panel.findings[panel.activeIndex];
        if (!finding) return null;
        const nav =
          panel.findings.length > 1 ? (
            <>
              <button
                type="button"
                className="inline-flex size-5 items-center justify-center rounded border text-[10px] leading-none"
                onClick={(event) => {
                  event.preventDefault();
                  panel.pinned
                    ? movePinnedFindingPanel(panel.rowKey, panel.kind, -1)
                    : moveFindingPanel(-1);
                }}
                style={{ color: C.fg3 }}
                aria-label="Previous issue"
                title="[←]"
              >
                ‹
              </button>
              <button
                type="button"
                className="inline-flex size-5 items-center justify-center rounded border text-[10px] leading-none"
                onClick={(event) => {
                  event.preventDefault();
                  panel.pinned
                    ? movePinnedFindingPanel(panel.rowKey, panel.kind, 1)
                    : moveFindingPanel(1);
                }}
                style={{ color: C.fg3 }}
                aria-label="Next issue"
                title="[→]"
              >
                ›
              </button>
            </>
          ) : null;
        return (
          <OverlayFrame
            key={`${panel.pinned ? 'pinned' : 'hover'}-${findingPanelKey(panel.rowKey, panel.kind)}`}
            x={panel.x}
            y={panel.y}
            title={`${panel.activeIndex + 1} of ${panel.findings.length}`}
            pinned={panel.pinned}
            nav={nav}
            onPin={() => {
              if (panel.pinned) {
                closePinnedFindingPanel(panel.rowKey, panel.kind);
                return;
              }
              setPinnedFindingPanels((current) => [...current, { ...panel, pinned: true }]);
              setFindingPanel(null);
            }}
            onClose={() => {
              panel.pinned
                ? closePinnedFindingPanel(panel.rowKey, panel.kind)
                : closeFindingPanelForced();
            }}
          >
            <div className="text-[10px] font-medium leading-tight text-white" style={{ color: C.fg4 }}>
              {finding.title}
            </div>
            <div className="text-[10px] leading-relaxed" style={{ color: C.fg2 }}>
              {finding.detail}
            </div>
            <div className="mt-1.5 max-h-16 space-y-0.5 overflow-hidden text-[10px] opacity-90">
              {findingEvidenceLines(finding).map((line, index) => (
                <div key={`${line}-${index}`}>{line}</div>
              ))}
            </div>
          </OverlayFrame>
        );
      })}

      {tooltip && !pinnedTooltips.some((item) => item.id === tooltip.id) && (
        <OverlayFrame
          x={tooltip.x}
          y={tooltip.y}
          title={tooltip.title}
          pinned={false}
          onPin={() => pinTooltip(tooltip)}
          onClose={() => setTooltip(null)}
        >
          {tooltip.lines.map((line) => (
            <div key={line} className="text-[10px]" style={{ color: C.fg2 }}>
              {line}
            </div>
          ))}
        </OverlayFrame>
      )}

      {pinnedTooltips.map((item) => (
        <OverlayFrame
          key={item.id}
          x={item.x}
          y={item.y}
          title={item.title}
          pinned
          onPin={() => closePinnedTooltip(item.id)}
          onClose={() => closePinnedTooltip(item.id)}
        >
          {item.lines.map((line) => (
            <div key={line} className="text-[10px]" style={{ color: C.fg2 }}>
              {line}
            </div>
          ))}
        </OverlayFrame>
      ))}
    </div>
  );
}
