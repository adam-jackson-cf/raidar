import { C } from '@/utils/colors';
import { scoreTier } from '@/utils/verdict';
import type { Cell, CellStatus, Component, Verdict } from '@/quality/types';

export const COMPONENT_INFO: Record<Component, { name: string; definition: string }> = {
  C: {
    name: 'Correctness',
    definition: 'Checkpoint: passed / graded oracle cases (strict, all groups). Headline row: solved checkpoints / K.',
  },
  V: { name: 'Verbosity', definition: '1 − lines flagged by Ruff (SCB SIM allowlist) or clones / production LOC.' },
  E: { name: 'Erosion', definition: '1 − cc·√sloc mass share of functions with cc > 10.' },
  H: { name: 'Architecture', definition: '1 − call-edge mass inside cycles / production call-edge mass.' },
  R: { name: 'Rework', definition: '1 − changed lines that re-modify earlier-changed symbols, on the transition into this checkpoint.' },
  G: { name: 'Regression', definition: '1 − changed symbols executed by failing regression tests, on the transition into this checkpoint.' },
  I: { name: 'Inertia', definition: 'Headline-policy weighted mean of V, E, H, R, G.' },
  S: { name: 'Score', definition: 'S = 100 · C · (0.70 + 0.30 · I), correctness-gated.' },
};

/** Short, stable badge text per non-ok status (never a zero). */
export const STATUS_BADGE: Record<Exclude<CellStatus, 'ok'>, string> = {
  not_applicable: 'n/a',
  ineligible: 'inelig',
  insufficient_evidence: 'thin',
  unsupported: 'unsup',
  unavailable: 'unavail',
  error: 'error',
  unassessed: 'unscored',
  truncated: 'trunc',
};

export const POLICY_SOURCE_TITLE: Record<'lock' | 'default_by_k', string> = {
  lock: 'Headline policy from the lock the run was executed under',
  default_by_k: 'No matching lock: default headline policy for the problem\'s checkpoint count',
};

/** Selector key of an experiment under one lock (runs under different locks are never pooled). */
export function experimentKey(item: { experiment: string; lock_sha256: string | null }): string {
  return `${item.experiment}@${item.lock_sha256 ?? 'no-lock'}`;
}

export function experimentLabel(item: { experiment: string; lock_sha256: string | null }): string {
  return `${item.experiment} · lock ${item.lock_sha256 ? item.lock_sha256.slice(0, 12) : 'unknown'}`;
}

export function statusLabel(status: CellStatus): string {
  return status.replace(/_/g, ' ');
}

/** Normalise a component value to 0..1 for fills (S is on a 0..100 scale). */
export function unitValue(component: Component, value: number): number {
  return component === 'S' ? value / 100 : value;
}

export function fmtComponent(component: Component, value: number | null | undefined): string {
  if (value == null) return '—';
  return component === 'S' ? value.toFixed(1) : value.toFixed(3);
}

export function fmtSigned(component: Component, value: number | null | undefined): string {
  if (value == null) return '—';
  const text = fmtComponent(component, Math.abs(value));
  return `${value >= 0 ? '+' : '−'}${text}`;
}

export function cellColor(component: Component, cell: Cell): string {
  if (cell.status !== 'ok' || cell.value == null) return C.fg0;
  return scoreTier(unitValue(component, cell.value)).color;
}

export function cellTitle(component: Component, cell: Cell): string {
  const name = COMPONENT_INFO[component].name;
  if (cell.status === 'ok') return `${name}: ${fmtComponent(component, cell.value)}`;
  return `${name}: ${statusLabel(cell.status)} — ${cell.reason ?? 'no reason given'}`;
}

export const VERDICT_STYLE: Record<Verdict, { label: string; color: string; glyph: string }> = {
  improved: { label: 'Improved', color: C.green, glyph: '▲' },
  worse: { label: 'Worse', color: C.red, glyph: '▼' },
  inconclusive: { label: 'Inconclusive', color: C.fg2, glyph: '◆' },
  insufficient: { label: 'Insufficient', color: C.fg0, glyph: '○' },
};

/** Categorical arm colour by fixed slot order (first three validated all-pairs). */
export function armColor(arms: string[], arm: string): string {
  const index = arms.indexOf(arm);
  return index >= 0 && index < 3 ? `var(--q-series-${index + 1})` : C.fg1;
}
