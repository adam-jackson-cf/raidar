// Arm movement, adapted from the deprecated benchmark-view's trajectory/delta
// panels: score, trial time and token deltas between arms of one problem.
import { useMemo } from 'react';
import { C } from '@/presentation/colors';
import { fmtTokens } from '@/presentation/format';
import type { ExperimentRecord } from '@/api/types';

function deltaColor(delta: number | null, higherIsBetter: boolean): string {
  if (delta == null || delta === 0) return C.fg1;
  const improved = higherIsBetter ? delta > 0 : delta < 0;
  return improved ? C.green : C.red;
}

function DeltaCell({
  delta,
  format,
  higherIsBetter,
}: {
  delta: number | null;
  format: (value: number) => string;
  higherIsBetter: boolean;
}) {
  const color = deltaColor(delta, higherIsBetter);
  const improved = delta != null && delta !== 0 && (higherIsBetter ? delta > 0 : delta < 0);
  const arrow = delta == null || delta === 0 ? '' : delta > 0 ? '↑' : '↓';
  return (
    <span
      className="num inline-flex items-center gap-1 text-[11px]"
      style={{ color }}
      title={delta == null || delta === 0 ? 'No change' : improved ? 'Improved' : 'Regressed'}
    >
      {arrow && <span aria-hidden>{arrow}</span>}
      {delta == null ? '—' : `${delta > 0 ? '+' : ''}${format(delta)}`}
    </span>
  );
}

export function ArmMovement({ experiments }: { experiments: ExperimentRecord[] }) {
  const movements = useMemo(() => {
    const arms = [...new Set(experiments.map((e) => e.arm).filter(Boolean))].sort() as string[];
    const rows: Array<{
      agentSpec: string;
      from: string;
      to: string;
      composite: number | null;
      duration: number | null;
      tokens: number | null;
    }> = [];
    for (let index = 1; index < arms.length; index += 1) {
      const fromArm = arms[index - 1];
      const toArm = arms[index];
      const before = experiments.filter((e) => e.arm === fromArm);
      const after = experiments.filter((e) => e.arm === toArm);
      for (const target of after) {
        const source = before.find((e) => e.agent_spec === target.agent_spec);
        if (!source) continue;
        const num = (a?: number, b?: number) => (a != null && b != null ? a - b : null);
        rows.push({
          agentSpec: target.agent_spec,
          from: fromArm,
          to: toArm,
          composite: num(target.aggregate.composite_score?.mean, source.aggregate.composite_score?.mean),
          duration: num(target.aggregate.duration_sec?.mean, source.aggregate.duration_sec?.mean),
          tokens: num(
            target.aggregate.uncached_input_tokens?.mean,
            source.aggregate.uncached_input_tokens?.mean,
          ),
        });
      }
    }
    return rows;
  }, [experiments]);

  if (movements.length === 0) return null;

  return (
    <div
      className="flex flex-col gap-2 rounded-lg p-2.5"
      style={{ background: C.surface, border: `1px solid ${C.border}` }}
    >
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium" style={{ color: C.fg3 }}>
          Arm movement
        </span>
        <span className="text-[10px]" style={{ color: C.fg0 }}>
          did the change between arms make delivery better?
        </span>
      </div>

      {movements.length > 0 && (
        <table className="w-full max-w-2xl border-collapse">
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              {['Agent spec', 'Arms', 'Delivery', 'Trial time', 'Tokens'].map((label) => (
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
                key={`${movement.agentSpec}-${movement.from}-${movement.to}`}
                style={{ borderBottom: '1px solid rgba(255,255,255,0.03)' }}
              >
                <td className="num px-2 py-1 text-[11px]" style={{ color: C.cyan }}>
                  {movement.agentSpec}
                </td>
                <td className="num px-2 py-1 text-[11px]" style={{ color: C.fg2 }}>
                  {movement.from} → {movement.to}
                </td>
                <td className="px-2 py-1">
                  <DeltaCell delta={movement.composite} format={(v) => v.toFixed(3)} higherIsBetter />
                </td>
                <td className="px-2 py-1">
                  <DeltaCell
                    delta={movement.duration}
                    format={(v) => v.toFixed(1)}
                    higherIsBetter={false}
                  />
                </td>
                <td className="px-2 py-1">
                  <DeltaCell
                    delta={movement.tokens}
                    format={(v) => `${v < 0 ? '-' : ''}${fmtTokens(Math.round(Math.abs(v)))}`}
                    higherIsBetter={false}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
