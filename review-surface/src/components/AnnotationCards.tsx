// Adapted from Raindrop Workshop (MIT) — app/src/components/TraceAnnotations.tsx
import { Crosshair, Trash2 } from 'lucide-react';
import { AnnotationChip, KIND_STYLES, SOURCE_GLYPH, annotationSourceLabel } from '@/components/AnnotationChip';
import { C } from '@/utils/colors';
import { categoryHint, categoryLabel } from '@/utils/verdict';
import type { Annotation, FindingEvidenceRef } from '@/utils/types';

export function canDeleteAnnotation(annotation: Annotation): boolean {
  return annotation.source === 'user' && annotation.id.startsWith('user-');
}

export function EvidenceRefList({ evidence }: { evidence: FindingEvidenceRef[] }) {
  if (evidence.length === 0) return null;
  return (
    <div className="mt-1.5 flex flex-col gap-1">
      {evidence.map((ref, i) => (
        <div key={i} className="flex items-baseline gap-2 text-[10px]">
          <code className="num shrink-0 rounded bg-white/5 px-1 py-px" style={{ color: C.fg2 }}>
            {ref.source}:{ref.reference}
          </code>
          {ref.detail && <span style={{ color: C.fg1 }}>{ref.detail}</span>}
        </div>
      ))}
    </div>
  );
}

export function AnnotationCard({
  annotation,
  spanName,
  onJumpToSpan,
  onDelete,
}: {
  annotation: Annotation;
  spanName?: string | null;
  onJumpToSpan?: () => void;
  onDelete?: () => void;
}) {
  const style = KIND_STYLES[annotation.kind];
  return (
    <div
      className="flex items-start gap-2.5 rounded-lg px-2.5 py-2"
      style={{
        border: `1px solid ${style.border}`,
        background: `linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.015)), ${style.bg}`,
        boxShadow: '0 1px 0 rgba(255,255,255,0.03) inset',
      }}
    >
      <span className="mt-px w-3.5 shrink-0 text-center text-[11px]" style={{ color: C.fg0 }}>
        {SOURCE_GLYPH[annotation.source]}
      </span>
      <div className="min-w-0 flex-1">
        <div className="mb-0.5 flex flex-wrap items-center gap-2">
          <AnnotationChip annotation={annotation} showLabel />
          {annotation.category && (
            <span
              className="text-[10px] font-medium"
              style={{ color: C.fg2 }}
              title={`${categoryHint(annotation.category)} (${annotation.category})`}
            >
              {categoryLabel(annotation.category)}
            </span>
          )}
          <span className="text-[10px]" style={{ color: C.fg0 }}>
            {annotationSourceLabel(annotation.source)}
          </span>
          {annotation.span_id && onJumpToSpan && (
            <button
              onClick={onJumpToSpan}
              title={spanName ? `Jump to span: ${spanName}` : 'Jump to span'}
              className="inline-flex items-center gap-1 rounded px-1.5 py-px text-[10px] transition hover:bg-white/10"
              style={{ color: C.accent, border: `1px solid ${C.selectedBorder}` }}
            >
              <Crosshair className="size-2.5" />
              {spanName ? `jump: ${spanName}` : 'jump to span'}
            </button>
          )}
        </div>
        {annotation.note ? (
          <div className="text-xs leading-relaxed" style={{ color: C.fg4 }}>
            {annotation.note}
          </div>
        ) : (
          <em className="text-xs" style={{ color: C.fg0 }}>
            (no note)
          </em>
        )}
        <EvidenceRefList evidence={annotation.evidence} />
      </div>
      {onDelete && canDeleteAnnotation(annotation) && (
        <button
          onClick={onDelete}
          title="Delete annotation"
          className="rounded p-1 transition hover:bg-white/10"
          style={{ color: C.fg0 }}
        >
          <Trash2 className="size-3" />
        </button>
      )}
    </div>
  );
}

export function AnnotationCards({
  annotations,
  spanNameById,
  onJumpToSpan,
  onDelete,
  flat = false,
}: {
  annotations: Annotation[];
  spanNameById?: Map<string, string>;
  onJumpToSpan?: (spanId: string) => void;
  onDelete?: (id: string) => void;
  flat?: boolean;
}) {
  if (annotations.length === 0) return null;
  const cards = annotations.map((a) => (
    <AnnotationCard
      key={a.id}
      annotation={a}
      spanName={a.span_id ? spanNameById?.get(a.span_id) : null}
      onJumpToSpan={a.span_id && onJumpToSpan ? () => onJumpToSpan(a.span_id as string) : undefined}
      onDelete={onDelete ? () => onDelete(a.id) : undefined}
    />
  ));
  if (flat) return <>{cards}</>;
  return <div className="flex flex-col gap-1.5">{cards}</div>;
}
