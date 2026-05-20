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

function sanitizeDashboardValue(value) {
  if (Array.isArray(value)) return value.map(sanitizeDashboardValue);
  if (!value || typeof value !== 'object') {
    if (
      typeof value === 'string' &&
      value.length >= 48 &&
      /^[A-Za-z0-9+/=_-]+$/.test(value)
    ) {
      return '[redacted-high-entropy-value]';
    }
    return value;
  }
  return Object.fromEntries(
    Object.entries(value).map(([key, entry]) => [key, sanitizeDashboardValue(entry)]),
  );
}

function pathExists(filePath) {
  return fs.existsSync(filePath);
}

function listDirs(dirPath) {
  try {
    return fs
      .readdirSync(dirPath)
      .filter((name) => fs.statSync(path.join(dirPath, name)).isDirectory());
  } catch {
    return [];
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

function valueOrDefault(value, fallback) {
  return value === null || value === undefined ? fallback : value;
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
  const prefix = `${key}:`;
  const line = text.split('\n').find((candidate) => candidate.startsWith(prefix));
  return line ? line.slice(prefix.length).replace(/^['"]|['"]$/g, '').trim() : null;
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

function scorerRefs(text) {
  const lines = text.split('\n');
  const start = lines.findIndex((line) => line.trim() === 'scorers:');
  if (start === -1) return [];
  const refs = [];
  let current = null;
  for (let index = start + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (/^\S/.test(line) && !line.startsWith('-')) break;
    const idMatch = line.match(/^\s*-\s*id:\s*(.+)$/);
    if (idMatch) {
      current = { id: idMatch[1].trim(), version: null, weight: null };
      refs.push(current);
      continue;
    }
    if (!current) continue;
    const versionMatch = line.match(/^\s*version:\s*(.+)$/);
    if (versionMatch) current.version = Number(versionMatch[1].trim()) || null;
    const weightMatch = line.match(/^\s*weight:\s*(.+)$/);
    if (weightMatch) current.weight = Number(weightMatch[1].trim()) || null;
  }
  return refs;
}

function scorerIds(text) {
  return [...new Set(scorerRefs(text).map((ref) => ref.id))];
}

function scorerEvaluationProfile(text) {
  return `scorers:${scorerRefs(text)
    .map((ref) => `${ref.id}@${ref.version ?? 1}:${ref.weight ?? 1}`)
    .join('+')}`;
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
    evaluation_profile: scorerEvaluationProfile(yaml),
    scorers: scorerIds(yaml),
    metrics: [],
    quality_gates: gateNames(yaml),
    deterministic_checks: countBetween(yaml, 'deterministic_checks', ['requirements', 'scorers', 'visual', 'prompt'], /^\s*-\s*type:/),
    requirements: countBetween(yaml, 'requirements', ['scorers', 'visual', 'prompt'], /^\s*-\s*id:/),
    llm_as_judge_metrics: [...yaml.matchAll(/judge:\s*/gm)].length,
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
  const dp = lineDiffTable(before, after);
  const state = { i: 0, j: 0, added: 0, removed: 0, lines: [] };
  appendChangedLines(state, before, after, dp, maxLines);
  appendRemainingLines(state, before, 'removed', maxLines);
  appendRemainingLines(state, after, 'added', maxLines);
  return {
    added: state.added,
    removed: state.removed,
    truncated: state.i < before.length || state.j < after.length,
    lines: state.lines,
  };
}

function lineDiffTable(before, after) {
  const dp = Array.from({ length: before.length + 1 }, () =>
    Array(after.length + 1).fill(0),
  );
  for (let i = before.length - 1; i >= 0; i -= 1) {
    for (let j = after.length - 1; j >= 0; j -= 1) {
      dp[i][j] = before[i] === after[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  return dp;
}

function appendChangedLines(state, before, after, dp, maxLines) {
  while (state.i < before.length && state.j < after.length && state.lines.length < maxLines) {
    if (before[state.i] === after[state.j]) {
      appendContextLine(state, before[state.i]);
    } else if (dp[state.i + 1][state.j] >= dp[state.i][state.j + 1]) {
      appendTypedLine(state, 'removed', before[state.i]);
    } else {
      appendTypedLine(state, 'added', after[state.j]);
    }
  }
}

function appendContextLine(state, text) {
  if (text.trim()) state.lines.push({ type: 'context', text });
  state.i += 1;
  state.j += 1;
}

function appendTypedLine(state, type, text) {
  state.lines.push({ type, text });
  state[type] += 1;
  if (type === 'removed') {
    state.i += 1;
  } else {
    state.j += 1;
  }
}

function appendRemainingLines(state, source, type, maxLines) {
  const cursor = type === 'removed' ? 'i' : 'j';
  while (state[cursor] < source.length && state.lines.length < maxLines) {
    appendTypedLine(state, type, source[state[cursor]]);
  }
}

function classifyRevisionChange(beforeMeta, afterMeta, files) {
  const changes = [
    [beforeMeta?.evaluation_profile !== afterMeta?.evaluation_profile, 'evaluation profile changed'],
    [qualityGateKey(beforeMeta) !== qualityGateKey(afterMeta), 'quality gates changed'],
    [beforeMeta?.deterministic_checks !== afterMeta?.deterministic_checks, 'deterministic checks changed'],
    [beforeMeta?.visual_reference !== afterMeta?.visual_reference, 'visual baseline changed'],
    [diffChanged(files.prompt.diff), 'prompt changed'],
    [diffChanged(files.scenario.diff), 'scenario contract changed'],
  ];
  const flags = changes.filter(([changed]) => changed).map(([, label]) => label);
  return flags.length ? flags : ['metadata unchanged'];
}

function qualityGateKey(meta) {
  return (meta?.quality_gates || []).join(',');
}

function diffChanged(diff) {
  return Boolean(diff.added || diff.removed);
}

function readRunDiagnostics(experimentDir) {
  const runsDir = path.join(experimentDir, 'runs');
  const runIds = listDirs(runsDir);
  return runIds.map((runId) => readRunDiagnostic(runsDir, runId));
}

function readRunDiagnostic(runsDir, runId) {
  const runDir = path.join(runsDir, runId);
  const verifier = path.join(runDir, 'verifier');
  const scorecard = readJson(path.join(verifier, 'scorecard.json'));
  const gateHistory = readJson(path.join(verifier, 'gate-history.json'));
  const performance = readJson(path.join(verifier, 'performance-gates.json'));
  const validity = readJson(path.join(verifier, 'execution-validity.json'));
  const workspaceDiff = readJson(path.join(runDir, 'workspace-diff.json'));
  const gateItems = diagnosticGateItems(gateHistory, performance);
  return {
    run_id: runId,
    failing_gates: failingGateNames(gateItems),
    acceptance_fail_ids: arrayOrEmpty(scorecard?.acceptance?.failed_ids),
    requirement_missing_ids: arrayOrEmpty(scorecard?.requirements?.missing_ids),
    validity_ok: validity?.valid ?? null,
    workspace_diff_summary: workspaceDiff?.summary ?? null,
    paths: diagnosticPaths(runDir, verifier),
  };
}

function diagnosticGateItems(gateHistory, performance) {
  if (Array.isArray(gateHistory?.gates)) return gateHistory.gates;
  if (Array.isArray(performance?.gates)) return performance.gates;
  return [];
}

function failingGateNames(gateItems) {
  return gateItems
    .filter((gate) => gate?.passed === false || hasFailureStatus(gate))
    .map((gate) => gate.name || gate.id || 'unknown-gate');
}

function hasFailureStatus(gate) {
  return String(gate?.status || '').toLowerCase().includes('fail');
}

function arrayOrEmpty(value) {
  return Array.isArray(value) ? value : [];
}

function relativeIfExists(filePath) {
  return pathExists(filePath) ? path.relative(repoRoot, filePath) : null;
}

function diagnosticPaths(runDir, verifier) {
  return {
    scorecard: relativeIfExists(path.join(verifier, 'scorecard.json')),
    gate_history: relativeIfExists(path.join(verifier, 'gate-history.json')),
    performance_gates: relativeIfExists(path.join(verifier, 'performance-gates.json')),
    execution_validity: relativeIfExists(path.join(verifier, 'execution-validity.json')),
    workspace_diff: relativeIfExists(path.join(runDir, 'workspace-diff.json')),
    report: relativeIfExists(path.join(runDir, 'report.md')),
  };
}

function collectBenchmarkRows() {
  if (!pathExists(benchRoot)) return [];
  return fs
    .readdirSync(benchRoot)
    .map(readBenchmarkRow)
    .filter(Boolean)
    .sort((a, b) => String(a.run_id).localeCompare(String(b.run_id)));
}

function readBenchmarkRow(dir) {
  const meta = parseRun(dir);
  if (!meta) return null;
  const full = path.join(benchRoot, dir);
  if (!fs.statSync(full).isDirectory()) return null;
  return buildBenchmarkRow(meta, full);
}

function buildBenchmarkRow(meta, experimentDir) {
  const payload = readExperimentPayload(experimentDir);
  const aggregate = payload.summary?.aggregate ?? payload.experiment?.aggregate ?? {};
  const config = payload.summary?.config ?? payload.experiment?.config ?? {};
  const row = {
    ...meta,
    ...scenarioIdentity(meta, config),
    ...scoreMetrics(aggregate),
    ...runHealthMetrics(aggregate),
    ...sampleMetadata(payload.summary, payload.experiment, config),
    artifact_paths: experimentArtifactPaths(experimentDir, payload),
    run_diagnostics: readRunDiagnostics(experimentDir),
  };
  row.agent_spec = `${row.harness} · ${row.model}`;
  row.latest_group_key = `${row.scenario}:${row.revision}:${row.agent_spec}`;
  row.decision_score = decisionScore(row);
  return row;
}

function readExperimentPayload(experimentDir) {
  const summaryPath = path.join(experimentDir, 'experiment-summary.json');
  const experimentPath = path.join(experimentDir, 'experiment.json');
  return {
    summaryPath,
    experimentPath,
    summary: readJson(summaryPath),
    experiment: readJson(experimentPath),
  };
}

function scenarioIdentity(meta, config) {
  return {
    scenario: valueOrDefault(config.scenario_name, meta.scenario),
    revision: valueOrDefault(config.scenario_revision, meta.revision),
    harness: valueOrDefault(config.harness, meta.harness),
    model: valueOrDefault(config.model, meta.model),
    evaluation_profile: valueOrDefault(config.evaluation_profile, null),
    metrics: valueOrDefault(config.metrics, []),
    scorers: valueOrDefault(config.scorers, []),
  };
}

function scoreMetrics(aggregate) {
  return {
    metric_outcomes: aggregate.metric_outcomes ?? {},
    scorer_outcomes: aggregate.scorer_outcomes ?? {},
    mean_score: statMean(aggregate.composite_score),
    median_score: statMedian(aggregate.composite_score),
    score_stddev: statStddev(aggregate.composite_score),
    quality_score: statMean(aggregate.quality_score),
    diagnostic_score: statMean(aggregate.diagnostic_score),
    duration_sec: statMean(aggregate.duration_sec),
    uncached_input_tokens: statMean(aggregate.uncached_input_tokens),
  };
}

function runHealthMetrics(aggregate) {
  return {
    valid_rate: valueOrDefault(
      aggregate.validity_rate_total,
      valueOrDefault(aggregate.validity_rate, null),
    ),
    performance_pass_rate: valueOrDefault(aggregate.performance_pass_rate, null),
    unscored_count: valueOrDefault(aggregate.unscored_count, null),
    run_count_scored: valueOrDefault(
      aggregate.run_count_scored,
      valueOrDefault(aggregate.run_count, null),
    ),
    run_count_total: valueOrDefault(
      aggregate.run_count_total,
      valueOrDefault(aggregate.run_count, null),
    ),
  };
}

function sampleMetadata(summary, experiment, config) {
  return {
    sample_adequacy: valueOrDefault(summary?.sample?.sample_adequacy, null),
    sample_class: valueOrDefault(summary?.sample?.sample_class, valueOrDefault(config.sample_class, null)),
    repeat_count: valueOrDefault(config.repeats, valueOrDefault(experiment?.repeats, null)),
    started_at: valueOrDefault(summary?.started_at_utc, valueOrDefault(experiment?.started_at_utc, null)),
    completed_at: valueOrDefault(
      summary?.completed_at_utc,
      valueOrDefault(experiment?.completed_at_utc, null),
    ),
    created_at: valueOrDefault(summary?.created_at_utc, valueOrDefault(experiment?.created_at_utc, null)),
  };
}

function experimentArtifactPaths(experimentDir, payload) {
  return {
    root: path.relative(repoRoot, experimentDir),
    summary: relativeIfExists(payload.summaryPath),
    experiment: relativeIfExists(payload.experimentPath),
    report: relativeIfExists(path.join(experimentDir, 'report.md')),
  };
}

function buildScenarioCatalog(rows) {
  const scenarioMap = new Map();
  for (const row of rows) {
    const key = `${row.scenario}:${row.revision}`;
    if (!scenarioMap.has(key)) scenarioMap.set(key, scenarioMetadata(row));
  }
  return [...scenarioMap.values()].sort(scenarioSort);
}

function scenarioMetadata(row) {
  return readScenarioRevision(row.scenario, row.revision) ?? {
    scenario: row.scenario,
    revision: row.revision,
    description: null,
    scorers: row.scorers,
    metrics: row.metrics,
    quality_gates: [],
    deterministic_checks: null,
    requirements: null,
    llm_as_judge_metrics: null,
    visual_reference: false,
    files: {},
  };
}

function scenarioSort(a, b) {
  return `${a.scenario}:${a.revision}`.localeCompare(`${b.scenario}:${b.revision}`);
}

const rows = collectBenchmarkRows();
const scenarios = buildScenarioCatalog(rows);
const scenarioDiffs = buildScenarioDiffs(scenarios);
const deltas = buildScoreDeltas(rows);

function buildScenarioDiffs(scenarios) {
  return [...scenarioFamilies(scenarios).entries()].flatMap(([scenario, revisions]) =>
    revisionDiffs(scenario, revisions),
  );
}

function scenarioFamilies(scenarios) {
  const families = new Map();
  for (const meta of scenarios) {
    if (!families.has(meta.scenario)) families.set(meta.scenario, []);
    families.get(meta.scenario).push(meta);
  }
  return families;
}

function revisionDiffs(scenario, revisions) {
  const sortedRevisions = [...revisions].sort((a, b) =>
    String(a.revision).localeCompare(String(b.revision)),
  );
  return sortedRevisions.slice(1).map((after, index) => {
    const before = sortedRevisions[index];
    return buildRevisionDiff(scenario, before, after);
  });
}

function buildRevisionDiff(scenario, before, after) {
  const files = revisionDiffFiles(before, after);
  const summary = classifyRevisionChange(before, after, files);
  return {
    key: `${scenario}:${before.revision}:${after.revision}`,
    scenario,
    from_revision: before.revision,
    to_revision: after.revision,
    summary,
    comparable_warnings: comparableRevisionWarnings(summary),
    files,
  };
}

function revisionDiffFiles(before, after) {
  const beforeYaml = readText(path.join(repoRoot, before.files.scenario_yaml || ''));
  const afterYaml = readText(path.join(repoRoot, after.files.scenario_yaml || ''));
  const beforePrompt = readText(path.join(repoRoot, before.files.prompt || ''));
  const afterPrompt = readText(path.join(repoRoot, after.files.prompt || ''));
  return {
    scenario: { path: after.files.scenario_yaml, diff: lineDiff(beforeYaml, afterYaml) },
    prompt: { path: after.files.prompt, diff: lineDiff(beforePrompt, afterPrompt) },
  };
}

function comparableRevisionWarnings(summary) {
  const nonComparable = new Set([
    'prompt changed',
    'scenario contract changed',
    'metadata unchanged',
  ]);
  return summary.filter((flag) => !nonComparable.has(flag));
}

function buildScoreDeltas(rows) {
  return Object.entries(groupRowsByScenarioModel(rows)).flatMap(([key, entries]) =>
    scoreDeltasForGroup(key, entries),
  );
}

function groupRowsByScenarioModel(rows) {
  const grouped = {};
  for (const row of rows) {
    const key = `${row.scenario}:${row.model}`;
    grouped[key] ||= [];
    grouped[key].push(row);
  }
  return grouped;
}

function scoreDeltasForGroup(key, entries) {
  const scoredEntries = latestScoredRevisionEntries(entries);
  const deltas = adjacentScoreDeltas(key, scoredEntries);
  if (scoredEntries.length > 2) {
    deltas.push(scoreDelta(key, scoredEntries[0], scoredEntries.at(-1), true));
  }
  return deltas;
}

function latestScoredRevisionEntries(entries) {
  const latestByRevision = new Map();
  for (const entry of entries) {
    if (typeof entry.mean_score !== 'number') continue;
    const current = latestByRevision.get(entry.revision);
    if (!current || String(entry.run_id).localeCompare(String(current.run_id)) > 0) {
      latestByRevision.set(entry.revision, entry);
    }
  }
  return [...latestByRevision.values()].sort((a, b) =>
    String(a.revision).localeCompare(String(b.revision)),
  );
}

function adjacentScoreDeltas(key, scoredEntries) {
  return scoredEntries
    .slice(1)
    .map((current, index) => scoreDelta(key, scoredEntries[index], current, false));
}

function scoreDelta(key, previous, current, firstToLast) {
  return {
    key,
    scenario: current.scenario,
    model: current.model,
    agent_spec: current.agent_spec,
    from_revision: previous.revision,
    to_revision: current.revision,
    previous_score: previous.mean_score,
    current_score: current.mean_score,
    delta: current.mean_score - previous.mean_score,
    first_to_last: firstToLast,
    duration_delta: current.duration_sec - previous.duration_sec,
    token_delta: current.uncached_input_tokens - previous.uncached_input_tokens,
    performance_delta: current.performance_pass_rate - previous.performance_pass_rate,
  };
}

function dashboardPayload({ rows, scenarios, scenarioDiffs, deltas }) {
  return {
    generated_at: new Date().toISOString(),
    rows: sanitizeDashboardValue(rows),
    scenarios: sanitizeDashboardValue(scenarios),
    scenario_diffs: sanitizeDashboardValue(scenarioDiffs),
    deltas: sanitizeDashboardValue(deltas),
  };
}

fs.writeFileSync(
  outPath,
  JSON.stringify(dashboardPayload({ rows, scenarios, scenarioDiffs, deltas }), null, 2),
);
console.log(`Wrote ${outPath} with ${rows.length} rows`);
