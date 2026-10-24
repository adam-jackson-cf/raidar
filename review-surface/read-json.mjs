// Reads a JSON data file; an absent or unparseable file yields `whenAbsent`
// (the surface starts empty until the data build has written it).
import fs from 'node:fs';

export function readJson(filePath, whenAbsent) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch {
    return whenAbsent;
  }
}
