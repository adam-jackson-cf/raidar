// Revision movement and scenario diff review, adapted from the deprecated
// benchmark-view's trajectory/delta/diff panels: review what changed in the
// scenario contract before trusting score movement between revisions.
import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, MoveRight, TriangleAlert } from 'lucide-react';
import { C } from '@/utils/colors';
import { fmtTokens } from '@/utils/helpers';
import type { ExperimentRecord, FileDiff, RevisionDiff } from '@/utils/types';

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

function RevisionDiffCard({ diff }: { diff: RevisionDiff }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<'prompt' | 'scenario'>('prompt');
  const file = diff.files[tab];
  return (
    <div className="rounded-md p-2" style={{ background: 'rgba(255,255,255,0.015)', border: `1px solid ${C.border}` }}>
      <button className="flex w-full flex-wrap items-center gap-2 text-left" onClick={() => setOpen((o) => !o)}>
        {open ? (
          <ChevronDown className="size-3.5" style={{ color: C.fg0 }} />
        ) : (
          <ChevronRight className="size-3.5" style={{ color: C.fg0 }} />
        )}
        <span className="num text-[11px] font-medium" style={{ color: C.fg3 }}>
          {diff.from_revision} <MoveRight className="inline size-3" /> {diff.to_revision} contract changes
        </span>
        {diff.summary
          .filter(
            (flag) =>
              !diff.comparable_warnings.some((w) => w.toLowerCase() === flag.toLowerCase()),
          )
          .map((flag) => (
            <span
              key={flag}
              className="rounded px-1.5 py-px text-[9px] uppercase tracking-wide"
              style={{ color: C.fg1, background: 'rgba(255,255,255,0.05)' }}
            >
              {flag}
            </span>
          ))}
        {diff.comparable_warnings.length > 0 && (
          <span
            className="inline-flex items-center gap-1 rounded px-1.5 py-px text-[9px] font-medium uppercase tracking-wide"
            style={{ color: C.orange, background: `${C.orange}12`, border: `1px solid ${C.orange}35` }}
            title="The evaluation contract itself changed — score movement is not purely the agent's doing."
          >
            <TriangleAlert className="size-2.5" />
            {diff.comparable_warnings.join(' · ')}
          </span>
        )}
      </button>
      {open && (
        <div className="mt-2 flex flex-col gap-1.5">
          <div className="flex gap-1">
            {(['prompt', 'scenario'] as const).map((name) => (
              <button
                key={name}
                onClick={() => setTab(name)}
                className="rounded px-2 py-0.5 text-[10px]"
                style={{
                  color: tab === name ? C.fg5 : C.fg1,
                  background: tab === name ? 'rgba(255,255,255,0.08)' : 'transparent',
                  border: `1px solid ${tab === name ? C.borderLight : C.border}`,
                }}
              >
                {name} (+{diff.files[name].diff.added} −{diff.files[name].diff.removed})
              </button>
            ))}
          </div>
          <DiffBlock file={file} />
        </div>
      )}
    </div>
  );
}

export function RevisionMovement({
  experiments,
  diffs,
}: {
  experiments: ExperimentRecord[];
  diffs: RevisionDiff[];
}) {
  const movements = useMemo(() => {
    const revisions = [...new Set(experiments.map((e) => e.revision).filter(Boolean))].sort() as string[];
    const rows: Array<{
      agentSpec: string;
      from: string;
      to: string;
      composite: number | null;
      duration: number | null;
      tokens: number | null;
    }> = [];
    for (let index = 1; index < revisions.length; index += 1) {
      const fromRevision = revisions[index - 1];
      const toRevision = revisions[index];
      const before = experiments.filter((e) => e.revision === fromRevision);
      const after = experiments.filter((e) => e.revision === toRevision);
      for (const target of after) {
        const source = before.find((e) => e.agent_spec === target.agent_spec);
        if (!source) continue;
        const num = (a?: number, b?: number) => (a != null && b != null ? a - b : null);
        rows.push({
          agentSpec: target.agent_spec,
          from: fromRevision,
          to: toRevision,
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

  if (movements.length === 0 && diffs.length === 0) return null;

  return (
    <div
      className="flex flex-col gap-2 rounded-lg p-2.5"
      style={{ background: C.surface, border: `1px solid ${C.border}` }}
    >
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium" style={{ color: C.fg3 }}>
          Revision movement
        </span>
        <span className="text-[10px]" style={{ color: C.fg0 }}>
          did the scenario change make delivery better? review the contract diff before trusting movement
        </span>
      </div>

      {movements.length > 0 && (
        <table className="w-full max-w-2xl border-collapse">
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              {['Agent spec', 'Revisions', 'Delivery', 'Run time', 'Tokens'].map((label) => (
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

      {diffs.map((diff) => (
        <RevisionDiffCard key={diff.key} diff={diff} />
      ))}
    </div>
  );
}
