// Projects Raidar benchmark artifacts (experiments/benchmarks/**) into the
// Workshop-shaped review data consumed by the review-surface app:
//   data/runs.json          - run index records
//   data/runs/<run_id>.json - { run, spans, annotations } detail records
//   data/experiments.json   - experiment rollups for comparison views
// Raidar artifacts stay authoritative; this output is regenerable review data.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const surfaceRoot = path.resolve(here, '..');
const repoRoot = path.resolve(surfaceRoot, '..');
const benchRoot = path.join(repoRoot, 'experiments', 'benchmarks');
const scenariosRoot = path.join(repoRoot, 'scenarios');
const dataRoot = path.join(surfaceRoot, 'data');

const PAYLOAD_CAP = 32000;

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch {
    return null;
  }
}

function listDirs(dirPath) {
  try {
    return fs
      .readdirSync(dirPath)
      .filter((name) => fs.statSync(path.join(dirPath, name)).isDirectory())
      .sort();
  } catch {
    return [];
  }
}

function toMs(isoOrNumber) {
  if (typeof isoOrNumber === 'number') return isoOrNumber;
  const parsed = Date.parse(String(isoOrNumber ?? ''));
  return Number.isFinite(parsed) ? parsed : null;
}

function payload(value) {
  if (value === null || value === undefined) return null;
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return text.length > PAYLOAD_CAP ? `${text.slice(0, PAYLOAD_CAP)}\n…[truncated]` : text;
}

function runStatus(scores) {
  if (scores.unscored) return 'ERROR';
  const validityChecks = scores.execution_validity?.checks ?? [];
  const validityFailed = validityChecks.some((check) => check.passed === false);
  if (validityFailed || scores.functional?.passed === false) return 'ERROR';
  return 'OK';
}

function uncachedInputTokens(scores) {
  const process = scores.metadata?.process;
  const processValue = process && typeof process === 'object' ? process.uncached_input_tokens : null;
  return Number(processValue ?? scores.resource_efficiency?.uncached_input_tokens ?? 0) || 0;
}

function outputTokens(scores) {
  return Number(scores.metadata?.process?.output_tokens ?? scores.resource_efficiency?.output_tokens ?? 0) || 0;
}

class SpanBuilder {
  constructor(runId, startedAtMs) {
    this.runId = runId;
    this.startedAtMs = startedAtMs;
    this.spans = [];
    this.counter = 0;
  }

  add(span) {
    this.counter += 1;
    const id = `${this.runId}:s${this.counter}`;
    const record = {
      id,
      run_id: this.runId,
      parent_span_id: span.parent ?? null,
      name: span.name,
      span_type: span.type ?? 'INTERNAL',
      status: span.status ?? 'OK',
      start_time_ms: span.start ?? null,
      end_time_ms: span.end ?? (span.start != null ? span.start + (span.duration ?? 1000) : null),
      duration_ms:
        span.duration ?? (span.start != null && span.end != null ? span.end - span.start : null),
      model: span.model ?? null,
      input_tokens: span.inputTokens ?? null,
      output_tokens: span.outputTokens ?? null,
      input_payload: payload(span.input),
      output_payload: payload(span.output),
      attributes: span.attributes ? payload(span.attributes) : null,
    };
    this.spans.push(record);
    return id;
  }
}

function traceSpanName(event) {
  const data = event.data ?? {};
  if (event.event_type === 'bash_command') {
    const command = String(data.command ?? 'command');
    return command.length > 60 ? `${command.slice(0, 57)}…` : command;
  }
  if (event.event_type === 'file_change') return `edit ${data.file_path ?? 'file'}`;
  if (event.event_type === 'assistant_message') return 'assistant message';
  if (event.event_type === 'user_prompt') return 'user prompt';
  if (event.event_type === 'tool_call') return `tool ${data.tool ?? data.name ?? 'call'}`;
  return event.event_type;
}

function projectTraceEvents(builder, run, agentRootId) {
  const traces = run.traces ?? [];
  let lastCommandSpanId = null;
  for (let index = 0; index < traces.length; index += 1) {
    const event = traces[index];
    const start = toMs(event.timestamp);
    const nextStart = index + 1 < traces.length ? toMs(traces[index + 1].timestamp) : null;
    const duration = start != null && nextStart != null ? Math.max(nextStart - start, 200) : 1000;
    if (event.event_type === 'gate_result' && lastCommandSpanId) {
      const target = builder.spans.find((span) => span.id === lastCommandSpanId);
      const exitCode = Number(event.data?.exit_code ?? 0);
      if (target) {
        target.status = exitCode === 0 ? 'OK' : 'ERROR';
        target.output_payload = payload(event.data);
      }
      lastCommandSpanId = null;
      continue;
    }
    const isCommand = event.event_type === 'bash_command' || event.event_type === 'tool_call';
    const spanId = builder.add({
      parent: agentRootId,
      name: traceSpanName(event),
      type:
        event.event_type === 'assistant_message'
          ? 'LLM_GENERATION'
          : isCommand
            ? 'TOOL_CALL'
            : 'INTERNAL',
      status: 'OK',
      start,
      duration,
      model: event.event_type === 'assistant_message' ? run.config?.model : null,
      input: event.event_type === 'assistant_message' ? null : event.data,
      output: event.event_type === 'assistant_message' ? event.data?.content : null,
    });
    if (isCommand) lastCommandSpanId = spanId;
  }
}

function projectGateHistory(builder, run, rootId, sections) {
  const gates = run.gate_history ?? [];
  if (!gates.length) return;
  const firstTs = toMs(gates[0]?.timestamp);
  const sectionId = builder.add({
    parent: rootId,
    name: 'verification gates',
    type: 'INTERNAL',
    status: gates.some((gate) => gate.exit_code !== 0) ? 'ERROR' : 'OK',
    start: firstTs,
    duration: Math.max(gates.length * 1500, 1500),
  });
  sections.verification = sectionId;
  for (const gate of gates) {
    builder.add({
      parent: sectionId,
      name: `gate:${gate.gate_name}`,
      type: 'TOOL_CALL',
      status: gate.exit_code === 0 ? 'OK' : 'ERROR',
      start: toMs(gate.timestamp),
      duration: 1200,
      input: gate.command,
      output: {
        exit_code: gate.exit_code,
        failure_category: gate.failure_category,
        is_repeat: gate.is_repeat,
        stdout: gate.stdout,
        stderr: gate.stderr,
      },
    });
  }
}

function projectScoring(builder, run, rootId, sections, baseTs) {
  const scores = run.scores ?? {};
  const sectionId = builder.add({
    parent: rootId,
    name: 'scoring',
    type: 'INTERNAL',
    status: scores.unscored ? 'ERROR' : 'OK',
    start: baseTs,
    duration: 2000,
    output: {
      composite_score: scores.composite_score,
      quality_score: scores.quality_score,
      diagnostic_score: scores.diagnostic_score,
      unscored: scores.unscored,
      unscored_reasons: scores.unscored_reasons,
    },
  });
  sections.scoring = sectionId;
  const contributionsByMetric = new Map();
  for (const scorer of scores.scorer_results ?? []) {
    const scorerId = builder.add({
      parent: sectionId,
      name: `scorer:${scorer.scorer_id}@${scorer.version}`,
      type: 'INTERNAL',
      status: 'OK',
      start: baseTs,
      duration: 1000,
      output: scorer,
    });
    for (const contribution of scorer.metric_contributions ?? []) {
      contributionsByMetric.set(contribution.metric_id, scorerId);
    }
  }
  for (const metric of scores.metric_scores ?? []) {
    const parent = contributionsByMetric.get(metric.metric_id) ?? sectionId;
    const spanId = builder.add({
      parent,
      name: `metric:${metric.metric_id}`,
      type: 'INTERNAL',
      status: metric.passed ? 'OK' : 'ERROR',
      start: baseTs,
      duration: 800,
      output: metric,
    });
    sections.metrics.set(metric.metric_id, spanId);
  }
}

function projectEvidenceSections(builder, run, rootId, runDir, sections, baseTs) {
  const scores = run.scores ?? {};
  if (scores.requirements_coverage) {
    sections.requirements = builder.add({
      parent: rootId,
      name: 'requirements',
      type: 'INTERNAL',
      status: (scores.requirements_coverage.missing_requirement_ids ?? []).length
        ? 'ERROR'
        : 'OK',
      start: baseTs,
      duration: 800,
      output: scores.requirements_coverage,
    });
  }
  sections.validity = builder.add({
    parent: rootId,
    name: 'execution validity',
    type: 'INTERNAL',
    status: (scores.execution_validity?.checks ?? []).some((check) => !check.passed)
      ? 'ERROR'
      : 'OK',
    start: baseTs,
    duration: 600,
    output: { execution_validity: scores.execution_validity, performance_gates: scores.performance_gates },
  });
  sections.process = builder.add({
    parent: rootId,
    name: 'process metrics',
    type: 'INTERNAL',
    status: 'OK',
    start: baseTs,
    duration: 600,
    output: {
      process: scores.metadata?.process ?? {},
      resource_efficiency: scores.resource_efficiency,
      harbor: scores.metadata?.harbor ?? {},
    },
  });
  const workspaceDiff = readJson(path.join(runDir, 'workspace-diff.json'));
  sections.artifacts = builder.add({
    parent: rootId,
    name: 'artifacts & evidence',
    type: 'INTERNAL',
    status: 'OK',
    start: baseTs,
    duration: 600,
    output: {
      evidence: scores.metadata?.evidence ?? {},
      workspace_diff: workspaceDiff ?? undefined,
    },
  });
}

const FINDING_SPAN_TARGETS = {
  'missing-required-command': 'process',
  'workflow-anomaly': 'process',
  'resource-outlier': 'process',
  'requirements-gap': 'requirements',
  'requirements-satisfied': 'requirements',
  'retained-evidence': 'artifacts',
  'completion-claim': 'validity',
  'performance-gate': 'validity',
  'clean-verification': 'verification',
};

function findingSpanId(finding, sections, builder) {
  if (finding.category === 'failed-gate') {
    const reference = finding.evidence?.[0]?.reference;
    const gateSpan = builder.spans.find((span) => span.name === `gate:${reference}`);
    if (gateSpan) return gateSpan.id;
    return sections.verification ?? null;
  }
  if (['judge-review', 'deterministic-cap'].includes(finding.category)) {
    const reference = finding.evidence?.[0]?.reference;
    return sections.metrics.get(reference) ?? sections.scoring ?? null;
  }
  if (finding.category === 'missing-artifact') {
    const source = finding.evidence?.[0]?.source;
    if (source === 'metric_scores') {
      return sections.metrics.get(finding.evidence?.[0]?.reference) ?? sections.scoring ?? null;
    }
    return sections.artifacts ?? null;
  }
  const target = FINDING_SPAN_TARGETS[finding.category];
  return target ? (sections[target] ?? null) : null;
}

function projectAnnotations(run, findingsPayload, sections, builder, startedAtMs) {
  const findings = findingsPayload?.findings ?? [];
  return findings.map((finding) => ({
    id: `${run.id}:${finding.id}`,
    run_id: run.id,
    span_id: findingSpanId(finding, sections, builder),
    kind: finding.kind,
    note: finding.detail ? `${finding.title} — ${finding.detail}` : finding.title,
    source: 'raidar',
    created_at: startedAtMs,
    category: finding.category,
    evidence: finding.evidence ?? [],
  }));
}

function findingCounts(annotations, experimentFindings) {
  const counts = { issue: 0, good: 0, note: 0 };
  for (const item of [...annotations, ...experimentFindings]) {
    if (counts[item.kind] !== undefined) counts[item.kind] += 1;
  }
  return counts;
}

function projectRun(experiment, runDir) {
  const run = readJson(path.join(runDir, 'run.json'));
  if (!run) return null;
  const findingsPayload = readJson(path.join(runDir, 'findings.json'));
  const scores = run.scores ?? {};
  const startedAtMs = toMs(run.timestamp) ?? 0;
  const durationMs = Math.round((run.duration_sec ?? 0) * 1000);
  const builder = new SpanBuilder(run.id, startedAtMs);
  const sections = { metrics: new Map() };

  const rootId = builder.add({
    name: `${scores.scenario_name}@${scores.scenario_revision}`,
    type: 'TRACE',
    status: runStatus(scores),
    start: startedAtMs,
    duration: durationMs,
    output: {
      composite_score: scores.composite_score,
      quality_score: scores.quality_score,
      harness: scores.harness,
      model: scores.model,
    },
  });
  const traceEvents = run.traces ?? [];
  const agentStart = traceEvents.length ? toMs(traceEvents[0].timestamp) : startedAtMs;
  const agentRootId = builder.add({
    parent: rootId,
    name: 'agent execution',
    type: 'AGENT_ROOT',
    status: 'OK',
    start: agentStart,
    duration: durationMs > 0 ? Math.round(durationMs * 0.7) : null,
    model: run.config?.model,
    inputTokens: uncachedInputTokens(scores),
    outputTokens: outputTokens(scores),
  });
  projectTraceEvents(builder, run, agentRootId);
  const lastEventTs = traceEvents.length
    ? toMs(traceEvents[traceEvents.length - 1].timestamp)
    : startedAtMs;
  projectGateHistory(builder, run, rootId, sections);
  const scoringBase = (lastEventTs ?? startedAtMs) + 2000;
  projectScoring(builder, run, rootId, sections, scoringBase);
  projectEvidenceSections(builder, run, rootId, runDir, sections, scoringBase + 2200);

  const annotations = projectAnnotations(run, findingsPayload, sections, builder, startedAtMs);
  const experimentFindings = experiment.findings ?? [];
  const indexRecord = {
    id: run.id,
    name: `${scores.scenario_name}@${scores.scenario_revision} · ${run.id}`,
    scenario: scores.scenario_name,
    revision: scores.scenario_revision,
    harness: scores.harness,
    model: scores.model,
    agent_spec: `${scores.harness} · ${scores.model}`,
    experiment_id: experiment.experiment_id,
    started_at: startedAtMs,
    duration_ms: durationMs,
    status: runStatus(scores),
    span_count: builder.spans.length,
    total_input_tokens: uncachedInputTokens(scores),
    total_output_tokens: outputTokens(scores),
    composite_score: scores.composite_score ?? null,
    quality_score: scores.quality_score ?? null,
    diagnostic_score: scores.diagnostic_score ?? null,
    unscored: Boolean(scores.unscored),
    unscored_reasons: scores.unscored_reasons ?? [],
    valid: !(scores.execution_validity?.checks ?? []).some((check) => !check.passed),
    synthetic: Boolean(experiment.synthetic),
    finding_counts: findingCounts(annotations, []),
    issue_categories: Object.fromEntries(
      annotations
        .filter((annotation) => annotation.kind === 'issue')
        .reduce((map, annotation) => {
          map.set(annotation.category, (map.get(annotation.category) ?? 0) + 1);
          return map;
        }, new Map()),
    ),
    failed_gates: builder.spans
      .filter((span) => span.name.startsWith('gate:') && span.status === 'ERROR')
      .map((span) => span.name.slice('gate:'.length)),
    artifact_paths: {
      run_json: path.relative(repoRoot, path.join(runDir, 'run.json')),
      findings_json: fs.existsSync(path.join(runDir, 'findings.json'))
        ? path.relative(repoRoot, path.join(runDir, 'findings.json'))
        : null,
    },
  };
  return {
    index: indexRecord,
    detail: { run: indexRecord, spans: builder.spans, annotations },
    experimentFindings,
  };
}

function firstYamlScalar(text, key) {
  const prefix = `${key}:`;
  const line = text.split('\n').find((candidate) => candidate.startsWith(prefix));
  return line ? line.slice(prefix.length).replace(/^['"]|['"]$/g, '').trim() : null;
}

function readText(filePath) {
  try {
    return fs.readFileSync(filePath, 'utf8');
  } catch {
    return '';
  }
}

// --- Revision diffing (adapted from the deprecated benchmark-view builder) ---

function lineDiff(beforeText, afterText, maxLines = 160) {
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
  const dp = Array.from({ length: before.length + 1 }, () => Array(after.length + 1).fill(0));
  for (let i = before.length - 1; i >= 0; i -= 1) {
    for (let j = after.length - 1; j >= 0; j -= 1) {
      dp[i][j] =
        before[i] === after[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  return dp;
}

function appendChangedLines(state, before, after, dp, maxLines) {
  while (state.i < before.length && state.j < after.length && state.lines.length < maxLines) {
    if (before[state.i] === after[state.j]) {
      if (before[state.i].trim()) state.lines.push({ type: 'context', text: before[state.i] });
      state.i += 1;
      state.j += 1;
    } else if (dp[state.i + 1][state.j] >= dp[state.i][state.j + 1]) {
      state.lines.push({ type: 'removed', text: before[state.i] });
      state.removed += 1;
      state.i += 1;
    } else {
      state.lines.push({ type: 'added', text: after[state.j] });
      state.added += 1;
      state.j += 1;
    }
  }
}

function appendRemainingLines(state, source, type, maxLines) {
  const cursor = type === 'removed' ? 'i' : 'j';
  while (state[cursor] < source.length && state.lines.length < maxLines) {
    state.lines.push({ type, text: source[state[cursor]] });
    state[type] += 1;
    state[cursor] += 1;
  }
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

function scorerProfile(text) {
  const lines = text.split('\n');
  const start = lines.findIndex((line) => line.trim() === 'scorers:');
  if (start === -1) return '';
  const refs = [];
  let current = null;
  for (let index = start + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (/^\S/.test(line) && !line.startsWith('-') && !line.startsWith(' ')) break;
    const idMatch = line.match(/^\s*-\s*id:\s*(.+)$/);
    if (idMatch) {
      current = { id: idMatch[1].trim(), weight: null };
      refs.push(current);
      continue;
    }
    if (!current) continue;
    const weightMatch = line.match(/^\s*weight:\s*(.+)$/);
    if (weightMatch) current.weight = weightMatch[1].trim();
  }
  return refs.map((ref) => `${ref.id}:${ref.weight ?? '1'}`).join('+');
}

function topLevelSection(text, key) {
  const lines = text.split('\n');
  const start = lines.findIndex((line) => line.trim() === `${key}:`);
  if (start === -1) return '';
  const section = [lines[start]];
  for (let index = start + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (/^\S/.test(line) && !line.startsWith('-')) break;
    section.push(line);
  }
  return section.join('\n').trim();
}

function rulesDigest(rulesDir) {
  try {
    return fs
      .readdirSync(rulesDir)
      .filter((name) => fs.statSync(path.join(rulesDir, name)).isFile())
      .sort()
      .map((name) => `${name}:${fs.readFileSync(path.join(rulesDir, name), 'utf8')}`)
      .join('\n---\n');
  } catch {
    return '';
  }
}

function classifyRevisionChange(beforeYaml, afterYaml, promptDiff, beforeRulesDir, afterRulesDir) {
  const changes = [
    [scorerProfile(beforeYaml) !== scorerProfile(afterYaml), 'evaluation profile changed'],
    [gateNames(beforeYaml).join(',') !== gateNames(afterYaml).join(','), 'quality gates changed'],
    [topLevelSection(beforeYaml, 'requirements') !== topLevelSection(afterYaml, 'requirements'), 'requirements changed'],
    [topLevelSection(beforeYaml, 'starter') !== topLevelSection(afterYaml, 'starter'), 'starter changed'],
    [rulesDigest(beforeRulesDir) !== rulesDigest(afterRulesDir), 'rules changed'],
    [promptDiff.added + promptDiff.removed > 0, 'prompt changed'],
  ];
  const flags = changes.filter(([changed]) => changed).map(([, label]) => label);
  return flags.length ? flags : ['metadata unchanged'];
}

const COMPARABILITY_WARNINGS = new Set(['evaluation profile changed']);

function buildRevisionDiffs(experiments) {
  const revisionsByScenario = new Map();
  for (const experiment of experiments) {
    if (!experiment.scenario || !experiment.revision) continue;
    const set = revisionsByScenario.get(experiment.scenario) ?? new Set();
    set.add(experiment.revision);
    revisionsByScenario.set(experiment.scenario, set);
  }
  const diffs = [];
  for (const [scenario, revisionSet] of revisionsByScenario) {
    const revisions = [...revisionSet].sort();
    for (let index = 1; index < revisions.length; index += 1) {
      const diff = buildRevisionDiff(scenario, revisions[index - 1], revisions[index]);
      if (diff) diffs.push(diff);
    }
  }
  return diffs;
}

function buildRevisionDiff(scenario, fromRevision, toRevision) {
  const beforeYaml = readText(path.join(scenariosRoot, scenario, fromRevision, 'scenario.yaml'));
  const afterYaml = readText(path.join(scenariosRoot, scenario, toRevision, 'scenario.yaml'));
  if (!beforeYaml || !afterYaml) return null;
  const beforePrompt = readText(path.join(scenariosRoot, scenario, fromRevision, 'prompt', 'task.md'));
  const afterPrompt = readText(path.join(scenariosRoot, scenario, toRevision, 'prompt', 'task.md'));
  const beforeRulesDir = path.join(scenariosRoot, scenario, fromRevision, 'rules');
  const afterRulesDir = path.join(scenariosRoot, scenario, toRevision, 'rules');
  const yamlDiff = lineDiff(beforeYaml, afterYaml);
  const promptDiff = lineDiff(beforePrompt, afterPrompt);
  const summary = classifyRevisionChange(beforeYaml, afterYaml, promptDiff, beforeRulesDir, afterRulesDir);
  return {
    key: `${scenario}:${fromRevision}:${toRevision}`,
    scenario,
    from_revision: fromRevision,
    to_revision: toRevision,
    summary,
    comparable_warnings: summary.filter((flag) => COMPARABILITY_WARNINGS.has(flag)),
    files: {
      scenario: {
        path: path.join('scenarios', scenario, toRevision, 'scenario.yaml'),
        diff: yamlDiff,
      },
      prompt: {
        path: path.join('scenarios', scenario, toRevision, 'prompt', 'task.md'),
        diff: promptDiff,
      },
    },
  };
}

function scenarioMeta(scenarioName, revision) {
  if (!scenarioName || !revision) return null;
  let text;
  try {
    text = fs.readFileSync(
      path.join(scenariosRoot, scenarioName, revision, 'scenario.yaml'),
      'utf8',
    );
  } catch {
    return null;
  }
  return {
    description: firstYamlScalar(text, 'description'),
    difficulty: firstYamlScalar(text, 'difficulty'),
    category: firstYamlScalar(text, 'category'),
    timeout_sec: Number(firstYamlScalar(text, 'timeout_sec')) || null,
  };
}

function projectExperiment(dirName) {
  const experimentDir = path.join(benchRoot, dirName);
  const summary = readJson(path.join(experimentDir, 'experiment-summary.json'));
  if (!summary) return null;
  const experiment = {
    experiment_id: summary.experiment_id ?? dirName,
    dir: path.relative(repoRoot, experimentDir),
    scenario: summary.config?.scenario_name ?? null,
    revision: summary.config?.scenario_revision ?? null,
    harness: summary.config?.harness ?? null,
    model: summary.config?.model ?? null,
    agent_spec: `${summary.config?.harness ?? '?'} · ${summary.config?.model ?? '?'}`,
    synthetic: Boolean(summary.synthetic),
    repeats: summary.config?.repeats ?? null,
    scenario_meta: scenarioMeta(summary.config?.scenario_name, summary.config?.scenario_revision),
    aggregate: summary.aggregate ?? {},
    sample: summary.sample ?? {},
    rerun: summary.rerun ?? {},
    findings: summary.findings ?? [],
    created_at_utc: summary.created_at_utc ?? null,
    run_ids: [],
  };
  const runs = [];
  for (const runId of listDirs(path.join(experimentDir, 'runs'))) {
    const projected = projectRun(experiment, path.join(experimentDir, 'runs', runId));
    if (!projected) continue;
    experiment.run_ids.push(projected.index.id);
    runs.push(projected);
  }
  return { experiment, runs };
}

function main() {
  fs.rmSync(path.join(dataRoot, 'runs'), { recursive: true, force: true });
  fs.mkdirSync(path.join(dataRoot, 'runs'), { recursive: true });

  const experiments = [];
  const runIndex = [];
  for (const dirName of listDirs(benchRoot)) {
    const projected = projectExperiment(dirName);
    if (!projected) continue;
    experiments.push(projected.experiment);
    for (const run of projected.runs) {
      runIndex.push(run.index);
      fs.writeFileSync(
        path.join(dataRoot, 'runs', `${run.index.id}.json`),
        JSON.stringify(run.detail, null, 2),
      );
    }
  }
  runIndex.sort((a, b) => b.started_at - a.started_at || a.id.localeCompare(b.id));
  fs.writeFileSync(
    path.join(dataRoot, 'runs.json'),
    JSON.stringify({ generated_from: path.relative(repoRoot, benchRoot), runs: runIndex }, null, 2),
  );
  fs.writeFileSync(
    path.join(dataRoot, 'experiments.json'),
    JSON.stringify({ experiments, revision_diffs: buildRevisionDiffs(experiments) }, null, 2),
  );
  console.log(
    `Projected ${runIndex.length} run(s) across ${experiments.length} experiment(s) into ${path.relative(repoRoot, dataRoot)}`,
  );
}

main();
