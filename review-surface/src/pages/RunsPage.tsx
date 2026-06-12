// Adapted from Raindrop Workshop (MIT) — app/src/components/RunDetail.tsx (layout)
import { useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '@/api/client';
import { AnnotationCards } from '@/components/AnnotationCards';
import { AnnotationCreateForm } from '@/components/AnnotationCreateForm';
import { RunHeader } from '@/components/RunHeader';
import { RunListItem } from '@/components/RunListItem';
import { SearchPanel } from '@/components/SearchPanel';
import { SpanDetail } from '@/components/SpanDetail';
import { SpanTree } from '@/components/SpanTree';
import { C } from '@/utils/colors';
import type { AnnotationKind, RunDetail as RunDetailData } from '@/utils/types';

function RunDetailView({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedSpanId = searchParams.get('span');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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
    return (
      <div className="flex h-full items-center justify-center text-xs" style={{ color: C.red }}>
        Failed to load run: {(detail.error as Error | null)?.message ?? 'unknown error'}
      </div>
    );
  }

  const data: RunDetailData = detail.data;
  const selectedSpan = selectedSpanId
    ? (data.spans.find((s) => s.id === selectedSpanId) ?? null)
    : null;
  const spanNameById = new Map(data.spans.map((s) => [s.id, s.name]));

  return (
    <div className="sb flex h-full flex-col gap-3 overflow-auto p-3">
      <RunHeader run={data.run} />

      <div className="flex flex-col gap-2">
        <AnnotationCards
          annotations={data.annotations}
          spanNameById={spanNameById}
          onJumpToSpan={(spanId) => selectSpan(spanId)}
          onDelete={(id) => {
            if (id.startsWith('user-')) deleteMutation.mutate(id);
          }}
        />
        <AnnotationCreateForm
          selectedSpan={selectedSpan}
          onClearSpan={() => selectSpan(null)}
          onSubmit={(input) => createMutation.mutate(input)}
          pending={createMutation.isPending}
          textareaRef={textareaRef}
        />
      </div>

      <SearchPanel runId={runId} onSelect={(spanId) => selectSpan(spanId)} />

      <div
        className="flex min-h-[420px] flex-1 flex-col overflow-hidden rounded-lg lg:flex-row"
        style={{ border: `1px solid ${C.border}` }}
      >
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
          {filtered.map((run) => (
            <RunListItem
              key={run.id}
              run={run}
              selected={run.id === runId}
              onClick={() => navigate(`/runs/${encodeURIComponent(run.id)}`)}
            />
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
          <div className="flex h-full flex-col items-center justify-center gap-1">
            <span className="text-sm" style={{ color: C.fg2 }}>
              Select a run to review its trace
            </span>
            <span className="text-xs" style={{ color: C.fg0 }}>
              Pick a run from the sidebar to see scores, spans, and annotations.
            </span>
          </div>
        )}
      </main>
    </div>
  );
}
