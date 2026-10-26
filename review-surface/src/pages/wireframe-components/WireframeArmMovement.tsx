import { useMemo } from 'react';
import { Trophy } from 'lucide-react';
import { C } from '@/presentation/colors';
import { fmtTokens } from '@/presentation/format';
import type { ExperimentRecord, StatBlock } from '@/api/types';
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

function armSortValue(arm?: string | null) {
  if (!arm) return 0;
  const match = arm.match(/\d+/g);
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

export function WireframeArmMovement({
  experiments,
  selectedArms,
  allArmSelected,
}: {
  experiments: ExperimentRecord[];
  selectedArms: string[];
  allArmSelected: boolean;
}) {
  const movements = useMemo(() => {
    const arms = [...new Set(experiments.map((experiment) => experiment.arm).filter(Boolean))].sort(
      (left, right) => armSortValue(left) - armSortValue(right),
    ) as string[];
    const selected = allArmSelected ? arms : selectedArms;
    const rows: MovementRow[] = [];

    for (const arm of selected) {
      const armIndex = arms.indexOf(arm);
      if (armIndex <= 0) continue;

      const previousArm = arms[armIndex - 1];
      const currentExperiments = experiments.filter((experiment) => experiment.arm === arm);
      const previousExperiments = experiments.filter((experiment) => experiment.arm === previousArm);

      for (const target of currentExperiments) {
        // The same agent (harness, model, reasoning) under the other arm's workflow.
        const source = previousExperiments.find(
          (experiment) =>
            experiment.harness === target.harness &&
            experiment.model === target.model &&
            experiment.reasoning_effort === target.reasoning_effort,
        );
        if (!source) continue;

        rows.push({
          key: `${target.agent_spec}-${previousArm}-${arm}`,
          agentSpec: target.agent_spec,
          agentLabel: compactSpec(target.agent_spec),
          from: previousArm,
          to: arm,
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
  }, [allArmSelected, experiments, selectedArms]);

  return (
    <div className="flex flex-col gap-2 rounded-lg p-2.5" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <div>
        <div className="flex items-center gap-2 text-[15px] font-semibold" style={{ color: C.fg4 }}>
          <span className="inline-block size-1.5 rounded-full" style={{ background: C.accent, boxShadow: `0 0 8px ${C.accent}` }} />
          Arm movement
        </div>
        <div className="mt-1 text-[11px] leading-4" style={{ color: C.fg0 }}>
          Did the outcome improve between arms
        </div>
      </div>

      {movements.length === 0 && (
        <div className="rounded-md px-2 py-4 text-[11px]" style={{ color: C.fg1, border: `1px dashed ${C.border}` }}>
          No previous visible arm to compare.
        </div>
      )}

      {movements.length > 0 && (
        <table className="w-full max-w-3xl border-collapse">
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              {['Agent', 'Arm', 'Outcome', 'Runtime', 'Spend'].map((label) => (
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
                  borderBottom: `1px solid ${C.rowBorder}`,
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
                        aria-label="Highest outcome improvement between compared arms"
                      />
                    ) : null}
                    {movement.rank === 2 ? (
                      <Trophy
                        className="shrink-0"
                        size={15}
                        style={{ color: '#C0C0C0' }}
                        aria-label="Runner-up outcome improvement between compared arms"
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
    </div>
  );
}
