// SCB-dashboard distributions per arm (lint density, CC-mass concentration,
// function LOC): a box (Q1–Q3, median) with whiskers to min/max and every
// checkpoint value as a dot. The source line names how each was measured.
import { C } from '@/utils/colors';
import { armColor } from '@/quality/format';
import type { SignalDistributions } from '@/quality/types';

const W = 280;
const ROW = 22;
const PAD_LEFT = 64;

function fmt(value: number): string {
  return Math.abs(value) >= 10 ? value.toFixed(0) : value.toFixed(2);
}

function Signal({ signal, arms }: { signal: SignalDistributions; arms: string[] }) {
  const all = signal.arms.flatMap((arm) => arm.values);
  const max = Math.max(...all, 0) || 1;
  const x = (value: number) => PAD_LEFT + (value / max) * (W - PAD_LEFT - 12);
  const height = signal.arms.length * ROW + 16;
  return (
    <figure className="min-w-0" data-testid="distribution" data-signal={signal.signal}>
      <figcaption className="text-[11px] font-semibold" style={{ color: C.fg3 }}>
        {signal.label} <span className="font-normal" style={{ color: C.fg1 }}>({signal.unit})</span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${height}`} className="w-full" role="img" aria-label={`${signal.label} distribution per arm`}>
        {signal.arms.map((dist, row) => {
          const cy = row * ROW + ROW / 2;
          const color = armColor(arms, dist.arm);
          const q = dist.quantiles;
          return (
            <g key={dist.arm}>
              <text x={PAD_LEFT - 6} y={cy + 3} textAnchor="end" fontSize="8" fill={C.fg2}>
                {dist.arm}
              </text>
              {q ? (
                <>
                  <line x1={x(q[0])} x2={x(q[4])} y1={cy} y2={cy} stroke={C.fg1} />
                  <rect x={x(q[1])} y={cy - 6} width={Math.max(2, x(q[3]) - x(q[1]))} height={12} rx={2} fill={color} opacity={0.25} stroke={color} />
                  <line x1={x(q[2])} x2={x(q[2])} y1={cy - 7} y2={cy + 7} stroke={color} strokeWidth={2} />
                  {dist.values.map((value, index) => (
                    <circle key={index} cx={x(value)} cy={cy + ((index % 5) - 2) * 1.5} r={1.6} fill={color} opacity={0.7} />
                  ))}
                  <rect x={PAD_LEFT} y={cy - ROW / 2} width={W - PAD_LEFT} height={ROW} fill="transparent">
                    <title>{`${dist.arm}: n=${dist.n}, min ${fmt(q[0])}, median ${fmt(q[2])}, max ${fmt(q[4])}`}</title>
                  </rect>
                </>
              ) : (
                <text x={PAD_LEFT} y={cy + 3} fontSize="9" fill={C.fg1}>
                  not measured
                </text>
              )}
            </g>
          );
        })}
        <text x={PAD_LEFT} y={height - 2} fontSize="8" fill={C.fg1}>
          0
        </text>
        <text x={W - 12} y={height - 2} textAnchor="end" fontSize="8" fill={C.fg1}>
          {fmt(max)}
        </text>
      </svg>
      <p className="text-[9px]" style={{ color: C.fg1 }}>
        {signal.source}
      </p>
    </figure>
  );
}

export function Distributions({ distributions, arms }: { distributions: SignalDistributions[]; arms: string[] }) {
  return (
    <div className="grid gap-4 md:grid-cols-3">
      {distributions.map((signal) => (
        <Signal key={signal.signal} signal={signal} arms={arms} />
      ))}
    </div>
  );
}
