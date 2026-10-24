// Component curves per arm over checkpoints: one small multiple per component,
// mean line with a 95% interval band across attempts (WS-J's paired t / bootstrap,
// as the analysis curves use). Hover a point for n, mean, SD and the interval.
import { C } from '@/presentation/colors';
import { COMPONENT_INFO, armColor, fmtComponent } from '@/quality/format';
import { COMPONENTS, type Component, type CurvePoint } from '@/quality/types';

const W = 200;
const H = 104;
const PAD = { left: 26, right: 8, top: 8, bottom: 18 };

function Multiple({
  component,
  arms,
  curves,
  checkpoints,
}: {
  component: Component;
  arms: string[];
  curves: Record<string, Record<Component, CurvePoint[]>>;
  checkpoints: string[];
}) {
  const top = component === 'S' ? 100 : 1;
  const x = (index: number) =>
    PAD.left + (checkpoints.length > 1 ? (index / (checkpoints.length - 1)) * (W - PAD.left - PAD.right) : 0);
  const y = (value: number) => PAD.top + (1 - Math.max(0, Math.min(top, value)) / top) * (H - PAD.top - PAD.bottom);
  const info = COMPONENT_INFO[component];
  return (
    <figure className="min-w-0" data-testid="component-curve" data-component={component}>
      <figcaption className="mb-0.5 text-[10px] font-semibold" style={{ color: C.fg3 }} title={info.definition}>
        {component} <span className="font-normal" style={{ color: C.fg1 }}>{info.name}</span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={`${info.name} by checkpoint per arm`}>
        {[0, 0.5, 1].map((tick) => (
          <g key={tick}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(tick * top)} y2={y(tick * top)} stroke="var(--q-grid)" />
            <text x={PAD.left - 4} y={y(tick * top) + 3} textAnchor="end" fontSize="8" fill={C.fg1}>
              {component === 'S' ? tick * 100 : tick}
            </text>
          </g>
        ))}
        {checkpoints.map((checkpoint, index) => (
          <text key={checkpoint} x={x(index)} y={H - 5} textAnchor="middle" fontSize="8" fill={C.fg1}>
            {index + 1}
          </text>
        ))}
        {arms.map((arm) => {
          const points = (curves[arm]?.[component] ?? []).map((point, index) => ({ point, index }));
          const measured = points.filter(({ point }) => point.mean != null);
          const color = armColor(arms, arm);
          const line = measured.map(({ point, index }) => `${x(index)},${y(point.mean ?? 0)}`).join(' ');
          const band = [
            ...measured.map(({ point, index }) => `${x(index)},${y(point.high ?? 0)}`),
            ...[...measured].reverse().map(({ point, index }) => `${x(index)},${y(point.low ?? 0)}`),
          ].join(' ');
          return (
            <g key={arm}>
              {measured.length > 1 && <polygon points={band} fill={color} opacity={0.12} />}
              {measured.length > 1 && <polyline points={line} fill="none" stroke={color} strokeWidth={2} />}
              {measured.map(({ point, index }) => (
                <g key={index}>
                  <circle cx={x(index)} cy={y(point.mean ?? 0)} r={2.5} fill={color} stroke={C.surface} strokeWidth={1} />
                  <circle cx={x(index)} cy={y(point.mean ?? 0)} r={7} fill="transparent">
                    <title>
                      {`${arm} · ${point.checkpoint}: mean ${fmtComponent(component, point.mean)}${
                        point.sd != null ? `, SD ${fmtComponent(component, point.sd)}` : ''
                      }${
                        point.low != null && point.high != null
                          ? `, 95% CI [${fmtComponent(component, point.low)}, ${fmtComponent(component, point.high)}]`
                          : ''
                      } (n=${point.n})`}
                    </title>
                  </circle>
                </g>
              ))}
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

export function ArmLegend({ arms, baseline }: { arms: string[]; baseline: string | null }) {
  return (
    <ul className="flex flex-wrap gap-3" aria-label="Arms">
      {arms.map((arm) => (
        <li key={arm} className="inline-flex items-center gap-1.5 text-[11px]" style={{ color: C.fg3 }}>
          <span className="inline-block h-0.5 w-3 rounded" style={{ background: armColor(arms, arm) }} />
          {arm}
          {arm === baseline && <span style={{ color: C.fg1 }}>(baseline)</span>}
        </li>
      ))}
    </ul>
  );
}

export function ComponentCurves({
  arms,
  curves,
  checkpoints,
}: {
  arms: string[];
  curves: Record<string, Record<Component, CurvePoint[]>>;
  checkpoints: string[];
}) {
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
      {COMPONENTS.map((component) => (
        <Multiple key={component} component={component} arms={arms} curves={curves} checkpoints={checkpoints} />
      ))}
    </div>
  );
}
