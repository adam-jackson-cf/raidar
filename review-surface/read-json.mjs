// Reads a JSON data file. A missing or unparseable file is an explicit error
// (`DataFileError`, carrying the HTTP status the API answers with): 404 when the
// data build has not written it, 500 when it is corrupt. Never empty data.
import fs from 'node:fs';

export class DataFileError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export function readJson(filePath) {
  let text;
  try {
    text = fs.readFileSync(filePath, 'utf8');
  } catch (error) {
    if (error.code === 'ENOENT') {
      throw new DataFileError(404, `data file not found: ${filePath}; run the review data build first`);
    }
    throw new DataFileError(500, `cannot read data file ${filePath}: ${error.message}`);
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new DataFileError(500, `data file ${filePath} is not valid JSON: ${error.message}`);
  }
}

// User-written state (annotations, labels) legitimately does not exist until the
// first write; only then is "absent" the empty list. A corrupt file is still an error.
export function readUserState(filePath) {
  return fs.existsSync(filePath) ? readJson(filePath) : [];
}


// A run-scoped file that is absent means "no such run" (null); a corrupt one still throws.
export function readRunJson(filePath) {
  try {
    return readJson(filePath);
  } catch (error) {
    if (error instanceof DataFileError && error.status === 404) return null;
    throw error;
  }
}
