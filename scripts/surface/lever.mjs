#!/usr/bin/env node
// Verification-surface lever for the review surface UI and the orchestrator CLI.
//
//   node scripts/surface/lever.mjs --data-dir <dir> --evidence-dir <dir> [--journey <file>]...
//
// One command performs launch -> doctor -> drive -> evidence -> cleanup:
//   launch    builds the synthetic review and Quality fixtures, projects them into
//             <data-dir>/surface/data and starts review-surface/server.mjs on a free port;
//   doctor    proves the instance is usable: the API serves runs, experiments and Quality
//             runs, the built app loads, an annotation round-trips through the data directory,
//             and the `raidar` CLI answers;
//   drive     runs each --journey file through the Node test runner (`node --test`) against
//             this instance (journeys call surface() from this module);
//   evidence  writes lever.json, doctor.json, logs and per-journey results to <evidence-dir>;
//   cleanup   stops the server on every exit path.
//
// Every generated file stays under <data-dir> (RAIDAR_VAR and TMPDIR point there), which must
// be empty or absent; the user's own review-surface/data and var/ are never read or written.
// Exit codes: 0 doctor (and journeys) passed, 1 a journey failed, 2 usage error,
// 3 doctor failed (environment, never a product defect; the doctor marker is printed).
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const DOCTOR_MARKER = 'RAIDAR-SURFACE-DOCTOR-FAILED';
const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const SURFACE = path.join(REPO, 'review-surface');
const ORCHESTRATOR = path.join(REPO, 'orchestrator');
const STEP_TIMEOUT_MS = 180_000;
const READY_TIMEOUT_MS = 30_000;
const JOURNEY_TIMEOUT_MS = 300_000;
const ENV_PREFIX = 'RAIDAR_SURFACE_';

class DoctorFailure extends Error {}

// ---------------------------------------------------------------- journey-side API

/** The running instance, for a journey file executed by the lever's drive step. */
export function surface() {
  const baseUrl = process.env[`${ENV_PREFIX}BASE_URL`];
  const dataDir = process.env[`${ENV_PREFIX}DATA_DIR`];
  const evidenceDir = process.env[`${ENV_PREFIX}EVIDENCE_DIR`];
  const journey = process.env[`${ENV_PREFIX}JOURNEY`];
  if (!baseUrl || !dataDir || !evidenceDir || !journey) {
    throw new Error(
      'journeys run through the lever: node scripts/surface/lever.mjs '
        + '--data-dir <dir> --evidence-dir <dir> --journey <file>',
    );
  }
  return {
    baseUrl,
    dataDir,
    evidenceDir,
    journey,
    /** Run the orchestrator CLI isolated in this instance's data directory. */
    raidar: (args, options = {}) => raidar(dataDir, args, options),
    /** Open a headless Chromium page on this instance; screenshots land in the evidence dir. */
    withPage: (fn) => withPage(baseUrl, evidenceDir, journey, fn),
    /** Record the journey's observed outcome; the lever digests it into lever.json. */
    record: (outcome) =>
      fs.writeFileSync(
        path.join(evidenceDir, `${journey}.result.json`),
        `${JSON.stringify(outcome, null, 2)}\n`,
      ),
  };
}

function isolatedEnv(dataDir, extra = {}) {
  const env = { ...process.env, ...extra };
  delete env.AGENT;
  env.RAIDAR_VAR = path.join(dataDir, 'var');
  env.TMPDIR = path.join(dataDir, 'tmp');
  env.TMP = env.TMPDIR;
  env.TEMP = env.TMPDIR;
  return env;
}

function raidar(dataDir, args, { timeoutMs = STEP_TIMEOUT_MS } = {}) {
  const cwd = path.join(dataDir, 'cli');
  fs.mkdirSync(cwd, { recursive: true });
  return run('uv', ['run', '--project', ORCHESTRATOR, 'raidar', ...args], {
    cwd,
    env: isolatedEnv(dataDir),
    timeoutMs,
  });
}

async function withPage(baseUrl, evidenceDir, journey, fn) {
  const { chromium } = createRequire(path.join(SURFACE, 'package.json'))('@playwright/test');
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined;
  const browser = await chromium.launch({ headless: true, executablePath });
  const errors = [];
  try {
    const context = await browser.newContext({ baseURL: baseUrl });
    const page = await context.newPage();
    page.setDefaultTimeout(15_000);
    page.on('console', (message) => message.type() === 'error' && errors.push(message.text()));
    page.on('pageerror', (error) => errors.push(error.message));
    try {
      return await fn(page, { errors });
    } catch (error) {
      await page.screenshot({ path: path.join(evidenceDir, `${journey}.failure.png`), fullPage: true })
        .catch(() => {});
      throw error;
    } finally {
      await page.screenshot({ path: path.join(evidenceDir, `${journey}.final.png`) }).catch(() => {});
    }
  } finally {
    await browser.close();
  }
}

// ---------------------------------------------------------------- process helpers

function run(command, args, { cwd = REPO, env = process.env, timeoutMs = STEP_TIMEOUT_MS, log } = {}) {
  return new Promise((resolve) => {
    let stdout = '';
    let stderr = '';
    let child;
    try {
      child = spawn(command, args, { cwd, env, stdio: ['ignore', 'pipe', 'pipe'] });
    } catch (error) {
      resolve({ code: null, stdout, stderr: String(error) });
      return;
    }
    const timer = setTimeout(() => child.kill('SIGKILL'), timeoutMs);
    child.stdout.on('data', (chunk) => {
      stdout += chunk;
      log?.write(chunk);
    });
    child.stderr.on('data', (chunk) => {
      stderr += chunk;
      log?.write(chunk);
    });
    child.on('error', (error) => {
      stderr += String(error);
    });
    child.on('close', (code, signal) => {
      clearTimeout(timer);
      resolve({ code: signal ? null : code, stdout, stderr });
    });
  });
}

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.unref();
    probe.on('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

// ---------------------------------------------------------------- lever steps

function parseArgs(argv) {
  const options = { journeys: [] };
  for (let index = 0; index < argv.length; index += 1) {
    const [flag, inline] = argv[index].split(/=(.*)/s, 2);
    const value = () => (inline !== undefined ? inline : argv[++index]);
    if (flag === '--data-dir') options.dataDir = value();
    else if (flag === '--evidence-dir') options.evidenceDir = value();
    else if (flag === '--journey') options.journeys.push(value());
    else throw new Error(`unknown argument '${argv[index]}'`);
  }
  if (!options.dataDir || !options.evidenceDir) {
    throw new Error('--data-dir and --evidence-dir are required');
  }
  return options;
}

async function step(state, name, command, args, options = {}) {
  state.log.write(`\n== ${name}: ${command} ${args.join(' ')}\n`);
  const result = await run(command, args, { log: state.log, ...options });
  if (result.code !== 0) {
    throw new DoctorFailure(`${name} exited ${result.code}; see ${path.join(state.evidenceDir, 'launch.log')}`);
  }
  return result;
}

function prepareDataDir(dataDir) {
  if (fs.existsSync(dataDir)) {
    if (!fs.statSync(dataDir).isDirectory()) {
      throw new DoctorFailure(`data directory ${dataDir} is not a directory`);
    }
    if (fs.readdirSync(dataDir).length > 0) {
      throw new DoctorFailure(`data directory ${dataDir} is not empty; pass a fresh directory`);
    }
  }
  try {
    for (const sub of ['var', 'tmp', 'cli', 'surface/scripts']) {
      fs.mkdirSync(path.join(dataDir, sub), { recursive: true });
    }
  } catch (error) {
    throw new DoctorFailure(`data directory ${dataDir} is unusable: ${error.message}`);
  }
}

async function launch(state) {
  const { dataDir } = state;
  for (const required of ['dist/index.html', 'node_modules/@playwright/test/package.json']) {
    if (!fs.existsSync(path.join(SURFACE, required))) {
      throw new DoctorFailure(`review-surface/${required} is missing; run make review-surface-build`);
    }
  }
  prepareDataDir(dataDir);
  const env = isolatedEnv(dataDir);
  const python = (module, args) =>
    ['run', '--project', ORCHESTRATOR, '--directory', ORCHESTRATOR, 'python', '-m', module, ...args];
  const review = path.join(dataDir, 'review-fixture');
  const quality = path.join(dataDir, 'quality-fixture');
  const reviewProjection = path.join(dataDir, 'review-projection.json');
  const qualityProjection = path.join(dataDir, 'quality-projection.json');
  await step(state, 'review fixture', 'uv', python('tests.review_fixtures.projection', [review]), { env });
  await step(state, 'quality fixture', 'uv', python('tests.review_fixtures.quality', [quality]), { env });
  await step(state, 'quality projection', 'uv', python('raidar.evaluation.review.quality', [
    '--jobs-root', path.join(quality, 'jobs'), '--store', path.join(quality, 'store'),
    '--jobs-root', path.join(review, 'jobs'), '--store', path.join(review, 'store'),
    '--output', qualityProjection, '--synthetic',
  ]), { env });
  await step(state, 'review projection', 'uv', python('raidar.evaluation.review.projection', [
    '--jobs-root', path.join(review, 'jobs'), '--jobs-root', path.join(review, 'review-only'),
    '--store', path.join(review, 'store'), '--output', reviewProjection,
  ]), { env });
  // The data builders write next to their own location (<script dir>/../data); running copies
  // under <data-dir>/surface keeps the repo's review-surface/data untouched.
  const scripts = path.join(dataDir, 'surface', 'scripts');
  for (const name of ['build-review-data.mjs', 'build-quality-data.mjs']) {
    fs.copyFileSync(path.join(SURFACE, 'scripts', name), path.join(scripts, name));
  }
  await step(state, 'review data', 'node', [path.join(scripts, 'build-review-data.mjs')], {
    env: { ...env, RAIDAR_REVIEW_PROJECTION: reviewProjection },
  });
  await step(state, 'quality data', 'node', [path.join(scripts, 'build-quality-data.mjs')], {
    env: { ...env, RAIDAR_QUALITY_PROJECTION: qualityProjection },
  });
  state.port = await freePort();
  state.baseUrl = `http://localhost:${state.port}`;
  const serverLog = fs.createWriteStream(path.join(state.evidenceDir, 'server.log'));
  // The trailing --lever-data argument is ignored by server.mjs; it names the instance in `ps`.
  state.server = spawn('node', ['server.mjs', '--lever-data', dataDir], {
    cwd: SURFACE,
    env: {
      ...env,
      REVIEW_SURFACE_DATA_ROOT: path.join(dataDir, 'surface', 'data'),
      REVIEW_SURFACE_PORT: String(state.port),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  state.server.stdout.pipe(serverLog);
  state.server.stderr.pipe(serverLog);
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new DoctorFailure('server did not start within 30 s')), READY_TIMEOUT_MS);
    state.server.on('exit', (code) => {
      clearTimeout(timer);
      reject(new DoctorFailure(`server exited ${code} before listening; see server.log`));
    });
    state.server.stdout.on('data', (chunk) => {
      if (String(chunk).includes(`localhost:${state.port}`)) {
        clearTimeout(timer);
        resolve();
      }
    });
  });
}

async function doctor(state) {
  const checks = {};
  const get = async (route) => {
    const response = await fetch(`${state.baseUrl}${route}`);
    if (!response.ok) throw new DoctorFailure(`GET ${route} returned ${response.status}`);
    return route.startsWith('/api/') ? response.json() : response.text();
  };
  const runs = await get('/api/runs');
  if (!Array.isArray(runs) || runs.length === 0) throw new DoctorFailure('GET /api/runs served no runs');
  checks.runs = runs.length;
  const experiments = await get('/api/experiments');
  checks.experiments = (experiments.experiments ?? experiments).length;
  const qualityRuns = await get('/api/quality/runs');
  checks.quality_runs = (qualityRuns.runs ?? qualityRuns).length;
  if (!checks.experiments || !checks.quality_runs) throw new DoctorFailure('experiments or Quality runs are empty');
  const html = await get('/');
  if (!html.includes('id="root"')) throw new DoctorFailure('GET / did not serve the built app');
  // Usable means writable: an annotation must persist in this instance's data directory.
  const created = await fetch(`${state.baseUrl}/api/annotations`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ run_id: runs[0].id, kind: 'note', note: 'lever doctor' }),
  });
  if (!created.ok) throw new DoctorFailure(`annotation write returned ${created.status}`);
  const { id } = await created.json();
  const stored = path.join(state.dataDir, 'surface', 'data', 'user-annotations.json');
  if (!fs.existsSync(stored) || !fs.readFileSync(stored, 'utf8').includes(id)) {
    throw new DoctorFailure('annotation did not persist in the data directory');
  }
  const removed = await fetch(`${state.baseUrl}/api/annotations/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (!removed.ok) throw new DoctorFailure(`annotation delete returned ${removed.status}`);
  checks.annotation_round_trip = true;
  // Journeys drive a real browser: the app must render in headless Chromium.
  try {
    checks.app_rendered = await withPage(state.baseUrl, state.evidenceDir, 'doctor', async (page) => {
      await page.goto('/');
      await page.locator('nav[aria-label="Problem families"] a').first().waitFor();
      await page.locator('tbody tr').first().waitFor();
      return true;
    });
  } catch (error) {
    const first = String(error instanceof Error ? error.message : error).split('\n')[0];
    throw new DoctorFailure(`headless Chromium could not render the app (npx playwright install chromium?): ${first}`);
  }
  const version = await raidar(state.dataDir, ['--version']);
  if (version.code !== 0) throw new DoctorFailure(`raidar --version exited ${version.code}`);
  checks.raidar_cli = version.stdout.trim();
  return checks;
}

async function drive(state, journeyFile) {
  const file = path.resolve(REPO, journeyFile);
  const name = path.basename(file).replace(/\.journey\.mjs$/, '').replace(/\.[^.]+$/, '');
  const started = Date.now();
  const log = fs.createWriteStream(path.join(state.evidenceDir, `${name}.log`));
  const result = await run(process.execPath, [
    '--test', '--test-reporter=spec', '--test-reporter-destination=stdout',
    '--test-reporter=junit', `--test-reporter-destination=${path.join(state.evidenceDir, `${name}.junit.xml`)}`,
    file,
  ], {
    env: {
      ...isolatedEnv(state.dataDir),
      [`${ENV_PREFIX}BASE_URL`]: state.baseUrl,
      [`${ENV_PREFIX}DATA_DIR`]: state.dataDir,
      [`${ENV_PREFIX}EVIDENCE_DIR`]: state.evidenceDir,
      [`${ENV_PREFIX}JOURNEY`]: name,
    },
    timeoutMs: JOURNEY_TIMEOUT_MS,
    log,
  });
  log.end();
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  const outcomePath = path.join(state.evidenceDir, `${name}.result.json`);
  const outcome = fs.existsSync(outcomePath) ? fs.readFileSync(outcomePath) : null;
  return {
    journey: path.relative(REPO, file),
    exit: result.code,
    passed: result.code === 0,
    duration_ms: Date.now() - started,
    outcome_sha256: outcome ? createHash('sha256').update(outcome).digest('hex') : null,
  };
}

function cleanup(state) {
  const server = state.server;
  if (!server || server.exitCode !== null || server.signalCode !== null) return Promise.resolve();
  return new Promise((resolve) => {
    const force = setTimeout(() => server.kill('SIGKILL'), 3_000);
    server.once('exit', () => {
      clearTimeout(force);
      resolve();
    });
    server.kill('SIGTERM');
  });
}

async function main(argv) {
  let options;
  try {
    options = parseArgs(argv);
  } catch (error) {
    console.error(`lever: ${error.message}`);
    return 2;
  }
  const state = {
    dataDir: path.resolve(options.dataDir),
    evidenceDir: path.resolve(options.evidenceDir),
  };
  fs.mkdirSync(state.evidenceDir, { recursive: true });
  state.log = fs.createWriteStream(path.join(state.evidenceDir, 'launch.log'));
  const summary = { data_dir: state.dataDir, doctor: null, journeys: [] };
  const stop = () => cleanup(state).then(() => process.exit(130));
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
  let code = 0;
  try {
    await launch(state);
    summary.doctor = { ok: true, ...(await doctor(state)) };
    console.log(`lever: doctor passed on ${state.baseUrl}`);
    for (const journey of options.journeys) {
      const result = await drive(state, journey);
      summary.journeys.push(result);
      console.log(`lever: journey ${result.journey} ${result.passed ? 'passed' : `failed (exit ${result.exit})`}`);
      if (!result.passed) code = 1;
    }
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    summary.doctor = { ok: false, reason };
    console.error(`${DOCTOR_MARKER}: ${reason}`);
    code = 3;
  } finally {
    await cleanup(state);
    await new Promise((resolve) => state.log.end(resolve));
    fs.writeFileSync(path.join(state.evidenceDir, 'doctor.json'), `${JSON.stringify(summary.doctor, null, 2)}\n`);
    fs.writeFileSync(path.join(state.evidenceDir, 'lever.json'), `${JSON.stringify(summary, null, 2)}\n`);
  }
  return code;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  process.exitCode = await main(process.argv.slice(2));
}
