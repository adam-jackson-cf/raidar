import { C } from '@/utils/colors';
import type { DimensionDelta, DimensionScore } from '@/utils/review-types';

export function scoreColor(score: number): string {
  if (score >= 0.85) return C.green;
  if (score >= 0.7) return C.accent;
  if (score >= 0.5) return C.orange;
  return C.red;
}

/**
 * One aligned dimension cell: fixed-scale bar for the current score, with the
 * benchmark delta rendered separately beneath it (never folded into the bar).
 */
export function DimensionCell({
  score,
  capped,
  delta,
  compact,
}: {
  score: number | null;
  capped?: string[];
  delta?: DimensionDelta | null;
  compact?: boolean;
}) {
  if (score == null) {
    return (
      <span className="text-[10px]" style={{ color: C.fg0 }}>
        Unavailable
      </span>
    );
  }
  const color = scoreColor(score);
  const showDelta = delta && delta.delta != null && delta.band !== 'Unavailable';
  return (
    <div className="flex flex-col gap-0.5" title={capped?.length ? `Capped: ${capped.join('; ')}` : undefined}>
      <div className="flex items-center gap-1.5">
        <div
          className="h-[5px] overflow-hidden rounded-full"
          style={{ width: compact ? 48 : 64, background: 'rgba(255,255,255,0.06)' }}
        >
          <div
            className="h-full rounded-full"
            style={{ width: `${Math.round(score * 100)}%`, background: color }}
          />
        </div>
        <span className="num text-[11px]" style={{ color }}>
          {score.toFixed(2)}
          {capped?.length ? '†' : ''}
        </span>
      </div>
      {showDelta && (
        <span
          className="num text-[9px]"
          style={{
            color:
              delta.band === 'Ahead' ? C.green : delta.band === 'Behind' ? C.orange : C.fg0,
          }}
          title={`Benchmark delta: ${delta.band}`}
        >
          {delta.delta! >= 0 ? '+' : ''}
          {delta.delta!.toFixed(2)} vs bench
        </span>
      )}
    </div>
  );
}
