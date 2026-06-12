import { C } from '@/utils/colors';

export interface DeltaBarEntry {
  label: string;
  current: number | null;
  comparator: number | null;
}

/** Exact labeled deltas paired with the radar, per the visual rules. */
export function DeltaBars({ entries, comparatorLabel }: { entries: DeltaBarEntry[]; comparatorLabel: string }) {
  const max = 0.6;
  return (
    <div className="flex flex-col gap-1.5">
      {entries.map((entry) => {
        const delta =
          entry.current != null && entry.comparator != null ? entry.current - entry.comparator : null;
        const width = delta != null ? Math.min(Math.abs(delta) / max, 1) * 70 : 0;
        const color = delta == null ? C.fg0 : delta > 0.001 ? C.green : delta < -0.001 ? C.orange : C.fg1;
        return (
          <div key={entry.label} className="flex items-center gap-2">
            <span className="w-36 shrink-0 text-[10px]" style={{ color: C.fg2 }}>
              {entry.label}
            </span>
            <div className="relative h-[6px] w-[150px]" style={{ background: 'rgba(255,255,255,0.04)' }}>
              <div className="absolute inset-y-0 left-1/2 w-px" style={{ background: C.borderLight }} />
              {delta != null && (
                <div
                  className="absolute inset-y-0 rounded-sm"
                  style={{
                    background: color,
                    width: `${width}px`,
                    left: delta >= 0 ? '50%' : `calc(50% - ${width}px)`,
                  }}
                />
              )}
            </div>
            <span className="num w-20 text-[10px]" style={{ color }}>
              {delta == null ? '—' : `${delta >= 0 ? '+' : ''}${delta.toFixed(2)}`}
            </span>
            <span className="num text-[9px]" style={{ color: C.fg0 }}>
              {entry.current?.toFixed(2) ?? '—'} vs {entry.comparator?.toFixed(2) ?? '—'} {comparatorLabel}
            </span>
          </div>
        );
      })}
    </div>
  );
}
