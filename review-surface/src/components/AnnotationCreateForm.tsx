// Adapted from Raindrop Workshop (MIT) — app/src/components/TraceAnnotations.tsx (InlineCreateForm)
import { useState, type RefObject } from 'react';
import { KIND_STYLES } from '@/components/AnnotationChip';
import { C } from '@/utils/colors';
import type { AnnotationKind, Span } from '@/utils/types';

const KINDS: AnnotationKind[] = ['issue', 'good', 'note'];

/**
 * Always-visible create form for manual annotations. Attaches to the
 * currently selected span when one is set, otherwise to the run.
 */
export function AnnotationCreateForm({
  selectedSpan,
  onClearSpan,
  onSubmit,
  pending,
  textareaRef,
}: {
  selectedSpan: Span | null;
  onClearSpan: () => void;
  onSubmit: (input: { kind: AnnotationKind; note: string; span_id: string | null }) => void;
  pending: boolean;
  textareaRef: RefObject<HTMLTextAreaElement>;
}) {
  const [kind, setKind] = useState<AnnotationKind>('note');
  const [note, setNote] = useState('');

  function save() {
    const trimmed = note.trim();
    if (!trimmed || pending) return;
    onSubmit({ kind, note: trimmed, span_id: selectedSpan?.id ?? null });
    setNote('');
  }

  return (
    <div
      className="flex flex-col gap-2 rounded-lg p-2.5"
      style={{ background: 'rgba(12,12,13,0.96)', border: `1px solid ${C.border}` }}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        {KINDS.map((k) => {
          const s = KIND_STYLES[k];
          const selected = k === kind;
          return (
            <button
              key={k}
              onClick={() => setKind(k)}
              className="rounded-full px-2.5 text-[10px] font-medium leading-[18px]"
              style={{
                color: selected ? s.fg : C.fg1,
                background: selected ? s.bg : 'rgba(255,255,255,0.025)',
                border: `1px solid ${selected ? s.border : C.border}`,
              }}
            >
              <span className="mr-1 font-bold">{s.icon}</span>
              {s.label}
            </button>
          );
        })}
        <div className="flex-1" />
        <span className="whitespace-nowrap text-[10px]" style={{ color: C.fg0 }}>
          {selectedSpan ? (
            <>
              attaches to span{' '}
              <code className="num rounded bg-white/5 px-1" style={{ color: C.fg2 }}>
                {selectedSpan.name}
              </code>{' '}
              <button className="underline" style={{ color: C.fg1 }} onClick={onClearSpan}>
                use run instead
              </button>
            </>
          ) : (
            'attaches to run'
          )}
          {' · '}
          <kbd className="rounded bg-white/5 px-1">⌘↵</kbd> save
        </span>
      </div>
      <textarea
        ref={textareaRef}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="What did you notice?"
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            save();
          }
        }}
        className="sb min-h-[48px] resize-y rounded-md px-2 py-1.5 text-xs outline-none"
        style={{ border: `1px solid ${C.border}`, background: 'rgba(0,0,0,0.45)', color: C.fg4 }}
      />
      <div className="flex justify-end">
        <button
          onClick={save}
          disabled={pending || note.trim().length === 0}
          className="rounded-md px-2.5 py-1 text-[11px] font-medium disabled:opacity-40"
          style={{ background: C.fg5, color: '#000', border: `1px solid ${C.fg5}` }}
        >
          {pending ? 'Saving…' : selectedSpan ? 'Annotate span' : 'Annotate run'}
        </button>
      </div>
    </div>
  );
}
