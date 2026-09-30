// Quality element API (plan K2/K4): serves the Python-emitted Quality
// projection and persists reviewer Jev calibration labels locally. Mounted by
// server.mjs under /api/quality/*; zero runtime dependencies.
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

export const JEV_LABELS = ['correct', 'false_positive', 'false_negative', 'uncertain'];
const SAFE_ID = /^[A-Za-z0-9._-]+$/;

function readJson(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch {
    return fallback;
  }
}

export function createQualityApi({ dataRoot, sendJson, readBody }) {
  const qualityRoot = path.join(dataRoot, 'quality');
  const labelsPath = path.join(dataRoot, 'jev-labels.json');

  const loadDetail = (runId) =>
    SAFE_ID.test(runId) ? readJson(path.join(qualityRoot, 'runs', `${runId}.json`), null) : null;
  const loadLabels = () => readJson(labelsPath, []);
  const saveLabels = (labels) => {
    fs.mkdirSync(dataRoot, { recursive: true });
    fs.writeFileSync(labelsPath, JSON.stringify(labels, null, 2));
  };

  async function createLabel(req, res) {
    const body = await readBody(req);
    if (!body?.run_id || !body?.request_id || !body?.policy || !JEV_LABELS.includes(body.label)) {
      return sendJson(res, 400, { error: `run_id, request_id, policy and label (${JEV_LABELS.join('|')}) required` });
    }
    const detail = loadDetail(String(body.run_id));
    const decision = detail?.jev_decisions?.find(
      (item) => item.request_id === body.request_id && item.policy === body.policy,
    );
    if (!decision) return sendJson(res, 404, { error: 'no such Jev decision on this run' });
    if (decision.label) return sendJson(res, 409, { error: 'decision already carries an imported label' });
    const label = {
      id: `jevlabel-${randomUUID()}`,
      run_id: String(body.run_id),
      request_id: String(body.request_id),
      policy: String(body.policy),
      label: body.label,
      note: body.note ? String(body.note).slice(0, 4000) : null,
      source: 'user',
      category: 'jev_label',
      created_at: Date.now(),
    };
    // One label per decision (jev-lab semantics): a new label replaces the pending one.
    const others = loadLabels().filter(
      (item) => !(item.request_id === label.request_id && item.policy === label.policy),
    );
    saveLabels([...others, label]);
    return sendJson(res, 200, label);
  }

  return async function handleQualityApi(req, res, url) {
    const parts = url.pathname.split('/').filter(Boolean); // ['api', 'quality', ...]
    if (req.method === 'GET' && parts[2] === 'runs' && !parts[3]) {
      return sendJson(res, 200, readJson(path.join(qualityRoot, 'runs.json'), { runs: [] }));
    }
    if (req.method === 'GET' && parts[2] === 'runs' && parts[3]) {
      const detail = loadDetail(parts[3]);
      if (!detail) return sendJson(res, 404, { error: 'quality run not found' });
      return sendJson(res, 200, detail);
    }
    if (req.method === 'GET' && parts[2] === 'experiments') {
      return sendJson(res, 200, readJson(path.join(qualityRoot, 'experiments.json'), { experiments: [] }));
    }
    if (parts[2] === 'labels' && !parts[3]) {
      if (req.method === 'GET') {
        const runId = url.searchParams.get('run_id');
        const labels = loadLabels();
        return sendJson(res, 200, runId ? labels.filter((item) => item.run_id === runId) : labels);
      }
      if (req.method === 'POST') return createLabel(req, res);
    }
    if (req.method === 'DELETE' && parts[2] === 'labels' && parts[3]) {
      const id = decodeURIComponent(parts[3]);
      saveLabels(loadLabels().filter((item) => item.id !== id));
      return sendJson(res, 200, { ok: true });
    }
    return sendJson(res, 404, { error: 'unknown quality endpoint' });
  };
}
