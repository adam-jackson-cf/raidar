import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowRight, ChevronDown, ChevronRight, Pin } from 'lucide-react';
import { api } from '@/api/client';
import { Badge } from '@/components/Badge';
import { FailurePatterns } from '@/components/FailurePatterns';
import { RevisionMovement } from '@/components/RevisionMovement';
import { TradeoffScatter } from '@/components/TradeoffScatter';
import { ConfidenceChip, DeltaChip, NeutralChip, RepresentativeBadge, StatusChip } from '@/components/review/chips';
import { DimensionCell } from '@/components/review/DimensionCells';
import { C } from '@/utils/colors';
import { fmtTokens } from '@/utils/helpers';
import {
  DIMENSION_KEYS,
  DIMENSION_LABELS,
  type Board,
  type ReviewDetail,
  type ReviewRow,
} from '@/utils/review-types';

const TH = 'px-2.5 py-1.5 text-left text-[9px] font-medium uppercase tracking-wider';

type SortKey = 'default' | 'delta' | 'confidence' | 'duration' | 'tokens' | (typeof DIMENSION_KEYS)[number];

const DELTA_SORT_RANK: Record<string, number> = {
  Benchmark: 1,
  Ahead: 0,
  Parity: 2,
  Mixed: 3,
  Inconclusive: 4,
  Behind: 5,
  Unavailable: 6,
};

function deltaSummaryOf(row: ReviewRow): string {
  if (!row.benchmark_delta) return 'Unavailable';
  return row.benchmark_delta.is_benchmark ? 'Benchmark' : row.benchmark_delta.summary;
}

function rowSortValue(row: ReviewRow, key: SortKey): number {
  if (key === 'confidence') return -(row.confidence.score ?? -1);
  if (key === 'delta') return DELTA_SORT_RANK[deltaSummaryOf(row)] ?? 6;
  if (key === 'duration') return row.efficiency.duration_sec ?? Number.MAX_SAFE_INTEGER;
  if (key === 'tokens') return row.efficiency.uncached_input_tokens ?? Number.MAX_SAFE_INTEGER;
  if (key === 'default') return 0;
  return -(row.dimensions[key]?.score ?? -1);
}

function BoardHeader({ board, revisionSelector }: { board: Board; revisionSelector?: React.ReactNode }) {
  const meta = board.scenario_meta;
  return (
    <div className="flex flex-col gap-1 px-3 py-2.5" style={{ borderBottom: `1px solid ${C.border}` }}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="num text-sm font-medium" style={{ color: C.fg5 }}>
          {board.scenario}
        </span>
        {revisionSelector ?? <NeutralChip label={board.revision} />}
        {meta?.category && <NeutralChip label={meta.category} />}
        {meta?.difficulty && <NeutralChip label={meta.difficulty} />}
        <span className="flex items-center gap-1 text-[10px]" style={{ color: board.benchmark.status === 'pinned' ? C.accent : C.orange }}>
          <Pin className="size-3" />
          {board.benchmark.status === 'pinned'
            ? `Benchmark: ${board.benchmark.agent_spec}`
            : board.benchmark.status === 'pinned-missing'
              ? `Benchmark ${board.benchmark.agent_spec} has no representative experiment on ${board.revision}`
              : 'No benchmark pinned — comparative claims suppressed'}
        </span>
      </div>
      {meta?.description && (
        <span className="text-[11px]" style={{ color: C.fg1 }}>
          {meta.description}
        </span>
      )}
      <div className="flex flex-wrap items-center gap-2 text-[10px]" style={{ color: C.fg0 }}>
        <span>
          {board.cohort.meets} meet bar · {board.cohort.below} below · {board.cohort.unavailable} unavailable
          {board.cohort.low_confidence > 0 && ` · ${board.cohort.low_confidence} low confidence`}
        </span>
        <span title={board.representative_rule} className="cursor-help underline decoration-dotted">
          representative rule
        </span>
        {board.freshness && <span>latest {new Date(board.freshness).toLocaleDateString()}</span>}
      </div>
    </div>
  );
}

function BoardRow({
  row,
  detail,
  selectedForCompare,
  onToggleCompare,
}: {
  row: ReviewRow;
  detail: ReviewDetail | undefined;
  selectedForCompare: boolean;
  onToggleCompare: () => void;
}) {
  const navigate = useNavigate();
  const unavailable = row.absolute_status === 'Unavailable';
  const deltaSummary = row.benchmark_delta
    ? row.benchmark_delta.is_benchmark
      ? 'Benchmark'
      : row.benchmark_delta.summary
    : null;
  return (
    <>
      <tr
        className="cursor-pointer transition hover:bg-white/[0.03]"
        style={{ borderLeft: selectedForCompare ? `2px solid ${C.accent}` : '2px solid transparent' }}
        onClick={() => navigate(`/review/${encodeURIComponent(row.review_id)}`)}
      >
        <td className="px-2.5 pt-2">
          <div className="flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={selectedForCompare}
              onClick={(e) => e.stopPropagation()}
              onChange={onToggleCompare}
              aria-label={`Select ${row.agent_spec} for comparison`}
              className="size-3 accent-[#5B8DEF]"
            />
            <span className="num text-[12px]" style={{ color: C.cyan }}>
              {row.agent_spec}
            </span>
            {row.synthetic && <Badge label="synthetic" />}
            <RepresentativeBadge representative={row.representative} />
          </div>
        </td>
        <td className="px-2.5 pt-2">
          <StatusChip status={row.absolute_status} />
        </td>
        <td className="px-2.5 pt-2">
          {deltaSummary ? (
            <span className="inline-flex flex-wrap items-center gap-1">
              <DeltaChip summary={deltaSummary} title={row.benchmark_delta?.compatibility_reason ?? undefined} />
              {row.benchmark_delta?.compatibility === 'changed-baseline' && (
                <span className="text-[9px]" style={{ color: C.orange }} title={row.benchmark_delta.compatibility_reason ?? ''}>
                  changed baseline
                </span>
              )}
            </span>
          ) : (
            <span className="text-[10px]" style={{ color: C.fg0 }}>
              No benchmark
            </span>
          )}
        </td>
        <td className="px-2.5 pt-2">
          <ConfidenceChip
            band={row.confidence.band}
            title={row.confidence.components
              .map((component) => `${component.name}: ${component.value ?? '—'}`)
              .join(' · ')}
          />
        </td>
        {DIMENSION_KEYS.map((key) => (
          <td key={key} className="px-2.5 pt-2">
            {unavailable ? (
              <span className="text-[10px]" style={{ color: C.fg0 }}>—</span>
            ) : (
              <DimensionCell
                score={row.dimensions[key]?.score ?? null}
                capped={row.dimensions[key]?.caps_triggered}
                delta={row.benchmark_delta && !row.benchmark_delta.is_benchmark ? row.benchmark_delta.dimensions[key] : null}
                compact
              />
            )}
          </td>
        ))}
        <td className="px-2.5 pt-2 text-right">
          <div className="num flex flex-col items-end text-[10px]" style={{ color: C.fg1 }}>
            <span>{row.efficiency.duration_sec != null ? `${row.efficiency.duration_sec}s` : '—'} · {fmtTokens(row.efficiency.uncached_input_tokens)} tok</span>
            <span style={{ color: row.representative.unresolved_unscored > 0 ? C.orange : C.fg0 }}>
              {row.representative.scored_count} scored
              {row.representative.unresolved_unscored > 0 && ` · ${row.representative.unresolved_unscored} unscored`}
            </span>
          </div>
        </td>
        <td className="px-2.5 pt-2">
          <Link
            to={`/review/${encodeURIComponent(row.review_id)}`}
            onClick={(e) => e.stopPropagation()}
            className="inline-flex items-center gap-1 text-[10px] transition hover:opacity-80"
            style={{ color: C.accent }}
          >
            Open review <ArrowRight className="size-3" />
          </Link>
        </td>
      </tr>
      <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
        <td colSpan={10} className="px-2.5 pb-2 pt-1">
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5 pl-[18px]">
            <span className="text-[11px]" style={{ color: C.fg3 }}>
              {row.verdict}
            </span>
            {detail && (
              <>
                <span className="text-[10px]" style={{ color: C.green }}>
                  ▲ {detail.primary_strength}
                </span>
                <span className="text-[10px]" style={{ color: C.orange }}>
                  ▼ {detail.primary_weakness}
                </span>
              </>
            )}
          </div>
        </td>
      </tr>
    </>
  );
}

function FilterChips<T extends string>({
  options,
  active,
  onToggle,
}: {
  options: T[];
  active: Set<T>;
  onToggle: (option: T) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      {options.map((option) => {
        const on = active.has(option);
        return (
          <button
            key={option}
            onClick={() => onToggle(option)}
            className="rounded px-1.5 py-0.5 text-[10px] transition"
            style={{
              color: on ? C.accent : C.fg1,
              border: `1px solid ${on ? C.selectedBorder : C.border}`,
              background: on ? C.selected : 'transparent',
            }}
          >
            {option}
          </button>
        );
      })}
    </div>
  );
}

function ScenarioBoard({
  board,
  reviews,
  revisionSelector,
}: {
  board: Board;
  reviews: Record<string, ReviewDetail>;
  revisionSelector?: React.ReactNode;
}) {
  const navigate = useNavigate();
  const [sortKey, setSortKey] = useState<SortKey>('default');
  const [statusFilter, setStatusFilter] = useState<Set<string>>(new Set());
  const [confidenceFilter, setConfidenceFilter] = useState<Set<string>>(new Set());
  const [deltaFilter, setDeltaFilter] = useState<Set<string>>(new Set());
  const [compare, setCompare] = useState<string[]>([]);

  const rows = useMemo(() => {
    let list = board.rows;
    if (statusFilter.size) list = list.filter((row) => statusFilter.has(row.absolute_status));
    if (confidenceFilter.size) list = list.filter((row) => confidenceFilter.has(row.confidence.band));
    if (deltaFilter.size) list = list.filter((row) => deltaFilter.has(deltaSummaryOf(row)));
    if (sortKey === 'default') return list;
    return [...list].sort((a, b) => rowSortValue(a, sortKey) - rowSortValue(b, sortKey));
  }, [board.rows, sortKey, statusFilter, confidenceFilter, deltaFilter]);

  const toggle = (set: Set<string>, value: string, update: (next: Set<string>) => void) => {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    update(next);
  };

  const toggleCompare = (reviewId: string) =>
    setCompare((prev) =>
      prev.includes(reviewId) ? prev.filter((id) => id !== reviewId) : [...prev.slice(-1), reviewId],
    );

  const compareTarget =
    compare.length === 2
      ? { from: compare[0], vs: compare[1] }
      : compare.length === 1 && board.benchmark.review_id && board.benchmark.review_id !== compare[0]
        ? { from: compare[0], vs: board.benchmark.review_id }
        : null;

  const sortHeader = (label: string, key: SortKey) => (
    <button
      onClick={() => setSortKey(sortKey === key ? 'default' : key)}
      className="uppercase tracking-wider transition hover:opacity-80"
      style={{ color: sortKey === key ? C.accent : C.fg0 }}
      title={`Sort by ${label}`}
    >
      {label}
    </button>
  );

  return (
    <section className="overflow-hidden rounded-lg" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <BoardHeader board={board} revisionSelector={revisionSelector} />
      {(board.rows.length > 2 || statusFilter.size > 0 || confidenceFilter.size > 0 || deltaFilter.size > 0) && (
        <div className="flex flex-wrap items-center gap-3 px-3 py-1.5" style={{ borderBottom: `1px solid ${C.border}` }}>
          <span className="text-[9px] uppercase tracking-wider" style={{ color: C.fg0 }}>filter</span>
          <FilterChips
            options={['Meets Scenario Bar', 'Below Scenario Bar', 'Unavailable']}
            active={statusFilter}
            onToggle={(value) => toggle(statusFilter, value, setStatusFilter)}
          />
          <FilterChips
            options={['High', 'Medium', 'Low', 'Very Low']}
            active={confidenceFilter}
            onToggle={(value) => toggle(confidenceFilter, value, setConfidenceFilter)}
          />
          {board.benchmark.status === 'pinned' && (
            <FilterChips
              options={['Ahead', 'Parity', 'Behind', 'Mixed', 'Inconclusive']}
              active={deltaFilter}
              onToggle={(value) => toggle(deltaFilter, value, setDeltaFilter)}
            />
          )}
        </div>
      )}
      <div className="sb overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              <th className={TH} style={{ color: C.fg0 }}>Agent spec</th>
              <th className={TH} style={{ color: C.fg0 }}>Status</th>
              <th className={TH}>{sortHeader('Vs benchmark', 'delta')}</th>
              <th className={TH}>{sortHeader('Confidence', 'confidence')}</th>
              {DIMENSION_KEYS.map((key) => (
                <th key={key} className={TH}>
                  {sortHeader(DIMENSION_LABELS[key], key)}
                </th>
              ))}
              <th className={`${TH} text-right`}>
                {sortHeader('Duration', 'duration')} · {sortHeader('Tokens', 'tokens')}
              </th>
              <th className={TH} style={{ color: C.fg0 }} />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <BoardRow
                key={row.review_id}
                row={row}
                detail={reviews[row.review_id]}
                selectedForCompare={compare.includes(row.review_id)}
                onToggleCompare={() => toggleCompare(row.review_id)}
              />
            ))}
          </tbody>
        </table>
        {rows.length === 0 && (
          <div className="px-3 py-3 text-[11px]" style={{ color: C.fg0 }}>
            No rows match the active filters.
          </div>
        )}
      </div>
      {compare.length > 0 && (
        <div className="flex items-center gap-2 px-3 py-2" style={{ borderTop: `1px solid ${C.border}` }}>
          <span className="text-[10px]" style={{ color: C.fg1 }}>
            {compare.length === 1
              ? compareTarget
                ? 'One selected — compare against the pinned benchmark, or pick a second row.'
                : 'Pick a second row to compare.'
              : 'Two selected.'}
          </span>
          {compareTarget && (
            <button
              onClick={() =>
                navigate(
                  `/review/${encodeURIComponent(compareTarget.from)}?vs=${encodeURIComponent(compareTarget.vs)}`,
                )
              }
              className="rounded px-2 py-0.5 text-[10px] transition hover:bg-white/10"
              style={{ color: C.accent, border: `1px solid ${C.selectedBorder}` }}
            >
              Open focused comparison
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function ExploratoryContext({ scenario, runIds }: { scenario: string; runIds: Set<string> }) {
  const [open, setOpen] = useState(false);
  const runs = useQuery({ queryKey: ['runs'], queryFn: api.runs, enabled: open });
  const experiments = useQuery({ queryKey: ['experiments'], queryFn: api.experiments, enabled: open });
  const scenarioRuns = (runs.data ?? []).filter((run) => runIds.has(run.id));
  const scenarioExperiments = (experiments.data?.experiments ?? []).filter((e) => e.scenario === scenario);
  const diffs = (experiments.data?.revision_diffs ?? []).filter((d) => d.scenario === scenario);
  return (
    <div className="rounded-lg" style={{ border: `1px solid ${C.border}` }}>
      <button className="flex w-full items-center gap-2 px-3 py-2 text-left" onClick={() => setOpen(!open)}>
        {open ? (
          <ChevronDown className="size-3.5" style={{ color: C.fg0 }} />
        ) : (
          <ChevronRight className="size-3.5" style={{ color: C.fg0 }} />
        )}
        <span className="text-xs font-medium" style={{ color: C.fg2 }}>
          Exploratory cohort context
        </span>
        <span className="text-[10px]" style={{ color: C.fg0 }}>
          run-level tradeoffs, failure patterns, revision movement — supporting context, not the verdict
        </span>
      </button>
      {open && (
        <div className="flex flex-col gap-3 p-3" style={{ borderTop: `1px solid ${C.border}` }}>
          <div className="grid items-start gap-3 xl:grid-cols-2">
            <TradeoffScatter runs={scenarioRuns} />
            <FailurePatterns runs={scenarioRuns} />
          </div>
          <RevisionMovement experiments={scenarioExperiments} diffs={diffs} />
        </div>
      )}
    </div>
  );
}

export function ScenarioBoardPage() {
  const review = useQuery({ queryKey: ['review'], queryFn: api.review });

  const scenarios = useMemo(() => {
    const grouped = new Map<string, Board[]>();
    for (const board of review.data?.boards ?? []) {
      const list = grouped.get(board.scenario) ?? [];
      list.push(board);
      grouped.set(board.scenario, list);
    }
    return [...grouped.entries()].map(([scenario, boards]) => ({
      scenario,
      boards: [...boards].sort((a, b) => b.revision.localeCompare(a.revision)),
    }));
  }, [review.data]);

  if (review.isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center text-xs" style={{ color: C.fg1 }}>
        Loading scenario boards…
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
  if (scenarios.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
        <span className="text-sm" style={{ color: C.fg2 }}>
          No scenario boards yet
        </span>
        <span className="max-w-md text-xs leading-relaxed" style={{ color: C.fg0 }}>
          Generate benchmark data first, then rebuild the review dataset:
        </span>
        <code
          className="num rounded px-2.5 py-1.5 text-[11px]"
          style={{ color: C.fg3, background: 'rgba(255,255,255,0.04)', border: `1px solid ${C.border}` }}
        >
          make benchmark-fixture-synthetic && make review-surface-data
        </code>
      </div>
    );
  }

  return (
    <div className="sb flex flex-1 flex-col gap-5 overflow-auto p-4">
      {scenarios.length > 1 && (
        <nav className="flex flex-wrap items-center gap-1.5" aria-label="Scenarios">
          <span className="text-[10px] uppercase tracking-wider" style={{ color: C.fg0 }}>
            scenarios
          </span>
          {scenarios.map(({ scenario }) => (
            <a
              key={scenario}
              href={`#scenario-${scenario}`}
              className="num rounded px-1.5 py-0.5 text-[10px] transition hover:bg-white/10"
              style={{ color: C.accent, border: `1px solid ${C.selectedBorder}` }}
            >
              {scenario}
            </a>
          ))}
        </nav>
      )}
      {scenarios.map(({ scenario, boards }) => (
        <ScenarioSection key={scenario} scenario={scenario} boards={boards} reviews={review.data!.reviews} />
      ))}
    </div>
  );
}

function ScenarioSection({
  scenario,
  boards,
  reviews,
}: {
  scenario: string;
  boards: Board[];
  reviews: Record<string, ReviewDetail>;
}) {
  const [revision, setRevision] = useState(boards[0].revision);
  const board = boards.find((b) => b.revision === revision) ?? boards[0];
  const runIds = new Set(board.rows.flatMap((row) => row.representative.run_ids));
  const revisionSelector =
    boards.length > 1 ? (
      <span className="inline-flex items-center gap-1">
        {boards.map((candidate) => (
          <button
            key={candidate.revision}
            onClick={() => setRevision(candidate.revision)}
            className="num rounded px-1.5 py-0.5 text-[10px] transition"
            style={{
              color: candidate.revision === board.revision ? C.accent : C.fg1,
              border: `1px solid ${candidate.revision === board.revision ? C.selectedBorder : C.border}`,
              background: candidate.revision === board.revision ? C.selected : 'transparent',
            }}
          >
            {candidate.revision}
            {candidate === boards[0] ? ' (current)' : ''}
          </button>
        ))}
      </span>
    ) : undefined;
  return (
    <div id={`scenario-${scenario}`} className="flex scroll-mt-4 flex-col gap-2">
      <ScenarioBoard board={board} reviews={reviews} revisionSelector={revisionSelector} />
      <ExploratoryContext scenario={scenario} runIds={runIds} />
    </div>
  );
}
