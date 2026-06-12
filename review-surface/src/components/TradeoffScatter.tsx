// Score-vs-duration tradeoff scatter adapted from the deprecated
// benchmark-view: fast, acceptable runs sit top-left; failures and slow
// outliers separate visually. One point per run, colored by AgentSpec.
import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { C } from '@/utils/colors';
import { fmtDuration, fmtScore } from '@/utils/helpers';
import type { RunRecord } from '@/utils/types';

const SPEC_COLORS = [C.accent, C.cyan, C.purple, C.orange, '#8BC34A', '#F5CE4E'];
const WIDTH = 560;
const HEIGHT = 190;
const PAD = { left: 42, right: 12, top: 12, bottom: 26 };

export function TradeoffScatter({ runs }: { runs: RunRecord[] }) {
  const navigate = useNavigate();
  const points = useMemo(
    () => runs.filter((run) => run.composite_score != null && run.duration_ms > 0),
    [runs],
  );
  const specs = useMemo(() => [...new Set(points.map((run) => run.agent_spec))], [points]);
  if (points.length < 2) return null;

  const maxDuration = Math.max(...points.map((run) => run.duration_ms)) * 1.08;
  const x = (run: RunRecord) =>
    PAD.left + (run.duration_ms / maxDuration) * (WIDTH - PAD.left - PAD.right);
  const y = (run: RunRecord) =>
    PAD.top + (1 - (run.composite_score ?? 0)) * (HEIGHT - PAD.top - PAD.bottom);
  const specColor = (spec: string) => SPEC_COLORS[specs.indexOf(spec) % SPEC_COLORS.length];

  return (
    <div
      className="flex flex-col gap-1.5 rounded-lg p-2.5"
      style={{ background: C.surface, border: `1px solid ${C.border}` }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium" style={{ color: C.fg3 }}>
          Score against run time
        </span>
        <span className="text-[10px]" style={{ color: C.fg0 }}>
          top-left is fast and good · red ring = failed run · click a point to open the run
        </span>
        <span className="ml-auto flex flex-wrap items-center gap-2">
          {specs.map((spec) => (
            <span key={spec} className="num inline-flex items-center gap-1 text-[10px]" style={{ color: C.fg1 }}>
              <span className="size-2 rounded-full" style={{ background: specColor(spec) }} />
              {spec}
            </span>
          ))}
        </span>
      </div>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="w-full max-w-2xl"
        role="img"
        aria-label="Composite score against run duration"
      >
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
          const ty = PAD.top + (1 - tick) * (HEIGHT - PAD.top - PAD.bottom);
          return (
            <g key={tick}>
              <line x1={PAD.left} x2={WIDTH - PAD.right} y1={ty} y2={ty} stroke="rgba(255,255,255,0.05)" />
              <text x={PAD.left - 6} y={ty + 3} textAnchor="end" fontSize="8" fill={C.fg0}>
                {tick.toFixed(2)}
              </text>
            </g>
          );
        })}
        <text
          x={(WIDTH - PAD.right + PAD.left) / 2}
          y={HEIGHT - 8}
          textAnchor="middle"
          fontSize="8"
          fill={C.fg0}
        >
          duration → ({fmtDuration(maxDuration)} max)
        </text>
        {points.map((run) => (
          <circle
            key={run.id}
            cx={x(run)}
            cy={y(run)}
            r={4.5}
            fill={specColor(run.agent_spec)}
            fillOpacity={0.85}
            stroke={run.status === 'ERROR' ? C.red : 'rgba(0,0,0,0.6)'}
            strokeWidth={run.status === 'ERROR' ? 2 : 1}
            style={{ cursor: 'pointer' }}
            onClick={() => navigate(`/runs/${encodeURIComponent(run.id)}`)}
          >
            <title>
              {`${run.id} · ${run.agent_spec}\ncomposite ${fmtScore(run.composite_score)} · ${fmtDuration(run.duration_ms)} · ${run.status}`}
            </title>
          </circle>
        ))}
      </svg>
    </div>
  );
}
