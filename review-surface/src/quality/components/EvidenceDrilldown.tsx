// V/E/H/R/G drill-down: the scorer's file:line evidence for one component,
// at one checkpoint or across all of them, plus files left out of measurement.
import { useMemo } from 'react';
import { X } from 'lucide-react';
import { C } from '@/presentation/colors';
import { COMPONENT_INFO } from '@/quality/format';
import type { Selection } from '@/quality/components/QualityStrip';
import type { CheckpointRow, EvidenceItem, ExclusionItem } from '@/quality/types';

const EMPTY_HINT: Record<string, string> = {
  V: 'No Ruff diagnostics or clone groups in the measured production files.',
  E: 'No function or method above cc 10.',
  H: 'No production call cycles.',
  R: 'No changed lines re-modified earlier-changed symbols.',
  G: 'No changed symbol was executed by a failing regression test.',
};

function location(item: EvidenceItem): string {
  const spans = item.lines.map(([start, end]) => (start === end ? `${start}` : `${start}-${end}`));
  return `${item.path}:${spans.slice(0, 4).join(',')}${spans.length > 4 ? ` +${spans.length - 4}` : ''}`;
}

export function EvidenceDrilldown({
  selection,
  evidence,
  exclusions,
  rows,
  onClose,
}: {
  selection: Selection;
  evidence: EvidenceItem[];
  exclusions: ExclusionItem[];
  rows: CheckpointRow[];
  onClose: () => void;
}) {
  const items = useMemo(
    () =>
      evidence.filter(
        (item) =>
          item.component === selection.component &&
          (selection.checkpoint == null || item.checkpoint === selection.checkpoint),
      ),
    [evidence, selection],
  );
  const excluded = exclusions.filter(
    (item) => selection.checkpoint == null || item.checkpoint === selection.checkpoint,
  );
  const info = COMPONENT_INFO[selection.component];
  const scope = selection.checkpoint ?? 'all checkpoints';
  const measurements = rows.filter(
    (row) => selection.checkpoint == null || row.checkpoint === selection.checkpoint,
  );
  const unmeasured = measurements.filter(
    (row) => row.cells[selection.component].status !== 'ok' || row.cells[selection.component].value == null,
  );
  const complete = measurements.length > 0 && unmeasured.length === 0;
  const clean = complete && measurements.every((row) => row.cells[selection.component].value === 1);
  return (
    <section
      data-testid="evidence-drilldown"
      aria-label={`${info.name} evidence`}
      className="rounded-md"
      style={{ border: `1px solid ${C.borderLight}`, background: C.elevated }}
    >
      <header className="flex items-baseline gap-2 px-3 py-2" style={{ borderBottom: `1px solid ${C.border}` }}>
        <h3 className="text-xs font-semibold" style={{ color: C.fg4 }}>
          {selection.component} · {info.name}
        </h3>
        <span className="text-[10px]" style={{ color: C.fg1 }}>
          {scope} · {items.length} finding{items.length === 1 ? '' : 's'}
        </span>
        <button
          type="button"
          className="q-focus ml-auto rounded p-0.5"
          style={{ color: C.fg2 }}
          aria-label="Close evidence"
          onClick={onClose}
        >
          <X className="size-3.5" />
        </button>
      </header>
      {!complete && (
        <div className="px-3 py-2 text-[11px]" style={{ color: C.orange }} data-testid="evidence-measurement-status">
          <p>Measurement is incomplete for this selection.</p>
          {measurements.length === 0 ? (
            <p>No checkpoint measurement is available.</p>
          ) : (
            <ul>
              {unmeasured.map((row) => {
                const cell = row.cells[selection.component];
                return (
                  <li key={row.checkpoint}>
                    {row.checkpoint}: {cell.status.replace(/_/g, ' ')} · {cell.reason ?? 'No measured value is available.'}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
      {items.length === 0 ? (
        <p className="px-3 py-3 text-[11px]" style={{ color: C.fg1 }}>
          {clean ? EMPTY_HINT[selection.component] : 'No located findings were retained in the available evidence.'}
        </p>
      ) : (
        <ul className="max-h-80 overflow-y-auto">
          {items.map((item, index) => (
            <li
              key={`${item.subject}-${item.path}-${index}`}
              data-testid="evidence-item"
              className="px-3 py-1.5"
              style={{ borderBottom: `1px solid ${C.rowBorder}` }}
            >
              <div className="flex items-baseline gap-2">
                <code className="text-[11px]" style={{ color: C.fg4 }} title={item.snapshot_path ?? item.path}>
                  {location(item)}
                </code>
                <span className="text-[10px]" style={{ color: C.fg1 }}>
                  {item.kind}
                </span>
                {selection.checkpoint == null && (
                  <span className="ml-auto text-[10px]" style={{ color: C.fg0 }}>
                    {item.subject}
                  </span>
                )}
              </div>
              <p className="text-[10px]" style={{ color: C.fg2 }}>
                {item.message}
              </p>
            </li>
          ))}
        </ul>
      )}
      {excluded.length > 0 && (
        <details className="px-3 py-2" style={{ borderTop: `1px solid ${C.border}` }}>
          <summary className="cursor-pointer text-[10px]" style={{ color: C.fg1 }}>
            {excluded.length} file{excluded.length === 1 ? '' : 's'} not measured
          </summary>
          <ul className="mt-1 max-h-40 overflow-y-auto">
            {excluded.map((item, index) => (
              <li key={`${item.checkpoint}-${item.path}-${index}`} className="text-[10px]" style={{ color: C.fg2 }}>
                <code>{item.path}</code> · {item.reason}
                {selection.checkpoint == null && <span style={{ color: C.fg0 }}> · {item.checkpoint}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
