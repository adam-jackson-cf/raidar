// Paired Δ per component vs the baseline arm: within-block differences, mean
// with a 95% paired-t interval, and a verdict only when the interval excludes 0.
import { C } from '@/utils/colors';
import { COMPONENT_INFO, VERDICT_STYLE, fmtSigned } from '@/quality/format';
import type { PairedDelta } from '@/quality/types';

function Interval({ delta, scale }: { delta: PairedDelta; scale: number }) {
  const width = 96;
  const position = (value: number) => width / 2 + (Math.max(-scale, Math.min(scale, value)) / scale) * (width / 2);
  return (
    <svg width={width} height={12} aria-hidden>
      <line x1={width / 2} x2={width / 2} y1={0} y2={12} stroke={C.borderLight} />
      {delta.ci && (
        <line x1={position(delta.ci[0])} x2={position(delta.ci[1])} y1={6} y2={6} stroke={C.fg2} strokeWidth={2} strokeLinecap="round" />
      )}
      {delta.mean != null && <circle cx={position(delta.mean)} cy={6} r={3} fill={VERDICT_STYLE[delta.verdict].color} />}
    </svg>
  );
}

export function PairedDeltaTable({ paired, baseline }: { paired: PairedDelta[]; baseline: string | null }) {
  if (paired.length === 0) {
    return (
      <p className="text-[11px]" style={{ color: C.fg1 }}>
        {baseline ? 'Only the baseline arm has trials; add another arm to compare.' : 'No baseline arm is declared, so there is nothing to pair against.'}
      </p>
    );
  }
  const arms = [...new Set(paired.map((item) => item.arm))];
  return (
    <table className="w-full border-collapse text-left" data-testid="paired-delta">
      <thead>
        <tr style={{ borderBottom: `1px solid ${C.border}` }}>
          <th className="px-2 py-1 text-[10px] font-medium" style={{ color: C.fg1 }}>
            Component
          </th>
          <th className="px-2 py-1 text-[10px] font-medium" style={{ color: C.fg1 }}>
            Arm
          </th>
          <th className="px-2 py-1 text-right text-[10px] font-medium" style={{ color: C.fg1 }} title="Mean within-block difference (arm − baseline)">
            Δ
          </th>
          <th className="px-2 py-1 text-[10px] font-medium" style={{ color: C.fg1 }} title="95% paired-t interval; the centre line is 0">
            95% CI
          </th>
          <th className="px-2 py-1 text-[10px] font-medium" style={{ color: C.fg1 }}>
            Verdict
          </th>
          <th className="px-2 py-1 text-right text-[10px] font-medium" style={{ color: C.fg1 }} title="Blocks paired / blocks left out">
            Pairs
          </th>
        </tr>
      </thead>
      <tbody>
        {arms.flatMap((arm) =>
          paired
            .filter((item) => item.arm === arm)
            .map((item) => {
              const verdict = VERDICT_STYLE[item.verdict];
              const scale = Math.max(
                item.component === 'S' ? 10 : 0.1,
                ...(item.ci ?? []).map(Math.abs),
                Math.abs(item.mean ?? 0),
              );
              return (
                <tr key={`${arm}-${item.component}`} data-testid="paired-row" style={{ borderBottom: `1px solid ${C.rowBorder}` }}>
                  <th scope="row" className="px-2 py-1 text-[11px] font-semibold" style={{ color: C.fg3 }} title={COMPONENT_INFO[item.component].definition}>
                    {item.component} <span className="font-normal" style={{ color: C.fg1 }}>{COMPONENT_INFO[item.component].name}</span>
                  </th>
                  <td className="px-2 py-1 text-[11px]" style={{ color: C.fg2 }}>
                    {arm}
                  </td>
                  <td className="num px-2 py-1 text-right text-[11px]" style={{ color: C.fg4 }}>
                    {fmtSigned(item.component, item.mean)}
                  </td>
                  <td className="px-2 py-1" title={item.ci ? `[${fmtSigned(item.component, item.ci[0])}, ${fmtSigned(item.component, item.ci[1])}]` : 'needs 2+ pairs'}>
                    <Interval delta={item} scale={scale} />
                  </td>
                  <td className="px-2 py-1 text-[11px]">
                    <span className="inline-flex items-center gap-1" style={{ color: verdict.color }}>
                      <span aria-hidden>{verdict.glyph}</span>
                      {verdict.label}
                    </span>
                  </td>
                  <td className="num px-2 py-1 text-right text-[11px]" style={{ color: C.fg2 }} title={item.excluded.join('\n') || undefined}>
                    {item.n}
                    {item.excluded.length > 0 && <span style={{ color: C.fg0 }}> / −{item.excluded.length}</span>}
                  </td>
                </tr>
              );
            }),
        )}
      </tbody>
    </table>
  );
}
