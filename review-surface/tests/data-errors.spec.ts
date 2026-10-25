import { test, expect } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';

// A missing or unparseable data file is an explicit error response, never empty data.
// A second server runs over a data root with a corrupt runs.json and no other files.
const PORT = 5961;
const BASE = `http://127.0.0.1:${PORT}`;
let server: ChildProcess;
let dataRoot: string;

test.beforeAll(async () => {
  dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'review-surface-empty-'));
  fs.writeFileSync(path.join(dataRoot, 'runs.json'), '{ not json');
  server = spawn('node', ['server.mjs'], {
    env: { ...process.env, REVIEW_SURFACE_PORT: String(PORT), REVIEW_SURFACE_DATA_ROOT: dataRoot },
    stdio: 'ignore',
  });
  await expect
    .poll(async () => (await fetch(`${BASE}/api/experiments`).catch(() => null))?.status, { timeout: 15_000 })
    .toBeDefined();
});

test.afterAll(() => {
  server.kill();
  fs.rmSync(dataRoot, { recursive: true, force: true });
});

test('an unparseable data file answers 500 with a message', async () => {
  const res = await fetch(`${BASE}/api/runs`);
  expect(res.status).toBe(500);
  expect((await res.json()).error).toContain('not valid JSON');
});

test('a missing data file answers 404 with a message, not empty data', async () => {
  for (const url of ['/api/experiments', '/api/quality/runs', '/api/quality/experiments']) {
    const res = await fetch(`${BASE}${url}`);
    expect(res.status, url).toBe(404);
    expect((await res.json()).error, url).toContain('data file not found');
  }
});

test('reviewer mutations accept same-origin JSON and non-browser API clients', async () => {
  for (const headers of [{ Origin: BASE }, {}]) {
    const created = await fetch(`${BASE}/api/annotations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8', ...headers },
      body: JSON.stringify({ run_id: 'api-security-fixture', kind: 'note', note: 'reviewed' }),
    });
    expect(created.status).toBe(200);
    const annotation = await created.json();
    const removed = await fetch(`${BASE}/api/annotations/${annotation.id}`, { method: 'DELETE', headers });
    expect(removed.status).toBe(200);
  }
  expect(JSON.parse(fs.readFileSync(path.join(dataRoot, 'user-annotations.json'), 'utf8'))).toEqual([]);
});

test('untrusted browser requests cannot change annotations or labels', async () => {
  const annotationPath = path.join(dataRoot, 'user-annotations.json');
  const labelPath = path.join(dataRoot, 'jev-labels.json');
  const beforeAnnotations = fs.existsSync(annotationPath) ? fs.readFileSync(annotationPath, 'utf8') : null;
  const forbiddenHeaders = [
    { Origin: 'https://untrusted.example', 'Content-Type': 'text/plain' },
    { Origin: 'https://untrusted.example', 'Content-Type': 'application/json' },
    { Origin: 'null', 'Content-Type': 'application/json' },
    { 'Sec-Fetch-Site': 'cross-site', 'Content-Type': 'application/json' },
    { Host: `untrusted.example:${PORT}`, 'Content-Type': 'application/json' },
  ];
  for (const endpoint of ['/api/annotations', '/api/quality/labels']) {
    for (const headers of forbiddenHeaders) {
      const response = await fetch(`${BASE}${endpoint}`, {
        method: 'POST', headers,
        body: JSON.stringify({ run_id: 'api-security-fixture', kind: 'note' }),
      });
      expect(response.status, JSON.stringify({ endpoint, headers })).toBe(403);
    }
    const plainText = await fetch(`${BASE}${endpoint}`, {
      method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: '{}',
    });
    expect(plainText.status).toBe(415);
    const deletion = await fetch(`${BASE}${endpoint}/user-fixture`, {
      method: 'DELETE', headers: { Origin: 'https://untrusted.example' },
    });
    expect(deletion.status).toBe(403);
  }
  const reboundRead = await fetch(`${BASE}/api/experiments`, { headers: { Host: `untrusted.example:${PORT}` } });
  expect(reboundRead.status).toBe(403);
  expect(fs.existsSync(annotationPath) ? fs.readFileSync(annotationPath, 'utf8') : null).toBe(beforeAnnotations);
  expect(fs.existsSync(labelPath)).toBe(false);
});

test('malformed request targets answer 400 and leave the server available', async () => {
  for (const target of ['//[/', 'http://[invalid/']) {
    const status = await new Promise<number | undefined>((resolve, reject) => {
      const request = http.request({ hostname: '127.0.0.1', port: PORT, path: target }, (response) => {
        response.resume();
        response.on('end', () => resolve(response.statusCode));
      });
      request.on('error', reject);
      request.end();
    });
    expect(status).toBe(400);
    expect((await fetch(`${BASE}/api/experiments`)).status).toBe(404);
  }
});
