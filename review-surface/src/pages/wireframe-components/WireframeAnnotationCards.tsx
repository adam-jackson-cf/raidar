import { Crosshair } from 'lucide-react';
import { C } from '@/utils/colors';
import type { Annotation, AnnotationKind } from '@/utils/types';

function findingHeadline(annotation: Annotation) {
  if (annotation.category === 'clean-verification') return 'No verification gates failed';
  if (annotation.category === 'requirements-satisfied') return annotation.note ?? 'All requirements satisfied';
  if (annotation.category === 'retained-evidence') return annotation.note ?? 'Required evidence was retained';
  if (annotation.category === 'requirements-gap') return annotation.note ?? 'Requirements were not satisfied';
  if (annotation.category === 'failed-gate') return annotation.note ?? 'A verification gate failed';
  if (annotation.category === 'missing-required-command') return annotation.note ?? 'A required verification command was not run';
  if (annotation.category === 'missing-artifact') return annotation.note ?? 'Expected evidence was missing';
  if (annotation.category === 'judge-review') return annotation.note ?? 'Judge-backed metric needs review';
  if (annotation.category === 'deterministic-cap') return annotation.note ?? 'A deterministic prerequisite capped the metric';
  if (annotation.category === 'workflow-anomaly') return annotation.note ?? 'Workflow anomaly detected';
  if (annotation.category === 'completion-claim') return annotation.note ?? 'Completion claim failed validation';
  if (annotation.category === 'performance-gate') return annotation.note ?? 'Performance gate failed';
  return annotation.note ?? '(no note)';
}

const FINDING_COLUMNS: Array<{
  kind: AnnotationKind;
  title: string;
  icon: string;
  color: string;
}> = [
  { kind: 'good', title: 'Good', icon: '✓', color: C.green },
  { kind: 'issue', title: 'Issue', icon: '✕', color: C.red },
  { kind: 'note', title: 'Note', icon: '•', color: C.cyan },
];

export function WireframeAnnotationCards({
  annotations,
  spanNameById,
  onJumpToSpan,
}: {
  annotations: Annotation[];
  spanNameById: Map<string, string>;
  onJumpToSpan: (spanId: string) => void;
}) {
  if (annotations.length === 0) return null;

  const grouped = FINDING_COLUMNS.map((column) => ({
    ...column,
    annotations: annotations.filter((annotation) => annotation.kind === column.kind),
  }));

  return (
    <div className="grid gap-3 md:grid-cols-3">
      {grouped.map((group) => (
        <div
          key={group.kind}
          className="min-w-0 rounded-lg border px-2.5 py-2"
          style={{
            borderColor: 'rgba(255,255,255,0.08)',
            background: 'linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.015))',
          }}
        >
          <div className="mb-2 flex items-center gap-2">
            <span className="size-2 rounded-sm" style={{ background: group.color }} />
            <span className="text-[11px] font-medium uppercase tracking-[0.16em]" style={{ color: C.fg2 }}>
              {group.title}
            </span>
          </div>
          <div className="space-y-1.5">
            {group.annotations.length === 0 && (
              <div className="num text-xs" style={{ color: C.fg0 }}>
                -
              </div>
            )}
            {group.annotations.map((annotation) => {
              const spanName = annotation.span_id ? spanNameById.get(annotation.span_id) : null;
              const canJump = Boolean(annotation.span_id);
              return (
                <div key={annotation.id} className="flex items-start gap-2 text-xs leading-relaxed" style={{ color: C.fg4 }}>
                  <span className="num mt-px w-3 shrink-0" style={{ color: group.color }}>
                    {group.icon}
                  </span>
                  <span className="min-w-0 flex-1">{findingHeadline(annotation)}</span>
                  {canJump && (
                    <button
                      onClick={() => onJumpToSpan(annotation.span_id as string)}
                      title={spanName ? `jump: ${spanName}` : 'jump to span'}
                      aria-label={spanName ? `jump: ${spanName}` : 'jump to span'}
                      className="mt-px inline-flex shrink-0 items-center rounded p-0.5 transition hover:bg-white/10"
                      style={{ color: C.accent, border: `1px solid ${C.selectedBorder}` }}
                    >
                      <Crosshair className="size-2.5" />
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
