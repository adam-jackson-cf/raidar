#!/usr/bin/env bun
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const APP_DIR = process.env.RAIDAR_APP_DIR || "/app";
const LOG_DIR = process.env.RAIDAR_LOG_DIR || "/logs/verifier";
const VISUAL_CONFIG_PATH = path.join(APP_DIR, ".raidar-visual-config.json");
const ODIFF_TOLERANCE = "0.03";
const DEFAULT_VISUAL_REGIONS = [
  {
    name: "hero",
    weight: 0.35,
    clip: { x: 0, y: 0, width: 1440, height: 320 },
  },
  {
    name: "features",
    weight: 0.45,
    clip: { x: 0, y: 320, width: 1440, height: 420 },
  },
  {
    name: "footer",
    weight: 0.2,
    clip: { x: 0, y: 740, width: 1440, height: 160 },
  },
];

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function readJson(jsonPath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(jsonPath, "utf8"));
  } catch {
    return fallback;
  }
}

function writeJson(jsonPath, payload) {
  fs.writeFileSync(jsonPath, JSON.stringify(payload, null, 2));
}

function runCommand(argv, cwd = APP_DIR) {
  const result = spawnSync(argv[0], argv.slice(1), {
    cwd,
    encoding: "utf8",
    env: process.env,
  });
  return {
    command: argv.join(" "),
    exit_code: typeof result.status === "number" ? result.status : -1,
    stdout: result.stdout || "",
    stderr: result.stderr || "",
  };
}

function runOdiffSimilarity(referencePath, actualPath, diffPath) {
  const odiff = runCommand([
    "bunx",
    "odiff",
    referencePath,
    actualPath,
    diffPath,
    "--threshold",
    ODIFF_TOLERANCE,
  ]);
  const odiffOutput = `${odiff.stdout}\\n${odiff.stderr}`;
  if (odiff.exit_code === 0) {
    return {
      similarity: 1,
      diff_path: fs.existsSync(diffPath) ? diffPath : null,
      raw_output: odiffOutput.trim(),
      exit_code: odiff.exit_code,
    };
  }
  const match = odiffOutput.match(/([0-9]+(?:\.[0-9]+)?)\s*%/);
  if (match) {
    return {
      similarity: Math.max(0, 1 - Number.parseFloat(match[1]) / 100),
      diff_path: fs.existsSync(diffPath) ? diffPath : null,
      raw_output: odiffOutput.trim(),
      exit_code: odiff.exit_code,
    };
  }
  return {
    similarity: 0,
    diff_path: fs.existsSync(diffPath) ? diffPath : null,
    raw_output: odiffOutput.trim(),
    exit_code: odiff.exit_code,
  };
}

function clamp01(value) {
  return Math.max(0, Math.min(1, value));
}

function normalizeWeightRegions(regions) {
  const total = regions.reduce((sum, region) => sum + region.weight, 0);
  if (total <= 0) {
    return regions.map((region) => ({ ...region, normalized_weight: 0 }));
  }
  return regions.map((region) => ({
    ...region,
    normalized_weight: region.weight / total,
  }));
}

function scoringComponent(value, band, gamma) {
  if (!band || typeof band.lower !== "number" || typeof band.upper !== "number") {
    return 0;
  }
  const normalized = clamp01(
    (value - band.lower) / Math.max(1e-9, band.upper - band.lower),
  );
  return normalized ** gamma;
}

function visualScoreV2({ globalSimilarity, regionalSimilarity, worstRegionSimilarity, regionPassRate, scoring }) {
  const weights = scoring?.weights || {};
  const bands = scoring?.bands || {};
  const gamma =
    typeof scoring?.gamma === "number" && Number.isFinite(scoring.gamma)
      ? scoring.gamma
      : 2;
  return (
    100 *
    (scoringComponent(globalSimilarity, bands.global, gamma) *
      (weights.global ?? 0) +
      scoringComponent(regionalSimilarity, bands.regional, gamma) *
        (weights.regional ?? 0) +
      scoringComponent(worstRegionSimilarity, bands.worst_region, gamma) *
        (weights.worst_region ?? 0) +
      clamp01(regionPassRate) * (weights.region_pass_rate ?? 0))
  );
}

function visualPassPolicyOutcome({
  globalSimilarity,
  worstRegionSimilarity,
  regionPassRate,
  scoreV2,
  passPolicy,
}) {
  const failGlobal = passPolicy?.fail_if_global_below ?? 0.9;
  const failWorst = passPolicy?.fail_if_worst_region_below ?? 0.85;
  if (globalSimilarity < failGlobal || worstRegionSimilarity < failWorst) {
    return { passed: false, tier: "failed" };
  }
  const passed =
    scoreV2 >= (passPolicy?.minimum_score ?? 70) &&
    regionPassRate >= (passPolicy?.minimum_region_pass_rate ?? 0.75) &&
    worstRegionSimilarity >= (passPolicy?.minimum_worst_region ?? 0.88);
  if (!passed) {
    return { passed: false, tier: "failed" };
  }
  const highFidelity =
    scoreV2 >= (passPolicy?.high_fidelity_score ?? 85) &&
    globalSimilarity >= (passPolicy?.high_fidelity_global ?? 0.95) &&
    worstRegionSimilarity >= (passPolicy?.high_fidelity_worst_region ?? 0.92);
  return {
    passed: true,
    tier: highFidelity ? "high_fidelity" : "passed",
  };
}

function resolveVisualRegions(visualSpec) {
  const configuredRegions =
    Array.isArray(visualSpec?.regions) && visualSpec.regions.length > 0
      ? visualSpec.regions
      : DEFAULT_VISUAL_REGIONS;
  const validRegions = configuredRegions.filter(
    (region) =>
      typeof region?.name === "string" &&
      region.name.length > 0 &&
      typeof region?.weight === "number" &&
      region.weight > 0,
  );
  return normalizeWeightRegions(validRegions);
}

function regionEvidenceStatus(expectedCount, availableCount) {
  if (expectedCount === 0) {
    return "not_configured";
  }
  if (availableCount === 0) {
    return "missing";
  }
  if (availableCount < expectedCount) {
    return "partial";
  }
  return "present";
}

function walkFiles(rootDir) {
  const queue = [rootDir];
  const files = [];
  while (queue.length > 0) {
    const current = queue.pop();
    const entries = fs.readdirSync(current, { withFileTypes: true });
    for (const entry of entries) {
      const entryPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        queue.push(entryPath);
      } else {
        files.push(entryPath);
      }
    }
  }
  return files;
}

function collectSourceFiles() {
  const srcDir = path.join(APP_DIR, "src");
  if (!fs.existsSync(srcDir)) return [];
  return walkFiles(srcDir)
    .filter((file) => file.endsWith(".ts") || file.endsWith(".tsx"))
    .map((file) => ({
      path: path.relative(APP_DIR, file),
      content: fs.readFileSync(file, "utf8"),
    }));
}

function collectTestSources() {
  const sourceFiles = collectSourceFiles();
  const testPattern = /\.(test|spec)\.tsx?$/;
  return sourceFiles
    .filter((sourceFile) => testPattern.test(sourceFile.path))
    .map((sourceFile) => sourceFile.content);
}

function globToRegex(pattern) {
  let regex = "^";
  for (let idx = 0; idx < pattern.length; ) {
    const char = pattern[idx];
    const next = pattern[idx + 1];
    const following = pattern[idx + 2];
    if (char === "*" && next === "*" && following === "/") {
      regex += "(?:.*/)?";
      idx += 3;
      continue;
    }
    if (char === "*" && next === "*") {
      regex += ".*";
      idx += 2;
      continue;
    }
    if (char === "*") {
      regex += "[^/]*";
      idx += 1;
      continue;
    }
    if (char === "?") {
      regex += "[^/]";
      idx += 1;
      continue;
    }
    regex += /[\\^$+?.()|[\]{}]/.test(char) ? `\\${char}` : char;
    idx += 1;
  }
  regex += "$";
  return new RegExp(regex);
}

function filesMatchingPattern(pattern) {
  const matcher = globToRegex(pattern);
  const allFiles = walkFiles(APP_DIR).map((file) =>
    path.relative(APP_DIR, file),
  );
  return allFiles.filter((file) => matcher.test(file));
}

function fileExistsByPattern(pattern) {
  return filesMatchingPattern(pattern).length > 0;
}

function runDeterministicCheck(check, sourceFiles) {
  if (check.type === "import_present") {
    const match = sourceFiles.find((sourceFile) =>
      sourceFile.content.includes(check.pattern),
    );
    return {
      rule: check.description,
      type: "deterministic",
      passed: Boolean(match),
      evidence: match
        ? `Found in ${match.path}`
        : `Pattern '${check.pattern}' not found in any source file`,
    };
  }

  if (check.type === "no_pattern") {
    let regex;
    try {
      regex = new RegExp(check.pattern);
    } catch {
      return {
        rule: check.description,
        type: "deterministic",
        passed: false,
        evidence: `Invalid regex pattern '${check.pattern}'`,
      };
    }
    const match = sourceFiles.find((sourceFile) =>
      regex.test(sourceFile.content),
    );
    return {
      rule: check.description,
      type: "deterministic",
      passed: !match,
      evidence: match
        ? `Pattern found in ${match.path}`
        : "Pattern not found (good)",
    };
  }

  if (check.type === "file_exists") {
    const passed = fileExistsByPattern(check.pattern);
    return {
      rule: check.description,
      type: "deterministic",
      passed,
      evidence: passed
        ? `Found files matching '${check.pattern}'`
        : `No files matching '${check.pattern}'`,
    };
  }

  return {
    rule: check.description,
    type: "deterministic",
    passed: false,
    evidence: `Unknown deterministic check type '${check.type}'`,
  };
}

function parseTestCounts(output) {
  const passValues = [];
  const failValues = [];
  for (const match of output.matchAll(/(\d+)\s+passed/gi)) {
    passValues.push(Number.parseInt(match[1], 10));
  }
  for (const match of output.matchAll(/(\d+)\s+pass/gi)) {
    passValues.push(Number.parseInt(match[1], 10));
  }
  for (const match of output.matchAll(/(\d+)\s+failed/gi)) {
    failValues.push(Number.parseInt(match[1], 10));
  }
  for (const match of output.matchAll(/(\d+)\s+fail/gi)) {
    failValues.push(Number.parseInt(match[1], 10));
  }
  const passed = passValues.length > 0 ? Math.max(...passValues) : 0;
  const failed = failValues.length > 0 ? Math.max(...failValues) : 0;
  return { passed, total: passed + failed };
}

function parseCoveragePercent(output) {
  const values = [];
  const patterns = [
    /Lines\s*:\s*([0-9]+(?:\.[0-9]+)?)%/gi,
    /Statements\s*:\s*([0-9]+(?:\.[0-9]+)?)%/gi,
    /Functions\s*:\s*([0-9]+(?:\.[0-9]+)?)%/gi,
    /Branches\s*:\s*([0-9]+(?:\.[0-9]+)?)%/gi,
  ];
  for (const pattern of patterns) {
    for (const match of output.matchAll(pattern)) {
      values.push(Number.parseFloat(match[1]));
    }
  }
  const tableRegex = [
    "All files\\\\s*\\\\|\\\\s*([0-9]+(?:\\\\.[0-9]+)?)\\\\s*\\\\|\\\\s*",
    "([0-9]+(?:\\\\.[0-9]+)?)\\\\s*\\\\|\\\\s*([0-9]+(?:\\\\.[0-9]+)?)",
    "\\\\s*\\\\|\\\\s*([0-9]+(?:\\\\.[0-9]+)?)",
  ].join("");
  const table = output.match(new RegExp(tableRegex, "i"));
  if (table) {
    for (let idx = 1; idx < table.length; idx += 1) {
      values.push(Number.parseFloat(table[idx]));
    }
  }
  if (values.length === 0) return null;
  return Math.min(...values) / 100;
}

function coverageFromSummary() {
  const summaryPath = path.join(APP_DIR, "coverage", "coverage-summary.json");
  if (!fs.existsSync(summaryPath)) return { measured: null, source: null };
  const payload = readJson(summaryPath, {});
  const total = payload.total || {};
  const values = [];
  for (const key of ["lines", "statements", "functions", "branches"]) {
    const pct = total?.[key]?.pct;
    if (typeof pct === "number") values.push(pct);
  }
  if (values.length === 0) return { measured: null, source: null };
  return { measured: Math.min(...values) / 100, source: summaryPath };
}

function scoreAcceptance(checks) {
  if (checks.length === 0) return 1;
  const passed = checks.filter((check) => check.passed).length;
  return passed / checks.length;
}

function scoreVerificationStability(verificationStability) {
  const basePenalty = verificationStability.total_gate_failures / 4;
  const repeatPenalty = verificationStability.repeat_failures * 0.2;
  return Math.max(0, Math.min(1, 1 - basePenalty - repeatPenalty));
}

function qualityScore({
  functional,
  acceptanceScore,
  visual,
  verificationStabilityScore,
  weights,
}) {
  if (visual) {
    return (
      functional.score * weights.functional +
      acceptanceScore * weights.acceptance +
      visual.score * weights.visual +
      verificationStabilityScore * weights.verification_stability
    );
  }
  const nonVisualTotal =
    weights.functional + weights.acceptance + weights.verification_stability;
  return (
    functional.score * (weights.functional / nonVisualTotal) +
    acceptanceScore * (weights.acceptance / nonVisualTotal) +
    verificationStabilityScore *
      (weights.verification_stability / nonVisualTotal)
  );
}

function starterIntegrityCheck(scenarioSpec) {
  const packagePath = path.join(APP_DIR, "package.json");
  if (!fs.existsSync(packagePath)) {
    return {
      name: "stack_integrity",
      passed: false,
      evidence: "Missing package.json in workspace.",
    };
  }
  const payload = readJson(packagePath, {});
  const scripts = payload.scripts || {};
  for (const scriptName of ["typecheck", "lint", "test"]) {
    if (
      (scripts[scriptName] || "") !==
      (scenarioSpec.baseline_scripts?.[scriptName] || "")
    ) {
      return {
        name: "stack_integrity",
        passed: false,
        evidence: `Script mismatch for '${scriptName}'.`,
      };
    }
  }
  if (!fs.existsSync(path.join(APP_DIR, "bun.lock"))) {
    return {
      name: "stack_integrity",
      passed: false,
      evidence: "Missing bun.lock lockfile.",
    };
  }
  for (const lockName of ["package-lock.json", "pnpm-lock.yaml", "yarn.lock"]) {
    if (fs.existsSync(path.join(APP_DIR, lockName))) {
      return {
        name: "stack_integrity",
        passed: false,
        evidence: `Unexpected lockfile present: ${lockName}`,
      };
    }
  }
  return {
    name: "stack_integrity",
    passed: true,
    evidence: "Starter scripts and package-manager integrity preserved.",
  };
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function testEvidenceLabel(evidence) {
  if (evidence?.type === "query_role") {
    const parts = [evidence.role];
    if (evidence.level !== undefined) {
      parts.push(`level=${evidence.level}`);
    }
    if (evidence.name) {
      parts.push(`name=${evidence.name}`);
    }
    return `query_role:${parts.join(",")} x${evidence.min_count || 1}`;
  }
  if (evidence?.type === "query_text") {
    return `query_text:${evidence.pattern} x${evidence.min_count || 1}`;
  }
  return String(evidence?.type || "unknown");
}

function countRoleQueryMatches(testSources, evidence) {
  const role = escapeRegExp(evidence?.role || "");
  if (!role) {
    return 0;
  }
  const queryPattern = new RegExp(
    String.raw`(?:screen\.)?(?:get|find|query)(?:All)?ByRole\s*\(\s*(['"])${role}\1(?<options>\s*,\s*\{[\s\S]*?\})?`,
    "gmi",
  );
  let count = 0;
  for (const source of testSources) {
    for (const match of source.matchAll(queryPattern)) {
      const options = match.groups?.options || "";
      if (
        evidence.level !== undefined &&
        !new RegExp(String.raw`level\s*:\s*${evidence.level}\b`, "mi").test(options)
      ) {
        continue;
      }
      if (
        evidence.name &&
        !new RegExp(escapeRegExp(evidence.name), "mi").test(options)
      ) {
        continue;
      }
      count += 1;
    }
  }
  return count;
}

function countTextQueryMatches(testSources, evidence) {
  const pattern = evidence?.pattern;
  if (!pattern) {
    return 0;
  }
  const byTextPattern = /(?:screen\.)?(?:get|find|query)(?:All)?ByText\s*\(/mi;
  const matchPattern = new RegExp(pattern, "gmi");
  let count = 0;
  for (const source of testSources) {
    if (!byTextPattern.test(source)) {
      continue;
    }
    count += [...source.matchAll(matchPattern)].length;
  }
  return count;
}

function missingTestEvidence(requiredEvidence, testSources) {
  const missing = [];
  for (const evidence of requiredEvidence || []) {
    const minCount = Number(evidence?.min_count || 1);
    let matched = 0;
    if (evidence?.type === "query_role") {
      matched = countRoleQueryMatches(testSources, evidence);
    } else if (evidence?.type === "query_text") {
      matched = countTextQueryMatches(testSources, evidence);
    }
    if (matched < minCount) {
      missing.push(testEvidenceLabel(evidence));
    }
  }
  return missing;
}

function checkRequirementMappings(requirements, testSources) {
  const missingRequirementIds = [];
  const requirementGapIds = [];
  const requirementTestEvidenceGaps = {};
  let satisfied = 0;
  let mapped = 0;
  let mappedSatisfied = 0;

  for (const requirement of requirements) {
    const result = runDeterministicCheck(
      requirement.check,
      collectSourceFiles(),
    );
    if (result.passed) {
      satisfied += 1;
    } else {
      missingRequirementIds.push(requirement.id);
    }

    const missingEvidence = missingTestEvidence(
      requirement.required_test_evidence || [],
      testSources,
    );
    const mappedForRequirement = missingEvidence.length === 0;
    if (mappedForRequirement) {
      mapped += 1;
      if (result.passed) {
        mappedSatisfied += 1;
      }
    }
    if (missingEvidence.length > 0) {
      requirementGapIds.push(requirement.id);
      requirementTestEvidenceGaps[requirement.id] = missingEvidence;
    }
  }

  const total = requirements.length;
  return {
    total_requirements: total,
    satisfied_requirements: satisfied,
    mapped_requirements: mapped,
    mapped_satisfied_requirements: mappedSatisfied,
    missing_requirement_ids: missingRequirementIds,
    requirement_gap_ids: requirementGapIds,
    requirement_test_evidence_gaps: requirementTestEvidenceGaps,
    presence_ratio: total === 0 ? 1 : satisfied / total,
    mapping_ratio: total === 0 ? 1 : mapped / total,
  };
}

function evaluateArtifactChecks(metricSpec) {
  const requiredPaths = metricSpec?.config?.required_paths || [];
  if (!Array.isArray(requiredPaths) || requiredPaths.length === 0) {
    return {
      metric_id: metricSpec?.id || "artifact-checks",
      passed: false,
      matched_count: 0,
      missing_patterns: [],
      evidence: "artifact-checks metric missing required_paths configuration.",
    };
  }
  const missingPatterns = [];
  let matchedCount = 0;
  const evidenceParts = [];
  for (const pattern of requiredPaths) {
    const matches = filesMatchingPattern(pattern);
    matchedCount += matches.length;
    if (matches.length === 0) {
      missingPatterns.push(pattern);
      evidenceParts.push(`${pattern}:0`);
    } else {
      evidenceParts.push(`${pattern}:${matches.length}`);
    }
  }
  return {
    metric_id: metricSpec?.id || "artifact-checks",
    passed: missingPatterns.length === 0,
    matched_count: matchedCount,
    missing_patterns: missingPatterns,
    evidence: `artifact-checks matches (${evidenceParts.join(", ")})`,
  };
}

function evaluateMetricResults(metrics) {
  const results = [];
  for (const metricSpec of metrics) {
    if (metricSpec?.type === "core") {
      continue;
    }
    if (metricSpec?.type === "artifact-checks") {
      results.push(evaluateArtifactChecks(metricSpec));
      continue;
    }
    results.push({
      metric_id: metricSpec?.id || "unknown-metric",
      passed: false,
      matched_count: 0,
      missing_patterns: [],
      evidence: `Unsupported metric type '${metricSpec?.type}'`,
    });
  }
  return results;
}

function buildPerformanceGateChecks({
  gateHistory,
  functional,
  coverage,
  visual,
  requirements,
  quality,
  minQuality,
}) {
  const checks = [];
  const allGatesPassed = gateHistory.every((event) => event.exit_code === 0);
  checks.push({
    name: "quality_gates_passed",
    passed: allGatesPassed,
    evidence:
      `${gateHistory.filter((event) => event.exit_code === 0).length}` +
      `/${gateHistory.length} gates passed.`,
  });
  checks.push({
    name: "functional_passed",
    passed: functional.passed,
    evidence:
      `build=${functional.build_succeeded}, ` +
      `tests=${functional.tests_passed}/${functional.tests_total}`,
  });
  checks.push({
    name: "coverage_threshold_met",
    passed: coverage.passed,
    evidence:
      `threshold=${coverage.threshold}, ` +
      `measured=${coverage.measured}, source=${coverage.source}`,
  });
  checks.push({
    name: "visual_passed",
    passed: visual ? visual.capture_succeeded && visual.passed === true : true,
    evidence: visual
      ? `captured=${visual.capture_succeeded}, ` +
        `global_similarity=${visual.global_similarity}, ` +
        `regional_similarity=${visual.regional_similarity}, ` +
        `worst_region_similarity=${visual.worst_region_similarity}, ` +
        `region_decent_pass_rate=${visual.region_decent_pass_rate}, ` +
        `policy_score=${visual.policy_score}, passed=${visual.passed}, fidelity_tier=${visual.fidelity_tier}`
      : "Visual threshold not configured.",
  });
  checks.push({
    name: "all_requirements_present",
    passed: requirements.presence_ratio >= 1,
    evidence:
      `satisfied=${requirements.satisfied_requirements}/${requirements.total_requirements}, ` +
      `missing=${JSON.stringify(requirements.missing_requirement_ids)}`,
  });
  checks.push({
    name: "requirement_test_gaps",
    passed:
      requirements.satisfied_requirements === 0 ||
      requirements.mapped_satisfied_requirements >=
        requirements.satisfied_requirements,
    evidence:
      `mapped_satisfied=${requirements.mapped_satisfied_requirements}/` +
      `${requirements.satisfied_requirements}, ` +
      `mapped_total=${requirements.mapped_requirements}/${requirements.total_requirements}, ` +
      `gaps=${JSON.stringify(requirements.requirement_gap_ids)}, ` +
      `evidence_gaps=${JSON.stringify(requirements.requirement_test_evidence_gaps)}`,
  });
  checks.push({
    name: "minimum_quality_score",
    passed: quality >= minQuality,
    evidence: `quality=${quality.toFixed(3)}, min=${minQuality.toFixed(3)}`,
  });
  return checks;
}

function buildExecutionValidityChecks(stackIntegrity) {
  return [
    {
      name: "run_completed",
      passed: true,
      evidence: "Run completed without early termination.",
    },
    stackIntegrity,
  ];
}

function main() {
  const scenarioSpecPath = process.argv[2];
  if (!scenarioSpecPath || !fs.existsSync(scenarioSpecPath)) {
    throw new Error("Missing scenario specification for verifier scoring.");
  }
  ensureDir(LOG_DIR);
  const scenarioSpec = readJson(scenarioSpecPath, {});
  const metricDefinitions = Array.isArray(scenarioSpec.metrics)
    ? scenarioSpec.metrics
    : [];
  const sourceFiles = collectSourceFiles();
  const deterministicChecks =
    scenarioSpec.acceptance?.deterministic_checks || [];
  const acceptanceChecks = deterministicChecks.map((check) =>
    runDeterministicCheck(check, sourceFiles),
  );
  const gateHistory = [];
  let gateFailures = 0;

  for (const gate of scenarioSpec.verification?.gates || []) {
    const result = runCommand(gate.command || []);
    gateHistory.push({
      timestamp: new Date().toISOString(),
      gate_name: gate.name || "gate",
      command: (gate.command || []).join(" "),
      exit_code: result.exit_code,
      stdout: result.stdout,
      stderr: result.stderr,
      failure_category: null,
      is_repeat: false,
    });
    if (result.exit_code !== 0) {
      gateFailures += 1;
      if (gate.on_failure === "terminate") break;
      if (
        gateFailures >=
        Number.parseInt(
          String(scenarioSpec.verification?.max_gate_failures || "3"),
          10,
        )
      ) {
        break;
      }
    }
  }

  const buildResult = runCommand(["bun", "run", "build"]);
  const testResult = runCommand(["bun", "run", "test"]);
  const testOutput = `${testResult.stdout}\\n${testResult.stderr}`;
  const testCounts = parseTestCounts(testOutput);
  const noTests = /No tests found|No test files found/i.test(testOutput);
  const testsPassedAll =
    testCounts.total === 0
      ? noTests
      : testResult.exit_code === 0 && testCounts.passed === testCounts.total;
  const functional = {
    passed: buildResult.exit_code === 0 && testsPassedAll,
    tests_passed: testCounts.passed,
    tests_total: testCounts.total,
    build_succeeded: buildResult.exit_code === 0,
    gates_passed: gateHistory.filter((event) => event.exit_code === 0).length,
    gates_total: gateHistory.length,
  };

  const testSources = collectTestSources();
  const requirementsCoverage = checkRequirementMappings(
    scenarioSpec.acceptance?.requirements || [],
    testSources,
  );

  const coverageFromFile = coverageFromSummary();
  let coverageMeasured = coverageFromFile.measured;
  let coverageSource = coverageFromFile.source;
  if (coverageMeasured === null) {
    for (let idx = gateHistory.length - 1; idx >= 0; idx -= 1) {
      const event = gateHistory[idx];
      const gateText = `${event.gate_name} ${event.command}`.toLowerCase();
      if (!gateText.includes("coverage")) continue;
      const parsed = parseCoveragePercent(`${event.stdout}\\n${event.stderr}`);
      if (parsed !== null) {
        coverageMeasured = parsed;
        coverageSource = `gate:${event.gate_name}`;
        break;
      }
    }
  }
  const coverageThreshold =
    scenarioSpec.verification?.coverage_threshold ?? null;
  const testCoverage = {
    threshold: coverageThreshold,
    measured: coverageMeasured,
    source: coverageSource,
    passed:
      coverageThreshold === null ||
      (coverageMeasured !== null && coverageMeasured >= coverageThreshold),
  };

  let visual = null;
  if (scenarioSpec.visual) {
    writeJson(VISUAL_CONFIG_PATH, {
      viewport: scenarioSpec.visual.viewport || null,
      regions: scenarioSpec.visual.regions || [],
      scoring: scenarioSpec.visual.scoring || null,
      pass_policy: scenarioSpec.visual.pass_policy || null,
      reference_image: scenarioSpec.visual.reference_image || null,
    });
    const screenshot = runCommand(scenarioSpec.visual.screenshot_command || []);
    const actualPath = path.join(APP_DIR, "actual.png");
    const diffPath = path.join(APP_DIR, "diff.png");
    const referencePath = path.isAbsolute(scenarioSpec.visual.reference_image)
      ? scenarioSpec.visual.reference_image
      : path.join(APP_DIR, scenarioSpec.visual.reference_image);
    const captureSucceeded =
      screenshot.exit_code === 0 && fs.existsSync(actualPath);
    let similarity = 0;
    let globalSimilarity = 0;
    let regionalSimilarity = null;
    let worstRegionSimilarity = null;
    let regionPassRate = 1;
    let scoreV2 = 0;
    let passV2 = false;
    let tierV2 = "failed";
    let diffOutput = null;
    const regionalScores = [];
    const visualRegions = resolveVisualRegions(scenarioSpec.visual);
    const expectedRegionCount = visualRegions.length;
    const captureOutput = `${screenshot.stdout}\\n${screenshot.stderr}`.trim();
    let captureError = null;
    if (!captureSucceeded) {
      captureError = captureOutput || `exit_code=${screenshot.exit_code}`;
    }
    if (captureSucceeded) {
      if (fs.existsSync(referencePath)) {
        const globalCompare = runOdiffSimilarity(
          referencePath,
          actualPath,
          diffPath,
        );
        globalSimilarity = globalCompare.similarity;
        diffOutput = globalCompare.diff_path;

        const referenceExt = path.extname(referencePath);
        const referenceStem = path.basename(referencePath, referenceExt);
        const referenceDir = path.dirname(referencePath);
        let weightedRegionalSum = 0;

        for (const region of visualRegions) {
          const actualRegionPath = path.join(
            APP_DIR,
            `actual-region-${region.name}.png`,
          );
          const referenceRegionPath = path.join(
            referenceDir,
            `${referenceStem}-region-${region.name}${referenceExt}`,
          );
          const regionDiffPath = path.join(
            APP_DIR,
            `diff-region-${region.name}.png`,
          );
          if (
            !fs.existsSync(actualRegionPath) ||
            !fs.existsSync(referenceRegionPath)
          ) {
            continue;
          }
          const regionCompare = runOdiffSimilarity(
            referenceRegionPath,
            actualRegionPath,
            regionDiffPath,
          );
          regionalScores.push({
            name: region.name,
            weight: region.weight,
            normalized_weight: region.normalized_weight,
            similarity: regionCompare.similarity,
            decent_pass:
              regionCompare.similarity >=
              (scenarioSpec.visual.scoring?.region_pass_threshold ?? 0.9),
            actual_path: actualRegionPath,
            reference_path: referenceRegionPath,
            diff_path: regionCompare.diff_path,
            odiff_exit_code: regionCompare.exit_code,
          });
          weightedRegionalSum +=
            region.normalized_weight * regionCompare.similarity;
        }

        if (regionalScores.length > 0) {
          regionalSimilarity = weightedRegionalSum;
          worstRegionSimilarity = regionalScores.reduce((worst, current) =>
            current.similarity < worst.similarity ? current : worst,
          ).similarity;
        } else {
          regionalSimilarity = globalSimilarity;
          worstRegionSimilarity = globalSimilarity;
        }
        const regionPassThreshold =
          scenarioSpec.visual.scoring?.region_pass_threshold ?? 0.9;
        const passingRegions = regionalScores.filter(
          (region) => region.similarity >= regionPassThreshold,
        ).length;
        regionPassRate =
          expectedRegionCount === 0 ? 1 : passingRegions / expectedRegionCount;
        scoreV2 = visualScoreV2({
          globalSimilarity,
          regionalSimilarity: regionalSimilarity ?? globalSimilarity,
          worstRegionSimilarity: worstRegionSimilarity ?? globalSimilarity,
          regionPassRate,
          scoring: scenarioSpec.visual.scoring,
        });
        const policyOutcome = visualPassPolicyOutcome({
          globalSimilarity,
          worstRegionSimilarity: worstRegionSimilarity ?? globalSimilarity,
          regionPassRate,
          scoreV2,
          passPolicy: scenarioSpec.visual.pass_policy,
        });
        passV2 = policyOutcome.passed;
        tierV2 = policyOutcome.tier;
        similarity = clamp01(scoreV2 / 100);
      }
    }
    const availableRegionCount = regionalScores.length;
    const evidenceStatus = regionEvidenceStatus(
      expectedRegionCount,
      availableRegionCount,
    );
    visual = {
      similarity,
      global_similarity: globalSimilarity,
      regional_similarity: regionalSimilarity,
      worst_region_similarity: worstRegionSimilarity,
      contract_version: "oracle",
      region_decent_pass_rate: regionPassRate,
      policy_score: scoreV2,
      passed: passV2,
      fidelity_tier: tierV2,
      expected_region_count: expectedRegionCount,
      available_region_count: availableRegionCount,
      region_evidence_status: evidenceStatus,
      actual_path: captureSucceeded ? actualPath : null,
      reference_path: fs.existsSync(referencePath) ? referencePath : null,
      regional_scores: regionalScores,
      diff_path: diffOutput,
      capture_succeeded: captureSucceeded,
      capture_error: captureError,
      odiff_tolerance: Number.parseFloat(ODIFF_TOLERANCE),
      score: similarity,
    };
  }

  const acceptanceScore = scoreAcceptance(acceptanceChecks);
  const failingGateNames = gateHistory
    .filter((event) => event.exit_code !== 0)
    .map((event) => event.gate_name);
  const repeats = Math.max(
    0,
    failingGateNames.length - new Set(failingGateNames).size,
  );
  const verificationStability = {
    total_gate_failures: gateFailures,
    unique_failure_categories: new Set(failingGateNames).size,
    repeat_failures: repeats,
    score: scoreVerificationStability({
      total_gate_failures: gateFailures,
      repeat_failures: repeats,
    }),
  };

  const stackIntegrity = starterIntegrityCheck(scenarioSpec);
  const quality = qualityScore({
    functional: { score: functional.passed ? 1 : 0 },
    acceptanceScore,
    visual: visual ? { score: visual.score } : null,
    verificationStabilityScore: verificationStability.score,
    weights: scenarioSpec.weights,
  });
  const performanceGateChecks = buildPerformanceGateChecks({
    gateHistory,
    functional,
    coverage: testCoverage,
    visual,
    requirements: requirementsCoverage,
    quality,
    minQuality: scenarioSpec.verification?.min_quality_score ?? 0,
  });
  const executionValidityChecks = buildExecutionValidityChecks(stackIntegrity);
  executionValidityChecks.push({
    name: "completion_claim_integrity",
    passed: true,
    evidence: "Validated post-run by orchestrator.",
  });
  const metricResults = evaluateMetricResults(metricDefinitions);

  const scorecard = {
    functional,
    acceptance: {
      checks: acceptanceChecks,
      score: acceptanceScore,
    },
    visual,
    verification_stability: verificationStability,
    test_coverage: testCoverage,
    requirements_coverage: requirementsCoverage,
    execution_validity: {
      checks: executionValidityChecks,
      passed: executionValidityChecks.every((check) => check.passed),
    },
    performance_gates: {
      checks: performanceGateChecks,
      passed: performanceGateChecks.every((check) => check.passed),
    },
    metric_results: metricResults,
    gate_history: gateHistory,
  };

  writeJson(path.join(LOG_DIR, "scorecard.json"), scorecard);
  writeJson(path.join(LOG_DIR, "gate-history.json"), gateHistory);
  writeJson(
    path.join(LOG_DIR, "execution-validity.json"),
    scorecard.execution_validity,
  );
  writeJson(
    path.join(LOG_DIR, "performance-gates.json"),
    scorecard.performance_gates,
  );
  const rewardValue = scorecard.execution_validity.passed ? quality : 0;
  fs.writeFileSync(path.join(LOG_DIR, "reward.txt"), `${rewardValue}`);
}

try {
  main();
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  ensureDir(LOG_DIR);
  writeJson(path.join(LOG_DIR, "scorecard.json"), {
    functional: {
      passed: false,
      tests_passed: 0,
      tests_total: 0,
      build_succeeded: false,
      gates_passed: 0,
      gates_total: 0,
    },
    acceptance: {
      checks: [
        {
          rule: "Verifier execution completed",
          type: "deterministic",
          passed: false,
          evidence: message,
        },
      ],
      score: 0,
    },
    visual: null,
    verification_stability: {
      total_gate_failures: 0,
      unique_failure_categories: 0,
      repeat_failures: 0,
      score: 0,
    },
    test_coverage: {
      threshold: null,
      measured: null,
      source: null,
      passed: false,
    },
    requirements_coverage: {
      total_requirements: 0,
      satisfied_requirements: 0,
      mapped_requirements: 0,
      mapped_satisfied_requirements: 0,
      missing_requirement_ids: [],
      requirement_gap_ids: [],
      requirement_test_evidence_gaps: {},
      presence_ratio: 0,
      mapping_ratio: 0,
    },
    execution_validity: {
      checks: [
        {
          name: "run_completed",
          passed: false,
          evidence: message,
        },
      ],
      passed: false,
    },
    performance_gates: {
      checks: [],
      passed: false,
    },
    metric_results: [],
    gate_history: [],
  });
  fs.writeFileSync(path.join(LOG_DIR, "reward.txt"), "0");
  console.error(message);
  process.exit(1);
}
