/** Parse a JSON-ish payload string; returns undefined when not parseable. */
export function tryParseJson(s: string | null | undefined): unknown {
  if (!s) return undefined;
  try {
    return JSON.parse(s);
  } catch {
    return undefined;
  }
}
