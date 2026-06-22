import { useMemo, useState, type MouseEvent } from 'react';
import { CircleAlert, CircleCheck, Pin, PinOff, X } from 'lucide-react';
import { C } from '@/utils/colors';
import { humanize, categoryHint, categoryLabel, runLabel } from '@/utils/verdict';
import type { ExperimentRecord, RunRecord } from '@/utils/types';
import { compactSpec } from './wireframeLabels';

type PatternKind = 'friction' | 'strength';
type PatternSource = 'criteria' | 'gate' | 'finding';

type MetricOutcome = {
  pass_rate: number;
  mean_score: number;
  sample_size: number;
  pass_count: number;
  fail_count: number;
};

type Pattern = {
  id: string;
  kind: PatternKind;
  source: PatternSource;
  label: string;
  detail: string;
  count: number;
  sample: number;
  agentSpecs: string[];
  revisions: string[];
  runIds: string[];
  severity: number;
};

type Overlay = {
  id: string;
  pattern: Pattern;
  x: number;
  y: number;
};

function threshold(sample: number) {
  return Math.max(1, Math.ceil(sample / 3));
}

function uniqueSorted(values: string[]) {
  return [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

function metricOutcomes(exp: ExperimentRecord) {
  return Object.entries(exp.aggregate.metric_outcomes ?? {}) as Array<[string, MetricOutcome]>;
}

function buildCriteriaPatterns(experiments: ExperimentRecord[]): Pattern[] {
  const byMetric = new Map<
    string,
    {
      sample: number;
      pass: number;
      fail: number;
      agentSpecs: string[];
      revisions: string[];
      runIds: string[];
      scoreTotal: number;
      scoreSamples: number;
    }
  >();

  for (const exp of experiments) {
    for (const [metric, outcome] of metricOutcomes(exp)) {
      const entry = byMetric.get(metric) ?? {
        sample: 0,
        pass: 0,
        fail: 0,
        agentSpecs: [],
        revisions: [],
        runIds: [],
        scoreTotal: 0,
        scoreSamples: 0,
      };
      entry.sample += outcome.sample_size;
      entry.pass += outcome.pass_count;
      entry.fail += outcome.fail_count;
      entry.agentSpecs.push(exp.agent_spec);
      if (exp.revision) entry.revisions.push(exp.revision);
      entry.runIds.push(...exp.run_ids);
      entry.scoreTotal += outcome.mean_score * outcome.sample_size;
      entry.scoreSamples += outcome.sample_size;
      byMetric.set(metric, entry);
    }
  }

  const patterns: Pattern[] = [];
  for (const [metric, entry] of byMetric) {
    if (entry.sample <= 0) continue;
    const minimum = threshold(entry.sample);
    const label = humanize(metric);
    const agents = uniqueSorted(entry.agentSpecs).map(compactSpec);
    const revisions = uniqueSorted(entry.revisions);
    const runIds = uniqueSorted(entry.runIds);
    const meanScore = entry.scoreSamples > 0 ? entry.scoreTotal / entry.scoreSamples : null;
    if (entry.fail >= minimum) {
      patterns.push({
        id: `criteria-friction-${metric}`,
        kind: 'friction',
        source: 'criteria',
        label,
        detail: `Failed ${entry.fail} of ${entry.sample} scored checks${meanScore == null ? '' : ` · mean ${meanScore.toFixed(2)}`}`,
        count: entry.fail,
        sample: entry.sample,
        agentSpecs: agents,
        revisions,
        runIds,
        severity: entry.fail / entry.sample + agents.length * 0.1 + revisions.length * 0.05,
      });
    }
    if (entry.pass >= minimum) {
      patterns.push({
        id: `criteria-strength-${metric}`,
        kind: 'strength',
        source: 'criteria',
        label,
        detail: `Passed ${entry.pass} of ${entry.sample} scored checks${meanScore == null ? '' : ` · mean ${meanScore.toFixed(2)}`}`,
        count: entry.pass,
        sample: entry.sample,
        agentSpecs: agents,
        revisions,
        runIds,
        severity: entry.pass / entry.sample + agents.length * 0.1 + revisions.length * 0.05,
      });
    }
  }
  return patterns;
}

function buildGatePatterns(runs: RunRecord[]): Pattern[] {
  const byGate = new Map<string, { runIds: string[]; agentSpecs: string[]; revisions: string[] }>();
  for (const run of runs) {
    for (const gate of run.failed_gates) {
      const entry = byGate.get(gate) ?? { runIds: [], agentSpecs: [], revisions: [] };
      entry.runIds.push(run.id);
      entry.agentSpecs.push(run.agent_spec);
      entry.revisions.push(run.revision);
      byGate.set(gate, entry);
    }
  }
  const minimum = threshold(runs.length);
  return [...byGate.entries()]
    .filter(([, entry]) => uniqueSorted(entry.runIds).length >= minimum)
    .map(([gate, entry]) => {
      const runIds = uniqueSorted(entry.runIds);
      const agents = uniqueSorted(entry.agentSpecs).map(compactSpec);
      const revisions = uniqueSorted(entry.revisions);
      return {
        id: `gate-${gate}`,
        kind: 'friction' as const,
        source: 'gate' as const,
        label: `${humanize(gate)} gate`,
        detail: `Verification gate failed in ${runIds.length} of ${runs.length} visible runs`,
        count: runIds.length,
        sample: runs.length,
        agentSpecs: agents,
        revisions,
        runIds,
        severity: 2 + runIds.length / Math.max(1, runs.length) + agents.length * 0.1,
      };
    });
}

function buildRunIssuePatterns(runs: RunRecord[]): Pattern[] {
  const byCategory = new Map<string, { runIds: string[]; agentSpecs: string[]; revisions: string[] }>();
  for (const run of runs) {
    for (const [category, count] of Object.entries(run.issue_categories)) {
      if (category === 'failed-gate' || count <= 0) continue;
      const entry = byCategory.get(category) ?? { runIds: [], agentSpecs: [], revisions: [] };
      entry.runIds.push(run.id);
      entry.agentSpecs.push(run.agent_spec);
      entry.revisions.push(run.revision);
      byCategory.set(category, entry);
    }
  }
  const minimum = threshold(runs.length);
  return [...byCategory.entries()]
    .filter(([, entry]) => uniqueSorted(entry.runIds).length >= minimum)
    .map(([category, entry]) => {
      const runIds = uniqueSorted(entry.runIds);
      const agents = uniqueSorted(entry.agentSpecs).map(compactSpec);
      const revisions = uniqueSorted(entry.revisions);
      return {
        id: `finding-issue-${category}`,
        kind: 'friction' as const,
        source: 'finding' as const,
        label: categoryLabel(category),
        detail: categoryHint(category),
        count: runIds.length,
        sample: runs.length,
        agentSpecs: agents,
        revisions,
        runIds,
        severity: runIds.length / Math.max(1, runs.length) + agents.length * 0.1 + revisions.length * 0.05,
      };
    });
}

function buildExperimentFindingPatterns(experiments: ExperimentRecord[], visibleRunCount: number): Pattern[] {
  const byCategory = new Map<string, { kind: PatternKind; runIds: string[]; agentSpecs: string[]; revisions: string[] }>();
  for (const exp of experiments) {
    for (const finding of exp.findings) {
      if (finding.kind !== 'good' && finding.kind !== 'issue') continue;
      const key = `${finding.kind}:${finding.category}`;
      const entry = byCategory.get(key) ?? {
        kind: finding.kind === 'good' ? 'strength' : 'friction',
        runIds: [],
        agentSpecs: [],
        revisions: [],
      };
      entry.runIds.push(...exp.run_ids);
      entry.agentSpecs.push(exp.agent_spec);
      if (exp.revision) entry.revisions.push(exp.revision);
      byCategory.set(key, entry);
    }
  }
  const minimum = threshold(visibleRunCount);
  return [...byCategory.entries()]
    .filter(([, entry]) => uniqueSorted(entry.runIds).length >= minimum)
    .map(([key, entry]) => {
      const [, category] = key.split(':');
      const runIds = uniqueSorted(entry.runIds);
      const agents = uniqueSorted(entry.agentSpecs).map(compactSpec);
      const revisions = uniqueSorted(entry.revisions);
      return {
        id: `experiment-finding-${key}`,
        kind: entry.kind,
        source: 'finding' as const,
        label: categoryLabel(category),
        detail: categoryHint(category),
        count: runIds.length,
        sample: visibleRunCount,
        agentSpecs: agents,
        revisions,
        runIds,
        severity: runIds.length / Math.max(1, visibleRunCount) + agents.length * 0.1 + revisions.length * 0.05,
      };
    });
}

function buildPatterns(experiments: ExperimentRecord[], runs: RunRecord[]) {
  return [
    ...buildCriteriaPatterns(experiments),
    ...buildGatePatterns(runs),
    ...buildRunIssuePatterns(runs),
    ...buildExperimentFindingPatterns(experiments, runs.length),
  ].sort((a, b) => b.severity - a.severity || b.count - a.count || a.label.localeCompare(b.label));
}

function sourceLabel(source: PatternSource) {
  if (source === 'criteria') return 'scorer criteria';
  if (source === 'gate') return 'verification gate';
  return 'finding evidence';
}

function PatternRow({ pattern, onHover, onLeave, onPin }: {
  pattern: Pattern;
  onHover: (event: MouseEvent<HTMLElement>, pattern: Pattern) => void;
  onLeave: () => void;
  onPin: (event: MouseEvent<HTMLElement>, pattern: Pattern) => void;
}) {
  const isStrength = pattern.kind === 'strength';
  const color = isStrength ? C.green : pattern.source === 'gate' ? C.red : C.orange;
  return (
    <button
      className="grid w-full grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-2 rounded-md px-2 py-1.5 text-left transition hover:bg-white/[0.035]"
      style={{ border: `1px solid ${C.border}`, background: 'rgba(255,255,255,0.015)' }}
      onMouseEnter={(event) => onHover(event, pattern)}
      onMouseMove={(event) => onHover(event, pattern)}
      onMouseLeave={onLeave}
      onClick={(event) => onPin(event, pattern)}
    >
      <span className="min-w-0">
        <span className="block truncate text-[12px] font-medium" style={{ color: C.fg4 }}>{pattern.label}</span>
        <span className="block truncate text-[11px]" style={{ color: C.fg1 }}>{sourceLabel(pattern.source)}</span>
      </span>
      <span className="num text-[12px] font-semibold" style={{ color }}>
        {pattern.count} of {pattern.sample}
      </span>
      <span className="text-[10px]" style={{ color: C.fg1 }}>
        {pattern.agentSpecs.length} agent{pattern.agentSpecs.length === 1 ? '' : 's'}
      </span>
    </button>
  );
}

function PatternColumn({ title, kind, patterns, empty, onHover, onLeave, onPin }: {
  title: string;
  kind: PatternKind;
  patterns: Pattern[];
  empty: string;
  onHover: (event: MouseEvent<HTMLElement>, pattern: Pattern) => void;
  onLeave: () => void;
  onPin: (event: MouseEvent<HTMLElement>, pattern: Pattern) => void;
}) {
  const color = kind === 'strength' ? C.green : C.orange;
  return (
    <div className="rounded-md p-2" style={{ background: 'rgba(255,255,255,0.015)', border: `1px solid ${C.border}` }}>
      <div className="mb-2 flex items-center gap-2">
        {kind === 'strength' ? <CircleCheck className="size-3.5" style={{ color }} /> : <CircleAlert className="size-3.5" style={{ color }} />}
        <span className="text-[12px] font-medium" style={{ color: C.fg4 }}>{title}</span>
      </div>
      {patterns.length === 0 ? (
        <div className="rounded-md px-2 py-4 text-center text-[11px]" style={{ color: C.fg1, border: `1px dashed ${C.border}` }}>
          {empty}
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          {patterns.map((pattern) => (
            <PatternRow key={pattern.id} pattern={pattern} onHover={onHover} onLeave={onLeave} onPin={onPin} />
          ))}
        </div>
      )}
    </div>
  );
}

function PatternOverlay({ overlay, pinned, onPin, onClose }: {
  overlay: Overlay;
  pinned: boolean;
  onPin: (overlay: Overlay) => void;
  onClose: (id: string) => void;
}) {
  const pattern = overlay.pattern;
  const color = pattern.kind === 'strength' ? C.green : pattern.source === 'gate' ? C.red : C.orange;
  return (
    <div
      className="fixed z-40 w-80 rounded-lg border p-2 shadow-2xl"
      style={{ left: overlay.x, top: overlay.y, color: C.fg3, background: 'rgba(5,5,5,0.96)', borderColor: 'rgba(255,255,255,0.16)' }}
      onClick={(event) => event.stopPropagation()}
    >
      <div className="mb-2 flex items-center justify-between gap-2 border-b border-white/10 pb-1.5">
        <span className="text-[10px] uppercase tracking-wide" style={{ color }}>{pattern.kind}</span>
        <span className="flex items-center gap-1">
          <button className="inline-flex size-5 items-center justify-center rounded border border-white/10" onClick={() => onPin(overlay)} title={pinned ? 'Unpin overlay' : 'Pin overlay'}>
            {pinned ? <PinOff size={11} color={C.fg2} /> : <Pin size={11} color={C.fg2} />}
          </button>
          <button className="inline-flex size-5 items-center justify-center rounded border border-white/10" onClick={() => onClose(overlay.id)} title="Close overlay">
            <X size={11} color={C.fg2} />
          </button>
        </span>
      </div>
      <div className="text-[12px] font-medium" style={{ color: C.fg5 }}>{pattern.label}</div>
      <div className="mt-1 text-[11px] leading-relaxed" style={{ color: C.fg2 }}>{pattern.detail}</div>
      <div className="mt-2 grid grid-cols-2 gap-2 text-[10px]">
        <div className="rounded p-1.5" style={{ background: 'rgba(255,255,255,0.035)' }}>
          <div style={{ color: C.fg0 }}>Coverage</div>
          <div className="num text-[12px]" style={{ color }}>{pattern.count} of {pattern.sample}</div>
        </div>
        <div className="rounded p-1.5" style={{ background: 'rgba(255,255,255,0.035)' }}>
          <div style={{ color: C.fg0 }}>Source</div>
          <div style={{ color: C.fg3 }}>{sourceLabel(pattern.source)}</div>
        </div>
      </div>
      <div className="mt-2 space-y-1 text-[10px] leading-4">
        <div><span style={{ color: C.fg0 }}>Agents </span><span style={{ color: C.fg3 }}>{pattern.agentSpecs.join(', ') || '—'}</span></div>
        <div><span style={{ color: C.fg0 }}>Revisions </span><span className="num" style={{ color: C.fg3 }}>{pattern.revisions.join(', ') || '—'}</span></div>
        <div><span style={{ color: C.fg0 }}>Runs </span><span className="num" style={{ color: C.fg3 }}>{pattern.runIds.slice(0, 8).map(runLabel).join(', ')}{pattern.runIds.length > 8 ? ` +${pattern.runIds.length - 8}` : ''}</span></div>
      </div>
    </div>
  );
}

export function WireframePatterns({ experiments, runs }: { experiments: ExperimentRecord[]; runs: RunRecord[] }) {
  const patterns = useMemo(() => buildPatterns(experiments, runs), [experiments, runs]);
  const frictions = patterns.filter((pattern) => pattern.kind === 'friction');
  const strengths = patterns.filter((pattern) => pattern.kind === 'strength');
  const [hovered, setHovered] = useState<Overlay | null>(null);
  const [pinned, setPinned] = useState<Overlay[]>([]);

  const showHover = (event: MouseEvent<HTMLElement>, pattern: Pattern) => {
    setHovered({ id: `hover-${pattern.id}`, pattern, x: event.clientX + 14, y: event.clientY + 14 });
  };
  const pinOverlay = (event: MouseEvent<HTMLElement>, pattern: Pattern) => {
    const next = { id: `pinned-${pattern.id}`, pattern, x: event.clientX + 14, y: event.clientY + 14 };
    setPinned((current) => current.some((item) => item.id === next.id) ? current : [...current, next]);
  };
  const togglePinnedOverlay = (overlay: Overlay) => {
    setPinned((current) => current.some((item) => item.id === overlay.id)
      ? current.filter((item) => item.id !== overlay.id)
      : [...current, { ...overlay, id: `pinned-${overlay.pattern.id}` }]);
  };
  const closeOverlay = (id: string) => {
    if (id.startsWith('hover-')) setHovered(null);
    setPinned((current) => current.filter((item) => item.id !== id));
  };

  if (runs.length === 0 && experiments.length === 0) return null;

  return (
    <div className="relative rounded-lg p-3" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <div className="mb-3">
        <div className="text-[15px] font-medium" style={{ color: C.fg4 }}>Patterns</div>
        <div className="text-[13px] leading-5" style={{ color: C.fg1 }}>
          Recurring signals across visible runs. Criteria appear when they affect at least one third of their sample.
        </div>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <PatternColumn
          title="Frictions"
          kind="friction"
          patterns={frictions}
          empty="No recurring frictions above threshold."
          onHover={showHover}
          onLeave={() => setHovered(null)}
          onPin={pinOverlay}
        />
        <PatternColumn
          title="Strengths"
          kind="strength"
          patterns={strengths}
          empty="No recurring strengths above threshold."
          onHover={showHover}
          onLeave={() => setHovered(null)}
          onPin={pinOverlay}
        />
      </div>
      {hovered ? <PatternOverlay overlay={hovered} pinned={false} onPin={togglePinnedOverlay} onClose={closeOverlay} /> : null}
      {pinned.map((overlay) => (
        <PatternOverlay key={overlay.id} overlay={overlay} pinned onPin={togglePinnedOverlay} onClose={closeOverlay} />
      ))}
    </div>
  );
}
