import { test, expect } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

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
