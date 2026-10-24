// Jev is parked (2026-10-04): it is out of the scoring and analysis paths, so a
// trial's audited Jev decisions are shown read-only, collapsed, for history.
// Nothing here labels, scores or counts; `JevLabelPanel` stays in the tree,
// dormant, for the day a new user decision un-parks Jev.
import { C } from '@/presentation/colors';
import type { JevDecisionItem } from '@/quality/types';

export function JevHistoryPanel({ decisions }: { decisions: JevDecisionItem[] }) {
  if (decisions.length === 0) return null;
  return (
    <details className="mt-6" data-testid="jev-history">
      <summary className="q-focus cursor-pointer text-[11px]" style={{ color: C.fg2 }}>
        Jev decisions ({decisions.length}) · parked, read-only history; not part of any score
      </summary>
      <ul className="mt-1" aria-label="Jev decisions (history)">
        {decisions.map((decision) => (
          <li
            key={decision.request_id}
            data-testid="jev-history-decision"
            className="flex flex-wrap items-center gap-2 py-1"
            style={{ borderBottom: `1px solid ${C.rowBorder}` }}
          >
            <code className="min-w-0 flex-1 truncate text-[11px]" style={{ color: C.fg3 }} title={decision.request_id}>
              {decision.subject}
            </code>
            <span className="text-[10px]" style={{ color: C.fg1 }}>
              {decision.policy} · {decision.stage} · {decision.verdict}
              {decision.label ? ` · labelled ${decision.label.replace('_', ' ')}` : ''}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}
