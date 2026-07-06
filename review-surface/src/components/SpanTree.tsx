// Adapted from Raindrop Workshop (MIT) — app/src/components/SpanTree.tsx
import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, ChevronsDownUp, ChevronsUpDown, CircleAlert } from 'lucide-react';
import { AnnotationChip } from '@/components/AnnotationChip';
import { C, SPAN_TYPE_INFO } from '@/utils/colors';
import { fmtDuration } from '@/utils/helpers';
import type { Annotation, Span } from '@/utils/types';

interface FlatRow {
  span: Span;
  depth: number;
  hasChildren: boolean;
}

function buildRows(spans: Span[], collapsed: Set<string>): FlatRow[] {
  const byId = new Map(spans.map((s) => [s.id, s]));
  const children = new Map<string, Span[]>();
  const roots: Span[] = [];
  for (const s of spans) {
    if (!s.parent_span_id || !byId.has(s.parent_span_id)) {
      roots.push(s);
    } else {
      const list = children.get(s.parent_span_id) ?? [];
      list.push(s);
      children.set(s.parent_span_id, list);
    }
  }
  const rows: FlatRow[] = [];
  const walk = (span: Span, depth: number) => {
    const kids = children.get(span.id) ?? [];
    rows.push({ span, depth, hasChildren: kids.length > 0 });
    if (!collapsed.has(span.id)) {
      for (const kid of kids) walk(kid, depth + 1);
    }
  };
  for (const root of roots) walk(root, 0);
  return rows;
}

function SpanRow({
  row,
  selected,
  collapsed,
  minTime,
  totalDur,
  annotations,
  onSelect,
  onToggle,
}: {
  row: FlatRow;
  selected: boolean;
  collapsed: boolean;
  minTime: number;
  totalDur: number;
  annotations: Annotation[];
  onSelect: () => void;
  onToggle: () => void;
}) {
  const { span, depth, hasChildren } = row;
  const info = SPAN_TYPE_INFO[span.span_type];
  const isErr = span.status === 'ERROR';
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (selected) ref.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [selected]);

  const hasBar = span.start_time_ms != null && totalDur > 0;
  const leftPct = hasBar ? (((span.start_time_ms as number) - minTime) / totalDur) * 100 : 0;
  const widthPct = hasBar ? Math.max(((span.duration_ms ?? 0) / totalDur) * 100, 0.5) : 0;

  return (
    <div
      ref={ref}
      data-span-row={span.id}
      className="flex cursor-pointer items-center"
      style={{
        minHeight: 28,
        borderBottom: `1px solid ${C.rowBorder}`,
        background: selected ? C.selected : isErr ? C.traceErrorBg : 'transparent',
        borderLeft: selected
          ? `2px solid ${C.accent}`
          : isErr
            ? `2px solid ${C.red}`
            : '2px solid transparent',
        transition: 'background 0.2s ease',
      }}
      onClick={onSelect}
    >
      <div
        className="flex min-w-0 flex-shrink-0 items-center gap-1.5"
        style={{ width: 280, paddingLeft: depth * 14 + 6 }}
      >
        {hasChildren ? (
          <button
            className="flex size-4 flex-shrink-0 items-center justify-center rounded transition hover:bg-white/10"
            title={collapsed ? 'Expand' : 'Collapse'}
            onClick={(e) => {
              e.stopPropagation();
              onToggle();
            }}
          >
            <ChevronRight
              className="size-3 transition-transform"
              style={{ color: C.fg1, transform: collapsed ? '' : 'rotate(90deg)' }}
            />
          </button>
        ) : (
          <span className="size-4 flex-shrink-0" />
        )}
        <span
          className="num rounded px-1 py-0.5 text-[9px] font-bold"
          style={{ color: info.color, background: `${info.color}12` }}
        >
          {info.label}
        </span>
        <span
          className="num truncate text-[11px]"
          style={{ color: isErr ? C.red : info.color }}
          title={span.name}
        >
          {span.name}
        </span>
        {annotations.map((a) => (
          <AnnotationChip key={a.id} annotation={a} />
        ))}
      </div>
      <div className="relative mx-2 flex-1" style={{ height: 10 }}>
        {hasBar && (
          <div
            className="absolute rounded-sm"
            style={{
              left: `${leftPct}%`,
              width: `${widthPct}%`,
              top: 0,
              height: 10,
              minWidth: 2,
              backgroundColor: isErr ? C.red : info.color,
              boxShadow: `0 0 8px ${isErr ? C.red : info.color}80`,
              opacity: selected ? 1 : 0.85,
            }}
          />
        )}
      </div>
      <div className="flex-shrink-0 pr-3 text-right" style={{ width: 58 }}>
        <span className="num text-[10px]" style={{ color: C.fg0 }}>
          {fmtDuration(span.duration_ms)}
        </span>
      </div>
    </div>
  );
}

export function SpanTree({
  spans,
  annotations,
  selectedSpanId,
  onSelect,
}: {
  spans: Span[];
  annotations: Annotation[];
  selectedSpanId: string | null;
  onSelect: (spanId: string | null) => void;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const parentById = useMemo(
    () => new Map(spans.map((s) => [s.id, s.parent_span_id])),
    [spans],
  );
  const sectionIds = useMemo(() => {
    // spans with children whose parent is a root span (depth 1 sections)
    const hasChildren = new Set(spans.map((s) => s.parent_span_id).filter(Boolean) as string[]);
    const rootIds = new Set(
      spans.filter((s) => !s.parent_span_id || !parentById.has(s.parent_span_id)).map((s) => s.id),
    );
    return spans
      .filter((s) => hasChildren.has(s.id) && s.parent_span_id != null && rootIds.has(s.parent_span_id))
      .map((s) => s.id);
  }, [spans, parentById]);
  const errorSpans = useMemo(
    () => buildRows(spans, new Set()).filter((row) => row.span.status === 'ERROR').map((r) => r.span.id),
    [spans],
  );

  // Selecting a span (deep link, search, finding jump) reveals it.
  useEffect(() => {
    if (!selectedSpanId) return;
    setCollapsed((prev) => {
      const next = new Set(prev);
      let cursor = parentById.get(selectedSpanId) ?? null;
      while (cursor) {
        next.delete(cursor);
        cursor = parentById.get(cursor) ?? null;
      }
      return next;
    });
  }, [selectedSpanId, parentById]);

  function nextError() {
    if (errorSpans.length === 0) return;
    const current = selectedSpanId ? errorSpans.indexOf(selectedSpanId) : -1;
    onSelect(errorSpans[(current + 1) % errorSpans.length]);
  }

  const rows = useMemo(() => buildRows(spans, collapsed), [spans, collapsed]);

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (!['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Escape'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'Escape') {
      onSelect(null);
      return;
    }
    const index = rows.findIndex((row) => row.span.id === selectedSpanId);
    if (event.key === 'ArrowDown') {
      const next = rows[Math.min(index + 1, rows.length - 1)] ?? rows[0];
      if (next) onSelect(next.span.id);
      return;
    }
    if (event.key === 'ArrowUp') {
      const previous = rows[Math.max(index - 1, 0)];
      if (previous) onSelect(previous.span.id);
      return;
    }
    if (!selectedSpanId) return;
    const row = rows[index];
    if (!row) return;
    if (event.key === 'ArrowRight' && row.hasChildren && collapsed.has(row.span.id)) {
      toggle(row.span.id);
    }
    if (event.key === 'ArrowLeft' && row.hasChildren && !collapsed.has(row.span.id)) {
      toggle(row.span.id);
    }
  }

  const annotationsBySpan = useMemo(() => {
    const map = new Map<string, Annotation[]>();
    for (const a of annotations) {
      if (!a.span_id) continue;
      const list = map.get(a.span_id) ?? [];
      list.push(a);
      map.set(a.span_id, list);
    }
    return map;
  }, [annotations]);

  const { minTime, totalDur } = useMemo(() => {
    const starts = spans.filter((s) => s.start_time_ms != null);
    if (starts.length === 0) return { minTime: 0, totalDur: 0 };
    const min = Math.min(...starts.map((s) => s.start_time_ms as number));
    const max = Math.max(...starts.map((s) => s.end_time_ms ?? (s.start_time_ms as number)));
    return { minTime: min, totalDur: Math.max(max - min, 1) };
  }, [spans]);

  const toggle = (id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  if (spans.length === 0) {
    return (
      <div className="p-4 text-xs" style={{ color: C.fg1 }}>
        No spans recorded for this run.
      </div>
    );
  }

  return (
    <div
      className="sb h-full overflow-auto outline-none"
      tabIndex={0}
      role="tree"
      aria-label="Span tree — arrow keys navigate, escape clears selection"
      onKeyDown={handleKeyDown}
    >
      <div
        className="sticky top-0 z-10 flex items-center gap-1.5 px-2 py-1.5"
        style={{ background: C.surface, borderBottom: `1px solid ${C.border}` }}
      >
        <div
          className="flex items-center gap-1.5 text-[9px] font-medium uppercase tracking-wider"
          style={{ color: C.fg0, width: 280 }}
        >
          Span
          <button
            title="Expand all"
            className="rounded p-0.5 transition hover:bg-white/10"
            onClick={() => setCollapsed(new Set())}
          >
            <ChevronsUpDown className="size-3" style={{ color: C.fg1 }} />
          </button>
          <button
            title="Collapse to sections"
            className="rounded p-0.5 transition hover:bg-white/10"
            onClick={() => setCollapsed(new Set(sectionIds))}
          >
            <ChevronsDownUp className="size-3" style={{ color: C.fg1 }} />
          </button>
          {errorSpans.length > 0 && (
            <button
              title="Cycle through error spans"
              onClick={nextError}
              className="num inline-flex items-center gap-1 rounded-full px-1.5 py-px text-[10px] normal-case tracking-normal transition hover:bg-white/10"
              style={{ color: C.red, background: `${C.red}10`, border: `1px solid ${C.red}38` }}
            >
              <CircleAlert className="size-2.5" />
              {errorSpans.length} error{errorSpans.length === 1 ? '' : 's'} ▸
            </button>
          )}
        </div>
        <div className="flex-1 text-[9px] font-medium uppercase tracking-wider" style={{ color: C.fg0 }}>
          Timeline
          <span className="ml-2 normal-case tracking-normal" style={{ color: C.fg0 }}>
            ↑↓ navigate · ←→ fold · esc clear
          </span>
        </div>
        <div
          className="pr-3 text-right text-[9px] font-medium uppercase tracking-wider"
          style={{ color: C.fg0, width: 58 }}
        >
          Dur
        </div>
      </div>
      {rows.map((row) => (
        <SpanRow
          key={row.span.id}
          row={row}
          selected={row.span.id === selectedSpanId}
          collapsed={collapsed.has(row.span.id)}
          minTime={minTime}
          totalDur={totalDur}
          annotations={annotationsBySpan.get(row.span.id) ?? []}
          onSelect={() => onSelect(row.span.id === selectedSpanId ? null : row.span.id)}
          onToggle={() => toggle(row.span.id)}
        />
      ))}
    </div>
  );
}
