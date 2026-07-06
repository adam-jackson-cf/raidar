import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { Info, SlidersHorizontal } from 'lucide-react';
import { WireframeBarChart } from './WireframeBarChart';
import { C } from '@/utils/colors';
import { fmtDuration, fmtScore } from '@/utils/helpers';
import { runLabel } from '@/utils/verdict';
import type { RunRecord } from '@/utils/types';

const MODEL_COLORS = [C.accent, C.cyan, C.green, C.orange, C.purple, '#F5CE4E', '#E879F9', '#94A3B8'];
const WIDTH = 560;
const HEIGHT = 210;
const PAD = { left: 23, right: 0, top: 16, bottom: 28 };
const TOKEN_COLORS = { input: C.cyan, output: C.orange };

type TooltipPayload = {
  id: string;
  x: number;
  y: number;
  title: string;
  lines: string[];
};

function reduxModelLabel(agentSpec: string) {
  const text = agentSpec.toLowerCase();
  const [, model = text] = text.split('·').map((part) => part.trim());
  const gpt = text.match(/gpt[\s-]?([0-9]+(?:\.[0-9]+)?)/);
  if (gpt?.[1]) return `GPT${gpt[1]}`;
  const sonnet = model.match(/claude[-_ ]?sonnet[-_ ]?([0-9]+(?:\.[0-9]+)?)/);
  if (sonnet?.[1]) return `Sonnet ${sonnet[1]}`;
  if (text.includes('opus')) return 'Opus';
  if (text.includes('haiku')) return 'Haiku';
  if (text.includes('claude')) return 'Claude';

  const [, fallbackModel = agentSpec] = agentSpec.split('·').map((part) => part.trim());
  return fallbackModel
    .replace(/^openai\s+/i, '')
    .replace(/^anthropic\s+/i, '')
    .replace(/[^a-z0-9.]+/gi, ' ')
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .join(' ');
}

function OverlayFrame({
  payload,
  showControls = true,
  children,
}: {
  payload: TooltipPayload;
  showControls?: boolean;
  children: ReactNode;
}) {
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
        {showControls ? (
          <div className="ml-auto flex items-center gap-1.5">
            <button type="button" aria-label="Close overlay" className="inline-flex size-5 items-center justify-center rounded border text-[11px] font-medium leading-none">
              x
            </button>
          </div>
        ) : null}
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

function durationTickLabel(ms: number) {
  if (ms >= 60000) return `${Math.round(ms / 60000)}m`;
  if (ms >= 1000) return `${Math.round(ms / 1000)}s`;
  return `${Math.round(ms)}ms`;
}

function titleCase(value: string) {
  return value
    .split(/[^a-z0-9]+/i)
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1).toLowerCase())
    .join(' ');
}

function providerValue(run: RunRecord) {
  return run.model.includes('/') ? run.model.split('/')[0] : run.harness;
}

function providerLabel(value: string) {
  if (value.toLowerCase() === 'openai') return 'OpenAI';
  if (value.toLowerCase() === 'anthropic') return 'Anthropic';
  return titleCase(value);
}

function effortValue(run: RunRecord) {
  const source = `${run.model} ${run.agent_spec}`.toLowerCase();
  const namedEffort = source.match(/(?:reasoning|thinking)[-_\s:]+(low|medium|high)/i);
  const modelSuffix = run.model.match(/:(low|medium|high)$/i);
  return (namedEffort?.[1] ?? modelSuffix?.[1] ?? 'default').toLowerCase();
}

function effortLabel(value: string) {
  if (value === 'default') return 'None';
  return titleCase(value);
}

function revisionValue(run: RunRecord) {
  return run.revision ?? 'unknown';
}

function revisionLabel(value: string) {
  if (value === 'unknown') return 'unknown revision';
  return `revision ${value}`;
}

export function WireframeTradeoffScatter({ runs, borderless = false, showSubtitle = true }: { runs: RunRecord[]; borderless?: boolean; showSubtitle?: boolean }) {
  const navigate = useNavigate();
  const [tooltip, setTooltip] = useState<TooltipPayload | null>(null);
  const [filterOpen, setFilterOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<'runtime' | 'tokens'>('tokens');
  const [selectedProviders, setSelectedProviders] = useState<string[]>([]);
  const [selectedEfforts, setSelectedEfforts] = useState<string[]>([]);
  const [selectedRevisions, setSelectedRevisions] = useState<string[]>([]);
  const filterRef = useRef<HTMLDivElement | null>(null);
  const allPoints = useMemo(
    () => bestRunForEachRevisionSpec(runs).filter((run) => run.composite_score != null && run.duration_ms > 0),
    [runs],
  );
  const providerOptions = useMemo(() => [...new Set(allPoints.map(providerValue))], [allPoints]);
  const effortOptions = useMemo(() => [...new Set(allPoints.map(effortValue))], [allPoints]);
  const revisionOptions = useMemo(
    () => [...new Set(allPoints.map(revisionValue))].sort((left, right) => right.localeCompare(left)),
    [allPoints],
  );

  useEffect(() => {
    setSelectedProviders((current) => (current.length === 0 ? providerOptions : current.filter((item) => providerOptions.includes(item))));
  }, [providerOptions]);

  useEffect(() => {
    setSelectedEfforts((current) => (current.length === 0 ? effortOptions : current.filter((item) => effortOptions.includes(item))));
  }, [effortOptions]);

  useEffect(() => {
    setSelectedRevisions((current) => (current.length === 0 ? revisionOptions : current.filter((item) => revisionOptions.includes(item))));
  }, [revisionOptions]);

  useEffect(() => {
    if (!filterOpen) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (filterRef.current && !filterRef.current.contains(event.target as Node)) {
        setFilterOpen(false);
      }
    };
    document.addEventListener('mousedown', closeOnOutsideClick);
    return () => document.removeEventListener('mousedown', closeOnOutsideClick);
  }, [filterOpen]);

  const points = useMemo(
    () =>
      allPoints.filter((run) => {
        const providerSelected = selectedProviders.length === 0 || selectedProviders.includes(providerValue(run));
        const effortSelected = selectedEfforts.length === 0 || selectedEfforts.includes(effortValue(run));
        const revisionSelected = selectedRevisions.length === 0 || selectedRevisions.includes(revisionValue(run));
        return providerSelected && effortSelected && revisionSelected;
      }),
    [allPoints, selectedProviders, selectedEfforts, selectedRevisions],
  );
  const modelLabels = useMemo(() => [...new Set(points.map((run) => reduxModelLabel(run.agent_spec)))], [points]);

  if (allPoints.length < 1) return null;

  const hasChart = points.length >= 1;
  const maxDuration = (hasChart ? Math.max(...points.map((run) => run.duration_ms)) : 1) * 1.2;
  const attractorMaxX = WIDTH - PAD.right;
  const xTicks = [0.25, 0.5, 0.75, 1];
  const x = (run: RunRecord) => PAD.left + (run.duration_ms / maxDuration) * (WIDTH - PAD.left - PAD.right);
  const y = (run: RunRecord) => PAD.top + (1 - (run.composite_score ?? 0)) * (HEIGHT - PAD.top - PAD.bottom);
  const modelColor = (model: string) => MODEL_COLORS[modelLabels.indexOf(model) % MODEL_COLORS.length];
  const openTooltip = (payload: TooltipPayload) => setTooltip(payload);
  const closeTooltip = () => setTooltip(null);
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
    <div className="flex flex-col gap-2 rounded-lg p-2.5" style={{ background: C.surface, border: borderless ? '0' : `1px solid ${C.border}` }}>
      <div className="flex items-start gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            {[
              ['tokens', 'Outcome vs spend'],
              ['runtime', 'Outcome vs runtime'],
            ].map(([tab, label]) => (
              <button
                key={tab}
                type="button"
                aria-pressed={activeTab === tab}
                className="rounded-md px-2 py-1 text-xs font-medium transition "
                style={{
                  color: activeTab === tab ? C.fg4 : C.fg2,
                  background: activeTab === tab ? C.selected : C.subtle,
                  border: `1px solid ${activeTab === tab ? C.selectedBorder : C.border}`,
                }}
                onClick={() => setActiveTab(tab as 'runtime' | 'tokens')}
              >
                {label}
              </button>
            ))}
          </div>
          {showSubtitle ? (
            <p className="mt-0.5 text-[11px]" style={{ color: C.fg0 }}>
              Scenario view: highest outcome run for each agent spec, then cheapest, quickest, earliest run.
            </p>
          ) : null}
        </div>
        <div ref={filterRef} className="relative ml-auto shrink-0">
          <button
            type="button"
            className="inline-flex h-7 w-7 items-center justify-center rounded-md border transition "
            style={{ borderColor: filterOpen ? C.selectedBorder : C.border, color: filterOpen ? C.fg4 : C.fg2, background: filterOpen ? C.hover : 'transparent' }}
            aria-label="Filter chart"
            title="Filter chart"
            onClick={() => setFilterOpen((open) => !open)}
          >
            <SlidersHorizontal size={14} />
          </button>
          {filterOpen ? (
            <div
              className="absolute right-0 top-full z-20 mt-1 w-56 rounded-md border p-3 text-xs shadow-xl"
              style={{ borderColor: C.border, background: C.surface, color: C.fg3 }}
            >
              <div className="mb-2 text-xs font-semibold" style={{ color: C.fg4 }}>
                Filters
              </div>
              <div className="space-y-2">
                <div>
                  <div className="mb-1 border-b pb-1 text-[10px] font-semibold uppercase tracking-wide" style={{ borderColor: C.border, color: C.fg1 }}>
                    Provider
                  </div>
                  <div className="space-y-1">
                    {providerOptions.map((provider) => (
                      <label key={provider} className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 " style={{ color: C.fg2 }}>
                        <input
                          type="checkbox"
                          checked={selectedProviders.includes(provider)}
                          onChange={() => {
                            setSelectedProviders((current) => {
                              if (!current.includes(provider)) return [...current, provider];
                              return current.length === 1 ? current : current.filter((item) => item !== provider);
                            });
                          }}
                        />
                        {providerLabel(provider)}
                      </label>
                    ))}
                  </div>
                </div>
                <div>
                  <div className="mb-1 flex items-center gap-1.5 border-b pb-1 text-[10px] font-semibold uppercase tracking-wide" style={{ borderColor: C.border, color: C.fg1 }}>
                    Effort
                    <span className="group relative inline-flex" aria-label="Effort explanation">
                      <Info size={11} />
                      <span
                        className="pointer-events-none absolute right-0 top-4 z-30 hidden w-52 rounded-md border p-2 text-[10px] normal-case leading-snug tracking-normal group-hover:block"
                        style={{ borderColor: C.selectedBorder, background: C.surface, color: C.fg2 }}
                      >
                        OpenAI effort maps reasoning effort. Anthropic effort maps thinking effort. None means no explicit effort was captured.
                      </span>
                    </span>
                  </div>
                  <div className="space-y-1">
                    {effortOptions.map((effort) => (
                      <label key={effort} className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 " style={{ color: C.fg2 }}>
                        <input
                          type="checkbox"
                          checked={selectedEfforts.includes(effort)}
                          onChange={() => {
                            setSelectedEfforts((current) => {
                              if (!current.includes(effort)) return [...current, effort];
                              return current.length === 1 ? current : current.filter((item) => item !== effort);
                            });
                          }}
                        />
                        {effortLabel(effort)}
                      </label>
                    ))}
                  </div>
                </div>
                <div>
                  <div className="mb-1 border-b pb-1 text-[10px] font-semibold uppercase tracking-wide" style={{ borderColor: C.border, color: C.fg1 }}>
                    Revision
                  </div>
                  <div className="space-y-1">
                    {revisionOptions.map((revision) => (
                      <label key={revision} className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 " style={{ color: C.fg2 }}>
                        <input
                          type="checkbox"
                          checked={selectedRevisions.includes(revision)}
                          onChange={() => {
                            setSelectedRevisions((current) => {
                              if (!current.includes(revision)) return [...current, revision];
                              return current.length === 1 ? current : current.filter((item) => item !== revision);
                            });
                          }}
                        />
                        {revisionLabel(revision)}
                      </label>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-start gap-2">
        {modelLabels.length === 0 ? (
          <span className="text-xs" style={{ color: C.fg0 }}>
            No matching runs
          </span>
        ) : null}
        {activeTab === 'runtime' ? modelLabels.map((model) => {
          const specs = [...new Set(points.filter((run) => reduxModelLabel(run.agent_spec) === model).map((run) => run.agent_spec))];
          const payloadAt = (event: { clientX: number; clientY: number }) => legendPayload(model, specs, event.clientX, event.clientY);

          return (
              <button
                key={model}
                type="button"
                className="num inline-flex items-center gap-1 rounded py-0.5 pl-0 pr-1 text-xs transition "
                style={{ color: C.fg1 }}
                onMouseEnter={(event) => openTooltip(payloadAt(event))}
                onMouseMove={(event) => openTooltip(payloadAt(event))}
                onMouseLeave={closeTooltip}
              >
                <span className="size-2 rounded-full" style={{ background: modelColor(model) }} />
                {model}
              </button>
          );
        }) : (
          <>
            <span className="num inline-flex items-center gap-1 rounded py-0.5 pl-0 pr-1 text-xs" style={{ color: C.fg1 }}>
              <span className="size-2 rounded-sm" style={{ background: TOKEN_COLORS.input }} />
              input
            </span>
            <span className="num inline-flex items-center gap-1 rounded py-0.5 pl-0 pr-1 text-xs" style={{ color: C.fg1 }}>
              <span className="size-2 rounded-sm" style={{ background: TOKEN_COLORS.output }} />
              output
            </span>
          </>
        )}
      </div>
      {hasChart ? (
        activeTab === 'runtime' ? (
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full" role="img" aria-label="Outcome against run duration">
        <polygon
          points={`${PAD.left},${HEIGHT - PAD.bottom - 4} ${attractorMaxX},${PAD.top} ${PAD.left},${PAD.top}`}
          fill={C.greenBg}
          stroke={C.greenBorder}
          strokeWidth="1"
        />
        <text x={PAD.left + 4} y={PAD.top + 11} fontSize="6" fill={C.green}>
          attractive region
        </text>
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
          const ty = PAD.top + (1 - tick) * (HEIGHT - PAD.top - PAD.bottom);
          return (
            <g key={tick}>
              <line x1={PAD.left} x2={WIDTH - PAD.right} y1={ty} y2={ty} stroke={C.rowBorder} />
              <text x={PAD.left - 6} y={ty + 3} textAnchor="end" fontSize="8" fill={C.fg0}>
                {tick.toFixed(2)}
              </text>
            </g>
          );
        })}
        {xTicks.map((frac) => {
          const tx = PAD.left + frac * (WIDTH - PAD.left - PAD.right);
          return (
            <g key={frac}>
              <line x1={tx} x2={tx} y1={PAD.top} y2={HEIGHT - PAD.bottom} stroke={C.rowBorder} />
              <text x={tx} y={HEIGHT - PAD.bottom + 10} textAnchor="middle" fontSize="8" fill={C.fg0}>
                {durationTickLabel(maxDuration * frac)}
              </text>
            </g>
          );
        })}
        <text x={(WIDTH - PAD.right + PAD.left) / 2} y={HEIGHT - 3} textAnchor="middle" fontSize="8" fill={C.fg0}>
          runtime {'>'}
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
              stroke={run.status === 'ERROR' ? C.red : C.surface}
              strokeWidth={run.status === 'ERROR' ? 2 : 1}
              style={{ cursor: 'pointer' }}
              onMouseEnter={(event) => openTooltip(runPayload(run, event.clientX, event.clientY))}
              onMouseMove={(event) => openTooltip(runPayload(run, event.clientX, event.clientY))}
              onMouseLeave={closeTooltip}
              onClick={(event) => {
                navigate(`/runs/${encodeURIComponent(run.id)}`);
              }}
            />
          );
        })}
        </svg>
        ) : (
        <WireframeBarChart
          runs={points}
          modelLabel={reduxModelLabel}
          onOpenTooltip={openTooltip}
          onCloseTooltip={closeTooltip}
        />
        )
      ) : (
        <div className="flex h-40 items-center justify-center rounded-md border text-xs" style={{ borderColor: C.border, color: C.fg1 }}>
          Select at least one matching run to plot this chart.
        </div>
      )}
      {tooltip ? (
        <OverlayFrame payload={tooltip} showControls={false}>
          <TooltipContent payload={tooltip} />
        </OverlayFrame>
      ) : null}
    </div>
  );
}
