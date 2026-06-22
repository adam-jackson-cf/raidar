import { Fragment, useEffect, useMemo, useState, type FocusEvent, type MouseEvent, type ReactNode, type WheelEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronRight, Eye, EyeOff, Pin, PinOff, Trophy } from 'lucide-react';
import { Link } from 'react-router-dom';
import { WireframePatterns } from './wireframe-components/WireframePatterns';
import { KIND_STYLES } from '@/components/AnnotationChip';
import { EvidenceRefList } from '@/components/AnnotationCards';
import { WireframeTradeoffScatter } from './wireframe-components/WireframeTradeoffScatter';
import { WireframeRevisionMovement } from './wireframe-components/WireframeRevisionMovement';
import { compactSpec } from './wireframe-components/wireframeLabels';
import { api } from '@/api/client';
import { C } from '@/utils/colors';
import { fmtPercent, fmtScore } from '@/utils/helpers';
import { humanize, runLabel, scorerName, scoreTier } from '@/utils/verdict';
import type { ExperimentRecord, RunRecord, StatBlock } from '@/utils/types';

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
      title={`${runLabel(id)} · score ${run?.composite_score?.toFixed(3) ?? 'unscored'}${run?.status === 'ERROR' ? ' · run failed' : ''}`}
      className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] transition hover:bg-white/10"
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
      {run ? (
        <span className="text-[9px]" style={{ color: C.fg0 }}>
          i {run.finding_counts.issue} · g {run.finding_counts.good} · n {run.finding_counts.note}
        </span>
      ) : null}
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

function WireframeExperimentExpansion({ exp }: { exp: ExperimentRecord }) {
  const runs = useQuery({ queryKey: ['runs'], queryFn: api.runs });
  const runsById = useMemo(() => new Map((runs.data ?? []).map((run) => [run.id, run])), [runs.data]);
  const metricOutcomes = Object.entries(exp.aggregate.metric_outcomes ?? {}) as Array<[string, MetricOutcome]>;
  const scorerOutcomes = Object.entries(exp.aggregate.scorer_outcomes ?? {});
  const failing = metricOutcomes.filter(([, o]) => o.pass_rate < 1).sort(([, a], [, b]) => a.pass_rate - b.pass_rate);
  const passing = metricOutcomes.filter(([, o]) => o.pass_rate >= 1);

  return (
    <div className="flex flex-col gap-3 border-t px-3 py-2.5" style={{ borderColor: 'rgba(255,255,255,0.05)', background: 'rgba(255,255,255,0.01)' }}>
      {exp.run_ids.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide" style={{ color: C.fg1 }}>
            Runs — open one to see its full story
          </div>
          <div className="flex flex-wrap gap-1.5">
            {exp.run_ids.map((id) => (
              <WireframeRunPill key={id} run={runsById.get(id)} id={id} />
            ))}
          </div>
        </div>
      )}

      {metricOutcomes.length > 0 && (
        <div className="grid gap-3 lg:grid-cols-2">
          <div>
            <div
              className="mb-1 text-[10px] font-medium uppercase tracking-wide"
              style={{ color: failing.length > 0 ? C.orange : C.fg1 }}
            >
              {failing.length > 0 ? `Where points were lost (${failing.length})` : 'Where points were lost'}
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
              What held up ({passing.length})
            </div>
            <div className="flex max-w-md flex-col">
              {passing.map(([metric, o]) => (
                <WireframeMetricOutcomeRow key={metric} metric={metric} outcome={o} />
              ))}
            </div>
          </div>
        </div>
      )}

      {scorerOutcomes.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide" style={{ color: C.fg1 }}>
            Score areas
          </div>
          <div className="flex flex-wrap gap-1.5">
            {scorerOutcomes.map(([scorer, o]) => (
              <span
                key={scorer}
                title={`${scorer} · mean score ${fmtScore(o.mean_score)} across ${o.sample_size} runs`}
                className="inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[10px]"
                style={{ color: C.fg2, background: 'rgba(255,255,255,0.04)', border: `1px solid ${C.border}` }}
              >
                {scorerName(scorer)}
                <span className="num" style={{ color: scoreTier(o.mean_score).color }}>
                  {fmtScore(o.mean_score)}
                </span>
              </span>
            ))}
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
                    background: `linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.015)), ${style.bg}`,
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
  const normalized = asNormalizedScore(score);
  if (normalized == null) return C.fg2;
  if (normalized >= 0.9) return C.green;
  if (normalized >= 0.75) return C.orange;
  return C.red;
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
    'Use this for quick model/agent comparison within a scenario family.',
  ],
  Outcome: [
    'Primary objective score for the run, represented as mean outcome quality.',
    'Higher scores mean stronger overall performance against the scenario target.',
  ],
  Stability: [
    'Outcome consistency across runs after repeatability processing.',
    'Higher stability means outcomes are less volatile and more repeatable.',
  ],
  Trust: [
    'How confidently the score can be trusted from available evidence.',
    'Derived from scored runs vs total runs and whether sample thresholds are met.',
  ],
  Run: [
    'How much of the recorded run set completed successfully.',
    'Higher fill means stronger signal strength for this experiment.',
  ],
  Findings: [
    'Grouped runtime findings collected from the scenario checks.',
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
  const neutral = `rgba(255,255,255,0.16)`;

  return (
    <button
      type="button"
      className="inline-flex size-7 items-center justify-center rounded-full transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
      style={{ color: ringColor }}
      title={title}
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
            className="inline-flex size-5 items-center justify-center rounded border border-white/20 text-[10px] leading-none"
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
            className="inline-flex size-5 items-center justify-center rounded border border-white/20 text-[11px] font-medium leading-none"
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

type RevisionFilterMap = Record<string, string[]>;

function formatFamilyMenuLabel(allSelected: boolean, families: string[]) {
  if (allSelected) return 'all scenarios';
  if (families.length === 1) return families[0];
  if (families.length > 0) return `${families.length} scenarios`;
  return 'all scenarios';
}

function formatRevisionMenuLabel(allSelected: boolean, revisions: string[], hasHidden: boolean) {
  if (allSelected) return 'all revisions';
  if (revisions.length === 0) return 'no revisions';
  if (!hasHidden) return `revision ${revisions[0]}`;
  if (revisions.length === 1) return `revision ${revisions[0]}`;
  return `${revisions.length} revisions`;
}

function findMissingRevisionLabel(revisions: string[], selected: string[]) {
  return revisions.some((revision) => !selected.includes(revision));
}

function experimentRowKey(exp: ExperimentRecord) {
  return `${exp.experiment_id}|${compactSpec(exp.agent_spec)}|${exp.revision ?? ''}`;
}

function revisionSortValue(revision?: string | null) {
  if (!revision) return 0;
  const match = revision.match(/\d+/g);
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
    revisionSortValue(exp.revision),
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
      className="relative inline-flex size-4 rotate-45 items-center justify-center rounded-none border text-[9px] font-bold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
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
      title={title}
    >
      <span className="-rotate-45 transform">{config.symbol}</span>
      <span className="sr-only">{`${title} findings ${findings.length}`}</span>
    </button>
  );
}

export function WireframeExperimentsPage() {
  const query = useQuery({ queryKey: ['experiments'], queryFn: api.experiments });
  const runsQuery = useQuery({ queryKey: ['runs'], queryFn: api.runs });
  const [scenarioFilter, setScenarioFilter] = useState('');
  const [selectedFamilies, setSelectedFamilies] = useState<string[]>(['__all_scenarios__']);
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [openFamilyMenu, setOpenFamilyMenu] = useState(false);
  const [openRevisionMenu, setOpenRevisionMenu] = useState<string | null>(null);
  const [tooltip, setTooltip] = useState<HoverPayload | null>(null);
  const [columnTooltip, setColumnTooltip] = useState<HoverPayload | null>(null);
  const [pinnedTooltips, setPinnedTooltips] = useState<HoverPayload[]>([]);
  const [findingPanel, setFindingPanel] = useState<FindingPanelState | null>(null);
  const [pinnedFindingPanels, setPinnedFindingPanels] = useState<FindingPanelState[]>([]);
  const [selectedRevisions, setSelectedRevisions] = useState<RevisionFilterMap>({});
  const [scenarioSortMode, setScenarioSortMode] = useState<'most_recent' | 'revision_improvement'>('most_recent');

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

  const families = useMemo(() => {
    const groups = new Map<string, Map<string, ExperimentRecord[]>>();
    for (const exp of query.data?.experiments ?? []) {
      const family = exp.scenario ?? 'unknown';
      const revision = exp.revision ?? 'unknown';
      const revisions = groups.get(family) ?? new Map<string, ExperimentRecord[]>();
      const list = revisions.get(revision) ?? [];
      list.push(exp);
      revisions.set(revision, list);
      groups.set(family, revisions);
    }
    return [...groups.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([family, revisionMap]) => ({
        family,
        revisions: [...revisionMap.entries()]
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([revision, exps]) => ({ revision, exps: exps.sort((a, b) => (b.revision ?? '').localeCompare(a.revision ?? '')) })),
      }));
  }, [query.data]);

  const getLatestRevision = (revisions: Array<{ revision: string }>) => {
    if (revisions.length === 0) return 'unknown';
    const sorted = [...revisions].sort((a, b) => a.revision.localeCompare(b.revision));
    return sorted.at(-1)?.revision ?? 'unknown';
  };

  useEffect(() => {
    setSelectedRevisions((current) => {
      const next = { ...current };
      let changed = false;

      for (const { family, revisions } of families) {
        const revisionIds = revisions.map((revision) => revision.revision);
        const validCurrent = next[family]?.filter((revision) => revisionIds.includes(revision));
        if (validCurrent && validCurrent.length > 0) {
          next[family] = validCurrent;
        } else {
          next[family] = [getLatestRevision(revisions)];
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

  const toggleRevisionVisibility = (family: string, revision: string) => {
    setSelectedRevisions((current) => {
      const revisions = current[family] ?? [getLatestRevision(families.find((entry) => entry.family === family)?.revisions ?? [])];
      if (revisions.includes(revision)) {
        return { ...current, [family]: revisions.filter((entry) => entry !== revision) };
      }
      return { ...current, [family]: [...revisions, revision] };
    });
  };

  const showAllRevisionsForFamily = (family: string) => {
    const revisionIds = families.find((entry) => entry.family === family)?.revisions ?? [];
    setSelectedRevisions((current) => ({ ...current, [family]: revisionIds.map((revision) => revision.revision) }));
  };

  useEffect(() => {
    setSelectedFamilies((current) => {
      const available = families.map((entry) => entry.family);
      const allToken = '__all_scenarios__';
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
    const search = scenarioFilter.trim().toLowerCase();
    const familySelection = selectedFamilies.includes('__all_scenarios__') || selectedFamilies.length === 0
      ? null
      : new Set(selectedFamilies);
    const base = families
      .filter(({ family }) => (familySelection == null || familySelection.has(family)))
      .filter(({ family }) => !search || family.toLowerCase().includes(search));

    return [...base].sort((left, right) => {
      if (scenarioSortMode === 'most_recent') {
        const leftStamp = revisionSortValue(left.revisions.at(-1)?.revision);
        const rightStamp = revisionSortValue(right.revisions.at(-1)?.revision);
        if (leftStamp !== rightStamp) {
          return rightStamp - leftStamp;
        }
        return left.family.localeCompare(right.family);
      }

      const leftRevisions = [...left.revisions].sort((a, b) => revisionSortValue(a.revision) - revisionSortValue(b.revision));
      const rightRevisions = [...right.revisions].sort((a, b) => revisionSortValue(a.revision) - revisionSortValue(b.revision));

      const leftCurrent = leftRevisions.at(-1);
      const leftPrevious = leftRevisions.length > 1 ? leftRevisions.at(-2) : undefined;
      const rightCurrent = rightRevisions.at(-1);
      const rightPrevious = rightRevisions.length > 1 ? rightRevisions.at(-2) : undefined;

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
  }, [families, selectedFamilies, scenarioFilter, scenarioSortMode]);

  const hasHiddenScenarios = useMemo(() => {
    if (selectedFamilies.includes('__all_scenarios__') || selectedFamilies.length === 0) {
      return false;
    }
    return selectedFamilies.length < families.length;
  }, [families.length, selectedFamilies]);

  const scenarioFilterSuggestion = useMemo(() => {
    const queryText = scenarioFilter.trim().toLowerCase();
    if (!queryText) return null;
    return families.find(({ family }) => family.toLowerCase().startsWith(queryText) && family.toLowerCase() !== queryText)?.family ?? null;
  }, [families, scenarioFilter]);

  const rankedByFamily = useMemo(() => {
    const map = new Map<string, { winnerId: string; runnerUpId?: string }>();

    for (const { family, revisions } of families) {
      for (const { revision, exps } of revisions) {
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
      map.set(`${family}::${revision}`, {
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
        setOpenRevisionMenu(null);
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
    <div className="sb flex h-full flex-col gap-3 overflow-auto p-4">
      <div className="rounded-lg border p-2" style={{ borderColor: C.border, background: C.surface }}>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="inline-flex size-7 items-center justify-center rounded border border-white/20"
            onClick={() => {
              if (hasHiddenScenarios) {
                setSelectedFamilies(['__all_scenarios__']);
              }
            }}
            aria-label={hasHiddenScenarios ? 'Show all scenario families' : 'All scenario families visible'}
            title={hasHiddenScenarios ? 'Show all scenario families' : 'All scenario families visible'}
          >
            {hasHiddenScenarios ? <EyeOff size={12} color={C.red} /> : <Eye size={12} color={C.fg3} />}
          </button>
          <div className="relative" data-wireframe-menu>
                <button
                  type="button"
                  className="min-w-56 h-7 rounded-md px-2 py-1 text-left text-xs"
                  style={{ border: `1px solid ${C.border}`, background: 'rgba(0,0,0,0.4)', color: C.fg4 }}
                  onClick={() => setOpenFamilyMenu((open) => !open)}
                >
                  {formatFamilyMenuLabel(selectedFamilies.includes('__all_scenarios__'), selectedFamilies.filter((item) => item !== '__all_scenarios__'))}
                </button>
                {openFamilyMenu ? (
              <div className="absolute left-0 top-full z-20 mt-1 min-w-56 rounded-md border border-white/15 bg-black/90 p-2 text-xs" data-wireframe-menu>
                <label className="mb-1 flex cursor-pointer items-center gap-1.5 px-1 py-1">
                    <input
                    type="checkbox"
                    checked={selectedFamilies.includes('__all_scenarios__')}
                    onChange={(event) => {
                      if (event.target.checked) {
                        setSelectedFamilies(['__all_scenarios__']);
                      } else {
                        setSelectedFamilies([]);
                      }
                    }}
                  />
                  all scenarios
                </label>
                {families.map(({ family }) => {
                  const checked = !selectedFamilies.includes('__all_scenarios__') && selectedFamilies.includes(family);
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
                              const next = [...current.filter((value) => value !== '__all_scenarios__'), family];
                              return [...new Set(next)];
                            }
                            const next = current.filter((value) => value !== family);
                            if (next.length === 0) {
                              return ['__all_scenarios__'];
                            }
                            return next.includes('__all_scenarios__') ? ['__all_scenarios__'] : next;
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
            {scenarioFilterSuggestion ? (
              <div
                className="pointer-events-none absolute inset-0 h-7 rounded-md px-2 py-1 text-xs"
                style={{ border: `1px solid transparent`, color: C.fg0 }}
                aria-hidden="true"
              >
                <span style={{ visibility: 'hidden' }}>{scenarioFilter}</span>
                <span>{scenarioFilterSuggestion.slice(scenarioFilter.length)}</span>
              </div>
            ) : null}
            <input
              className="relative h-7 w-full rounded-md px-2 py-1 text-xs"
              style={{ border: `1px solid ${C.border}`, background: 'rgba(0,0,0,0.4)', color: C.fg4 }}
              value={scenarioFilter}
              placeholder="Search scenario family…"
              onChange={(event) => setScenarioFilter(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Tab' && scenarioFilterSuggestion) {
                  event.preventDefault();
                  setScenarioFilter(scenarioFilterSuggestion);
                }
              }}
              aria-label="Scenario family filter"
            />
          </div>
          <div className="ml-auto flex items-center gap-2">
            <label className="text-xs" style={{ color: C.fg2 }} htmlFor="scenario-sort-mode">
              Sort:
            </label>
            <select
              id="scenario-sort-mode"
              value={scenarioSortMode}
              onChange={(event) => setScenarioSortMode(event.target.value as 'most_recent' | 'revision_improvement')}
              className="h-7 min-w-[250px] rounded-md px-2 py-1 text-xs"
              style={{ border: `1px solid ${C.border}`, background: 'rgba(0,0,0,0.45)', color: C.fg4 }}
            >
              <option value="most_recent">latest data</option>
              <option value="revision_improvement">delivery score</option>
            </select>
          </div>
        </div>
      </div>

      {filteredFamilies.map(({ family, revisions }) => {
        const hasSynthetic = revisions.some(({ exps }) => exps.some((exp) => exp.synthetic));
                const revisionIds = revisions.map((revision) => revision.revision);
                const selected = selectedRevisions[family] ?? [getLatestRevision(revisions)];
                const selectedSet = new Set(selected);
                const hasHiddenRevisions = revisionIds.some((revision) => !selectedSet.has(revision));
                const allFamilyExps = revisions.flatMap(({ exps }) => exps);
                const visibleFamilyExps = revisions
                  .filter(({ revision }) => selectedSet.has(revision))
                  .flatMap(({ exps }) => exps);
                const revisionRunIds = new Set(visibleFamilyExps.flatMap((exp) => exp.run_ids));
                const familyRuns = (runsQuery.data ?? []).filter((run) => revisionRunIds.has(run.id));
                const familyDiffs = (query.data?.revision_diffs ?? []).filter((diff) => diff.scenario === family);
                return (
          <section
            key={family}
            id={`family-${family}`}
            className="rounded-lg border"
            style={{
              borderColor: C.border,
              background: 'linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.015))',
            }}
          >
            <div className="px-2 py-2 border-b" style={{ borderColor: C.border }}>
              <div className="flex items-center gap-2">
                <div className="text-sm font-medium" style={{ color: C.fg5 }}>
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
                {(revisions[0]?.exps[0]?.scenario_meta?.description || 'Scenario family').slice(0, 180)}
              </div>
              <div className="mt-2 flex items-center gap-2">
                <button
                  type="button"
                  className="inline-flex size-7 items-center justify-center rounded border border-white/20"
                  onClick={() => {
                    if (hasHiddenRevisions) {
                      showAllRevisionsForFamily(family);
                    }
                  }}
                  aria-label={hasHiddenRevisions ? 'Show all revision tables' : 'All revision tables visible'}
                  title={hasHiddenRevisions ? 'Show all revision tables' : 'All revision tables visible'}
                >
                  {hasHiddenRevisions ? <EyeOff size={12} color={C.red} /> : <Eye size={12} color={C.fg3} />}
                </button>
                <div className="relative" data-wireframe-menu>
                  <button
                    type="button"
                    className="min-w-60 h-7 rounded-md px-2 py-1 text-left text-xs"
                    style={{ border: `1px solid ${C.border}`, background: 'rgba(0,0,0,0.45)', color: C.fg4 }}
                    onClick={() => setOpenRevisionMenu((current) => (current === family ? null : family))}
                  >
                    {formatRevisionMenuLabel(
                      selectedSet.size === revisionIds.length,
                      selected,
                      hasHiddenRevisions,
                    )}
                  </button>
                  {openRevisionMenu === family ? (
                    <div className="absolute left-0 top-full z-20 mt-1 min-w-60 rounded-md border border-white/15 bg-black/90 p-2 text-xs" data-wireframe-menu>
                      <label className="mb-1 flex cursor-pointer items-center gap-1.5 px-1 py-1">
                            <input
                              type="checkbox"
                              checked={selectedSet.size === revisionIds.length}
                              onChange={(event) => {
                                setSelectedRevisions((current) => {
                                  const next = { ...current };
                                  if (event.target.checked) {
                                    next[family] = revisionIds;
                                  } else {
                                    next[family] = [];
                                  }
                                  return next;
                                });
                              }}
                            />
                            all revisions
                          </label>
                          {revisionIds.map((revision) => {
                            const revisionChecked = selectedSet.has(revision);
                            return (
                              <label key={`${family}-revision-${revision}`} className="mb-1 flex cursor-pointer items-center gap-1.5 px-1 py-1">
                                <input
                        type="checkbox"
                        onMouseDown={(event) => event.stopPropagation()}
                        checked={revisionChecked}
                        onClick={(event) => {
                          const isSingleSelect = event.ctrlKey || event.metaKey;
                          const nextChecked = event.currentTarget.checked;
                          setSelectedRevisions((current) => {
                            const currentSelected = current[family] ?? [getLatestRevision(families.find((entry) => entry.family === family)?.revisions ?? [])];
                            if (nextChecked) {
                              if (isSingleSelect) {
                                return { ...current, [family]: [revision] };
                              }
                                        return { ...current, [family]: Array.from(new Set([...currentSelected, revision])) };
                                      }
                                      const next = currentSelected.filter((value) => value !== revision);
                                      return { ...current, [family]: next };
                                    });
                                  }}
                                />
                                revision {revision}
                              </label>
                            );
                          })}
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          <div
            className="space-y-3 px-2 py-2"
            style={{
              background:
                'repeating-linear-gradient(45deg, rgba(255,255,255,0.06) 0, rgba(255,255,255,0.06) 1px, transparent 1px, transparent 6px)',
            }}
          >
              {revisions
                .filter(({ revision }) => selectedSet.has(revision))
                .map(({ revision, exps }) => (
                <div
                  key={`${family}:${revision}`}
                  className="overflow-hidden rounded-md border"
                  style={{ borderColor: 'rgba(255,255,255,0.12)', background: 'rgba(0,0,0,0.78)' }}
                >
                  <div className="flex items-center gap-2 px-2.5 py-2 text-[11px] font-semibold uppercase tracking-wide" style={{ borderBottom: '1px solid rgba(255,255,255,0.1)', color: C.fg2 }}>
                    <button
                      type="button"
                      className="inline-flex size-6 items-center justify-center rounded border border-white/20"
                      onClick={() => toggleRevisionVisibility(family, revision)}
                      title={selectedSet.has(revision) ? 'Hide this revision table' : 'Show this revision table'}
                      aria-label={selectedSet.has(revision) ? `Hide revision ${revision} table` : `Show revision ${revision} table`}
                    >
                      {selectedSet.has(revision) ? <Eye size={12} color={C.fg3} /> : <EyeOff size={12} color={C.red} />}
                    </button>
                    Revision {revision}
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full table-auto border-collapse">
                      <thead>
                        <tr>
                          <HeaderInfoCell
                            label="Agent spec"
                            className="w-52 px-2.5 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide"
                            onOpen={setColumnTooltip}
                            onClose={() => setColumnTooltip(null)}
                          >
                            Agent spec
                          </HeaderInfoCell>
                          <HeaderInfoCell
                            label="Outcome"
                            className="w-20 px-2.5 py-1.5 text-center text-[10px] font-medium uppercase tracking-wide"
                            onOpen={setColumnTooltip}
                            onClose={() => setColumnTooltip(null)}
                          >
                            Outcome
                          </HeaderInfoCell>
                          <HeaderInfoCell
                            label="Stability"
                            className="w-20 px-2.5 py-1.5 text-center text-[10px] font-medium uppercase tracking-wide"
                            onOpen={setColumnTooltip}
                            onClose={() => setColumnTooltip(null)}
                          >
                            Stability
                          </HeaderInfoCell>
                          <HeaderInfoCell
                            label="Trust"
                            className="w-20 px-2.5 py-1.5 text-center text-[10px] font-medium uppercase tracking-wide"
                            onOpen={setColumnTooltip}
                            onClose={() => setColumnTooltip(null)}
                          >
                            Trust
                          </HeaderInfoCell>
                          <HeaderInfoCell
                            label="Run"
                            className="w-20 px-2.5 py-1.5 text-center text-[10px] font-medium uppercase tracking-wide"
                            onOpen={setColumnTooltip}
                            onClose={() => setColumnTooltip(null)}
                          >
                            run
                          </HeaderInfoCell>
                          <HeaderInfoCell
                            label="Findings"
                            className="px-2.5 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide"
                            onOpen={setColumnTooltip}
                            onClose={() => setColumnTooltip(null)}
                          >
                            Findings
                          </HeaderInfoCell>
                        </tr>
                      </thead>
                      <tbody>
                        {[...exps]
                          .sort((left, right) => compareExperimentRank(experimentRankKey(left), experimentRankKey(right)))
                          .map((exp) => {
                          const experimentKey = experimentRowKey(exp);
                          const revisionRanking = rankedByFamily.get(`${family}::${revision}`);
                          const isWinner = revisionRanking?.winnerId === experimentKey;
                          const isRunnerUp =
                            revisionRanking?.runnerUpId === experimentKey && revisionRanking?.winnerId !== revisionRanking?.runnerUpId;
                          const composite = findabilityScore(
                            exp.aggregate.composite_score?.mean,
                            exp.aggregate.quality_score?.mean,
                          );
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
                                className="cursor-pointer border-b transition hover:bg-white/[0.03]"
                                style={{
                                  borderColor: 'rgba(255,255,255,0.05)',
                                  background: isExpanded ? 'rgba(255,255,255,0.015)' : 'transparent',
                                  borderLeft: isWinner
                                    ? `3px solid ${C.orange}`
                                    : isRunnerUp
                                      ? `3px solid ${C.fg1}`
                                      : '3px solid transparent',
                                }}
                                onClick={() => toggleExpanded(experimentKey)}
                              >
                                <td className="max-w-52 px-2.5 py-2">
                                  <div className="flex items-center gap-2 truncate text-xs" style={{ color: C.fg4 }} title={exp.agent_spec}>
                                    <ChevronRight
                                      className="size-3 shrink-0 transition-transform"
                                      style={{ color: C.fg1, transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)' }}
                                    />
                                    <span className="truncate">{compact}</span>
                                    {isWinner ? (
                                      <Trophy
                                        className="shrink-0"
                                        size={15}
                                        style={{ color: C.orange }}
                                        aria-label="Top scoring experiment in scenario revision"
                                      />
                                    ) : null}
                                    {isRunnerUp ? (
                                      <Trophy
                                        className="shrink-0"
                                        size={15}
                                        style={{ color: '#C0C0C0' }}
                                        aria-label="Runner-up experiment in scenario revision"
                                      />
                                    ) : null}
                                  </div>
                                </td>
                                <td className="w-20 px-2.5 py-2">
                                  <div className="flex items-center justify-center">
                                    <ScoreRing
                                      id={`${experimentKey}-outcome`}
                                      score={composite}
                                      title="Outcome score"
                                      ringColor={deliveryColor}
                                      tooltip={[
                                        `mean: ${composite == null ? '—' : composite.toFixed(3)}`,
                                        `runs scored: ${exp.aggregate.run_count_scored ?? 0}/${exp.aggregate.run_count_total ?? 0}`,
                                        statString(exp.aggregate.composite_score),
                                      ]}
                                      hover={setTooltip}
                                      onMove={(next) => setTooltip(next)}
                                      onPin={pinTooltip}
                                    />
                                  </div>
                                </td>
                                <td className="w-20 px-2.5 py-2">
                                  <div className="flex items-center justify-center">
                                    <ScoreRing
                                      id={`${experimentKey}-stability`}
                                      score={repeatScore}
                                      title="Stability"
                                      ringColor={repeatColor}
                                      tooltip={[
                                        `stddev: ${exp.aggregate.composite_score?.stddev?.toFixed(3) ?? '—'}`,
                                        `repeat score: ${repeatScore == null ? '—' : repeatScore.toFixed(2)}`,
                                        `scored runs: ${exp.aggregate.run_count_scored ?? 0}`,
                                      ]}
                                      hover={setTooltip}
                                      onMove={(next) => setTooltip(next)}
                                      onPin={pinTooltip}
                                    />
                                  </div>
                                </td>
                                <td className="w-20 px-2.5 py-2">
                                  <div className="flex items-center justify-center">
                                    <ScoreRing
                                      id={`${experimentKey}-sample-quality`}
                                      score={confidence}
                                      title="Trust"
                                      ringColor={confidenceColor}
                                      tooltip={[
                                        `preferred met: ${exp.sample.preferred_met ? 'true' : 'false'}`,
                                        `minimum met: ${exp.sample.minimum_met ? 'true' : 'false'}`,
                                        `scored ratio: ${totalRuns > 0 ? `${(confidence * 100).toFixed(0)}%` : '—'}`,
                                        `unscored: ${exp.aggregate.unscored_count ?? 0}`,
                                      ]}
                                      hover={setTooltip}
                                      onMove={(next) => setTooltip(next)}
                                      onPin={pinTooltip}
                                    />
                                  </div>
                                </td>
                                <td className="w-20 px-2.5 py-2">
                                  <div className="flex items-center justify-center">
                                    <ScoreRing
                                      id={`${experimentKey}-run`}
                                      score={totalRuns > 0 ? scoredRuns / totalRuns : 0}
                                      title="Run"
                                      ringColor={runCoverageColor(scoredRuns, totalRuns)}
                                      tooltip={[
                                        `valid runs: ${scoredRuns}`,
                                        `total runs: ${totalRuns}`,
                                        `coverage: ${totalRuns > 0 ? `${Math.round((scoredRuns / totalRuns) * 100)}%` : '—'}`,
                                      ]}
                                      hover={setTooltip}
                                      onMove={(next) => setTooltip(next)}
                                      onPin={pinTooltip}
                                    />
                                  </div>
                                </td>
                                <td
                                  className="px-2.5 py-2"
                                  onMouseLeave={() => closeFindingPanel(experimentKey)}
                                  style={{ color: C.fg1 }}
                                >
                                  {(() => {
                                    const clusters = clusterFindings(exp.findings);
                                    return (
                                      <div className="flex items-center justify-start gap-3">
                                        {clusters.length === 0 && <span className="text-[10px]" style={{ color: C.fg0 }}>No findings</span>}
                                        {clusters.map(({ kind, findings }) => (
                                          <FindingClusterBadge
                                            key={`${experimentKey}-${kind}`}
                                            kind={kind}
                                            findings={findings}
                                            onOpen={(event, groupFindings, groupKind) => {
                                              openFindingPanel(event, experimentKey, groupFindings, groupKind);
                                            }}
                                            onClose={() => closeFindingPanel(experimentKey, kind)}
                                            onPin={(event, groupFindings, groupKind) => {
                                              openPinnedFindingPanel(event, experimentKey, groupFindings, groupKind);
                                            }}
                                          />
                                        ))}
                                      </div>
                                    );
                                  })()}
                                </td>
                              </tr>
                              {isExpanded ? (
                                <tr className="border-b" style={{ borderColor: 'rgba(255,255,255,0.05)' }}>
                                  <td colSpan={6} className="p-0">
                                    <WireframeExperimentExpansion exp={exp} />
                                  </td>
                                </tr>
                              ) : null}
                            </Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
              <div className="grid items-start gap-3 xl:grid-cols-2">
                <WireframeRevisionMovement
                  experiments={allFamilyExps}
                  diffs={familyDiffs}
                  selectedRevisions={selected}
                  allRevisionSelected={selectedSet.size === revisionIds.length}
                />
                <WireframePatterns experiments={visibleFamilyExps} runs={familyRuns} />
              </div>
              <WireframeTradeoffScatter runs={familyRuns} />
          </div>
        </section>
      );
      })}

      {columnTooltip ? (
        <div
          className="pointer-events-none fixed z-30 max-w-56 rounded-md border border-white/15 bg-black/90 p-2 text-[10px]"
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
                className="inline-flex size-5 items-center justify-center rounded border border-white/20 text-[10px] leading-none"
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
                className="inline-flex size-5 items-center justify-center rounded border border-white/20 text-[10px] leading-none"
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
