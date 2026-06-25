import { ChevronRight } from 'lucide-react';
import { C } from '@/utils/colors';
import { runLabel, scoreTier } from '@/utils/verdict';
import type { RunRecord } from '@/utils/types';

function outcomeColor(score: number | null): string {
  if (score == null) return C.fg1;
  if (score >= 0.9) return C.green;
  if (score >= 0.75) return C.orange;
  return C.red;
}

function OutcomeRing({ score }: { score: number | null }) {
  const normalized = score == null ? 0 : Math.max(0, Math.min(1, score));
  const color = outcomeColor(score);
  return (
    <span
      className="inline-flex size-4 shrink-0 rounded-full"
      style={{
        background: `conic-gradient(${color} ${normalized * 360}deg, rgba(255,255,255,0.16) 0deg)`,
      }}
      aria-label="Outcome"
    >
      <span className="m-[3px] flex-1 rounded-full" style={{ background: C.surface }} />
    </span>
  );
}

function OutcomeMovement({ before, after }: { before: number | null; after: number | null }) {
  if (before == null || after == null) {
    return (
      <span className="num text-[10px] font-semibold" style={{ color: C.fg1 }}>
        -
      </span>
    );
  }
  const delta = after - before;
  if (delta === 0) {
    return (
      <span className="num text-[10px] font-semibold" style={{ color: C.fg1 }}>
        -
      </span>
    );
  }
  return (
    <span
      className="num inline-flex items-center gap-0.5 text-[10px] font-semibold"
      style={{ color: delta > 0 ? C.green : C.red }}
    >
      <span className="text-[13px] leading-none" aria-hidden>
        {delta > 0 ? '↑' : '↓'}
      </span>
      <span>
        {delta > 0 ? '+' : ''}
        {delta.toFixed(3)}
      </span>
    </span>
  );
}

export function WireframeRunListItem({
  run,
  previousRun,
  selected,
  onClick,
}: {
  run: RunRecord;
  previousRun?: RunRecord;
  selected: boolean;
  onClick: () => void;
}) {
  const score = run.unscored ? null : run.composite_score;
  const tier = scoreTier(score);
  const previousScore = previousRun && !previousRun.unscored ? previousRun.composite_score : null;

  return (
    <button
      data-run-id={run.id}
      title={`${run.id} — ${run.scenario} ${run.revision} — ${tier.label}`}
      className="w-full rounded-md px-2.5 py-2 text-left transition"
      style={{
        background: selected ? 'rgba(255,255,255,0.03)' : 'transparent',
        border: selected ? `1px solid ${C.selectedBorder}` : '1px solid transparent',
      }}
      onMouseEnter={(e) => {
        if (!selected) {
          e.currentTarget.style.background = 'rgba(255,255,255,0.04)';
        }
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = selected ? 'rgba(255,255,255,0.03)' : 'transparent';
      }}
      onClick={onClick}
    >
      <div className="flex items-center gap-2">
        <span
          className="size-2 shrink-0 rounded-full"
          style={{ background: tier.color }}
          aria-hidden
        />
        <span className="min-w-0 flex-1 truncate text-xs font-medium" style={{ color: C.fg4 }}>
          {runLabel(run.id)}
        </span>
        <span className="ml-auto inline-flex items-center justify-end gap-2">
          <OutcomeMovement before={previousScore} after={score} />
          <OutcomeRing score={score} />
        </span>
        <ChevronRight className="size-3.5" style={{ color: selected ? C.fg5 : C.fg1 }} />
      </div>
    </button>
  );
}
