// Validates and copies the Quality projection emitted by the Python
// orchestrator (python -m raidar.review_quality). Like build-review-data.mjs,
// it owns no scoring or status interpretation.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const surfaceRoot = path.resolve(here, '..');
const repoRoot = path.resolve(surfaceRoot, '..');
const qualityRoot = path.join(surfaceRoot, 'data', 'quality');
const projectionPath = process.env.RAIDAR_QUALITY_PROJECTION
  ? path.resolve(process.env.RAIDAR_QUALITY_PROJECTION)
  : path.join(repoRoot, '.tmp', 'quality-projection.json');
const COMPONENTS = ['C', 'V', 'E', 'H', 'R', 'G', 'I', 'S'];

function invariant(condition, message) {
  if (!condition) throw new Error(`Invalid quality projection: ${message}`);
}

function validateCell(cell, name) {
  invariant(cell && typeof cell.status === 'string', `${name}.status is required`);
  if (cell.status === 'ok') invariant(typeof cell.value === 'number', `${name} is ok without a value`);
  else invariant(cell.value === null && typeof cell.reason === 'string', `${name} is ${cell.status} without a reason`);
}

function validateDetail(detail, runId) {
  invariant(detail.run?.id === runId, `run_details.${runId}.run.id must match its key`);
  invariant(Array.isArray(detail.rows), `run_details.${runId}.rows must be an array`);
  for (const row of detail.rows) {
    for (const key of COMPONENTS) validateCell(row.cells?.[key], `${runId}.${row.checkpoint}.${key}`);
  }
  for (const key of COMPONENTS) validateCell(detail.problem?.[key], `${runId}.problem.${key}`);
}

function loadProjection() {
  let projection;
  try {
    projection = JSON.parse(fs.readFileSync(projectionPath, 'utf8'));
  } catch (error) {
    throw new Error(`Unable to read Python quality projection at ${projectionPath}: ${error.message}`);
  }
  invariant(projection.kind === 'raidar-quality-projection', 'kind must be raidar-quality-projection');
  invariant(projection.schema_version === 1, 'schema_version must be 1');
  invariant(Array.isArray(projection.runs), 'runs must be an array');
  invariant(Array.isArray(projection.experiments), 'experiments must be an array');
  const ids = new Set(projection.runs.map((run) => run.id));
  invariant(ids.size === projection.runs.length, 'runs contains duplicate ids');
  invariant(ids.size === Object.keys(projection.run_details ?? {}).length, 'run index/detail counts differ');
  for (const [runId, detail] of Object.entries(projection.run_details)) validateDetail(detail, runId);
  return projection;
}

function main() {
  const projection = loadProjection();
  fs.rmSync(qualityRoot, { recursive: true, force: true });
  fs.mkdirSync(path.join(qualityRoot, 'runs'), { recursive: true });
  for (const [runId, detail] of Object.entries(projection.run_details)) {
    fs.writeFileSync(path.join(qualityRoot, 'runs', `${runId}.json`), `${JSON.stringify(detail, null, 2)}\n`);
  }
  const meta = { generated_from: projection.generated_from, synthetic: projection.synthetic };
  fs.writeFileSync(path.join(qualityRoot, 'runs.json'), `${JSON.stringify({ ...meta, runs: projection.runs }, null, 2)}\n`);
  fs.writeFileSync(
    path.join(qualityRoot, 'experiments.json'),
    `${JSON.stringify({ ...meta, experiments: projection.experiments }, null, 2)}\n`,
  );
  console.log(
    `Copied ${projection.runs.length} quality run(s) across ${projection.experiments.length} experiment(s) into ${path.relative(repoRoot, qualityRoot)}`,
  );
}

main();
