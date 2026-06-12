// Adapted from Raindrop Workshop (MIT) — app/src/components/RunList.tsx (RunListItem)
import { Badge } from '@/components/Badge';
import { FindingChips } from '@/components/FindingChips';
import { C } from '@/utils/colors';
import { fmtScore } from '@/utils/helpers';
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
  const statusColor = run.status === 'OK' ? C.green : run.status === 'ERROR' ? C.red : C.fg1;
  return (
    <button
      data-run-id={run.id}
      className="w-full rounded-lg p-2.5 text-left transition-all duration-150"
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
      <div className="flex items-start gap-2">
        <div
          className="mt-1.5 size-2 shrink-0 rounded-full"
          style={{ background: statusColor }}
          title={run.status}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-medium" style={{ color: C.fg4 }}>
              {run.scenario}@{run.revision}
            </span>
            {run.synthetic && <Badge label="synthetic" />}
          </div>
          <div className="num mt-0.5 truncate text-[10px]" style={{ color: C.fg0 }}>
            {run.id}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span
              className="num truncate rounded px-1 text-[10px]"
              style={{ color: C.cyan, background: `${C.cyan}10` }}
            >
              {run.agent_spec}
            </span>
            <span className="num text-[10px]" style={{ color: C.accent }} title="Composite score">
              {fmtScore(run.composite_score)}
            </span>
            <FindingChips counts={run.finding_counts} />
          </div>
        </div>
      </div>
    </button>
  );
}
