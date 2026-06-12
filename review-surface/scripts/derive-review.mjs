// Derives the review presentation model (data/review.json) from projected
// benchmark artifacts, following docs/todos/review-surface specs:
// representative-experiment selection, five canonical review dimensions,
// pinned-benchmark deltas, confidence bands, verdict language, evidence
// blocks, run consistency, and hypothesis-shaped recommendations.

const DELTA_BAND = 0.05;
const INSTABILITY_THRESHOLD = 0.15;
const FAMILY_THRESHOLDS = {
  'visual-ui-implementation': { minimum: 3, preferred: 5 },
  'code-task-nonvisual': { minimum: 3, preferred: 5 },
  'open-ended-judged': { minimum: 5, preferred: 7 },
};
const DEFAULT_FAMILY = 'code-task-nonvisual';

const SUBTYPE_LABELS = {
  bugfix: 'Defect-Fix Fidelity',
  'typescript-code-task': 'Implementation Fidelity',
  'python-code-task': 'Implementation Fidelity',
  refactor: 'Refactor Fidelity',
  'test-generation': 'Test-Suite Fidelity',
  'plan-to-code': 'Plan Fidelity',
  'design-to-code': 'Visual Fidelity',
};

const DIMENSION_LABELS = {
  task_fidelity: 'Task Fidelity',
  scenario_fidelity: 'Scenario Fidelity',
  workflow_discipline: 'Workflow Discipline',
  execution_reliability: 'Execution Reliability',
  confidence: 'Confidence',
};

// --- small numeric helpers -------------------------------------------------

function clamp01(value) {
  return Math.min(1, Math.max(0, value));
}

function mean(values) {
  return values.length ? values.reduce((sum, v) => sum + v, 0) / values.length : null;
}

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function stddev(values) {
  if (values.length < 2) return 0;
  const m = mean(values);
  return Math.sqrt(mean(values.map((v) => (v - m) ** 2)));
}

function round3(value) {
  return value == null ? null : Math.round(value * 1000) / 1000;
}

/** Weighted mean over the components whose value is non-null, renormalizing weights. */
function weighted(components) {
  const present = components.filter((c) => c.value != null);
  const totalWeight = present.reduce((sum, c) => sum + c.weight, 0);
  if (!totalWeight) return null;
  return present.reduce((sum, c) => sum + c.value * (c.weight / totalWeight), 0);
}

function humanizeId(id) {
  return String(id ?? '').replace(/[-_]/g, ' ');
}

// --- per-run dimension snapshots -------------------------------------------

function primaryScorerId(input) {
  const candidates = (input.scorer_results ?? []).filter(
    (s) => !['requirements', 'resource-efficiency'].includes(s.scorer_id),
  );
  if (!candidates.length) return null;
  return candidates.sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0))[0].scorer_id;
}

function judgeVerdictRate(input) {
  const judged = (input.metric_scores ?? []).filter((m) => m.judge_output);
  if (!judged.length) return null;
  return mean(judged.map((m) => (m.judge_output.verdict === 'satisfied' ? 1 : 0)));
}

function runTaskFidelity(input) {
  const fn = input.functional ?? {};
  const req = input.requirements ?? null;
  const gateRatio = fn.gates_total > 0 ? fn.gates_passed / fn.gates_total : null;
  return weighted([
    { value: gateRatio, weight: 0.45 },
    { value: fn.passed && fn.build_succeeded !== false ? 1 : 0, weight: 0.2 },
    { value: req ? req.presence_ratio : null, weight: 0.15 },
    { value: req ? req.mapping_ratio : null, weight: 0.1 },
    { value: judgeVerdictRate(input), weight: 0.1 },
  ]);
}

/**
 * Fidelity context: how Scenario Fidelity is read for this scenario family.
 * Visual families read screenshot similarity; non-visual families read the
 * primary scenario-family scorer (the evidence-model subtype contract).
 */
function fidelityContext(inputs, family) {
  return {
    visual: family === 'visual-ui-implementation',
    scorerId: inputs.length ? primaryScorerId(inputs[0]) : null,
  };
}

function runScenarioFidelity(input, ctx) {
  if (ctx?.visual) return input.visual?.similarity ?? null;
  if (!ctx?.scorerId) return null;
  const scorer = (input.scorer_results ?? []).find((s) => s.scorer_id === ctx.scorerId);
  return scorer?.score ?? null;
}

function runWorkflowDiscipline(input) {
  const vs = input.verification_stability ?? {};
  const requiredExecuted = input.finding_categories?.['missing-required-command'] ? 0 : 1;
  const score = weighted([
    { value: requiredExecuted, weight: 0.3 },
    { value: (vs.total_gate_failures ?? 0) === 0 ? 1 : 0, weight: 0.3 },
    { value: vs.score ?? null, weight: 0.25 },
    { value: 1 - clamp01((vs.repeat_failures ?? 0) / 3), weight: 0.15 },
  ]);
  return score;
}

function runExecutionReliability(input) {
  return weighted([
    { value: input.execution_validity_passed ? 1 : 0, weight: 0.5 },
    { value: input.terminated_early ? 0 : 1, weight: 0.2 },
    { value: input.performance_gates_passed ? 1 : 0, weight: 0.15 },
    { value: input.termination_reason ? 0 : 1, weight: 0.15 },
  ]);
}

function runSnapshot(input, ctx) {
  return {
    run_id: input.run_id,
    task_fidelity: runTaskFidelity(input),
    scenario_fidelity: runScenarioFidelity(input, ctx),
    workflow_discipline: runWorkflowDiscipline(input),
    execution_reliability: runExecutionReliability(input),
  };
}

// --- experiment-level dimensions -------------------------------------------

function applyCaps(score, caps) {
  let capped = score;
  for (const cap of caps) capped = Math.min(capped, cap.limit);
  return { score: round3(capped), caps_triggered: caps.map((c) => c.reason) };
}

function deriveTaskFidelity(inputs) {
  const scored = inputs.filter((i) => !i.unscored);
  const raw = mean(scored.map((i) => runTaskFidelity(i)).filter((v) => v != null));
  if (raw == null) return { score: null, caps_triggered: [] };
  const caps = [];
  const detFailRate = mean(
    scored.map((i) => {
      const fn = i.functional ?? {};
      return fn.passed === false || (fn.gates_total > 0 && fn.gates_passed < fn.gates_total) ? 1 : 0;
    }),
  );
  if (detFailRate >= 0.5) {
    caps.push({ limit: 0.49, reason: 'deterministic authored checks failed in at least half of scored runs' });
  }
  const functionalRate = mean(scored.map((i) => (i.functional?.passed ? 1 : 0)));
  if (functionalRate < 0.5) {
    caps.push({ limit: 0.39, reason: 'functional success rate below 50%' });
  }
  return applyCaps(raw, caps);
}

function regionPassRate(visual) {
  const regions = visual?.regional_scores ?? [];
  if (!regions.length) return null;
  return mean(
    regions.map((region) =>
      (region.passed ?? (region.similarity ?? region.score ?? 0) >= (region.threshold ?? 0.8)) ? 1 : 0,
    ),
  );
}

function weakestRegions(inputs, limit = 2) {
  const byName = new Map();
  for (const input of inputs.filter((i) => !i.unscored)) {
    for (const region of input.visual?.regional_scores ?? []) {
      const entry = byName.get(region.name) ?? { scores: [], passes: 0, samples: 0 };
      entry.scores.push(region.similarity ?? region.score ?? 0);
      if (region.passed ?? (region.similarity ?? 0) >= (region.threshold ?? 0.8)) entry.passes += 1;
      entry.samples += 1;
      byName.set(region.name, entry);
    }
  }
  return [...byName.entries()]
    .map(([name, entry]) => ({
      name,
      median: round3(median(entry.scores)),
      pass_rate: round3(entry.passes / entry.samples),
    }))
    .filter((region) => region.pass_rate < 1)
    .sort((a, b) => a.median - b.median)
    .slice(0, limit);
}

function deriveVisualScenarioFidelity(inputs) {
  const scored = inputs.filter((i) => !i.unscored);
  const visuals = scored.map((i) => i.visual).filter(Boolean);
  if (!visuals.length) {
    return { score: null, caps_triggered: ['no visual evidence captured'], confidence_cap: 0.39 };
  }
  const simMedian = median(visuals.map((v) => v.similarity).filter((v) => v != null));
  const thresholdRate = mean(visuals.map((v) => (v.passed ? 1 : 0)));
  const regionRates = visuals.map((v) => regionPassRate(v)).filter((v) => v != null);
  let score;
  let confidenceCap = null;
  if (regionRates.length) {
    score = 0.6 * simMedian + 0.2 * thresholdRate + 0.2 * mean(regionRates);
  } else {
    score = 0.8 * simMedian + 0.2 * thresholdRate;
    confidenceCap = 0.59;
  }
  const caps = [];
  const captureFailRate = mean(scored.map((i) => (i.visual?.capture_succeeded ? 0 : 1)));
  if (captureFailRate >= 0.5) {
    caps.push({ limit: 0.29, reason: 'screenshot capture failed in at least half of scored runs' });
  }
  return { ...applyCaps(score, caps), confidence_cap: confidenceCap };
}

function deriveScenarioFidelity(inputs, ctx) {
  if (ctx.visual) return deriveVisualScenarioFidelity(inputs);
  const scores = inputs
    .filter((i) => !i.unscored)
    .map((i) => runScenarioFidelity(i, ctx))
    .filter((v) => v != null);
  if (!ctx.scorerId || !scores.length) {
    return { score: null, caps_triggered: [], confidence_cap: 0.39 };
  }
  return { ...applyCaps(median(scores), []), confidence_cap: null };
}

function deriveWorkflowDiscipline(inputs) {
  const scored = inputs.filter((i) => !i.unscored);
  const raw = mean(scored.map((i) => runWorkflowDiscipline(i)).filter((v) => v != null));
  if (raw == null) return { score: null, caps_triggered: [] };
  const caps = [];
  const executionRate = mean(
    scored.map((i) => (i.finding_categories?.['missing-required-command'] ? 0 : 1)),
  );
  if (executionRate < 1) {
    caps.push({ limit: 0.59, reason: 'required verification commands were not always executed' });
  }
  return applyCaps(raw, caps);
}

function deriveExecutionReliability(inputs) {
  const scored = inputs.filter((i) => !i.unscored);
  const raw = mean(scored.map((i) => runExecutionReliability(i)).filter((v) => v != null));
  if (raw == null) return { score: null, caps_triggered: [] };
  const caps = [];
  const validityRate = mean(scored.map((i) => (i.execution_validity_passed ? 1 : 0)));
  if (validityRate === 0) caps.push({ limit: 0, reason: 'no scored run preserved execution validity' });
  const fatalRate = mean(scored.map((i) => (i.termination_reason ? 1 : 0)));
  if (fatalRate >= 0.5) caps.push({ limit: 0.39, reason: 'fatal termination in at least half of scored runs' });
  return applyCaps(raw, caps);
}

function evidenceCompleteness(inputs, family) {
  const blocks = expectedEvidenceBlocks(inputs, family);
  const present = blocks.filter((b) => b.status === 'Present').length;
  return { fraction: blocks.length ? present / blocks.length : 0, blocks };
}

function expectedEvidenceBlocks(inputs, family) {
  const scored = inputs.filter((i) => !i.unscored);
  const has = (predicate) => scored.some(predicate);
  const blocks = [];
  if (family === 'visual-ui-implementation') {
    blocks.push({
      block: 'Visual evidence',
      status: has((i) => Boolean(i.visual?.actual_path && i.visual?.reference_path)) ? 'Present' : 'Missing',
    });
  }
  blocks.push(
    { block: 'Outcome proof', status: has((i) => (i.metric_scores ?? []).length > 0) ? 'Present' : 'Missing' },
    { block: 'Implementation proof', status: has((i) => (i.changed_files ?? []).length > 0) ? 'Present' : 'Missing' },
    {
      block: 'Verification proof',
      status: has((i) => (i.functional?.gates_total ?? 0) > 0 || (i.gate_history ?? []).length > 0)
        ? 'Present'
        : 'Missing',
    },
  );
  return blocks;
}

function deriveConfidence(experiment, inputs, snapshots, family) {
  const thresholds = FAMILY_THRESHOLDS[family] ?? FAMILY_THRESHOLDS[DEFAULT_FAMILY];
  const scoredCount = inputs.filter((i) => !i.unscored).length;
  const totalCount = inputs.length;
  const unresolved = experiment.rerun?.unresolved_unscored_count ?? (totalCount - scoredCount);
  const dims = ['task_fidelity', 'scenario_fidelity', 'workflow_discipline', 'execution_reliability'];
  const spreads = dims
    .map((d) => snapshots.map((s) => s[d]).filter((v) => v != null))
    .filter((vals) => vals.length >= 2)
    .map((vals) => stddev(vals));
  const aggregateSpread = spreads.length ? mean(spreads) : 0;
  const completeness = evidenceCompleteness(inputs, family);
  const components = [
    { name: 'sample adequacy', value: clamp01(scoredCount / thresholds.preferred), weight: 0.35 },
    {
      name: 'unresolved unscored burden',
      value: totalCount ? clamp01(1 - unresolved / totalCount) : 0,
      weight: 0.2,
    },
    { name: 'cross-run stability', value: clamp01(1 - aggregateSpread / INSTABILITY_THRESHOLD), weight: 0.25 },
    { name: 'evidence completeness', value: completeness.fraction, weight: 0.2 },
  ];
  const score = weighted(components);
  return {
    score: round3(score),
    band: confidenceBand(score),
    components: components.map((c) => ({ name: c.name, value: round3(c.value) })),
    spread: round3(aggregateSpread),
    evidence_blocks: completeness.blocks,
  };
}

function confidenceBand(score) {
  if (score == null) return 'Very Low';
  if (score >= 0.8) return 'High';
  if (score >= 0.6) return 'Medium';
  if (score >= 0.4) return 'Low';
  return 'Very Low';
}

// --- representative selection ----------------------------------------------

function identityKey(experiment) {
  return [experiment.scenario, experiment.revision, experiment.harness, experiment.model].join('|');
}

function scenarioFamily(experiment) {
  return experiment.sample?.scenario_family ?? DEFAULT_FAMILY;
}

function selectRepresentative(candidates) {
  const family = scenarioFamily(candidates[0]);
  const thresholds = FAMILY_THRESHOLDS[family] ?? FAMILY_THRESHOLDS[DEFAULT_FAMILY];
  const byRecency = [...candidates].sort((a, b) =>
    String(b.created_at_utc ?? '').localeCompare(String(a.created_at_utc ?? '')),
  );
  const scoredOf = (e) => e.aggregate?.run_count_scored ?? 0;
  const meeting = byRecency.find((e) => scoredOf(e) >= thresholds.minimum);
  if (meeting) {
    return {
      experiment: meeting,
      reason: `latest completed experiment with at least ${thresholds.minimum} scored runs`,
      below_minimum: false,
    };
  }
  const anyScored = byRecency.find((e) => scoredOf(e) > 0);
  if (anyScored) {
    return {
      experiment: anyScored,
      reason: `no experiment reached ${thresholds.minimum} scored runs; showing the most recent scored experiment`,
      below_minimum: true,
    };
  }
  return { experiment: null, reason: 'no completed experiment has scored runs', below_minimum: true };
}

// --- benchmark + deltas ----------------------------------------------------

function resolveBenchmark(config, scenario, rows) {
  const pin = config?.benchmarks?.[scenario] ?? null;
  if (!pin) return { status: 'none', pin: null, row: null };
  const row = rows.find((r) => r.harness === pin.harness && r.model === pin.model) ?? null;
  if (!row) return { status: 'pinned-missing', pin, row: null };
  return { status: 'pinned', pin, row };
}

function deltaBand(delta) {
  if (delta >= DELTA_BAND) return 'Ahead';
  if (delta <= -DELTA_BAND) return 'Behind';
  return 'Parity';
}

function dimensionDeltas(row, benchRow) {
  const dims = ['task_fidelity', 'scenario_fidelity', 'workflow_discipline', 'execution_reliability'];
  const lowConfidence = (r) => (r.confidence?.score ?? 0) < 0.4;
  const deltas = {};
  for (const dim of dims) {
    const current = row.dimensions[dim]?.score;
    const bench = benchRow.dimensions[dim]?.score;
    if (current == null || bench == null) {
      deltas[dim] = { delta: null, band: 'Unavailable' };
    } else if (lowConfidence(row) || lowConfidence(benchRow)) {
      deltas[dim] = { delta: round3(current - bench), band: 'Inconclusive' };
    } else {
      deltas[dim] = { delta: round3(current - bench), band: deltaBand(current - bench) };
    }
  }
  return deltas;
}

function summarizeDelta(deltas) {
  const bands = Object.values(deltas).map((d) => d.band);
  if (bands.every((b) => b === 'Unavailable')) return 'Unavailable';
  if (bands.some((b) => b === 'Inconclusive')) return 'Inconclusive';
  if (bands.includes('Behind') && !bands.includes('Ahead')) return 'Behind';
  if (bands.includes('Ahead') && !bands.includes('Behind')) return 'Ahead';
  if (bands.includes('Ahead') && bands.includes('Behind')) return 'Mixed';
  return 'Parity';
}

// --- absolute status ---------------------------------------------------------

function absoluteStatus(row) {
  const { task_fidelity, scenario_fidelity, execution_reliability } = row.dimensions;
  if (row.representative.scored_count === 0) return 'Unavailable';
  if (scenario_fidelity.score == null) return 'Unavailable';
  const capped = [...task_fidelity.caps_triggered, ...execution_reliability.caps_triggered].length > 0;
  const meets =
    !capped &&
    (task_fidelity.score ?? 0) >= 0.85 &&
    (scenario_fidelity.score ?? 0) >= 0.8 &&
    (execution_reliability.score ?? 0) >= 0.85;
  return meets ? 'Meets Scenario Bar' : 'Below Scenario Bar';
}

// --- verdict + diagnosis language -------------------------------------------

const COMPARATOR_VERBS = {
  High: { Ahead: 'outperforms the benchmark', Behind: 'underperforms the benchmark', Parity: 'is at parity with the benchmark', Mixed: 'splits results with the benchmark' },
  Medium: { Ahead: 'appears stronger than the benchmark', Behind: 'looks weaker than the benchmark', Parity: 'tracks close to the benchmark', Mixed: 'shows mixed results against the benchmark' },
  Low: { Ahead: 'may be stronger than the benchmark', Behind: 'is provisionally behind the benchmark', Parity: 'is provisionally level with the benchmark', Mixed: 'shows unstable results against the benchmark' },
};

function statusPhrase(status) {
  if (status === 'Meets Scenario Bar') return 'Meets the scenario bar';
  if (status === 'Below Scenario Bar') return 'Falls below the scenario bar';
  return 'No scored evidence yet';
}

function dominantDelta(deltas) {
  const entries = Object.entries(deltas).filter(([, d]) => d.delta != null && d.band !== 'Inconclusive');
  if (!entries.length) return null;
  return entries.sort((a, b) => Math.abs(b[1].delta) - Math.abs(a[1].delta))[0];
}

function comparatorPhrase(row) {
  const { benchmark_delta } = row;
  if (!benchmark_delta) return 'no benchmark pinned for this scenario';
  if (benchmark_delta.is_benchmark) return 'this is the pinned benchmark for the scenario';
  const summary = benchmark_delta.summary;
  if (summary === 'Unavailable') return 'benchmark comparison unavailable';
  if (summary === 'Inconclusive' || row.confidence.band === 'Very Low') {
    return 'insufficient evidence for a benchmark claim';
  }
  const verbs = COMPARATOR_VERBS[row.confidence.band] ?? COMPARATOR_VERBS.Low;
  const lead = dominantDelta(benchmark_delta.dimensions);
  const verb = verbs[summary] ?? verbs.Parity;
  const baseline = benchmark_delta.compatibility === 'changed-baseline' ? ' (changed baseline — treat with care)' : '';
  if (lead && summary !== 'Parity') {
    return `${verb} mainly on ${DIMENSION_LABELS[lead[0]].toLowerCase()}${baseline}`;
  }
  return `${verb}${baseline}`;
}

function buildVerdict(row) {
  const confidenceNote = `confidence ${row.confidence.band.toLowerCase()}`;
  return `${statusPhrase(row.absolute_status)}; ${comparatorPhrase(row)}; ${confidenceNote}.`;
}

// Efficiency metrics live in the anchor cluster, never in fidelity diagnosis.
const NON_FIDELITY_METRICS = new Set(['resource-efficiency']);
const REQUIREMENTS_METRICS = new Set(['requirements-coverage', 'requirements-adherence']);

function weakestChecks(inputs, limit = 2, exclude = NON_FIDELITY_METRICS) {
  const byMetric = new Map();
  for (const input of inputs.filter((i) => !i.unscored)) {
    for (const metric of input.metric_scores ?? []) {
      if (exclude.has(metric.metric_id)) continue;
      const entry = byMetric.get(metric.metric_id) ?? { scores: [], fails: 0 };
      entry.scores.push(metric.score ?? 0);
      if (metric.passed === false) entry.fails += 1;
      byMetric.set(metric.metric_id, entry);
    }
  }
  return [...byMetric.entries()]
    .map(([id, entry]) => ({ id, median: median(entry.scores), fails: entry.fails }))
    .sort((a, b) => b.fails - a.fails || a.median - b.median)
    .slice(0, limit);
}

function workflowWeaknessDetail(inputs) {
  const scored = inputs.filter((i) => !i.unscored);
  const parts = [];
  const failures = new Map();
  for (const input of scored) {
    for (const gate of input.gate_history ?? []) {
      if (gate.exit_code !== 0) failures.set(gate.gate_name, (failures.get(gate.gate_name) ?? 0) + 1);
    }
  }
  if (failures.size) {
    parts.push(`gate failures: ${[...failures.entries()].map(([name, count]) => `${name} ×${count}`).join(', ')}`);
  }
  const misses = scored.filter((i) => i.finding_categories?.['missing-required-command']).length;
  if (misses) parts.push(`required verification command missed in ${misses} run(s)`);
  return parts.join('; ');
}

function reliabilityWeaknessDetail(inputs) {
  const scored = inputs.filter((i) => !i.unscored);
  const invalid = scored.filter((i) => !i.execution_validity_passed).length;
  const early = scored.filter((i) => i.terminated_early || i.termination_reason).length;
  const parts = [];
  if (invalid) parts.push(`${invalid} run(s) failed validity checks`);
  if (early) parts.push(`${early} run(s) terminated abnormally`);
  return parts.join('; ');
}

function dimensionWeaknessDetail(key, row, inputs) {
  if (key === 'scenario_fidelity' && rowFidelityContext(row).visual) {
    const regions = weakestRegions(inputs);
    return regions.length
      ? ` Weakest regions: ${regions.map((r) => `${r.name} (median ${r.median}, pass rate ${Math.round((r.pass_rate ?? 0) * 100)}%)`).join(', ')}.`
      : '';
  }
  if (key === 'workflow_discipline') {
    const detail = workflowWeaknessDetail(inputs);
    return detail ? ` ${detail[0].toUpperCase()}${detail.slice(1)}.` : '';
  }
  if (key === 'execution_reliability') {
    const detail = reliabilityWeaknessDetail(inputs);
    return detail ? ` ${detail[0].toUpperCase()}${detail.slice(1)}.` : '';
  }
  const exclude =
    key === 'scenario_fidelity'
      ? new Set([...NON_FIDELITY_METRICS, ...REQUIREMENTS_METRICS])
      : NON_FIDELITY_METRICS;
  const checks = weakestChecks(inputs, 2, exclude);
  return checks.length
    ? ` Weakest checks: ${checks.map((c) => `${humanizeId(c.id)} (median ${round3(c.median)})`).join(', ')}.`
    : '';
}

function strengthLine(row, inputs) {
  const dims = Object.entries(row.dimensions)
    .filter(([key, d]) => key !== 'confidence' && d.score != null)
    .sort((a, b) => b[1].score - a[1].score);
  if (!dims.length) return 'No scored strengths yet.';
  const [key, dim] = dims[0];
  if (key === 'scenario_fidelity') {
    return `${row.scenario_fidelity_subtype} is the strongest signal (median ${dim.score}).`;
  }
  if (key === 'workflow_discipline') {
    const cleanRate = mean(
      inputs.filter((i) => !i.unscored).map((i) => ((i.verification_stability?.total_gate_failures ?? 0) === 0 ? 1 : 0)),
    );
    return `Clean verification behavior: ${Math.round((cleanRate ?? 0) * 100)}% of scored runs passed gates first time.`;
  }
  if (key === 'execution_reliability') {
    return `Runs completed cleanly and preserved evaluation validity (${dim.score}).`;
  }
  return `Authored task checks largely satisfied (${DIMENSION_LABELS[key]} ${dim.score}).`;
}

function weaknessLine(row, inputs) {
  const dims = Object.entries(row.dimensions)
    .filter(([key, d]) => key !== 'confidence' && d.score != null)
    .sort((a, b) => a[1].score - b[1].score);
  if (!dims.length) return 'No scored evidence to diagnose.';
  const [key, dim] = dims[0];
  if (dim.score >= 0.85) return `No material weakness; lowest dimension is ${DIMENSION_LABELS[key]} at ${dim.score}.`;
  return `${DIMENSION_LABELS[key]} is the limiting factor (${dim.score}).${dimensionWeaknessDetail(key, row, inputs)}`;
}

// --- evidence blocks ---------------------------------------------------------

function rowFidelityContext(row) {
  return { visual: row.scenario_family === 'visual-ui-implementation', scorerId: row.primary_scorer };
}

function anchorRun(inputs, ctx) {
  const scored = inputs.filter((i) => !i.unscored);
  if (!scored.length) return null;
  const med = median(scored.map((i) => runScenarioFidelity(i, ctx)).filter((v) => v != null));
  const pool = scored.filter((i) => i.valid);
  const candidates = pool.length ? pool : scored;
  const ranked = [...candidates].sort((a, b) => {
    const da = Math.abs((runScenarioFidelity(a, ctx) ?? 0) - (med ?? 0));
    const db = Math.abs((runScenarioFidelity(b, ctx) ?? 0) - (med ?? 0));
    return da - db;
  });
  return { run_id: ranked[0].run_id, atypical: !pool.length };
}

function outcomeProof(inputs, scorerId) {
  const scored = inputs.filter((i) => !i.unscored);
  if (!scored.length) return null;
  const byMetric = new Map();
  for (const input of scored) {
    for (const metric of input.metric_scores ?? []) {
      if (NON_FIDELITY_METRICS.has(metric.metric_id)) continue;
      const entry = byMetric.get(metric.metric_id) ?? {
        scores: [],
        passes: 0,
        judge: false,
        missing: new Set(),
        evidence: null,
      };
      entry.scores.push(metric.score ?? 0);
      if (metric.passed) entry.passes += 1;
      if (metric.judge_output) entry.judge = true;
      for (const pattern of metric.missing_patterns ?? []) entry.missing.add(pattern);
      if (metric.evidence && !entry.evidence) entry.evidence = metric.evidence;
      byMetric.set(metric.metric_id, entry);
    }
  }
  const checks = [...byMetric.entries()].map(([id, entry]) => ({
    name: humanizeId(id),
    kind: entry.judge ? 'judge' : 'deterministic',
    pass_rate: round3(entry.passes / scored.length),
    median_score: round3(median(entry.scores)),
    missing_patterns: [...entry.missing],
    evidence: entry.evidence,
  }));
  const req = scored.find((i) => i.requirements)?.requirements ?? null;
  return {
    checks: checks.sort((a, b) => a.pass_rate - b.pass_rate || a.median_score - b.median_score),
    requirements: req
      ? {
          total: req.total_requirements,
          presence_ratio: round3(median(scored.map((i) => i.requirements?.presence_ratio ?? 0))),
          mapping_ratio: round3(median(scored.map((i) => i.requirements?.mapping_ratio ?? 0))),
          missing_ids: req.missing_requirement_ids ?? [],
        }
      : null,
  };
}

function implementationProof(inputs) {
  const counts = new Map();
  for (const input of inputs) {
    for (const file of input.changed_files ?? []) {
      counts.set(file, (counts.get(file) ?? 0) + 1);
    }
  }
  if (!counts.size) return null;
  return {
    files: [...counts.entries()]
      .map(([path, runs_touched]) => ({ path, runs_touched }))
      .sort((a, b) => b.runs_touched - a.runs_touched),
    run_count: inputs.length,
  };
}

function verificationProof(inputs) {
  const scored = inputs.filter((i) => !i.unscored);
  if (!scored.length) return null;
  const gates = new Map();
  for (const input of scored) {
    for (const gate of input.gate_history ?? []) {
      const entry = gates.get(gate.gate_name) ?? { failures: 0, last_detail: null };
      if (gate.exit_code !== 0) {
        entry.failures += 1;
        entry.last_detail = gate.detail ?? null;
      }
      gates.set(gate.gate_name, entry);
    }
  }
  const firstPass = mean(scored.map((i) => ((i.verification_stability?.total_gate_failures ?? 0) === 0 ? 1 : 0)));
  const requiredMisses = inputs
    .flatMap((i) => (i.findings ?? []).filter((f) => f.category === 'missing-required-command'))
    .map((f) => f.title);
  return {
    first_pass_rate: round3(firstPass),
    gate_failures: [...gates.entries()]
      .filter(([, entry]) => entry.failures > 0)
      .map(([name, entry]) => ({ name, failures: entry.failures, last_detail: entry.last_detail })),
    required_command_misses: [...new Set(requiredMisses)],
    gates_per_run: scored[0]?.functional?.gates_total ?? 0,
  };
}

function visualProof(inputs, ctx) {
  if (!ctx.visual) return null;
  const scored = inputs.filter((i) => !i.unscored);
  const visuals = scored.filter((i) => i.visual);
  if (!visuals.length) return null;
  const anchor = anchorRun(inputs, ctx);
  const anchorInput = visuals.find((i) => i.run_id === anchor?.run_id) ?? visuals[0];
  const regionsByName = new Map();
  for (const input of visuals) {
    for (const region of input.visual.regional_scores ?? []) {
      const entry = regionsByName.get(region.name) ?? { scores: [], passes: 0, samples: 0 };
      entry.scores.push(region.similarity ?? region.score ?? 0);
      if (region.passed ?? (region.similarity ?? 0) >= (region.threshold ?? 0.8)) entry.passes += 1;
      entry.samples += 1;
      regionsByName.set(region.name, entry);
    }
  }
  const anchorRegions = new Map(
    (anchorInput.visual.regional_scores ?? []).map((region) => [region.name, region]),
  );
  return {
    anchor_run: anchorInput.run_id,
    similarity_median: round3(median(visuals.map((i) => i.visual.similarity).filter((v) => v != null))),
    threshold_pass_rate: round3(mean(visuals.map((i) => (i.visual.passed ? 1 : 0)))),
    capture_failures: scored.filter((i) => !i.visual?.capture_succeeded).length,
    reference_path: anchorInput.visual.reference_path,
    actual_path: anchorInput.visual.actual_path,
    diff_path: anchorInput.visual.diff_path,
    regions: [...regionsByName.entries()].map(([name, entry]) => {
      const anchorRegion = anchorRegions.get(name) ?? {};
      return {
        name,
        median_score: round3(median(entry.scores)),
        pass_rate: round3(entry.passes / entry.samples),
        threshold: anchorRegion.threshold ?? null,
        actual_path: anchorRegion.actual_path ?? null,
        reference_path: anchorRegion.reference_path ?? null,
        diff_path: anchorRegion.diff_path ?? null,
      };
    }),
  };
}

function buildEvidence(row, inputs, benchRow, benchInputs) {
  const ctx = rowFidelityContext(row);
  const current = {
    anchor: anchorRun(inputs, ctx),
    visual: visualProof(inputs, ctx),
    outcome: outcomeProof(inputs, row.primary_scorer),
    implementation: implementationProof(inputs),
    verification: verificationProof(inputs),
  };
  const benchmark = benchRow
    ? {
        anchor: anchorRun(benchInputs, rowFidelityContext(benchRow)),
        visual: visualProof(benchInputs, rowFidelityContext(benchRow)),
        outcome: outcomeProof(benchInputs, benchRow.primary_scorer),
        implementation: implementationProof(benchInputs),
        verification: verificationProof(benchInputs),
      }
    : null;
  return { availability: expectedEvidenceBlocks(inputs, row.scenario_family), current, benchmark };
}

// --- diagnosis ----------------------------------------------------------------

function diagnosisItems(row, inputs) {
  const strengths = [];
  const weaknesses = [];
  const scoredDims = Object.entries(row.dimensions).filter(
    ([key, d]) => key !== 'confidence' && d.score != null,
  );
  for (const [key, dim] of scoredDims) {
    const delta = row.benchmark_delta?.dimensions?.[key];
    const comparator = delta && delta.band !== 'Unavailable' ? `benchmark delta ${delta.band.toLowerCase()} (${delta.delta >= 0 ? '+' : ''}${delta.delta})` : 'no benchmark comparison';
    if (dim.score >= 0.85 || delta?.band === 'Ahead') {
      strengths.push({
        statement: strengthStatement(key, dim, row),
        dimension: DIMENSION_LABELS[key],
        comparator,
        evidence: dimensionEvidence(key, row, inputs, 'strength'),
        confidence: row.confidence.band,
      });
    }
    if (dim.score < 0.7 || delta?.band === 'Behind' || dim.caps_triggered.length) {
      weaknesses.push({
        statement: weaknessStatement(key, dim, row, inputs),
        dimension: DIMENSION_LABELS[key],
        comparator,
        evidence: dimensionEvidence(key, row, inputs, 'weakness'),
        confidence: row.confidence.band,
      });
    }
  }
  return { strengths, weaknesses };
}

function strengthStatement(key, dim, row) {
  if (key === 'scenario_fidelity') return `${row.scenario_fidelity_subtype} holds at ${dim.score} median across scored runs.`;
  return `${DIMENSION_LABELS[key]} is strong at ${dim.score}.`;
}

function weaknessStatement(key, dim, row, inputs) {
  const caps = dim.caps_triggered.length ? ` Hard override: ${dim.caps_triggered.join('; ')}.` : '';
  const label = key === 'scenario_fidelity' ? row.scenario_fidelity_subtype : DIMENSION_LABELS[key];
  return `${label} sits at ${dim.score}.${dimensionWeaknessDetail(key, row, inputs)}${caps}`;
}

function dimensionEvidence(key, row, inputs, polarity) {
  const refs = [];
  const scored = inputs.filter((i) => !i.unscored);
  const visual = rowFidelityContext(row).visual;
  if (key === 'scenario_fidelity' && polarity === 'weakness' && visual) {
    for (const region of weakestRegions(scored)) {
      refs.push({ label: `${region.name} region median ${region.median}`, block: 'Visual evidence' });
    }
  } else if ((key === 'task_fidelity' || key === 'scenario_fidelity') && polarity === 'weakness') {
    for (const check of weakestChecks(scored, 2)) {
      refs.push({ label: `${humanizeId(check.id)} median ${round3(check.median)}`, block: 'Outcome proof' });
    }
  }
  if (key === 'workflow_discipline') {
    for (const input of scored) {
      for (const finding of (input.findings ?? []).filter((f) =>
        ['failed-gate', 'missing-required-command'].includes(f.category),
      )) {
        refs.push({ label: finding.title, run_id: input.run_id, block: 'Verification proof' });
      }
    }
  }
  if (key === 'execution_reliability') {
    for (const input of scored.filter((i) => !i.valid || i.terminated_early)) {
      refs.push({ label: `run ${input.run_id} ${input.terminated_early ? 'terminated early' : 'failed validity checks'}`, run_id: input.run_id, block: 'Verification proof' });
    }
  }
  if (!refs.length) {
    const anchor = anchorRun(scored, rowFidelityContext(row));
    if (anchor) refs.push({ label: 'evidence anchor run', run_id: anchor.run_id, block: 'Outcome proof' });
  }
  return refs.slice(0, 4);
}

// --- recommendations -----------------------------------------------------------

function buildRecommendations(row, inputs, thresholds) {
  if (row.confidence.band === 'Very Low' || row.representative.scored_count === 0) {
    return [abstainRecommendation(row, thresholds)];
  }
  const candidates = [];
  pushSampleRecommendation(candidates, row, thresholds);
  pushFidelityRecommendation(candidates, row, inputs);
  pushWorkflowRecommendation(candidates, row);
  pushReliabilityRecommendation(candidates, row);
  pushContractRecommendation(candidates, row, inputs);
  const hedged = row.confidence.band === 'Low';
  return candidates.slice(0, 3).map((rec) => ({
    ...rec,
    confidence: hedged ? 'Low' : rec.confidence,
    hypothesis: hedged ? `${rec.hypothesis} Evidence is still limited, so treat this as a possible next test.` : rec.hypothesis,
  }));
}

function abstainRecommendation(row, thresholds) {
  return {
    title: 'Gather stronger evidence before optimizing',
    category: 'Verification workflow',
    target_dimension: 'Confidence',
    comparator: 'evidence gap',
    hypothesis: 'Do not recommend an optimization yet. The representative sample is too weak to support a directional change.',
    expected_gain: 'a trustworthy verdict and benchmark delta',
    evidence_refs: [{ label: `${row.representative.scored_count} scored run(s) available`, block: 'Outcome proof' }],
    confidence: 'Low',
    effort: 'Low',
    validation_plan: `Re-run this experiment with at least ${thresholds.preferred} scored repeats, then re-derive the review.`,
    abstain: true,
  };
}

function pushSampleRecommendation(candidates, row, thresholds) {
  if (row.representative.scored_count >= thresholds.preferred) return;
  candidates.push({
    title: 'Increase scored repeats before tuning',
    category: 'Verification workflow',
    target_dimension: 'Confidence',
    comparator: 'evidence gap',
    hypothesis: `Sample adequacy is the weakest input (${row.representative.scored_count}/${thresholds.preferred} preferred scored runs); more repeats should firm up every other conclusion.`,
    expected_gain: 'higher confidence band and stable benchmark deltas',
    evidence_refs: [{ label: `scored runs ${row.representative.scored_count} of preferred ${thresholds.preferred}`, block: 'Outcome proof' }],
    confidence: 'High',
    effort: 'Low',
    validation_plan: `Re-run the same AgentSpec on this revision with repeats=${thresholds.preferred} and compare confidence inputs.`,
  });
}

function pushFidelityRecommendation(candidates, row, inputs) {
  const delta = row.benchmark_delta?.dimensions?.scenario_fidelity;
  const behindBenchmark = delta?.band === 'Behind';
  const weak = (row.dimensions.scenario_fidelity.score ?? 1) < 0.8;
  if (!behindBenchmark && !weak) return;
  if (rowFidelityContext(row).visual) {
    const regions = weakestRegions(inputs);
    const target = regions.map((r) => `the ${r.name} region`).join(' and ') || 'the weakest page regions';
    candidates.push({
      title: `Improve design replication of ${target}`,
      category: 'Context and asset provisioning',
      target_dimension: 'Scenario Fidelity',
      comparator: behindBenchmark ? 'benchmark gap' : 'absolute weakness',
      hypothesis: `Providing the reference asset and explicit layout decomposition for ${target} could close the visual gap, because those regions score lowest against the reference.`,
      expected_gain: 'higher visual fidelity and regional threshold pass rates',
      evidence_refs: regions.map((r) => ({ label: `${r.name} region median ${r.median}`, block: 'Visual evidence' })),
      confidence: 'Medium',
      effort: 'Medium',
      validation_plan: 'Re-run with region-level design context, then compare the regional scores and diff assets against this representative.',
    });
    return;
  }
  const checks = weakestChecks(inputs, 2, new Set([...NON_FIDELITY_METRICS, ...REQUIREMENTS_METRICS]));
  const target = checks.map((c) => humanizeId(c.id)).join(' and ') || 'the weakest scenario checks';
  candidates.push({
    title: `Target ${target} directly in the task framing`,
    category: 'Prompting and task framing',
    target_dimension: 'Scenario Fidelity',
    comparator: behindBenchmark ? 'benchmark gap' : 'absolute weakness',
    hypothesis: `Steering the agent explicitly toward ${target} could close the largest fidelity gap, because those checks score lowest across scored runs.`,
    expected_gain: `higher ${row.scenario_fidelity_subtype.toLowerCase()} and a better shot at the scenario bar`,
    evidence_refs: checks.map((c) => ({ label: `${humanizeId(c.id)} median ${round3(c.median)}`, block: 'Outcome proof' })),
    confidence: 'Medium',
    effort: 'Medium',
    validation_plan: 'Revise the prompt/context to require the missing evidence, re-run, and compare these check medians against this representative.',
  });
}

function pushWorkflowRecommendation(candidates, row) {
  if ((row.dimensions.workflow_discipline.score ?? 1) >= 0.8) return;
  candidates.push({
    title: 'Tighten the verification workflow',
    category: 'Verification workflow',
    target_dimension: 'Workflow Discipline',
    comparator: 'absolute weakness',
    hypothesis: 'Requiring the harness to run every authored verification command and react to first failures should remove repeat gate failures.',
    expected_gain: 'cleaner first-pass verification and fewer wasted iterations',
    evidence_refs: [{ label: 'gate failures and required-command misses', block: 'Verification proof' }],
    confidence: 'Medium',
    effort: 'Medium',
    validation_plan: 'Add verification guardrails to the harness configuration, re-run, and compare first-pass verification rate.',
  });
}

function pushReliabilityRecommendation(candidates, row) {
  if ((row.dimensions.execution_reliability.score ?? 1) >= 0.85) return;
  candidates.push({
    title: 'Stabilize execution before optimizing quality',
    category: 'Harness or model choice',
    target_dimension: 'Execution Reliability',
    comparator: 'absolute weakness',
    hypothesis: 'Reliability failures (validity checks, early termination) invalidate comparisons; stabilizing the run loop comes before fidelity tuning.',
    expected_gain: 'comparable, valid runs across the whole sample',
    evidence_refs: [{ label: 'execution validity and termination evidence', block: 'Verification proof' }],
    confidence: 'Medium',
    effort: 'Medium',
    validation_plan: 'Re-run with the same contract and inspect validity checks and termination reasons per run.',
  });
}

function pushContractRecommendation(candidates, row, inputs) {
  const scored = inputs.filter((i) => !i.unscored && i.requirements);
  if (!scored.length) return;
  const mapping = median(scored.map((i) => i.requirements.mapping_ratio ?? 0));
  const presence = median(scored.map((i) => i.requirements.presence_ratio ?? 0));
  if (mapping > 0 || presence < 0.8) return;
  candidates.push({
    title: 'Add requirement-to-test mapping to the scenario contract',
    category: 'Scenario contract remediation',
    target_dimension: 'Task Fidelity',
    comparator: 'evidence gap',
    hypothesis: 'Requirements are present in the output but never mapped to tests; authoring mapping patterns in the scenario contract would make adherence measurable instead of unproven.',
    expected_gain: 'requirement-to-test evidence instead of a permanent mapping gap',
    evidence_refs: [{ label: `requirement mapping ratio ${round3(mapping)} despite presence ${round3(presence)}`, block: 'Outcome proof' }],
    confidence: 'Medium',
    effort: 'Medium',
    validation_plan: 'Author mapping patterns for each requirement in scenario.yaml, re-score retained runs, and check the mapping ratio moves.',
  });
}

// --- run consistency -----------------------------------------------------------

function runConsistency(inputs, ctx) {
  const scored = inputs.filter((i) => !i.unscored);
  const fidelityMedian = median(scored.map((i) => runScenarioFidelity(i, ctx)).filter((v) => v != null));
  return inputs.map((input) => {
    const snapshot = runSnapshot(input, ctx);
    const fidelity = snapshot.scenario_fidelity;
    const outlierReasons = [];
    if (input.unscored) outlierReasons.push('unscored');
    if (!input.valid) outlierReasons.push('failed validity checks');
    if (input.terminated_early) outlierReasons.push('terminated early');
    if (fidelity != null && fidelityMedian != null && Math.abs(fidelity - fidelityMedian) > 0.15) {
      outlierReasons.push(`scenario fidelity ${round3(fidelity)} vs median ${round3(fidelityMedian)}`);
    }
    if (input.visual && input.visual.passed === false) {
      outlierReasons.push('visual similarity threshold not met');
    }
    return {
      run_id: input.run_id,
      scored: !input.unscored,
      valid: input.valid,
      duration_sec: input.duration_sec,
      uncached_input_tokens: input.resource?.uncached_input_tokens ?? null,
      dimensions: {
        task_fidelity: round3(snapshot.task_fidelity),
        scenario_fidelity: round3(snapshot.scenario_fidelity),
        workflow_discipline: round3(snapshot.workflow_discipline),
        execution_reliability: round3(snapshot.execution_reliability),
      },
      issues: (input.findings ?? []).filter((f) => f.kind === 'issue').length,
      outlier: outlierReasons.length > 0,
      outlier_reasons: outlierReasons,
    };
  });
}

// --- change context --------------------------------------------------------------

function changeContext(row, allExperiments, revisionDiffs) {
  const isPrior = (e) =>
    String(e.revision).localeCompare(String(row.revision)) < 0 ||
    (e.revision === row.revision &&
      String(e.created_at_utc ?? '').localeCompare(String(row.representative.created_at_utc ?? '')) < 0);
  const prior = allExperiments
    .filter(
      (e) =>
        e.scenario === row.scenario &&
        e.harness === row.harness &&
        e.model === row.model &&
        e.dir !== row.representative.dir &&
        isPrior(e),
    )
    .sort((a, b) => String(b.revision).localeCompare(String(a.revision)) || String(b.created_at_utc ?? '').localeCompare(String(a.created_at_utc ?? '')))[0];
  if (!prior) {
    return { previous_experiment_id: null, previous_review_id: null, changes: [], summary: 'No prior representative experiment.' };
  }
  const changes = [];
  if (prior.revision !== row.revision) {
    const diff = revisionDiffs.find(
      (d) => d.scenario === row.scenario && d.to_revision === row.revision && d.from_revision === prior.revision,
    );
    changes.push({
      category: 'Scenario contract',
      detail: diff ? diff.summary.join(', ') : `scenario revision ${prior.revision} -> ${row.revision}`,
      comparability_warnings: diff?.comparable_warnings ?? [],
    });
  }
  if ((prior.repeats ?? null) !== (row.representative.repeats ?? null)) {
    changes.push({ category: 'Tooling and verification', detail: `repeat count ${prior.repeats} -> ${row.representative.repeats}`, comparability_warnings: [] });
  }
  const summary = changes.length
    ? `Compared with ${prior.revision}: ${changes[0].detail}`
    : 'No contract or policy changes detected since the previous representative experiment.';
  return {
    previous_experiment_id: prior.experiment_id,
    previous_review_id: reviewIdFor(prior),
    previous_revision: prior.revision,
    changes,
    summary,
  };
}

// --- efficiency anchors -----------------------------------------------------------

function efficiencyAnchors(inputs) {
  const scored = inputs.filter((i) => !i.unscored);
  const med = (selector) => round3(median(scored.map(selector).filter((v) => v != null)));
  return {
    duration_sec: med((i) => i.duration_sec),
    uncached_input_tokens: med((i) => i.resource?.uncached_input_tokens),
    command_count: med((i) => i.resource?.command_count),
    failed_command_count: med((i) => i.resource?.failed_command_count),
    verification_rounds: med((i) => i.resource?.verification_rounds),
  };
}

// --- main assembly -----------------------------------------------------------------

function reviewIdFor(experiment) {
  return String(experiment.dir ?? experiment.experiment_id).split('/').pop();
}

function buildRow(representative, inputsByExperiment) {
  const experiment = representative.experiment;
  const inputs = inputsByExperiment.get(experiment.dir) ?? [];
  const family = scenarioFamily(experiment);
  const ctx = fidelityContext(inputs, family);
  const scorerId = ctx.scorerId;
  const snapshots = inputs.filter((i) => !i.unscored).map((i) => runSnapshot(i, ctx));
  const scenarioFidelity = deriveScenarioFidelity(inputs, ctx);
  const dimensions = {
    task_fidelity: deriveTaskFidelity(inputs),
    scenario_fidelity: { score: scenarioFidelity.score, caps_triggered: scenarioFidelity.caps_triggered },
    workflow_discipline: deriveWorkflowDiscipline(inputs),
    execution_reliability: deriveExecutionReliability(inputs),
  };
  const confidence = deriveConfidence(experiment, inputs, snapshots, family);
  if (scenarioFidelity.confidence_cap != null && (confidence.score ?? 0) > scenarioFidelity.confidence_cap) {
    confidence.score = scenarioFidelity.confidence_cap;
    confidence.band = confidenceBand(confidence.score);
    confidence.components.push({ name: 'scenario-fidelity evidence cap', value: scenarioFidelity.confidence_cap });
  }
  const scoredCount = experiment.aggregate?.run_count_scored ?? 0;
  const totalCount = experiment.aggregate?.run_count_total ?? inputs.length;
  const row = {
    review_id: reviewIdFor(experiment),
    scenario: experiment.scenario,
    revision: experiment.revision,
    harness: experiment.harness,
    model: experiment.model,
    agent_spec: experiment.agent_spec,
    synthetic: Boolean(experiment.synthetic),
    scenario_family: family,
    primary_scorer: scorerId,
    scenario_fidelity_subtype: ctx.visual
      ? 'Visual Fidelity'
      : (SUBTYPE_LABELS[scorerId] ?? `${humanizeId(scorerId)} fidelity`),
    representative: {
      experiment_id: experiment.experiment_id,
      dir: experiment.dir,
      evaluation_profile: experiment.evaluation_profile ?? null,
      starter_fingerprint: experiment.starter_fingerprint ?? null,
      created_at_utc: experiment.created_at_utc,
      reason: representative.reason,
      below_minimum: representative.below_minimum,
      scored_count: scoredCount,
      total_count: totalCount,
      unresolved_unscored: experiment.rerun?.unresolved_unscored_count ?? totalCount - scoredCount,
      repeats: experiment.repeats,
      run_ids: experiment.run_ids,
    },
    dimensions,
    confidence,
    efficiency: efficiencyAnchors(inputs),
  };
  row.absolute_status = absoluteStatus(row);
  return row;
}

function benchmarkCompatibility(row, benchRow) {
  if ((row.representative.evaluation_profile ?? null) !== (benchRow.representative.evaluation_profile ?? null)) {
    return { state: 'incompatible', reason: 'evaluation profile differs from the benchmark' };
  }
  if ((row.representative.starter_fingerprint ?? null) !== (benchRow.representative.starter_fingerprint ?? null)) {
    return { state: 'changed-baseline', reason: 'starter fingerprint differs from the benchmark' };
  }
  return { state: 'compatible', reason: null };
}

function attachBenchmark(row, benchmark) {
  if (benchmark.status === 'none') {
    row.benchmark_delta = null;
    return;
  }
  if (!benchmark.row || benchmark.row.review_id === row.review_id) {
    row.benchmark_delta =
      benchmark.row && benchmark.row.review_id === row.review_id
        ? { is_benchmark: true, summary: 'Benchmark', dimensions: {}, compatibility: 'compatible', compatibility_reason: null }
        : { is_benchmark: false, summary: 'Unavailable', dimensions: {}, compatibility: 'compatible', compatibility_reason: null };
    return;
  }
  const compatibility = benchmarkCompatibility(row, benchmark.row);
  if (compatibility.state === 'incompatible') {
    row.benchmark_delta = {
      is_benchmark: false,
      summary: 'Unavailable',
      dimensions: {},
      compatibility: compatibility.state,
      compatibility_reason: compatibility.reason,
    };
    return;
  }
  const dimensions = dimensionDeltas(row, benchmark.row);
  row.benchmark_delta = {
    is_benchmark: false,
    summary: summarizeDelta(dimensions),
    dimensions,
    compatibility: compatibility.state,
    compatibility_reason: compatibility.reason,
  };
}

function buildBoard(scenario, revision, rows, config, experimentsAll) {
  const benchmark = resolveBenchmark(config, scenario, rows);
  for (const row of rows) attachBenchmark(row, benchmark);
  for (const row of rows) {
    row.verdict = buildVerdict(row);
  }
  const meta = experimentsAll.find(
    (e) => e.scenario === scenario && e.revision === revision && e.scenario_meta,
  )?.scenario_meta ?? null;
  const family = rows[0]?.scenario_family ?? DEFAULT_FAMILY;
  const thresholds = FAMILY_THRESHOLDS[family] ?? FAMILY_THRESHOLDS[DEFAULT_FAMILY];
  const statusRank = { 'Meets Scenario Bar': 0, 'Below Scenario Bar': 1, Unavailable: 2 };
  const deltaRank = { Ahead: 0, Benchmark: 1, Parity: 1, Mixed: 2, Inconclusive: 3, Behind: 4, Unavailable: 5 };
  rows.sort(
    (a, b) =>
      statusRank[a.absolute_status] - statusRank[b.absolute_status] ||
      (deltaRank[a.benchmark_delta?.is_benchmark ? 'Benchmark' : a.benchmark_delta?.summary ?? 'Unavailable'] ?? 5) -
        (deltaRank[b.benchmark_delta?.is_benchmark ? 'Benchmark' : b.benchmark_delta?.summary ?? 'Unavailable'] ?? 5) ||
      (b.confidence.score ?? 0) - (a.confidence.score ?? 0),
  );
  return {
    scenario,
    revision,
    scenario_meta: meta,
    scenario_family: family,
    benchmark: {
      status: benchmark.status,
      agent_spec: benchmark.pin ? `${benchmark.pin.harness} · ${benchmark.pin.model}` : null,
      review_id: benchmark.row?.review_id ?? null,
    },
    representative_rule: `Representative = latest completed experiment on ${revision} with ≥${thresholds.minimum} scored runs (preferred ${thresholds.preferred}); weaker samples surface as Low Confidence, never silently.`,
    cohort: {
      size: rows.length,
      meets: rows.filter((r) => r.absolute_status === 'Meets Scenario Bar').length,
      below: rows.filter((r) => r.absolute_status === 'Below Scenario Bar').length,
      unavailable: rows.filter((r) => r.absolute_status === 'Unavailable').length,
      low_confidence: rows.filter((r) => ['Low', 'Very Low'].includes(r.confidence.band)).length,
    },
    freshness: rows.map((r) => r.representative.created_at_utc).sort().reverse()[0] ?? null,
    rows,
  };
}

function buildReview(row, board, inputsByExperiment, experimentsAll, revisionDiffs) {
  const inputs = inputsByExperiment.get(row.representative.dir) ?? [];
  const benchRow = board.rows.find((r) => r.review_id === board.benchmark.review_id) ?? null;
  const benchInputs = benchRow ? (inputsByExperiment.get(benchRow.representative.dir) ?? []) : [];
  const family = row.scenario_family;
  const thresholds = FAMILY_THRESHOLDS[family] ?? FAMILY_THRESHOLDS[DEFAULT_FAMILY];
  const diagnosis = diagnosisItems(row, inputs);
  const isBenchmark = Boolean(row.benchmark_delta?.is_benchmark);
  return {
    ...row,
    benchmark: {
      ...board.benchmark,
      agent_spec: benchRow?.agent_spec ?? board.benchmark.agent_spec,
      dimensions: benchRow && !isBenchmark ? benchRow.dimensions : null,
      confidence_band: benchRow?.confidence.band ?? null,
    },
    primary_strength: strengthLine(row, inputs),
    primary_weakness: weaknessLine(row, inputs),
    change_context: changeContext(row, experimentsAll, revisionDiffs),
    evidence: buildEvidence(row, inputs, !isBenchmark ? benchRow : null, benchInputs),
    diagnosis: {
      ...diagnosis,
      opportunities: buildRecommendations(row, inputs, thresholds),
    },
    run_consistency: runConsistency(inputs, rowFidelityContext(row)),
  };
}

/**
 * experiments: projected experiment records (with reviewInputs attached per run).
 * inputsByExperiment: Map experiment_id -> reviewInput[].
 */
export function deriveReview({ experiments, inputsByExperiment, revisionDiffs, config }) {
  const identities = new Map();
  for (const experiment of experiments) {
    if (!experiment.scenario || !experiment.revision) continue;
    const key = identityKey(experiment);
    const list = identities.get(key) ?? [];
    list.push(experiment);
    identities.set(key, list);
  }
  const rowsByScenarioRevision = new Map();
  for (const candidates of identities.values()) {
    const representative = selectRepresentative(candidates);
    if (!representative.experiment) continue;
    const row = buildRow(representative, inputsByExperiment);
    const key = `${row.scenario}|${row.revision}`;
    const list = rowsByScenarioRevision.get(key) ?? [];
    list.push(row);
    rowsByScenarioRevision.set(key, list);
  }
  const boards = [...rowsByScenarioRevision.entries()]
    .map(([key, rows]) => {
      const [scenario, revision] = key.split('|');
      return buildBoard(scenario, revision, rows, config, experiments);
    })
    .sort((a, b) => a.scenario.localeCompare(b.scenario) || b.revision.localeCompare(a.revision));
  const reviews = {};
  for (const board of boards) {
    for (const row of board.rows) {
      reviews[row.review_id] = buildReview(row, board, inputsByExperiment, experiments, revisionDiffs);
    }
  }
  return { boards, reviews };
}
