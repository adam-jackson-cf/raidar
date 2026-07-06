// Adapted from Raindrop Workshop (MIT) — app/src/utils/colors.ts
export const C = {
  bg: 'var(--raidar-bg)',
  surface: 'var(--raidar-surface)',
  elevated: 'var(--raidar-elevated)',
  border: 'var(--raidar-border)',
  borderLight: 'var(--raidar-border-light)',

  fg0: 'var(--raidar-fg0)',
  fg1: 'var(--raidar-fg1)',
  fg2: 'var(--raidar-fg2)',
  fg3: 'var(--raidar-fg3)',
  fg4: 'var(--raidar-fg4)',
  fg5: 'var(--raidar-fg5)',

  accent: '#5B8DEF',
  green: 'var(--raidar-green)',
  greenBg: 'var(--raidar-green-bg)',
  greenBorder: 'var(--raidar-green-border)',
  red: '#EB1414',
  purple: '#A57CF5',
  orange: '#F0AD4E',
  cyan: 'var(--raidar-cyan)',

  selected: 'var(--raidar-selected)',
  selectedBorder: 'var(--raidar-selected-border)',
  input: 'var(--raidar-input)',
  inputSoft: 'var(--raidar-input-soft)',
  popover: 'var(--raidar-popover)',
  overlay: 'var(--raidar-overlay)',
  button: 'var(--raidar-button)',
  buttonFg: 'var(--raidar-button-fg)',
  subtle: 'var(--raidar-subtle)',
  subtleStrong: 'var(--raidar-subtle-strong)',
  hover: 'var(--raidar-hover)',
  rowBorder: 'var(--raidar-row-border)',
  ringTrack: 'var(--raidar-ring-track)',
  hatchLine: 'var(--raidar-hatch-line)',
  tooltipBg: 'var(--raidar-tooltip-bg)',
  tooltipBorder: 'var(--raidar-tooltip-border)',
  traceErrorBg: 'var(--raidar-trace-error-bg)',
} as const;

import type { SpanType } from '@/utils/types';

export const SPAN_TYPE_INFO: Record<SpanType, { color: string; label: string }> = {
  TRACE: { color: C.purple, label: 'TRACE' },
  AGENT_ROOT: { color: C.cyan, label: 'AGENT' },
  LLM_GENERATION: { color: C.accent, label: 'LLM' },
  TOOL_CALL: { color: C.orange, label: 'TOOL' },
  INTERNAL: { color: C.fg0, label: 'SPAN' },
};
