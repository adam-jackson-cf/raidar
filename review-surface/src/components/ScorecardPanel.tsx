// Raidar-native scorecard breakdown: scorer → weighted metric contributions,
// parsed from the projected scorer:*/metric:* evidence spans.
import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Crosshair } from 'lucide-react';
import { C } from '@/utils/colors';
import { fmtScore, tryParseJson } from '@/utils/helpers';
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
  scorer_id?: string;
  version?: number;
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
  const blocks: ScorerBlock[] = [];
  for (const span of spans) {
    if (!span.name.startsWith('scorer:')) continue;
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
    blocks.push({
      scorerId: span.name.slice('scorer:'.length),
      weight: payload.weight ?? 0,
      score: payload.score ?? 0,
      category: payload.category ?? 'quality',
      metrics,
    });
  }
  return blocks;
}

function scoreColor(score: number, passed: boolean | null): string {
  if (passed === false) return C.red;
  if (score >= 0.9) return C.green;
  if (score >= 0.6) return C.fg3;
  return C.orange;
}

export function ScorecardPanel({
  spans,
  selectedSpanId,
  onSelect,
}: {
  spans: Span[];
  selectedSpanId: string | null;
  onSelect: (spanId: string) => void;
}) {
  const [open, setOpen] = useState(true);
  const scorers = useMemo(() => parseScorers(spans), [spans]);
  if (scorers.length === 0) return null;

  return (
    <div
      className="flex flex-col gap-1.5 rounded-lg p-2.5"
      style={{ background: C.surface, border: `1px solid ${C.border}` }}
    >
      <button className="flex items-center gap-2 text-left" onClick={() => setOpen((o) => !o)}>
        {open ? (
          <ChevronDown className="size-3.5" style={{ color: C.fg0 }} />
        ) : (
          <ChevronRight className="size-3.5" style={{ color: C.fg0 }} />
        )}
        <span className="text-xs font-medium" style={{ color: C.fg3 }}>
          Scorecard
        </span>
        <span className="flex items-center gap-1.5">
          {scorers.map((scorer) => (
            <span
              key={scorer.scorerId}
              className="num rounded px-1.5 py-px text-[10px]"
              style={{ color: C.fg2, background: 'rgba(255,255,255,0.04)', border: `1px solid ${C.border}` }}
            >
              {scorer.scorerId}
              <span className="ml-1" style={{ color: scoreColor(scorer.score, null) }}>
                {fmtScore(scorer.score)}
              </span>
            </span>
          ))}
        </span>
        <span className="text-[10px]" style={{ color: C.fg0 }}>
          why this run scored what it scored
        </span>
      </button>

      {open && (
        <div className="grid gap-2 lg:grid-cols-2 xl:grid-cols-3">
          {scorers.map((scorer) => (
            <div
              key={scorer.scorerId}
              className="rounded-md p-2"
              style={{ background: 'rgba(255,255,255,0.015)', border: `1px solid ${C.border}` }}
            >
              <div className="mb-1.5 flex items-center gap-2">
                <span className="num text-[11px] font-medium" style={{ color: C.fg4 }}>
                  {scorer.scorerId}
                </span>
                <span className="num text-[10px]" style={{ color: C.fg0 }}>
                  weight {scorer.weight} · {scorer.category}
                </span>
                <span className="num ml-auto text-[11px] font-bold" style={{ color: C.accent }}>
                  {fmtScore(scorer.score)}
                </span>
              </div>
              <div className="flex flex-col">
                {scorer.metrics.map((metric) => {
                  const selected = metric.spanId != null && metric.spanId === selectedSpanId;
                  return (
                    <button
                      key={metric.metricId}
                      disabled={metric.spanId == null}
                      onClick={() => metric.spanId && onSelect(metric.spanId)}
                      title={metric.evidence || metric.metricId}
                      className="group flex items-center gap-2 rounded px-1 py-0.5 text-left transition hover:bg-white/5 disabled:cursor-default"
                      style={{ background: selected ? C.selected : undefined }}
                    >
                      <span
                        className="num w-3 shrink-0 text-center text-[10px] font-bold"
                        style={{ color: metric.passed === false ? C.red : metric.passed ? C.green : C.fg0 }}
                      >
                        {metric.passed === false ? '✗' : metric.passed ? '✓' : '·'}
                      </span>
                      <span className="num min-w-0 flex-1 truncate text-[10px]" style={{ color: C.fg2 }}>
                        {metric.metricId}
                      </span>
                      <span className="num shrink-0 text-[10px]" style={{ color: C.fg0 }}>
                        w {metric.weight}
                      </span>
                      <span
                        className="num shrink-0 text-[10px] font-medium"
                        style={{ color: scoreColor(metric.score, metric.passed) }}
                      >
                        {fmtScore(metric.score)}
                      </span>
                      {metric.spanId && (
                        <Crosshair
                          className="size-2.5 shrink-0 opacity-0 transition group-hover:opacity-100"
                          style={{ color: C.accent }}
                        />
                      )}
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
