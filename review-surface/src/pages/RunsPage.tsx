// Adapted from Raindrop Workshop (MIT) — app/src/components/RunDetail.tsx (layout)
import { useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { api } from '@/api/client';
import { AnnotationCards } from '@/components/AnnotationCards';
import { AnnotationCreateForm } from '@/components/AnnotationCreateForm';
import { FindingChips } from '@/components/FindingChips';
import { GateChips } from '@/components/GateChips';
import { RunHeader } from '@/components/RunHeader';
import { ScorecardPanel } from '@/components/ScorecardPanel';
import { RunListItem } from '@/components/RunListItem';
import { SearchPanel } from '@/components/SearchPanel';
import { SpanDetail } from '@/components/SpanDetail';
import { SpanTree } from '@/components/SpanTree';
import { C } from '@/utils/colors';
import type { Annotation, AnnotationKind, RunDetail as RunDetailData } from '@/utils/types';

const KIND_ORDER: Record<Annotation['kind'], number> = { issue: 0, note: 1, good: 2 };

function sortedAnnotations(annotations: Annotation[]): Annotation[] {
  return [...annotations].sort(
    (a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || a.id.localeCompare(b.id),
  );
}

function RunDetailView({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedSpanId = searchParams.get('span');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [findingsOpen, setFindingsOpen] = useState(true);

  const detail = useQuery({
    queryKey: ['run-detail', runId],
    queryFn: () => api.runDetail(runId),
  });

  const invalidateDetail = () => {
    void queryClient.invalidateQueries({ queryKey: ['run-detail', runId] });
    void queryClient.invalidateQueries({ queryKey: ['annotations', runId] });
  };

  const createMutation = useMutation({
    mutationFn: (input: { kind: AnnotationKind; note: string; span_id: string | null }) =>
      api.createAnnotation({ run_id: runId, span_id: input.span_id, kind: input.kind, note: input.note }),
    onSuccess: invalidateDetail,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteAnnotation(id),
    onSuccess: invalidateDetail,
  });

  function selectSpan(spanId: string | null) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (spanId) next.set('span', spanId);
        else next.delete('span');
        return next;
      },
      { replace: true },
    );
  }

  if (detail.isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-xs" style={{ color: C.fg1 }}>
        Loading run…
      </div>
    );
  }
  if (detail.isError || !detail.data) {
    const message = (detail.error as Error | null)?.message ?? 'unknown error';
    const notFound = message.includes('404');
    return (
      <div className="flex h-full flex-col items-center justify-center gap-1.5">
        <span className="text-sm" style={{ color: notFound ? C.fg3 : C.red }}>
          {notFound ? 'Run not found' : `Failed to load run: ${message}`}
        </span>
        <span className="text-xs" style={{ color: C.fg0 }}>
          {notFound
            ? `No projected run with id “${runId}”. Re-run make review-surface-data if the benchmark exists.`
            : 'Check that the review-surface server is running.'}
        </span>
      </div>
    );
  }

  const data: RunDetailData = detail.data;
  const selectedSpan = selectedSpanId
    ? (data.spans.find((s) => s.id === selectedSpanId) ?? null)
    : null;
  const spanNameById = new Map(data.spans.map((s) => [s.id, s.name]));
  const annotations = sortedAnnotations(data.annotations);
  const counts = {
    issue: annotations.filter((a) => a.kind === 'issue').length,
    good: annotations.filter((a) => a.kind === 'good').length,
    note: annotations.filter((a) => a.kind === 'note').length,
  };

  return (
    <div className="sb flex h-full flex-col gap-3 overflow-auto p-3">
      <RunHeader run={data.run}>
        <GateChips spans={data.spans} onSelect={(spanId) => selectSpan(spanId)} />
      </RunHeader>

      <ScorecardPanel
        spans={data.spans}
        selectedSpanId={selectedSpanId}
        onSelect={(spanId) => selectSpan(spanId)}
      />

      <div
        className="flex flex-col gap-2 rounded-lg p-2.5"
        style={{ background: C.surface, border: `1px solid ${C.border}` }}
      >
        <button
          className="flex items-center gap-2 text-left"
          onClick={() => setFindingsOpen((open) => !open)}
        >
          {findingsOpen ? (
            <ChevronDown className="size-3.5" style={{ color: C.fg0 }} />
          ) : (
            <ChevronRight className="size-3.5" style={{ color: C.fg0 }} />
          )}
          <span className="text-xs font-medium" style={{ color: C.fg3 }}>
            Findings & annotations
          </span>
          <FindingChips counts={counts} />
          <span className="text-[10px]" style={{ color: C.fg0 }}>
            issues first · generated by Raidar from retained evidence
          </span>
        </button>
        {findingsOpen && (
          <div className="sb grid max-h-72 gap-1.5 overflow-auto xl:grid-cols-2">
            <AnnotationCards
              annotations={annotations}
              spanNameById={spanNameById}
              onJumpToSpan={(spanId) => selectSpan(spanId)}
              onDelete={(id) => {
                if (id.startsWith('user-')) deleteMutation.mutate(id);
              }}
              flat
            />
          </div>
        )}
        <AnnotationCreateForm
          selectedSpan={selectedSpan}
          onClearSpan={() => selectSpan(null)}
          onSubmit={(input) => createMutation.mutate(input)}
          pending={createMutation.isPending}
          textareaRef={textareaRef}
        />
      </div>

      <div
        className="flex min-h-[420px] flex-1 flex-col overflow-hidden rounded-lg"
        style={{ border: `1px solid ${C.border}` }}
      >
        <SearchPanel runId={runId} onSelect={(spanId) => selectSpan(spanId)} frameless />
        <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <div
          className="min-h-0 flex-1 lg:basis-[60%]"
          style={{ borderRight: selectedSpan ? `1px solid ${C.border}` : 'none' }}
        >
          <SpanTree
            spans={data.spans}
            annotations={data.annotations}
            selectedSpanId={selectedSpanId}
            onSelect={(spanId) => selectSpan(spanId)}
          />
        </div>
        {selectedSpan && (
          <div className="min-h-0 lg:basis-[40%]" style={{ background: C.surface }}>
            <SpanDetail
              span={selectedSpan}
              annotations={data.annotations}
              onAnnotate={() => {
                textareaRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
                textareaRef.current?.focus();
              }}
            />
          </div>
        )}
        </div>
      </div>
    </div>
  );
}

export function RunsPage() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const [filter, setFilter] = useState('');

  const runs = useQuery({ queryKey: ['runs'], queryFn: api.runs });

  const filtered = useMemo(() => {
    const list = runs.data ?? [];
    const q = filter.trim().toLowerCase();
    if (!q) return list;
    return list.filter(
      (r) =>
        r.scenario.toLowerCase().includes(q) ||
        r.agent_spec.toLowerCase().includes(q) ||
        r.id.toLowerCase().includes(q),
    );
  }, [runs.data, filter]);

  const grouped = useMemo(() => {
    const groups = new Map<string, { scenario: string; spec: string; experimentId: string; runs: typeof filtered }>();
    for (const run of filtered) {
      const key = run.experiment_id;
      const entry = groups.get(key) ?? {
        scenario: `${run.scenario}@${run.revision}`,
        spec: run.agent_spec,
        experimentId: run.experiment_id,
        runs: [],
      };
      entry.runs.push(run);
      groups.set(key, entry);
    }
    return [...groups.values()];
  }, [filtered]);

  return (
    <div className="flex min-h-0 flex-1">
      <aside
        className="flex w-72 shrink-0 flex-col gap-2 p-2.5"
        style={{ borderRight: `1px solid ${C.border}`, background: C.surface }}
      >
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter scenario / agent spec / id…"
          className="rounded-md px-2 py-1.5 text-xs outline-none"
          style={{ border: `1px solid ${C.border}`, background: 'rgba(0,0,0,0.45)', color: C.fg4 }}
        />
        <div className="sb flex min-h-0 flex-1 flex-col gap-1 overflow-auto">
          {runs.isLoading && (
            <div className="p-2 text-xs" style={{ color: C.fg1 }}>
              Loading runs…
            </div>
          )}
          {runs.isError && (
            <div className="p-2 text-xs" style={{ color: C.red }}>
              Failed to load runs.
            </div>
          )}
          {grouped.map((group) => (
            <div key={group.experimentId} className="flex flex-col gap-1">
              <div
                className="sticky top-0 z-10 flex flex-col px-1 py-1"
                style={{ background: C.surface }}
                title={group.experimentId}
              >
                <span className="truncate text-[10px] font-medium" style={{ color: C.fg2 }}>
                  {group.scenario}
                </span>
                <span className="num truncate text-[9px]" style={{ color: C.cyan }}>
                  {group.spec}
                </span>
              </div>
              {group.runs.map((run) => (
                <RunListItem
                  key={run.id}
                  run={run}
                  selected={run.id === runId}
                  onClick={() => navigate(`/runs/${encodeURIComponent(run.id)}`)}
                />
              ))}
            </div>
          ))}
          {runs.data && filtered.length === 0 && (
            <div className="p-2 text-xs" style={{ color: C.fg0 }}>
              No runs match the filter.
            </div>
          )}
        </div>
      </aside>

      <main className="min-w-0 flex-1">
        {runId ? (
          <RunDetailView key={runId} runId={runId} />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
            <span className="text-sm" style={{ color: C.fg2 }}>
              Pick a run to see why it scored what it did
            </span>
            <span className="max-w-sm text-xs leading-relaxed" style={{ color: C.fg0 }}>
              Each run opens with a plain-language verdict, the scorecard behind it, the
              issues Raidar found, and the full execution trace.
            </span>
            <span className="max-w-sm text-xs leading-relaxed" style={{ color: C.fg0 }}>
              Not sure where to start? Compare agent specs on the{' '}
              <Link to="/" className="underline decoration-dotted underline-offset-2" style={{ color: C.accent }}>
                Experiments
              </Link>{' '}
              page — it points you to the run worth opening first.
            </span>
          </div>
        )}
      </main>
    </div>
  );
}
