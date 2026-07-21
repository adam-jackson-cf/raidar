// Validates and copies the semantic review projection emitted by the Python
// orchestrator. This build step intentionally owns no scenario, scoring, or
// status interpretation.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const surfaceRoot = path.resolve(here, '..');
const repoRoot = path.resolve(surfaceRoot, '..');
const dataRoot = path.join(surfaceRoot, 'data');
const projectionPath = process.env.RAIDAR_REVIEW_PROJECTION
  ? path.resolve(process.env.RAIDAR_REVIEW_PROJECTION)
  : path.join(repoRoot, '.tmp', 'review-projection.json');

function invariant(condition, message) {
  if (!condition) throw new Error(`Invalid review projection: ${message}`);
}

function object(value, name) {
  invariant(value !== null && typeof value === 'object' && !Array.isArray(value), `${name} must be an object`);
  return value;
}

function array(value, name) {
  invariant(Array.isArray(value), `${name} must be an array`);
  return value;
}

function text(value, name) {
  invariant(typeof value === 'string' && value.length > 0, `${name} must be a non-empty string`);
}

function validateRun(run, name) {
  object(run, name);
  for (const key of ['id', 'name', 'scenario', 'revision', 'harness', 'model', 'agent_spec', 'experiment_id']) {
    text(run[key], `${name}.${key}`);
  }
  invariant(['OK', 'ERROR', 'UNSET'].includes(run.status), `${name}.status is unsupported`);
  invariant(Number.isInteger(run.started_at), `${name}.started_at must be an integer`);
  invariant(Number.isInteger(run.duration_ms), `${name}.duration_ms must be an integer`);
  array(run.metric_scores, `${name}.metric_scores`);
  object(run.finding_counts, `${name}.finding_counts`);
  object(run.issue_categories, `${name}.issue_categories`);
  object(run.artifact_paths, `${name}.artifact_paths`);
}

function validateDetail(detail, runId) {
  object(detail, `run_details.${runId}`);
  validateRun(detail.run, `run_details.${runId}.run`);
  invariant(detail.run.id === runId, `run_details.${runId}.run.id must match its key`);
  array(detail.spans, `run_details.${runId}.spans`);
  array(detail.annotations, `run_details.${runId}.annotations`);
  for (const [index, span] of detail.spans.entries()) {
    object(span, `run_details.${runId}.spans[${index}]`);
    text(span.id, `run_details.${runId}.spans[${index}].id`);
    invariant(span.run_id === runId, `span ${span.id} has a mismatched run_id`);
    invariant(['OK', 'ERROR', 'UNSET'].includes(span.status), `span ${span.id} has an unsupported status`);
  }
}

function validateExperiment(experiment, index) {
  object(experiment, `experiments[${index}]`);
  for (const key of ['experiment_id', 'dir', 'scenario', 'revision', 'harness', 'model', 'agent_spec']) {
    text(experiment[key], `experiments[${index}].${key}`);
  }
  object(experiment.scenario_meta, `experiments[${index}].scenario_meta`);
  object(experiment.comparable_config, `experiments[${index}].comparable_config`);
  text(experiment.comparable_config.fingerprint, `experiments[${index}].comparable_config.fingerprint`);
  array(experiment.change_categories, `experiments[${index}].change_categories`);
  array(experiment.run_ids, `experiments[${index}].run_ids`);
}

function validateRevisionDiff(diff, index) {
  object(diff, `revision_diffs[${index}]`);
  invariant(diff.schema_version === 1, `revision_diffs[${index}].schema_version must be 1`);
  for (const key of ['key', 'scenario', 'from_revision', 'to_revision']) {
    text(diff[key], `revision_diffs[${index}].${key}`);
  }
  array(diff.change_categories, `revision_diffs[${index}].change_categories`);
  array(diff.summary, `revision_diffs[${index}].summary`);
  array(diff.comparable_warnings, `revision_diffs[${index}].comparable_warnings`);
  object(diff.sections, `revision_diffs[${index}].sections`);
  object(diff.files, `revision_diffs[${index}].files`);
}

function loadProjection() {
  let projection;
  try {
    projection = JSON.parse(fs.readFileSync(projectionPath, 'utf8'));
  } catch (error) {
    throw new Error(`Unable to read Python review projection at ${projectionPath}: ${error.message}`);
  }
  object(projection, 'root');
  invariant(projection.schema_version === 1, 'schema_version must be 1');
  text(projection.generated_from, 'generated_from');
  array(projection.runs, 'runs').forEach((run, index) => validateRun(run, `runs[${index}]`));
  array(projection.experiments, 'experiments').forEach(validateExperiment);
  array(projection.revision_diffs, 'revision_diffs').forEach(validateRevisionDiff);
  const details = object(projection.run_details, 'run_details');
  for (const [runId, detail] of Object.entries(details)) validateDetail(detail, runId);
  const indexedIds = new Set(projection.runs.map((run) => run.id));
  invariant(indexedIds.size === projection.runs.length, 'runs contains duplicate ids');
  invariant(indexedIds.size === Object.keys(details).length, 'run index/detail counts differ');
  for (const runId of indexedIds) invariant(details[runId], `run ${runId} has no detail artifact`);
  return projection;
}

function main() {
  const projection = loadProjection();
  fs.rmSync(path.join(dataRoot, 'runs'), { recursive: true, force: true });
  fs.mkdirSync(path.join(dataRoot, 'runs'), { recursive: true });
  for (const [runId, detail] of Object.entries(projection.run_details)) {
    fs.writeFileSync(path.join(dataRoot, 'runs', `${runId}.json`), `${JSON.stringify(detail, null, 2)}\n`);
  }
  fs.writeFileSync(
    path.join(dataRoot, 'runs.json'),
    `${JSON.stringify({ generated_from: projection.generated_from, runs: projection.runs }, null, 2)}\n`,
  );
  fs.writeFileSync(
    path.join(dataRoot, 'experiments.json'),
    `${JSON.stringify({ experiments: projection.experiments, revision_diffs: projection.revision_diffs }, null, 2)}\n`,
  );
  console.log(
    `Copied ${projection.runs.length} validated run(s) across ${projection.experiments.length} experiment(s) into ${path.relative(repoRoot, dataRoot)}`,
  );
}

main();
