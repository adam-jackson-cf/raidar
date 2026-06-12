import { C } from '@/utils/colors';

export interface RadarProfile {
  label: string;
  color: string;
  values: Array<number | null>;
}

/**
 * Five-axis radar for the detail view only (never the board). Overlays at most
 * two profiles, per the review-surface visual rules.
 */
export function RadarChart({
  axes,
  profiles,
  size = 240,
}: {
  axes: string[];
  profiles: RadarProfile[];
  size?: number;
}) {
  const center = size / 2;
  const radius = size / 2 - 36;
  const shown = profiles.slice(0, 2);

  const point = (index: number, value: number) => {
    const angle = (Math.PI * 2 * index) / axes.length - Math.PI / 2;
    return [center + Math.cos(angle) * radius * value, center + Math.sin(angle) * radius * value];
  };

  const polygon = (values: Array<number | null>) =>
    values
      .map((value, index) => point(index, Math.max(value ?? 0, 0.02)))
      .map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`)
      .join(' ');

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={`Dimension radar: ${shown.map((p) => p.label).join(' vs ')}`}
    >
      {[0.25, 0.5, 0.75, 1].map((ring) => (
        <polygon
          key={ring}
          points={polygon(axes.map(() => ring))}
          fill="none"
          stroke={C.border}
          strokeWidth={1}
        />
      ))}
      {axes.map((axis, index) => {
        const [x, y] = point(index, 1);
        const [lx, ly] = point(index, 1.18);
        return (
          <g key={axis}>
            <line x1={center} y1={center} x2={x} y2={y} stroke={C.border} strokeWidth={1} />
            <text
              x={lx}
              y={ly}
              textAnchor="middle"
              dominantBaseline="middle"
              fontSize={9}
              fill={C.fg1}
            >
              {axis}
            </text>
          </g>
        );
      })}
      {shown.map((profile) => (
        <g key={profile.label}>
          <polygon
            points={polygon(profile.values)}
            fill={profile.color}
            fillOpacity={0.12}
            stroke={profile.color}
            strokeWidth={1.5}
          />
          {profile.values.map((value, index) => {
            if (value == null) return null;
            const [x, y] = point(index, Math.max(value, 0.02));
            return <circle key={index} cx={x} cy={y} r={2.5} fill={profile.color} />;
          })}
        </g>
      ))}
    </svg>
  );
}
