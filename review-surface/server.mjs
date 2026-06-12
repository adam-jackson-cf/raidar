// Review-surface server: serves the built SPA plus a Workshop-shaped read API
// over the projected review data, and persists user annotations locally.
// Zero runtime dependencies (node:http only).
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const dataRoot = path.join(here, 'data');
const distRoot = path.join(here, 'dist');
const benchRoot = path.resolve(here, '..', 'experiments', 'benchmarks');
const userAnnotationsPath = path.join(dataRoot, 'user-annotations.json');
const port = Number(process.env.REVIEW_SURFACE_PORT || 5950);

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
};

function readJson(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch {
    return fallback;
  }
}

function loadRunIndex() {
  return readJson(path.join(dataRoot, 'runs.json'), { runs: [] });
}

function loadRunDetail(runId) {
  if (!/^[A-Za-z0-9._-]+$/.test(runId)) return null;
  return readJson(path.join(dataRoot, 'runs', `${runId}.json`), null);
}

function loadUserAnnotations() {
  return readJson(userAnnotationsPath, []);
}

function saveUserAnnotations(annotations) {
  fs.mkdirSync(dataRoot, { recursive: true });
  fs.writeFileSync(userAnnotationsPath, JSON.stringify(annotations, null, 2));
}

function annotationsForRun(runId) {
  const detail = loadRunDetail(runId);
  const generated = detail?.annotations ?? [];
  const user = loadUserAnnotations().filter((item) => item.run_id === runId);
  return [...generated, ...user];
}

function sendJson(res, status, value) {
  const body = JSON.stringify(value);
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' });
  res.end(body);
}

function runOutline(detail) {
  const spans = detail.spans;
  const typeCounts = {};
  const toolsByName = new Map();
  const errors = [];
  for (const span of spans) {
    typeCounts[span.span_type] = (typeCounts[span.span_type] ?? 0) + 1;
    if (span.span_type === 'TOOL_CALL') {
      const entry = toolsByName.get(span.name) ?? { name: span.name, count: 0, errors: 0, example: null };
      entry.count += 1;
      if (span.status === 'ERROR') entry.errors += 1;
      if (!entry.example || span.status === 'ERROR') {
        entry.example = {
          span_id: span.id,
          status: span.status,
          input_preview: (span.input_payload ?? '').slice(0, 120),
          output_preview: (span.output_payload ?? '').slice(0, 120),
        };
      }
      toolsByName.set(span.name, entry);
    }
    if (span.status === 'ERROR') {
      errors.push({
        span_id: span.id,
        name: span.name,
        ts: span.start_time_ms,
        first_line_of_output: (span.output_payload ?? '').split('\n')[0]?.slice(0, 160) ?? '',
      });
    }
  }
  return {
    run: detail.run,
    summary: {
      span_type_counts: typeCounts,
      tool_calls: { total: toolsByName.size, by_name: [...toolsByName.values()] },
    },
    errors: errors.slice(0, 50),
    annotations: annotationsForRun(detail.run.id),
  };
}

function searchRun(detail, query) {
  const pattern = String(query.get('pattern') ?? '');
  if (!pattern) return { matches: [], truncated: false };
  const caseSensitive = query.get('case_sensitive') === 'true';
  const useRegex = query.get('regex') === 'true';
  const maxMatches = Math.min(Number(query.get('max_matches') ?? 50), 200);
  const contextChars = Math.min(Number(query.get('context_chars') ?? 80), 300);
  let matcher;
  try {
    matcher = useRegex
      ? new RegExp(pattern, caseSensitive ? 'g' : 'gi')
      : null;
  } catch {
    return { matches: [], truncated: false, error: 'invalid regex' };
  }
  const matches = [];
  const scopes = [
    ['span_input', (span) => span.input_payload],
    ['span_output', (span) => span.output_payload],
    ['span_attributes', (span) => span.attributes],
  ];
  outer: for (const span of detail.spans) {
    for (const [scope, accessor] of scopes) {
      const text = accessor(span);
      if (!text) continue;
      const haystack = caseSensitive ? text : text.toLowerCase();
      const needle = caseSensitive ? pattern : pattern.toLowerCase();
      let found = 0;
      if (matcher) {
        matcher.lastIndex = 0;
        let result;
        while ((result = matcher.exec(text)) && found < 10) {
          matches.push(searchMatch(span, scope, text, result.index, result[0].length, contextChars));
          found += 1;
          if (matches.length >= maxMatches) break outer;
          if (result[0].length === 0) matcher.lastIndex += 1;
        }
      } else {
        let offset = haystack.indexOf(needle);
        while (offset !== -1 && found < 10) {
          matches.push(searchMatch(span, scope, text, offset, pattern.length, contextChars));
          found += 1;
          if (matches.length >= maxMatches) break outer;
          offset = haystack.indexOf(needle, offset + Math.max(pattern.length, 1));
        }
      }
    }
  }
  return { matches, truncated: matches.length >= maxMatches };
}

function searchMatch(span, scope, text, start, length, contextChars) {
  const before = text.slice(Math.max(0, start - contextChars), start);
  const hit = text.slice(start, start + length);
  const after = text.slice(start + length, start + length + contextChars);
  return {
    span_id: span.id,
    span_name: span.name,
    scope,
    match_range: [start, start + length],
    snippet: `…${before}<<MATCH>>${hit}<<END>>${after}…`,
    payload_total_chars: text.length,
  };
}

function spanPayload(span, query) {
  const target = query.get('target') === 'output' ? 'output' : 'input';
  const text = (target === 'output' ? span.output_payload : span.input_payload) ?? '';
  const maxChars = Math.min(Number(query.get('max_chars') ?? 8000), 32000);
  const start = Math.max(Number(query.get('offset') ?? 0), 0);
  const end = Math.min(start + maxChars, text.length);
  return {
    span_id: span.id,
    target,
    format: 'text',
    value: text.slice(start, end),
    total_chars: text.length,
    returned_range: [start, end],
    truncated: end < text.length,
    next_offset: end < text.length ? end : undefined,
  };
}

function findSpan(spanId) {
  const splitAt = spanId.lastIndexOf(':s');
  if (splitAt === -1) return null;
  const detail = loadRunDetail(spanId.slice(0, splitAt));
  return detail?.spans.find((span) => span.id === spanId) ?? null;
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    return null;
  }
}

async function handleApi(req, res, url) {
  const parts = url.pathname.split('/').filter(Boolean); // ['api', ...]
  if (req.method === 'GET' && url.pathname === '/api/runs') {
    return sendJson(res, 200, loadRunIndex().runs);
  }
  if (req.method === 'GET' && url.pathname === '/api/experiments') {
    return sendJson(res, 200, readJson(path.join(dataRoot, 'experiments.json'), { experiments: [] }));
  }
  if (req.method === 'GET' && url.pathname === '/api/review') {
    return sendJson(res, 200, readJson(path.join(dataRoot, 'review.json'), { boards: [], reviews: {} }));
  }
  if (req.method === 'GET' && parts[1] === 'runs' && parts[2] === 'detail' && parts[3]) {
    const detail = loadRunDetail(parts[3]);
    if (!detail) return sendJson(res, 404, { error: 'run not found' });
    return sendJson(res, 200, { ...detail, annotations: annotationsForRun(parts[3]) });
  }
  if (req.method === 'GET' && parts[1] === 'runs' && parts[3] === 'outline') {
    const detail = loadRunDetail(parts[2]);
    if (!detail) return sendJson(res, 404, { error: 'run not found' });
    return sendJson(res, 200, runOutline(detail));
  }
  if (req.method === 'GET' && parts[1] === 'runs' && parts[3] === 'search') {
    const detail = loadRunDetail(parts[2]);
    if (!detail) return sendJson(res, 404, { error: 'run not found' });
    return sendJson(res, 200, searchRun(detail, url.searchParams));
  }
  if (req.method === 'GET' && parts[1] === 'spans' && parts[2] && parts[3] === 'payload') {
    const span = findSpan(decodeURIComponent(parts[2]));
    if (!span) return sendJson(res, 404, { error: 'span not found' });
    return sendJson(res, 200, spanPayload(span, url.searchParams));
  }
  if (url.pathname === '/api/annotations') {
    if (req.method === 'GET') {
      const runId = url.searchParams.get('run_id');
      if (!runId) return sendJson(res, 400, { error: 'run_id required' });
      return sendJson(res, 200, annotationsForRun(runId));
    }
    if (req.method === 'POST') {
      const body = await readBody(req);
      if (!body?.run_id || !['issue', 'good', 'note'].includes(body.kind)) {
        return sendJson(res, 400, { error: 'run_id and valid kind required' });
      }
      const annotation = {
        id: `user-${randomUUID()}`,
        run_id: String(body.run_id),
        span_id: body.span_id ? String(body.span_id) : null,
        kind: body.kind,
        note: body.note ? String(body.note).slice(0, 4000) : null,
        source: 'user',
        created_at: Date.now(),
        category: 'annotation',
        evidence: [],
      };
      saveUserAnnotations([...loadUserAnnotations(), annotation]);
      return sendJson(res, 200, annotation);
    }
  }
  if (req.method === 'DELETE' && parts[1] === 'annotations' && parts[2]) {
    const id = decodeURIComponent(parts[2]);
    if (!id.startsWith('user-')) {
      return sendJson(res, 400, { error: 'only user annotations can be deleted' });
    }
    saveUserAnnotations(loadUserAnnotations().filter((item) => item.id !== id));
    return sendJson(res, 200, { ok: true });
  }
  return sendJson(res, 404, { error: 'unknown endpoint' });
}

// Serves retained benchmark evidence assets (screenshots, diffs) referenced by
// the review model. Read-only, restricted to experiments/benchmarks/**.
function serveArtifact(res, url) {
  const relative = decodeURIComponent(url.pathname.slice('/artifacts/'.length));
  if (!relative.startsWith('experiments/benchmarks/')) {
    res.writeHead(403);
    return res.end('forbidden');
  }
  const resolved = path.normalize(path.join(benchRoot, '..', '..', relative));
  if (!resolved.startsWith(benchRoot) || !fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) {
    res.writeHead(404);
    return res.end('artifact not found');
  }
  const mime = MIME[path.extname(resolved)];
  if (!mime) {
    res.writeHead(415);
    return res.end('unsupported artifact type');
  }
  res.writeHead(200, { 'content-type': mime });
  res.end(fs.readFileSync(resolved));
}

function serveStatic(res, url) {
  const requested = url.pathname === '/' ? '/index.html' : url.pathname;
  const resolved = path.normalize(path.join(distRoot, requested));
  if (!resolved.startsWith(distRoot)) {
    res.writeHead(403);
    return res.end('forbidden');
  }
  const target = fs.existsSync(resolved) && fs.statSync(resolved).isFile()
    ? resolved
    : path.join(distRoot, 'index.html'); // SPA fallback
  if (!fs.existsSync(target)) {
    res.writeHead(503, { 'content-type': 'text/plain' });
    return res.end('review-surface app not built; run: cd review-surface && npm install && npm run build');
  }
  res.writeHead(200, { 'content-type': MIME[path.extname(target)] ?? 'application/octet-stream' });
  res.end(fs.readFileSync(target));
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', `http://localhost:${port}`);
  try {
    if (url.pathname.startsWith('/api/')) {
      await handleApi(req, res, url);
      return;
    }
    if (url.pathname.startsWith('/artifacts/')) {
      serveArtifact(res, url);
      return;
    }
    serveStatic(res, url);
  } catch (error) {
    sendJson(res, 500, { error: String(error) });
  }
});

server.listen(port, '127.0.0.1', () => {
  console.log(`Raidar review surface on http://localhost:${port}`);
});
