import fs from 'node:fs';
import path from 'node:path';

const repoRoot = path.resolve(process.cwd(), '..');
const benchRoot = path.join(repoRoot, 'experiments', 'benchmarks');
const outPath = path.join(process.cwd(), 'src', 'data.json');

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch {
    return null;
  }
}

function parseRun(dirName) {
  const parts = dirName.split('__');
  if (parts.length < 4) return null;
  return {
    run_id: parts[0],
    scenario: parts[1],
    revision: parts[2],
    harness: parts[3],
    model: parts.slice(4).join('__') || 'unknown',
  };
}

function statMean(block) {
  return typeof block?.mean === 'number' ? block.mean : null;
}

function statMedian(block) {
  return typeof block?.median === 'number' ? block.median : null;
}

function statStddev(block) {
  return typeof block?.stddev === 'number' ? block.stddev : null;
}

const rows = [];
if (fs.existsSync(benchRoot)) {
  for (const dir of fs.readdirSync(benchRoot)) {
    const meta = parseRun(dir);
    if (!meta) continue;
    const full = path.join(benchRoot, dir);
    if (!fs.statSync(full).isDirectory()) continue;
    const summary = readJson(path.join(full, 'experiment-summary.json'));
    const experiment = readJson(path.join(full, 'experiment.json'));
    const aggregate = summary?.aggregate ?? experiment?.aggregate ?? {};
    const config = summary?.config ?? experiment?.config ?? {};
    rows.push({
      ...meta,
      scenario: config.scenario_name ?? meta.scenario,
      revision: config.scenario_revision ?? meta.revision,
      harness: config.harness ?? meta.harness,
      model: config.model ?? meta.model,
      evaluation_profile: config.evaluation_profile ?? null,
      metrics: config.metrics ?? [],
      mean_score: statMean(aggregate.composite_score),
      median_score: statMedian(aggregate.composite_score),
      score_stddev: statStddev(aggregate.composite_score),
      quality_score: statMean(aggregate.quality_score),
      diagnostic_score: statMean(aggregate.diagnostic_score),
      duration_sec: statMean(aggregate.duration_sec),
      uncached_input_tokens: statMean(aggregate.uncached_input_tokens),
      valid_rate: aggregate.validity_rate_total ?? aggregate.validity_rate ?? null,
      performance_pass_rate: aggregate.performance_pass_rate ?? null,
      unscored_count: aggregate.unscored_count ?? null,
      run_count_scored: aggregate.run_count_scored ?? aggregate.run_count ?? null,
      run_count_total: aggregate.run_count_total ?? aggregate.run_count ?? null,
      sample_adequacy: summary?.sample?.sample_adequacy ?? null,
      sample_class: summary?.sample?.sample_class ?? config.sample_class ?? null,
      repeat_count: config.repeats ?? experiment?.repeats ?? null,
      started_at: summary?.started_at_utc ?? experiment?.started_at_utc ?? null,
      completed_at: summary?.completed_at_utc ?? experiment?.completed_at_utc ?? null,
      created_at: summary?.created_at_utc ?? experiment?.created_at_utc ?? null,
    });
  }
}

rows.sort((a, b) => String(a.run_id).localeCompare(String(b.run_id)));
const byScenario = {};
for (const row of rows) {
  const key = `${row.scenario}:${row.model}`;
  byScenario[key] ||= [];
  byScenario[key].push(row);
}

const deltas = [];
for (const [key, entries] of Object.entries(byScenario)) {
  const latestByRevision = new Map();
  for (const entry of entries) {
    if (typeof entry.mean_score !== 'number') continue;
    const current = latestByRevision.get(entry.revision);
    if (!current || String(entry.run_id).localeCompare(String(current.run_id)) > 0) {
      latestByRevision.set(entry.revision, entry);
    }
  }
  const scoredEntries = [...latestByRevision.values()]
    .filter((x) => typeof x.mean_score === 'number')
    .sort((a, b) => String(a.revision).localeCompare(String(b.revision)));
  for (let index = 1; index < scoredEntries.length; index += 1) {
    const previous = scoredEntries[index - 1];
    const current = scoredEntries[index];
    deltas.push({
      key,
      scenario: current.scenario,
      model: current.model,
      from_revision: previous.revision,
      to_revision: current.revision,
      previous_score: previous.mean_score,
      current_score: current.mean_score,
      delta: current.mean_score - previous.mean_score,
      duration_delta: current.duration_sec - previous.duration_sec,
      token_delta: current.uncached_input_tokens - previous.uncached_input_tokens,
      performance_delta: current.performance_pass_rate - previous.performance_pass_rate,
    });
  }
}

fs.writeFileSync(
  outPath,
  JSON.stringify(
    {
      generated_at: new Date().toISOString(),
      rows,
      deltas,
    },
    null,
    2,
  ),
);
console.log(`Wrote ${outPath} with ${rows.length} rows`);
