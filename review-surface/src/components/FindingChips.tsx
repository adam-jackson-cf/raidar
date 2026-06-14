import { KIND_STYLES } from '@/components/AnnotationChip';
import type { AnnotationKind } from '@/utils/types';

const ORDER: AnnotationKind[] = ['issue', 'good', 'note'];

/** Compact issue/good/note count chips used in run lists and experiment tables. */
export function FindingChips({ counts }: { counts: Partial<Record<AnnotationKind, number>> }) {
  const visible = ORDER.filter((kind) => (counts[kind] ?? 0) > 0);
  if (visible.length === 0) return null;
  return (
    <span className="inline-flex items-center gap-1">
      {visible.map((kind) => {
        const style = KIND_STYLES[kind];
        return (
          <span
            key={kind}
            title={`${counts[kind]} ${style.label} finding${(counts[kind] ?? 0) === 1 ? '' : 's'}`}
            className="num inline-flex h-[18px] items-center gap-1 rounded-full px-1.5 text-[10px] font-medium leading-[18px]"
            style={{ color: style.fg, background: style.bg, border: `1px solid ${style.border}` }}
          >
            <span className="text-[10px] font-bold">{style.icon}</span>
            {counts[kind]}
          </span>
        );
      })}
    </span>
  );
}
