import { useMemo, useState, type MouseEvent } from 'react';
import { Info, Pin, PinOff, X } from 'lucide-react';
import { C } from '@/utils/colors';
import { humanize } from '@/utils/verdict';
import type { ExperimentRecord, RunRecord, StatBlock } from '@/utils/types';
import { compactSpec } from './wireframeLabels';

type MetricOutcome = {
  pass_rate: number;
  mean_score: number;
  sample_size: number;
  pass_count: number;
  fail_count: number;
};

type CellState = 'pass' | 'fail' | 'cleared' | 'missing';
type CriterionState = 'costing' | 'trending' | 'repeatable' | 'watchlist';

type AgentPerformance = {
  spec: string;
  label: string;
  outcome: MetricOutcome;
  composite: number | null;
  runtime: number | null;
  spend: number | null;
  stability: number | null;
  trust: number;
};

type Cell = {
  state: CellState;
  sample: number;
  pass: number;
  fail: number;
  passRate: number | null;
  failRate: number | null;
  threshold: number;
  topAgents: AgentPerformance[];
};

type Criterion = {
  key: string;
  label: string;
  shortLabel: string;
  state: CriterionState;
  pass: number;
  fail: number;
  sample: number;
};

type RevisionRow = {
  revision: string;
  cells: Record<string, Cell>;
};

type Overlay = {
  id: string;
  x: number;
  y: number;
  title: string;
  lines: string[];
};

const RECOVERY_COLOR = '#B58CFF';
const MIN_SAMPLE = 3;
const COSTING_FAIL_RATE = 0.34;
const STRENGTH_PASS_RATE = 0.8;

function threshold(sample: number) {
  return Math.max(1, Math.ceil(sample / 3));
}

function metricOutcomes(exp: ExperimentRecord) {
  return Object.entries(exp.aggregate.metric_outcomes ?? {}) as Array<[string, MetricOutcome]>;
}

function revisionValue(revision?: string | null) {
  if (!revision) return 0;
  const match = revision.match(/\d+/g);
  return match ? Number(match.join('')) : 0;
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


function criterionOverlayLines(criterion: Criterion) {
  return [
    `Scorer: metric outcomes`,
    `Scorer field: aggregate.metric_outcomes["${criterion.key}"]`,
    `Criterion label: ${criterion.label}`,
    `${criterion.pass} pass / ${criterion.fail} fail from ${criterion.sample} scored checks across the scenario.`,
    `Latest classification: ${criterion.state}.`,
  ];
}

function stateColor(state: CriterionState | CellState) {
  if (state === 'costing' || state === 'fail') return C.red;
  if (state === 'trending' || state === 'cleared') return RECOVERY_COLOR;
  if (state === 'repeatable' || state === 'pass') return C.green;
  return C.fg1;
}

function cellSymbol(state: CellState) {
  if (state === 'pass') return '✓';
  if (state === 'cleared') return '✓';
  if (state === 'fail') return '×';
  return '·';
}

function cellTitle(state: CellState) {
  if (state === 'pass') return 'strength: pass rate is at or above 80%';
  if (state === 'cleared') return 'trending: previously costing, now no longer above the failure threshold';
  if (state === 'fail') return 'costing: fail rate is at or above one third';
  return 'insufficient or mixed evidence';
}

function statMean(stat?: StatBlock | null) {
  return stat?.mean ?? null;
}

function repeatabilityValue(stddev: number | null | undefined): number | null {
  if (stddev == null) return null;
  return Math.max(0, Math.min(1, 1 - stddev));
}

function confidenceScore(scored: number, total: number): number {
  if (total <= 0) return 0;
  return Math.max(0, Math.min(1, scored / total));
}

function spendFor(exp: ExperimentRecord) {
  const aggregate = exp.aggregate as typeof exp.aggregate & { output_tokens?: { mean?: number }; uncached_output_tokens?: { mean?: number } };
  const input = exp.aggregate.uncached_input_tokens?.mean;
  if (input == null) return null;
  return input + (aggregate.output_tokens?.mean ?? aggregate.uncached_output_tokens?.mean ?? 0);
}

function agentPerformance(exp: ExperimentRecord, outcome: MetricOutcome): AgentPerformance {
  return {
    spec: exp.agent_spec,
    label: compactSpec(exp.agent_spec),
    outcome,
    composite: exp.aggregate.quality_score?.mean ?? exp.aggregate.composite_score?.mean ?? null,
    runtime: statMean(exp.aggregate.duration_sec),
    spend: spendFor(exp),
    stability: repeatabilityValue(exp.aggregate.composite_score?.stddev ?? null),
    trust: confidenceScore(exp.aggregate.run_count_scored ?? 0, exp.aggregate.run_count_total ?? 0),
  };
}

function compareAgentPerformance(left: AgentPerformance, right: AgentPerformance) {
  const scoreLeft = left.composite ?? -1;
  const scoreRight = right.composite ?? -1;
  if (scoreRight !== scoreLeft) return scoreRight - scoreLeft;

  const runtimeLeft = left.runtime ?? Number.POSITIVE_INFINITY;
  const runtimeRight = right.runtime ?? Number.POSITIVE_INFINITY;
  if (runtimeLeft !== runtimeRight) return runtimeLeft - runtimeRight;

  const spendLeft = left.spend ?? Number.POSITIVE_INFINITY;
  const spendRight = right.spend ?? Number.POSITIVE_INFINITY;
  if (spendLeft !== spendRight) return spendLeft - spendRight;

  const stabilityLeft = left.stability ?? -1;
  const stabilityRight = right.stability ?? -1;
  if (stabilityRight !== stabilityLeft) return stabilityRight - stabilityLeft;

  if (right.trust !== left.trust) return right.trust - left.trust;

  return left.label.localeCompare(right.label);
}

function classifyCell(sample: number, pass: number, fail: number, previouslyCosting: boolean): CellState {
  if (sample < MIN_SAMPLE) return 'missing';
  const failRate = fail / sample;
  const passRate = pass / sample;
  if (failRate >= COSTING_FAIL_RATE) return 'fail';
  if (previouslyCosting) return 'cleared';
  if (passRate >= STRENGTH_PASS_RATE) return 'pass';
  return 'missing';
}

function criterionStateFromCell(cell: Cell | undefined): CriterionState {
  if (!cell) return 'watchlist';
  if (cell.state === 'fail') return 'costing';
  if (cell.state === 'cleared') return 'trending';
  if (cell.state === 'pass') return 'repeatable';
  return 'watchlist';
}

function buildMap(experiments: ExperimentRecord[]) {
  const revisions = [...new Set(experiments.map((exp) => exp.revision).filter(Boolean) as string[])].sort(
    (left, right) => revisionValue(left) - revisionValue(right),
  );
  const latestRevision = revisions[revisions.length - 1] ?? null;
  const metrics = [...new Set(experiments.flatMap((exp) => metricOutcomes(exp).map(([metric]) => metric)))].sort();
  const revisionExperiments = new Map(revisions.map((revision) => [revision, experiments.filter((exp) => exp.revision === revision)]));

  const cellsByRevision = new Map<string, Record<string, Cell>>();
  const priorCostingByMetric = new Map<string, boolean>();

  for (const revision of revisions) {
    const rowCells: Record<string, Cell> = {};
    const exps = revisionExperiments.get(revision) ?? [];
    for (const metric of metrics) {
      let pass = 0;
      let fail = 0;
      let sample = 0;
      const topAgents: AgentPerformance[] = [];
      for (const exp of exps) {
        const outcome = exp.aggregate.metric_outcomes?.[metric] as MetricOutcome | undefined;
        if (!outcome) continue;
        pass += outcome.pass_count;
        fail += outcome.fail_count;
        sample += outcome.sample_size;
        topAgents.push(agentPerformance(exp, outcome));
      }
      const state = classifyCell(sample, pass, fail, priorCostingByMetric.get(metric) ?? false);
      rowCells[metric] = {
        state,
        sample,
        pass,
        fail,
        passRate: sample > 0 ? pass / sample : null,
        failRate: sample > 0 ? fail / sample : null,
        threshold: threshold(sample),
        topAgents: topAgents.sort(compareAgentPerformance).slice(0, 2),
      };
      if (state === 'fail') priorCostingByMetric.set(metric, true);
    }
    cellsByRevision.set(revision, rowCells);
  }

  const criteria: Criterion[] = metrics.map((metric) => {
    let pass = 0;
    let fail = 0;
    let sample = 0;
    for (const revision of revisions) {
      const cell = cellsByRevision.get(revision)?.[metric];
      pass += cell?.pass ?? 0;
      fail += cell?.fail ?? 0;
      sample += cell?.sample ?? 0;
    }
    return {
      key: metric,
      label: humanize(metric),
      shortLabel: shortCriterion(metric),
      state: criterionStateFromCell(latestRevision ? cellsByRevision.get(latestRevision)?.[metric] : undefined),
      pass,
      fail,
      sample,
    };
  }).sort((left, right) => {
    const stateOrder: Record<CriterionState, number> = { costing: 0, trending: 1, repeatable: 2, watchlist: 3 };
    return stateOrder[left.state] - stateOrder[right.state] || left.label.localeCompare(right.label);
  });

  const rows: RevisionRow[] = [...revisions].reverse().map((revision) => ({ revision, cells: cellsByRevision.get(revision) ?? {} }));
  const agentCount = new Set(experiments.map((exp) => exp.agent_spec)).size;
  return { criteria, rows, revisions, latestRevision, agentCount };
}

function pct(value: number | null) {
  return value == null ? '—' : `${Math.round(value * 100)}%`;
}

function agentLine(label: string, agent: AgentPerformance | undefined) {
  if (!agent) return `${label}: —`;
  return `${label}: ${agent.label} · outcome ${agent.composite == null ? '—' : agent.composite.toFixed(3)} · criterion ${agent.outcome.pass_count}/${agent.outcome.sample_size} pass`;
}

function SummaryCard({
  state,
  label,
  count,
  copy,
  onInfo,
}: {
  state: CriterionState;
  label: string;
  count: number;
  copy: string;
  onInfo: (event: MouseEvent<HTMLElement>, title: string, copy: string) => void;
}) {
  return (
    <div className="p-3" style={{ borderLeft: `1px solid ${C.border}`, background: C.surface }}>
      <div className="flex items-center gap-2 text-[12px] font-semibold uppercase tracking-wide" style={{ color: C.fg4 }}>
        <span className="inline-block size-2 rounded-sm" style={{ background: stateColor(state), boxShadow: `0 0 8px ${stateColor(state)}70` }} />
        {label}
        <button
          className="inline-flex size-4 items-center justify-center rounded-full border"
          style={{ color: C.fg1, borderColor: C.border }}
          onMouseEnter={(event) => onInfo(event, label, copy)}
          onMouseMove={(event) => onInfo(event, label, copy)}
          onMouseLeave={() => undefined}
          type="button"
          aria-label={`${label} explanation`}
        >
          <Info size={10} />
        </button>
        <span className="num ml-3 text-[16px] font-bold leading-none" style={{ color: stateColor(state) }}>{count}</span>
      </div>
    </div>
  );
}

function DetailOverlay({ overlay, pinned, onPin, onClose }: {
  overlay: Overlay;
  pinned: boolean;
  onPin: (overlay: Overlay) => void;
  onClose: (id: string) => void;
}) {
  return (
    <div
      className="fixed z-40 w-96 rounded-lg border p-2 shadow-2xl"
      style={{ left: overlay.x, top: overlay.y, color: C.fg3, background: 'rgba(5,5,5,0.96)', borderColor: 'rgba(255,255,255,0.16)' }}
      onClick={(event) => event.stopPropagation()}
    >
      <div className="mb-2 flex items-center justify-between border-b border-white/10 pb-1.5">
        <div className="text-[12px] font-medium" style={{ color: C.fg5 }}>{overlay.title}</div>
        <span className="flex items-center gap-1">
          <button className="inline-flex size-5 items-center justify-center rounded border border-white/10" onClick={() => onPin(overlay)} title={pinned ? 'Unpin overlay' : 'Pin overlay'}>
            {pinned ? <PinOff size={11} color={C.fg2} /> : <Pin size={11} color={C.fg2} />}
          </button>
          <button className="inline-flex size-5 items-center justify-center rounded border border-white/10" onClick={() => onClose(overlay.id)} title="Close overlay">
            <X size={11} color={C.fg2} />
          </button>
        </span>
      </div>
      <div className="space-y-1 text-[11px] leading-4">
        {overlay.lines.map((line) => <div key={line} style={{ color: C.fg2 }}>{line}</div>)}
      </div>
    </div>
  );
}

export function WireframePatternsMap({ experiments, runs }: { experiments: ExperimentRecord[]; runs: RunRecord[] }) {
  const { criteria, rows, revisions, latestRevision, agentCount } = useMemo(() => buildMap(experiments), [experiments]);
  const [hovered, setHovered] = useState<Overlay | null>(null);
  const [pinned, setPinned] = useState<Overlay[]>([]);
  const costing = criteria.filter((criterion) => criterion.state === 'costing');
  const trending = criteria.filter((criterion) => criterion.state === 'trending');
  const repeatable = criteria.filter((criterion) => criterion.state === 'repeatable');
  const passTotal = criteria.reduce((sum, criterion) => sum + criterion.pass, 0);
  const failTotal = criteria.reduce((sum, criterion) => sum + criterion.fail, 0);
  const costingCopy = costing.length ? `Latest revision has criteria with fail rate at or above 34%.` : `No criteria are above the failure threshold in ${latestRevision ?? 'latest'}.`;
  const trendingCopy = trending.length ? 'Previously costing criteria are now below the failure threshold.' : 'No criterion has cleared after previously costing.';
  const strengthsCopy = repeatable.length ? 'Latest revision criteria pass at or above 80%.' : 'No latest-revision strengths above threshold.';

  const openOverlay = (event: MouseEvent<HTMLElement>, overlay: Omit<Overlay, 'x' | 'y'>) => {
    setHovered({ ...overlay, x: event.clientX + 14, y: event.clientY + 14 });
  };
  const pinOverlay = (event: MouseEvent<HTMLElement>, overlay: Omit<Overlay, 'x' | 'y'>) => {
    const next = { ...overlay, id: `pinned-${overlay.id}`, x: event.clientX + 14, y: event.clientY + 14 };
    setPinned((current) => current.some((item) => item.id === next.id) ? current : [...current, next]);
  };
  const togglePin = (overlay: Overlay) => {
    setPinned((current) => current.some((item) => item.id === overlay.id)
      ? current.filter((item) => item.id !== overlay.id)
      : [...current, { ...overlay, id: `pinned-${overlay.id}` }]);
  };
  const closeOverlay = (id: string) => {
    if (id.startsWith('hover-')) setHovered(null);
    setPinned((current) => current.filter((item) => item.id !== id));
  };

  if (criteria.length === 0 || rows.length === 0) return null;

  const cellLines = (revision: string, criterion: Criterion, cell: Cell) => [
    cellTitle(cell.state),
    `Revision: ${revision}`,
    `Sample: ${cell.sample} checks · threshold ${cell.threshold}`,
    `Pass/fail: ${cell.pass} pass / ${cell.fail} fail`,
    `Rates: ${pct(cell.passRate)} pass / ${pct(cell.failRate)} fail`,
    agentLine('Top agent', cell.topAgents[0]),
    agentLine('Runner-up', cell.topAgents[1]),
    'Agent ranking uses the revision trophy order: highest outcome, lower runtime, lower spend, higher stability, higher trust.',
  ];

  return (
    <div className="relative overflow-hidden rounded-lg" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <div className="border-b px-3 py-2.5" style={{ borderColor: C.border }}>
        <div className="flex items-center gap-2 text-[15px] font-semibold" style={{ color: C.fg4 }}>
          <span className="inline-block size-1.5 rounded-full" style={{ background: C.accent, boxShadow: `0 0 8px ${C.accent}` }} />
          Patterns
          <span
            className="inline-flex size-4 items-center justify-center rounded-full border"
            style={{ color: C.fg1, borderColor: C.border }}
            onMouseEnter={(event) => openOverlay(event, {
              id: 'hover-threshold',
              title: 'Pattern threshold',
              lines: [
                'Each cell aggregates one revision and one scoring criterion across all agent specs and scored runs in the scenario.',
                'Costing: fail rate >= 34%. Strength: pass rate >= 80%. Trending: previously costing and now below the failure threshold.',
                'Cells with fewer than 3 checks, or mixed results below threshold, stay muted.',
              ],
            })}
            onMouseMove={(event) => openOverlay(event, {
              id: 'hover-threshold',
              title: 'Pattern threshold',
              lines: [
                'Each cell aggregates one revision and one scoring criterion across all agent specs and scored runs in the scenario.',
                'Costing: fail rate >= 34%. Strength: pass rate >= 80%. Trending: previously costing and now below the failure threshold.',
                'Cells with fewer than 3 checks, or mixed results below threshold, stay muted.',
              ],
            })}
            onMouseLeave={() => setHovered(null)}
          >
            <Info size={10} />
          </span>
          <span className="text-[11px] font-normal leading-4" style={{ color: C.fg0 }}>
            What is costing you, what has improved and what remains a strength
          </span>
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-[11px]" style={{ color: C.fg1 }}>
          <span className="rounded border px-2 py-1" style={{ borderColor: C.border }}>revisions <span className="num" style={{ color: C.fg4 }}>{revisions.length}</span></span>
          <span className="rounded border px-2 py-1" style={{ borderColor: C.border }}>agent specs <span className="num" style={{ color: C.fg4 }}>{agentCount}</span></span>
          <span className="rounded border px-2 py-1" style={{ borderColor: C.border }}>criteria <span className="num" style={{ color: C.fg4 }}>{criteria.length}</span></span>
          <span className="rounded border px-2 py-1" style={{ borderColor: C.border }}>runs <span className="num" style={{ color: C.fg4 }}>{runs.length}</span></span>
          <span className="inline-flex items-center gap-1 rounded border px-2 py-1" style={{ borderColor: C.border }}>checks <span className="num inline-flex items-center" style={{ color: C.green }}>{passTotal} pass</span><span style={{ color: C.fg1 }}>/</span><span className="num inline-flex items-center" style={{ color: C.red }}>{failTotal} fail</span></span>
        </div>
      </div>

      <div className="grid border-b lg:grid-cols-3" style={{ borderColor: C.border }}>
        <SummaryCard state="costing" label="Costing you" count={costing.length} copy={costingCopy} onInfo={(event, title, copy) => openOverlay(event, { id: `hover-summary-${title}`, title, lines: [copy] })} />
        <SummaryCard state="trending" label="Trending up" count={trending.length} copy={trendingCopy} onInfo={(event, title, copy) => openOverlay(event, { id: `hover-summary-${title}`, title, lines: [copy] })} />
        <SummaryCard state="repeatable" label="Strengths" count={repeatable.length} copy={strengthsCopy} onInfo={(event, title, copy) => openOverlay(event, { id: `hover-summary-${title}`, title, lines: [copy] })} />
      </div>

      <div className="sb overflow-x-auto px-3 pb-2 pt-3">
        <div className="mb-2 flex items-center justify-between gap-3 text-[11px]" style={{ color: C.fg1 }}>
          <span className="text-xs font-medium" style={{ color: C.fg4 }}>Evidence map</span>
        </div>
        <table className="min-w-full border-collapse text-[11px]">
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              <th className="sticky left-0 z-10 w-32 px-2 py-1" style={{ background: C.surface }} aria-label="Revision" />
              {criteria.map((criterion) => (
                <th key={criterion.key} className="relative h-20 min-w-14 px-1 pb-2 text-left align-bottom" style={{ color: C.fg1 }}>
                  <button
                    className="absolute bottom-4 inline-flex items-center gap-1 whitespace-nowrap rounded px-1 py-0.5 text-left text-[10px] transition hover:bg-white/[0.04]"
                    style={{ color: C.fg3, left: '50%', transform: 'rotate(-60deg)', transformOrigin: 'left bottom' }}
                    onMouseEnter={(event) => openOverlay(event, {
                      id: `hover-criterion-${criterion.key}`,
                      title: criterion.label,
                      lines: criterionOverlayLines(criterion),
                    })}
                    onMouseMove={(event) => openOverlay(event, {
                      id: `hover-criterion-${criterion.key}`,
                      title: criterion.label,
                      lines: criterionOverlayLines(criterion),
                    })}
                    onMouseLeave={() => setHovered(null)}
                    onClick={(event) => pinOverlay(event, {
                      id: `criterion-${criterion.key}`,
                      title: criterion.label,
                      lines: criterionOverlayLines(criterion),
                    })}
                  >
                    <span>{criterion.shortLabel}</span>
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.revision} style={{ borderBottom: '1px solid rgba(255,255,255,0.035)' }}>
                <td className="sticky left-0 z-10 w-32 px-2 py-2 align-middle" style={{ background: C.surface }}>
                  <span className="num block text-left text-[11px] font-medium leading-none" style={{ color: C.fg3 }}>
                    revision {row.revision}
                  </span>
                </td>
                {criteria.map((criterion) => {
                  const cell = row.cells[criterion.key];
                  const state = cell?.state ?? 'missing';
                  const color = stateColor(state);
                  return (
                    <td key={`${row.revision}-${criterion.key}`} className="px-1 py-2 text-center">
                      <button
                        className="inline-flex size-7 items-center justify-center rounded border text-[13px] font-bold"
                        style={{ color, borderColor: `${color}55`, background: `${color}14` }}
                        onMouseEnter={(event) => openOverlay(event, {
                          id: `hover-cell-${row.revision}-${criterion.key}`,
                          title: `${row.revision} · ${criterion.label}`,
                          lines: cellLines(row.revision, criterion, cell),
                        })}
                        onMouseMove={(event) => openOverlay(event, {
                          id: `hover-cell-${row.revision}-${criterion.key}`,
                          title: `${row.revision} · ${criterion.label}`,
                          lines: cellLines(row.revision, criterion, cell),
                        })}
                        onMouseLeave={() => setHovered(null)}
                        onClick={(event) => pinOverlay(event, {
                          id: `cell-${row.revision}-${criterion.key}`,
                          title: `${row.revision} · ${criterion.label}`,
                          lines: cellLines(row.revision, criterion, cell),
                        })}
                      >
                        {cellSymbol(state)}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {hovered ? <DetailOverlay overlay={hovered} pinned={false} onPin={togglePin} onClose={closeOverlay} /> : null}
      {pinned.map((overlay) => <DetailOverlay key={overlay.id} overlay={overlay} pinned onPin={togglePin} onClose={closeOverlay} />)}
    </div>
  );
}
