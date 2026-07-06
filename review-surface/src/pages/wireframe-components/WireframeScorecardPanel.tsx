import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Crosshair } from 'lucide-react';
import { C } from '@/utils/colors';
import { fmtPercent, fmtScore, tryParseJson } from '@/utils/helpers';
import { humanize, scoreTier, scorerName } from '@/utils/verdict';
import type { Span } from '@/utils/types';

interface MetricRow {
  metricId: string;
  weight: number;
  score: number;
  passed: boolean | null;
  evidence: string;
  spanId: string | null;
}

interface ScorerBlock {
  scorerId: string;
  weight: number;
  score: number;
  category: string;
  metrics: MetricRow[];
}

interface MetricPayload {
  passed?: boolean;
  evidence?: string;
}

interface ScorerPayload {
  weight?: number;
  score?: number;
  category?: string;
  metric_contributions?: Array<{ metric_id?: string; weight?: number; score?: number }>;
}

function parseScorers(spans: Span[]): ScorerBlock[] {
  const metricSpans = new Map<string, { spanId: string; payload: MetricPayload }>();
  for (const span of spans) {
    if (!span.name.startsWith('metric:')) continue;
    const payload = (tryParseJson(span.output_payload ?? '') ?? {}) as MetricPayload;
    metricSpans.set(span.name.slice('metric:'.length), { spanId: span.id, payload });
  }

  return spans
    .filter((span) => span.name.startsWith('scorer:'))
    .map((span) => {
      const payload = (tryParseJson(span.output_payload ?? '') ?? {}) as ScorerPayload;
      const metrics: MetricRow[] = (payload.metric_contributions ?? []).map((contribution) => {
        const metricId = contribution.metric_id ?? 'unknown';
        const metric = metricSpans.get(metricId);
        return {
          metricId,
          weight: contribution.weight ?? 0,
          score: contribution.score ?? 0,
          passed: metric?.payload.passed ?? null,
          evidence: metric?.payload.evidence ?? '',
          spanId: metric?.spanId ?? null,
        };
      });
      return {
        scorerId: span.name.slice('scorer:'.length),
        weight: payload.weight ?? 0,
        score: payload.score ?? 0,
        category: payload.category ?? 'quality',
        metrics,
      };
    });
}

function scoreColor(score: number, passed: boolean | null): string {
  if (passed === false) return C.red;
  return scoreTier(score).color;
}

function ScoreRing({ score }: { score: number }) {
  const color = scoreTier(score).color;
  const normalized = Math.max(0, Math.min(1, score));
  return (
    <span
      className="inline-flex size-4 shrink-0 rounded-full"
      style={{ background: `conic-gradient(${color} ${normalized * 360}deg, ${C.ringTrack} 0deg)` }}
      aria-label="Score"
    >
      <span className="m-[3px] flex-1 rounded-full" style={{ background: C.surface }} />
    </span>
  );
}

export function WireframeScorecardPanel({
  spans,
  onSelect,
}: {
  spans: Span[];
  onSelect: (spanId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [scorecardSelectedSpanId, setScorecardSelectedSpanId] = useState<string | null>(null);
  const scorers = useMemo(() => parseScorers(spans), [spans]);
  if (scorers.length === 0) return null;

  return (
    <div className="flex flex-col gap-1.5 rounded-lg p-2.5" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <button className="flex flex-wrap items-center gap-2 text-left" onClick={() => setOpen((o) => !o)}>
        {open ? <ChevronDown className="size-3.5" style={{ color: C.fg0 }} /> : <ChevronRight className="size-3.5" style={{ color: C.fg0 }} />}
        <span className="text-xs font-medium" style={{ color: C.fg3 }}>
          Criterion
        </span>
      </button>

      {open && (
        <div className="grid gap-2 lg:grid-cols-2 xl:grid-cols-3">
          {scorers.map((scorer) => (
            <div key={scorer.scorerId} className="rounded-md px-1.5 py-2" style={{ background: C.subtle, border: `1px solid ${C.border}` }}>
              <div className="mb-1.5 flex items-center gap-2">
                <span className="text-[11px] font-medium" style={{ color: C.fg4 }} title={scorer.scorerId}>
                  {scorerName(scorer.scorerId)}
                </span>
                <span
                  className="text-[10px]"
                  style={{ color: C.fg0 }}
                  title={`This ${scorer.category} area contributes ${fmtPercent(scorer.weight)} of the composite score`}
                >
                  {fmtPercent(scorer.weight)} of composite
                </span>
                <span className="ml-auto inline-flex items-center gap-1.5">
                  <ScoreRing score={scorer.score} />
                  <span className="num text-[11px] font-bold" style={{ color: scoreColor(scorer.score, null) }}>
                    {fmtScore(scorer.score)}
                  </span>
                </span>
              </div>
              <div className="flex flex-col">
                {scorer.metrics.map((metric) => {
                  const selected = metric.spanId != null && metric.spanId === scorecardSelectedSpanId;
                  return (
                    <button
                      key={metric.metricId}
                      disabled={metric.spanId == null}
                      onClick={() => {
                        if (!metric.spanId) return;
                        setScorecardSelectedSpanId(metric.spanId);
                        onSelect(metric.spanId);
                      }}
                      title={`${metric.metricId} · ${fmtPercent(metric.weight)} of this area${metric.evidence ? `\n${metric.evidence}` : ''}`}
                      className="group flex items-center gap-2 rounded px-1 py-0.5 text-left transition hover:bg-white/5 disabled:cursor-default"
                      style={{ background: selected ? C.selected : undefined }}
                    >
                      <span className="num w-3 shrink-0 text-center text-[10px] font-bold" style={{ color: metric.passed === false ? C.red : metric.passed ? C.green : C.fg0 }}>
                        {metric.passed === false ? '✗' : metric.passed ? '✓' : '·'}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[10px]" style={{ color: C.fg2 }}>
                        {humanize(metric.metricId)}
                      </span>
                      {metric.spanId && <Crosshair className="size-2.5 shrink-0 opacity-0 transition group-hover:opacity-100" style={{ color: C.accent }} />}
                      <span className="num w-12 shrink-0 text-right text-[10px] font-medium" style={{ color: scoreColor(metric.score, metric.passed) }}>
                        {fmtScore(metric.score)}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
