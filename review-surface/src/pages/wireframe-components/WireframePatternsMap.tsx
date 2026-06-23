import { useMemo, useState, type MouseEvent } from 'react';
import { Info, Pin, PinOff, X } from 'lucide-react';
import { C } from '@/utils/colors';
import { humanize } from '@/utils/verdict';
import type { ExperimentRecord, RunRecord } from '@/utils/types';
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

type Criterion = {
  key: string;
  label: string;
  shortLabel: string;
  state: CriterionState;
  pass: number;
  fail: number;
  sample: number;
  latestFail: number;
  latestSample: number;
};

type AgentRow = {
  spec: string;
  label: string;
  cells: Record<string, CellState>;
};

type Overlay = {
  id: string;
  x: number;
  y: number;
  title: string;
  lines: string[];
};

const RECOVERY_COLOR = '#B58CFF';

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

function stateColor(state: CriterionState) {
  if (state === 'costing') return C.red;
  if (state === 'trending') return RECOVERY_COLOR;
  if (state === 'repeatable') return C.green;
  return C.fg1;
}

function cellColor(state: CellState) {
  if (state === 'pass') return C.green;
  if (state === 'cleared') return RECOVERY_COLOR;
  if (state === 'fail') return C.red;
  return C.fg0;
}

function cellSymbol(state: CellState) {
  if (state === 'pass') return '✓';
  if (state === 'cleared') return '✓';
  if (state === 'fail') return '×';
  return '·';
}

function cellTitle(state: CellState) {
  if (state === 'pass') return 'passes in the latest visible revision';
  if (state === 'cleared') return 'failed earlier, now passes in the latest visible revision';
  if (state === 'fail') return 'still failing in the latest visible revision';
  return 'not evaluated in the latest visible revision';
}

function buildMap(experiments: ExperimentRecord[]) {
  const revisions = [...new Set(experiments.map((exp) => exp.revision).filter(Boolean) as string[])].sort(
    (left, right) => revisionValue(left) - revisionValue(right),
  );
  const latestRevision = revisions[revisions.length - 1] ?? null;
  const metrics = [...new Set(experiments.flatMap((exp) => metricOutcomes(exp).map(([metric]) => metric)))].sort();
  const specs = [...new Set(experiments.map((exp) => exp.agent_spec))].sort((left, right) => compactSpec(left).localeCompare(compactSpec(right)));

  const criteria: Criterion[] = metrics.map((metric) => {
    let pass = 0;
    let fail = 0;
    let sample = 0;
    let latestFail = 0;
    let latestSample = 0;
    for (const exp of experiments) {
      const outcome = exp.aggregate.metric_outcomes?.[metric] as MetricOutcome | undefined;
      if (!outcome) continue;
      pass += outcome.pass_count;
      fail += outcome.fail_count;
      sample += outcome.sample_size;
      if (exp.revision === latestRevision) {
        latestFail += outcome.fail_count;
        latestSample += outcome.sample_size;
      }
    }
    const minimum = threshold(sample);
    const latestMinimum = threshold(latestSample);
    const hadEarlierFailure = fail - latestFail > 0;
    const latestClean = latestSample > 0 && latestFail === 0;
    const latestCosting = latestSample > 0 && latestFail >= latestMinimum;
    const state: CriterionState = latestCosting
      ? 'costing'
      : hadEarlierFailure && latestClean
        ? 'trending'
        : sample > 0 && pass >= minimum && fail === 0
          ? 'repeatable'
          : sample > 0 && pass >= minimum && latestClean
            ? 'repeatable'
            : 'watchlist';
    return {
      key: metric,
      label: humanize(metric),
      shortLabel: shortCriterion(metric),
      state,
      pass,
      fail,
      sample,
      latestFail,
      latestSample,
    };
  }).sort((left, right) => {
    const stateOrder: Record<CriterionState, number> = { costing: 0, trending: 1, repeatable: 2, watchlist: 3 };
    return stateOrder[left.state] - stateOrder[right.state] || left.label.localeCompare(right.label);
  });

  const rows: AgentRow[] = specs.map((spec) => {
    const cells: Record<string, CellState> = {};
    for (const criterion of criteria) {
      const agentExperiments = experiments
        .filter((exp) => exp.agent_spec === spec && exp.aggregate.metric_outcomes?.[criterion.key])
        .sort((left, right) => revisionValue(left.revision) - revisionValue(right.revision));
      const latest = agentExperiments.find((exp) => exp.revision === latestRevision) ?? agentExperiments[agentExperiments.length - 1];
      const latestOutcome = latest?.aggregate.metric_outcomes?.[criterion.key] as MetricOutcome | undefined;
      const earlierFailed = agentExperiments.some((exp) => {
        if (exp.revision === latest?.revision) return false;
        const outcome = exp.aggregate.metric_outcomes?.[criterion.key] as MetricOutcome | undefined;
        return outcome ? outcome.fail_count > 0 : false;
      });
      if (!latestOutcome) cells[criterion.key] = 'missing';
      else if (latestOutcome.fail_count > 0) cells[criterion.key] = 'fail';
      else if (earlierFailed) cells[criterion.key] = 'cleared';
      else cells[criterion.key] = 'pass';
    }
    return { spec, label: compactSpec(spec), cells };
  });

  return { criteria, rows, revisions, latestRevision };
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
      className="fixed z-40 w-80 rounded-lg border p-2 shadow-2xl"
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
  const { criteria, rows, revisions, latestRevision } = useMemo(() => buildMap(experiments), [experiments]);
  const [hovered, setHovered] = useState<Overlay | null>(null);
  const [pinned, setPinned] = useState<Overlay[]>([]);
  const costing = criteria.filter((criterion) => criterion.state === 'costing');
  const trending = criteria.filter((criterion) => criterion.state === 'trending');
  const repeatable = criteria.filter((criterion) => criterion.state === 'repeatable');
  const passTotal = criteria.reduce((sum, criterion) => sum + criterion.pass, 0);
  const failTotal = criteria.reduce((sum, criterion) => sum + criterion.fail, 0);
  const costingCopy = costing.length ? `Still failing in ${latestRevision ?? 'latest'} and likely pulling outcome down.` : `No criteria still fail in ${latestRevision ?? 'latest'}.`;
  const trendingCopy = trending.length ? `Failed earlier, now cleared in ${latestRevision ?? 'latest'}.` : 'No visible recovery trend above threshold.';
  const strengthsCopy = repeatable.length ? 'Passes hold across visible agents rather than only one clean run.' : 'No fully repeatable strengths above threshold.';

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

  return (
    <div className="relative overflow-hidden rounded-lg" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <div className="border-b px-3 py-3" style={{ borderColor: C.border }}>
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
                'Criteria use the visible revision sample and appear when their pass or fail count reaches ceil(sample / 3).',
                'Cells show the latest visible state per agent; cyan means the criterion failed earlier but is now green.',
              ],
            })}
            onMouseMove={(event) => openOverlay(event, {
              id: 'hover-threshold',
              title: 'Pattern threshold',
              lines: [
                'Criteria use the visible revision sample and appear when their pass or fail count reaches ceil(sample / 3).',
                'Cells show the latest visible state per agent; cyan means the criterion failed earlier but is now green.',
              ],
            })}
            onMouseLeave={() => setHovered(null)}
          >
            <Info size={10} />
          </span>
        </div>
        <div className="mt-1 w-full text-[13px] leading-5" style={{ color: C.fg1 }}>
          What is costing you, what has improved and what remains a strength
        </div>
        <div className="mt-2 text-[11px]" style={{ color: C.fg1 }}>
          Evidence map · linked to {runs.length} visible runs across {revisions.length} revisions
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-[11px]" style={{ color: C.fg1 }}>
          <span className="rounded border px-2 py-1" style={{ borderColor: C.border }}>visible revisions <span className="num" style={{ color: C.fg4 }}>{revisions.length}</span></span>
          <span className="rounded border px-2 py-1" style={{ borderColor: C.border }}>agents <span className="num" style={{ color: C.fg4 }}>{rows.length}</span></span>
          <span className="rounded border px-2 py-1" style={{ borderColor: C.border }}>criteria <span className="num" style={{ color: C.fg4 }}>{criteria.length}</span></span>
          <span className="rounded border px-2 py-1" style={{ borderColor: C.border }}>runs <span className="num" style={{ color: C.fg4 }}>{runs.length}</span></span>
          <span className="inline-flex items-center gap-1 rounded border px-2 py-1" style={{ borderColor: C.border }}>checks <span className="num inline-flex items-center" style={{ color: C.green }}>{passTotal} pass</span><span style={{ color: C.fg1 }}>/</span><span className="num inline-flex items-center" style={{ color: C.red }}>{failTotal} fail</span></span>
        </div>
      </div>

      <div className="grid border-b lg:grid-cols-3" style={{ borderColor: C.border }}>
        <SummaryCard
          state="costing"
          label="Costing you"
          count={costing.length}
          copy={costingCopy}
          onInfo={(event, title, copy) => openOverlay(event, { id: `hover-summary-${title}`, title, lines: [copy] })}
        />
        <SummaryCard
          state="trending"
          label="Trending up"
          count={trending.length}
          copy={trendingCopy}
          onInfo={(event, title, copy) => openOverlay(event, { id: `hover-summary-${title}`, title, lines: [copy] })}
        />
        <SummaryCard
          state="repeatable"
          label="Strengths"
          count={repeatable.length}
          copy={strengthsCopy}
          onInfo={(event, title, copy) => openOverlay(event, { id: `hover-summary-${title}`, title, lines: [copy] })}
        />
      </div>

      <div className="sb overflow-x-auto px-3 pb-2 pt-3">
        <div className="mb-2 flex items-center justify-between gap-3 text-[11px]" style={{ color: C.fg1 }}>
          <span className="text-xs font-medium" style={{ color: C.fg4 }}>Evidence map</span>
        </div>
        <table className="min-w-full border-collapse text-[11px]">
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              <th className="sticky left-0 z-10 w-44 px-2 py-1" style={{ background: C.surface }} aria-label="Agent spec" />
              {criteria.map((criterion) => (
                <th key={criterion.key} className="relative h-20 min-w-14 px-1 pb-2 text-left align-bottom" style={{ color: C.fg1 }}>
                  <button
                    className="absolute bottom-9 origin-bottom whitespace-nowrap rounded px-1 py-0.5 text-left text-[10px] transition hover:bg-white/[0.04]"
                    style={{ color: C.fg3, left: 'calc(50% + 8px)', transform: 'translateX(-50%) rotate(-60deg)' }}
                    onMouseEnter={(event) => openOverlay(event, {
                      id: `hover-criterion-${criterion.key}`,
                      title: criterion.label,
                      lines: [
                        `${criterion.pass} pass / ${criterion.fail} fail from ${criterion.sample} scored checks.`,
                        `Current classification: ${criterion.state}.`,
                        'Scoring explanation placeholder: criterion rubric detail is not available in the current wireframe data yet.',
                      ],
                    })}
                    onMouseMove={(event) => openOverlay(event, {
                      id: `hover-criterion-${criterion.key}`,
                      title: criterion.label,
                      lines: [
                        `${criterion.pass} pass / ${criterion.fail} fail from ${criterion.sample} scored checks.`,
                        `Current classification: ${criterion.state}.`,
                        'Scoring explanation placeholder: criterion rubric detail is not available in the current wireframe data yet.',
                      ],
                    })}
                    onMouseLeave={() => setHovered(null)}
                    onClick={(event) => pinOverlay(event, {
                      id: `criterion-${criterion.key}`,
                      title: criterion.label,
                      lines: [
                        `${criterion.pass} pass / ${criterion.fail} fail from ${criterion.sample} scored checks.`,
                        `Current classification: ${criterion.state}.`,
                        'Scoring explanation placeholder: criterion rubric detail is not available in the current wireframe data yet.',
                      ],
                    })}
                  >
                    {criterion.shortLabel}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.spec} style={{ borderBottom: '1px solid rgba(255,255,255,0.035)' }}>
                <td className="sticky left-0 z-10 w-44 px-2 py-2 align-middle" style={{ background: C.surface }} title={row.spec}>
                  <span className="block text-left text-[11px] font-medium leading-none" style={{ color: C.fg3 }}>
                    {row.label}
                  </span>
                </td>
                {criteria.map((criterion) => {
                  const state = row.cells[criterion.key] ?? 'missing';
                  const color = cellColor(state);
                  return (
                    <td key={`${row.spec}-${criterion.key}`} className="px-1 py-2 text-center">
                      <button
                        className="inline-flex size-7 items-center justify-center rounded border text-[13px] font-bold"
                        style={{ color, borderColor: `${color}55`, background: `${color}14` }}
                        title={`${row.label} · ${criterion.label} · ${cellTitle(state)}`}
                        onMouseEnter={(event) => openOverlay(event, {
                          id: `hover-cell-${row.spec}-${criterion.key}`,
                          title: `${row.label} · ${criterion.label}`,
                          lines: [
                            cellTitle(state),
                            `Latest visible revision: ${latestRevision ?? '—'}.`,
                            state === 'fail' ? 'Placeholder cost: this criterion is likely holding back outcome until resolved.' : state === 'cleared' ? 'Placeholder trend: earlier failure has cleared in the latest revision.' : 'Placeholder context: stable pass needs rubric detail to explain why it is valuable.',
                          ],
                        })}
                        onMouseMove={(event) => openOverlay(event, {
                          id: `hover-cell-${row.spec}-${criterion.key}`,
                          title: `${row.label} · ${criterion.label}`,
                          lines: [
                            cellTitle(state),
                            `Latest visible revision: ${latestRevision ?? '—'}.`,
                            state === 'fail' ? 'Placeholder cost: this criterion is likely holding back outcome until resolved.' : state === 'cleared' ? 'Placeholder trend: earlier failure has cleared in the latest revision.' : 'Placeholder context: stable pass needs rubric detail to explain why it is valuable.',
                          ],
                        })}
                        onMouseLeave={() => setHovered(null)}
                        onClick={(event) => pinOverlay(event, {
                          id: `cell-${row.spec}-${criterion.key}`,
                          title: `${row.label} · ${criterion.label}`,
                          lines: [
                            cellTitle(state),
                            `Latest visible revision: ${latestRevision ?? '—'}.`,
                            state === 'fail' ? 'Placeholder cost: this criterion is likely holding back outcome until resolved.' : state === 'cleared' ? 'Placeholder trend: earlier failure has cleared in the latest revision.' : 'Placeholder context: stable pass needs rubric detail to explain why it is valuable.',
                          ],
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
