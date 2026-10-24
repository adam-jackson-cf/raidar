// Head-to-head checkpoint scatter (docs/reboot/h2h-signals.md): lint findings
// and functions with CC > 10, arm A on x and arm B on y, one point per
// checkpoint (median across attempts), dashed y = x parity. Below the line, B
// is better. Plotted and excluded counts are always shown.
import { C } from '@/presentation/colors';
import type { HeadToHead, HeadToHeadSignal } from '@/quality/types';

const SIZE = 220;
const PAD = 30;

function Scatter({ signal, armA, armB }: { signal: HeadToHeadSignal; armA: string; armB: string }) {
  const max = Math.max(1, ...signal.points.flatMap((point) => [point.a, point.b]));
  const scale = (value: number) => (value / max) * (SIZE - PAD - 10);
  const px = (value: number) => PAD + scale(value);
  const py = (value: number) => SIZE - PAD - scale(value);
  return (
    <figure className="min-w-0" data-testid="h2h-scatter" data-signal={signal.signal}>
      <figcaption className="text-[11px] font-semibold" style={{ color: C.fg3 }}>
        {signal.label}
      </figcaption>
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="w-full max-w-[260px]" role="img" aria-label={`${signal.label}: ${armA} versus ${armB}`}>
        <line x1={PAD} y1={SIZE - PAD} x2={PAD} y2={10} stroke={C.borderLight} />
        <line x1={PAD} y1={SIZE - PAD} x2={SIZE - 10} y2={SIZE - PAD} stroke={C.borderLight} />
        <line x1={px(0)} y1={py(0)} x2={px(max)} y2={py(max)} stroke={C.fg1} strokeDasharray="4 3" />
        <text x={PAD - 4} y={py(max) + 3} textAnchor="end" fontSize="8" fill={C.fg1}>
          {max}
        </text>
        <text x={px(max)} y={SIZE - PAD + 10} textAnchor="middle" fontSize="8" fill={C.fg1}>
          {max}
        </text>
        <text x={(SIZE + PAD) / 2} y={SIZE - 4} textAnchor="middle" fontSize="9" fill={C.fg2}>
          {armA} ({signal.axis})
        </text>
        <text x={10} y={(SIZE - PAD) / 2} textAnchor="middle" fontSize="9" fill={C.fg2} transform={`rotate(-90 10 ${(SIZE - PAD) / 2})`}>
          {armB} ({signal.axis})
        </text>
        {signal.points.map((point) => (
          <g key={point.checkpoint} data-testid="h2h-point">
            <circle cx={px(point.a)} cy={py(point.b)} r={4} fill="var(--q-series-1)" stroke={C.surface} strokeWidth={2} />
            <circle cx={px(point.a)} cy={py(point.b)} r={9} fill="transparent">
              <title>{`${point.checkpoint}: ${armA} ${point.a} (n=${point.n_a}), ${armB} ${point.b} (n=${point.n_b})`}</title>
            </circle>
          </g>
        ))}
      </svg>
      <p className="text-[10px]" style={{ color: C.fg2 }} data-testid="h2h-counts" title={signal.excluded.join('\n') || undefined}>
        {signal.points.length} plotted · {signal.excluded.length} excluded
      </p>
      <p className="text-[9px]" style={{ color: C.fg1 }}>
        {signal.engine}
      </p>
    </figure>
  );
}

export function HeadToHeadScatter({ headToHead }: { headToHead: HeadToHead | null }) {
  if (!headToHead) {
    return (
      <p className="text-[11px]" style={{ color: C.fg1 }}>
        Head-to-head needs two arms with measured checkpoints.
      </p>
    );
  }
  return (
    <div>
      <p className="mb-2 text-[10px]" style={{ color: C.fg1 }}>
        {headToHead.arm_a} vs {headToHead.arm_b}, median across attempts per checkpoint. Lower is better; points below the dashed line favour {headToHead.arm_b}.
      </p>
      <div className="grid gap-6 sm:grid-cols-2">
        {headToHead.signals.map((signal) => (
          <Scatter key={signal.signal} signal={signal} armA={headToHead.arm_a} armB={headToHead.arm_b} />
        ))}
      </div>
    </div>
  );
}
