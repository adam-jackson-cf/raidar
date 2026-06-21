import { useMemo, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { Pin, PinOff } from 'lucide-react';
import { C } from '@/utils/colors';
import { fmtDuration, fmtScore } from '@/utils/helpers';
import { runLabel } from '@/utils/verdict';
import type { RunRecord } from '@/utils/types';

const MODEL_COLORS = [C.accent, C.cyan, C.green, C.orange, C.purple, '#F5CE4E', '#E879F9', '#94A3B8'];
const WIDTH = 560;
const HEIGHT = 210;
const PAD = { left: 42, right: 14, top: 16, bottom: 28 };

type TooltipPayload = {
  id: string;
  x: number;
  y: number;
  title: string;
  lines: string[];
};

function reduxModelLabel(agentSpec: string) {
  const text = agentSpec.toLowerCase();
  const gpt = text.match(/gpt[\s-]?([0-9]+(?:\.[0-9]+)?)/);
  if (gpt?.[1]) return `GPT${gpt[1]}`;
  if (text.includes('sonnet')) return 'Sonnet';
  if (text.includes('opus')) return 'Opus';
  if (text.includes('haiku')) return 'Haiku';
  if (text.includes('claude')) return 'Claude';

  const [, model = agentSpec] = agentSpec.split('·').map((part) => part.trim());
  return model
    .replace(/^openai\s+/i, '')
    .replace(/^anthropic\s+/i, '')
    .replace(/[^a-z0-9.]+/gi, ' ')
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .join(' ');
}

function OverlayFrame({ payload, pinned, onPin, onClose, children }: { payload: TooltipPayload; pinned: boolean; onPin: () => void; onClose: () => void; children: ReactNode }) {
  return (
    <div
      className="fixed z-30 min-w-60 max-w-72 rounded-md border p-2.5 text-[11px]"
      style={{
        left: Math.min(payload.x + 14, window.innerWidth - 300),
        top: Math.min(payload.y + 14, window.innerHeight - 180),
        borderColor: C.selectedBorder,
        background: C.surface,
        color: C.fg3,
      }}
    >
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0 truncate text-[11px]" style={{ color: C.fg4 }}>
          {payload.title}
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            aria-label={pinned ? 'Unpin overlay' : 'Pin overlay'}
            className="inline-flex size-5 items-center justify-center rounded border border-white/20 text-[10px] leading-none"
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              onPin();
            }}
            title="[P]"
          >
            {pinned ? <Pin size={11} /> : <PinOff size={11} />}
          </button>
          <button
            type="button"
            aria-label="Close overlay"
            className="inline-flex size-5 items-center justify-center rounded border border-white/20 text-[11px] font-medium leading-none"
            onClick={(event) => {
              event.stopPropagation();
              onClose();
            }}
          >
            x
          </button>
        </div>
      </div>
      {children}
    </div>
  );
}

function TooltipContent({ payload }: { payload: TooltipPayload }) {
  return (
    <div className="space-y-1">
      {payload.lines.map((line) => (
        <div key={line} className="leading-relaxed" style={{ color: C.fg2 }}>
          {line}
        </div>
      ))}
    </div>
  );
}

function runSequence(run: RunRecord) {
  const match = run.id.match(/(\d+)$/);
  if (!match) return Number.MAX_SAFE_INTEGER;
  return Number(match[1]);
}

function bestRunForEachRevisionSpec(runs: RunRecord[]) {
  const grouped = new Map<string, RunRecord[]>();

  for (const run of runs) {
    const key = `${run.revision}|${run.agent_spec}`;
    const bucket = grouped.get(key) ?? [];
    bucket.push(run);
    grouped.set(key, bucket);
  }

  const selected = [] as RunRecord[];
  for (const bucket of grouped.values()) {
    const sorted = [...bucket].sort((left, right) => {
      const leftOutcome = left.composite_score ?? -1;
      const rightOutcome = right.composite_score ?? -1;
      if (leftOutcome !== rightOutcome) return rightOutcome - leftOutcome;
      const leftCost = (left.total_input_tokens ?? 0) + (left.total_output_tokens ?? 0);
      const rightCost = (right.total_input_tokens ?? 0) + (right.total_output_tokens ?? 0);
      if (leftCost !== rightCost) {
        return leftCost - rightCost;
      }
      if (left.duration_ms !== right.duration_ms) return left.duration_ms - right.duration_ms;
      return runSequence(left) - runSequence(right);
    });
    const best = sorted[0];
    if (best) {
      selected.push(best);
    }
  }

  return selected;
}

export function WireframeTradeoffScatter({ runs }: { runs: RunRecord[] }) {
  const navigate = useNavigate();
  const [tooltip, setTooltip] = useState<TooltipPayload | null>(null);
  const [pinnedTooltip, setPinnedTooltip] = useState<TooltipPayload | null>(null);
  const points = useMemo(
    () => bestRunForEachRevisionSpec(runs).filter((run) => run.composite_score != null && run.duration_ms > 0),
    [runs],
  );
  const modelLabels = useMemo(() => [...new Set(points.map((run) => reduxModelLabel(run.agent_spec)))], [points]);

  if (points.length < 2) return null;

  const maxDuration = Math.max(...points.map((run) => run.duration_ms)) * 1.08;
  const x = (run: RunRecord) => PAD.left + (run.duration_ms / maxDuration) * (WIDTH - PAD.left - PAD.right);
  const y = (run: RunRecord) => PAD.top + (1 - (run.composite_score ?? 0)) * (HEIGHT - PAD.top - PAD.bottom);
  const modelColor = (model: string) => MODEL_COLORS[modelLabels.indexOf(model) % MODEL_COLORS.length];
  const openTooltip = (payload: TooltipPayload) => {
    if (pinnedTooltip?.id === payload.id) return;
    setTooltip(payload);
  };
  const closeTooltip = () => setTooltip(null);
  const pinPayload = (payload: TooltipPayload) => {
    setTooltip(null);
    setPinnedTooltip((current) => (current?.id === payload.id ? null : payload));
  };
  const runPayload = (run: RunRecord, clientX: number, clientY: number): TooltipPayload => {
    const model = reduxModelLabel(run.agent_spec);
    return {
      id: `run:${run.id}`,
      x: clientX,
      y: clientY,
      title: model,
      lines: [
        `agent spec: ${run.agent_spec}`,
        `run: ${runLabel(run.id)}`,
        `outcome: ${fmtScore(run.composite_score)}`,
        `run time: ${fmtDuration(run.duration_ms)}`,
        `status: ${run.status}`,
        run.id,
      ],
    };
  };
  const legendPayload = (model: string, specs: string[], clientX: number, clientY: number): TooltipPayload => ({
    id: `legend:${model}`,
    x: clientX,
    y: clientY,
    title: model,
    lines: [`agent specs sharing this colour:`, ...specs],
  });

  return (
    <div className="flex flex-col gap-2 rounded-lg p-2.5" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium" style={{ color: C.fg3 }}>
          Outcome vs run time
        </span>
        <span className="ml-auto flex flex-wrap items-center justify-end gap-2">
          {modelLabels.map((model) => {
            const specs = [...new Set(points.filter((run) => reduxModelLabel(run.agent_spec) === model).map((run) => run.agent_spec))];
            const payloadAt = (event: { clientX: number; clientY: number }) => legendPayload(model, specs, event.clientX, event.clientY);

            return (
              <button
                key={model}
                type="button"
                className="num inline-flex items-center gap-1 rounded px-1 py-0.5 text-[10px] transition hover:bg-white/5"
                style={{ color: C.fg1 }}
                onMouseEnter={(event) => openTooltip(payloadAt(event))}
                onMouseMove={(event) => openTooltip(payloadAt(event))}
                onMouseLeave={closeTooltip}
                onClick={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  pinPayload(payloadAt(event));
                }}
              >
                <span className="size-2 rounded-full" style={{ background: modelColor(model) }} />
                {model}
              </button>
            );
          })}
        </span>
      </div>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full max-w-2xl" role="img" aria-label="Outcome against run duration">
        <polygon
          points={`${PAD.left},${PAD.top} ${WIDTH - PAD.right},${PAD.top} ${WIDTH - PAD.right},${PAD.top + 78} ${PAD.left},${HEIGHT - PAD.bottom - 4}`}
          fill="rgba(34,197,94,0.12)"
          stroke="rgba(34,197,94,0.22)"
          strokeWidth="1"
        />
        <text x={PAD.left + 4} y={PAD.top + 11} fontSize="8" fill={C.green}>
          attractive region
        </text>
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
        {[0.25, 0.5, 0.75, 1].map((frac) => {
          const tx = PAD.left + frac * (WIDTH - PAD.left - PAD.right);
          return (
            <g key={frac}>
              <line x1={tx} x2={tx} y1={PAD.top} y2={HEIGHT - PAD.bottom} stroke="rgba(255,255,255,0.04)" />
              <text x={tx} y={HEIGHT - PAD.bottom + 10} textAnchor="middle" fontSize="8" fill={C.fg0}>
                {fmtDuration(maxDuration * frac)}
              </text>
            </g>
          );
        })}
        <text x={(WIDTH - PAD.right + PAD.left) / 2} y={HEIGHT - 3} textAnchor="middle" fontSize="8" fill={C.fg0}>
          run time {'>'}
        </text>
        {points.map((run) => {
          const model = reduxModelLabel(run.agent_spec);

          return (
            <circle
              key={run.id}
              cx={x(run)}
              cy={y(run)}
              r={4.75}
              fill={modelColor(model)}
              fillOpacity={0.86}
              stroke={run.status === 'ERROR' ? C.red : 'rgba(0,0,0,0.6)'}
              strokeWidth={run.status === 'ERROR' ? 2 : 1}
              style={{ cursor: 'pointer' }}
              onMouseEnter={(event) => openTooltip(runPayload(run, event.clientX, event.clientY))}
              onMouseMove={(event) => openTooltip(runPayload(run, event.clientX, event.clientY))}
              onMouseLeave={closeTooltip}
              onClick={(event) => {
                if (event.altKey || event.metaKey || event.ctrlKey) {
                  pinPayload(runPayload(run, event.clientX, event.clientY));
                  return;
                }
                navigate(`/runs/${encodeURIComponent(run.id)}`);
              }}
            />
          );
        })}
      </svg>
      {tooltip ? (
        <OverlayFrame payload={tooltip} pinned={false} onPin={() => pinPayload(tooltip)} onClose={closeTooltip}>
          <TooltipContent payload={tooltip} />
        </OverlayFrame>
      ) : null}
      {pinnedTooltip ? (
        <OverlayFrame payload={pinnedTooltip} pinned onPin={() => setPinnedTooltip(null)} onClose={() => setPinnedTooltip(null)}>
          <TooltipContent payload={pinnedTooltip} />
        </OverlayFrame>
      ) : null}
    </div>
  );
}
