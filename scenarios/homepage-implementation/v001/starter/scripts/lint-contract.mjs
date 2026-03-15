#!/usr/bin/env bun
import fs from "node:fs";
import path from "node:path";

const ROOT = process.cwd();
const SRC_DIR = path.join(ROOT, "src");
const GLOBALS_PATH = path.join(SRC_DIR, "app", "globals.css");
const REQUIRED_THEME_TOKENS = [
  "--background",
  "--foreground",
  "--primary",
  "--primary-foreground",
  "--muted",
  "--border",
];
const LOCATION_RULES = [
  {
    label: "app files",
    match: /^src\/app\/(?:.+\.(?:ts|tsx)|globals\.css)$/,
  },
  {
    label: "shared ui components",
    match: /^src\/components\/ui\/.+\.tsx$/,
  },
  {
    label: "page section components",
    match: /^src\/components\/sections\/.+\.tsx$/,
  },
  {
    label: "library files",
    match: /^src\/lib\/.+\.ts$/,
  },
  {
    label: "test files",
    match: /^src\/test\/.+\.(?:ts|tsx)$/,
  },
];

function walkFiles(rootDir) {
  if (!fs.existsSync(rootDir)) return [];
  const queue = [rootDir];
  const files = [];
  while (queue.length > 0) {
    const current = queue.pop();
    const entries = fs.readdirSync(current, { withFileTypes: true });
    for (const entry of entries) {
      const entryPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        queue.push(entryPath);
        continue;
      }
      files.push(entryPath);
    }
  }
  return files.sort();
}

function relativePath(filePath) {
  return path.relative(ROOT, filePath).split(path.sep).join("/");
}

const sourceFiles = walkFiles(SRC_DIR);
const errors = [];
let checkedSourceCount = 0;

for (const filePath of sourceFiles) {
  const relPath = relativePath(filePath);
  const basename = path.basename(filePath);
  if (basename.startsWith(".")) {
    continue;
  }
  if (!LOCATION_RULES.some((rule) => rule.match.test(relPath))) {
    errors.push(
      `${relPath}: unexpected source location or file type. Expected one of: ${LOCATION_RULES.map((rule) => rule.label).join(", ")}.`,
    );
  }
  if (relPath.endsWith(".module.css")) {
    errors.push(`${relPath}: CSS modules are not allowed for this scenario.`);
  }
  if (relPath.endsWith(".css") && relPath !== "src/app/globals.css") {
    errors.push(
      `${relPath}: only src/app/globals.css may define stylesheet rules.`,
    );
  }

  const content = fs.readFileSync(filePath, "utf8");
  if (content.includes("style={{")) {
    errors.push(
      `${relPath}: inline styles are forbidden; use shared theming and utility classes.`,
    );
  }
  if (
    (relPath.startsWith("src/app/") ||
      relPath.startsWith("src/components/sections/")) &&
    relPath.endsWith(".tsx")
  ) {
    checkedSourceCount += 1;
  }
}

if (!fs.existsSync(GLOBALS_PATH)) {
  errors.push("src/app/globals.css: missing required global theme file.");
} else {
  const globalsContent = fs.readFileSync(GLOBALS_PATH, "utf8");
  for (const token of REQUIRED_THEME_TOKENS) {
    if (!globalsContent.includes(token)) {
      errors.push(
        `src/app/globals.css: missing required theme token ${token}.`,
      );
    }
  }
}

if (errors.length > 0) {
  console.error("Homepage contract lint failed:");
  for (const error of errors) {
    console.error(`- ${error}`);
  }
  process.exit(1);
}

console.log(
  `Homepage contract lint passed (${checkedSourceCount} app/section sources checked).`,
);
