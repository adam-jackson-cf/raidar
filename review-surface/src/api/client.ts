import type {
  Annotation,
  AnnotationKind,
  ExperimentsResponse,
  RunDetail,
  RunOutline,
  RunRecord,
  SearchMatch,
} from '@/utils/types';

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  runs: () => getJson<RunRecord[]>('/api/runs'),
  runDetail: (runId: string) => getJson<RunDetail>(`/api/runs/detail/${encodeURIComponent(runId)}`),
  runOutline: (runId: string) =>
    getJson<RunOutline>(`/api/runs/${encodeURIComponent(runId)}/outline`),
  searchRun: (runId: string, pattern: string, options?: { regex?: boolean; caseSensitive?: boolean }) => {
    const params = new URLSearchParams({ pattern });
    if (options?.regex) params.set('regex', 'true');
    if (options?.caseSensitive) params.set('case_sensitive', 'true');
    return getJson<{ matches: SearchMatch[]; truncated: boolean }>(
      `/api/runs/${encodeURIComponent(runId)}/search?${params}`,
    );
  },
  experiments: () => getJson<ExperimentsResponse>('/api/experiments'),
  annotations: (runId: string) =>
    getJson<Annotation[]>(`/api/annotations?run_id=${encodeURIComponent(runId)}`),
  createAnnotation: async (input: {
    run_id: string;
    span_id?: string | null;
    kind: AnnotationKind;
    note?: string;
  }): Promise<Annotation> => {
    const res = await fetch('/api/annotations', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(input),
    });
    if (!res.ok) throw new Error(`create annotation -> ${res.status}`);
    return res.json() as Promise<Annotation>;
  },
  deleteAnnotation: async (id: string): Promise<void> => {
    const res = await fetch(`/api/annotations/${encodeURIComponent(id)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`delete annotation -> ${res.status}`);
  },
};
