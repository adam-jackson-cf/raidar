// Failure-pattern rollup adapted from benchmark-view's "Failure root causes"
// panel: aggregates issue findings and failed gates across a scenario family's
// runs so reviewers can triage what keeps going wrong and where.
import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { CircleAlert } from 'lucide-react';
import { C } from '@/utils/colors';
import { categoryHint, categoryLabel, runLabel } from '@/utils/verdict';
import type { RunRecord } from '@/utils/types';

interface Pattern {
  label: string;
  hint: string;
  kind: 'gate' | 'category';
  count: number;
  runs: RunRecord[];
}

function buildPatterns(runs: RunRecord[]): Pattern[] {
  const byKey = new Map<string, Pattern>();
  for (const run of runs) {
    for (const gate of run.failed_gates) {
      const key = `gate:${gate}`;
      const entry = byKey.get(key) ?? {
        label: `‘${gate}’ gate fails`,
        hint: `The ${gate} verification gate did not pass`,
        kind: 'gate' as const,
        count: 0,
        runs: [],
      };
      entry.count += 1;
      if (!entry.runs.includes(run)) entry.runs.push(run);
      byKey.set(key, entry);
    }
    for (const [category, count] of Object.entries(run.issue_categories)) {
      if (category === 'failed-gate') continue; // covered by the gate rows above
      const key = `cat:${category}`;
      const entry = byKey.get(key) ?? {
        label: categoryLabel(category),
        hint: categoryHint(category),
        kind: 'category' as const,
        count: 0,
        runs: [],
      };
      entry.count += count;
      if (!entry.runs.includes(run)) entry.runs.push(run);
      byKey.set(key, entry);
    }
  }
  return [...byKey.values()].sort((a, b) => b.count - a.count);
}

export function FailurePatterns({ runs }: { runs: RunRecord[] }) {
  const patterns = useMemo(() => buildPatterns(runs), [runs]);
  if (patterns.length === 0) return null;

  return (
    <div
      className="flex flex-col gap-1.5 rounded-lg p-2.5"
      style={{ background: C.surface, border: `1px solid ${C.border}` }}
    >
      <div className="flex items-center gap-2">
        <CircleAlert className="size-3.5" style={{ color: C.red }} />
        <span className="text-xs font-medium" style={{ color: C.fg3 }}>
          Failure patterns
        </span>
        <span className="text-[10px]" style={{ color: C.fg0 }}>
          recurring issues across this scenario's runs — open a run to inspect the evidence
        </span>
      </div>
      <div className="flex flex-col gap-1">
        {patterns.map((pattern) => (
          <div key={`${pattern.kind}:${pattern.label}`} className="flex flex-wrap items-center gap-2">
            <span
              className="num rounded px-1.5 py-px text-[10px] font-medium"
              style={{
                color: C.red,
                background: 'rgba(235,20,20,0.07)',
                border: '1px solid rgba(235,20,20,0.3)',
              }}
            >
              {pattern.count}×
            </span>
            <span className="text-[11px]" style={{ color: C.fg3 }} title={pattern.hint}>
              {pattern.label}
            </span>
            <span className="flex flex-wrap items-center gap-1">
              {pattern.runs.map((run) => (
                <Link
                  key={run.id}
                  to={`/runs/${encodeURIComponent(run.id)}`}
                  className="rounded px-1 py-px text-[10px] transition hover:bg-white/10"
                  style={{ color: C.accent }}
                  title={`${run.id} (${run.agent_spec}) — open run review`}
                >
                  {runLabel(run.id)}
                </Link>
              ))}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
