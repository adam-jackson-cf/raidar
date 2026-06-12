import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, GitCompareArrows } from 'lucide-react';
import { api } from '@/api/client';
import { Badge } from '@/components/Badge';
import { ConfidenceChip, DeltaChip, NeutralChip, StatusChip } from '@/components/review/chips';
import { DeltaBars } from '@/components/review/DeltaBars';
import { DiagnosisSection } from '@/components/review/DiagnosisSection';
import { EvidenceStrip } from '@/components/review/EvidenceStrip';
import { RadarChart } from '@/components/review/RadarChart';
import { RunConsistency } from '@/components/review/RunConsistency';
import { C } from '@/utils/colors';
import { fmtTokens } from '@/utils/helpers';
import {
  DIMENSION_KEYS,
  DIMENSION_LABELS,
  type ReviewDetail,
  type ReviewResponse,
} from '@/utils/review-types';

type ComparatorMode = 'benchmark' | 'previous' | 'selected';

function resolveComparator(
  review: ReviewDetail,
  reviews: Record<string, ReviewDetail>,
  mode: ComparatorMode,
  selectedId: string | null,
): { review: ReviewDetail | null; label: string } {
  if (mode === 'selected' && selectedId && reviews[selectedId]) {
    return { review: reviews[selectedId], label: `${reviews[selectedId].agent_spec} (selected)` };
  }
  if (mode === 'previous' && review.change_context.previous_review_id) {
    const previous = reviews[review.change_context.previous_review_id] ?? null;
    return { review: previous, label: previous ? `previous representative (${previous.revision})` : 'previous' };
  }
  if (mode === 'benchmark' && review.benchmark.review_id && review.benchmark.review_id !== review.review_id) {
    const bench = reviews[review.benchmark.review_id] ?? null;
    return { review: bench, label: bench ? `benchmark (${bench.agent_spec})` : 'benchmark' };
  }
  return { review: null, label: '' };
}

function OutcomeHeader({ review }: { review: ReviewDetail }) {
  const rep = review.representative;
  const deltaSummary = review.benchmark_delta
    ? review.benchmark_delta.is_benchmark
      ? 'Benchmark'
      : review.benchmark_delta.summary
    : null;
  return (
    <div className="flex flex-col gap-1.5 rounded-lg p-3" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <div className="flex flex-wrap items-center gap-2">
        <Link to="/" className="inline-flex items-center gap-1 text-[10px] transition hover:opacity-80" style={{ color: C.fg1 }}>
          <ArrowLeft className="size-3" /> board
        </Link>
        <span className="num text-sm font-medium" style={{ color: C.fg5 }}>
          {review.agent_spec}
        </span>
        <span className="num text-[11px]" style={{ color: C.fg1 }}>
          {review.scenario}@{review.revision}
        </span>
        {review.synthetic && <Badge label="synthetic" />}
        <span className="num text-[9px]" style={{ color: C.fg0 }} title="Representative experiment id">
          {rep.experiment_id}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <StatusChip status={review.absolute_status} title="Absolute status — independent of the benchmark" />
        {deltaSummary ? (
          <DeltaChip summary={deltaSummary} title="Benchmark comparison — separate from absolute status" />
        ) : (
          <NeutralChip label="No benchmark pinned" />
        )}
        <ConfidenceChip
          band={review.confidence.band}
          title={review.confidence.components.map((c) => `${c.name}: ${c.value ?? '—'}`).join(' · ')}
        />
        <span className="text-[10px]" style={{ color: rep.unresolved_unscored > 0 ? C.orange : C.fg0 }}>
          {rep.scored_count}/{rep.total_count} runs scored
          {rep.unresolved_unscored > 0 && ` · ${rep.unresolved_unscored} unresolved unscored`}
        </span>
      </div>
      <span className="text-[12px] leading-snug" style={{ color: C.fg4 }}>
        {review.verdict}
      </span>
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5">
        <span className="text-[11px]" style={{ color: C.green }}>
          ▲ {review.primary_strength}
        </span>
        <span className="text-[11px]" style={{ color: C.orange }}>
          ▼ {review.primary_weakness}
        </span>
      </div>
      <span className="text-[9px]" style={{ color: C.fg0 }}>
        representative: {rep.reason}
        {review.benchmark.status === 'pinned' && ` · benchmark: ${review.benchmark.agent_spec}`}
        {review.benchmark.status === 'none' && ' · no benchmark pinned for this scenario'}
      </span>
    </div>
  );
}

function ChangeContextSection({ review }: { review: ReviewDetail }) {
  const context = review.change_context;
  return (
    <div className="flex flex-col gap-1 rounded-lg p-2.5" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] font-medium uppercase tracking-wide" style={{ color: C.fg1 }}>
          Change context
        </span>
        {context.changes.map((change, index) => (
          <NeutralChip key={index} label={change.category} title={change.detail} />
        ))}
        {context.previous_review_id && (
          <Link
            to={`/review/${encodeURIComponent(context.previous_review_id)}`}
            className="text-[10px] transition hover:opacity-80"
            style={{ color: C.accent }}
          >
            open previous representative →
          </Link>
        )}
      </div>
      <span className="text-[11px]" style={{ color: C.fg2 }}>
        {context.summary}
      </span>
      {context.changes.some((change) => change.comparability_warnings.length > 0) && (
        <span className="text-[10px]" style={{ color: C.orange }}>
          Comparability warning: {context.changes.flatMap((change) => change.comparability_warnings).join('; ')} — do not
          attribute movement to the harness alone.
        </span>
      )}
    </div>
  );
}

function deltaFor(current: number | null, comparator: number | null): number | null {
  return current != null && comparator != null ? current - comparator : null;
}

function AttributeComparison({
  review,
  comparator,
  comparatorLabel,
}: {
  review: ReviewDetail;
  comparator: ReviewDetail | null;
  comparatorLabel: string;
}) {
  const axes = ['Task', 'Scenario', 'Workflow', 'Reliability', 'Confidence'];
  const values = (target: ReviewDetail) => [
    ...DIMENSION_KEYS.map((key) => target.dimensions[key]?.score ?? null),
    target.confidence.score,
  ];
  const profiles = [
    { label: review.agent_spec, color: C.cyan, values: values(review) },
    ...(comparator ? [{ label: comparatorLabel, color: C.accent, values: values(comparator) }] : []),
  ];
  const entries = [
    ...DIMENSION_KEYS.map((key) => ({
      label: DIMENSION_LABELS[key],
      current: review.dimensions[key]?.score ?? null,
      comparator: comparator?.dimensions[key]?.score ?? null,
    })),
    { label: 'Confidence', current: review.confidence.score, comparator: comparator?.confidence.score ?? null },
  ];
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline gap-2">
        <span className="text-xs font-medium" style={{ color: C.fg3 }}>
          Attribute comparison
        </span>
        {!comparator && (
          <span className="text-[10px]" style={{ color: C.fg0 }}>
            no comparator available — showing the current shape only, directional wording suppressed
          </span>
        )}
      </div>
      <div className="grid items-start gap-3 rounded-lg p-3 lg:grid-cols-[auto_1fr]" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
        <div className="flex flex-col items-center gap-1">
          <RadarChart axes={axes} profiles={profiles} />
          <div className="flex items-center gap-3 text-[10px]">
            <span className="flex items-center gap-1" style={{ color: C.cyan }}>
              <span className="inline-block h-[2px] w-4" style={{ background: C.cyan }} /> {review.agent_spec}
            </span>
            {comparator && (
              <span className="flex items-center gap-1" style={{ color: C.accent }}>
                <span className="inline-block h-[2px] w-4" style={{ background: C.accent }} /> {comparatorLabel}
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-col gap-3">
          {comparator ? (
            <DeltaBars entries={entries} comparatorLabel="" />
          ) : (
            <div className="flex flex-col gap-1">
              {entries.map((entry) => (
                <div key={entry.label} className="flex items-center gap-2 text-[11px]">
                  <span className="w-36" style={{ color: C.fg2 }}>{entry.label}</span>
                  <span className="num" style={{ color: C.fg3 }}>{entry.current?.toFixed(2) ?? 'Unavailable'}</span>
                </div>
              ))}
            </div>
          )}
          <EfficiencyAnchors review={review} comparator={comparator} comparatorLabel={comparatorLabel} />
        </div>
      </div>
    </div>
  );
}

function EfficiencyAnchors({
  review,
  comparator,
  comparatorLabel,
}: {
  review: ReviewDetail;
  comparator: ReviewDetail | null;
  comparatorLabel: string;
}) {
  const anchors: Array<{ label: string; value: (r: ReviewDetail) => string }> = [
    { label: 'median duration', value: (r) => (r.efficiency.duration_sec != null ? `${r.efficiency.duration_sec}s` : '—') },
    { label: 'median uncached tokens', value: (r) => fmtTokens(r.efficiency.uncached_input_tokens) },
    { label: 'median commands', value: (r) => `${r.efficiency.command_count ?? '—'}` },
    { label: 'failed commands', value: (r) => `${r.efficiency.failed_command_count ?? '—'}` },
    { label: 'verification rounds', value: (r) => `${r.efficiency.verification_rounds ?? '—'}` },
  ];
  return (
    <div className="flex flex-col gap-1" style={{ borderTop: `1px solid ${C.border}` }}>
      <span className="pt-2 text-[9px] uppercase tracking-wider" style={{ color: C.fg0 }}>
        efficiency anchors — supporting context, kept outside the dimension radar
      </span>
      <div className="flex flex-wrap gap-x-5 gap-y-1">
        {anchors.map((anchor) => (
          <div key={anchor.label} className="flex flex-col">
            <span className="text-[9px]" style={{ color: C.fg0 }}>{anchor.label}</span>
            <span className="num text-[11px]" style={{ color: C.fg2 }}>
              {anchor.value(review)}
              {comparator && (
                <span style={{ color: C.fg0 }} title={comparatorLabel}>
                  {' '}vs {anchor.value(comparator)}
                </span>
              )}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ReviewBody({ review, data }: { review: ReviewDetail; data: ReviewResponse }) {
  const [searchParams] = useSearchParams();
  const selectedId = searchParams.get('vs');
  const hasBenchmark = Boolean(review.benchmark.review_id) && review.benchmark.review_id !== review.review_id;
  const hasPrevious = Boolean(review.change_context.previous_review_id);
  const defaultMode: ComparatorMode = selectedId ? 'selected' : hasBenchmark ? 'benchmark' : 'previous';
  const [mode, setMode] = useState<ComparatorMode>(defaultMode);
  const { review: comparator, label } = resolveComparator(review, data.reviews, mode, selectedId);

  const modes: Array<{ key: ComparatorMode; label: string; enabled: boolean }> = [
    { key: 'benchmark', label: 'vs benchmark', enabled: hasBenchmark },
    { key: 'previous', label: 'vs previous (self-trend)', enabled: hasPrevious },
    { key: 'selected', label: 'vs selected', enabled: Boolean(selectedId) },
  ];

  return (
    <div className="sb flex flex-1 flex-col gap-4 overflow-auto p-4">
      <OutcomeHeader review={review} />
      <ChangeContextSection review={review} />
      <div className="flex items-center gap-1.5">
        <GitCompareArrows className="size-3.5" style={{ color: C.fg0 }} />
        <span className="text-[10px] uppercase tracking-wider" style={{ color: C.fg0 }}>
          comparator
        </span>
        {modes
          .filter((entry) => entry.enabled)
          .map((entry) => (
            <button
              key={entry.key}
              onClick={() => setMode(entry.key)}
              className="rounded px-1.5 py-0.5 text-[10px] transition"
              style={{
                color: mode === entry.key ? C.accent : C.fg1,
                border: `1px solid ${mode === entry.key ? C.selectedBorder : C.border}`,
                background: mode === entry.key ? C.selected : 'transparent',
              }}
            >
              {entry.label}
            </button>
          ))}
        {!modes.some((entry) => entry.enabled) && (
          <span className="text-[10px]" style={{ color: C.fg0 }}>
            none available — no benchmark pinned and no previous representative
          </span>
        )}
      </div>
      <EvidenceStrip
        evidence={review.evidence}
        comparator={comparator ? comparator.evidence.current : null}
        comparatorLabel={comparator ? label : null}
        subtype={review.scenario_fidelity_subtype}
      />
      <DiagnosisSection
        strengths={review.diagnosis.strengths}
        weaknesses={review.diagnosis.weaknesses}
        opportunities={review.diagnosis.opportunities}
      />
      <AttributeComparison review={review} comparator={comparator} comparatorLabel={label} />
      <RunConsistency rows={review.run_consistency} confidence={review.confidence} />
    </div>
  );
}

export function ExperimentReviewPage() {
  const { reviewId } = useParams<{ reviewId: string }>();
  const review = useQuery({ queryKey: ['review'], queryFn: api.review });

  const detail = useMemo(
    () => (reviewId && review.data ? (review.data.reviews[reviewId] ?? null) : null),
    [review.data, reviewId],
  );

  if (review.isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center text-xs" style={{ color: C.fg1 }}>
        Loading experiment review…
      </div>
    );
  }
  if (review.isError) {
    return (
      <div className="flex flex-1 items-center justify-center text-xs" style={{ color: C.red }}>
        Failed to load review data: {(review.error as Error).message}
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-1.5">
        <span className="text-sm" style={{ color: C.fg3 }}>
          Experiment review not found
        </span>
        <span className="text-xs" style={{ color: C.fg0 }}>
          No derived review for “{reviewId}”. Re-run make review-surface-data if the experiment exists.
        </span>
        <Link to="/" className="text-xs transition hover:opacity-80" style={{ color: C.accent }}>
          Back to scenario boards
        </Link>
      </div>
    );
  }
  return <ReviewBody key={detail.review_id} review={detail} data={review.data!} />;
}
