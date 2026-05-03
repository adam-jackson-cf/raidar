import fs from 'node:fs';
import path from 'node:path';

const repoRoot = path.resolve(process.cwd(), '..');
const benchRoot = path.join(repoRoot, 'experiments', 'benchmarks');
const scenariosRoot = path.join(repoRoot, 'scenarios');
const outPath = path.join(process.cwd(), 'src', 'data.json');

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch {
    return null;
  }
}

function readText(filePath) {
  try {
    return fs.readFileSync(filePath, 'utf8');
  } catch {
    return '';
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

function clamp(value, min = 0, max = 1) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 0;
  return Math.max(min, Math.min(max, value));
}

function efficiencyScore(durationSec, tokens) {
  const durationPart = 1 - clamp((durationSec ?? 600) / 600);
  const tokenPart = 1 - clamp((tokens ?? 80000) / 80000);
  return durationPart * 0.6 + tokenPart * 0.4;
}

function decisionScore(row) {
  return Number((
    clamp(row.mean_score) * 0.42 +
    clamp(row.valid_rate) * 0.22 +
    clamp(row.performance_pass_rate) * 0.18 +
    clamp(row.sample_adequacy) * 0.10 +
    efficiencyScore(row.duration_sec, row.uncached_input_tokens) * 0.08
  ).toFixed(6));
}

function firstScalar(text, key) {
  const match = text.match(new RegExp(`^${key}:\\s*(.+)$`, 'm'));
  return match ? match[1].replace(/^['"]|['"]$/g, '').trim() : null;
}

function countBetween(text, startKey, endKeys, pattern) {
  const lines = text.split('\n');
  const start = lines.findIndex((line) => line.trim() === `${startKey}:`);
  if (start === -1) return 0;
  let count = 0;
  for (let index = start + 1; index < lines.length; index += 1) {
    const trimmed = lines[index].trim();
    if (endKeys.includes(trimmed.replace(/:$/, ''))) break;
    if (pattern.test(lines[index])) count += 1;
  }
  return count;
}

function gateNames(text) {
  const lines = text.split('\n');
  const start = lines.findIndex((line) => line.trim() === 'gates:');
  if (start === -1) return [];
  const names = [];
  for (let index = start + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (/^\S/.test(line) && !line.startsWith('-')) break;
    const match = line.match(/^\s*-?\s*name:\s*(.+)$/);
    if (match) names.push(match[1].trim());
  }
  return names;
}

function metricIds(text) {
  const matches = [...text.matchAll(/^\s*id:\s*(.+)$/gm)].map((match) => match[1].trim());
  return [...new Set(matches)];
}

function readScenarioRevision(scenario, revision) {
  const root = path.join(scenariosRoot, scenario, revision);
  const scenarioYamlPath = path.join(root, 'scenario.yaml');
  const promptPath = path.join(root, 'prompt', 'task.md');
  const yaml = readText(scenarioYamlPath);
  const prompt = readText(promptPath);
  if (!yaml) return null;
  return {
    scenario,
    revision,
    description: firstScalar(yaml, 'description'),
    difficulty: firstScalar(yaml, 'difficulty'),
    category: firstScalar(yaml, 'category'),
    timeout_sec: Number(firstScalar(yaml, 'timeout_sec')) || null,
    evaluation_profile: metricIds(yaml).join('+'),
    metrics: metricIds(yaml),
    quality_gates: gateNames(yaml),
    deterministic_checks: countBetween(yaml, 'deterministic_checks', ['requirements', 'llm_judge_rubric', 'metrics', 'visual', 'prompt'], /^\s*-\s*type:/),
    requirements: countBetween(yaml, 'requirements', ['llm_judge_rubric', 'metrics', 'visual', 'prompt'], /^\s*-\s*id:/),
    llm_judge_criteria: [...yaml.matchAll(/^\s*-\s*criterion:/gm)].length,
    visual_reference: /reference_image:\s*/.test(yaml),
    prompt_preview: prompt.split('\n').filter(Boolean).slice(0, 5).join(' '),
    files: {
      scenario_yaml: path.relative(repoRoot, scenarioYamlPath),
      prompt: path.relative(repoRoot, promptPath),
    },
  };
}

function lineDiff(beforeText, afterText, maxLines = 180) {
  const before = beforeText.split('\n');
  const after = afterText.split('\n');
  const dp = Array.from({ length: before.length + 1 }, () => Array(after.length + 1).fill(0));
  for (let i = before.length - 1; i >= 0; i -= 1) {
    for (let j = after.length - 1; j >= 0; j -= 1) {
      dp[i][j] = before[i] === after[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const lines = [];
  let i = 0;
  let j = 0;
  let added = 0;
  let removed = 0;
  while (i < before.length && j < after.length && lines.length < maxLines) {
    if (before[i] === after[j]) {
      if (before[i].trim()) lines.push({ type: 'context', text: before[i] });
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      lines.push({ type: 'removed', text: before[i] });
      removed += 1;
      i += 1;
    } else {
      lines.push({ type: 'added', text: after[j] });
      added += 1;
      j += 1;
    }
  }
  while (i < before.length && lines.length < maxLines) {
    lines.push({ type: 'removed', text: before[i] });
    removed += 1;
    i += 1;
  }
  while (j < after.length && lines.length < maxLines) {
    lines.push({ type: 'added', text: after[j] });
    added += 1;
    j += 1;
  }
  return { added, removed, truncated: i < before.length || j < after.length, lines };
}

function classifyRevisionChange(beforeMeta, afterMeta, files) {
  const flags = [];
  if (beforeMeta?.evaluation_profile !== afterMeta?.evaluation_profile) flags.push('evaluation profile changed');
  if ((beforeMeta?.quality_gates || []).join(',') !== (afterMeta?.quality_gates || []).join(',')) flags.push('quality gates changed');
  if (beforeMeta?.deterministic_checks !== afterMeta?.deterministic_checks) flags.push('deterministic checks changed');
  if (beforeMeta?.visual_reference !== afterMeta?.visual_reference) flags.push('visual baseline changed');
  if (files.prompt.diff.added || files.prompt.diff.removed) flags.push('prompt changed');
  if (files.scenario.diff.added || files.scenario.diff.removed) flags.push('scenario contract changed');
  return flags.length ? flags : ['metadata unchanged'];
}

const rows = [];
if (fs.existsSync(benchRoot)) {
  for (const dir of fs.readdirSync(benchRoot)) {
    const meta = parseRun(dir);
    if (!meta) continue;
    const full = path.join(benchRoot, dir);
    if (!fs.statSync(full).isDirectory()) continue;
    const summaryPath = path.join(full, 'experiment-summary.json');
    const experimentPath = path.join(full, 'experiment.json');
    const summary = readJson(summaryPath);
    const experiment = readJson(experimentPath);
    const aggregate = summary?.aggregate ?? experiment?.aggregate ?? {};
    const config = summary?.config ?? experiment?.config ?? {};
    const row = {
      ...meta,
      scenario: config.scenario_name ?? meta.scenario,
      revision: config.scenario_revision ?? meta.revision,
      harness: config.harness ?? meta.harness,
      model: config.model ?? meta.model,
      evaluation_profile: config.evaluation_profile ?? null,
      metrics: config.metrics ?? [],
      metric_outcomes: aggregate.metric_outcomes ?? {},
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
      artifact_paths: {
        root: path.relative(repoRoot, full),
        summary: fs.existsSync(summaryPath) ? path.relative(repoRoot, summaryPath) : null,
        experiment: fs.existsSync(experimentPath) ? path.relative(repoRoot, experimentPath) : null,
        report: fs.existsSync(path.join(full, 'report.md')) ? path.relative(repoRoot, path.join(full, 'report.md')) : null,
      },
    };
    row.agent_spec = `${row.harness} · ${row.model}`;
    row.decision_score = decisionScore(row);
    rows.push(row);
  }
}

rows.sort((a, b) => String(a.run_id).localeCompare(String(b.run_id)));

const scenarioMap = new Map();
for (const row of rows) {
  const key = `${row.scenario}:${row.revision}`;
  if (!scenarioMap.has(key)) {
    const meta = readScenarioRevision(row.scenario, row.revision) ?? {
      scenario: row.scenario,
      revision: row.revision,
      description: null,
      metrics: row.metrics,
      quality_gates: [],
      deterministic_checks: null,
      requirements: null,
      llm_judge_criteria: null,
      visual_reference: false,
      files: {},
    };
    scenarioMap.set(key, meta);
  }
}
const scenarios = [...scenarioMap.values()].sort((a, b) => `${a.scenario}:${a.revision}`.localeCompare(`${b.scenario}:${b.revision}`));

const scenarioDiffs = [];
const scenarioFamilies = new Map();
for (const meta of scenarios) {
  if (!scenarioFamilies.has(meta.scenario)) scenarioFamilies.set(meta.scenario, []);
  scenarioFamilies.get(meta.scenario).push(meta);
}
for (const [scenario, revisions] of scenarioFamilies) {
  revisions.sort((a, b) => String(a.revision).localeCompare(String(b.revision)));
  for (let index = 1; index < revisions.length; index += 1) {
    const before = revisions[index - 1];
    const after = revisions[index];
    const beforeYaml = readText(path.join(repoRoot, before.files.scenario_yaml || ''));
    const afterYaml = readText(path.join(repoRoot, after.files.scenario_yaml || ''));
    const beforePrompt = readText(path.join(repoRoot, before.files.prompt || ''));
    const afterPrompt = readText(path.join(repoRoot, after.files.prompt || ''));
    const files = {
      scenario: { path: after.files.scenario_yaml, diff: lineDiff(beforeYaml, afterYaml) },
      prompt: { path: after.files.prompt, diff: lineDiff(beforePrompt, afterPrompt) },
    };
    const summary = classifyRevisionChange(before, after, files);
    scenarioDiffs.push({
      key: `${scenario}:${before.revision}:${after.revision}`,
      scenario,
      from_revision: before.revision,
      to_revision: after.revision,
      summary,
      comparable_warnings: summary.filter((flag) => flag !== 'prompt changed' && flag !== 'scenario contract changed' && flag !== 'metadata unchanged'),
      files,
    });
  }
}

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
      agent_spec: current.agent_spec,
      from_revision: previous.revision,
      to_revision: current.revision,
      previous_score: previous.mean_score,
      current_score: current.mean_score,
      delta: current.mean_score - previous.mean_score,
      first_to_last: false,
      duration_delta: current.duration_sec - previous.duration_sec,
      token_delta: current.uncached_input_tokens - previous.uncached_input_tokens,
      performance_delta: current.performance_pass_rate - previous.performance_pass_rate,
    });
  }
  if (scoredEntries.length > 2) {
    const previous = scoredEntries[0];
    const current = scoredEntries.at(-1);
    deltas.push({
      key,
      scenario: current.scenario,
      model: current.model,
      agent_spec: current.agent_spec,
      from_revision: previous.revision,
      to_revision: current.revision,
      previous_score: previous.mean_score,
      current_score: current.mean_score,
      delta: current.mean_score - previous.mean_score,
      first_to_last: true,
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
      scenarios,
      scenario_diffs: scenarioDiffs,
      deltas,
    },
    null,
    2,
  ),
);
console.log(`Wrote ${outPath} with ${rows.length} rows`);
