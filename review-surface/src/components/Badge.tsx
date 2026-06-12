import { C } from '@/utils/colors';

/** Small uppercase pill badge, e.g. SYNTHETIC. */
export function Badge({ label, color = C.orange, title }: { label: string; color?: string; title?: string }) {
  return (
    <span
      title={title}
      className="inline-flex items-center rounded px-1.5 py-px text-[9px] font-bold uppercase tracking-wider"
      style={{ color, background: `${color}1a`, border: `1px solid ${color}40` }}
    >
      {label}
    </span>
  );
}
