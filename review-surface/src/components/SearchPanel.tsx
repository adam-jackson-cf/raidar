import { useState, type FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { api } from '@/api/client';
import { C } from '@/utils/colors';
import type { SearchMatch } from '@/utils/types';

const MATCH_OPEN = '<<MATCH>>';
const MATCH_CLOSE = '<<END>>';

function Snippet({ snippet }: { snippet: string }) {
  const open = snippet.indexOf(MATCH_OPEN);
  const close = snippet.indexOf(MATCH_CLOSE, open + MATCH_OPEN.length);
  if (open === -1 || close === -1) {
    return (
      <span className="num text-[10px]" style={{ color: C.fg1 }}>
        {snippet}
      </span>
    );
  }
  const before = snippet.slice(0, open);
  const match = snippet.slice(open + MATCH_OPEN.length, close);
  const after = snippet.slice(close + MATCH_CLOSE.length);
  return (
    <span className="num break-all text-[10px]" style={{ color: C.fg1 }}>
      {before}
      <span
        className="rounded-sm px-px font-bold"
        style={{ color: C.accent, background: `${C.accent}1f` }}
      >
        {match}
      </span>
      {after}
    </span>
  );
}

export function SearchPanel({
  runId,
  onSelect,
}: {
  runId: string;
  onSelect: (spanId: string) => void;
}) {
  const [pattern, setPattern] = useState('');
  const [regex, setRegex] = useState(false);
  const [submitted, setSubmitted] = useState<{ pattern: string; regex: boolean } | null>(null);

  const search = useQuery({
    queryKey: ['search', runId, submitted?.pattern, submitted?.regex],
    queryFn: () => api.searchRun(runId, submitted?.pattern ?? '', { regex: submitted?.regex }),
    enabled: submitted !== null && submitted.pattern.length > 0,
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    const trimmed = pattern.trim();
    setSubmitted(trimmed ? { pattern: trimmed, regex } : null);
  }

  return (
    <div
      className="flex flex-col gap-2 rounded-lg p-2.5"
      style={{ background: C.surface, border: `1px solid ${C.border}` }}
    >
      <form className="flex items-center gap-2" onSubmit={submit}>
        <Search className="size-3.5 shrink-0" style={{ color: C.fg0 }} />
        <input
          value={pattern}
          onChange={(e) => setPattern(e.target.value)}
          placeholder="Search span payloads…"
          className="num min-w-0 flex-1 rounded-md px-2 py-1 text-xs outline-none"
          style={{ border: `1px solid ${C.border}`, background: 'rgba(0,0,0,0.45)', color: C.fg4 }}
        />
        <label className="flex shrink-0 items-center gap-1 text-[10px]" style={{ color: C.fg1 }}>
          <input type="checkbox" checked={regex} onChange={(e) => setRegex(e.target.checked)} />
          regex
        </label>
        <button
          type="submit"
          className="shrink-0 rounded-md px-2 py-1 text-[11px] font-medium"
          style={{ background: C.fg5, color: '#000' }}
        >
          Search
        </button>
      </form>

      {search.isFetching && (
        <div className="text-[10px]" style={{ color: C.fg0 }}>
          Searching…
        </div>
      )}
      {search.isError && (
        <div className="text-[10px]" style={{ color: C.red }}>
          Search failed: {(search.error as Error).message}
        </div>
      )}
      {search.data && (
        <div className="flex flex-col gap-1">
          <div className="text-[10px]" style={{ color: C.fg0 }}>
            {search.data.matches.length} match{search.data.matches.length === 1 ? '' : 'es'}
            {search.data.truncated && (
              <span style={{ color: C.orange }}> · results truncated, refine your pattern</span>
            )}
          </div>
          <div className="sb flex max-h-56 flex-col gap-0.5 overflow-auto">
            {search.data.matches.map((m: SearchMatch, i: number) => (
              <button
                key={`${m.span_id}-${i}`}
                className="flex flex-col items-start gap-0.5 rounded px-1.5 py-1 text-left transition hover:bg-white/5"
                onClick={() => onSelect(m.span_id)}
              >
                <span className="flex items-center gap-2">
                  <span className="num text-[11px]" style={{ color: C.fg3 }}>
                    {m.span_name}
                  </span>
                  <span
                    className="rounded px-1 text-[9px] uppercase tracking-wide"
                    style={{ color: C.fg1, background: 'rgba(255,255,255,0.05)' }}
                  >
                    {m.scope}
                  </span>
                </span>
                <Snippet snippet={m.snippet} />
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
