#!/usr/bin/env bun
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const APP_DIR = "/app";
const LOG_DIR = "/logs/verifier";
const ODIFF_TOLERANCE = "0.1";

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
  const escaped = pattern.replace(/[.+^${}()|[\\]\\\\]/g, "\\\\$&");
  const regex = escaped
    .replaceAll("**", "###DOUBLESTAR###")
    .replaceAll("*", "[^/]*")
    .replaceAll("###DOUBLESTAR###", ".*");
  return new RegExp(`^${regex}$`);
}

function fileExistsByPattern(pattern) {
  const matcher = globToRegex(pattern);
  const allFiles = walkFiles(APP_DIR).map((file) => path.relative(APP_DIR, file));
  return allFiles.some((file) => matcher.test(file));
}

function runDeterministicCheck(check, sourceFiles) {
  if (check.type === "import_present") {
    const match = sourceFiles.find((sourceFile) => sourceFile.content.includes(check.pattern));
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
    const match = sourceFiles.find((sourceFile) => regex.test(sourceFile.content));
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

function scoreCompliance(checks) {
  if (checks.length === 0) return 1;
  const passed = checks.filter((check) => check.passed).length;
  return passed / checks.length;
}

function scoreEfficiency(efficiency) {
  const basePenalty = efficiency.total_gate_failures / 4;
  const repeatPenalty = efficiency.repeat_failures * 0.2;
  return Math.max(0, Math.min(1, 1 - basePenalty - repeatPenalty));
}

function qualityScore({ functional, complianceScore, visual, efficiencyScore, weights }) {
  if (visual) {
    return (
      functional.score * weights.functional +
      complianceScore * weights.compliance +
      visual.score * weights.visual +
      efficiencyScore * weights.efficiency
    );
  }
  const nonVisualTotal = weights.functional + weights.compliance + weights.efficiency;
  return (
    functional.score * (weights.functional / nonVisualTotal) +
    complianceScore * (weights.compliance / nonVisualTotal) +
    efficiencyScore * (weights.efficiency / nonVisualTotal)
  );
}

function stackIntegrityCheck(taskSpec) {
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
    if ((scripts[scriptName] || "") !== (taskSpec.baseline_scripts?.[scriptName] || "")) {
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
    evidence: "Scaffold scripts and package-manager integrity preserved.",
  };
}

function checkRequirementMappings(requirements, testSources) {
  const missingRequirementIds = [];
  const requirementGapIds = [];
  const requirementPatternGaps = {};
  let satisfied = 0;
  let mapped = 0;
  let mappedSatisfied = 0;

  for (const requirement of requirements) {
    const result = runDeterministicCheck(requirement.check, collectSourceFiles());
    if (result.passed) {
      satisfied += 1;
    } else {
      missingRequirementIds.push(requirement.id);
    }

    const patterns = requirement.required_test_patterns || [];
    const missingPatterns = patterns.filter(
      (pattern) => !testSources.some((content) => new RegExp(pattern, "mi").test(content))
    );
    const mappedForRequirement = patterns.length > 0 && missingPatterns.length === 0;
    if (mappedForRequirement) {
      mapped += 1;
      if (result.passed) {
        mappedSatisfied += 1;
      }
    } else {
      requirementGapIds.push(requirement.id);
      if (missingPatterns.length > 0) {
        requirementPatternGaps[requirement.id] = missingPatterns;
      }
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
    requirement_pattern_gaps: requirementPatternGaps,
    presence_ratio: total === 0 ? 1 : satisfied / total,
    mapping_ratio: total === 0 ? 1 : mapped / total,
  };
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
  const visualPassed = visual
    ? visual.capture_succeeded && visual.threshold_met === true
    : true;
  checks.push({
    name: "visual_threshold_met",
    passed: visualPassed,
    evidence: visual
      ? (
          `captured=${visual.capture_succeeded}, ` +
          `similarity=${visual.similarity}, threshold=${visual.threshold}`
        )
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
    name: "no_requirement_test_gaps",
    passed:
      requirements.satisfied_requirements === 0 ||
      requirements.mapped_satisfied_requirements >= requirements.satisfied_requirements,
    evidence:
      `mapped_satisfied=${requirements.mapped_satisfied_requirements}/` +
      `${requirements.satisfied_requirements}, ` +
      `mapped_total=${requirements.mapped_requirements}/${requirements.total_requirements}, ` +
      `gaps=${JSON.stringify(requirements.requirement_gap_ids)}, ` +
      `pattern_gaps=${JSON.stringify(requirements.requirement_pattern_gaps)}`,
  });
  checks.push({
    name: "minimum_quality_score",
    passed: quality >= minQuality,
    evidence: `quality=${quality.toFixed(3)}, min=${minQuality.toFixed(3)}`,
  });
  return checks;
}

function buildRunValidityChecks(stackIntegrity) {
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
  const taskSpecPath = process.argv[2];
  if (!taskSpecPath || !fs.existsSync(taskSpecPath)) {
    throw new Error("Missing task specification for verifier scoring.");
  }
  ensureDir(LOG_DIR);
  const taskSpec = readJson(taskSpecPath, {});
  const sourceFiles = collectSourceFiles();
  const deterministicChecks = taskSpec.compliance?.deterministic_checks || [];
  const complianceChecks = deterministicChecks.map((check) =>
    runDeterministicCheck(check, sourceFiles)
  );
  const gateHistory = [];
  let gateFailures = 0;

  for (const gate of taskSpec.verification?.gates || []) {
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
        Number.parseInt(String(taskSpec.verification?.max_gate_failures || "3"), 10)
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
  const requirements = checkRequirementMappings(
    taskSpec.compliance?.requirements || [],
    testSources
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
  const coverageThreshold = taskSpec.verification?.coverage_threshold ?? null;
  const coverage = {
    threshold: coverageThreshold,
    measured: coverageMeasured,
    source: coverageSource,
    passed:
      coverageThreshold === null ||
      (coverageMeasured !== null && coverageMeasured >= coverageThreshold),
  };

  let visual = null;
  if (taskSpec.visual) {
    const screenshot = runCommand(taskSpec.visual.screenshot_command || []);
    const actualPath = path.join(APP_DIR, "actual.png");
    const diffPath = path.join(APP_DIR, "diff.png");
    const captureSucceeded =
      screenshot.exit_code === 0 && fs.existsSync(actualPath);
    let similarity = 0;
    let diffOutput = null;
    const captureOutput = `${screenshot.stdout}\\n${screenshot.stderr}`.trim();
    let captureError = null;
    if (!captureSucceeded) {
      captureError = captureOutput || `exit_code=${screenshot.exit_code}`;
    }
    if (captureSucceeded) {
      const referencePath = path.isAbsolute(taskSpec.visual.reference_image)
        ? taskSpec.visual.reference_image
        : path.join(APP_DIR, taskSpec.visual.reference_image);
      if (fs.existsSync(referencePath)) {
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
          similarity = 1;
        } else {
          const match = odiffOutput.match(/([0-9]+(?:\.[0-9]+)?)\s*%/);
          if (match) similarity = Math.max(0, 1 - Number.parseFloat(match[1]) / 100);
        }
        if (fs.existsSync(diffPath)) diffOutput = diffPath;
      }
    }
    const threshold = taskSpec.visual.threshold ?? null;
    visual = {
      similarity,
      diff_path: diffOutput,
      capture_succeeded: captureSucceeded,
      capture_error: captureError,
      threshold,
      score: similarity,
      threshold_met: threshold === null ? null : similarity >= threshold,
    };
  }

  const complianceScore = scoreCompliance(complianceChecks);
  const failingGateNames = gateHistory
    .filter((event) => event.exit_code !== 0)
    .map((event) => event.gate_name);
  const repeats = Math.max(0, failingGateNames.length - new Set(failingGateNames).size);
  const efficiency = {
    total_gate_failures: gateFailures,
    unique_failure_categories: new Set(failingGateNames).size,
    repeat_failures: repeats,
    score: scoreEfficiency({
      total_gate_failures: gateFailures,
      repeat_failures: repeats,
    }),
  };

  const stackIntegrity = stackIntegrityCheck(taskSpec);
  const quality = qualityScore({
    functional: { score: functional.passed ? 1 : 0 },
    complianceScore,
    visual: visual ? { score: visual.score } : null,
    efficiencyScore: efficiency.score,
    weights: taskSpec.weights,
  });
  const performanceGateChecks = buildPerformanceGateChecks({
    gateHistory,
    functional,
    coverage,
    visual,
    requirements,
    quality,
    minQuality: taskSpec.verification?.min_quality_score ?? 0,
  });
  const runValidityChecks = buildRunValidityChecks(stackIntegrity);
  runValidityChecks.push({
    name: "completion_claim_integrity",
    passed: true,
    evidence: "Validated post-run by orchestrator.",
  });

  const scorecard = {
    functional,
    compliance: {
      checks: complianceChecks,
      score: complianceScore,
    },
    visual,
    efficiency,
    coverage,
    requirements,
    run_validity: {
      checks: runValidityChecks,
      passed: runValidityChecks.every((check) => check.passed),
    },
    performance_gates: {
      checks: performanceGateChecks,
      passed: performanceGateChecks.every((check) => check.passed),
    },
    gate_history: gateHistory,
  };

  writeJson(path.join(LOG_DIR, "scorecard.json"), scorecard);
  writeJson(path.join(LOG_DIR, "gate-history.json"), gateHistory);
  writeJson(path.join(LOG_DIR, "run-validity.json"), scorecard.run_validity);
  writeJson(path.join(LOG_DIR, "performance-gates.json"), scorecard.performance_gates);
  const rewardValue = scorecard.run_validity.passed ? quality : 0;
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
    compliance: {
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
    efficiency: {
      total_gate_failures: 0,
      unique_failure_categories: 0,
      repeat_failures: 0,
      score: 0,
    },
    coverage: {
      threshold: null,
      measured: null,
      source: null,
      passed: false,
    },
    requirements: {
      total_requirements: 0,
      satisfied_requirements: 0,
      mapped_requirements: 0,
      mapped_satisfied_requirements: 0,
      missing_requirement_ids: [],
      requirement_gap_ids: [],
      requirement_pattern_gaps: {},
      presence_ratio: 0,
      mapping_ratio: 0,
    },
    run_validity: {
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
    gate_history: [],
  });
  fs.writeFileSync(path.join(LOG_DIR, "reward.txt"), "0");
  console.error(message);
  process.exit(1);
}
