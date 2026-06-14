// Adapted from Raindrop Workshop (MIT) — app/src/utils/colors.ts
export const C = {
  bg: '#000000',
  surface: '#0a0a0a',
  elevated: '#111111',
  border: 'rgba(255,255,255,0.06)',
  borderLight: 'rgba(255,255,255,0.1)',

  fg0: '#5a6a72',
  fg1: '#7d8a90',
  fg2: '#a0acb2',
  fg3: '#c8d5dc',
  fg4: '#e1e8ec',
  fg5: '#f2f5f7',

  accent: '#5B8DEF',
  green: '#60E36D',
  red: '#EB1414',
  purple: '#A57CF5',
  orange: '#F0AD4E',
  cyan: '#4FCAE3',

  selected: 'rgba(91,141,239,0.08)',
  selectedBorder: 'rgba(91,141,239,0.2)',
} as const;

import type { SpanType } from '@/utils/types';

export const SPAN_TYPE_INFO: Record<SpanType, { color: string; label: string }> = {
  TRACE: { color: C.purple, label: 'TRACE' },
  AGENT_ROOT: { color: C.cyan, label: 'AGENT' },
  LLM_GENERATION: { color: C.accent, label: 'LLM' },
  TOOL_CALL: { color: C.orange, label: 'TOOL' },
  INTERNAL: { color: C.fg0, label: 'SPAN' },
};
