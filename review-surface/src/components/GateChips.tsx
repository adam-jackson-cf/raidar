// Quick gate-status scan for the run header: one chip per verification gate,
// clickable to select the gate's evidence span in the tree.
import { C } from '@/utils/colors';
import type { Span } from '@/utils/types';

export function GateChips({
  spans,
  onSelect,
}: {
  spans: Span[];
  onSelect: (spanId: string) => void;
}) {
  const gates = spans.filter((span) => span.name.startsWith('gate:'));
  if (gates.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[9px] font-medium uppercase tracking-wider" style={{ color: C.fg0 }}>
        gates
      </span>
      {gates.map((gate) => {
        const failed = gate.status === 'ERROR';
        const color = failed ? C.red : C.green;
        return (
          <button
            key={gate.id}
            onClick={() => onSelect(gate.id)}
            title={`${gate.name} ${failed ? 'failed' : 'passed'} — view evidence`}
            className="num inline-flex items-center gap-1 rounded-full px-2 py-px text-[10px] transition hover:bg-white/10"
            style={{ color, background: `${color}10`, border: `1px solid ${color}38` }}
          >
            <span className="font-bold">{failed ? '✗' : '✓'}</span>
            {gate.name.slice('gate:'.length)}
          </button>
        );
      })}
    </div>
  );
}
