// Sidebar run entry: concise conceptual label with the verdict up front;
// the raw run id stays in the tooltip.
import { Badge } from '@/components/Badge';
import { FindingChips } from '@/components/FindingChips';
import { C } from '@/utils/colors';
import { fmtScore } from '@/utils/helpers';
import { runLabel, scoreTier } from '@/utils/verdict';
import type { RunRecord } from '@/utils/types';

export function RunListItem({
  run,
  selected,
  onClick,
}: {
  run: RunRecord;
  selected: boolean;
  onClick: () => void;
}) {
  const tier = scoreTier(run.unscored ? null : run.composite_score);
  return (
    <button
      data-run-id={run.id}
      title={`${run.id}\n${tier.label} — ${tier.blurb}${run.status === 'ERROR' ? '\nRun errored' : ''}`}
      className="w-full rounded-lg px-2.5 py-2 text-left transition-all duration-150"
      style={{
        background: selected ? C.selected : 'transparent',
        border: selected ? `1px solid ${C.selectedBorder}` : '1px solid transparent',
      }}
      onMouseEnter={(e) => {
        if (!selected) e.currentTarget.style.background = 'rgba(255,255,255,0.04)';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = selected ? C.selected : 'transparent';
      }}
      onClick={onClick}
    >
      <div className="flex items-center gap-2">
        <span className="size-2 shrink-0 rounded-full" style={{ background: tier.color }} />
        <span className="text-xs font-medium" style={{ color: C.fg4 }}>
          {runLabel(run.id)}
        </span>
        <span className="num text-[10px]" style={{ color: tier.color }} title={`Composite ${fmtScore(run.composite_score)}`}>
          {run.composite_score != null ? run.composite_score.toFixed(2) : '—'}
        </span>
        <span className="text-[10px]" style={{ color: tier.color }}>
          {tier.label}
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          <FindingChips counts={run.finding_counts} />
          {run.synthetic && <Badge label="syn" title="Synthetic fixture run" />}
        </span>
      </div>
    </button>
  );
}
