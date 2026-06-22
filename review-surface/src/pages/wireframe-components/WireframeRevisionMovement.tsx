import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, MoveRight, TriangleAlert, Trophy } from 'lucide-react';
import { C } from '@/utils/colors';
import { fmtTokens } from '@/utils/helpers';
import type { ExperimentRecord, FileDiff, RevisionDiff, StatBlock } from '@/utils/types';
import { compactSpec } from './wireframeLabels';

type MovementMetric = 'outcome' | 'runtime' | 'spend';

type DeltaValue = {
  before: number | null;
  after: number | null;
  delta: number | null;
};

type MovementRow = {
  key: string;
  agentSpec: string;
  agentLabel: string;
  from: string;
  to: string;
  outcome: DeltaValue;
  runtime: DeltaValue;
  spend: DeltaValue;
  rank: number;
};

function revisionSortValue(revision?: string | null) {
  if (!revision) return 0;
  const match = revision.match(/\d+/g);
  return match ? Number(match.join('')) : 0;
}

function statMean(stat?: StatBlock) {
  return stat?.mean ?? null;
}

function outputTokenMean(exp: ExperimentRecord) {
  const aggregate = exp.aggregate as ExperimentRecord['aggregate'] & {
    output_tokens?: StatBlock;
    uncached_output_tokens?: StatBlock;
  };
  return statMean(aggregate.output_tokens) ?? statMean(aggregate.uncached_output_tokens) ?? 0;
}

function spendMean(exp: ExperimentRecord) {
  const input = statMean(exp.aggregate.uncached_input_tokens);
  if (input == null) return null;
  return input + outputTokenMean(exp);
}

function delta(before: number | null, after: number | null): DeltaValue {
  return {
    before,
    after,
    delta: before != null && after != null ? after - before : null,
  };
}

function deltaState(value: DeltaValue, higherIsBetter: boolean) {
  if (value.delta == null || value.delta === 0) {
    return { label: 'No movement', arrow: '→', color: C.fg1 };
  }
  const improved = higherIsBetter ? value.delta > 0 : value.delta < 0;
  return {
    label: improved ? 'Improved' : 'Regressed',
    arrow: value.delta > 0 ? '↑' : '↓',
    color: improved ? C.green : C.red,
  };
}

function formatDelta(metric: MovementMetric, value: DeltaValue) {
  if (value.delta == null) return '—';
  const sign = value.delta > 0 ? '+' : '';
  if (metric === 'outcome') return `${sign}${value.delta.toFixed(3)}`;
  if (metric === 'runtime') return `${sign}${value.delta.toFixed(1)}s`;
  return `${sign}${fmtTokens(Math.round(value.delta))}`;
}

function formatMetric(metric: MovementMetric, value: number | null) {
  if (value == null) return '—';
  if (metric === 'outcome') return value.toFixed(3);
  if (metric === 'runtime') return `${value.toFixed(1)}s`;
  return fmtTokens(Math.round(value));
}

function DeltaCell({
  metric,
  value,
  higherIsBetter,
}: {
  metric: MovementMetric;
  value: DeltaValue;
  higherIsBetter: boolean;
}) {
  const state = deltaState(value, higherIsBetter);
  return (
    <span
      className="num inline-flex min-w-20 items-center gap-1 text-[12px] font-semibold"
      style={{ color: state.color }}
      title={`${state.label}: ${formatMetric(metric, value.before)} to ${formatMetric(metric, value.after)} (${formatDelta(metric, value)})`}
    >
      <span className="text-[16px] leading-none" aria-hidden>
        {state.arrow}
      </span>
      <span>{formatDelta(metric, value)}</span>
    </span>
  );
}

function DiffBlock({ file }: { file: FileDiff }) {
  return (
    <div
      className="sb max-h-72 overflow-auto rounded p-2"
      style={{ background: 'rgba(255,255,255,0.02)', border: `1px solid ${C.border}` }}
    >
      {file.diff.lines.map((line, index) => (
        <div
          key={index}
          className="num whitespace-pre-wrap break-words text-[10px] leading-relaxed"
          style={{
            color: line.type === 'added' ? C.green : line.type === 'removed' ? C.red : C.fg1,
            background:
              line.type === 'added'
                ? 'rgba(96,227,109,0.06)'
                : line.type === 'removed'
                  ? 'rgba(235,20,20,0.06)'
                  : 'transparent',
          }}
        >
          {line.type === 'added' ? '+ ' : line.type === 'removed' ? '- ' : '  '}
          {line.text}
        </div>
      ))}
      {file.diff.truncated && (
        <div className="mt-1 text-[10px]" style={{ color: C.orange }}>
          diff truncated — review {file.path} directly
        </div>
      )}
    </div>
  );
}

type DiffPill = { label: string; warning: boolean; tab: string };

export function RevisionDiffCard({ diff }: { diff: RevisionDiff }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState('prompt');
  const pillConfig = (flag: string) => {
    const normalized = flag.toLowerCase();
    if (normalized === 'prompt changed') return { label: 'PROMPT', warning: false, tab: 'prompt' };
    if (normalized === 'rules changed') return { label: 'RULES', warning: false, tab: 'rules' };
    if (normalized === 'starter changed') return { label: 'STARTER', warning: false, tab: 'starter' };
    if (normalized === 'quality gates changed') return { label: 'GATES', warning: false, tab: 'gates' };
    if (normalized === 'requirements changed') return { label: 'REQS', warning: false, tab: 'reqs' };
    if (normalized === 'evaluation profile changed') return { label: 'EVAL', warning: true, tab: 'eval' };
    return null;
  };
  const pills = diff.summary.map(pillConfig).filter(Boolean) as DiffPill[];
  const sections = diff.sections ?? { prompt: diff.files.prompt, scenario: diff.files.scenario };
  const tabs = pills.filter((pill) => sections[pill.tab]);
  const activeTab = sections[tab] ? tab : (tabs[0]?.tab ?? 'prompt');
  const file = sections[activeTab] ?? diff.files.prompt;
  const openTab = (nextTab: string) => {
    if (!sections[nextTab]) return;
    setTab(nextTab);
    setOpen(true);
  };
  return (
    <div className="rounded-md p-2" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <button className="flex w-full flex-wrap items-center gap-2 text-left" onClick={() => setOpen((openNow) => !openNow)}>
        {open ? (
          <ChevronDown className="size-3.5" style={{ color: C.fg0 }} />
        ) : (
          <ChevronRight className="size-3.5" style={{ color: C.fg0 }} />
        )}
        <span className="num text-[11px] font-medium" style={{ color: C.fg3 }}>
          {diff.from_revision} <MoveRight className="inline size-3" /> {diff.to_revision}
        </span>
        {pills.filter((pill) => !pill.warning).map((pill) => (
          <span
            key={pill.label}
            role="button"
            tabIndex={0}
            className="rounded px-1.5 py-px text-[9px] uppercase tracking-wide"
            style={{ color: C.cyan, background: `${C.cyan}12`, border: `1px solid ${C.cyan}35` }}
            onClick={(event) => {
              event.stopPropagation();
              openTab(pill.tab);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                event.stopPropagation();
                openTab(pill.tab);
              }
            }}
          >
            {pill.label}
          </span>
        ))}
        {pills.filter((pill) => pill.warning).map((pill) => (
          <span
            key={pill.label}
            role="button"
            tabIndex={0}
            className="inline-flex items-center gap-1 rounded px-1.5 py-px text-[9px] font-medium uppercase tracking-wide"
            style={{ color: C.orange, background: `${C.orange}12`, border: `1px solid ${C.orange}35` }}
            title="The evaluation contract itself changed - score movement is not purely the agent's doing."
            onClick={(event) => {
              event.stopPropagation();
              openTab(pill.tab);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                event.stopPropagation();
                openTab(pill.tab);
              }
            }}
          >
            <TriangleAlert className="size-2.5" />
            {pill.label}
          </span>
        ))}
      </button>
      {open && (
        <div className="mt-2 flex flex-col gap-1.5">
          <div className="flex gap-1">
            {tabs.map((pill) => (
              <button
                key={pill.tab}
                onClick={() => setTab(pill.tab)}
                className="rounded px-2 py-0.5 text-[10px]"
                style={{
                  color: activeTab === pill.tab ? C.fg5 : C.fg1,
                  background: activeTab === pill.tab ? 'rgba(255,255,255,0.08)' : 'transparent',
                  border: `1px solid ${activeTab === pill.tab ? C.borderLight : C.border}`,
                }}
              >
                {pill.label.toLowerCase()} (+{sections[pill.tab].diff.added} -{sections[pill.tab].diff.removed})
              </button>
            ))}
          </div>
          <DiffBlock file={file} />
        </div>
      )}
    </div>
  );
}

export function WireframeRevisionMovement({
  experiments,
  diffs,
  selectedRevisions,
  allRevisionSelected,
}: {
  experiments: ExperimentRecord[];
  diffs: RevisionDiff[];
  selectedRevisions: string[];
  allRevisionSelected: boolean;
}) {
  const movements = useMemo(() => {
    const revisions = [...new Set(experiments.map((experiment) => experiment.revision).filter(Boolean))].sort(
      (left, right) => revisionSortValue(left) - revisionSortValue(right),
    ) as string[];
    const selected = allRevisionSelected ? revisions : selectedRevisions;
    const rows: MovementRow[] = [];

    for (const revision of selected) {
      const revisionIndex = revisions.indexOf(revision);
      if (revisionIndex <= 0) continue;

      const previousRevision = revisions[revisionIndex - 1];
      const currentExperiments = experiments.filter((experiment) => experiment.revision === revision);
      const previousExperiments = experiments.filter((experiment) => experiment.revision === previousRevision);

      for (const target of currentExperiments) {
        const source = previousExperiments.find((experiment) => experiment.agent_spec === target.agent_spec);
        if (!source) continue;

        rows.push({
          key: `${target.agent_spec}-${previousRevision}-${revision}`,
          agentSpec: target.agent_spec,
          agentLabel: compactSpec(target.agent_spec),
          from: previousRevision,
          to: revision,
          outcome: delta(statMean(source.aggregate.composite_score), statMean(target.aggregate.composite_score)),
          runtime: delta(statMean(source.aggregate.duration_sec), statMean(target.aggregate.duration_sec)),
          spend: delta(spendMean(source), spendMean(target)),
          rank: 0,
        });
      }
    }

    return rows
      .sort((left, right) => {
        const leftOutcome = left.outcome.delta ?? Number.NEGATIVE_INFINITY;
        const rightOutcome = right.outcome.delta ?? Number.NEGATIVE_INFINITY;
        if (rightOutcome !== leftOutcome) return rightOutcome - leftOutcome;

        const leftRuntime = left.runtime.delta ?? Number.POSITIVE_INFINITY;
        const rightRuntime = right.runtime.delta ?? Number.POSITIVE_INFINITY;
        if (leftRuntime !== rightRuntime) return leftRuntime - rightRuntime;

        const leftSpend = left.spend.delta ?? Number.POSITIVE_INFINITY;
        const rightSpend = right.spend.delta ?? Number.POSITIVE_INFINITY;
        if (leftSpend !== rightSpend) return leftSpend - rightSpend;

        return left.agentLabel.localeCompare(right.agentLabel);
      })
      .map((row, index) => ({ ...row, rank: index + 1 }));
  }, [allRevisionSelected, experiments, selectedRevisions]);

  const visibleDiffs = useMemo(() => {
    const pairs = new Set(movements.map((movement) => `${movement.from}->${movement.to}`));
    return diffs.filter((diff) => pairs.has(`${diff.from_revision}->${diff.to_revision}`));
  }, [diffs, movements]);

  return (
    <div className="flex flex-col gap-2 rounded-lg p-2.5" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <div>
        <div className="flex items-center gap-2 text-[15px] font-semibold" style={{ color: C.fg4 }}>
          <span className="inline-block size-1.5 rounded-full" style={{ background: C.accent, boxShadow: `0 0 8px ${C.accent}` }} />
          Revision movement
        </div>
        <div className="mt-1 text-[11px] leading-4" style={{ color: C.fg0 }}>
          Did the outcome improve, contract diff shows changes
        </div>
      </div>

      {movements.length === 0 && (
        <div className="rounded-md px-2 py-4 text-[11px]" style={{ color: C.fg1, border: `1px dashed ${C.border}` }}>
          No previous visible revision to compare.
        </div>
      )}

      {movements.length > 0 && (
        <table className="w-full max-w-3xl border-collapse">
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              {['Agent', 'Revision', 'Outcome', 'Runtime', 'Spend'].map((label) => (
                <th
                  key={label}
                  className="px-2 py-1 text-left text-[9px] font-medium uppercase tracking-wider"
                  style={{ color: C.fg0 }}
                >
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {movements.map((movement) => (
              <tr
                key={movement.key}
                style={{
                  borderBottom: '1px solid rgba(255,255,255,0.03)',
                  borderLeft:
                    movement.rank === 1
                      ? `3px solid ${C.orange}`
                      : movement.rank === 2
                        ? `3px solid ${C.fg1}`
                        : '3px solid transparent',
                }}
              >
                <td className="num px-2 py-2 text-[11px]" style={{ color: C.fg4 }} title={movement.agentSpec}>
                  <span className="inline-flex items-center gap-2">
                    <span>{movement.agentLabel}</span>
                    {movement.rank === 1 ? (
                      <Trophy
                        className="shrink-0"
                        size={15}
                        style={{ color: C.orange }}
                        aria-label="Highest outcome improvement between compared revisions"
                      />
                    ) : null}
                    {movement.rank === 2 ? (
                      <Trophy
                        className="shrink-0"
                        size={15}
                        style={{ color: '#C0C0C0' }}
                        aria-label="Runner-up outcome improvement between compared revisions"
                      />
                    ) : null}
                  </span>
                </td>
                <td className="num px-2 py-2 text-[11px]" style={{ color: C.fg2 }}>
                  {movement.from} -&gt; {movement.to}
                </td>
                <td className="px-2 py-2">
                  <DeltaCell metric="outcome" value={movement.outcome} higherIsBetter />
                </td>
                <td className="px-2 py-2">
                  <DeltaCell metric="runtime" value={movement.runtime} higherIsBetter={false} />
                </td>
                <td className="px-2 py-2">
                  <DeltaCell metric="spend" value={movement.spend} higherIsBetter={false} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {visibleDiffs.map((diff) => (
        <RevisionDiffCard key={diff.key} diff={diff} />
      ))}
    </div>
  );
}
