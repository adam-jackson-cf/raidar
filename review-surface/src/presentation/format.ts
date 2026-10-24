// Adapted from Raindrop Workshop (MIT) — app/src/utils/helpers.ts

/** Format a millisecond duration, e.g. 842ms / 4.2s / 2.2m. */
export function fmtDuration(ms: number | null | undefined): string {
  if (ms == null) return '—';
  if (ms < 1) return '<1ms';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

/** Format a token count, e.g. 980 / 42.0k / 1.2M. */
export function fmtTokens(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

/** Format a 0..1 score to three decimals; null-safe. */
export function fmtScore(v: number | null | undefined): string {
  if (v == null) return '—';
  return v.toFixed(3);
}

/** Format a 0..1 rate as a percentage. */
export function fmtPercent(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${Math.round(v * 100)}%`;
}
