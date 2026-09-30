import type {
  JevLabel,
  JevLabelRecord,
  QualityExperimentsResponse,
  QualityRunDetail,
  QualityRunsResponse,
} from '@/quality/types';

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export const qualityApi = {
  runs: () => getJson<QualityRunsResponse>('/api/quality/runs'),
  run: (runId: string) => getJson<QualityRunDetail>(`/api/quality/runs/${encodeURIComponent(runId)}`),
  experiments: () => getJson<QualityExperimentsResponse>('/api/quality/experiments'),
  labels: (runId: string) =>
    getJson<JevLabelRecord[]>(`/api/quality/labels?run_id=${encodeURIComponent(runId)}`),
  createLabel: async (input: {
    run_id: string;
    request_id: string;
    policy: string;
    label: JevLabel;
    note?: string;
  }): Promise<JevLabelRecord> => {
    const res = await fetch('/api/quality/labels', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(input),
    });
    if (!res.ok) throw new Error(`label decision -> ${res.status}`);
    return res.json() as Promise<JevLabelRecord>;
  },
  deleteLabel: async (id: string): Promise<void> => {
    const res = await fetch(`/api/quality/labels/${encodeURIComponent(id)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`remove label -> ${res.status}`);
  },
};
