// Adapted from Raindrop Workshop (MIT) — app/src/components/AnnotationChip.tsx
import type { Annotation, AnnotationKind } from '@/utils/types';

export const KIND_STYLES: Record<
  AnnotationKind,
  { icon: string; label: string; fg: string; bg: string; border: string }
> = {
  issue: { icon: '!', label: 'issue', fg: '#f87171', bg: 'rgba(220,38,38,0.12)', border: 'rgba(220,38,38,0.35)' },
  good: { icon: '✓', label: 'good', fg: '#34d399', bg: 'rgba(5,150,105,0.12)', border: 'rgba(5,150,105,0.35)' },
  note: { icon: '·', label: 'note', fg: '#60a5fa', bg: 'rgba(37,99,235,0.12)', border: 'rgba(37,99,235,0.35)' },
};

export const SOURCE_GLYPH: Record<Annotation['source'], string> = {
  raidar: '◆',
  user: '·',
};

export function annotationSourceLabel(source: Annotation['source']): string {
  return source === 'raidar' ? 'Raidar' : 'You';
}

/** Icon-only chip (for span rows, run-list badges, etc.). */
export function AnnotationChip({
  annotation,
  title,
  showLabel = false,
}: {
  annotation: Annotation;
  title?: string;
  showLabel?: boolean;
}) {
  const style = KIND_STYLES[annotation.kind];
  return (
    <span
      title={title ?? annotation.note ?? style.label}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 3,
        padding: showLabel ? '0 7px' : '0 5px',
        height: 18,
        lineHeight: '18px',
        borderRadius: 999,
        fontSize: 10,
        fontWeight: 500,
        fontFamily: 'inherit',
        color: style.fg,
        background: style.bg,
        border: `1px solid ${style.border}`,
        whiteSpace: 'nowrap',
      }}
    >
      <span style={{ fontWeight: 700, fontSize: 10 }}>{style.icon}</span>
      {showLabel ? style.label : null}
      <span style={{ fontSize: 8, opacity: 0.65, marginLeft: 1 }}>{SOURCE_GLYPH[annotation.source]}</span>
    </span>
  );
}
