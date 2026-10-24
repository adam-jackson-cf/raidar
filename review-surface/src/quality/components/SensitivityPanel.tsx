// Sensitivity of the headline score: weight ablation (mean S lost if a
// component's weight were zero) and component correlation (a pair near ±1
// suggests one component is redundant).
import { C } from '@/presentation/colors';
import { COMPONENT_INFO } from '@/quality/format';
import { COMPONENTS, type Component, type Sensitivity } from '@/quality/types';

function correlationColor(r: number): string {
  const pole = r >= 0 ? 'var(--q-diverge-pos)' : 'var(--q-diverge-neg)';
  return `color-mix(in srgb, ${pole} ${Math.round(Math.abs(r) * 100)}%, ${C.subtleStrong})`;
}

export function SensitivityPanel({ sensitivity }: { sensitivity: Sensitivity }) {
  const components = COMPONENTS.filter((component) => component in sensitivity.weights) as Component[];
  const maxLoss = Math.max(...Object.values(sensitivity.ablation), 0.001);
  const lookup = new Map(
    sensitivity.correlations.flatMap((item) => [
      [`${item.a}/${item.b}`, item],
      [`${item.b}/${item.a}`, item],
    ]),
  );
  return (
    <div className="grid gap-6 md:grid-cols-2" data-testid="sensitivity">
      <figure>
        <figcaption className="mb-1 text-[11px] font-semibold" style={{ color: C.fg3 }}>
          Weight ablation <span className="font-normal" style={{ color: C.fg1 }}>(mean S points lost, n={sensitivity.n})</span>
        </figcaption>
        {sensitivity.n === 0 && (
          <p className="text-[11px]" style={{ color: C.fg1 }}>
            No trial has an eligible headline score yet.
          </p>
        )}
        <ul hidden={sensitivity.n === 0}>
          {components.map((component) => {
            const loss = sensitivity.ablation[component] ?? 0;
            return (
              <li key={component} className="flex items-center gap-2 py-0.5" data-testid="ablation-row">
                <span className="w-24 text-[10px]" style={{ color: C.fg2 }} title={COMPONENT_INFO[component].definition}>
                  {component} {COMPONENT_INFO[component].name}
                </span>
                <span className="h-2 flex-1 rounded-sm" style={{ background: C.subtle }}>
                  <span
                    className="block h-full rounded-sm"
                    style={{ width: `${(loss / maxLoss) * 100}%`, background: 'var(--q-series-1)' }}
                  />
                </span>
                <span className="num w-20 text-right text-[10px]" style={{ color: C.fg3 }}>
                  {loss.toFixed(2)} · w {sensitivity.weights[component]}
                </span>
              </li>
            );
          })}
        </ul>
      </figure>
      <figure>
        <figcaption className="mb-1 text-[11px] font-semibold" style={{ color: C.fg3 }}>
          Component correlation <span className="font-normal" style={{ color: C.fg1 }}>(Pearson r over trials)</span>
        </figcaption>
        <table className="border-collapse" data-testid="correlation-matrix">
          <thead>
            <tr>
              <th />
              {components.map((component) => (
                <th key={component} className="px-1 text-[10px] font-semibold" style={{ color: C.fg2 }}>
                  {component}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {components.map((row, rowIndex) => (
              <tr key={row}>
                <th scope="row" className="pr-1 text-[10px] font-semibold" style={{ color: C.fg2 }}>
                  {row}
                </th>
                {components.map((column, columnIndex) => {
                  if (columnIndex <= rowIndex) return <td key={column} className="size-9" />;
                  const item = lookup.get(`${row}/${column}`);
                  const r = item?.r ?? null;
                  return (
                    <td
                      key={column}
                      className="num size-9 text-center text-[9px]"
                      style={{ background: r == null ? C.subtle : correlationColor(r), color: C.fg4, border: `2px solid ${C.surface}` }}
                      title={r == null ? `${row}–${column}: ${item?.reason ?? 'not computed'}` : `${row}–${column}: r = ${r.toFixed(3)}`}
                    >
                      {r == null ? '—' : r.toFixed(2)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </figure>
      {sensitivity.excluded.length > 0 && (
        <p className="text-[10px] md:col-span-2" style={{ color: C.fg1 }} title={sensitivity.excluded.join('\n')}>
          {sensitivity.excluded.length} trial{sensitivity.excluded.length === 1 ? '' : 's'} left out: headline score not eligible.
        </p>
      )}
    </div>
  );
}
